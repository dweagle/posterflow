import json
from models.drive import Drive
from models.setting import Setting, get_setting

def test_subscribe_drive_adds_to_priority_when_requested(client, test_db):
    """Subscribing with add_to_priority=true appends the drive to the bottom of poster priority."""
    drive = Drive(name="P Drive", drive_id="p-prio", style_type="MM2K", subscribed=False)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/drives/{drive.id}/subscribe?add_to_priority=true")
    assert response.status_code == 200
    assert response.json()["added_to_priority"] is True

    setting = get_setting(test_db, "poster_drive_priority")
    assert setting is not None
    assert drive.id in json.loads(setting.value)["drive_ids"]


def test_subscribe_drive_default_does_not_add_to_priority(client, test_db):
    drive = Drive(name="P Drive2", drive_id="p-noprio", style_type="MM2K", subscribed=False)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/drives/{drive.id}/subscribe")
    assert response.status_code == 200
    assert response.json()["added_to_priority"] is False


def test_subscribe_drive(client, test_db):
    """Test subscribing to a drive"""
    # Create a test drive
    drive = Drive(
        name="Test Drive",
        drive_id="test-123",
        style_type="MM2K",
        subscribed=False
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)
    
    # Subscribe to it
    response = client.post(f"/api/drives/{drive.id}/subscribe")
    assert response.status_code == 200
    data = response.json()
    assert data["drive"]["subscribed"] is True

def test_unsubscribe_drive(client, test_db):
    """Test unsubscribing from a drive"""
    # Create a subscribed drive
    drive = Drive(
        name="Test Drive",
        drive_id="test-123",
        style_type="MM2K",
        subscribed=True
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)
    
    # Unsubscribe from it
    response = client.post(f"/api/drives/{drive.id}/unsubscribe")
    assert response.status_code == 200
    data = response.json()
    assert data["drive"]["subscribed"] is False
    assert data["files_deleted"] is False
    assert data["poster_records_deleted"] == 0


def test_unsubscribe_default_keeps_local_files_and_records(client, test_db, tmp_path):
    from models.poster import Poster

    folder = tmp_path / "Keep_Drive"
    folder.mkdir()
    (folder / "Movie (2020).jpg").write_bytes(b"img")
    drive = Drive(name="Keep Drive", drive_id="keep-1", style_type="MM2K",
                  subscribed=True, custom_path=str(folder))
    test_db.add(drive)
    test_db.add(Poster(drive_id="keep-1", file_name="Movie (2020).jpg",
                       file_path=str(folder / "Movie (2020).jpg")))
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/drives/{drive.id}/unsubscribe")
    assert response.status_code == 200
    assert folder.exists()
    assert test_db.query(Poster).filter(Poster.drive_id == "keep-1").count() == 1


def test_unsubscribe_delete_files_removes_folder_and_poster_records(client, test_db, tmp_path):
    from models.poster import Poster

    folder = tmp_path / "Bye_Drive"
    folder.mkdir()
    (folder / "Movie (2020).jpg").write_bytes(b"img")
    drive = Drive(name="Bye Drive", drive_id="bye-1", style_type="MM2K",
                  subscribed=True, custom_path=str(folder), sync_file_count=1)
    other = Drive(name="Other Drive", drive_id="other-1", style_type="MM2K", subscribed=True)
    test_db.add_all([drive, other])
    test_db.add(Poster(drive_id="bye-1", file_name="Movie (2020).jpg",
                       file_path=str(folder / "Movie (2020).jpg")))
    test_db.add(Poster(drive_id="other-1", file_name="Other (2021).jpg",
                       file_path="/gdrive/MM2K/Other_Drive/Other (2021).jpg"))
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/drives/{drive.id}/unsubscribe?delete_files=true")
    assert response.status_code == 200
    data = response.json()
    assert data["drive"]["subscribed"] is False
    assert data["files_deleted"] is True
    assert data["poster_records_deleted"] == 1

    assert not folder.exists()
    test_db.expire_all()
    assert test_db.query(Poster).filter(Poster.drive_id == "bye-1").count() == 0
    # Other drives' records are untouched.
    assert test_db.query(Poster).filter(Poster.drive_id == "other-1").count() == 1
    assert test_db.query(Drive).filter(Drive.drive_id == "bye-1").first().sync_file_count == 0


def test_unsubscribe_delete_files_with_no_local_folder(client, test_db, tmp_path):
    drive = Drive(name="Ghost Drive", drive_id="ghost-1", style_type="MM2K",
                  subscribed=True, custom_path=str(tmp_path / "never-synced"))
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.post(f"/api/drives/{drive.id}/unsubscribe?delete_files=true")
    assert response.status_code == 200
    data = response.json()
    assert data["drive"]["subscribed"] is False
    assert data["files_deleted"] is False


