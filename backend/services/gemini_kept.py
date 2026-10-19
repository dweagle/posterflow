"""Server-side folder for Gemini results (config/gemini/<item>/). A run lands here as a pending
file, Keep marks it so the maker card lists it as a gallery tile, and the PSD export that uses
it deletes it. Pending files and long-kept ones are swept so the folder never builds up."""
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from core.config import settings as app_settings
from core.logging import LogTags, log_info, log_warning

REF_PREFIX = "gemini:"
PENDING_TTL = 60 * 60            # a result the user never kept
KEPT_TTL = 7 * 24 * 60 * 60      # a kept result never exported
MIME = {"jpg": "image/jpeg", "png": "image/png"}
_KEY_RE = re.compile(r"^(tmdb|tvdb|title)-[a-z0-9-]{1,60}$")
_NAME_RE = re.compile(r"^(poster|background|logo)-[0-9a-f]{12}\.(jpg|png)$")


def folder() -> Path:
    return app_settings.config_dir / "gemini"


def item_key(tmdb_id: Optional[int], tvdb_id: Optional[int], title: str) -> str:
    """One sub-folder per title: by TMDB id, else TVDB id, else a title slug."""
    if tmdb_id:
        return f"tmdb-{int(tmdb_id)}"
    if tvdb_id:
        return f"tvdb-{int(tvdb_id)}"
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:60]
    return f"title-{slug or 'untitled'}"


def is_ref(ref: Any) -> bool:
    return isinstance(ref, str) and ref.startswith(REF_PREFIX)


def ref_path(ref: str) -> Path | None:
    """The file behind a gemini: ref, or None when the ref is malformed (so it can never escape the folder)."""
    parts = ref[len(REF_PREFIX):].split("/")
    if len(parts) != 2 or not _KEY_RE.match(parts[0]) or not _NAME_RE.match(parts[1]):
        return None
    return folder() / parts[0] / parts[1]


def is_valid_ref(ref: str) -> bool:
    path = ref_path(ref)
    return path is not None and path.is_file()


def read_ref(ref: str) -> bytes:
    path = ref_path(ref)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"No kept Gemini image for {ref}")
    return path.read_bytes()


def file_url(ref: str, download: bool = False) -> str:
    return f"/api/maker-tools/gemini-image/file?ref={quote(ref, safe='')}" + ("&download=1" if download else "")


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".json")


def read_meta(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(_meta_path(path).read_text())
    except (OSError, ValueError):
        return None


def _write_meta(path: Path, meta: dict[str, Any]) -> None:
    _meta_path(path).write_text(json.dumps(meta))


def _ref_for(path: Path) -> str:
    return f"{REF_PREFIX}{path.parent.name}/{path.name}"


def tile(ref: str, meta: dict[str, Any]) -> dict[str, Any]:
    """What the maker card needs to show the result as a gallery image."""
    return {
        "ref": ref,
        "role": meta.get("role", "poster"),
        "source": meta.get("source", "tmdb"),
        "season": meta.get("season"),
        "operation": meta.get("operation", "remove_text"),
        "width": meta.get("width", 0),
        "height": meta.get("height", 0),
        "download_name": meta.get("download_name", ""),
        "url_full": file_url(ref),
        "url_thumb": file_url(ref),
    }


def save_pending(image: bytes, *, ext: str, key: str, meta: dict[str, Any]) -> str:
    """Write a fresh result and its sidecar (not yet kept); returns the ref."""
    if ext not in MIME or not _KEY_RE.match(key):
        raise ValueError("bad extension or item key")
    name = f"{meta['role']}-{hashlib.sha1(image).hexdigest()[:12]}.{ext}"
    target = folder() / key
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    path.write_bytes(image)
    _write_meta(path, {**meta, "kept": False, "created": time.time()})
    return _ref_for(path)


def keep(ref: str) -> dict[str, Any] | None:
    """Mark a pending result as kept; None when it is gone (swept or never existed)."""
    path = ref_path(ref)
    if path is None or not path.is_file():
        return None
    meta = read_meta(path) or {}
    meta["kept"] = True
    meta["created"] = time.time()
    _write_meta(path, meta)
    return tile(ref, meta)


def delete(ref: str) -> bool:
    path = ref_path(ref)
    if path is None or not path.is_file():
        return False
    path.unlink()
    _meta_path(path).unlink(missing_ok=True)
    _prune_dir(path.parent)
    return True


def list_kept(key: str) -> list[dict[str, Any]]:
    """Kept results for one title, newest first."""
    target = folder() / key
    if not _KEY_RE.match(key) or not target.is_dir():
        return []
    tiles = []
    for path in target.iterdir():
        if not _NAME_RE.match(path.name):
            continue
        meta = read_meta(path)
        if meta and meta.get("kept"):
            tiles.append((meta.get("created", 0), tile(_ref_for(path), meta)))
    tiles.sort(key=lambda t: t[0], reverse=True)
    return [t for _, t in tiles]


def clear_used(refs: list[str]) -> list[str]:
    """Delete the kept results a finished PSD export used; returns the refs that were removed."""
    cleared = [ref for ref in refs if is_ref(ref) and delete(ref)]
    if cleared:
        log_info(LogTags.API, f"Cleared {len(cleared)} Gemini image(s) used by the PSD export")
    return cleared


def sweep(now: float | None = None) -> int:
    """Drop pending results older than an hour and kept ones older than a week."""
    root = folder()
    if not root.is_dir():
        return 0
    now = now or time.time()
    removed = 0
    for item_dir in [d for d in root.iterdir() if d.is_dir()]:
        for path in [p for p in item_dir.iterdir() if _NAME_RE.match(p.name)]:
            meta = read_meta(path) or {}
            age = now - float(meta.get("created") or path.stat().st_mtime)
            if age > (KEPT_TTL if meta.get("kept") else PENDING_TTL):
                try:
                    path.unlink()
                    _meta_path(path).unlink(missing_ok=True)
                    removed += 1
                except OSError as exc:
                    log_warning(LogTags.API, f"Could not sweep {path.name}: {exc}")
        _prune_dir(item_dir)
    if removed:
        log_info(LogTags.API, f"Swept {removed} stale Gemini image(s)")
    return removed


def _prune_dir(item_dir: Path) -> None:
    try:
        if item_dir.is_dir() and not any(item_dir.iterdir()):
            item_dir.rmdir()
    except OSError:
        pass
