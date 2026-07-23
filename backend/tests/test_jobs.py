from datetime import datetime, timedelta, timezone
import json

from models.drive import Drive
from models.job import Job, prune_job_history
from models.setting import Setting


def test_list_jobs_returns_recent_first(client, test_db):
    """Jobs list should return most recent jobs first."""
    now = datetime.now(timezone.utc)
    old_job = Job(
        job_type="Old Job",
        status="completed",
        progress=100,
        message="done",
        started_at=now - timedelta(minutes=5),
    )
    new_job = Job(
        job_type="New Job",
        status="running",
        progress=10,
        message="working",
        started_at=now,
    )
    test_db.add(old_job)
    test_db.commit()
    test_db.add(new_job)
    test_db.commit()

    response = client.get("/api/jobs/")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    assert data[0]["job_type"] == "New Job"


def test_get_job_returns_404_for_missing_job(client):
    """Fetching a non-existent job should return 404."""
    response = client.get("/api/jobs/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_start_sync_rejects_nonexistent_drive(client):
    """Sync should fail when drive does not exist."""
    response = client.post("/api/jobs/sync", json={"drive_id": 999999})
    assert response.status_code == 404
    assert response.json()["detail"] == "Drive not found"


def test_start_sync_rejects_unsubscribed_drive(client, test_db):
    """Sync should fail for unsubscribed drives."""
    drive = Drive(
        name="Unsubscribed",
        drive_id="unsub-drive-1",
        style_type="MM2K",
        subscribed=False,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post("/api/jobs/sync", json={"drive_id": drive.id})
    assert response.status_code == 400
    assert response.json()["detail"] == "Drive not subscribed"


def test_start_sync_all_rejects_without_subscribed_drives(client):
    """Sync-all should fail when there are no subscribed drives."""
    response = client.post("/api/jobs/sync-all")
    assert response.status_code == 400
    assert response.json()["detail"] == "No subscribed drives found"


def test_start_sync_creates_pending_job(client, test_db, monkeypatch):
    """Sync start should create a pending job and queue it."""
    drive = Drive(
        name="Subscribed",
        drive_id="sub-drive-1",
        style_type="MM2K",
        subscribed=True,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    queued_calls: list[int] = []

    def _fake_submit(_fn, job_id, *args):
        queued_calls.append(job_id)

    monkeypatch.setattr("api.jobs.job_queue.submit", _fake_submit)

    response = client.post("/api/jobs/sync", json={"drive_id": drive.id})
    assert response.status_code == 200
    data = response.json()
    assert data["job_type"] == f"Sync: {drive.name}"
    assert data["status"] == "pending"
    assert isinstance(data["id"], int)
    assert queued_calls == [data["id"]]


def test_start_sync_all_creates_pending_job(client, test_db, monkeypatch):
    """Sync-all start should create a pending job and queue it."""
    drive_one = Drive(
        name="Drive One",
        drive_id="sync-all-1",
        style_type="MM2K",
        subscribed=True,
    )
    drive_two = Drive(
        name="Drive Two",
        drive_id="sync-all-2",
        style_type="CL2K",
        subscribed=True,
    )
    test_db.add(drive_one)
    test_db.add(drive_two)
    test_db.commit()

    queued_calls: list[int] = []

    def _fake_submit(_fn, job_id, *args):
        queued_calls.append(job_id)

    monkeypatch.setattr("api.jobs.job_queue.submit", _fake_submit)

    response = client.post("/api/jobs/sync-all")
    assert response.status_code == 200
    data = response.json()
    assert data["job_type"] == "Sync All (2 drives)"
    assert data["status"] == "pending"
    assert isinstance(data["id"], int)
    assert queued_calls == [data["id"]]


def test_cancel_pending_job_marks_cancelled(client, test_db):
    """Cancelling a queued job should finalize it as 'cancelled' immediately."""
    job = Job(job_type="Sync All", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    response = client.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "cancelled"

    test_db.expire_all()
    refreshed = test_db.query(Job).filter(Job.id == job.id).first()
    assert refreshed.status == "cancelled"
    assert refreshed.completed_at is not None


def test_cancel_running_job_flags_for_stop(client, test_db):
    """Cancelling a running job should flag it for cooperative stop, not finalize it here."""
    from core.job_cancel import is_cancel_requested, clear_cancel

    job = Job(job_type="Poster Workflow", status="running", progress=42, message="working")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    try:
        response = client.post(f"/api/jobs/{job.id}/cancel")
        assert response.status_code == 200
        data = response.json()
        # Still running from the API's perspective — the worker finalizes it.
        assert data["status"] == "running"
        assert is_cancel_requested(job.id)
    finally:
        clear_cancel(job.id)


def test_cancel_terminal_job_rejected(client, test_db):
    """Cancelling an already-finished job should return 400."""
    job = Job(job_type="Sync All", status="completed", progress=100, message="done")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    response = client.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == 400


def test_cancel_missing_job_returns_404(client):
    """Cancelling a non-existent job should return 404."""
    response = client.post("/api/jobs/999999/cancel")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_delete_job_success(client, test_db):
    """Deleting an existing job should return success message."""
    job = Job(job_type="Delete Me", status="completed", progress=100, message="done")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    response = client.delete(f"/api/jobs/{job.id}")
    assert response.status_code == 200
    assert response.json()["message"] == "Job deleted"


def test_delete_job_404_for_missing_job(client):
    """Deleting a missing job should return 404."""
    response = client.delete("/api/jobs/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_prune_job_history_applies_age_and_per_type_cap(test_db):
    """Pruning should delete aged rows and cap terminal rows per exact job_type."""
    now = datetime.now(timezone.utc)

    stale_completed = Job(
        job_type="Retention Completed",
        status="completed",
        progress=100,
        message="old-completed",
        started_at=now - timedelta(days=20),
        completed_at=now - timedelta(days=20),
    )
    stale_failed = Job(
        job_type="Retention Failed",
        status="failed",
        progress=100,
        error="old-failed",
        started_at=now - timedelta(days=30),
        completed_at=now - timedelta(days=30),
    )
    test_db.add_all([stale_completed, stale_failed])

    capped_jobs = []
    for index in range(55):
        capped_jobs.append(
            Job(
                job_type="Cap Type",
                status="completed",
                progress=100,
                message=f"cap-{index}",
                started_at=now - timedelta(minutes=index + 1),
                completed_at=now - timedelta(minutes=index + 1),
            )
        )
    test_db.add_all(capped_jobs)

    running_job = Job(
        job_type="Cap Type",
        status="running",
        progress=50,
        message="still-running",
        started_at=now,
    )
    test_db.add(running_job)
    test_db.commit()

    deleted = prune_job_history(
        test_db,
        keep_completed_days=7,
        keep_failed_days=14,
        max_per_job_type=50,
    )
    assert deleted >= 7

    assert test_db.query(Job).filter(Job.message == "old-completed").first() is None
    assert test_db.query(Job).filter(Job.error == "old-failed").first() is None

    cap_count = test_db.query(Job).filter(Job.job_type == "Cap Type", Job.status == "completed").count()
    assert cap_count == 50

    still_running = test_db.query(Job).filter(Job.job_type == "Cap Type", Job.status == "running").count()
    assert still_running == 1


def test_start_idarr_accepts_scoped_source_filenames(client, test_db, monkeypatch, tmp_path):
    """IDarr start should pass sanitized source_filenames into queued job config."""
    source_dir = tmp_path / "idarr-source"
    source_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "sync_targets": [
            {
                "label": "Drive 1",
                "personal_drive_id": "drive-1",
                "source_dir": str(source_dir),
            }
        ],
    }
    test_db.add(Setting(key="maker_tools_idarr_config", value=json.dumps(payload)))
    test_db.add(Setting(key="tmdb_api_key", value="tmdb-test-key"))
    test_db.commit()

    queued_payloads: list[dict] = []

    def _fake_submit(_fn, _job_id, *_args):
        queued_payloads.append(dict(_args[-1]))

    monkeypatch.setattr("api.jobs.job_queue.submit", _fake_submit)

    response = client.post(
        "/api/jobs/idarr",
        json={
            "dry_run": False,
            "sync_target_index": 0,
            "source_filenames": ["poster-one.jpg", "../poster-two.png", "POSTER-ONE.jpg", ""],
        },
    )

    assert response.status_code == 200
    assert queued_payloads
    queued_config = queued_payloads[0]
    assert queued_config.get("source_filenames") == ["poster-one.jpg", "poster-two.png"]


def test_start_idarr_targeted_queues_behind_active_job_but_full_run_blocked(client, test_db, monkeypatch, tmp_path):
    """With an IDarr job already active, a targeted run (source_filenames) queues,
    while a duplicate full run is still refused with 409."""
    source_dir = tmp_path / "idarr-source"
    source_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "sync_targets": [
            {
                "label": "Drive 1",
                "personal_drive_id": "drive-1",
                "source_dir": str(source_dir),
            }
        ],
    }
    test_db.add(Setting(key="maker_tools_idarr_config", value=json.dumps(payload)))
    test_db.add(Setting(key="tmdb_api_key", value="tmdb-test-key"))
    test_db.add(Job(job_type="idarr", status="running", progress=50, message="busy"))
    test_db.commit()

    monkeypatch.setattr("api.jobs.job_queue.submit", lambda *_a, **_k: None)

    targeted = client.post(
        "/api/jobs/idarr",
        json={"dry_run": False, "sync_target_index": 0, "source_filenames": ["poster-one.jpg"]},
    )
    assert targeted.status_code == 200

    full = client.post("/api/jobs/idarr", json={"dry_run": False, "sync_target_index": 0})
    assert full.status_code == 409
    assert "already" in full.json()["detail"]
