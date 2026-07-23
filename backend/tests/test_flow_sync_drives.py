"""Workflow step gating: which sub-jobs a given config actually dispatches.

Sync Drives: poster and artwork drives are independently selectable. Artwork lives in its own
table with its own sync job, so before this the workflow synced poster drives only — then
organized artwork from whatever a manual sync last left on disk.

Asset Cleanup: the UI nests the cleanup toggle inside the Rename & Organize step, so a stored
cleanup_assets.enabled must not run cleanup on its own once rename is turned off.
"""

import json

import pytest

from models.job import Job, JOB_STATUS_COMPLETED, update_job_state
from models.setting import Setting
import modules.cleanup as cleanup_module
import modules.flow as flow_module


def _flow_config(test_db, sync_drives):
    """Only the sync step runs; everything else is off."""
    config = {
        "sync_drives": sync_drives,
        "rename_assets": {"enabled": False, "stop_on_error": True},
        "detect_unmatched": {"enabled": False, "stop_on_error": True},
        "border_replacer": {"enabled": False, "stop_on_error": True},
        "plex_upload": {"enabled": False, "stop_on_error": False},
        "cleanup_assets": {"enabled": False, "delete_unknown": False},
    }
    test_db.add(Setting(key="poster_flow_config", value=json.dumps(config)))
    test_db.commit()


