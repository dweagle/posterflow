"""Shared backup zip builder used by the manual download endpoint and scheduled backups."""
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from core.config import settings as app_settings
from core.logging import LogTags, log_info, log_success, log_warning
from models.setting import get_setting_value

BACKUP_PREFIX = "posterflow_backup_"
DEFAULT_RETENTION = 7

# Config files bundled alongside the database snapshot
CONFIG_FILES = ("rclone.conf", "drives_cache.json", "artwork_drives_cache.json")


class UnsupportedDatabaseBackup(NotImplementedError):
    pass


def sqlite_database_path() -> Path:
    """The database file the app actually opened; backup and restore both work on this."""
    url = make_url(app_settings.database_url)
    database = url.database or ""
    if (
        url.get_backend_name() != "sqlite"
        or not database
        or database == ":memory:"
        or database.startswith("file:")
        or url.query.get("mode") == "memory"
        or "uri" in url.query
    ):
        raise UnsupportedDatabaseBackup("Backup and restore need a file-backed SQLite database")
    return Path(database).resolve()


def _copy_database(source: Path, dest: Path) -> None:
    # SQLite's online backup copies a consistent snapshot, pending journal included, even
    # while the app is writing; a plain file copy would miss or scramble the journal
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest), timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def snapshot_database(dest: Path) -> None:
    """Write a consistent copy of the live database to dest."""
    _copy_database(sqlite_database_path(), dest)


def is_sqlite_database(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(16) == b"SQLite format 3\x00"


def restore_database(backup_file: Path) -> None:
    """Load a backup into the live database file through SQLite, so the journal stays consistent."""
    _copy_database(backup_file, sqlite_database_path())


def default_backup_dir() -> Path:
    return app_settings.config_dir / "backups"


def get_backup_location(db: Session) -> Path:
    """Resolve the scheduled-backup destination, falling back to the default dir."""
    raw = str(get_setting_value(db, "backup_location", "") or "").strip()
    return Path(raw) if raw else default_backup_dir()


def get_backup_retention(db: Session) -> int:
    """Number of scheduled backups to keep (0 = keep all)."""
    raw = get_setting_value(db, "backup_retention", None)
    try:
        return max(int(str(raw).strip()), 0)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION


def build_backup_zip(dest_dir: Path) -> Path:
    """Create a timestamped backup zip in dest_dir and return its path."""
    sqlite_database_path()
    dest_dir.mkdir(parents=True, exist_ok=True)
    backup_path = dest_dir / f"{BACKUP_PREFIX}{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    metadata = {
        "version": "1.0",
        "created_at": datetime.now().isoformat(),
        "app": "PosterFlow",
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        # snapshot first: a backup without the database is worse than a failed backup
        db_snapshot = Path(temp_dir) / "posterflow.db"
        snapshot_database(db_snapshot)
        try:
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(db_snapshot, "posterflow.db")
                log_info(LogTags.BACKUP, "Added database to backup")

                for name in CONFIG_FILES:
                    source = app_settings.config_dir / name
                    if source.exists():
                        zipf.write(source, name)
                        log_info(LogTags.BACKUP, f"Added {name} to backup")

                zipf.writestr("metadata.json", json.dumps(metadata, indent=2))
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise

    log_success(LogTags.BACKUP, f"Created backup: {backup_path.name}")
    return backup_path


def prune_backups(dest_dir: Path, keep: int) -> int:
    """Delete the oldest posterflow_backup_*.zip files beyond the newest `keep`."""
    if keep <= 0:
        return 0
    backups = sorted(dest_dir.glob(f"{BACKUP_PREFIX}*.zip"), key=lambda p: p.name)
    removed = 0
    for old in backups[:-keep]:
        try:
            old.unlink()
            removed += 1
            log_info(LogTags.BACKUP, f"Pruned old backup: {old.name}")
        except OSError as e:
            log_warning(LogTags.BACKUP, f"Failed to prune {old.name}: {e}")
    return removed


def run_backup_to_location(db: Session) -> Path:
    """Build a backup zip in the configured backup location and apply retention.

    Used by both scheduled runs and the manual save-to-location action."""
    dest_dir = get_backup_location(db)
    backup_path = build_backup_zip(dest_dir)
    pruned = prune_backups(dest_dir, get_backup_retention(db))

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    suffix = f" ({pruned} old backup{'s' if pruned != 1 else ''} pruned)" if pruned else ""
    log_success(LogTags.BACKUP, f"Backup saved to {backup_path} ({size_mb:.1f} MB){suffix}")
    return backup_path
