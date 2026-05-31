import json
from models.drive import Drive
from models.setting import Setting

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

