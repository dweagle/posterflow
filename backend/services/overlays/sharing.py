"""Overlay exports: overlays written to one zip that another install can read.

An export holds overlays.json (each overlay's type, name, placement and options) and the
images the overlays draw: a custom overlay's own pictures, and for the other types the files
the user replaced. What belongs to the install stays out: the libraries an overlay names, its
switch, and the server or Arr instance a condition's value was picked for.

An export is read as a stranger's file. Entries are looked up by the names the overlay list
implies, never taken from the zip's own listing; every image passes the upload checks; every
overlay goes through its type's own cleanup.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
import zlib
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from . import images
from .base import OverlayType, normalize_placement
from .registry import all_types, get_type
from .types.custom import KEY as CUSTOM_KEY, MAX_BADGES, badge_file, store_badge

FORMAT = "posterflow-overlays"
VERSION = 1
MANIFEST = "overlays.json"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_OVERLAYS = 200
_IMAGE_ID = re.compile(r"[0-9a-f]{12}")
_LIST_FIELDS = ("list", "tmdb_list", "id_list")  # conditions whose value is an address the app reads


class ExportError(ValueError):
    """What is wrong with an export or a file to import, in words for the user."""


def _rows(options: Any) -> List[Dict[str, Any]]:
    """A custom overlay's badges, as far as its type keeps them."""
    rows = options.get("badges") if isinstance(options, dict) else None
    return [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)][:MAX_BADGES]


def _rules(options: Any) -> List[Dict[str, Any]]:
    return [rule for row in _rows(options) for rule in (row.get("rules") if isinstance(row.get("rules"), list) else []) if isinstance(rule, dict)]


def _shareable(options: Any) -> Dict[str, Any]:
    """The options without the server or Arr instance a condition's value was picked for."""
    out = deepcopy(options) if isinstance(options, dict) else {}
    for rule in _rules(out):
        rule.pop("instance", None)
        for part in rule.get("also") if isinstance(rule.get("also"), list) else []:
            if isinstance(part, dict):
                part.pop("instance", None)
    return out


def _host(address: Any) -> str:
    """The host a list address names ('' for one that cannot be read as an address)."""
    try:
        return urlsplit(str(address or "")).hostname or ""
    except ValueError:
        return ""


def _image_ids(options: Any) -> List[str]:
    """The pictures a custom overlay's badges name, each once."""
    return list(dict.fromkeys(str(row.get("image")) for row in _rows(options) if _IMAGE_ID.fullmatch(str(row.get("image") or ""))))


def replaced_images(otype: OverlayType) -> List[Tuple[str, str, Path]]:
    """(set, key, file) of every image of a type that the user replaced with their own."""
    library = otype.images
    if library is None:
        return []
    return [(image_set, key, library.custom_file(key, image_set))
            for image_set, _label in library.sets for key, _l in otype.image_items_in(image_set)
            if library.custom_file(key, image_set).is_file()]


def replaced_counts() -> Dict[str, int]:
    """Per overlay type: how many of its images the user replaced (types with none are left out)."""
    counts = {key: len(replaced_images(otype)) for key, otype in all_types().items()}
    return {key: n for key, n in counts.items() if n}


# ── writing ──────────────────────────────────────────────────────────────

def build_export(config: Dict[str, Any], picks: Iterable[Tuple[str, int]], image_types: Iterable[str] = (), app: str = "") -> Tuple[bytes, List[str]]:
    """The zip for the overlays picked ((type, index) into the settings) and the names of the
    overlays in it. `image_types` names the types whose replaced images go along."""
    types = config.get("types") or {}
    entries: List[Dict[str, Any]] = []
    files: Dict[str, Path] = {}
    for key, index in dict.fromkeys(picks):
        otype = get_type(key)
        overlays = (types.get(key) or {}).get("overlays") or []
        if otype is None or not 0 <= index < len(overlays):
            continue
        overlay = overlays[index]
        options = _shareable(overlay.get("options"))
        entries.append({"type": key, "name": str(overlay.get("name") or otype.label), "placement": dict(overlay.get("placement") or {}), "options": options})
        if key == CUSTOM_KEY:
            files.update({f"images/{key}/{image_id}.png": path for image_id in _image_ids(options) for path in [badge_file(image_id)] if path is not None})
    if not entries:
        raise ExportError("Pick at least one overlay to export")
    if len(entries) > MAX_OVERLAYS:
        raise ExportError(f"One file holds at most {MAX_OVERLAYS} overlays. Pick fewer.")
    type_images: Dict[str, List[Dict[str, str]]] = {}
    for key in dict.fromkeys(image_types):
        otype = get_type(key)
        found = replaced_images(otype) if otype is not None and any(e["type"] == key for e in entries) else []
        if found:
            type_images[key] = [{"set": image_set, "key": name} for image_set, name, _path in found]
            files.update({f"images/{key}/{image_set}/{name}.png": path for image_set, name, path in found})
    manifest = json.dumps({"format": FORMAT, "version": VERSION, "app": app, "created": date.today().isoformat(),
                           "overlays": entries, "images": type_images}, indent=1).encode("utf-8")
    sizes = sum(path.stat().st_size for path in files.values())
    if len(manifest) > MAX_MANIFEST_BYTES or len(files) + 1 > MAX_ENTRIES or sizes + len(manifest) > MAX_FILE_BYTES:
        raise ExportError("That is too much for one file. Pick fewer overlays.")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(MANIFEST, manifest, compress_type=zipfile.ZIP_DEFLATED)
        for name, path in files.items():
            zf.write(path, name, compress_type=zipfile.ZIP_STORED)  # a PNG is compressed already
    return out.getvalue(), [e["name"] for e in entries]


