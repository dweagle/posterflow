"""
Tests for the post-job scripts feature:
  - GET  /api/scripts/available
  - GET  /api/scripts/hooks
  - PUT  /api/scripts/hooks
  - core/hooks.py  run_post_job_hook()
"""
import json
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.setting import Setting
from core.hooks import (
    HOOK_KEY_SYNC_ONE,
    HOOK_KEY_WORKFLOW,
    SCRIPTS_DIR,
    _validate_script,
    list_available_scripts,
    run_post_job_hook,
)


# ---------------------------------------------------------------------------
# API – GET /api/scripts/available
# ---------------------------------------------------------------------------

def test_available_scripts_empty(client, tmp_path, monkeypatch):
    """Returns an empty list when the scripts dir has no files."""
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr("api.scripts.SCRIPTS_DIR", tmp_path)

    response = client.get("/api/scripts/available")
    assert response.status_code == 200
    assert response.json()["scripts"] == []


def test_available_scripts_lists_files(client, tmp_path, monkeypatch):
    """Returns only executable filenames found in the scripts directory."""
    s1 = tmp_path / "notify.sh"
    s1.write_text("#!/bin/bash\necho hi")
    s1.chmod(0o755)
    s2 = tmp_path / "cleanup.sh"
    s2.write_text("#!/bin/bash\necho bye")
    s2.chmod(0o755)

    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr("api.scripts.SCRIPTS_DIR", tmp_path)

    response = client.get("/api/scripts/available")
    assert response.status_code == 200
    scripts = response.json()["scripts"]
    assert sorted(scripts) == ["cleanup.sh", "notify.sh"]


def test_available_scripts_ignores_subdirectories(client, tmp_path, monkeypatch):
    """Subdirectories inside the scripts dir are not listed."""
    s = tmp_path / "script.sh"
    s.write_text("#!/bin/bash")
    s.chmod(0o755)
    (tmp_path / "subdir").mkdir()

    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr("api.scripts.SCRIPTS_DIR", tmp_path)

    response = client.get("/api/scripts/available")
    assert response.status_code == 200
    assert response.json()["scripts"] == ["script.sh"]


def test_available_scripts_excludes_non_executable(client, tmp_path, monkeypatch):
    """Non-executable files (e.g. .log files) are not listed."""
    exe = tmp_path / "hook.sh"
    exe.write_text("#!/bin/bash\necho hi")
    exe.chmod(0o755)
    log = tmp_path / "hook.log"
    log.write_text("some log output")
    log.chmod(0o644)  # not executable

    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr("api.scripts.SCRIPTS_DIR", tmp_path)

    response = client.get("/api/scripts/available")
    assert response.status_code == 200
    assert response.json()["scripts"] == ["hook.sh"]


# ---------------------------------------------------------------------------
# API – GET /api/scripts/hooks
# ---------------------------------------------------------------------------

def test_get_hooks_returns_defaults_when_no_setting(client, test_db):
    """All hooks are returned with enabled=False when nothing is configured."""
    response = client.get("/api/scripts/hooks")
    assert response.status_code == 200
    hooks = response.json()["hooks"]

    for key in ["sync_one", "sync_all", "poster_workflow", "poster_renamer", "border_replacer", "unmatched"]:
        assert key in hooks
        assert hooks[key]["enabled"] is False
        assert hooks[key]["run_when"] == "always"
        assert hooks[key]["trigger_on"] == "always"


def test_get_hooks_merges_stored_config(client, test_db):
    """Hooks endpoint merges stored config with defaults."""
    stored = {
        "sync_one": {"enabled": True, "script": "notify.sh", "run_when": "scheduled", "trigger_on": "success"},
    }
    test_db.add(Setting(key="post_job_hooks", value=json.dumps(stored)))
    test_db.commit()

    response = client.get("/api/scripts/hooks")
    assert response.status_code == 200
    hooks = response.json()["hooks"]

    assert hooks["sync_one"]["enabled"] is True
    assert hooks["sync_one"]["script"] == "notify.sh"
    assert hooks["sync_one"]["run_when"] == "scheduled"
    # Keys not in stored config still have defaults
    assert hooks["sync_all"]["enabled"] is False


