from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Float, Index
from sqlalchemy.sql import func
from database import Base

class Poster(Base):
    """
    Represents a downloaded poster file.
    Tracks file location and metadata.
    """
    __tablename__ = "posters"

    id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(String, nullable=False)  # Which drive it came from (indexed via ix_posters_drive_last_processed)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False, unique=True)  # Full path on disk
    file_size = Column(BigInteger, nullable=True)  # Size in bytes
    gdrive_file_id = Column(String, nullable=True)  # Google Drive file ID
    downloaded_at = Column(DateTime(timezone=True), server_default=func.now())
    file_mtime = Column(Float, nullable=True)  # Filesystem modification time for change detection
    dest_file_mtime = Column(Float, nullable=True)  # Destination file mtime after last write (for external-change detection)
    last_processed = Column(DateTime(timezone=True), nullable=True)  # When last renamed/organized
    border_rule_sig = Column(String, nullable=True)  # Fingerprint of the Plex border rule applied last run (incremental per-item reprocessing)

    __table_args__ = (
        # Makes the per-drive "unprocessed" count (drive_id + last_processed IS NULL) index-only.
        Index("ix_posters_drive_last_processed", "drive_id", "last_processed"),
    )

    def __repr__(self) -> str:
        return f"<Poster(file_name='{self.file_name}')>"