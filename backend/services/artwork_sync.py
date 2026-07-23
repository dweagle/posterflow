import re
from pathlib import Path
from typing import Any

from services.sync_base import BaseSyncService
from models.artwork_drive import ArtworkDrive
from models.artwork import Artwork
from util.data.extract import extract_ids, extract_year
from core.logging import LogTags, log_debug

# Artwork image extensions (logos are PNG, backgrounds/squareart are JPG/WEBP).
ARTWORK_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

# Artwork drive layout: each subtype lives in its own subfolder.
ARTWORK_SUBFOLDER_SUBTYPES = [("logo", "logos"), ("background", "backgrounds"), ("squareart", "squareart")]

# Strip a trailing " - logo"/" - background"/" - squareart" suffix when parsing a title.
_SUBTYPE_SUFFIX_RE = re.compile(r"\s*-\s*(?:logo|background|squareart)s?\s*$", re.IGNORECASE)
_ID_TAG_RE = re.compile(r"\{[^}]*\}")
_YEAR_PAREN_RE = re.compile(r"\(\d{4}\)")


def scan_artwork_files(folder: Path) -> list[dict[str, Any]]:
    """Walk the logos/backgrounds/squareart subfolders and return one dict per image file.

    Subtype comes from which subfolder the file is in. Files outside those three
    subfolders are ignored. Shared by the sync indexer and the live drive scan.
    """
    results: list[dict[str, Any]] = []
    if not folder.exists() or not folder.is_dir():
        return results
    for subtype, subfolder_name in ARTWORK_SUBFOLDER_SUBTYPES:
        sub = folder / subfolder_name
        if not sub.is_dir():
            continue
        for f in sub.rglob('*'):
            if not (f.is_file() and f.suffix.lower() in ARTWORK_IMAGE_EXTENSIONS):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            results.append({
                'name': f.name,
                'path': str(f),
                'size': st.st_size,
                'mtime': st.st_mtime,
                'artwork_type': subtype,
            })
    return results


def artwork_group_stem(file_name: str) -> str:
    """Filename stem minus the ' - logo' / ' - background' / ' - squareart' suffix.

    The key that groups ONE item's artwork files across the type subfolders. Drives name them
    "Title (Year) {ids} - logo.png" / "... - background.jpg", so a plain splitext stem differs
    per type and groups nothing.
    """
    return _SUBTYPE_SUFFIX_RE.sub("", Path(file_name).stem).strip()


def parse_artwork_title(file_name: str) -> str:
    """Best-effort title: filename stem minus the subtype suffix, id tags and (year)."""
    stem = Path(file_name).stem
    stem = _SUBTYPE_SUFFIX_RE.sub("", stem)
    stem = _ID_TAG_RE.sub("", stem)
    stem = _YEAR_PAREN_RE.sub("", stem)
    return re.sub(r"\s+", " ", stem).strip()


