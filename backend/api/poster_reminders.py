"""Poster reminders — items flagged on a maker / artwork card to come back to later, with a note.
Backs the bell checkbox on the cards and the Reminders tab in Maker Tools.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.logging import log_user_action
from database import get_db
from models.poster_reminder import PosterReminder

router = APIRouter(prefix="/api/maker-tools/reminders", tags=["poster-reminders"])

KINDS = ("poster", "artwork")
MEDIA_TYPES = ("movie", "tv", "collection")
NOTE_MAX_CHARS = 2000


class ReminderInput(BaseModel):
    kind: str = "poster"
    media_type: str
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    title: str
    year: Optional[str] = None
    poster_url: Optional[str] = None
    homepage: Optional[str] = None
    note: str = ""


class ReminderNoteInput(BaseModel):
    note: str = ""


def _clean_note(note: Optional[str]) -> str:
    text = (note or "").strip()
    if len(text) > NOTE_MAX_CHARS:
        raise HTTPException(status_code=400, detail=f"Note is too long (max {NOTE_MAX_CHARS} characters)")
    return text


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    # SQLite's CURRENT_TIMESTAMP is UTC but comes back naive; stamp it so the browser converts.
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _reminder_dict(r: PosterReminder) -> Dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind,
        "media_type": r.media_type,
        "tmdb_id": r.tmdb_id,
        "tvdb_id": r.tvdb_id,
        "imdb_id": r.imdb_id,
        "title": r.title,
        "year": r.year or "",
        "poster_url": r.poster_url,
        "homepage": r.homepage,
        "note": r.note or "",
        "created_at": _iso_utc(r.created_at),
        "updated_at": _iso_utc(r.updated_at),
    }


def _same_item(r: PosterReminder, payload: ReminderInput) -> bool:
    if payload.tmdb_id and r.tmdb_id:
        return r.tmdb_id == payload.tmdb_id
    if payload.tmdb_id or r.tmdb_id:
        return False
    return (r.title or "").strip().lower() == payload.title.strip().lower() and (r.year or "") == (payload.year or "")


@router.get("")
def list_reminders(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = db.query(PosterReminder).order_by(PosterReminder.created_at.desc(), PosterReminder.id.desc()).all()
    return [_reminder_dict(r) for r in rows]


@router.post("")
def upsert_reminder(payload: ReminderInput, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Create a reminder, or update the note on the existing one for the same item + kind."""
    if payload.kind not in KINDS:
        raise HTTPException(status_code=400, detail="kind must be 'poster' or 'artwork'")
    if payload.media_type not in MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="media_type must be movie, tv or collection")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    note = _clean_note(payload.note)
    tmdb_id = payload.tmdb_id if payload.tmdb_id and payload.tmdb_id > 0 else None
    payload.tmdb_id = tmdb_id

    existing = next(
        (r for r in db.query(PosterReminder)
         .filter(PosterReminder.kind == payload.kind, PosterReminder.media_type == payload.media_type).all()
         if _same_item(r, payload)),
        None,
    )
    if existing:
        existing.note = note
        # Freshen the identity fields too: a later flag may carry ids/poster the first one lacked.
        existing.tvdb_id = payload.tvdb_id or existing.tvdb_id
        existing.imdb_id = payload.imdb_id or existing.imdb_id
        existing.poster_url = payload.poster_url or existing.poster_url
        existing.homepage = payload.homepage or existing.homepage
        reminder = existing
        action = "Updated reminder"
    else:
        reminder = PosterReminder(
            kind=payload.kind,
            media_type=payload.media_type,
            tmdb_id=tmdb_id,
            tvdb_id=payload.tvdb_id,
            imdb_id=payload.imdb_id,
            title=title,
            year=(payload.year or "").strip() or None,
            poster_url=payload.poster_url or None,
            homepage=payload.homepage or None,
            note=note,
        )
        db.add(reminder)
        action = "Added reminder"
    db.commit()
    db.refresh(reminder)
    log_user_action(f"{action} ({payload.kind}): {title}" + (f" ({reminder.year})" if reminder.year else ""))
    return _reminder_dict(reminder)


@router.put("/{reminder_id}")
def update_reminder_note(reminder_id: int, payload: ReminderNoteInput, db: Session = Depends(get_db)) -> Dict[str, Any]:
    reminder = db.query(PosterReminder).filter(PosterReminder.id == reminder_id).first()
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    reminder.note = _clean_note(payload.note)
    db.commit()
    db.refresh(reminder)
    log_user_action(f"Updated reminder ({reminder.kind}): {reminder.title}")
    return _reminder_dict(reminder)


@router.delete("/{reminder_id}")
def delete_reminder(reminder_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    reminder = db.query(PosterReminder).filter(PosterReminder.id == reminder_id).first()
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    db.delete(reminder)
    db.commit()
    log_user_action(f"Removed reminder ({reminder.kind}): {reminder.title}")
    return {"success": True}