# ── reading ──────────────────────────────────────────────────────────────

def _read(zf: zipfile.ZipFile, name: str, limit: int) -> Optional[bytes]:
    """An entry's bytes, or None when it is missing, unreadable or larger than `limit`."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.is_dir() or info.file_size > limit:
        return None
    try:
        with zf.open(info) as entry:
            data = entry.read(limit + 1)  # the size in the listing is the file's own word
    except (zipfile.BadZipFile, zlib.error, OSError, RuntimeError, NotImplementedError, EOFError):
        return None
    return data if len(data) <= limit else None


def _decoded(raw: bytes) -> Any:
    """Image bytes checked like an upload: the image, or None."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        if max(img.size) > images.MAX_UPLOAD_SIDE:
            return None
        img.load()
        return img
    except Exception:
        return None


def _has(zf: zipfile.ZipFile, name: str) -> bool:
    try:
        return not zf.getinfo(name).is_dir()
    except KeyError:
        return False


class OverlayImport:
    """An export opened for reading. Use it as a context manager: it holds the file open."""

    def __init__(self, zf: zipfile.ZipFile, manifest: Dict[str, Any]) -> None:
        self._zf = zf
        self._entries: List[Any] = manifest["overlays"]
        self._images: Dict[str, Any] = manifest.get("images") if isinstance(manifest.get("images"), dict) else {}
        self._stored: Dict[str, str] = {}  # a picture's id in the file → its id here
        self._room = MAX_FILE_BYTES  # image bytes still read from this file: no more than an export can hold

    def __enter__(self) -> "OverlayImport":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._zf.close()

    def _image(self, name: str) -> Any:
        """An image from the file, checked like an upload, or None."""
        raw = _read(self._zf, name, min(images.MAX_UPLOAD_BYTES, self._room)) if self._room > 0 else None
        if not raw:
            return None
        self._room -= len(raw)
        return _decoded(raw)

    def _known(self) -> List[Tuple[int, OverlayType, Dict[str, Any]]]:
        """(index, type, entry) of the overlays whose type this install has."""
        found = []
        for index, entry in enumerate(self._entries):
            otype = get_type(str(entry.get("type") or "")) if isinstance(entry, dict) else None
            if otype is not None:
                found.append((index, otype, entry))
        return found

    def _type_images(self, key: str) -> List[Tuple[str, str, str]]:
        """(set, key, entry name) of a type's images that are in the file and that the type has."""
        otype = get_type(key)
        listed = self._images.get(key)
        if otype is None or otype.images is None or not isinstance(listed, list):
            return []
        sets = {image_set: {name for name, _l in otype.image_items_in(image_set)} for image_set, _label in otype.images.sets}
        found = []
        for item in listed:
            image_set, name = (str(item.get("set") or ""), str(item.get("key") or "")) if isinstance(item, dict) else ("", "")
            entry = f"images/{key}/{image_set}/{name}.png"
            if name in sets.get(image_set, ()) and _has(self._zf, entry):
                found.append((image_set, name, entry))
        return list(dict.fromkeys(found))

    def _notes(self, otype: OverlayType, entry: Dict[str, Any], options: Dict[str, Any], fonts: set) -> List[str]:
        notes: List[str] = []
        raw_rows = entry.get("options", {}).get("badges") if isinstance(entry.get("options"), dict) else None
        if isinstance(raw_rows, list) and len(raw_rows) > MAX_BADGES:
            notes.append(f"Only the first {MAX_BADGES} of its {len(raw_rows)} badges are kept.")
        rules = _rules(options)
        patterns = sum(1 for rule in rules if rule.get("op") == "regex")
        if patterns:
            notes.append(f"{patterns} condition{'s use' if patterns != 1 else ' uses'} a regex pattern. Check {'them' if patterns != 1 else 'it'} before switching the overlay on.")
        hosts = sorted({host for rule in rules if rule.get("field") in _LIST_FIELDS
                        for part in [rule, *(rule.get("also") or [])] for host in [_host(part.get("value"))] if host})
        if hosts:
            notes.append(f"Its conditions read lists from {', '.join(hosts[:5])}{' and more' if len(hosts) > 5 else ''}.")
        font = str((entry.get("placement") or {}).get("font") or "") if isinstance(entry.get("placement"), dict) else ""
        draws_text = "text" not in otype.hidden_placement and not (otype.key == CUSTOM_KEY and options.get("badge") != "text")
        if font and draws_text and font not in fonts:
            notes.append(f"The font {font[:60]} is not installed here. The default font is drawn.")
        return notes

    def describe(self) -> Dict[str, Any]:
        """What the file holds, for the page to offer: its overlays, the images that come with
        the built-in types, and how many overlays are of a type this install does not have."""
        from services.text_logo import list_fonts

        fonts = {str(f.get("id")) for f in list_fonts()}
        known = self._known()
        overlays = []
        for index, otype, entry in known:
            try:
                options = otype.normalize_options(_shareable(entry.get("options")))
            except Exception:
                continue  # an overlay its own type cannot make sense of is left out, like one of a type not known here
            ids = _image_ids(options) if otype.key == CUSTOM_KEY else []
            have = sum(1 for image_id in ids if _has(self._zf, f"images/{otype.key}/{image_id}.png"))
            rows = _rows(options) if otype.key == CUSTOM_KEY else None
            overlays.append({
                "index": index, "type": otype.key, "type_label": otype.label,
                "name": str(entry.get("name") or "").strip()[:60] or otype.label,
                "badges": len(rows) if rows is not None else None,
                "images": have, "missing_images": len(ids) - have,
                "notes": self._notes(otype, entry, options, fonts),
            })
        type_images = []
        for key in self._images:
            otype, found = get_type(str(key)), self._type_images(str(key))
            if otype is not None and found:
                type_images.append({"type": otype.key, "label": otype.label, "noun": otype.images.noun, "count": len(found),
                                    "replaces": sum(1 for image_set, name, _entry in found if otype.images.custom_file(name, image_set).is_file())})
        return {"overlays": overlays, "images": type_images, "unknown": len(self._entries) - len(overlays)}

    def _stored_image(self, image_id: str) -> str:
        """A custom overlay's picture kept here under a new id ('' when the file has none that will do)."""
        if image_id not in self._stored:
            img = self._image(f"images/{CUSTOM_KEY}/{image_id}.png")
            self._stored[image_id] = store_badge(img)[0] if img is not None else ""
        return self._stored[image_id]

    def take(self, picks: Iterable[int], image_types: Iterable[str] = ()) -> Dict[str, Any]:
        """Bring in the overlays picked (their index in the file) and the images of the types
        named. The overlays come back switched off, for the page to add; the images are stored."""
        wanted = set(picks)
        overlays = []
        for index, otype, entry in self._known():
            if index not in wanted:
                continue
            options = _shareable(entry.get("options"))
            try:  # cleaned up before any picture is stored: an overlay that cannot be read stores none
                kept = otype.normalize_options(options)
                placement = normalize_placement(entry.get("placement"), otype.default_placement())
            except Exception:
                continue
            if otype.key == CUSTOM_KEY:
                for row in _rows(kept):
                    row["image"] = self._stored_image(row["image"]) if row.get("image") else ""
            overlays.append({"type": otype.key, "overlay": {
                "name": str(entry.get("name") or "").strip()[:60] or otype.label,
                "enabled": False, "all_libraries": True, "libraries": [], "placement": placement, "options": kept,
            }})
        stored: Dict[str, int] = {}
        for key in dict.fromkeys(str(k) for k in image_types):
            otype = get_type(key)
            for image_set, name, entry_name in self._type_images(key):
                img = self._image(entry_name)
                try:
                    fitted = otype.images.fit(img, name, image_set) if img is not None else None
                except Exception:
                    fitted = None
                if fitted is None:
                    continue
                target = otype.images.custom_file(name, image_set)
                target.parent.mkdir(parents=True, exist_ok=True)
                fitted.save(target, format="PNG")
                stored[key] = stored.get(key, 0) + 1
        return {"overlays": overlays, "images": stored}


def open_import(source: BinaryIO) -> OverlayImport:
    """Open an export for reading. Raises ExportError, in words for the user, for anything else."""
    source.seek(0, 2)
    size = source.tell()
    source.seek(0)
    if size > MAX_FILE_BYTES:
        raise ExportError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        zf = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ExportError("That file is not an overlay export")
    try:
        if len(zf.infolist()) > MAX_ENTRIES:
            raise ExportError("The file holds too many entries")
        raw = _read(zf, MANIFEST, MAX_MANIFEST_BYTES)
        try:
            manifest = json.loads(raw) if raw else None
        except (ValueError, RecursionError):
            manifest = None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(manifest.get("overlays"), list):
            raise ExportError("That file is not an overlay export")
        version = manifest.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ExportError("That file is not an overlay export")
        if version > VERSION:
            raise ExportError("This file was made by a newer Posterflow. Update Posterflow to import it.")
        if len(manifest["overlays"]) > MAX_OVERLAYS:
            raise ExportError(f"The file holds more than {MAX_OVERLAYS} overlays")
        return OverlayImport(zf, manifest)
    except Exception:
        zf.close()
        raise
