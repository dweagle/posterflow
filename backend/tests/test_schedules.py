from datetime import datetime, timezone


def test_create_schedule_rejects_invalid_drive_group(client):
    """Schedule create should reject unsupported drive_group values."""
    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "sync",
            "name": "Invalid Group",
            "enabled": True,
            "drive_group": "INVALID",
            "schedule_type": "daily",
            "schedule_value": "02:00",
        },
    )
    assert response.status_code == 400
    assert "Invalid drive_group" in response.json()["detail"]


def test_create_schedule_rejects_invalid_schedule_type(client):
    """Schedule create should reject unsupported schedule_type values."""
    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "sync",
            "name": "Invalid Type",
            "enabled": True,
            "schedule_type": "every_minute",
            "schedule_value": "*",
        },
    )
    assert response.status_code == 400
    assert "Invalid schedule_type" in response.json()["detail"]


def test_create_schedule_rejects_plex_upload_job_type(client):
    """Schedule create should reject plex_upload now that it is no longer schedulable."""
    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "plex_upload",
            "name": "Nightly Plex Upload",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "03:30",
        },
    )
    assert response.status_code == 400
    assert "Invalid job_type" in response.json()["detail"]


def test_create_schedule_accepts_idarr_job_type(client, monkeypatch):
    """Schedule create should accept idarr as a schedulable job type."""
    import api.schedules as schedules_module

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: None)

    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "idarr",
            "name": "Nightly IDarr",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "03:30",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["job_type"] == "idarr"
    assert payload["name"] == "Nightly IDarr"


def test_create_schedule_accepts_maker_monitor_job_type(client, monkeypatch):
    """Schedule create should accept maker_monitor as a schedulable job type."""
    import api.schedules as schedules_module

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: None)

    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "maker_monitor",
            "name": "Nightly Maker Monitor",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "03:30",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["job_type"] == "maker_monitor"
    assert payload["name"] == "Nightly Maker Monitor"


def test_create_schedule_accepts_idarr_scope_token(client, monkeypatch):
    """Schedule create should accept idarr scope token in drive_group."""
    import api.schedules as schedules_module

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: None)

    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "idarr",
            "name": "Nightly IDarr Scope",
            "enabled": True,
            "drive_group": "idarr_target_1",
            "schedule_type": "daily",
            "schedule_value": "03:30",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["drive_group"] == "idarr_target_1"


def test_create_schedule_rejects_drive_id_for_idarr(client):
    """Drive targeting should be rejected for non-sync job types like idarr."""
    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "idarr",
            "name": "IDarr With Drive",
            "enabled": True,
            "drive_id": 1,
            "schedule_type": "daily",
            "schedule_value": "03:30",
        },
    )
    assert response.status_code == 400
    assert "drive_id is only valid for gdrive_sync schedules" in response.json()["detail"]


def test_create_schedule_rejects_drive_id_for_plex_upload(client):
    """plex_upload should be rejected as invalid job_type before drive validation."""
    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "plex_upload",
            "name": "Invalid Plex Upload Target",
            "enabled": True,
            "drive_id": 1,
            "schedule_type": "daily",
            "schedule_value": "03:30",
        },
    )
    assert response.status_code == 400
    assert "Invalid job_type" in response.json()["detail"]


