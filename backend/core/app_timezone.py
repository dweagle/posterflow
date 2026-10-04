from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy.orm import Session
from tzlocal import get_localzone_name

from core.logging import LogTags, log_warning
from models.setting import get_setting_value

_cached: Optional[tzinfo] = None
_REGIONS = {"Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific"}


def parse_timezone(name: str) -> Optional[ZoneInfo]:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        return None


def timezone_names() -> list[str]:
    # from this machine's tzdata, since browser zone lists carry legacy names it may not have
    names = {name for name in available_timezones() if name.split("/", 1)[0] in _REGIONS}
    return ["UTC", *sorted(names)]


def host_timezone() -> tzinfo:
    # by name so APScheduler can pickle jobs; get_localzone() returns an unpicklable file-stream zone when TZ points at a file or the host has no zone name
    try:
        name = get_localzone_name()
    except (KeyError, ValueError, OSError):
        name = None
    zone = parse_timezone(name) if name else None
    if zone:
        return zone
    log_warning(
        LogTags.SCHEDULER,
        "Local timezone has no zone name, so scheduled times will run in UTC. "
        "Pick a timezone in Settings > Scheduling, or set TZ to an IANA name such as Europe/Amsterdam (FreeBSD: run tzsetup) and restart.",
    )
    return timezone.utc


def get_app_timezone() -> tzinfo:
    global _cached
    if _cached is None:
        _cached = host_timezone()
    return _cached


def set_app_timezone(name: str) -> bool:
    # blank means follow the host; an unknown name keeps the zone already in force
    global _cached
    name = (name or "").strip()
    zone = parse_timezone(name) if name else host_timezone()
    if zone is None:
        return False
    _cached = zone
    return True


def restore_saved_timezone(db: Session) -> None:
    saved = (get_setting_value(db, "timezone") or "").strip()
    if saved and not set_app_timezone(saved):
        log_warning(LogTags.STARTUP, f"Saved timezone '{saved}' is not available here, using the host timezone")


def now_in_app_tz() -> datetime:
    return datetime.now(get_app_timezone())


def today_in_app_tz() -> date:
    return now_in_app_tz().date()


def day_bounds_utc(now: datetime, tz: tzinfo) -> tuple[datetime, datetime]:
    # both ends come from the same local date, so a DST day is 23 or 25 hours
    day = now.astimezone(tz).date()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