class ArtworkSyncService(BaseSyncService):
    """Syncs artwork drives (logos / backdrops / square art) into the `artwork` table via the
    shared sync engine — identical behavior to posters.

    Artwork drives lay files out in logos/backgrounds/squareart subfolders, so the match key is
    the full file path (a name can repeat across subtypes) and the one type-specific field is
    the artwork_type. Each row also carries the parsed title/year/ids (forward-looking, mirroring
    Poster.tmdb_id).
    """

    drive_model = ArtworkDrive
    content_model = Artwork
    content_drive_attr = "artwork_drive_id"
    key_attr = "file_path"
    log_tag = LogTags.SYNC
    log_tag_all = LogTags.SYNC_ALL
    asset_label = "artwork"
    third_field_label = "artwork_type"

    def _get_local_path(self, drive) -> Path:
        return drive.get_local_path(validate=False)

    def _scan_local_files(self, folder: Path) -> list[dict[str, Any]]:
        return scan_artwork_files(folder)

    def _disk_keys(self, folder: Path) -> set:
        return {s['path'] for s in scan_artwork_files(folder)}

    def _file_key(self, file_info: dict) -> Any:
        return file_info['path']

    def _row_key(self, row) -> Any:
        return row.file_path

    def _query_existing_rows(self, drive_id: str):
        return (
            self.db.query(Artwork.id, Artwork.file_path, Artwork.file_name, Artwork.file_size, Artwork.file_mtime, Artwork.artwork_type)
            .filter(Artwork.artwork_drive_id == drive_id)
            .all()
        )

    def _new_content_row(self, drive_id: str, file_info: dict):
        return Artwork(artwork_drive_id=drive_id, file_path=file_info['path'], **self._metadata_fields(file_info))

    def _third_field_changed(self, existing, file_info: dict) -> bool:
        return existing.artwork_type != file_info['artwork_type']

    def _metadata_fields(self, file_info: dict) -> dict:
        name = file_info['name']
        tmdb_id, tvdb_id, imdb_id = extract_ids(name)
        return {
            'file_name': name,
            'artwork_type': file_info['artwork_type'],
            'title': parse_artwork_title(name),
            'year': extract_year(name),
            'tmdb_id': tmdb_id,
            'tvdb_id': tvdb_id,
            'imdb_id': imdb_id,
            'file_size': file_info['size'],
            'file_mtime': file_info['mtime'],
        }

    def _index_artwork(self, drive: ArtworkDrive) -> dict[str, Any]:
        """Standalone re-index (no rclone / no job) for the "refresh counts" endpoint: scan the
        drive folder and upsert Artwork rows. Returns total, per-type counts, add/update/delete.
        """
        folder = drive.get_local_path(validate=False)
        scanned = scan_artwork_files(folder)
        scanned_paths = {s['path'] for s in scanned}

        existing = (
            self.db.query(Artwork.id, Artwork.file_path, Artwork.file_size, Artwork.file_mtime, Artwork.artwork_type)
            .filter(Artwork.artwork_drive_id == drive.drive_id)
            .all()
        )
        existing_by_path = {r.file_path: r for r in existing}

        orphaned_ids = [r.id for path, r in existing_by_path.items() if path not in scanned_paths]
        deleted = 0
        if orphaned_ids:
            self.db.query(Artwork).filter(Artwork.id.in_(orphaned_ids)).delete(synchronize_session=False)
            deleted = len(orphaned_ids)

        added = 0
        updated = 0
        MTIME_TOLERANCE = 1.0
        pending_inserts: list[Artwork] = []
        pending_updates: list[dict[str, Any]] = []

        for s in scanned:
            ex = existing_by_path.get(s['path'])
            fields = self._metadata_fields(s)
            if ex:
                changed = (
                    ex.file_mtime is None
                    or abs((ex.file_mtime or 0.0) - s['mtime']) > MTIME_TOLERANCE
                    or ex.file_size != s['size']
                    or ex.artwork_type != s['artwork_type']
                )
                if changed:
                    pending_updates.append({'id': ex.id, 'last_processed': None, **fields})
                    updated += 1
            else:
                pending_inserts.append(Artwork(artwork_drive_id=drive.drive_id, file_path=s['path'], **fields))
                added += 1

        if pending_updates:
            self.db.bulk_update_mappings(Artwork, pending_updates)
        if pending_inserts:
            self.db.bulk_save_objects(pending_inserts)
        self.db.commit()

        by_type = {'logo': 0, 'background': 0, 'squareart': 0}
        for s in scanned:
            by_type[s['artwork_type']] = by_type.get(s['artwork_type'], 0) + 1

        log_debug(
            LogTags.SYNC,
            f"Artwork re-index for '{drive.name}': {len(scanned)} files "
            f"({by_type['logo']} logos, {by_type['background']} backgrounds, {by_type['squareart']} squareart | "
            f"{added} added, {updated} updated, {deleted} deleted)",
            drive=drive.name,
        )
        return {'total': len(scanned), 'by_type': by_type, 'added': added, 'updated': updated, 'deleted': deleted}
