from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from pathlib import Path
from database import Base
from core.logging import log_warning

class ArtworkDrive(Base):
    """
    Represents a Google Drive containing artwork (logos, backgrounds, square art).
    Separate from poster drives: artwork has its own subfolders and is not part of the
    poster style scheme (no MM2K/CL2K). Artwork drives DO have a priority so users can
    order them across makers, just like poster drives.
    """
    __tablename__ = "artwork_drives"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    display_name = Column(String, nullable=True)  # Optional friendly display name from artwork_drives.json
    drive_id = Column(String, nullable=False, unique=True, index=True)
    subscribed = Column(Boolean, default=False)
    sync_enabled = Column(Boolean, default=True, nullable=False)  # If False, rclone is skipped but local folder is still scanned
    priority = Column(Integer, default=0)  # Higher = syncs first / preferred source when merging
    custom_path = Column(String, nullable=True)  # Custom sync path for this drive
    is_custom = Column(Boolean, default=False)  # True if user-added drive
    is_deprecated = Column(Boolean, default=False)  # True if removed from preset list
    last_synced = Column(DateTime(timezone=True), nullable=True)
    last_rename_processed = Column(DateTime(timezone=True), nullable=True)  # Last rename operation
    sync_file_count = Column(Integer, default=0)  # Current file count after sync
    last_files_transferred = Column(Integer, default=0)  # Files changed in last sync
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<ArtworkDrive(name='{self.name}', priority={self.priority}, subscribed={self.subscribed})>"

    def get_local_path(self, validate: bool = True) -> Path:
        """
        Get the local filesystem path where this drive's artwork is stored.
        Canonical path logic used by both sync and artwork manager.

        Args:
            validate: If True, logs a warning if the path doesn't exist on disk

        Returns:
            Path object representing the drive's local storage location
        """
        from core.config import settings

        if self.custom_path:
            # Use custom path if specified (can be relative or absolute)
            if Path(self.custom_path).is_absolute():
                path = Path(self.custom_path)
            else:
                path = settings.artwork_gdrive_dir / self.custom_path
        elif self.is_custom:
            # Custom drives go in Custom folder
            path = settings.artwork_gdrive_dir / "Custom" / self.name.replace(" ", "_")
        else:
            # Preset artwork drives are stored flat by name (no style grouping)
            path = settings.artwork_gdrive_dir / self.name.replace(" ", "_")

        # Validate path exists if requested
        if validate and not path.exists():
            log_warning(
                "DRIVES",
                f"Artwork drive '{self.name}' (ID: {self.id}) path does not exist: {path}. "
                f"This may indicate a sync issue or manual file system changes."
            )

        return path
