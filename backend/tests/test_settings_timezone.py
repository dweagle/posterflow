"""The settings API is where the application timezone gets set, so it is also where the
scheduler has to be re-pointed: a new zone re-interprets every cron expression.

Both globals mutated here (core.app_timezone._cached, core.scheduler.scheduler.timezone)
are process-wide and outlive the test, so every test restores them.
"""
import pytest

import core.app_timezone as app_timezone
import core.scheduler as scheduler_module
from models.setting import Setting, upsert_setting


@pytest.fixture(autouse=True)
def restore_app_timezone_globals():
    original_cached = app_timezone._cached
    original_scheduler_tz = scheduler_module.scheduler.timezone
    yield
    app_timezone._cached = original_cached
    scheduler_module.scheduler.timezone = original_scheduler_tz


def test_get_settings_exposes_effective_timezone(client, test_db):
    """The GET must show the zone actually in force, not just the stored string —
    otherwise a bad stored value is invisible."""
    assert app_timezone.set_app_timezone("Europe/Amsterdam")
    upsert_setting(test_db, "timezone", "Europe/Amsterdam")
    test_db.commit()

    response = client.get("/api/settings/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["effective_timezone"] == "Europe/Amsterdam"
    assert payload["timezone"] == "Europe/Amsterdam"


def test_posting_a_valid_timezone_persists_it_and_repoints_the_scheduler(client, test_db):
    """Persisting is half the job; if the scheduler keeps the old zone every schedule
    silently fires at the wrong hour."""
    response = client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"})

    assert response.status_code == 200
    saved = test_db.query(Setting).filter(Setting.key == "timezone").first()
    assert saved is not None
    assert saved.value == "Asia/Tokyo"

    assert str(app_timezone.get_app_timezone()) == "Asia/Tokyo"
    assert str(scheduler_module.scheduler.timezone) == "Asia/Tokyo"


def test_posting_an_invalid_timezone_leaves_the_previous_zone_in_force(client, test_db):
    """The stored value can be garbage; the app must not be. The user finds out through
    effective_timezone, not through a 500.

    The starting zone is seeded through the API rather than by calling set_app_timezone()
    directly: a directly-set zone would satisfy every assertion below even if the POST
    path stopped calling set_app_timezone() altogether, so the test would pass vacuously.
    """
    seeded = client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"})
    assert seeded.status_code == 200
    assert client.get("/api/settings/").json()["effective_timezone"] == "Asia/Tokyo"

    response = client.post("/api/settings/bulk", json={"timezone": "Not/AZone"})

    assert response.status_code == 200
    assert str(app_timezone.get_app_timezone()) == "Asia/Tokyo"

    payload = client.get("/api/settings/").json()
    assert payload["effective_timezone"] == "Asia/Tokyo"
    # The garbage is still stored -- that is what makes it visible and fixable.
    assert payload["timezone"] == "Not/AZone"


def test_update_schedules_runs_only_when_the_post_carries_a_timezone(client, test_db, monkeypatch):
    """Re-pointing scheduler.timezone is not enough on its own -- jobs already added keep
    the cron they were built with, so a zone change has to rebuild them. And a bulk save
    that does not touch the timezone must not pay for a rebuild."""
    rebuilds = []
    monkeypatch.setattr(scheduler_module, "update_schedules", lambda: rebuilds.append(1))

    assert client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"}).status_code == 200
    assert len(rebuilds) == 1

    assert client.post("/api/settings/bulk", json={"poster_destination": "remote:posters"}).status_code == 200
    assert len(rebuilds) == 1


def test_effective_timezone_cannot_be_written(client, test_db):
    """Read-only: it is a computed value, and accepting it would let a client pin the
    resolved zone while the stored setting says something else."""
    assert app_timezone.set_app_timezone("Europe/Amsterdam")
    assert "effective_timezone" in client.get("/api/settings/").json()

    response = client.post("/api/settings/bulk", json={"effective_timezone": "Asia/Tokyo"})

    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert test_db.query(Setting).filter(Setting.key == "effective_timezone").first() is None
    # The computed value is unaffected by the rejected write.
    assert client.get("/api/settings/").json()["effective_timezone"] == "Europe/Amsterdam"