def test_get_schedule_returns_404_for_missing_id(client):
    """Fetching a missing schedule should return 404."""
    response = client.get("/api/schedules/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Schedule not found"


def test_schedule_create_update_delete_lifecycle(client, monkeypatch):
    """Schedule create, update, and delete should complete successfully."""
    import api.schedules as schedules_module

    next_run = datetime.now(timezone.utc)

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: next_run)

    create_response = client.post(
        "/api/schedules/",
        json={
            "job_type": "sync",
            "name": "Nightly Sync",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "02:30",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["name"] == "Nightly Sync"
    assert created["enabled"] is True
    schedule_id = created["id"]

    update_response = client.put(
        f"/api/schedules/{schedule_id}",
        json={
            "name": "Nightly Sync Updated",
            "enabled": False,
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "Nightly Sync Updated"
    assert updated["enabled"] is False

    delete_response = client.delete(f"/api/schedules/{schedule_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Schedule deleted successfully"


def test_update_schedule_rejects_invalid_schedule_type(client, monkeypatch):
    """Schedule update should reject unsupported schedule_type values."""
    import api.schedules as schedules_module

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: None)

    create_response = client.post(
        "/api/schedules/",
        json={
            "job_type": "sync",
            "name": "Update Type Validation",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "03:00",
        },
    )
    assert create_response.status_code == 200
    schedule_id = create_response.json()["id"]

    update_response = client.put(
        f"/api/schedules/{schedule_id}",
        json={"schedule_type": "bad_type"},
    )
    assert update_response.status_code == 400
    assert "Invalid schedule_type" in update_response.json()["detail"]


def test_delete_schedule_returns_404_for_missing_id(client):
    """Deleting a missing schedule should return 404."""
    response = client.delete("/api/schedules/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Schedule not found"


def test_create_schedule_rejects_invalid_drive_id(client):
    """Schedule create should reject unknown drive IDs."""
    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "sync",
            "name": "Invalid Drive",
            "enabled": True,
            "drive_id": 999999,
            "schedule_type": "daily",
            "schedule_value": "04:00",
        },
    )
    assert response.status_code == 400
    assert "Drive with ID 999999 not found" in response.json()["detail"]


def test_update_schedule_rejects_invalid_drive_id(client, monkeypatch):
    """Schedule update should reject unknown drive IDs."""
    import api.schedules as schedules_module

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: None)

    create_response = client.post(
        "/api/schedules/",
        json={
            "job_type": "sync",
            "name": "Update Drive Validation",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "05:00",
        },
    )
    assert create_response.status_code == 200
    schedule_id = create_response.json()["id"]

    update_response = client.put(
        f"/api/schedules/{schedule_id}",
        json={"drive_id": 999999},
    )
    assert update_response.status_code == 400
    assert "Drive with ID 999999 not found" in update_response.json()["detail"]


def test_create_schedule_accepts_artwork_sync_job_type(client, monkeypatch):
    """Artwork drives sync separately from poster drives, so they schedule separately."""
    import api.schedules as schedules_module

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: None)

    response = client.post(
        "/api/schedules/",
        json={
            "job_type": "artwork_sync",
            "name": "Nightly Artwork Sync",
            "enabled": True,
            "schedule_type": "daily",
            "schedule_value": "04:00",
        },
    )
    assert response.status_code == 200
    assert response.json()["job_type"] == "artwork_sync"


def test_artwork_sync_schedules_are_not_returned_by_the_poster_sync_filter(client, monkeypatch):
    """gdrive_sync/sync are aliases of each other; artwork_sync is its own type."""
    import api.schedules as schedules_module

    monkeypatch.setattr(schedules_module, "update_schedules", lambda: None)
    monkeypatch.setattr(schedules_module, "get_schedule_next_run", lambda _id: None)

    for job_type, name in (("artwork_sync", "Artwork"), ("gdrive_sync", "Posters")):
        client.post("/api/schedules/", json={
            "job_type": job_type, "name": name, "enabled": True,
            "schedule_type": "daily", "schedule_value": "05:00",
        })

    poster_names = [s["name"] for s in client.get("/api/schedules/?job_type=gdrive_sync").json()]
    artwork_names = [s["name"] for s in client.get("/api/schedules/?job_type=artwork_sync").json()]

    assert poster_names == ["Posters"]
    assert artwork_names == ["Artwork"]


def test_scheduled_artwork_sync_queues_the_artwork_job(test_db, monkeypatch):
    """The scheduler wrapper must queue the artwork sync job, not the poster one."""
    import core.scheduler as scheduler_module
    from models.artwork_drive import ArtworkDrive

    test_db.add(ArtworkDrive(name="MakerA", drive_id="makerA", subscribed=True))
    test_db.commit()

    submitted = []
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)
    monkeypatch.setattr(
        scheduler_module.job_queue, "submit",
        lambda runner, job_id, *a, **k: submitted.append((runner.__name__, job_id)),
    )

    scheduler_module.sync_all_artwork_drives_for_schedule()

    assert len(submitted) == 1
    assert submitted[0][0] == "sync_all_artwork_drives_job"

    from models.job import Job
    job = test_db.query(Job).filter(Job.id == submitted[0][1]).first()
    assert job.job_type == "Artwork Sync All (1 drives)"
