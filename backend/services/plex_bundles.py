"""Plex keeps every uploaded poster in the item's bundle folder and offers no API to remove
them. Given each server's Metadata folder (as Posterflow sees it) the superseded uploads can
be pruned from disk. Shared by the overlay engine (tidying after each badge) and the Tools page.
A movie or show in two libraries (Movies and 4K Movies) is two items with ONE bundle folder, so
a prune there keeps every image each copy shows at that moment, asked of Plex then."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from models.setting import get_setting_value, upsert_setting
from services.server_posters import upload_sha
from util.media_server.plex import PLEX_TYPE_DIRS, bundle_identity

SETTING_PLEX_METADATA_PATHS = "plex_metadata_paths"
UPLOAD_FOLDERS = {"poster": "posters", "background": "art", "logo": "clearLogos", "squareart": "squareArt"}  # Uploads/<folder> per image kind


def normalize_plex_metadata_paths(raw: Any) -> Dict[str, str]:
    """{instance name: Metadata folder}; blanks dropped, trailing slashes trimmed."""
    if not isinstance(raw, dict):
        return {}
    trimmed = {str(k).strip(): str(v or "").strip().rstrip("/\\") for k, v in raw.items()}
    return {k: v for k, v in trimmed.items() if k and v}


def is_plex_metadata_root(path: str) -> bool:
    """A Plex Metadata folder holds the per-type bundle folders (Movies, TV Shows, ...)."""
    return any((Path(path) / d).is_dir() for d in ("Movies", "TV Shows"))


def resolve_plex_metadata_root(path: str) -> str:
    """The Metadata folder for a configured path: the path itself, or its Metadata subfolder
    when the path is the Plex Media Server data folder (the folder ImageMaid takes)."""
    if path and not is_plex_metadata_root(path) and (Path(path) / "Metadata").is_dir():
        return str(Path(path) / "Metadata")
    return path


def load_plex_metadata_paths(db: Session, *, resolve: bool = True) -> Dict[str, str]:
    """{instance name: Metadata folder}; `resolve=False` returns the paths as saved."""
    raw = get_setting_value(db, SETTING_PLEX_METADATA_PATHS)
    if not raw:
        return {}
    try:
        paths = normalize_plex_metadata_paths(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return {}
    return {k: resolve_plex_metadata_root(v) for k, v in paths.items()} if resolve else paths


def save_plex_metadata_paths(db: Session, raw: Any) -> Dict[str, str]:
    paths = normalize_plex_metadata_paths(raw)
    upsert_setting(db, SETTING_PLEX_METADATA_PATHS, json.dumps(paths))
    db.commit()
    return paths


def plex_bundle_dir(metadata_root: str, guid: str, item_type: str) -> Path:
    """Plex keeps an item's metadata in <Metadata>/<Type>/<h[0]>/<h[1:]>.bundle where h is
    the SHA1 of its guid. Seasons live inside their show's bundle."""
    h = hashlib.sha1(guid.encode("utf-8")).hexdigest()
    return Path(metadata_root) / PLEX_TYPE_DIRS.get(item_type, "TV Shows") / h[0] / f"{h[1:]}.bundle"


def plex_item_bundle(metadata_root: str, item: Any, parent: Any = None) -> Optional[Path]:
    """The bundle folder an item's images live in (a season's is its show's), or None when its
    guid is unknown."""
    identity = bundle_identity(item, parent)
    if identity is None:
        return None
    h = hashlib.sha1(identity[1].encode("utf-8")).hexdigest()
    return Path(metadata_root) / identity[0] / h[0] / f"{h[1:]}.bundle"


def plex_uploads_dir(metadata_root: str, item: Any, parent: Any = None, kind: str = "poster") -> Optional[Path]:
    """The folder holding the item's uploads of one image kind, or None when its guid is unknown
    (or the kind does not apply: seasons only take posters)."""
    folder = UPLOAD_FOLDERS.get(kind)
    if folder is None or (item.item_type == "season" and kind != "poster"):
        return None
    bundle = plex_item_bundle(metadata_root, item, parent)
    if bundle is None:
        return None
    if item.item_type == "season":
        return bundle / "Uploads" / "posters" / "seasons" / str(item.index) if item.index is not None else None
    return bundle / "Uploads" / folder


