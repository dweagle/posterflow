"""Tests for the match report background job wrapper."""
import pytest

import modules.match_report as job_module
from core.job_cancel import JobCancelled
from models.job import JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_TYPE_MATCH_REPORT, Job, create_job
from services import match_report_store as store


ITEM = {"media_type": "series", "title": "Wonder Man", "year": 2026, "tmdb_id": None,
        "tvdb_id": 428629, "imdb_id": None, "missing_seasons": [], "missing_main": False, "artwork_type": None}


@pytest.fixture(autouse=True)
def _session(test_db, monkeypatch):
    monkeypatch.setattr(job_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)
    store.clear_reports()
    yield
    store.clear_reports()


def test_item_label():
    assert job_module.item_label(ITEM) == "Wonder Man (2026)"
    assert job_module.item_label({"title": "Bond Collection"}) == "Bond Collection"


def test_job_stores_the_report_and_completes(test_db, monkeypatch):
    job = create_job(test_db, job_type=JOB_TYPE_MATCH_REPORT, message="queued")
    seen = []

    def fake_build(db, item, progress=None):
        progress("Scanning drive 1/2: DriveA", 40)
        seen.append(item["title"])
        return {"item": item, "verdicts": []}

    monkeypatch.setattr(job_module, "build_match_report", fake_build)
    monkeypatch.setattr(job_module, "render_match_report_text", lambda report: "rendered")

    job_module.run_match_report_background_job(job.id, ITEM)

    refreshed = test_db.query(Job).filter(Job.id == job.id).first()
    assert refreshed.status == JOB_STATUS_COMPLETED and refreshed.progress == 100
    assert "Wonder Man (2026)" in refreshed.message
    payload = store.get_report(job.id)
    assert payload["report_text"] == "rendered" and payload["filename"].startswith("posterflow-match-report_wonder-man_")
    assert seen == ["Wonder Man"]


def test_job_failure_marks_the_job_failed(test_db, monkeypatch):
    job = create_job(test_db, job_type=JOB_TYPE_MATCH_REPORT, message="queued")

    def boom(db, item, progress=None):
        raise RuntimeError("TVDB exploded")

    monkeypatch.setattr(job_module, "build_match_report", boom)
    job_module.run_match_report_background_job(job.id, ITEM)

    refreshed = test_db.query(Job).filter(Job.id == job.id).first()
    assert refreshed.status == JOB_STATUS_FAILED and "TVDB exploded" in refreshed.error
    assert store.get_report(job.id) is None


def test_cancel_request_stops_the_job_at_the_next_tick(test_db, monkeypatch):
    from core import job_cancel

    job_id = create_job(test_db, job_type=JOB_TYPE_MATCH_REPORT, message="queued").id
    finalized = []
    monkeypatch.setattr(job_module, "finalize_job_cancelled", lambda db, jid: finalized.append(jid))

    def fake_build(db, item, progress=None):
        job_cancel.request_cancel(job_id)
        progress("Scanning drive 1/2: DriveA", 40)
        raise AssertionError("progress must raise before the scan continues")

    monkeypatch.setattr(job_module, "build_match_report", fake_build)
    try:
        with pytest.raises(JobCancelled):
            job_module.run_match_report_background_job(job_id, ITEM)
    finally:
        job_cancel.clear_cancel(job_id)

    assert finalized == [job_id]
    assert store.get_report(job_id) is None