@pytest.fixture
def run_flow(test_db, monkeypatch):
    """Run the real flow, recording which sync children it dispatches."""
    ran = []

    def _promote(db, job, child_id, lo, hi, msg, runner, *args):
        # Both steps call the unified run_sync_all_job; tell them apart by the sync flags.
        kw = getattr(runner, "keywords", {}) or {}
        ran.append("artwork" if (kw.get("sync_artwork") and not kw.get("sync_posters", True)) else "posters")
        child = db.query(Job).filter(Job.id == child_id).first()
        update_job_state(db, child, status=JOB_STATUS_COMPLETED, progress=100, message="stubbed")
        return {}

    monkeypatch.setattr(flow_module, "_promote_child_progress_to_parent", _promote)
    monkeypatch.setattr(flow_module, "SessionLocal", lambda: test_db)
    # The flow closes its own session in a finally; keep the test's alive.
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    def _go():
        job = Job(job_type="Poster Workflow", status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        flow_module.run_flow_background_job(job.id, dry_run=False)
        return test_db.query(Job).filter(Job.id == job.id).first()

    return {"go": _go, "ran": ran}


def test_both_selected_syncs_poster_then_artwork_drives(test_db, run_flow):
    _flow_config(test_db, {"enabled": True, "stop_on_error": True, "posters": True, "artwork": True})

    job = run_flow["go"]()

    assert run_flow["ran"] == ["posters", "artwork"]
    assert job.status == "completed"


def test_artwork_only_skips_the_poster_sync(test_db, run_flow):
    _flow_config(test_db, {"enabled": True, "stop_on_error": True, "posters": False, "artwork": True})

    run_flow["go"]()

    assert run_flow["ran"] == ["artwork"]


def test_posters_only_skips_the_artwork_sync(test_db, run_flow):
    _flow_config(test_db, {"enabled": True, "stop_on_error": True, "posters": True, "artwork": False})

    run_flow["go"]()

    assert run_flow["ran"] == ["posters"]


def test_step_disabled_syncs_nothing(test_db, run_flow):
    _flow_config(test_db, {"enabled": False, "stop_on_error": True, "posters": True, "artwork": True})

    run_flow["go"]()

    assert run_flow["ran"] == []


def test_config_saved_before_artwork_existed_keeps_syncing_posters_only(test_db, run_flow):
    """Old configs carry neither key. Artwork is opt-in (like the Asset Renamer's Include
    selector), so an existing workflow keeps doing exactly what it did before."""
    _flow_config(test_db, {"enabled": True, "stop_on_error": True})

    run_flow["go"]()

    assert run_flow["ran"] == ["posters"]


def test_artwork_sync_failure_stops_the_workflow_when_asked(test_db, monkeypatch):
    _flow_config(test_db, {"enabled": True, "stop_on_error": True, "posters": False, "artwork": True})

    def _promote(db, job, child_id, lo, hi, msg, runner, *args):
        raise RuntimeError("rclone exploded")

    monkeypatch.setattr(flow_module, "_promote_child_progress_to_parent", _promote)
    monkeypatch.setattr(flow_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    job = Job(job_type="Poster Workflow", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    result = flow_module.run_flow_background_job(job.id, dry_run=False)

    assert result["success"] is False
    assert [f["job"] for f in result["jobs_failed"]] == ["sync_artwork_drives"]


# --- Asset cleanup gating (a sub-option of the Rename & Organize step) ---------------------


def _cleanup_flow_config(test_db, rename_enabled, cleanup_enabled):
    """Only rename (+ its cleanup sub-option) is in play; every other step is off."""
    config = {
        "sync_drives": {"enabled": False, "stop_on_error": True, "posters": True, "artwork": False},
        "rename_assets": {"enabled": rename_enabled, "stop_on_error": True},
        "detect_unmatched": {"enabled": False, "stop_on_error": True},
        "border_replacer": {"enabled": False, "stop_on_error": True},
        "plex_upload": {"enabled": False, "stop_on_error": False},
        "cleanup_assets": {"enabled": cleanup_enabled, "delete_unknown": False},
    }
    test_db.add(Setting(key="poster_flow_config", value=json.dumps(config)))
    test_db.commit()


@pytest.fixture
def run_flow_cleanup(test_db, monkeypatch):
    """Run the real flow, recording whether asset cleanup was invoked."""
    cleanup_calls = []

    def _promote(db, job, child_id, lo, hi, msg, runner, *args):
        child = db.query(Job).filter(Job.id == child_id).first()
        update_job_state(db, child, status=JOB_STATUS_COMPLETED, progress=100, message="stubbed")
        # Stand in for the rename handing back its media fetch + artwork drive scan.
        scan_out = getattr(runner, "keywords", {}).get("scan_out")
        if scan_out is not None:
            scan_out["media_dict"] = {"movies": ["m"], "series": [], "collections": []}
            scan_out["artwork_boxes"] = ["box"]
        return {}

    def _fake_cleanup(db, config_data, dry_run, triggered_by, job, **kwargs):
        cleanup_calls.append({**config_data, "_kwargs": kwargs})
        return {}

    monkeypatch.setattr(flow_module, "_promote_child_progress_to_parent", _promote)
    monkeypatch.setattr(cleanup_module, "maybe_run_asset_cleanup", _fake_cleanup)
    monkeypatch.setattr(cleanup_module, "summarize_cleanup", lambda _r: "no changes")
    monkeypatch.setattr(flow_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    def _go():
        job = Job(job_type="Poster Workflow", status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        flow_module.run_flow_background_job(job.id, dry_run=False)
        return cleanup_calls

    return _go


def test_cleanup_runs_when_rename_is_enabled(test_db, run_flow_cleanup):
    _cleanup_flow_config(test_db, rename_enabled=True, cleanup_enabled=True)

    calls = run_flow_cleanup()

    assert len(calls) == 1, "cleanup should run once when rename is on and cleanup is checked"


def test_cleanup_reuses_the_renames_media_and_artwork_scan(test_db, run_flow_cleanup):
    """A workflow ran cleanup itself (the rename job skips its own), so it re-fetched media and
    re-walked every artwork drive — a second full scan and ~60k duplicate log lines."""
    _cleanup_flow_config(test_db, rename_enabled=True, cleanup_enabled=True)

    calls = run_flow_cleanup()

    passed = calls[0]["_kwargs"]
    assert passed["media_dict"] == {"movies": ["m"], "series": [], "collections": []}
    assert passed["artwork_boxes"] == ["box"]


def test_cleanup_is_skipped_when_rename_is_disabled(test_db, run_flow_cleanup):
    """The regression: cleanup_assets.enabled stays True in a stored config after rename is
    turned off (its checkbox is then hidden under the rename card), and it ran anyway."""
    _cleanup_flow_config(test_db, rename_enabled=False, cleanup_enabled=True)

    calls = run_flow_cleanup()

    assert calls == [], "cleanup must not run when the rename step is disabled"
