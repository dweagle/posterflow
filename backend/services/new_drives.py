import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Literal

from sqlalchemy.orm import Session

from models.setting import get_setting_value, upsert_setting

Domain = Literal["poster", "artwork"]

SETTING_KEYS: Dict[str, str] = {"poster": "new_poster_drives", "artwork": "new_artwork_drives"}

# How long a drive keeps its "New" tag after joining the community list
NEW_DRIVE_TTL_DAYS = 14

NewDriveEntries = Dict[str, Dict[str, Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_added_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_new_drives(db: Session, domain: Domain) -> NewDriveEntries:
    """drive_id -> {"added_at": iso, "seen": bool}, with entries past the TTL dropped."""
    raw = get_setting_value(db, SETTING_KEYS[domain])
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    cutoff = _now() - timedelta(days=NEW_DRIVE_TTL_DAYS)
    entries: NewDriveEntries = {}
    for drive_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        added_at = _parse_added_at(entry.get("added_at"))
        if added_at is None or added_at < cutoff:
            continue
        entries[str(drive_id)] = {"added_at": added_at.isoformat(), "seen": bool(entry.get("seen", False))}
    return entries


def _save(db: Session, domain: Domain, entries: NewDriveEntries) -> None:
    upsert_setting(db, SETTING_KEYS[domain], json.dumps(entries))


def record_new_drives(db: Session, domain: Domain, drive_ids: Iterable[str]) -> None:
    """Flag drives that just joined the community list. Caller commits."""
    ids = [d for d in drive_ids if d]
    if not ids:
        return
    entries = load_new_drives(db, domain)
    now = _now().isoformat()
    for drive_id in ids:
        entries[drive_id] = {"added_at": now, "seen": False}
    _save(db, domain, entries)


def forget_new_drives(db: Session, domain: Domain, drive_ids: Iterable[str]) -> None:
    """Drop the flag for drives the user subscribed to or that left the list. Caller commits."""
    entries = load_new_drives(db, domain)
    remaining = {k: v for k, v in entries.items() if k not in set(drive_ids)}
    if remaining != entries:
        _save(db, domain, remaining)


def mark_new_drives_seen(db: Session, domain: Domain) -> None:
    """The user has looked at the drives page; keep the tags but clear the sidebar badge. Caller commits."""
    entries = load_new_drives(db, domain)
    if any(not e["seen"] for e in entries.values()):
        for entry in entries.values():
            entry["seen"] = True
        _save(db, domain, entries)


def clear_new_drives(db: Session, domain: Domain) -> None:
    """Explicit dismiss: forget every flagged drive. Caller commits."""
    if load_new_drives(db, domain):
        _save(db, domain, {})
