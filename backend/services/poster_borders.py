"""Which holiday or border rule each placed poster's border came from.

The border pass notes it for every poster it covers. Overlays linked to a holiday or a rule
read it to know which posters they apply to. A key is "holiday:<name>" or "rule:<name>"."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_warning
from models.poster_border import PosterBorder

HOLIDAY = "holiday"
RULE = "rule"
_KINDS = (HOLIDAY, RULE)
_CHUNK = 500  # paths per statement, under SQLite's variable limit
MAX_LINKS = 100


def border_key(kind: str, name: Any) -> str:
    return f"{kind}:{str(name or '').strip()}"


def match_key(key: Any) -> str:
    """How two keys are compared: a name's case does not matter."""
    return str(key or "").strip().casefold()


def normalize_border_keys(raw: Any) -> List[str]:
    """A stored list of keys, cleaned: a known kind and a name each, every one once."""
    out: Dict[str, str] = {}
    for value in raw if isinstance(raw, list) else []:
        kind, sep, name = str(value or "").partition(":")
        kind, name = kind.strip().lower(), name.strip()[:200]
        if sep and kind in _KINDS and name:
            out.setdefault(match_key(f"{kind}:{name}"), border_key(kind, name))
    return sorted(out.values(), key=match_key)[:MAX_LINKS]


class BorderNotes:
    """What one border pass applied, written when it ends: only the rows that changed."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._noted: Dict[str, Optional[str]] = {}

    def note(self, dest_file: str, border: Optional[str]) -> None:
        self._noted[os.path.normpath(dest_file)] = border or None

    def save(self, placed: Optional[Iterable[str]] = None) -> None:
        """`placed` is every poster the pass covers: the rows of files no longer among them go."""
        try:
            existing = {path: border for path, border in self.db.query(PosterBorder.file_path, PosterBorder.border)}
            changed = {path: border for path, border in self._noted.items() if border and existing.get(path) != border}
            gone = [path for path, border in self._noted.items() if not border and path in existing]
            if placed is not None:
                covered = {os.path.normpath(path) for path in placed}
                gone += [path for path in existing if path not in covered and path not in self._noted]
            stale = gone + [path for path in changed if path in existing]
            if not stale and not changed:
                return
            for start in range(0, len(stale), _CHUNK):
                self.db.query(PosterBorder).filter(PosterBorder.file_path.in_(stale[start:start + _CHUNK])).delete(synchronize_session=False)
            if changed:
                self.db.bulk_insert_mappings(PosterBorder, [{"file_path": path, "border": border} for path, border in changed.items()])
            self.db.commit()
            log_debug(LogTags.BORDER_REPLACER, f"Holiday and rule borders noted for overlays: {len(changed)} set, {len(gone)} cleared")
        except SQLAlchemyError as exc:
            self.db.rollback()
            log_warning(LogTags.BORDER_REPLACER, f"Could not note which posters got a holiday or rule border: {exc}")


def forget_borders(db: Session, files: Iterable[str]) -> None:
    """These posters were written without the border pass: none carries a holiday's or a rule's border."""
    paths = [os.path.normpath(path) for path in files]
    try:
        removed = 0
        for start in range(0, len(paths), _CHUNK):
            removed += db.query(PosterBorder).filter(PosterBorder.file_path.in_(paths[start:start + _CHUNK])).delete(synchronize_session=False)
        if removed:
            db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        log_warning(LogTags.BORDER_REPLACER, f"Could not clear the holiday and rule border notes: {exc}")


def border_of(db: Session, file_path: Optional[str]) -> str:
    """The key of the holiday or rule a placed poster's border came from, '' for neither."""
    if not file_path:
        return ""
    try:
        row = db.query(PosterBorder.border).filter(PosterBorder.file_path == os.path.normpath(file_path)).first()
    except SQLAlchemyError:
        db.rollback()
        return ""
    return str(row[0]) if row else ""


def border_choices(db: Session) -> List[Dict[str, str]]:
    """The holidays and border rules an overlay can be linked to, as the Border tab holds them."""
    from services.border_replacer import BorderReplacerService
    from services.plex_border_rules import load_plex_rules

    choices: Dict[str, Dict[str, str]] = {}
    for holiday in BorderReplacerService(db)._load_holiday_schedules():
        key = border_key(HOLIDAY, holiday.get("name"))
        if key == border_key(HOLIDAY, ""):
            continue
        schedule = str(holiday.get("schedule") or "")
        dates = schedule[len("range("):-1] if schedule.startswith("range(") and schedule.endswith(")") else schedule
        choices.setdefault(match_key(key), {"key": key, "kind": HOLIDAY, "name": key.partition(":")[2], "detail": dates.replace("-", " to ")})
    for rule in load_plex_rules(db):
        key = border_key(RULE, rule.name)
        choices.setdefault(match_key(key), {"key": key, "kind": RULE, "name": key.partition(":")[2], "detail": f"{rule.match} {rule.value}"})
    return list(choices.values())