def shown_by_copies(client: Any, copies: List[Any], item: Any, parent: Any, kind: str) -> Optional[Set[str]]:
    """The uploads the OTHER copies of a title show now, asked of Plex, by sha (a copy showing
    the provider's image, or none, adds nothing). For a season, the same season of each other
    copy. None when Plex could not say for one of them."""
    lookup = getattr(client, "selected_image_key", None)
    if not callable(lookup):
        return None
    own = parent if item.item_type == "season" else item
    shown: Set[str] = set()
    for copy in copies:
        if own is None or str(copy.item_id) == str(own.item_id):
            continue
        target = copy
        if item.item_type == "season":
            try:
                target = next((s for s in client.get_seasons(copy) if s.index == item.index), None)
            except Exception:
                return None
            if target is None:
                continue  # that copy does not have this season: nothing of it is in the folder
        key = lookup(target, kind)
        if not key:
            return None
        if upload_sha(key):
            shown.add(upload_sha(key))
    return shown


@dataclass
class PruneResult:
    removed: int = 0
    freed: int = 0
    missing: bool = False  # the folder, or every keep file, is absent: wrong path or not uploaded yet
    error: str = ""  # a delete the process is not allowed to do: the folder is right but not writable by it
    shared: bool = False  # Plex could not say if another library's copy uses the folder, or what that copy shows: left alone


def upload_gone(root: str, item: Any, parent: Any, sha: str) -> bool:
    """Is an upload Plex should show gone from the item's folder? Only when the folder is there:
    with no folder the path may be wrong, and nothing can be told."""
    folder = plex_uploads_dir(root, item, parent) if sha else None
    return folder is not None and folder.is_dir() and not (folder / sha).is_file()


def prune_plex_uploads(root: str, item: Any, parent: Any, keep: Optional[Set[str]], *, kind: str = "poster", dry_run: bool = False,
                       client: Any = None) -> PruneResult:
    """Remove superseded uploads of one image kind from the item's bundle folder, keeping the
    SHA1-named files in `keep` (the image Plex shows now). `keep=None` removes every upload,
    provided the folder exists. With a keep set, nothing is deleted unless one of those files is
    actually there — so a wrong path deletes nothing. For a title in more than one library (the
    Plex `client` says) the images its other copies show are kept too, asked of Plex then; when
    Plex cannot say, the folder is left alone."""
    result = PruneResult()
    folder = plex_uploads_dir(root, item, parent, kind)
    if folder is None:
        return result
    if not folder.is_dir():
        result.missing = keep is not None and any(keep)
        return result
    lookup = getattr(client, "shared_copies", None)
    copies = lookup(item, parent) if callable(lookup) else []
    if copies is None:
        result.shared = True
        return result
    if copies:
        others = shown_by_copies(client, copies, item, parent, kind)
        if others is None:
            result.shared = True
            return result
        if keep is not None or others:  # with nothing any other copy shows, `keep=None` still clears the folder
            keep = set(keep or ()) | others
    if keep is None:
        keep = set()
    else:
        keep = {k for k in keep if k}
        if not keep:
            return result
        if not any((folder / k).is_file() for k in keep):
            result.missing = True
            return result
    if not os.access(folder, os.W_OK):  # checked in a dry run too, so a rehearsal already says so
        result.error = f"no write access to {folder}"
        return result
    for entry in folder.iterdir():
        if not entry.is_file() or entry.name in keep:
            continue
        try:
            size = entry.stat().st_size
            if not dry_run:
                entry.unlink()
        except OSError as exc:
            result.error = f"{exc.strerror or exc}: {entry}"
            break
        result.removed += 1
        result.freed += size
    return result