def test_unsubscribe_prunes_drive_from_poster_priority(client, test_db):
    drive_one = Drive(
        name="Drive One",
        drive_id="priority-drive-1",
        style_type="MM2K",
        subscribed=True,
    )
    drive_two = Drive(
        name="Drive Two",
        drive_id="priority-drive-2",
        style_type="CL2K",
        subscribed=True,
    )
    test_db.add_all([drive_one, drive_two])
    test_db.commit()
    test_db.refresh(drive_one)
    test_db.refresh(drive_two)

    setting = Setting(
        key="poster_drive_priority",
        value=json.dumps(
            {
                "drive_ids": [drive_one.id, drive_two.id],
                "enabled_styles": ["MM2K", "CL2K", "Custom"],
            }
        ),
    )
    test_db.add(setting)
    test_db.commit()

    response = client.post(f"/api/drives/{drive_one.id}/unsubscribe")
    assert response.status_code == 200
    assert response.json()["removed_from_priority"] is True

    updated_setting = test_db.query(Setting).filter(Setting.key == "poster_drive_priority").first()
    assert updated_setting is not None
    updated_priority = json.loads(updated_setting.value)
    assert updated_priority["drive_ids"] == [drive_two.id]

    removed_setting = test_db.query(Setting).filter(Setting.key == "poster_drive_priority_removed").first()
    assert removed_setting is not None
    removed_map = json.loads(removed_setting.value)
    assert removed_map[str(drive_one.id)] == 0


def test_subscribe_restores_drive_to_previous_priority_position(client, test_db):
    drive_one = Drive(
        name="Drive One",
        drive_id="priority-restore-drive-1",
        style_type="MM2K",
        subscribed=False,
    )
    drive_two = Drive(
        name="Drive Two",
        drive_id="priority-restore-drive-2",
        style_type="CL2K",
        subscribed=True,
    )
    test_db.add_all([drive_one, drive_two])
    test_db.commit()
    test_db.refresh(drive_one)
    test_db.refresh(drive_two)

    test_db.add(
        Setting(
            key="poster_drive_priority",
            value=json.dumps(
                {
                    "drive_ids": [drive_two.id],
                    "enabled_styles": ["MM2K", "CL2K", "Custom"],
                }
            ),
        )
    )
    test_db.add(
        Setting(
            key="poster_drive_priority_removed",
            value=json.dumps({str(drive_one.id): 0}),
        )
    )
    test_db.commit()

    response = client.post(f"/api/drives/{drive_one.id}/subscribe")
    assert response.status_code == 200
    payload = response.json()
    assert payload["drive"]["subscribed"] is True
    assert payload["restored_to_priority"] is True

    updated_setting = test_db.query(Setting).filter(Setting.key == "poster_drive_priority").first()
    assert updated_setting is not None
    updated_priority = json.loads(updated_setting.value)
    assert updated_priority["drive_ids"] == [drive_one.id, drive_two.id]

    removed_setting = test_db.query(Setting).filter(Setting.key == "poster_drive_priority_removed").first()
    assert removed_setting is not None
    assert json.loads(removed_setting.value) == {}


