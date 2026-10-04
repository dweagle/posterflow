from zoneinfo import ZoneInfo

import pytest

import api.settings as settings_api
import core.app_timezone as app_timezone
import core.scheduler as scheduler_module
from models.setting import Setting


@pytest.fixture(autouse=True)
def restore_timezone_globals():
    cached = app_timezone._cached
    scheduler_tz = scheduler_module.scheduler.timezone
    yield
    app_timezone._cached = cached
    scheduler_module.scheduler.timezone = scheduler_tz


def _saved_timezone(db):
    row = db.query(Setting).filter(Setting.key == "timezone").first()
    return row.value if row else None


def test_saving_a_timezone_stores_it_and_repoints_the_scheduler(client, test_db):
    response = client.post("/api/settings/bulk", json={"timezone": " Asia/Tokyo "})

    assert response.status_code == 200
    assert _saved_timezone(test_db) == "Asia/Tokyo"
    assert str(app_timezone.get_app_timezone()) == "Asia/Tokyo"
    assert str(scheduler_module.scheduler.timezone) == "Asia/Tokyo"

    payload = client.get("/api/settings/").json()
    assert payload["timezone"] == "Asia/Tokyo"
    assert payload["effective_timezone"] == "Asia/Tokyo"


def test_unknown_timezone_is_rejected_and_not_stored(client, test_db):
    assert client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"}).status_code == 200

    response = client.post("/api/settings/bulk", json={"timezone": "Not/AZone"})

    assert response.status_code == 400
    assert "Not/AZone" in response.json()["detail"]
    assert _saved_timezone(test_db) == "Asia/Tokyo"
    assert str(app_timezone.get_app_timezone()) == "Asia/Tokyo"


def test_blank_timezone_goes_back_to_the_host(client, test_db, monkeypatch):
    monkeypatch.setattr(app_timezone, "host_timezone", lambda: ZoneInfo("Europe/Amsterdam"))
    assert client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"}).status_code == 200

    response = client.post("/api/settings/bulk", json={"timezone": ""})

    assert response.status_code == 200
    assert _saved_timezone(test_db) == ""
    assert client.get("/api/settings/").json()["effective_timezone"] == "Europe/Amsterdam"


def test_schedules_are_rebuilt_only_when_the_timezone_is_saved(client, monkeypatch):
    rebuilds = []
    monkeypatch.setattr(settings_api, "update_schedules", lambda: rebuilds.append(1))

    assert client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"}).status_code == 200
    assert len(rebuilds) == 1

    assert client.post("/api/settings/bulk", json={"poster_destination": "remote:posters"}).status_code == 200
    assert len(rebuilds) == 1


def test_a_failed_rebuild_does_not_fail_the_save(client, test_db, monkeypatch):
    def boom():
        raise RuntimeError("scheduler is not running")

    monkeypatch.setattr(settings_api, "update_schedules", boom)

    response = client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"})

    assert response.status_code == 200
    assert _saved_timezone(test_db) == "Asia/Tokyo"
    assert str(app_timezone.get_app_timezone()) == "Asia/Tokyo"


def test_timezone_list_only_offers_zones_the_server_can_run(client):
    zones = client.get("/api/settings/timezones").json()

    assert zones[0] == "UTC"
    assert "America/New_York" in zones
    assert len(zones) == len(set(zones))
    assert all(app_timezone.parse_timezone(zone) is not None for zone in zones)
    assert all(zone == "UTC" or "/" in zone for zone in zones)


def test_effective_timezone_cannot_be_written(client, test_db):
    response = client.post("/api/settings/bulk", json={"effective_timezone": "Asia/Tokyo"})

    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert test_db.query(Setting).filter(Setting.key == "effective_timezone").first() is None
