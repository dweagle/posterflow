import json
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from database import Base


class PlexUploadRecord(Base):
    """
    Tracks which local poster files have been successfully uploaded to Plex.

    Replaces the old JSON blob stored under the 'plex_upload_file_cache' settings key.
    Stored in the database so records survive container rebuilds and config volume
    changes without being silently lost.

    file_hash is the sha256 hex digest of the file at upload time. If a local file
    is re-synced from a drive (new poster), its hash will differ and the record
    is treated as stale — triggering a fresh upload. Records migrated from the old
    JSON cache have file_hash=None and are treated as valid legacy entries (no
    hash comparison is possible, so they are accepted as-is to avoid spurious
    re-uploads on first migration).
    """

    __tablename__ = "plex_upload_records"

    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String, unique=True, nullable=False, index=True)
    file_hash = Column(String, nullable=True)  # sha256 hex; None for migrated legacy entries
    uploaded_to_libraries = Column(String, nullable=True)       # JSON list of library names
    uploaded_to_library_keys = Column(String, nullable=True)    # JSON list of stable library keys
    uploaded_editions = Column(String, nullable=True)           # JSON list of edition titles
    uploaded_media_types = Column(String, nullable=True)        # JSON list of media types
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<PlexUploadRecord(file_path='{self.file_path}')>"

    def to_dict(self) -> Dict[str, Any]:
        """Return a dict matching the legacy JSON cache entry format."""

        def _parse(val: Optional[str]) -> List[str]:
            if not val:
                return []
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        d: Dict[str, Any] = {
            "uploaded_to_libraries": _parse(self.uploaded_to_libraries),
            "uploaded_to_library_keys": _parse(self.uploaded_to_library_keys),
            "uploaded_editions": _parse(self.uploaded_editions),
            "uploaded_media_types": _parse(self.uploaded_media_types),
        }
        if self.file_hash is not None:
            d["file_hash"] = self.file_hash
        return d