def test_subscribe_without_stashed_priority_does_not_restore(client, test_db):
    drive = Drive(
        name="Drive No Stash",
        drive_id="priority-no-stash",
        style_type="MM2K",
        subscribed=False,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    test_db.add(
        Setting(
            key="poster_drive_priority",
            value=json.dumps({"drive_ids": [], "enabled_styles": ["MM2K", "CL2K", "Custom"]}),
        )
    )
    test_db.commit()

    response = client.post(f"/api/drives/{drive.id}/subscribe")
    assert response.status_code == 200
    payload = response.json()
    assert payload["restored_to_priority"] is False

    updated_setting = test_db.query(Setting).filter(Setting.key == "poster_drive_priority").first()
    assert updated_setting is not None
    updated_priority = json.loads(updated_setting.value)
    assert updated_priority["drive_ids"] == []


def _seed_priority_drives(test_db, count, prefix):
    drives = [Drive(name=f"{prefix} {i}", drive_id=f"{prefix}-{i}", style_type="MM2K", subscribed=True) for i in range(count)]
    test_db.add_all(drives)
    test_db.commit()
    for d in drives:
        test_db.refresh(d)
    ids = [d.id for d in drives]
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": ids, "enabled_styles": ["MM2K", "CL2K", "Custom"]})))
    test_db.commit()
    return ids


def _poster_priority_ids(test_db):
    return json.loads(test_db.query(Setting).filter(Setting.key == "poster_drive_priority").first().value)["drive_ids"]


def test_unsubscribe_all_then_resubscribe_all_restores_original_order(client, test_db):
    """Bulk round-trip must come back in the original order, not reversed by stale stash indices."""
    ids = _seed_priority_drives(test_db, 5, "order")

    for did in ids:
        assert client.post(f"/api/drives/{did}/unsubscribe").status_code == 200
    assert _poster_priority_ids(test_db) == []
    for did in ids:
        assert client.post(f"/api/drives/{did}/subscribe").status_code == 200

    assert _poster_priority_ids(test_db) == ids


def test_adjacent_unsubscribes_restore_to_original_positions(client, test_db):
    """Removing neighbours shifts later indices; restore must not swap them."""
    ids = _seed_priority_drives(test_db, 5, "adj")

    for did in (ids[1], ids[2]):
        client.post(f"/api/drives/{did}/unsubscribe")
    for did in (ids[1], ids[2]):
        client.post(f"/api/drives/{did}/subscribe")

    assert _poster_priority_ids(test_db) == ids


def test_manual_reorder_clears_stashed_positions(client, test_db):
    """A manual priority save defines a fresh order; earlier stashes must not apply anymore."""
    ids = _seed_priority_drives(test_db, 3, "fresh")

    client.post(f"/api/drives/{ids[0]}/unsubscribe")
    reordered = [ids[2], ids[1]]
    response = client.post("/api/posterflow/priority", json={"drive_ids": reordered, "enabled_styles": ["MM2K"]})
    assert response.status_code == 200

    response = client.post(f"/api/drives/{ids[0]}/subscribe")
    assert response.json()["restored_to_priority"] is False
    assert _poster_priority_ids(test_db) == reordered


def test_delete_drive_prunes_drive_from_poster_priority(client, test_db):
    drive = Drive(
        name="Custom Drive",
        drive_id="priority-delete-drive",
        style_type="Custom",
        subscribed=True,
        is_custom=True,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    setting = Setting(
        key="poster_drive_priority",
        value=json.dumps(
            {
                "drive_ids": [drive.id],
                "enabled_styles": ["MM2K", "CL2K", "Custom"],
            }
        ),
    )
    test_db.add(setting)
    test_db.commit()

    response = client.delete(f"/api/drives/{drive.id}?confirm=true")
    assert response.status_code == 200

    updated_setting = test_db.query(Setting).filter(Setting.key == "poster_drive_priority").first()
    assert updated_setting is not None
    updated_priority = json.loads(updated_setting.value)
    assert updated_priority["drive_ids"] == []

def test_subscribe_nonexistent_drive(client):
    """Test subscribing to a drive that doesn't exist"""
    response = client.post("/api/drives/99999/subscribe")
    assert response.status_code == 404


def test_update_drive_sets_custom_path(client, test_db):
    drive = Drive(name="Test Drive", drive_id="path-set-1", style_type="MM2K", subscribed=True)
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.patch(f"/api/drives/{drive.id}", json={"custom_path": "/some/path"})
    assert response.status_code == 200
    assert response.json()["custom_path"] == "/some/path"

    test_db.refresh(drive)
    assert drive.custom_path == "/some/path"


def test_update_drive_clears_custom_path_with_empty_string(client, test_db):
    drive = Drive(name="Test Drive", drive_id="path-clear-empty-1", style_type="MM2K", subscribed=True, custom_path="/existing/path")
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.patch(f"/api/drives/{drive.id}", json={"custom_path": ""})
    assert response.status_code == 200
    assert response.json()["custom_path"] is None

    test_db.refresh(drive)
    assert drive.custom_path is None


def test_update_drive_clears_custom_path_with_null(client, test_db):
    drive = Drive(name="Test Drive", drive_id="path-clear-null-1", style_type="MM2K", subscribed=True, custom_path="/existing/path")
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.patch(f"/api/drives/{drive.id}", json={"custom_path": None})
    assert response.status_code == 200
    assert response.json()["custom_path"] is None

    test_db.refresh(drive)
    assert drive.custom_path is None


def test_update_drive_omitting_custom_path_leaves_it_unchanged(client, test_db):
    drive = Drive(name="Test Drive", drive_id="path-unchanged-1", style_type="MM2K", subscribed=True, custom_path="/keep/this/path")
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    response = client.patch(f"/api/drives/{drive.id}", json={"priority": 5})
    assert response.status_code == 200
    assert response.json()["custom_path"] == "/keep/this/path"

    test_db.refresh(drive)
    assert drive.custom_path == "/keep/this/path"

