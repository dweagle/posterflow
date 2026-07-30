"""Artwork scan + match core — the shared parallel to the poster pipeline.

Scans:
- `scan_artwork_drive_boxes` — the Asset Renamer's source: live-scan the subscribed artwork
  drives into per-item BOXES (logo/background/square slots), so an item's artwork rides the
  same match+placement as its poster.
- `scan_destination_artwork` — the Unmatched Artwork check: scan the organized DESTINATION
  for placed logo/background/squareart, just like poster unmatched scans it for posters.

Plus the one index + matcher (`build_artwork_index` / `match_media_to_index`) that both the
rename and unmatched services share, so there's a single place that decides what matches.
"""

import json
import os
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.logging import LogTags, log_info, log_debug, log_phase
from models.artwork_drive import ArtworkDrive
from models.setting import get_setting
from services.artwork_sync import scan_artwork_files, parse_artwork_title, artwork_group_stem
from util.data.extract import extract_ids, extract_year
from util.data.normalization import normalize_titles
from util.data.construct import build_slots, ITEM_SLOTS, SLOT_LOGO, SLOT_BACKGROUND, SLOT_SQUARE
from util.posters.assets import merge_assets
from util.posters.index import build_search_index, create_new_empty_index
from util.posters.match import match_assets_to_media
from util.posters.scanner import artwork_type_of, artwork_flat_base, ARTWORK_TYPE_TO_NAME

ARTWORK_TYPES = ["logo", "background", "squareart"]

# The slot an artwork file fills == its on-disk name (logo/background/square), so the canonical
# type->name map IS the type->slot map, and its reverse is slot->type.
_SLOT_TO_TYPE = {name: atype for atype, name in ARTWORK_TYPE_TO_NAME.items()}


def drives_in_priority_order(db: Session, require: bool = True) -> List[ArtworkDrive]:
    """Subscribed artwork drives from the `artwork_drive_priority` list, in order.

    Mirrors the poster renamer: only drives in the configured priority list are used, and
    a missing/empty/unsubscribed configuration is an error (not a silent fallback).

    require=False returns [] instead of raising, so the merged Asset Renamer can soft-skip
    the artwork pass when no artwork drives are set up (a poster-only run is unaffected).
    """
    def _fail(msg: str) -> List[ArtworkDrive]:
        if require:
            raise ValueError(msg)
        return []

    setting = get_setting(db, "artwork_drive_priority")
    if not setting or not setting.value:
        return _fail(
            "No artwork drive priority configured. Please configure it in "
            "Asset Manager → Drive Priority → Artwork Drives."
        )
    try:
        drive_ids = json.loads(setting.value).get("drive_ids", []) or []
    except json.JSONDecodeError:
        return _fail(
            "Artwork drive priority configuration is invalid. Please reconfigure it in "
            "Asset Manager → Drive Priority → Artwork Drives."
        )
    if not drive_ids:
        return _fail(
            "Artwork drive priority list is empty. Please add artwork drives in "
            "Asset Manager → Drive Priority → Artwork Drives."
        )

    drives: List[ArtworkDrive] = []
    seen: set[int] = set()
    for drive_id in drive_ids:
        d = db.query(ArtworkDrive).filter(ArtworkDrive.id == drive_id, ArtworkDrive.subscribed == True).first()
        if d and d.id not in seen:
            drives.append(d)
            seen.add(d.id)
    if not drives:
        return _fail(
            "No artwork drives in the priority list are subscribed. Subscribe drives on the "
            "GDrives page and add them in Asset Manager → Drive Priority → Artwork Drives."
        )
    return drives


def _asset_from_name(name: str, **extra: Any) -> Dict[str, Any]:
    """Build a matchable asset dict from a base name (folder/file), plus any extra fields
    (e.g. ``_file_path`` / ``_priority`` for drive scans). Shared by both artwork scanners."""
    tmdb_id, tvdb_id, imdb_id = extract_ids(name)
    title = parse_artwork_title(name)
    return {
        "title": title,
        "year": extract_year(name),
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
        "imdb_id": imdb_id,
        "normalized_title": normalize_titles(title),
        **extra,
    }


