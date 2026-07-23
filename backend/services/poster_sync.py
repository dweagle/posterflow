from pathlib import Path
import os
from typing import Any

from services.sync_base import BaseSyncService
from util.data.extract import extract_tmdb_id
from models.drive import Drive
from models.poster import Poster
from core.logging import LogTags, log_warning

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


class PosterSyncService(BaseSyncService):
    """Syncs posters from Google Drives into the `posters` table via the shared sync engine.

    Poster drives keep a flat folder of images; the match key is the file name, and the one
    type-specific field is the on-disk path.
    """

    drive_model = Drive
    content_model = Poster
    content_drive_attr = "drive_id"
    key_attr = "file_name"
    log_tag = LogTags.SYNC
    log_tag_all = LogTags.SYNC_ALL
    asset_label = "posters"
    third_field_label = "path"

    def _scan_local_files(self, folder: Path) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS:
                    st = entry.stat()
                    files.append({'name': entry.name, 'path': Path(entry.path), 'size': st.st_size, 'mtime': st.st_mtime})
        return files

    def _disk_keys(self, folder: Path) -> set:
        keys: set = set()
        if folder.exists() and folder.is_dir():
            try:
                for fp in folder.iterdir():
                    if fp.is_file() and fp.suffix.lower() in IMAGE_EXTENSIONS:
                        keys.add(fp.name)
            except Exception as e:
                log_warning(LogTags.SYNC, f"Could not scan folder before sync: {folder}: {e}", folder=str(folder))
        return keys

    def _file_key(self, file_info: dict) -> Any:
        return file_info['name']

    def _row_key(self, row) -> Any:
        return row.file_name

    def _query_existing_rows(self, drive_id: str):
        return (
            self.db.query(Poster.id, Poster.file_name, Poster.file_path, Poster.file_size, Poster.file_mtime)
            .filter(Poster.drive_id == drive_id)
            .all()
        )

    def _new_content_row(self, drive_id: str, file_info: dict):
        return Poster(
            drive_id=drive_id,
            file_name=file_info['name'],
            tmdb_id=extract_tmdb_id(file_info['name']),
            file_path=str(file_info['path']),
            file_size=file_info['size'],
            file_mtime=file_info['mtime'],
            gdrive_file_id=file_info['name'],  # filename as ID since we scan locally
        )

    def _third_field_changed(self, existing, file_info: dict) -> bool:
        return existing.file_path != str(file_info['path'])

    def _metadata_fields(self, file_info: dict) -> dict:
        return {'file_path': str(file_info['path']), 'file_size': file_info['size'], 'file_mtime': file_info['mtime']}

    def _is_local_only(self, drive) -> bool:
        # Also treat a manual custom drive with no real Drive ID as local-folder-only.
        return (not drive.sync_enabled) or (
            drive.is_custom and (drive.drive_id.startswith('manual-') or not (drive.drive_id or '').strip())
        )
