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


# --- Per-drive artwork type selection ---

def test_subscribe_with_selected_types_stores_only_those(client, test_db):
    drive = ArtworkDrive(name="Art Types", drive_id="art-types", subscribed=False, sync_enabled=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/subscribe", json={"synced_types": ["logo"]})
    assert response.status_code == 200
    assert response.json()["drive"]["synced_types"] == ["logo"]

    test_db.refresh(drive)
    assert drive.synced_type_list == ["logo"]


def test_subscribe_defaults_to_all_types(client, test_db):
    drive = ArtworkDrive(name="Art All", drive_id="art-all", subscribed=False, sync_enabled=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/subscribe")
    assert response.status_code == 200
    assert response.json()["drive"]["synced_types"] == ["logo", "background", "squareart"]


def test_update_rejects_empty_or_unknown_types(client, test_db):
    drive = ArtworkDrive(name="Art Bad", drive_id="art-bad", subscribed=True, sync_enabled=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    assert client.patch(f"/api/artwork-drives/{drive.id}", json={"synced_types": []}).status_code == 400
    assert client.patch(f"/api/artwork-drives/{drive.id}", json={"synced_types": ["poster"]}).status_code == 400

    test_db.refresh(drive)
    assert drive.synced_type_list == ["logo", "background", "squareart"]


def test_dropping_a_type_deletes_its_files_and_rows(client, test_db, tmp_path):
    """rclone filters hide the folder rather than prune it, so the drop has to clean up here —
    otherwise the deselected files sit on disk indexed forever."""
    from models.artwork import Artwork

    folder = tmp_path / "artwork" / "ArtDrop"
    (folder / "logos").mkdir(parents=True)
    (folder / "backgrounds").mkdir(parents=True)
    logo = folder / "logos" / "Inception (2010) - logo.png"
    background = folder / "backgrounds" / "Inception (2010) - background.jpg"
    logo.write_bytes(b"l")
    background.write_bytes(b"b")

    drive = ArtworkDrive(name="Art Drop", drive_id="art-drop", subscribed=True, sync_enabled=True, custom_path=str(folder))
    test_db.add(drive)
    test_db.add(Artwork(artwork_drive_id="art-drop", file_path=str(logo), file_name=logo.name, artwork_type="logo"))
    test_db.add(Artwork(artwork_drive_id="art-drop", file_path=str(background), file_name=background.name, artwork_type="background"))
    test_db.commit()
    test_db.refresh(drive)

    response = client.patch(f"/api/artwork-drives/{drive.id}", json={"synced_types": ["logo"]})
    assert response.status_code == 200
    assert response.json()["synced_types"] == ["logo"]

    assert logo.exists()
    assert not (folder / "backgrounds").exists()
    rows = test_db.query(Artwork).filter(Artwork.artwork_drive_id == "art-drop").all()
    assert [r.artwork_type for r in rows] == ["logo"]


def test_adding_a_type_back_keeps_existing_files(client, test_db, tmp_path):
    from models.artwork import Artwork

    folder = tmp_path / "artwork" / "ArtAdd"
    (folder / "logos").mkdir(parents=True)
    logo = folder / "logos" / "Inception (2010) - logo.png"
    logo.write_bytes(b"l")

    drive = ArtworkDrive(name="Art Add", drive_id="art-add", subscribed=True, sync_enabled=True,
                         custom_path=str(folder), synced_types="logo")
    test_db.add(drive)
    test_db.add(Artwork(artwork_drive_id="art-add", file_path=str(logo), file_name=logo.name, artwork_type="logo"))
    test_db.commit()
    test_db.refresh(drive)

    response = client.patch(f"/api/artwork-drives/{drive.id}", json={"synced_types": ["logo", "background"]})
    assert response.status_code == 200
    assert response.json()["synced_types"] == ["logo", "background"]
    assert logo.exists()
    assert test_db.query(Artwork).filter(Artwork.artwork_drive_id == "art-add").count() == 1


def test_resubscribe_without_types_keeps_the_previous_selection(client, test_db):
    """Bulk subscribe sends no selection — it must not silently re-enable types the user dropped."""
    drive = ArtworkDrive(name="Art Keep", drive_id="art-keep", subscribed=False, sync_enabled=True, synced_types="logo")
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/artwork-drives/{drive.id}/subscribe")
    assert response.status_code == 200
    assert response.json()["drive"]["synced_types"] == ["logo"]


def _artwork_priority(test_db, drive_ids):
    from models.setting import upsert_setting
    upsert_setting(test_db, "artwork_drive_priority", json.dumps({"drive_ids": drive_ids}))
    test_db.commit()


def _make_artwork_drives(test_db, names):
    drives = [ArtworkDrive(name=n, drive_id=n, subscribed=True, sync_enabled=True) for n in names]
    test_db.add_all(drives)
    test_db.commit()
    for d in drives:
        test_db.refresh(d)
    return drives


def test_unsubscribe_artwork_drive_prunes_from_priority(client, test_db):
    """Unsubscribe removes the drive from artwork priority and stashes its position."""
    one, two = _make_artwork_drives(test_db, ["art-prio-1", "art-prio-2"])
    _artwork_priority(test_db, [one.id, two.id])

    response = client.post(f"/api/artwork-drives/{one.id}/unsubscribe")
    assert response.status_code == 200
    assert response.json()["removed_from_priority"] is True

    assert json.loads(get_setting(test_db, "artwork_drive_priority").value)["drive_ids"] == [two.id]
    assert json.loads(get_setting(test_db, "artwork_drive_priority_removed").value) == {str(one.id): 0}


def test_resubscribe_artwork_drive_restores_its_old_position(client, test_db):
    """A resubscribed artwork drive goes back where it sat, not onto the bottom."""
    one, two, three = _make_artwork_drives(test_db, ["art-pos-1", "art-pos-2", "art-pos-3"])
    _artwork_priority(test_db, [one.id, two.id, three.id])

    client.post(f"/api/artwork-drives/{two.id}/unsubscribe")
    assert json.loads(get_setting(test_db, "artwork_drive_priority").value)["drive_ids"] == [one.id, three.id]

    response = client.post(f"/api/artwork-drives/{two.id}/subscribe")
    assert response.status_code == 200
    assert response.json()["restored_to_priority"] is True

    assert json.loads(get_setting(test_db, "artwork_drive_priority").value)["drive_ids"] == [one.id, two.id, three.id]
    assert json.loads(get_setting(test_db, "artwork_drive_priority_removed").value) == {}


def test_delete_artwork_drive_prunes_from_priority(client, test_db):
    """Deleting a custom artwork drive must not leave its id dangling in the priority list."""
    one, two = _make_artwork_drives(test_db, ["art-del-1", "art-del-2"])
    one.is_custom = True
    test_db.commit()
    _artwork_priority(test_db, [one.id, two.id])

    response = client.delete(f"/api/artwork-drives/{one.id}?confirm=true")
    assert response.status_code == 200

    assert json.loads(get_setting(test_db, "artwork_drive_priority").value)["drive_ids"] == [two.id]


def test_artwork_unsubscribe_all_then_resubscribe_all_restores_order(client, test_db):
    """Bulk round-trip must come back in the original order, not reversed by stale stash indices."""
    drives = _make_artwork_drives(test_db, [f"art-order-{i}" for i in range(4)])
    ids = [d.id for d in drives]
    _artwork_priority(test_db, ids)

    for did in ids:
        client.post(f"/api/artwork-drives/{did}/unsubscribe")
    for did in ids:
        client.post(f"/api/artwork-drives/{did}/subscribe")

    assert json.loads(get_setting(test_db, "artwork_drive_priority").value)["drive_ids"] == ids


def test_artwork_manual_reorder_clears_stashed_positions(client, test_db):
    """A manual priority save defines a fresh order; earlier stashes must not apply anymore."""
    one, two, three = _make_artwork_drives(test_db, ["art-fresh-1", "art-fresh-2", "art-fresh-3"])
    _artwork_priority(test_db, [one.id, two.id, three.id])

    client.post(f"/api/artwork-drives/{one.id}/unsubscribe")
    response = client.post("/api/artwork-drives/priority", json={"drive_ids": [three.id, two.id]})
    assert response.status_code == 200

    response = client.post(f"/api/artwork-drives/{one.id}/subscribe")
    assert response.json()["restored_to_priority"] is False
    assert json.loads(get_setting(test_db, "artwork_drive_priority").value)["drive_ids"] == [three.id, two.id]


def test_subscribe_artwork_drive_appends_when_asked(client, test_db):
    """add_to_priority appends a never-before-listed drive to the bottom."""
    one, two = _make_artwork_drives(test_db, ["art-app-1", "art-app-2"])
    two.subscribed = False
    test_db.commit()
    _artwork_priority(test_db, [one.id])

    response = client.post(f"/api/artwork-drives/{two.id}/subscribe?add_to_priority=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["added_to_priority"] is True
    assert payload["restored_to_priority"] is False

    assert json.loads(get_setting(test_db, "artwork_drive_priority").value)["drive_ids"] == [one.id, two.id]
