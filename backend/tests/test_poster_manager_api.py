import time
import json
from datetime import datetime, timezone, timedelta

from models.setting import Setting
from models.drive import Drive
from models.job import Job


def test_rename_rejects_missing_destination(client):
    """Rename should fail when destination is missing from payload config."""
    response = client.post("/api/poster-manager/rename", json={"config": {}})
    assert response.status_code == 400
    assert "Destination directory not specified" in response.json()["detail"]


def test_rename_rejects_without_drive_priority(client):
    """Rename should fail when drive priority is not configured."""
    response = client.post(
        "/api/poster-manager/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 400
    assert "No drive priority configured" in response.json()["detail"]


def test_rename_rejects_invalid_drive_priority_json(client, test_db):
    """Rename should fail when drive priority setting is invalid JSON."""
    test_db.add(Setting(key="poster_drive_priority", value="{invalid-json"))
    test_db.commit()

    response = client.post(
        "/api/poster-manager/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 400
    assert "Drive priority configuration is invalid" in response.json()["detail"]


def test_rename_rejects_when_priority_drives_not_subscribed(client, test_db):
    """Rename should fail when priority drives exist but none are subscribed."""
    drive = Drive(
        name="Unsubscribed Drive",
        drive_id="drive-unsubscribed-1",
        style_type="MM2K",
        subscribed=False,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    test_db.add(Setting(key="poster_drive_priority", value=f'{{"drive_ids": [{drive.id}]}}'))
    test_db.commit()

    response = client.post(
        "/api/poster-manager/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 400
    assert "No drives in priority list are subscribed" in response.json()["detail"]


def test_border_replacer_rejects_when_destination_missing(client):
    """Border replacer should fail when destination setting is not configured."""
    response = client.post("/api/poster-manager/border-replacer/run", json={})
    assert response.status_code == 400
    assert "No destination directory configured" in response.json()["detail"]


def test_border_replacer_rejects_when_destination_path_missing(client, test_db):
    """Border replacer should fail when destination setting path does not exist."""
    missing_path = "/tmp/posterflow-test-path-does-not-exist"
    test_db.add(Setting(key="poster_destination", value=missing_path))
    test_db.commit()

    response = client.post("/api/poster-manager/border-replacer/run", json={})
    assert response.status_code == 400
    assert "Destination directory does not exist" in response.json()["detail"]


def test_rename_starts_successfully_with_valid_priority(client, test_db, monkeypatch):
    """Rename should start and create a job when config and priority are valid."""
    drive = Drive(
        name="Subscribed Drive",
        drive_id="drive-subscribed-1",
        style_type="MM2K",
        subscribed=True,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    test_db.add(Setting(key="poster_drive_priority", value=f'{{"drive_ids": [{drive.id}]}}'))
    test_db.commit()

    def _noop_rename_job(job_id, config_data):
        return None

    monkeypatch.setattr("api.poster_manager.run_rename_background_job", _noop_rename_job)

    response = client.post(
        "/api/poster-manager/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert isinstance(data["job_id"], int)

    job = test_db.query(Job).filter(Job.id == data["job_id"]).first()
    assert job is not None
    assert job.job_type == "Poster Renamer"


def test_border_replacer_starts_successfully(client, test_db, monkeypatch, tmp_path):
    """Border replacer should start and create a job when destination exists."""
    test_db.add(Setting(key="poster_destination", value=str(tmp_path)))
    test_db.commit()

    def _noop_border_job(job_id, dry_run, mode):
        return None

    monkeypatch.setattr("api.poster_manager.run_border_replacer_background_job", _noop_border_job)

    response = client.post(
        "/api/poster-manager/border-replacer/run",
        json={"dry_run": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert isinstance(data["job_id"], int)

    job = test_db.query(Job).filter(Job.id == data["job_id"]).first()
    assert job is not None
    assert job.job_type == "Border Replacer"


def test_flow_config_round_trip(client):
    """Flow config should save and then load with updated values."""
    payload = {
        "sync_drives": {"enabled": True, "stop_on_error": False},
        "rename_posters": {"enabled": True, "stop_on_error": True},
        "detect_unmatched": {"enabled": False, "stop_on_error": False},
        "border_replacer": {"enabled": True, "stop_on_error": False},
        "plex_upload": {"enabled": False, "stop_on_error": False},
    }

    save_response = client.post("/api/poster-manager/flow/config", json=payload)
    assert save_response.status_code == 200
    assert save_response.json()["success"] is True

    get_response = client.get("/api/poster-manager/flow/config")
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["border_replacer"]["enabled"] is True
    assert data["detect_unmatched"]["enabled"] is False


def test_flow_run_rejects_when_already_running(client, test_db):
    """Flow run should return 409 while a workflow job is already running."""
    import api.poster_manager as poster_manager_module

    existing_job = Job(
        job_type="Poster Workflow",
        status="running",
        progress=10,
        message="Running",
    )
    test_db.add(existing_job)
    test_db.commit()

    poster_manager_module._flow_running = True
    poster_manager_module._flow_started_at = datetime.now(timezone.utc)

    try:
        response = client.post("/api/poster-manager/flow/run", json={"dry_run": False})
        assert response.status_code == 409
        assert "Workflow is already running" in response.json()["detail"]
    finally:
        poster_manager_module._flow_running = False
        poster_manager_module._flow_started_at = None


def test_flow_run_recovers_stale_lock_and_starts(client, test_db, monkeypatch):
    """Flow run should recover a stale lock when no running jobs exist and start successfully."""
    import api.poster_manager as poster_manager_module

    def _run_flow_now(job_id, dry_run, release_flow_lock):
        release_flow_lock()

    monkeypatch.setattr("api.poster_manager.run_flow_background_job", _run_flow_now)

    poster_manager_module._flow_running = True
    poster_manager_module._flow_started_at = datetime.now(timezone.utc)

    try:
        response = client.post("/api/poster-manager/flow/run", json={"dry_run": True})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["job_id"], int)

        job = test_db.query(Job).filter(Job.id == data["job_id"]).first()
        assert job is not None
        assert job.job_type == "Poster Workflow"
        assert job.status in {"pending", "running"}

        for _ in range(50):
            if poster_manager_module._flow_running is False:
                break
            time.sleep(0.01)

        assert poster_manager_module._flow_running is False
        assert poster_manager_module._flow_started_at is None
    finally:
        poster_manager_module._flow_running = False
        poster_manager_module._flow_started_at = None


def test_flow_run_forces_release_after_long_lock_age(client, test_db, monkeypatch):
    """Flow run should force lock release when lock age exceeds threshold and then start."""
    import api.poster_manager as poster_manager_module

    existing_job = Job(
        job_type="Poster Workflow",
        status="running",
        progress=40,
        message="Long-running",
    )
    test_db.add(existing_job)
    test_db.commit()

    def _run_flow_now(job_id, dry_run, release_flow_lock):
        release_flow_lock()

    monkeypatch.setattr("api.poster_manager.run_flow_background_job", _run_flow_now)

    poster_manager_module._flow_running = True
    poster_manager_module._flow_started_at = datetime.now(timezone.utc) - timedelta(minutes=16)

    try:
        response = client.post("/api/poster-manager/flow/run", json={"dry_run": False})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["job_id"], int)
    finally:
        poster_manager_module._flow_running = False
        poster_manager_module._flow_started_at = None


def test_flow_module_delegates_to_module_runners(test_db, monkeypatch):
    """Flow runner should orchestrate by delegating each enabled step to module runner entrypoints."""
    import modules.flow as flow_module

    call_order: list[str] = []
    rename_config: dict = {}

    monkeypatch.setattr("modules.flow.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.flow.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.flow.remove_job_log_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "modules.flow._promote_child_progress_to_parent",
        lambda _db, _parent_job, child_job_id, _start, _end, _fallback, runner, *runner_args: runner(child_job_id, *runner_args),
    )

    def _complete_child(child_job_id: int, step_name: str) -> None:
        child = test_db.query(Job).filter(Job.id == child_job_id).first()
        assert child is not None
        child.status = "completed"
        child.progress = 100
        child.message = f"{step_name} completed"
        test_db.commit()

    def _sync_runner(child_job_id: int, skip_discord: bool = False, **kwargs):
        call_order.append("sync_drives")
        _complete_child(child_job_id, "sync")
        return {"success": True}

    def _rename_runner(child_job_id: int, _config_data: dict, skip_discord: bool = False, **kwargs):
        call_order.append("rename_posters")
        rename_config.update(_config_data)
        _complete_child(child_job_id, "rename")

    def _border_runner(child_job_id: int, _dry_run: bool, _mode: str, **kwargs):
        call_order.append("border_replacer")
        _complete_child(child_job_id, "border")

    def _unmatched_runner(child_job_id: int, skip_discord: bool = False, **kwargs):
        call_order.append("detect_unmatched")
        _complete_child(child_job_id, "unmatched")

    monkeypatch.setattr("modules.flow.run_sync_all_job", _sync_runner)
    monkeypatch.setattr("modules.flow.run_rename_background_job", _rename_runner)
    monkeypatch.setattr("modules.flow.run_border_replacer_background_job", _border_runner)
    monkeypatch.setattr("modules.flow.run_unmatched_detection_background_job", _unmatched_runner)

    flow_config = {
        "sync_drives": {"enabled": True, "stop_on_error": True},
        "rename_posters": {"enabled": True, "stop_on_error": True},
        "border_replacer": {"enabled": True, "stop_on_error": True},
        "detect_unmatched": {"enabled": True, "stop_on_error": True},
    }
    test_db.add(Setting(key="poster_flow_config", value=json.dumps(flow_config)))

    workflow_job = Job(
        job_type="Poster Workflow",
        status="pending",
        progress=0,
        message="Queued",
    )
    test_db.add(workflow_job)
    test_db.commit()
    test_db.refresh(workflow_job)
    workflow_job_id = workflow_job.id

    flow_module.run_flow_background_job(workflow_job_id, dry_run=False)

    refreshed = test_db.query(Job).filter(Job.id == workflow_job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert call_order == [
        "sync_drives",
        "rename_posters",
        "border_replacer",
        "detect_unmatched",
    ]
    assert rename_config.get("auto_run_border") is False
    assert rename_config.get("skip_border_post_processing") is True


def test_flow_config_save_returns_500_when_storage_fails(client, monkeypatch):
    """Flow config save should return 500 when persistence layer raises."""
    import api.poster_manager as poster_manager_module

    def _fail_upsert(*_args, **_kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(poster_manager_module, "upsert_setting", _fail_upsert)

    payload = {
        "sync_drives": {"enabled": True, "stop_on_error": False},
        "rename_posters": {"enabled": True, "stop_on_error": True},
        "detect_unmatched": {"enabled": True, "stop_on_error": False},
        "border_replacer": {"enabled": False, "stop_on_error": False},
        "plex_upload": {"enabled": False, "stop_on_error": False},
    }

    response = client.post("/api/poster-manager/flow/config", json=payload)
    assert response.status_code == 500
    assert "simulated storage failure" in response.json()["detail"]


def test_flow_run_returns_500_and_releases_lock_when_create_job_fails(client, monkeypatch):
    """Flow run should release lock and return 500 when job creation fails."""
    import api.poster_manager as poster_manager_module

    def _fail_create_job(*_args, **_kwargs):
        raise RuntimeError("simulated job creation failure")

    monkeypatch.setattr(poster_manager_module, "create_job", _fail_create_job)

    poster_manager_module._flow_running = False
    poster_manager_module._flow_started_at = None

    response = client.post("/api/poster-manager/flow/run", json={"dry_run": False})
    assert response.status_code == 500
    assert "simulated job creation failure" in response.json()["detail"]

    assert poster_manager_module._flow_running is False
    assert poster_manager_module._flow_started_at is None
