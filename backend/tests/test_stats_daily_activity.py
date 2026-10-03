"""The dashboard's daily card must count the user's calendar day, not the server's.

The code this replaces read "today" from the process's local day, so a UTC host
flipped the card at 20:00 for an Amsterdam user.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import core.app_timezone as app_timezone
from core.app_timezone import day_bounds_utc, set_app_timezone
from models.poster import Poster


@pytest.fixture(autouse=True)
def restore_app_timezone():
    """set_app_timezone mutates a module-level global that outlives the test, so snapshot it.

    Restores whatever was in force rather than assuming UTC, matching the fixture in
    tests/test_settings_timezone.py.
    """
    original = app_timezone._cached
    yield
    app_timezone._cached = original


def test_daily_activity_window_follows_the_application_timezone(client, test_db):
    """The same poster must fall inside UTC's today and outside a zone 12h behind it.

    Where exactly that is depends on the time of day the test runs: the two windows
    are both 24h and start 12h apart, so the half of UTC's day the other zone cannot
    cover is the morning when UTC is past noon and the evening when it is not. Pinning
    one clock time (noon UTC, say) would only work before 12:00 UTC and would start
    failing the moment CI ran after it, so place the poster in the middle of whichever
    half is UTC's alone. day_bounds_utc is pinned separately in test_app_timezone.py.

    `test_db` rolls back after each test, so the counts are exact rather than deltas.
    """
    other = ZoneInfo("Etc/GMT+12")
    now = datetime.now(timezone.utc)
    utc_start, utc_end = day_bounds_utc(now, ZoneInfo("UTC"))
    other_start, other_end = day_bounds_utc(now, other)
    lo, hi = (utc_start, other_start) if other_start > utc_start else (other_end, utc_end)
    edge = lo + (hi - lo) / 2

    test_db.add(
        Poster(
            drive_id="tz-edge",
            file_name="tz-edge.jpg",
            file_path="/tmp/tz-edge.jpg",
            last_processed=edge,
        )
    )
    test_db.commit()

    set_app_timezone("UTC")
    in_utc = client.get("/api/stats/poster-daily-activity").json()["posters_renamed_today"]

    set_app_timezone("Etc/GMT+12")
    behind_utc = client.get("/api/stats/poster-daily-activity").json()["posters_renamed_today"]

    assert in_utc == 1
    assert behind_utc == 0