def _slot_files(box: Dict[str, Any]) -> List[str]:
    """A box's slot paths as a flat file list, keeping ``files`` consistent with ``slots``."""
    slots = box.get("slots") or {}
    files = [slots.get(s) for s in ITEM_SLOTS]
    files += list((slots.get("seasons") or {}).values())
    return sorted(p for p in files if p)


def scan_artwork_drive_boxes(db: Session) -> List[Dict[str, Any]]:
    """Live-scan the subscribed artwork drives into per-item BOXES with the logo/background/
    square slots filled — the same 'box' shape create_movie/series/collection produce, so an
    item's artwork rides the same match as its poster instead of a separate pass.

    An item's logo.png / background.jpg / square.jpg (which share a stem across the logos/
    backgrounds/squareart subfolders) group into one box. Boxes are then merged ACROSS drives
    through the shared ``merge_assets``, so each slot resolves to the highest-priority drive
    that provides it (an item's logo and square can come from different drives).

    ``type`` is None because a filename can't say whether an item is a movie or a series —
    the matcher treats a type-less asset as compatible with any media type.
    """
    boxes: List[Dict[str, Any]] = []
    drives = drives_in_priority_order(db)
    log_phase(LogTags.SCANNER, f"Scanning {len(drives)} artwork drive(s)")
    source_priority: Dict[str, int] = {}
    for prio, drive in enumerate(drives):
        folder = drive.get_local_path(validate=False)
        source_priority[str(folder)] = prio
        by_stem: Dict[str, Dict[str, Any]] = {}
        n_files = 0
        for s in scan_artwork_files(folder, drive.synced_type_list):
            slot = ARTWORK_TYPE_TO_NAME.get(s["artwork_type"])
            if not slot:
                continue
            n_files += 1
            stem = artwork_group_stem(s["name"])
            entry = by_stem.setdefault(stem, {"name": s["name"], "slots": {}})
            entry["slots"][slot] = s["path"]
        # Alphabetical like the poster scanner's group sort, not file-discovery order.
        for stem in sorted(by_stem, key=str.lower):
            entry = by_stem[stem]
            box = _asset_from_name(entry["name"], _priority=prio, type=None)
            box["slots"] = build_slots(**entry["slots"])
            box["files"] = _slot_files(box)
            boxes.append(box)
            # Per-item line, mirroring the poster scanner's "'Title' (type), N file(s) from [drive]".
            slot_names = ", ".join(sorted(entry["slots"]))
            box_year = f" ({box['year']})" if box.get("year") else ""
            log_debug(LogTags.SCANNER, f"{box['title']}{box_year} ({slot_names}) from [{drive.name}]",
                      title=box["title"], year=box.get("year"), slots=slot_names, source=drive.name)
        log_debug(LogTags.SCANNER, f"Artwork drive '{drive.name}': {n_files} artwork file(s) -> {len(by_stem)} item(s)", drive=drive.name, path=str(folder))

    # Cross-drive merge only means something with more than one drive; a single drive already
    # yields one box per item (grouped by stem above), so skip a full no-op pass over the
    # library. The poster-index merge later still reconciles anything that needs it.
    if len(drives) < 2:
        log_info(LogTags.SCANNER, f"Scanned {len(boxes)} artwork item(s) from 1 artwork drive", items=len(boxes))
        return boxes

    log_phase(LogTags.MERGE, f"Merging {len(boxes)} artwork item(s) across {len(drives)} drives")
    merged: List[Dict[str, Any]] = []
    merge_index = create_new_empty_index()
    for box in boxes:
        merge_assets([box], merged, merge_index, source_priority, log_new=False)
    for box in merged:
        # Slots won per-slot above; re-derive files so the two never disagree (the filename
        # dedup inside merge_assets collapses artwork stems, which differ only by folder).
        box["files"] = _slot_files(box)

    log_info(LogTags.SCANNER, f"Scanned {len(merged)} artwork item(s) across {len(drives)} artwork drive(s)", items=len(merged), drives=len(drives))
    return merged


