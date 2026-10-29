"""What Asset Upload last put on each media server item, so a later run can tell when the
poster on the server is no longer ours (reset by hand, replaced by a metadata refresh).
One row per server item: `hint` is the listing's cheap change hint (Jellyfin's image tag,
Plex's thumb path) as last confirmed, `sha1` the bytes we sent (Plex names uploads by it)."""

from typing import Any, Dict, Optional

from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from database import Base
from util.utc_datetime import UTCDateTime


class ServerPoster(Base):
    __tablename__ = "server_posters"
    __table_args__ = (UniqueConstraint("instance", "item_id", name="uq_server_posters_item"),)

    id = Column(Integer, primary_key=True)
    instance = Column(String, nullable=False, index=True)
    item_id = Column(String, nullable=False)
    hint = Column(String, nullable=True)       # listing hint last seen with our poster selected; "" = not confirmed yet
    sha1 = Column(String, nullable=True)       # sha1 of the bytes we uploaded; None for a baseline row
    file_path = Column(String, nullable=True)  # the placed file behind the upload
    updated_at = Column(UTCDateTime, server_default=func.now(), onupdate=func.now())

    def to_dict(self) -> Dict[str, Any]:
        return {"hint": self.hint or "", "sha1": self.sha1, "file_path": self.file_path}


def server_poster_key(instance: str, item_id: str) -> str:
    return f"{instance}|{item_id}"


def load_server_posters(db: Session) -> Dict[str, Dict[str, Any]]:
    """{instance|item id: {hint, sha1, file_path}} for every row, in one query."""
    return {server_poster_key(row.instance, row.item_id): row.to_dict() for row in db.query(ServerPoster).all()}


def upsert_server_poster(db: Session, instance: str, item_id: str, *, hint: str, sha1: Optional[str], file_path: Optional[str], commit: bool = True) -> Dict[str, Any]:
    """Write one row; commit=False leaves it in the session for the caller to commit in a batch."""
    row = db.query(ServerPoster).filter(ServerPoster.instance == instance, ServerPoster.item_id == item_id).first()
    if row is None:
        row = ServerPoster(instance=instance, item_id=item_id)
        db.add(row)
    row.hint, row.sha1, row.file_path = hint or "", sha1, file_path
    if commit:
        db.commit()
    else:
        db.flush()
    return row.to_dict()


def clear_server_posters(db: Session) -> int:
    removed = db.query(ServerPoster).delete()
    db.commit()
    return removed