# ---------------------------------------------------------------------------
# API – PUT /api/scripts/hooks
# ---------------------------------------------------------------------------

def test_save_hooks_persists_to_db(client, test_db):
    """Saving hooks writes the config to the post_job_hooks setting."""
    payload = {
        "hooks": {
            "sync_one": {"enabled": True, "script": "test.sh", "run_when": "always", "trigger_on": "success"},
            "sync_all": {"enabled": False, "script": "", "run_when": "always", "trigger_on": "always"},
            "poster_workflow": {"enabled": False, "script": "", "run_when": "always", "trigger_on": "always"},
            "poster_renamer": {"enabled": False, "script": "", "run_when": "always", "trigger_on": "always"},
            "border_replacer": {"enabled": False, "script": "", "run_when": "always", "trigger_on": "always"},
            "unmatched": {"enabled": False, "script": "", "run_when": "always", "trigger_on": "always"},
        }
    }

    response = client.put("/api/scripts/hooks", json=payload)
    assert response.status_code == 200

    saved = test_db.query(Setting).filter(Setting.key == "post_job_hooks").first()
    assert saved is not None
    data = json.loads(saved.value)
    assert data["sync_one"]["enabled"] is True
    assert data["sync_one"]["script"] == "test.sh"
    assert data["sync_one"]["trigger_on"] == "success"


def test_save_hooks_rejects_unknown_key(client, test_db):
    """Unknown hook keys are rejected with 400."""
    payload = {
        "hooks": {
            "totally_made_up": {"enabled": True, "script": "x.sh", "run_when": "always", "trigger_on": "always"},
        }
    }
    response = client.put("/api/scripts/hooks", json=payload)
    assert response.status_code == 400


def test_save_hooks_rejects_invalid_run_when(client, test_db):
    """Invalid run_when value is rejected with 400."""
    payload = {
        "hooks": {
            "sync_one": {"enabled": True, "script": "x.sh", "run_when": "whenever", "trigger_on": "always"},
        }
    }
    response = client.put("/api/scripts/hooks", json=payload)
    assert response.status_code == 400


def test_save_hooks_rejects_invalid_trigger_on(client, test_db):
    """Invalid trigger_on value is rejected with 400."""
    payload = {
        "hooks": {
            "sync_one": {"enabled": True, "script": "x.sh", "run_when": "always", "trigger_on": "sometimes"},
        }
    }
    response = client.put("/api/scripts/hooks", json=payload)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# core/hooks.py – _validate_script
# ---------------------------------------------------------------------------

def test_validate_script_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    valid, err = _validate_script("../../etc/passwd")
    assert not valid
    assert "path separators" in err.lower() or "invalid" in err.lower()


def test_validate_script_rejects_subdirectory_separator(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    valid, err = _validate_script("subdir/script.sh")
    assert not valid


def test_validate_script_rejects_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    valid, err = _validate_script("ghost.sh")
    assert not valid
    assert "not found" in err.lower()


def test_validate_script_rejects_non_executable(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    script = tmp_path / "nope.sh"
    script.write_text("#!/bin/bash")
    script.chmod(0o644)  # not executable

    valid, err = _validate_script("nope.sh")
    assert not valid
    assert "executable" in err.lower()


def test_validate_script_accepts_valid_executable(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/bash\necho hi")
    script.chmod(0o755)

    valid, err = _validate_script("ok.sh")
    assert valid
    assert err == ""


# ---------------------------------------------------------------------------
# core/hooks.py – run_post_job_hook (execution logic)
# ---------------------------------------------------------------------------

def _make_executable_script(tmp_path: Path, name: str = "test.sh") -> Path:
    script = tmp_path / name
    script.write_text("#!/bin/bash\necho 'hook ran'")
    script.chmod(0o755)
    return script


def test_hook_skips_when_disabled(tmp_path, monkeypatch):
    """Hook with enabled=False is never executed."""
    _make_executable_script(tmp_path, "test.sh")
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)

    hooks = {HOOK_KEY_SYNC_ONE: {"enabled": False, "script": "test.sh", "run_when": "always", "trigger_on": "always"}}
    db = MagicMock()

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run") as mock_run:
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=True, triggered_by="manual", db=db)
        mock_run.assert_not_called()


def test_hook_skips_when_run_when_scheduled_but_triggered_manually(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    _make_executable_script(tmp_path, "test.sh")

    hooks = {HOOK_KEY_SYNC_ONE: {"enabled": True, "script": "test.sh", "run_when": "scheduled", "trigger_on": "always"}}
    db = MagicMock()

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run") as mock_run:
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=True, triggered_by="manual", db=db)
        mock_run.assert_not_called()


def test_hook_skips_when_trigger_on_success_but_job_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    _make_executable_script(tmp_path, "test.sh")

    hooks = {HOOK_KEY_SYNC_ONE: {"enabled": True, "script": "test.sh", "run_when": "always", "trigger_on": "success"}}
    db = MagicMock()

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run") as mock_run:
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=False, triggered_by="manual", db=db)
        mock_run.assert_not_called()


