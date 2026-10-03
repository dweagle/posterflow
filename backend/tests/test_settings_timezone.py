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


def test_a_failed_rebuild_does_not_500_a_save_that_took(client, test_db, monkeypatch):
    """The zone is committed and the scheduler re-pointed before the rebuild, so a rebuild
    failure is not a save failure.

    A stopped scheduler is the realistic trigger: startup can fail and main.py keeps
    serving, and add_job() on a stopped BackgroundScheduler queues the job as pending
    without ever setting next_run_time. Reporting 500 there tells the user their change
    was lost when it was in fact persisted and is already in force.
    """
    def boom():
        raise AttributeError("'Job' object has no attribute 'next_run_time'")

    monkeypatch.setattr(scheduler_module, "update_schedules", boom)

    response = client.post("/api/settings/bulk", json={"timezone": "Asia/Tokyo"})

    assert response.status_code == 200
    saved = test_db.query(Setting).filter(Setting.key == "timezone").first()
    assert saved is not None and saved.value == "Asia/Tokyo"
    assert str(app_timezone.get_app_timezone()) == "Asia/Tokyo"


def test_update_schedules_repoints_the_scheduler_by_itself(client, test_db):
    """The rebuild reads get_app_timezone() to build its cron triggers, so the re-point has
    to be part of it. Leaving it to the caller means any future caller can forget, and the
    zone change then silently does nothing to the jobs."""
    # Drop the scheduler's zone without telling anyone, then run a real rebuild.
    scheduler_module.scheduler.timezone = None
    assert app_timezone.set_app_timezone("America/New_York")

    scheduler_module.update_schedules()

    assert str(scheduler_module.scheduler.timezone) == "America/New_York"


def test_setting_an_empty_timezone_falls_back_to_the_host(client, test_db):
    """Clearing the stored value must resolve to the host zone, not blow up.

    _host_timezone() is the single place every fallback routes through, so an unloadable
    host zone name has to degrade there rather than propagate a ZoneInfoNotFoundError out
    of a settings write that has already committed.
    """
    import tzlocal

    original = tzlocal.get_localzone_name
    try:
        tzlocal.get_localzone_name = lambda: "Not/ARealZone"

        response = client.post("/api/settings/bulk", json={"timezone": ""})

        assert response.status_code == 200
        assert str(app_timezone.get_app_timezone()) == "UTC"
    finally:
        tzlocal.get_localzone_name = original
