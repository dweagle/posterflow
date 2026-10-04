from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import core.app_timezone as app_timezone
from core.app_timezone import day_bounds_utc, set_app_timezone
from models.poster import Poster


@pytest.fixture(autouse=True)
def restore_app_timezone():
    cached = app_timezone._cached
    yield
    app_timezone._cached = cached


def test_daily_activity_counts_the_app_timezone_day(client, test_db):
    """The same poster is inside UTC's today and outside the today of a zone 12 hours behind."""
    now = datetime.now(timezone.utc)
    utc_start, utc_end = day_bounds_utc(now, timezone.utc)
    other_start, other_end = day_bounds_utc(now, ZoneInfo("Etc/GMT+12"))
    # pick the half of UTC's day the other zone's day does not cover, whatever time this runs
    lo, hi = (utc_start, other_start) if other_start > utc_start else (other_end, utc_end)
    test_db.add(Poster(drive_id="tz-edge", file_name="tz-edge.jpg", file_path="/tmp/tz-edge.jpg",
                       last_processed=lo + (hi - lo) / 2))
    test_db.commit()

    set_app_timezone("UTC")
    in_utc = client.get("/api/stats/poster-daily-activity").json()["posters_renamed_today"]
    set_app_timezone("Etc/GMT+12")
    behind_utc = client.get("/api/stats/poster-daily-activity").json()["posters_renamed_today"]

    assert in_utc == 1
    assert behind_utc == 0
