from typing import Any, Dict

from sqlalchemy import Column, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from database import Base
from util.utc_datetime import UTCDateTime


class OverlayItem(Base):
    """A media-server item that carries an overlay badge: which badge it got, the Posterflow
    poster file it was composed from, and what was uploaded."""

    __tablename__ = "overlay_items"
    __table_args__ = (UniqueConstraint("instance", "item_id", name="uq_overlay_items_instance_item"),)

    FIELDS = ("item_type", "library", "title", "sig", "source_path", "source_sha1", "composite_sha1", "server_key")

    id = Column(Integer, primary_key=True, index=True)
    instance = Column(String, nullable=False, index=True)  # media server name
    item_id = Column(String, nullable=False)  # server item id (Plex ratingKey / Jellyfin Id)
    item_type = Column(String, nullable=True)  # movie / show / season
    library = Column(String, nullable=True)  # library key the item was seen in
    title = Column(String, nullable=True)
    sig = Column(String, nullable=True)  # badge signature: what was drawn
    source_path = Column(String, nullable=True)  # the clean poster file in the destination folder
    source_sha1 = Column(String, nullable=True)  # SHA1 of that file when the badge was composed
    composite_sha1 = Column(String, nullable=True)  # SHA1 of the composite uploaded (Plex names the upload by it)
    server_key = Column(String, nullable=True)  # the server's poster hint when we last confirmed it showed ours
    updated_at = Column(UTCDateTime, server_default=func.now(), onupdate=func.now())

    @property
    def key(self) -> str:
        return f"{self.instance}|{self.item_id}"

    def to_entry(self) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"instance": self.instance}
        for field in self.FIELDS:
            value = getattr(self, field)
            if value is not None:
                entry[field] = value
        return entry

    def apply(self, entry: Dict[str, Any]) -> None:
        for field in self.FIELDS:
            value = entry.get(field)
            setattr(self, field, None if value is None else str(value))


class OverlayMetadataCache(Base):
    """TMDB records the overlays resolved, with the time they were fetched (the provider
    decides how long each kind of record stays fresh)."""

    __tablename__ = "overlay_metadata_cache"

    key = Column(String, primary_key=True)  # "movie:<id>", "tv:<id>", a /find or /search key
    value = Column(Text, nullable=False)  # the record as JSON, fetched_at included
    fetched_at = Column(Float, nullable=True, index=True)
