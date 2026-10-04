import pickle
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import core.app_timezone as app_timezone
from core.app_timezone import day_bounds_utc
from models.setting import upsert_setting


@pytest.fixture
def app_tz():
    original = app_timezone._cached
    app_timezone._cached = None
    yield app_timezone
    app_timezone._cached = original


@pytest.fixture
def warnings(monkeypatch):
    captured = []
    monkeypatch.setattr(app_timezone, "log_warning", lambda tag, msg, **k: captured.append(msg))
    return captured


def test_host_zone_is_used_when_nothing_is_saved(app_tz, monkeypatch):
    monkeypatch.setattr(app_tz, "get_localzone_name", lambda: "Europe/Amsterdam")

    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_saved_zone_wins_over_the_host(app_tz, monkeypatch):
    monkeypatch.setattr(app_tz, "get_localzone_name", lambda: "Europe/Amsterdam")

    assert app_tz.set_app_timezone(" Asia/Tokyo ") is True
    assert str(app_tz.get_app_timezone()) == "Asia/Tokyo"


def test_unknown_zone_keeps_the_previous_one(app_tz):
    assert app_tz.set_app_timezone("Asia/Tokyo") is True

    assert app_tz.set_app_timezone("Not/AZone") is False
    assert str(app_tz.get_app_timezone()) == "Asia/Tokyo"


def test_blank_zone_goes_back_to_the_host(app_tz, monkeypatch):
    monkeypatch.setattr(app_tz, "get_localzone_name", lambda: "Europe/Amsterdam")
    assert app_tz.set_app_timezone("Asia/Tokyo") is True

    assert app_tz.set_app_timezone("") is True
    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_saved_zone_is_restored_at_startup(app_tz, test_db, monkeypatch):
    monkeypatch.setattr(app_tz, "get_localzone_name", lambda: "Europe/Amsterdam")
    upsert_setting(test_db, "timezone", "Asia/Tokyo")
    test_db.commit()

    app_tz.restore_saved_timezone(test_db)

    assert str(app_tz.get_app_timezone()) == "Asia/Tokyo"


def test_unavailable_saved_zone_falls_back_to_the_host(app_tz, test_db, monkeypatch, warnings):
    monkeypatch.setattr(app_tz, "get_localzone_name", lambda: "Europe/Amsterdam")
    upsert_setting(test_db, "timezone", "Gone/Zone")
    test_db.commit()

    app_tz.restore_saved_timezone(test_db)

    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"
    assert len(warnings) == 1
    assert "Gone/Zone" in warnings[0]


def test_host_timezone_resolves_by_name(app_tz, monkeypatch, warnings):
    """A named zone pickles by key, which the SQLAlchemy job store needs for every add_job."""
    monkeypatch.setattr(app_tz, "get_localzone_name", lambda: "Europe/Amsterdam")

    tz = app_tz.host_timezone()

    assert str(tz) == "Europe/Amsterdam"
    assert pickle.loads(pickle.dumps(tz)) == tz
    assert warnings == []


def test_host_timezone_warns_and_uses_utc_when_host_has_no_zone_name(app_tz, monkeypatch, warnings):
    """No TZ, no /var/db/zoneinfo and a copied /etc/localtime (FreeBSD jail) yields no name; fall back loudly, not silently."""
    monkeypatch.setattr(app_tz, "get_localzone_name", lambda: None)

    tz = app_tz.host_timezone()

    assert tz is timezone.utc
    assert pickle.loads(pickle.dumps(tz)) is timezone.utc
    assert len(warnings) == 1
    assert "TZ" in warnings[0]


def test_host_timezone_survives_an_unusable_host_zone(app_tz, monkeypatch, warnings):
    def unusable():
        raise KeyError("tzlocal() does not support non-zoneinfo timezones")

    monkeypatch.setattr(app_tz, "get_localzone_name", unusable)

    assert app_tz.host_timezone() is timezone.utc
    assert len(warnings) == 1


def test_day_bounds_follow_the_local_calendar_day():
    """23:30 UTC on the 30th is already Oct 1 in Amsterdam."""
    now = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)

    start, end = day_bounds_utc(now, ZoneInfo("Europe/Amsterdam"))

    assert start == datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def test_day_bounds_in_utc():
    now = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)

    start, end = day_bounds_utc(now, timezone.utc)

    assert start == datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_day_is_23_hours_across_spring_forward():
    start, end = day_bounds_utc(datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc), ZoneInfo("Europe/Amsterdam"))

    assert end - start == timedelta(hours=23)
