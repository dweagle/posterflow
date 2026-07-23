from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Float, Index
from sqlalchemy.sql import func
from database import Base


class Artwork(Base):
    """
    Represents one downloaded artwork file (a logo, backdrop/background, or square art).
    Parallel to Poster, but each row also carries its artwork_type. Populated by the
    artwork sync's indexing pass from the drive's logos/backgrounds/squareart subfolders.
    """
    __tablename__ = "artwork"

    id = Column(Integer, primary_key=True, index=True)
    artwork_drive_id = Column(String, nullable=False)  # ArtworkDrive.drive_id it came from
    artwork_type = Column(String, nullable=False)  # "logo" | "background" | "squareart"
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False, unique=True)  # Full path on disk
    title = Column(String, nullable=True)  # Best-effort parsed title (year/ids/subtype stripped)
    year = Column(Integer, nullable=True)
    tmdb_id = Column(Integer, nullable=True, index=True)  # From {tmdb-<id>} tag
    tvdb_id = Column(Integer, nullable=True)  # From {tvdb-<id>} tag (TV)
    imdb_id = Column(String, nullable=True)  # From {imdb-tt...} tag
    file_size = Column(BigInteger, nullable=True)
    file_mtime = Column(Float, nullable=True)  # Filesystem mtime for change detection
    downloaded_at = Column(DateTime(timezone=True), server_default=func.now())
    last_processed = Column(DateTime(timezone=True), nullable=True)  # When last uploaded/processed (future)

    __table_args__ = (
        # Fast per-drive, per-type counts (drive_id + artwork_type).
        Index("ix_artwork_drive_type", "artwork_drive_id", "artwork_type"),
    )

    def __repr__(self) -> str:
        return f"<Artwork(type='{self.artwork_type}', file_name='{self.file_name}')>"
