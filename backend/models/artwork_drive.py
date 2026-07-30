from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from pathlib import Path
from database import Base
from core.logging import log_warning

# Canonical artwork types and the drive subfolder each one lives in. Lives on the model
# (not the sync service) so migrations, API and sync all read the same source.
ARTWORK_TYPES = ("logo", "background", "squareart")
ARTWORK_TYPE_SUBFOLDERS = {"logo": "logos", "background": "backgrounds", "squareart": "squareart"}
ALL_ARTWORK_TYPES_CSV = ",".join(ARTWORK_TYPES)


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
    # Which artwork types this drive syncs, comma-separated. Unselected types are filtered out
    # of the rclone transfer, so a user can take logos only, backdrops only, or any mix.
    synced_types = Column(String, nullable=False, default=ALL_ARTWORK_TYPES_CSV, server_default=ALL_ARTWORK_TYPES_CSV)
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

    @property
    def synced_type_list(self) -> list[str]:
        """Artwork types this drive syncs, in canonical order. An empty/garbage value means
        all three — a drive that syncs nothing is never what a stored value should imply."""
        picked = {t.strip() for t in (self.synced_types or "").split(",") if t.strip()}
        selected = [t for t in ARTWORK_TYPES if t in picked]
        return selected or list(ARTWORK_TYPES)

    @property
    def synced_subfolders(self) -> list[str]:
        """Drive subfolders this drive pulls (e.g. ['logos', 'backgrounds'])."""
        return [ARTWORK_TYPE_SUBFOLDERS[t] for t in self.synced_type_list]

    @property
    def excluded_subfolders(self) -> list[str]:
        """Drive subfolders to keep out of the transfer (empty when all types are selected)."""
        selected = set(self.synced_type_list)
        return [ARTWORK_TYPE_SUBFOLDERS[t] for t in ARTWORK_TYPES if t not in selected]

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
