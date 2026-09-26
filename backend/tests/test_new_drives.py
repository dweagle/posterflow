import json
from datetime import datetime, timedelta, timezone

from api.drives import load_drives_from_json
from api.artwork_drives import load_artwork_drives_from_json
from models.drive import Drive
from models.artwork_drive import ArtworkDrive
from models.setting import get_setting, upsert_setting
from services.new_drives import SETTING_KEYS, load_new_drives


def _poster_payload(*ids):
    return {"drives": [{"name": f"Drive {i}", "drive_id": i, "style_type": "MM2K"} for i in ids]}


def _artwork_payload(*ids):
    return {"drives": [{"name": f"Art {i}", "drive_id": i} for i in ids]}


def test_first_seed_flags_nothing(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a", "b"))
    assert load_new_drives(test_db, "poster") == {}
    assert client.get("/api/drives/new").json() == {"poster": [], "artwork": [], "unseen_count": 0}


def test_later_additions_are_flagged_and_listed(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a"))
    load_drives_from_json(test_db, _poster_payload("a", "b"))

    body = client.get("/api/drives/new").json()
    assert [d["drive_id"] for d in body["poster"]] == ["b"]
    assert body["poster"][0]["name"] == "Drive b"
    assert body["poster"][0]["style_type"] == "MM2K"
    assert body["poster"][0]["seen"] is False
    assert body["unseen_count"] == 1


def test_reactivated_drive_is_flagged(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a", "b"))
    load_drives_from_json(test_db, _poster_payload("a"))
    assert test_db.query(Drive).filter(Drive.drive_id == "b").one().is_deprecated is True

    load_drives_from_json(test_db, _poster_payload("a", "b"))
    assert [d["drive_id"] for d in client.get("/api/drives/new").json()["poster"]] == ["b"]


def test_deprecation_clears_the_flag(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a"))
    load_drives_from_json(test_db, _poster_payload("a", "b"))
    load_drives_from_json(test_db, _poster_payload("a"))
    assert load_new_drives(test_db, "poster") == {}


def test_subscribing_clears_the_flag(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a"))
    load_drives_from_json(test_db, _poster_payload("a", "b"))
    drive = test_db.query(Drive).filter(Drive.drive_id == "b").one()

    assert client.post(f"/api/drives/{drive.id}/subscribe").status_code == 200
    assert client.get("/api/drives/new").json()["poster"] == []


def test_seen_clears_the_badge_but_keeps_the_tags(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a"))
    load_drives_from_json(test_db, _poster_payload("a", "b"))

    assert client.post("/api/drives/new/seen").json() == {"success": True}
    body = client.get("/api/drives/new").json()
    assert body["unseen_count"] == 0
    assert [d["drive_id"] for d in body["poster"]] == ["b"]
    assert body["poster"][0]["seen"] is True


def test_dismiss_forgets_everything(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a"))
    load_drives_from_json(test_db, _poster_payload("a", "b"))
    load_artwork_drives_from_json(test_db, _artwork_payload("x"))
    load_artwork_drives_from_json(test_db, _artwork_payload("x", "y"))
    assert client.get("/api/drives/new").json()["unseen_count"] == 2

    assert client.post("/api/drives/new/dismiss").json() == {"success": True}
    assert client.get("/api/drives/new").json() == {"poster": [], "artwork": [], "unseen_count": 0}


def test_flags_expire_after_the_ttl(client, test_db):
    load_drives_from_json(test_db, _poster_payload("a"))
    load_drives_from_json(test_db, _poster_payload("a", "b"))
    stale = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    upsert_setting(test_db, SETTING_KEYS["poster"], json.dumps({"b": {"added_at": stale, "seen": False}}))
    test_db.commit()

    assert client.get("/api/drives/new").json()["poster"] == []


def test_artwork_additions_are_flagged_and_subscribe_clears(client, test_db):
    load_artwork_drives_from_json(test_db, _artwork_payload("x"))
    load_artwork_drives_from_json(test_db, _artwork_payload("x", "y"))

    body = client.get("/api/drives/new").json()
    assert [d["drive_id"] for d in body["artwork"]] == ["y"]
    assert body["artwork"][0]["style_type"] is None
    assert body["unseen_count"] == 1

    drive = test_db.query(ArtworkDrive).filter(ArtworkDrive.drive_id == "y").one()
    assert client.post(f"/api/artwork-drives/{drive.id}/subscribe").status_code == 200
    assert client.get("/api/drives/new").json()["artwork"] == []


def test_garbage_setting_is_ignored(client, test_db):
    upsert_setting(test_db, SETTING_KEYS["poster"], "not json")
    test_db.commit()
    assert client.get("/api/drives/new").json()["poster"] == []
    assert get_setting(test_db, SETTING_KEYS["poster"]).value == "not json"
