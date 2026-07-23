"""Workflow Detect Unmatched step: one slot-aware pass covers posters AND artwork.

There's no per-asset selection anymore — the step is simply enabled or not, and it runs the
single unified detector (run_unmatched_detection_background_job), which handles posters and
artwork in one pass.
"""

import json

import pytest

from models.job import Job, JOB_STATUS_COMPLETED, update_job_state
from models.setting import Setting
import modules.flow as flow_module


def _flow_config(test_db, detect_unmatched):
    """Only the detect step runs; everything else is off."""
    config = {
        "sync_drives": {"enabled": False, "stop_on_error": True},
        "rename_assets": {"enabled": False, "stop_on_error": True},
        "detect_unmatched": detect_unmatched,
        "border_replacer": {"enabled": False, "stop_on_error": True},
        "plex_upload": {"enabled": False, "stop_on_error": False},
        "cleanup_assets": {"enabled": False, "delete_unknown": False},
    }
    test_db.add(Setting(key="poster_flow_config", value=json.dumps(config)))
    test_db.commit()


@pytest.fixture
def run_flow(test_db, monkeypatch):
    ran = []

    def _promote(db, job, child_id, lo, hi, msg, runner, *args):
        ran.append("detect")
        child = db.query(Job).filter(Job.id == child_id).first()
        update_job_state(db, child, status=JOB_STATUS_COMPLETED, progress=100, message="stubbed")
        return {}

    monkeypatch.setattr(flow_module, "_promote_child_progress_to_parent", _promote)
    monkeypatch.setattr(flow_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    def _go():
        job = Job(job_type="Poster Workflow", status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        flow_module.run_flow_background_job(job.id, dry_run=False)
        return test_db.query(Job).filter(Job.id == job.id).first()

    return {"go": _go, "ran": ran}


def test_enabled_runs_one_unified_detect(test_db, run_flow):
    _flow_config(test_db, {"enabled": True, "stop_on_error": True})

    job = run_flow["go"]()

    assert run_flow["ran"] == ["detect"]
    assert job.status == "completed"


def test_disabled_detects_nothing(test_db, run_flow):
    _flow_config(test_db, {"enabled": False, "stop_on_error": True})

    run_flow["go"]()

    assert run_flow["ran"] == []


def test_legacy_posters_artwork_keys_are_ignored(test_db, run_flow):
    """Configs saved before the toggles were removed may still carry posters/artwork keys;
    they're ignored now — the step just runs the one unified detector."""
    _flow_config(test_db, {"enabled": True, "stop_on_error": True, "posters": True, "artwork": False})

    run_flow["go"]()

    assert run_flow["ran"] == ["detect"]


def test_detection_failure_stops_the_workflow_when_asked(test_db, monkeypatch):
    """Detection raises when nothing has been organized yet; with stop_on_error that must
    fail the run rather than be swallowed."""
    _flow_config(test_db, {"enabled": True, "stop_on_error": True})

    def _promote(db, job, child_id, lo, hi, msg, runner, *args):
        raise RuntimeError("Destination directory does not exist")

    monkeypatch.setattr(flow_module, "_promote_child_progress_to_parent", _promote)
    monkeypatch.setattr(flow_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    job = Job(job_type="Poster Workflow", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    result = flow_module.run_flow_background_job(job.id, dry_run=False)

    assert result["success"] is False
    assert [f["job"] for f in result["jobs_failed"]] == ["detect_unmatched"]
