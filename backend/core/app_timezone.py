"""The application timezone: what schedule times and calendar days mean to the user.

Process TZ stays UTC so storage and logs are timezone-independent. This module owns
the other half — the zone a user types into a schedule and the zone that decides
where "today" starts. It resolves lazily on first use (env var or host lookup), not at
import, so the database-backed setting does not have to be reachable that early; the
lifespan calls set_app_timezone() with the persisted value before the scheduler starts.

Keep this module free of scheduler imports — core.scheduler imports this, and a
reverse import would be circular. Callers that need the scheduler re-pointed call
core.scheduler.apply_app_timezone().
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import settings
from core.logging import LogTags, log_warning

_cached: Optional[ZoneInfo] = None


def _host_timezone() -> ZoneInfo:
    """Fall back to the host's zone, then UTC. Mirrors the scheduler's tzlocal lookup."""
    from tzlocal import get_localzone_name

    # Every caller routes through here, so this is the one place that has to survive a
    # host zone we cannot build a ZoneInfo from. tzlocal raises ZoneInfoNotFoundError,
    # which subclasses KeyError, and get_localzone_name() can also return a name the
    # system has no tzdata for.
    try:
        name = get_localzone_name()
        if name:
            return ZoneInfo(name)
        reason = "the host has no zone name"
    except (ZoneInfoNotFoundError, KeyError, ValueError) as e:
        reason = str(e)
    log_warning(
        LogTags.SCHEDULER,
        f"No application timezone configured and {reason}; using UTC. "
        "Set the timezone in Settings → Scheduling.",
    )
    return ZoneInfo("UTC")


def _resolve() -> ZoneInfo:
    name = (settings.app_timezone or "").strip()
    return ZoneInfo(name) if name else _host_timezone()


def get_app_timezone() -> ZoneInfo:
    """The resolved application timezone. Cached after the first call."""
    global _cached
    if _cached is None:
        try:
            _cached = _resolve()
        except (ZoneInfoNotFoundError, ValueError) as e:
            log_warning(LogTags.SCHEDULER, f"Invalid application timezone {settings.app_timezone!r} ({e}); using UTC")
            _cached = ZoneInfo("UTC")
    return _cached


def set_app_timezone(name: str) -> bool:
    """Point the application at a new zone. Returns False and keeps the old one if invalid."""
    global _cached
    name = (name or "").strip()
    if not name:
        _cached = _host_timezone()
        return True
    try:
        _cached = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log_warning(LogTags.SCHEDULER, f"Ignoring invalid application timezone {name!r}")
        return False
    return True


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_in_app_tz() -> datetime:
    return datetime.now(get_app_timezone())


def today_in_app_tz() -> date:
    return now_in_app_tz().date()


def day_bounds_utc(now: datetime, tz) -> tuple[datetime, datetime]:
    """[start of `now`'s calendar day in `tz`, start of the next day), as aware UTC.

    The window is not always 24h — across a DST transition the local day is 23 or 25
    hours. Deriving both ends from the same local date handles that for free.
    """
    local_day = now.astimezone(tz).date()
    start = datetime.combine(local_day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end
