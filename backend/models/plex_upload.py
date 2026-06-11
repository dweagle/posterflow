import json
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.sql import func

from database import Base


class PlexUploadRecord(Base):
    """Per-file record of posters uploaded to Plex, kept in the DB so it survives rebuilds (see column comments)."""

    __tablename__ = "plex_upload_records"

    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String, unique=True, nullable=False, index=True)
    file_hash = Column(String, nullable=True)    # sha256 hex; None for migrated legacy entries
    file_mtime = Column(Float, nullable=True)    # st_mtime at upload time; used as fast pre-check
    uploaded_to_libraries = Column(String, nullable=True)       # JSON list of library names
    uploaded_to_library_keys = Column(String, nullable=True)    # JSON list of stable library keys
    uploaded_to_rating_keys = Column(String, nullable=True)     # JSON list of Plex ratingKeys; an unseen key = item re-added → re-upload
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
            "uploaded_to_rating_keys": _parse(self.uploaded_to_rating_keys),
            "uploaded_editions": _parse(self.uploaded_editions),
            "uploaded_media_types": _parse(self.uploaded_media_types),
        }
        if self.file_hash is not None:
            d["file_hash"] = self.file_hash
        return d