def test_hook_executes_when_all_conditions_met(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    _make_executable_script(tmp_path, "test.sh")

    hooks = {HOOK_KEY_SYNC_ONE: {"enabled": True, "script": "test.sh", "run_when": "always", "trigger_on": "always"}}
    db = MagicMock()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run", return_value=mock_result) as mock_run:
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=True, triggered_by="manual", db=db)
        mock_run.assert_called_once()

        call_args = mock_run.call_args
        # Must NOT use shell=True
        assert call_args.kwargs.get("shell") is not True
        # Env vars are passed
        env = call_args.kwargs["env"]
        assert env["POSTERFLOW_JOB"] == HOOK_KEY_SYNC_ONE
        assert env["POSTERFLOW_STATUS"] == "success"
        assert env["POSTERFLOW_TRIGGERED_BY"] == "manual"


def test_hook_passes_failure_status_in_env(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    _make_executable_script(tmp_path, "test.sh")

    hooks = {HOOK_KEY_WORKFLOW: {"enabled": True, "script": "test.sh", "run_when": "always", "trigger_on": "failure"}}
    db = MagicMock()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run", return_value=mock_result) as mock_run:
        run_post_job_hook(HOOK_KEY_WORKFLOW, success=False, triggered_by="scheduled", db=db)
        mock_run.assert_called_once()
        env = mock_run.call_args.kwargs["env"]
        assert env["POSTERFLOW_STATUS"] == "failure"
        assert env["POSTERFLOW_TRIGGERED_BY"] == "scheduled"


def test_hook_does_not_raise_on_timeout(tmp_path, monkeypatch):
    """A timed-out hook must not propagate an exception."""
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    _make_executable_script(tmp_path, "slow.sh")

    hooks = {HOOK_KEY_SYNC_ONE: {"enabled": True, "script": "slow.sh", "run_when": "always", "trigger_on": "always"}}
    db = MagicMock()

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="slow.sh", timeout=60)):
        # Must not raise
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=True, triggered_by="manual", db=db)


def test_hook_does_not_raise_on_unexpected_error(tmp_path, monkeypatch):
    """Any unexpected exception inside the hook runner must be swallowed."""
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    _make_executable_script(tmp_path, "boom.sh")

    hooks = {HOOK_KEY_SYNC_ONE: {"enabled": True, "script": "boom.sh", "run_when": "always", "trigger_on": "always"}}
    db = MagicMock()

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run", side_effect=RuntimeError("unexpected")):
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=True, triggered_by="manual", db=db)


def test_hook_skips_when_triggered_by_workflow(tmp_path, monkeypatch):
    """Sub-module hooks are suppressed when triggered as part of a workflow run."""
    monkeypatch.setattr("core.hooks.SCRIPTS_DIR", tmp_path)
    _make_executable_script(tmp_path, "test.sh")

    hooks = {HOOK_KEY_SYNC_ONE: {"enabled": True, "script": "test.sh", "run_when": "always", "trigger_on": "always"}}
    db = MagicMock()

    with patch("models.setting.get_setting_value", return_value=json.dumps(hooks)), \
         patch("subprocess.run") as mock_run:
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=True, triggered_by="workflow", db=db)
        mock_run.assert_not_called()
