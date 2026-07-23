"""Shared helper for writing an image file into an IDarr scope's source folder.

Mirrors the write/collision logic in ``api/idarr.py``'s ``/upload`` endpoint, but is
subtype-explicit (the caller already knows logo/background/squareart) instead of guessing the
subfolder from a filename keyword. Used by the Artwork Finder's "Add" flow. IDarr's own upload
endpoint keeps its inline copy, so this file can be deleted with the feature.
"""
import shutil
import time
from pathlib import Path
from typing import Optional

from core.config import settings as app_settings
from core.logging import LogTags, log_error, log_warning

# subtype -> the subfolder IDarr scans for that artwork type (services/idarr_runner.py)
ASSET_SUBFOLDERS = {"logo": "logos", "background": "backgrounds", "squareart": "squareart"}


def resolve_asset_dir(source_dir: Path, subtype: Optional[str], is_asset_drive: bool) -> Path:
    """Where a dropped file should land. Asset drives split by subtype subfolder (unknown ->
    logos, matching the endpoint's default); everything else lands in the source root."""
    if is_asset_drive:
        return source_dir / ASSET_SUBFOLDERS.get((subtype or "").lower(), "logos")
    return source_dir


def place_asset_file(
    source_dir: Path,
    subtype: Optional[str],
    filename: str,
    data: bytes,
    *,
    is_asset_drive: bool = True,
) -> dict:
    """Write ``data`` to ``source_dir/<subfolder>/<filename>``, archiving any existing file to
    ``config/idarr/duplicates`` first (same as the IDarr upload endpoint). Returns
    ``{"dest": Path, "archived": bool}``."""
    dest_dir = resolve_asset_dir(source_dir, subtype, is_asset_drive)
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = dest_dir / filename

    archived = False
    if destination.exists():
        duplicates_dir = app_settings.config_dir / "idarr" / "duplicates"
        duplicates_dir.mkdir(parents=True, exist_ok=True)
        archived_path = duplicates_dir / filename
        if archived_path.exists():
            archived_path = duplicates_dir / f"{destination.stem}_{int(time.time())}{destination.suffix}"
        try:
            shutil.move(str(destination), str(archived_path))
            archived = True
            log_warning(LogTags.API, f"Artwork add archived existing file to duplicates: {filename}")
        except OSError as exc:
            log_error(LogTags.API, f"Artwork add could not archive existing '{filename}': {exc}")
            raise

    destination.write_bytes(data)
    return {"dest": destination, "archived": archived}
