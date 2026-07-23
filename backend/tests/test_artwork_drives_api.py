import json

from models.artwork_drive import ArtworkDrive
from models.job import Job
from models.setting import get_setting
from core.job_queue import job_queue


def test_subscribe_artwork_drive_no_autoscan_when_sync_enabled(client, test_db):
    """A gdrive-synced artwork drive subscribes without queuing an initial scan."""
    drive = ArtworkDrive(name="Art A", drive_id="art-a", subscribed=False, sync_enabled=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/subscribe")
    assert response.status_code == 200
    data = response.json()
    assert data["drive"]["subscribed"] is True
    assert data["scan_job_id"] is None


def test_subscribe_artwork_drive_autoscans_local_only(client, test_db, monkeypatch):
    """A local-only (sync disabled) artwork drive queues an initial scan on subscribe,
    mirroring poster subscribe."""
    submitted = []
    monkeypatch.setattr(job_queue, "submit", lambda *a, **k: submitted.append(a))

    drive = ArtworkDrive(name="Art Local", drive_id="art-local", subscribed=False, sync_enabled=False)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/subscribe")
    assert response.status_code == 200
    data = response.json()
    assert data["drive"]["subscribed"] is True
    assert isinstance(data["scan_job_id"], int)

    job = test_db.query(Job).filter(Job.id == data["scan_job_id"]).first()
    assert job is not None
    assert job.job_type == "Artwork Sync: Art Local"
    assert len(submitted) == 1


def test_subscribe_artwork_drive_adds_to_priority_when_requested(client, test_db):
    drive = ArtworkDrive(name="Art P", drive_id="art-p", subscribed=False, sync_enabled=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/subscribe?add_to_priority=true")
    assert response.status_code == 200
    assert response.json()["added_to_priority"] is True

    setting = get_setting(test_db, "artwork_drive_priority")
    assert setting is not None
    assert drive.id in json.loads(setting.value)["drive_ids"]


def test_subscribe_artwork_drive_default_does_not_touch_priority(client, test_db):
    drive = ArtworkDrive(name="Art Q", drive_id="art-q", subscribed=False, sync_enabled=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/subscribe")
    assert response.status_code == 200
    assert response.json()["added_to_priority"] is False
    assert get_setting(test_db, "artwork_drive_priority") is None


def test_unsubscribe_artwork_drive(client, test_db):
    drive = ArtworkDrive(name="Art B", drive_id="art-b", subscribed=True, sync_enabled=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/unsubscribe")
    assert response.status_code == 200
    assert response.json()["drive"]["subscribed"] is False
