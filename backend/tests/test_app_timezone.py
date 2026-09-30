from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def app_tz():
    """The module with its cache reset, restored afterwards so other tests are unaffected.

    Deliberately not importlib.reload(): reloading creates a second module object with
    different globals, while core.scheduler (Task 5) holds a reference to the first one.
    """
    import core.app_timezone as module

    original = module._cached
    module._cached = None
    yield module
    module._cached = original


@contextmanager
def capture_warnings():
    """Yield a list collecting WARNING records. Logging is loguru, which does not propagate
    to stdlib logging, so caplog sees nothing; attach a sink directly, as tests/test_match.py does."""
    from loguru import logger

    messages: list = []
    sink_id = logger.add(messages.append, level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def test_env_var_is_used_when_no_db_setting(app_tz, monkeypatch):
    # Host patched to a third zone so the assertion tests precedence, not the dev machine's zone.
    monkeypatch.setattr(app_tz.settings, "app_timezone", "Europe/Amsterdam", raising=False)
    monkeypatch.setattr(app_tz, "_host_timezone", lambda: ZoneInfo("America/New_York"))

    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_set_app_timezone_wins_and_is_cached(app_tz, monkeypatch):
    monkeypatch.setattr(app_tz.settings, "app_timezone", "Asia/Tokyo", raising=False)

    assert app_tz.set_app_timezone("Europe/Amsterdam") is True
    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"

    # Cached: a later change to the env source must not leak through.
    monkeypatch.setattr(app_tz.settings, "app_timezone", "Asia/Tokyo")
    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_resolve_prefers_env_over_host(app_tz, monkeypatch):
    """With nothing configured, the host's zone is the fallback — never silently UTC."""
    monkeypatch.setattr(app_tz.settings, "app_timezone", "", raising=False)
    monkeypatch.setattr(app_tz, "_host_timezone", lambda: ZoneInfo("Europe/Amsterdam"))

    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_invalid_env_zone_warns_and_falls_back_to_utc(app_tz, monkeypatch):
    """An unusable APP_TIMEZONE must degrade to UTC with a warning, not raise."""
    monkeypatch.setattr(app_tz.settings, "app_timezone", "Not/AZone", raising=False)

    with capture_warnings() as warnings:
        assert str(app_tz.get_app_timezone()) == "UTC"

    assert any("Not/AZone" in message for message in warnings)


def test_invalid_zone_warns_and_keeps_previous(app_tz):
    assert app_tz.set_app_timezone("Europe/Amsterdam") is True

    with capture_warnings() as warnings:
        assert app_tz.set_app_timezone("Not/AZone") is False

    assert any("Not/AZone" in message for message in warnings)
    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_empty_value_falls_back_to_host_lookup(app_tz, monkeypatch):
    # Host zone is neither UTC nor this machine's real zone, so the delegation is observable.
    monkeypatch.setattr(app_tz, "_host_timezone", lambda: ZoneInfo("Asia/Tokyo"))

    assert app_tz.set_app_timezone("") is True
    assert str(app_tz.get_app_timezone()) == "Asia/Tokyo"


def test_day_bounds_utc_spans_the_local_calendar_day():
    """23:30 UTC on the 30th is already the 1st in Amsterdam; the window must follow."""
    from core.app_timezone import day_bounds_utc

    now = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)

    start, end = day_bounds_utc(now, ZoneInfo("Europe/Amsterdam"))

    assert start == datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def test_day_bounds_utc_is_utc_when_asked():
    from core.app_timezone import day_bounds_utc

    now = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)

    start, end = day_bounds_utc(now, timezone.utc)

    assert start == datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def test_day_window_is_23h_across_a_dst_transition():
    """A DST transition makes the local day 23 or 25 hours long — the bounds must reflect that."""
    from core.app_timezone import day_bounds_utc

    # Europe/Amsterdam springs forward on 2026-03-29.
    start, end = day_bounds_utc(datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc), ZoneInfo("Europe/Amsterdam"))

    assert end - start == timedelta(hours=23)