def assets_from_names(names_by_type: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """Build matchable artwork assets from item names collected per type. The ONE place that
    constructs destination-artwork assets — shared by scan_destination_artwork and the unified
    single-walk unmatched scan, so both produce identical assets."""
    return {t: [_asset_from_name(n) for n in (names_by_type.get(t) or [])] for t in ARTWORK_TYPES}


def scan_destination_artwork(destination_dir: str, asset_folders: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """Return ``{artwork_type: [asset, ...]}`` for artwork already PLACED in the destination.

    One asset per destination item that has that artwork type — nested ``logo.png`` /
    ``background.jpg`` / ``square.jpg`` in the item folder, or flat ``Title-logo.png``.
    Parallel to how poster unmatched scans the destination for posters.
    """
    names: Dict[str, List[str]] = {t: [] for t in ARTWORK_TYPES}
    if not destination_dir or not os.path.isdir(destination_dir):
        return assets_from_names(names)

    if asset_folders:
        for entry in os.scandir(destination_dir):
            if not entry.is_dir() or entry.name == "tmp" or entry.name.startswith("."):
                continue
            present: set[str] = set()
            try:
                for f in os.scandir(entry.path):
                    if f.is_file():
                        atype = artwork_type_of(f.name)
                        if atype:
                            present.add(atype)
            except OSError:
                continue
            for t in present:
                names[t].append(entry.name)
    else:
        for f in os.scandir(destination_dir):
            if not f.is_file():
                continue
            atype = artwork_type_of(f.name)
            base = artwork_flat_base(f.name)
            if atype and base:
                names[atype].append(base)
    return assets_from_names(names)


def build_artwork_index(assets: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Prefix-index one artwork type's scanned assets for fast title lookup."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    for asset in assets:
        build_search_index(index, asset.get("title", ""), asset)
    return index


def sourced_types_by_media(
    db: Session,
    media_dict: Dict[str, List[Dict[str, Any]]],
    boxes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[int, Dict[str, set]]:
    """Per live media item (keyed by ``id(media)``), the artwork the subscribed drives
    currently provide, as ``{artwork_type: {source extensions}}``. Cleanup reconciles placed
    artwork against this to prune what a drive no longer sources. Raises (via the scan) when
    no artwork drives are subscribed — callers treat that as 'prune nothing'.

    ``boxes`` reuses a scan the caller already did (the Asset Renamer's), so a rename+cleanup
    run walks the artwork drives once instead of twice. It MUST be the unfiltered scan — an
    Include-filtered view would drop types from the result, and cleanup only prunes a type
    something still sources, so those types would silently stop being cleaned.
    """
    # Same matcher and raw media as the renamer, so both agree by construction. Do NOT pre-apply
    # media_source_refs: it flips collections to ID-matched while the renamer title-matches them, so cleanup would prune artwork the rename just placed.
    index = build_artwork_index(scan_artwork_drive_boxes(db) if boxes is None else boxes)
    matched = match_assets_to_media(media_dict, index, label="artwork drive sources", report_near_misses=False)
    return sourced_types_from_matched(matched)


def sourced_types_from_matched(matched: Dict[str, List[Dict[str, Any]]]) -> Dict[int, Dict[str, set]]:
    """Derive the per-item sourced artwork from an ALREADY-matched result — the renamer
    hands its own match across so a rename+cleanup run matches the library once, and the two
    can't disagree about who owns what.

    Values map artwork type -> the SOURCE file extensions. Placement copies the source
    extension, so this also tells cleanup which placed filename is the live one — a file of a
    sourced type whose extension no drive produces (e.g. a stale ``background.png`` beside the
    live ``background.jpg``) is prunable instead of being kept forever by a type-only check.
    Unions across boxes when several match the same media."""
    sourced: Dict[int, Dict[str, set]] = {}
    for items in matched.values():
        for item in items:
            slots = (item.get("asset_ref") or {}).get("slots") or {}
            provided: Dict[str, set] = {}
            for s in (SLOT_LOGO, SLOT_BACKGROUND, SLOT_SQUARE):
                path = slots.get(s)
                if path and os.path.isfile(path):
                    provided.setdefault(_SLOT_TO_TYPE[s], set()).add(os.path.splitext(path)[1].lower())
            if provided and item.get("media_ref") is not None:
                merged = sourced.setdefault(id(item["media_ref"]), {})
                for atype, exts in provided.items():
                    merged.setdefault(atype, set()).update(exts)
    return sourced
