"""Single-item "why isn't this matching" report for the unmatched modals.

Gathers the same facts the matcher sees — the live *arr/Plex record, the subscribed
source-drive scan, and a TMDB/TVDB id cross-check — replays ``is_match`` against every
candidate poster, and ranks the likely causes. The result is one JSON payload the UI
renders in a popup and a plain-text file the user can drop into a support thread.
"""
import json
import os
import re
import textwrap
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import requests as http_requests
from sqlalchemy.orm import Session

from core.logging import LogTags, log_error, log_info
from models.manual_media import ManualMediaEntry
from models.setting import get_setting
from services.tvdb import TvdbError, get_tvdb_credentials, _get as tvdb_get
from util.constants import season_pattern
from util.data.extract import extract_ids
from util.data.normalization import normalize_titles
from util.posters.assets import get_assets_files
from util.posters.index import search_matches
from util.posters.match import (
    ID_CONFLICT,
    NO_SHARED_ID,
    YEAR_MISMATCH,
    collection_title_variants,
    is_match,
)
from util.posters.scanner import ARTWORK_TYPE_TO_NAME, _is_asset_folders

# Human labels for artwork types in verdict text.
ARTWORK_LABELS = {"logo": "logo", "background": "background", "squareart": "square art"}


def _box_artwork_types(asset: Dict[str, Any]) -> List[str]:
    """Artwork types an asset box actually carries (slot filled)."""
    slots = asset.get("slots") or {}
    return [atype for atype, slot in ARTWORK_TYPE_TO_NAME.items() if slots.get(slot)]

# Detail cap for the report: the top-ranked candidates get full lines, the rest a count.
MAX_CANDIDATES = 5

# Alternate titles shown per source (popular titles can carry dozens on TMDB).
ALT_TITLE_CAP = 8

# Image extensions the NESTED scanner accepts; other types inside item folders are invisible.
_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

# Tag-looking chunks; those the strict id parser rejects are reported as malformed.
_TAG_LIKE_RE = re.compile(r"\{(?:tmdb|tvdb|imdb)[^}]*\}", re.IGNORECASE)

# Similarity floor for the "a very close title exists" hint (Rocky II vs Rocky 2 ≈ 0.77).
_CLOSE_TITLE_RATIO = 0.75


def _malformed_tags(names: List[str]) -> List[str]:
    """Tag-looking {tmdb/tvdb/imdb-...} chunks the strict parser rejects (typos, stray
    characters, imdb without 'tt') — the poster silently loses its id."""
    bad: List[str] = []
    for name in names:
        for chunk in _TAG_LIKE_RE.findall(name):
            if not any(extract_ids(chunk)):
                bad.append(chunk)
    return list(dict.fromkeys(bad))


def _file_level_ids(files: List[str]) -> Optional[Dict[str, Any]]:
    """Ids found on FILE basenames — for nested drives the scanner only reads the folder
    name, so tags placed on the files are silently ignored."""
    for file in files:
        tmdb, tvdb, imdb = extract_ids(os.path.basename(file))
        if tmdb or tvdb or imdb:
            return {"tmdb_id": tmdb, "tvdb_id": tvdb, "imdb_id": imdb}
    return None


def _unscannable_near_files(source_dirs: List[str], titles: List[str]) -> List[Dict[str, str]]:
    """Files that look like this item's poster but the scanner can never see: hidden files,
    loose files at the root of a folder-per-item drive, or folders holding only unsupported
    image types."""
    wanted = {normalize_titles(t) for t in titles if t}
    wanted.discard("")
    if not wanted:
        return []
    found: List[Dict[str, str]] = []
    for source_dir in source_dirs:
        try:
            nested = _is_asset_folders(source_dir)
            entries = list(os.scandir(source_dir))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file():
                    if normalize_titles(os.path.splitext(entry.name)[0]) not in wanted:
                        continue
                    if entry.name.startswith("."):
                        reason = "hidden file — names starting with a dot are skipped"
                    elif nested:
                        reason = ("it sits outside the item folders — on this drive every poster "
                                  "lives inside its item's folder, and loose files at the drive "
                                  "root are never scanned; move it into the item's folder")
                    else:
                        continue  # flat drives scan every visible file, so it was seen
                    found.append({"file": entry.name, "drive_dir": os.path.basename(source_dir), "reason": reason})
                elif nested and entry.is_dir() and normalize_titles(entry.name) in wanted:
                    names = [f.name for f in os.scandir(entry.path) if f.is_file()]
                    good = [n for n in names
                            if not n.startswith(".") and os.path.splitext(n)[1].lower() in _IMAGE_EXTS]
                    bad = [n for n in names if os.path.splitext(n)[1].lower() not in _IMAGE_EXTS]
                    if not good and bad:
                        found.append({
                            "file": f"{entry.name}/{bad[0]}",
                            "drive_dir": os.path.basename(source_dir),
                            "reason": "the item's folder only holds image types Posterflow doesn't read — only .jpg, .jpeg and .png are scanned",
                        })
            except OSError:
                continue
    return found


def _close_titles(pool: List[Tuple[Dict[str, Any], str]], media_title: str) -> List[Dict[str, Any]]:
    """Assets whose normalized titles are close-but-not-equal (Rocky II vs Rocky 2) — no
    matching criterion covers these, so surface them as a hint."""
    target = normalize_titles(media_title or "")
    if not target:
        return []
    scored = []
    for asset, _found_by in pool:
        candidate = str(asset.get("normalized_title") or "")
        if not candidate or candidate == target:
            continue
        ratio = SequenceMatcher(None, target, candidate).ratio()
        if ratio >= _CLOSE_TITLE_RATIO:
            scored.append((ratio, asset))
    scored.sort(key=lambda pair: -pair[0])
    return [
        {"title": asset.get("title"), "year": asset.get("year"), "similarity": round(ratio, 2),
         "files": [os.path.basename(f) for f in (asset.get("files") or [])[:2]]}
        for ratio, asset in scored[:3]
    ]


def _dedupe_titles(titles: List[Any], exclude: str = "") -> List[str]:
    """Clean alternate-title lists: strings only, order-preserving dedupe (loose-normalized),
    the primary title excluded."""
    seen = {normalize_titles(exclude)} if exclude else set()
    out: List[str] = []
    for title in titles:
        name = str(title or "").strip()
        key = normalize_titles(name)
        if name and key and key not in seen:
            seen.add(key)
            out.append(name)
    return out

# Rejection-reason ranking for candidate ordering (matched candidates always sort first).
_REASON_RANK = {ID_CONFLICT: 0, NO_SHARED_ID: 1, YEAR_MISMATCH: 2, "": 3}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _id_tags(d: Dict[str, Any]) -> str:
    """Render tmdb/tvdb/imdb ids as filename-style tags, or '(none)'."""
    tags = [f"{{{k}-{d[f'{k}_id']}}}" for k in ("tmdb", "tvdb", "imdb") if d.get(f"{k}_id")]
    return " ".join(tags) if tags else "(none)"


def _norm_title(title: Any) -> str:
    return normalize_titles(str(title or ""))


def _record_matches_item(record: Dict[str, Any], item: Dict[str, Any]) -> bool:
    """Same semantics as the ignore list: any shared id decides; title+year otherwise."""
    ids_compared = False
    for key in ("tmdb_id", "tvdb_id", "imdb_id"):
        rec_id = record.get(key) or (record.get("tmdb_id_ref") if key == "tmdb_id" else None)
        item_id = item.get(key)
        if rec_id and item_id:
            ids_compared = True
            if str(rec_id) == str(item_id):
                return True
    if ids_compared:
        return False
    if _norm_title(record.get("title")) != _norm_title(item.get("title")):
        return False
    rec_year, item_year = record.get("year"), item.get("year")
    return not rec_year or not item_year or rec_year == item_year


def _fetch_library_records(db: Session, item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The live *arr/Plex records for this one item, fetched the way the unmatched job
    fetches them (full instance pull, then filtered) so the report sees identical data."""
    from services.poster_renamer import PosterRenameService

    media_type = item["media_type"]
    service = PosterRenameService(db)
    media_dict: Dict[str, List[Dict[str, Any]]] = {"movies": [], "series": [], "collections": []}

    if media_type == "collections":
        instances_setting = get_setting(db, "plex_instances")
        if instances_setting and instances_setting.value:
            try:
                for instance in json.loads(instances_setting.value):
                    service._fetch_media_server_collections(
                        instance, media_dict, LogTags.UNMATCHED,
                    )
            except Exception as exc:
                log_error(LogTags.UNMATCHED, f"Match report: media server fetch failed: {exc}", error=str(exc))
    else:
        from util.arr.client import create_arr_client

        setting_key = "radarr_instances" if media_type == "movies" else "sonarr_instances"
        instance_type = "radarr" if media_type == "movies" else "sonarr"
        instances_setting = get_setting(db, setting_key)
        if instances_setting and instances_setting.value:
            try:
                instances = json.loads(instances_setting.value)
            except Exception:
                instances = []
            for instance in instances:
                try:
                    client = create_arr_client(instance["url"], instance["api_key"], instance_type, None)
                    if client and client.connect_status:
                        for record in client.get_parsed_media(include_unmonitored=True) or []:
                            record["instance"] = instance.get("name", instance_type.title())
                            media_dict[media_type].append(record)
                except Exception as exc:
                    log_error(LogTags.UNMATCHED, f"Match report: {instance_type} fetch failed: {exc}", error=str(exc))

        # Arr-less (or hybrid) installs: media-server libraries are the item source, so
        # their records count as library records too. Appended after the arrs so an arr
        # record stays the primary one when both exist.
        from util.poster_settings import media_server_media_source_enabled

        if media_server_media_source_enabled(db):
            # Mirror the unmatched job's library scope so the report sees the same data
            selected_libraries = None
            selection_setting = get_setting(db, "unmatched_assets_libraries")
            if selection_setting and selection_setting.value:
                try:
                    selected_libraries = json.loads(selection_setting.value)
                except Exception:
                    selected_libraries = None
            instances_setting = get_setting(db, "plex_instances")
            if instances_setting and instances_setting.value:
                try:
                    for instance in json.loads(instances_setting.value):
                        service._fetch_media_server_media(
                            instance, media_dict, LogTags.UNMATCHED,
                            selected_libraries=selected_libraries,
                        )
                except Exception as exc:
                    log_error(LogTags.UNMATCHED, f"Match report: media server media fetch failed: {exc}", error=str(exc))

    # Merge duplicates the same way the pipeline does before diagnosing
    if media_type == "movies":
        media_dict["movies"] = service._merge_duplicate_movies(media_dict["movies"], LogTags.UNMATCHED)
    elif media_type == "series":
        media_dict["series"] = service._merge_duplicate_series(media_dict["series"], LogTags.UNMATCHED)
    else:
        media_dict["collections"] = service._merge_duplicate_collections(media_dict["collections"], LogTags.UNMATCHED)

    return [r for r in media_dict[media_type] if _record_matches_item(r, item)]


def _manual_entry_for(db: Session, item: Dict[str, Any]) -> Optional[ManualMediaEntry]:
    wanted = "movie" if item["media_type"] == "movies" else "series"
    if item["media_type"] == "collections":
        return None
    for entry in db.query(ManualMediaEntry).filter(ManualMediaEntry.media_type == wanted).all():
        record = {"title": entry.title, "year": entry.year, "tmdb_id": entry.tmdb_id,
                  "tvdb_id": entry.tvdb_id, "imdb_id": entry.imdb_id}
        if _record_matches_item(record, item):
            return entry
    return None


def _synth_media(item: Dict[str, Any]) -> Dict[str, Any]:
    """A matcher-shaped media dict from the unmatched row alone, for when no live record is
    found. Series/collections keep tmdb on the ref key exactly like the real records do."""
    media_type = item["media_type"]
    media: Dict[str, Any] = {
        "type": media_type,
        "title": item.get("title") or "",
        "year": item.get("year"),
        "normalized_title": _norm_title(item.get("title")),
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "imdb_id": item.get("imdb_id"),
    }
    if media_type == "movies":
        media["tmdb_id"] = item.get("tmdb_id")
    else:
        media["tmdb_id_ref"] = item.get("tmdb_id")
    if media_type == "series":
        media["tvdb_id"] = item.get("tvdb_id")
    if media_type == "collections":
        # Real Plex collection records carry "X"/"X Collection" variants; mirror that.
        media["alternate_titles"] = [v for v in collection_title_variants(media["title"]) if v != media["title"]]
        media["normalized_alternate_titles"] = [normalize_titles(t) for t in media["alternate_titles"]]
    return media


def _scan_source_drives(db: Session) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    """Scan the renamer's subscribed priority drives. Returns (drives_info, prefix_index,
    assets, error). ``drives_info`` rows carry name/style/last_synced/missing."""
    from services.poster_renamer import subscribed_priority_drives

    try:
        drives = subscribed_priority_drives(db)
    except ValueError as exc:
        return [], None, [], str(exc)

    drives_info: List[Dict[str, Any]] = []
    source_dirs: List[str] = []
    for drive in drives:
        path = drive.get_local_path(validate=False)
        missing = not path.is_dir()
        drives_info.append({
            "name": drive.display_name or drive.name,
            "style_type": drive.style_type,
            "local_path": str(path),
            "last_synced": drive.last_synced.strftime("%Y-%m-%d %H:%M") if drive.last_synced else None,
            "missing": missing,
        })
        if not missing:
            source_dirs.append(str(path))

    if not source_dirs:
        return drives_info, None, [], "No subscribed poster drive folders exist locally — sync the drives first."

    assets, prefix_index = get_assets_files(source_dirs)
    return drives_info, prefix_index, assets or [], None


def _scan_artwork_drives(db: Session) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    """Artwork twin of _scan_source_drives: the subscribed priority ARTWORK drives, scanned
    into slot boxes through the renamer's own scanner and indexed for the same matcher."""
    from services.artwork_scan import build_artwork_index, drives_in_priority_order, scan_artwork_drive_boxes

    try:
        drives = drives_in_priority_order(db)
    except ValueError as exc:
        return [], None, [], str(exc)

    drives_info: List[Dict[str, Any]] = []
    for drive in drives:
        path = drive.get_local_path(validate=False)
        drives_info.append({
            "name": drive.display_name or drive.name,
            "style_type": "ART",
            "local_path": str(path),
            "last_synced": drive.last_synced.strftime("%Y-%m-%d %H:%M") if drive.last_synced else None,
            "missing": not path.is_dir(),
        })

    if all(d["missing"] for d in drives_info):
        return drives_info, None, [], "No subscribed artwork drive folders exist locally — sync the drives first."

    try:
        boxes = scan_artwork_drive_boxes(db)
    except ValueError as exc:
        return drives_info, None, [], str(exc)
    return drives_info, build_artwork_index(boxes), boxes, None


def _drive_for_file(path: str, drives_info: List[Dict[str, Any]]) -> Optional[str]:
    """Attribute a file to a drive by longest local-path prefix."""
    best: Tuple[int, Optional[str]] = (0, None)
    for info in drives_info:
        root = info["local_path"].rstrip("/") + "/"
        if path.startswith(root) and len(root) > best[0]:
            best = (len(root), info["name"])
    return best[1]


def _collect_candidates(
    media: Dict[str, Any],
    prefix_index: Dict[str, Any],
    media_type: str,
) -> Tuple[List[Tuple[Dict[str, Any], str]], int]:
    """Candidate posters the matcher would consider, deduped: ((asset, found_by) pairs,
    id-pool size). The id-pool size matters because the live pipeline skips the title
    search entirely whenever the id lookup returned candidates."""
    seen: set = set()
    out: List[Tuple[Dict[str, Any], str]] = []

    tmdb_id = media.get("tmdb_id")
    tvdb_id = media.get("tvdb_id")
    id_pool = 0
    if tmdb_id or tvdb_id:
        for asset in search_matches(prefix_index, media.get("title", ""), tmdb_id=tmdb_id, tvdb_id=tvdb_id):
            if id(asset) not in seen:
                seen.add(id(asset))
                out.append((asset, "id"))
                id_pool += 1

    base_title = media.get("title") or ""
    if media_type == "collections":
        titles = collection_title_variants(base_title) + list(media.get("alternate_titles") or [])
    else:
        titles = [base_title] + list(media.get("alternate_titles") or [])
    for title in titles:
        for asset in search_matches(prefix_index, title):
            if id(asset) not in seen:
                seen.add(id(asset))
                out.append((asset, "title"))
    return out, id_pool


def _nonpriority_matches(
    db: Session,
    media: Dict[str, Any],
    media_type: str,
    priority_paths: set,
) -> List[Dict[str, Any]]:
    """Matching posters on SUBSCRIBED drives that are not in the poster priority list —
    the renamer never scans those, which reads as 'no poster found' without this check."""
    from models.drive import Drive

    extra_dirs: Dict[str, str] = {}
    for drive in db.query(Drive).filter(Drive.subscribed == True).all():  # noqa: E712
        path = str(drive.get_local_path(validate=False))
        if path in priority_paths:
            continue
        if os.path.isdir(path):
            extra_dirs[path] = drive.display_name or drive.name
    if not extra_dirs:
        return []

    try:
        _assets, index = get_assets_files(list(extra_dirs.keys()))
    except Exception:
        return []
    if not index:
        return []

    hits: List[Dict[str, Any]] = []
    pairs, _ = _collect_candidates(media, index, media_type)
    for asset, _found_by in pairs:
        matched, reason = is_match(dict(asset), media)
        if not matched:
            continue
        files = asset.get("files") or []
        drive_name = next(
            (name for path, name in extra_dirs.items() if files and files[0].startswith(path.rstrip("/") + "/")),
            None,
        )
        hits.append({"title": asset.get("title"), "year": asset.get("year"), "drive": drive_name,
                     "reason": reason, "files": [os.path.basename(f) for f in files[:4]]})
        if len(hits) >= 3:
            break
    return hits


def _newest_mtime(files: List[str]) -> Optional[str]:
    stamps = []
    for f in files:
        try:
            stamps.append(os.path.getmtime(f))
        except OSError:
            continue
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _tmdb_reference(db: Session, item: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the item on TMDB by its strongest id and return TMDB's view of the ids."""
    key_setting = get_setting(db, "tmdb_api_key")
    api_key = str(key_setting.value or "").strip() if key_setting else ""
    if not api_key:
        return {"skipped": "no TMDB API key configured"}
    if not (item.get("tmdb_id") or item.get("tvdb_id") or item.get("imdb_id")):
        return {"skipped": "no ids on the library record to check"}

    media_type = item["media_type"]
    entity = {"movies": "movie", "series": "tv", "collections": "collection"}[media_type]
    tmdb_id = item.get("tmdb_id")

    try:
        # Resolve a tmdb id via /find when we only hold imdb/tvdb, then fetch the detail
        # ONCE with external ids and alternative titles appended (one round-trip).
        if not tmdb_id and entity != "collection" and (item.get("imdb_id") or item.get("tvdb_id")):
            source, ext_id = (
                ("imdb_id", item["imdb_id"]) if item.get("imdb_id") else ("tvdb_id", str(item["tvdb_id"]))
            )
            resp = http_requests.get(
                f"https://api.themoviedb.org/3/find/{ext_id}",
                params={"api_key": api_key, "external_source": source}, timeout=12,
            )
            resp.raise_for_status()
            found = resp.json() or {}
            hits = found.get("tv_results" if entity == "tv" else "movie_results") or []
            if hits and isinstance(hits[0], dict) and isinstance(hits[0].get("id"), int):
                tmdb_id = hits[0]["id"]
        if not tmdb_id:
            return {"error": "TMDB could not resolve this item from its ids"}

        params: Dict[str, Any] = {"api_key": api_key}
        if entity != "collection":
            params["append_to_response"] = "external_ids,alternative_titles"
        resp = http_requests.get(
            f"https://api.themoviedb.org/3/{entity}/{tmdb_id}", params=params, timeout=12,
        )
        if resp.status_code == 404:
            return {"error": f"TMDB has no {entity} with id {tmdb_id} (deleted or wrong namespace)"}
        resp.raise_for_status()
        detail = resp.json()
    except http_requests.RequestException as exc:
        return {"error": f"TMDB lookup failed: {exc}"}

    if not isinstance(detail, dict) or not isinstance(detail.get("id"), int):
        return {"error": "TMDB could not resolve this item from its ids"}

    resolved: Dict[str, Any] = {
        "tmdb_id": detail["id"],
        "title": str(detail.get("title") or detail.get("name") or "").strip(),
        "tvdb_id": None,
        "imdb_id": None,
    }
    release = str(detail.get("release_date") or detail.get("first_air_date") or "")
    resolved["year"] = int(release[:4]) if len(release) >= 4 and release[:4].isdigit() else None

    ext_data = detail.get("external_ids") or {}
    resolved["imdb_id"] = ext_data.get("imdb_id") if isinstance(ext_data.get("imdb_id"), str) else None
    resolved["tvdb_id"] = ext_data.get("tvdb_id") if isinstance(ext_data.get("tvdb_id"), int) else None

    # Movies use "titles", TV uses "results"; either way, names only, primary excluded.
    # TMDB keeps original_title OFF the alternative-titles list, so prepend it here —
    # for a foreign item that's the name a poster is most likely filed under, and
    # leading the list keeps it safe from the display cap.
    alt_container = detail.get("alternative_titles") or {}
    original_title = str(detail.get("original_title") or detail.get("original_name") or "").strip()
    names = _dedupe_titles(
        [original_title]
        + [t.get("title") for t in (alt_container.get("titles") or alt_container.get("results") or [])
           if isinstance(t, dict)],
        exclude=resolved["title"],
    )
    resolved["alternate_titles"] = names[:ALT_TITLE_CAP]
    resolved["alternate_titles_total"] = len(names)
    return resolved


def _tvdb_reference(db: Session, item: Dict[str, Any]) -> Dict[str, Any]:
    """TVDB's view of a series' identity (name/year + imdb/tmdb remote ids)."""
    if item["media_type"] != "series":
        return {"skipped": "TVDB check applies to series"}
    if not item.get("tvdb_id"):
        return {"skipped": "no tvdb id on the library record"}
    api_key, pin = get_tvdb_credentials(db)
    if not api_key:
        return {"skipped": "no TVDB API key configured"}
    try:
        data = tvdb_get(f"/series/{item['tvdb_id']}/extended", api_key, pin,
                        params={"short": "true"}, what="match report series")
    except TvdbError as exc:
        return {"error": str(exc)}
    if not isinstance(data, dict):
        return {"error": f"TVDB has no series with id {item['tvdb_id']} (deleted or merged entry)"}

    resolved: Dict[str, Any] = {
        "tvdb_id": item["tvdb_id"],
        "title": str(data.get("name") or "").strip(),
        "year": int(data["year"]) if str(data.get("year") or "").isdigit() else None,
        "tmdb_id": None,
        "imdb_id": None,
    }
    for remote in data.get("remoteIds") or []:
        source = str((remote or {}).get("sourceName") or "").lower()
        rid = str((remote or {}).get("id") or "").strip()
        if "imdb" in source and rid.startswith("tt"):
            resolved["imdb_id"] = rid
        elif "themoviedb" in source and rid.isdigit():
            resolved["tmdb_id"] = int(rid)

    names = _dedupe_titles(
        [a.get("name") for a in (data.get("aliases") or []) if isinstance(a, dict)],
        exclude=resolved["title"],
    )
    resolved["alternate_titles"] = names[:ALT_TITLE_CAP]
    resolved["alternate_titles_total"] = len(names)
    return resolved


def _plex_instances(db: Session) -> List[Dict[str, str]]:
    """Configured Plex instances, supporting both the array and legacy url/token formats."""
    instances_setting = get_setting(db, "plex_instances")
    if instances_setting and instances_setting.value:
        try:
            parsed = json.loads(instances_setting.value)
            return [i for i in parsed if isinstance(i, dict) and i.get("url") and i.get("api_key")]
        except Exception:
            return []
    url_setting = get_setting(db, "plex_url")
    token_setting = get_setting(db, "plex_token")
    if url_setting and token_setting and url_setting.value and token_setting.value:
        return [{"url": url_setting.value, "api_key": token_setting.value, "name": "Plex"}]
    return []


def _plex_guid_ids(item: Any) -> Dict[str, Any]:
    """tmdb/tvdb/imdb ids a Plex item's metadata agent mapped it to (from the parsed wrapper)."""
    ids: Dict[str, Any] = {"tmdb_id": None, "tvdb_id": None, "imdb_id": None}
    provider_ids = item.provider_ids or {}
    for source in ("tmdb", "tvdb"):
        raw = str(provider_ids.get(source) or "")
        if raw.isdigit():
            ids[f"{source}_id"] = int(raw)
    imdb_raw = str(provider_ids.get("imdb") or "")
    if imdb_raw.startswith("tt"):
        ids["imdb_id"] = imdb_raw
    return ids


def _plex_reference(db: Session, item: Dict[str, Any], ids: Dict[str, Any]) -> Dict[str, Any]:
    """Plex's view of the item: presence + what ids its metadata agent mapped it to.

    Targeted single-item lookup (guid search, title fallback) across every section of the
    right type, mirroring plex_upload's scoped index — never a full library walk.
    """
    instances = _plex_instances(db)
    if not instances:
        return {"skipped": "no media server instance configured"}

    media_type = item["media_type"]
    section_types = {"movies": {"movie"}, "series": {"show"}, "collections": {"movie", "show"}}[media_type]
    provider_ids: Dict[str, str] = {}
    if ids.get("tmdb_id"):
        provider_ids["tmdb"] = str(ids["tmdb_id"])
    if ids.get("tvdb_id"):
        provider_ids["tvdb"] = str(ids["tvdb_id"])
    if ids.get("imdb_id"):
        provider_ids["imdb"] = str(ids["imdb_id"])

    wanted_titles = {
        _norm_title(t)
        for t in ([item.get("title")] + (collection_title_variants(item.get("title") or "") if media_type == "collections" else []))
        if t
    }
    errors: List[str] = []
    servers: List[Dict[str, Any]] = []
    from util.media_server.client import create_media_server_client
    from util.media_server.instances import instance_label, instance_type

    for instance in instances:
        name = instance_label(instance)
        server_type = instance_type(instance)
        try:
            # raise_on_error so the per-instance error text lands in the report
            client = create_media_server_client(instance, timeout=15, raise_on_error=True)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            servers.append({"instance": name, "type": server_type, "error": str(exc)})
            continue
        try:
            libraries = [l for l in client.get_libraries() if l.type in section_types]
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            servers.append({"instance": name, "type": server_type, "error": str(exc)})
            continue
        hit = None
        hit_library = None
        for library in libraries:
            if media_type == "collections":
                try:
                    for collection in client.get_collections(library.key):
                        if _norm_title(collection.title or "") in wanted_titles:
                            hit = collection
                            break
                except Exception:
                    pass
            else:
                matches = client.find_by_provider_ids(provider_ids, library.type, library_keys=[library.key])
                if matches:
                    hit = matches[0]
                if hit is None and item.get("title"):
                    try:
                        for result in client.find_by_title(item["title"], library.type, library_keys=[library.key]):
                            if _norm_title(result.title or "") in wanted_titles and (
                                not item.get("year") or result.year in (None, item["year"])
                            ):
                                hit = result
                                break
                    except Exception:
                        pass
            if hit is not None:
                hit_library = library.title
                break
        if hit is not None:
            servers.append({
                "instance": name,
                "type": server_type,
                "title": hit.title or None,
                "year": hit.year,
                "library": hit_library,
                **_plex_guid_ids(hit),
            })
        else:
            servers.append({"instance": name, "type": server_type, "missing": True})

    # Top-level keys keep the single-hit shape (first server that has the item) for
    # existing consumers; "servers" carries every instance's outcome for display
    found = [s for s in servers if not s.get("missing") and not s.get("error")]
    if found:
        return {**found[0], "servers": servers}
    if errors:
        return {"error": f"Media server lookup failed: {errors[0]}", "servers": servers}
    return {"missing": "not found in any media server library", "servers": servers}


def _is_on_ignore_list(db: Session, item: Dict[str, Any]) -> bool:
    from services.unmatched_assets import UnmatchedAssetsService

    service = UnmatchedAssetsService(db)
    bucket = item["media_type"]
    probe = {"title": item.get("title"), "year": item.get("year"), "tmdb_id": item.get("tmdb_id"),
             "tvdb_id": item.get("tvdb_id"), "imdb_id": item.get("imdb_id")}
    return any(
        service._ignore_entry_matches(entry, probe)
        for entry in service._ignore_entries_by_bucket(service.get_ignore_items()).get(bucket, [])
    )


def _reference_conflicts(record_ids: Dict[str, Any], reference: Dict[str, Any]) -> List[str]:
    """Id types where the library record disagrees with a resolved TMDB/TVDB reference."""
    conflicts = []
    for key in ("tmdb_id", "tvdb_id", "imdb_id"):
        rec, ref = record_ids.get(key), reference.get(key)
        if rec and ref and str(rec) != str(ref):
            conflicts.append(key)
    return conflicts


def _app_version() -> str:
    # main is already imported in the running app; guarded so service tests don't need it.
    try:
        from main import get_app_version
        return get_app_version()
    except Exception:
        return "unknown"


def build_match_report(db: Session, item: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the full single-item report. ``item`` is the unmatched row: media_type
    ('movies'|'series'|'collections'), title, year, tmdb_id, tvdb_id, imdb_id,
    missing_seasons (optional list)."""
    media_type = item["media_type"]
    log_info(LogTags.UNMATCHED, f"Building match report for {item.get('title')} ({item.get('year')})",
             media_type=media_type, title=item.get("title"))

    report: Dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "app_version": _app_version(),
        "item": {
            "media_type": media_type,
            "title": item.get("title"),
            "year": item.get("year"),
            "tmdb_id": item.get("tmdb_id"),
            "tvdb_id": item.get("tvdb_id"),
            "imdb_id": item.get("imdb_id"),
            "missing_seasons": item.get("missing_seasons") or [],
            "missing_main": bool(item.get("missing_main")),
            # None = posters; 'logo'|'background'|'squareart' = artwork report.
            "artwork_type": item.get("artwork_type") or None,
        },
    }
    artwork_type = report["item"]["artwork_type"]

    # --- Library side -----------------------------------------------------
    records = _fetch_library_records(db, item)
    manual = None if records else _manual_entry_for(db, item)
    library_records = []
    for record in records:
        # A Plex collection's "folder" is just its sanitized title (the destination folder
        # the renamer would create), not an *arr path — showing it as one only misleads.
        folder = (record.get("folder") or "") if media_type != "collections" else ""
        library_records.append({
            "instance": record.get("instance"),
            "title": record.get("title"),
            "year": record.get("year"),
            "folder": os.path.basename(folder) if folder else None,
            "folder_has_year": bool(folder) and bool(re.search(r"\(\d{4}\)", os.path.basename(folder))),
            "tmdb_id": record.get("tmdb_id") or record.get("tmdb_id_ref"),
            "tvdb_id": record.get("tvdb_id"),
            "imdb_id": record.get("imdb_id"),
            "monitored": record.get("monitored"),
            # Radarr: tba/announced/incinemas/released — Sonarr: upcoming/continuing/ended.
            "status": record.get("status") or None,
            "available": (
                bool(record.get("has_file")) if media_type == "movies"
                else bool(record.get("has_episodes")) if media_type == "series"
                else None
            ),
            "alternate_titles": (record.get("alternate_titles") or [])[:8],
            "seasons_with_episodes": [s.get("season_number") for s in (record.get("seasons") or [])],
        })
    # Detection-eligibility filters beyond the per-item ignore list: an item excluded by a
    # root-folder or unmonitored filter can only be a stale row from an older run.
    excluded_by: List[str] = []
    if records:
        from services.unmatched_assets import UnmatchedAssetsService

        service = UnmatchedAssetsService(db)
        ignore_roots = service._get_list_setting("unmatched_ignore_root_folders")
        if service._matches_ignored_root(records[0].get("root_folder"), ignore_roots):
            excluded_by.append(f"root folder filter ({records[0].get('root_folder')})")
        if service._get_bool_setting("unmatched_ignore_unmonitored", default=False) and not records[0].get("monitored"):
            excluded_by.append("unmonitored filter (item is unmonitored)")

    report["library"] = {
        "found": bool(records),
        "records": library_records,
        "manual_entry": bool(manual),
        "on_ignore_list": _is_on_ignore_list(db, item),
        "excluded_by": excluded_by,
    }

    # The matcher runs against the merged live record when we have one, else a dict
    # synthesized from the row (same ids/title the unmatched stats carried).
    media = dict(records[0]) if records else _synth_media(item)

    # The strongest ids we hold: the live record's, falling back to the row's. The
    # reference cross-check and the verdicts both compare against these.
    effective_ids = {
        key: (library_records[0].get(key) if library_records else None) or item.get(key)
        for key in ("tmdb_id", "tvdb_id", "imdb_id")
    }
    report["library"]["effective_ids"] = effective_ids
    # Where those ids came from: the instance name (Sonarr/Radarr/Plex as configured), or
    # the cached unmatched row when no live record was found.
    report["library"]["ids_source"] = (
        library_records[0].get("instance") if library_records else "unmatched cache"
    ) or "library"

    # --- Reference cross-check -------------------------------------------
    ref_probe = {"media_type": media_type, **effective_ids}
    report["reference"] = {
        "tmdb": _tmdb_reference(db, ref_probe),
        "tvdb": _tvdb_reference(db, ref_probe),
        "plex": _plex_reference(db, {**item, "media_type": media_type}, effective_ids),
    }

    # --- Source drives + candidate replay --------------------------------
    if artwork_type:
        drives_info, prefix_index, assets, scan_error = _scan_artwork_drives(db)
    else:
        drives_info, prefix_index, assets, scan_error = _scan_source_drives(db)
    report["drives"] = {
        "scanned": drives_info,
        "total_assets": len(assets),
        "error": scan_error,
    }

    # Replay the matcher. Only matched candidates and true near-misses (a typed rejection
    # reason) are reported — prefix-search brushes like "Our Changing Planet" for
    # "Our Man..." carry no signal and would drown the list.
    candidates_out: List[Dict[str, Any]] = []
    considered = 0
    omitted = 0
    id_pool = 0
    pairs: List[Tuple[Dict[str, Any], str]] = []
    any_matched = False
    if prefix_index is not None:
        pairs, id_pool = _collect_candidates(media, prefix_index, media_type)
        considered = len(pairs)
        evaluated = [
            (asset, found_by, matched, reason)
            for asset, found_by in pairs
            for matched, reason in [is_match(dict(asset), media)]
            if matched or reason
        ]
        evaluated.sort(key=lambda e: (not e[2], _REASON_RANK.get(e[3], 4)))
        any_matched = any(matched for _, _, matched, _ in evaluated)
        for asset, found_by, matched, reason in evaluated[:MAX_CANDIDATES]:
            files = asset.get("files") or []
            basenames = [os.path.basename(f) for f in files]
            asset_ids = {"tmdb_id": asset.get("tmdb_id"), "tvdb_id": asset.get("tvdb_id"),
                         "imdb_id": asset.get("imdb_id")}
            candidates_out.append({
                "title": asset.get("title"),
                "year": asset.get("year"),
                "type": asset.get("type"),
                **asset_ids,
                "drive": _drive_for_file(files[0], drives_info) if files else None,
                "files": basenames[:6],
                "season_numbers": asset.get("season_numbers") or [],
                # A file without a season marker is the main poster slot.
                "has_main": any(not season_pattern.search(name) for name in basenames),
                # Tag-looking text the id parser rejected (typos etc.).
                "malformed_tags": _malformed_tags(basenames),
                # Ids sitting on the FILES of an id-less nested asset (wrong level).
                "file_ids": None if any(asset_ids.values()) else _file_level_ids(files),
                "found_by": found_by,
                "matched": matched,
                "reason": reason,
                "newest_file": _newest_mtime(files),
                # Which artwork types the box carries (artwork reports only).
                "artwork_types": _box_artwork_types(asset) if artwork_type else None,
            })
        omitted = max(0, len(evaluated) - MAX_CANDIDATES)
    report["candidates"] = {
        "considered": considered,
        "shown": len(candidates_out),
        "omitted": omitted,
        "id_pool": id_pool,
        "items": candidates_out,
    }

    # --- Only-when-nothing-matched extras: things the scan cannot see ------
    # The raw-file and non-priority sweeps understand the POSTER drive layout only, so
    # artwork reports skip them; the close-title check is layout-agnostic.
    titles_to_probe = [media.get("title") or ""] + list(media.get("alternate_titles") or [])
    source_dirs = [d["local_path"] for d in drives_info if not d.get("missing")]
    report["unscannable"] = (
        _unscannable_near_files(source_dirs, titles_to_probe)
        if not any_matched and not artwork_type else []
    )
    priority_paths = {d["local_path"] for d in drives_info}
    report["nonpriority_hits"] = (
        _nonpriority_matches(db, media, media_type, priority_paths)
        if not any_matched and not artwork_type else []
    )
    report["close_titles"] = (
        _close_titles(pairs, media.get("title") or "")
        if not any_matched and not candidates_out else []
    )

    # Collections deliberately match by TITLE only (tmdb sits on the ref key, off the
    # matcher) — an id-tagged collection poster with a different name is a silent miss.
    # Look the id up in the index directly: a differently-named poster never enters the
    # title-searched pool.
    # (Artwork boxes are type-less and DO id-match collections via the ref, so the note
    # only applies to poster reports.)
    report["collection_id_note"] = None
    if (media_type == "collections" and not artwork_type and not any_matched
            and effective_ids.get("tmdb_id") and prefix_index is not None):
        agreeing = next(
            (asset for asset in prefix_index.get(f"tmdb:{effective_ids['tmdb_id']}", [])
             if asset.get("tmdb_id") == effective_ids["tmdb_id"]),
            None,
        )
        if agreeing is not None:
            report["collection_id_note"] = {
                "title": agreeing.get("title"),
                "files": [os.path.basename(f) for f in (agreeing.get("files") or [])[:3]],
            }

    report["verdicts"] = _build_verdicts(report)
    return report


def _build_verdicts(report: Dict[str, Any]) -> List[Dict[str, str]]:
    """Ranked plain-language conclusions; the first entry is the headline."""
    verdicts: List[Dict[str, str]] = []
    item = report["item"]
    library = report["library"]
    candidates = report["candidates"]["items"]
    drives = report["drives"]

    def add(level: str, code: str, message: str) -> None:
        verdicts.append({"level": level, "code": code, "message": message})

    if library["on_ignore_list"]:
        add("info", "ignored", "This item is on the unmatched ignore list — it should not appear as unmatched after the next detection run.")

    if not library["found"] and not library["manual_entry"]:
        add("problem", "not_in_sources",
            "No Sonarr/Radarr/media server record was found for this item in the configured instances — "
            "the unmatched entry may be stale; re-run unmatched detection.")

    # Library ids vs the neutral TMDB/TVDB references.
    record_ids = library.get("effective_ids") or {k: item.get(k) for k in ("tmdb_id", "tvdb_id", "imdb_id")}
    for source in ("tvdb", "tmdb", "plex"):
        reference = report["reference"].get(source) or {}
        if source == "plex" and reference.get("servers"):
            # One check per configured media server
            for entry in reference["servers"]:
                if entry.get("error"):
                    add("problem", "plex_unresolved", f"{entry.get('instance')}: {entry['error']}")
                    continue
                if entry.get("missing"):
                    continue
                for key in _reference_conflicts(record_ids, entry):
                    add("problem", "library_id_conflict",
                        f"The library record's {key.replace('_id', '').upper()} ({record_ids[key]}) disagrees with "
                        f"{entry.get('instance')}'s metadata ({entry[key]}) — one side is stale or remapped. "
                        "Refresh the item's metadata, or remove and re-add it.")
            continue
        if reference.get("error"):
            add("problem", f"{source}_unresolved", f"{source.upper()}: {reference['error']}")
            continue
        if reference.get("skipped") or reference.get("missing"):
            continue
        for key in _reference_conflicts(record_ids, reference):
            origin = "the media server's metadata agent" if source == "plex" else f"{source.upper()}'s mapping"
            add("problem", "library_id_conflict",
                f"The library record's {key.replace('_id', '').upper()} ({record_ids[key]}) disagrees with "
                f"{origin} ({reference[key]}) — one side is stale or remapped. "
                "Refresh the item's metadata, or remove and re-add it.")

    if drives.get("error"):
        add("problem", "drive_scan_failed", drives["error"])

    missing_drives = [d["name"] for d in drives.get("scanned", []) if d.get("missing")]
    if missing_drives:
        add("problem", "drive_folder_missing",
            f"Drive folder(s) missing locally (not synced?): {', '.join(missing_drives)}")

    # Eligibility filters (beyond the per-item ignore list).
    for filter_name in library.get("excluded_by") or []:
        add("info", "excluded_by_filter",
            f"This item is excluded from unmatched detection by the {filter_name} — "
            "the row you clicked is from an older run; re-run unmatched detection.")

    # Multiple instances holding different ids for the same item (e.g. HD vs 4K remap).
    if len(library.get("records") or []) > 1:
        for key in ("tvdb_id", "tmdb_id", "imdb_id"):
            values = {(r.get("instance"), r.get(key)) for r in library["records"] if r.get(key)}
            distinct = {v for _, v in values}
            if len(distinct) > 1:
                pairs_text = ", ".join(f"{inst}={val}" for inst, val in sorted(values, key=str))
                add("problem", "instances_disagree",
                    f"Your instances disagree on this item's {key.replace('_id', '').upper()} "
                    f"({pairs_text}) — one instance is mapped to a different entry.")

    # An unreleased item reframes "no poster found": makers likely haven't made one yet.
    if library["records"]:
        first_record = library["records"][0]
        status = str(first_record.get("status") or "").lower()
        if item["media_type"] == "movies" and status in ("tba", "announced", "incinemas"):
            add("info", "not_released",
                f"Radarr lists this movie as '{status}' — it has no home release yet, so a "
                "community poster may simply not exist.")
        elif item["media_type"] == "series" and status == "upcoming":
            add("info", "not_released",
                "Sonarr lists this series as upcoming — it has not aired yet, so a community "
                "poster may simply not exist.")

    # A yearless *arr folder deprives the matcher of the folder's title and year.
    yearless_folders = [
        r for r in (library.get("records") or [])
        if r.get("folder") and not r.get("folder_has_year")
    ]

    matched = [c for c in candidates if c["matched"]]
    best = matched[0] if matched else (candidates[0] if candidates else None)

    # Broken tags on the most relevant candidate are worth naming in every outcome.
    if best and best.get("malformed_tags"):
        tags = " ".join(best["malformed_tags"])
        add("problem", "malformed_id_tag",
            f"The poster filename has id tag(s) written in a way Posterflow can't read: {tags}. "
            "A broken tag is ignored, so the poster acts as if it had no id at all. "
            "Use the format {tmdb-123}, {tvdb-123} or {imdb-tt1234567}.")

    artwork_type = item.get("artwork_type")
    artwork_label = ARTWORK_LABELS.get(artwork_type or "", artwork_type)
    if matched and artwork_type:
        best = matched[0]
        have = best.get("artwork_types") or []
        if artwork_type in have:
            add("ok", "artwork_available",
                f"A {artwork_label} for this item exists on [{best['drive']}] — run the Asset "
                "Renamer; if it is still unmatched afterwards, check the renamer log for this title.")
        else:
            have_text = ", ".join(ARTWORK_LABELS.get(t, t) for t in have) or "nothing else"
            add("problem", "artwork_type_not_on_drive",
                f"Artwork for this item exists on [{best['drive']}], but it has no {artwork_label} — "
                f"the set holds {have_text}. A {artwork_label} for this item likely hasn't been "
                "made or uploaded yet.")
    elif matched:
        best = matched[0]
        missing_seasons = item.get("missing_seasons") or []
        if item.get("missing_main") and not best.get("has_main"):
            add("problem", "main_not_on_drive",
                f"A poster set matches on [{best['drive']}], but it holds only season files — "
                "there is no main poster in the set, which is exactly what this item is missing.")
        elif missing_seasons:
            not_on_drive = [s for s in missing_seasons if s not in (best.get("season_numbers") or [])]
            if not_on_drive:
                labels = ", ".join("Specials" if s == 0 else f"Season {s}" for s in sorted(not_on_drive))
                have = ", ".join(str(s) for s in sorted(best.get("season_numbers") or [])) or "none"
                add("problem", "seasons_not_on_drive",
                    f"A poster set matches on [{best['drive']}], but it has no files for: {labels}. "
                    f"The set carries season(s): {have}. "
                    "If the counts look right but the numbers differ, check the numbering scheme — "
                    "aired vs DVD order, and Specials = season 0.")
            else:
                add("ok", "seasons_available",
                    f"Season posters for this item exist on [{best['drive']}] — run the Asset Renamer to place them.")
        else:
            add("ok", "poster_available",
                f"A poster on [{best['drive']}] matches this item ({best['reason']}) — "
                "run the Asset Renamer (a real run, not dry-run); if it is still unmatched "
                "afterwards, check the renamer log for this title.")
    # The live pipeline never reaches the title search when the id lookup returned
    # candidates — a title-pool match here can still be skipped by the renamer.
    if matched:
        best = matched[0]
        if best.get("found_by") == "title" and report["candidates"].get("id_pool", 0) > 0:
            add("problem", "pipeline_may_skip",
                "This poster matched by its title, but other posters on the drives carry this item's "
                "id tag. When id-tagged posters exist, Posterflow uses those and skips title matching "
                "— so this poster may still be passed over. "
                "Add the item's id tag to this poster's filename to make it win.")
    elif candidates:
        best = candidates[0]
        poster_tags = _id_tags(best)
        library_tags = _id_tags(record_ids)
        if best["reason"] == ID_CONFLICT:
            add("problem", "poster_id_conflict",
                f"A poster with the same title and year carries a DIFFERENT id — poster {poster_tags} vs "
                f"library {library_tags}. One side is mistagged; the reference cross-check above shows which. ")
        elif best["reason"] == NO_SHARED_ID:
            add("problem", "no_shared_id",
                f"A title-matching poster shares no id type with the library record — poster {poster_tags} vs "
                f"library {library_tags}. Add a matching id tag to the poster filename (or to the library record).")
        elif best["reason"] == YEAR_MISMATCH:
            if best.get("year") is None and item["media_type"] != "collections":
                add("problem", "yearless_poster",
                    "A poster with this title has no '(Year)' in its filename. Without a year, "
                    "Posterflow can't confirm it is the same item (and treats yearless posters as "
                    "collection posters). Add the year to the filename, e.g. 'Title (2020).jpg'.")
            elif yearless_folders:
                folders = ", ".join(f"[{r.get('instance')}] {r['folder']}" for r in yearless_folders)
                add("problem", "yearless_folder",
                    "A poster matches by title but the years don't line up - the "
                    f"Sonarr/Radarr folder has no '(Year)' in its path ({folders}), so the "
                    "folder can't confirm the poster's year. Rename the folder to include "
                    "the year, e.g. 'Title (2020)', or fix the year in the poster filename.")
            else:
                add("problem", "year_mismatch",
                    "A poster matches by title but the years don't line up. Add the year to the "
                    "folder path in Sonarr/Radarr, or fix the year in the poster filename.")
        if best.get("file_ids"):
            add("problem", "tag_on_file_not_folder",
                f"The id tag {_id_tags(best['file_ids'])} is on the poster FILE, but this drive keeps "
                "each item's posters inside its own folder — and Posterflow reads id tags from the "
                "FOLDER's name, so a tag on the file inside is never seen. "
                "Add the tag to the folder name instead.")
    elif not drives.get("error"):
        scanned = len([d for d in drives.get("scanned", []) if not d.get("missing")])
        add("problem", "no_poster_found",
            f"No poster for this title was found on the {scanned} subscribed drive(s) "
            f"({report['drives']['total_assets']:,} assets scanned, "
            f"{report['candidates']['considered']} similar titles checked). It may not exist yet, "
            "live on a drive you don't subscribe to, or your last sync may predate it.")
        if yearless_folders:
            folders = ", ".join(f"[{r.get('instance')}] {r['folder']}" for r in yearless_folders)
            add("info", "yearless_folder",
                f"The Sonarr/Radarr folder has no '(Year)' in its path ({folders}), so the "
                "folder's name can't be used for matching — a poster named after the folder "
                "instead of the title won't be found. Renaming the folder to 'Title (Year)' "
                "gives posters another name and year to match on.")

    # Things the priority-drive scan cannot see, checked only when nothing matched.
    for hit in report.get("nonpriority_hits") or []:
        add("problem", "poster_on_nonpriority_drive",
            f"A matching poster ({hit['reason']}) exists on [{hit['drive']}], which is subscribed "
            "but NOT in your poster drive priority list — add it under Poster Drives → Priority.")
    for entry in (report.get("unscannable") or [])[:2]:
        add("problem", "poster_unscannable",
            f"'{entry['file']}' on [{entry['drive_dir']}] looks like this item's poster but the "
            f"scanner cannot see it: {entry['reason']}.")
    for close in (report.get("close_titles") or [])[:1]:
        add("info", "close_title",
            f"A similar-titled poster exists: '{close['title']}'"
            + (f" ({close['year']})" if close.get("year") else "")
            + " — not equal under any matching rule (spelling/numerals differ). "
            "Rename it or tag it with the item's id.")

    if report.get("collection_id_note"):
        note = report["collection_id_note"]
        add("problem", "collection_ids_ignored",
            f"A poster tagged with this collection's TMDB id exists ('{note['title']}'), but "
            "collection posters match by TITLE only — id tags are deliberately ignored for them. "
            "Rename the poster to match the collection name.")

    # The sources themselves disagreeing on the year explains stubborn year mismatches.
    if not matched:
        years = {("your library", (library.get("records") or [{}])[0].get("year") if library.get("records") else item.get("year"))}
        for source in ("tvdb", "tmdb"):
            reference = report.get("reference", {}).get(source) or {}
            if reference.get("year"):
                years.add((source.upper(), reference["year"]))
        distinct_years = {y for _, y in years if y}
        if len(distinct_years) > 1:
            listing = ", ".join(f"{src}={yr}" for src, yr in sorted(years, key=str) if yr)
            add("info", "sources_disagree_on_year",
                f"The sources themselves disagree on this item's year ({listing}) — posters may "
                "legitimately be tagged with either year.")

    if not verdicts:
        add("info", "inconclusive",
            "Nothing conclusive found — compare the library record and candidate list above manually.")
    return verdicts


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------

_LEVEL_MARK = {"problem": "✗", "ok": "✓", "info": "•"}

# Keep every rendered line near the ID cross-check row length so nothing runs off the
# page when the file is read in Discord or a forum.
_WRAP_WIDTH = 96


def _sentence_lines(message: str, indent: str) -> List[str]:
    """One sentence per line, with over-long sentences wrapped, all at ``indent``."""
    out: List[str] = []
    for sentence in re.split(r"(?<=[.!?]) +", message.strip()):
        if not sentence:
            continue
        out.extend(
            textwrap.wrap(sentence, width=_WRAP_WIDTH, initial_indent=indent,
                          subsequent_indent=indent + "  ") or [indent + sentence]
        )
    return out


def report_filename(item: Dict[str, Any]) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in str(item.get("title") or "item").lower())
    slug = "-".join(filter(None, slug.split("-")))[:60] or "item"
    if item.get("artwork_type"):
        slug += f"_{item['artwork_type']}"
    return f"posterflow-match-report_{slug}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.txt"


def render_match_report_text(report: Dict[str, Any]) -> str:
    """The shareable plain-text rendering: verdict first, evidence after, JSON appendix."""
    item = report["item"]
    lines: List[str] = []
    title_line = f"{item.get('title')}" + (f" ({item.get('year')})" if item.get("year") else "")

    lines.append(f"Posterflow match report — v{report.get('app_version')} — {report.get('generated_at')}")
    scope = f" — {ARTWORK_LABELS.get(item['artwork_type'], item['artwork_type'])} artwork" if item.get("artwork_type") else ""
    lines.append(f"Item: {title_line}  [{item.get('media_type')}]{scope}")
    if item.get("missing_seasons"):
        labels = ", ".join("Specials" if s == 0 else f"S{s}" for s in item["missing_seasons"])
        lines.append(f"Missing seasons: {labels}")
    lines.append("")

    lines.append("VERDICT")
    for verdict in report.get("verdicts", []):
        mark = _LEVEL_MARK.get(verdict.get("level"), "•")
        wrapped = _sentence_lines(str(verdict.get("message") or ""), "    ")
        if wrapped:
            lines.append(f"  {mark} {wrapped[0].lstrip()}")
            lines.extend(wrapped[1:])
    lines.append("")

    lines.append("LIBRARY RECORD")
    library = report.get("library", {})
    if not library.get("records"):
        source = "manual media entry" if library.get("manual_entry") else "none found in configured instances"
        lines.append(f"  {source}")
    for record in library.get("records", []):
        year_text = f" ({record.get('year')})" if record.get("year") else ""
        lines.append(f"  [{record.get('instance')}]  {record.get('title')}{year_text}")
        lines.append(f"    ids      {_id_tags(record)}")
        if record.get("folder"):
            year_note = "" if record.get("folder_has_year") else "   ⚠ no (year) in path"
            lines.append(f"    folder   {record['folder']}{year_note}")
        seasons = record.get("seasons_with_episodes") or []
        state_bits = []
        if record.get("monitored") is not None:
            state_bits.append(f"monitored={record.get('monitored')}")
        if record.get("status"):
            state_bits.append(f"status={record['status']}")
        if record.get("available") is not None:
            state_bits.append("downloaded" if record["available"] else "not downloaded")
        if report["item"]["media_type"] == "series":
            state_bits.append(f"{len(seasons)} season(s) with episodes")
        if state_bits:
            lines.append(f"    state    {', '.join(state_bits)}")
    lines.append("")

    lines.append("ID CROSS-CHECK")
    ids_label = report.get("library", {}).get("ids_source") or "library"
    label_width = max(10, len(ids_label))
    lines.append(f"  {ids_label:<{label_width}}  {_id_tags(_first_record_ids(report))}")
    for source in ("tvdb", "tmdb", "plex"):
        # The reference key stays "plex" for structure compat; the rows cover every
        # configured media server (Plex and Jellyfin), one line per instance
        row_label = "server" if source == "plex" else source
        reference = report.get("reference", {}).get(source) or {}
        if source == "plex" and reference.get("servers"):
            for entry in reference["servers"]:
                entry_label = entry.get("type") or row_label
                if entry.get("error"):
                    lines.append(f"  {entry_label:<{label_width}}  ✗ {entry.get('instance')}: {entry['error']}")
                elif entry.get("missing"):
                    lines.append(f"  {entry_label:<{label_width}}  not found on {entry.get('instance')}")
                else:
                    extra = f"  → {entry.get('title')}" + (f" ({entry.get('year')})" if entry.get("year") else "")
                    if entry.get("library"):
                        extra += f" in {entry['library']} [{entry.get('instance')}]"
                    lines.append(f"  {entry_label:<{label_width}}  {_id_tags(entry)}{extra}")
            continue
        if reference.get("skipped"):
            lines.append(f"  {row_label:<{label_width}}  (skipped: {reference['skipped']})")
        elif reference.get("error"):
            lines.append(f"  {row_label:<{label_width}}  ✗ {reference['error']}")
        elif reference.get("missing"):
            lines.append(f"  {row_label:<{label_width}}  {reference['missing']}")
        else:
            extra = f"  → {reference.get('title')}" + (f" ({reference.get('year')})" if reference.get("year") else "")
            if reference.get("library"):
                extra += f" in {reference['library']} [{reference.get('instance')}]"
            lines.append(f"  {row_label:<{label_width}}  {_id_tags(reference)}{extra}")
    lines.append("")

    # What each source knows this title as — the names the title-fallback matcher can use.
    alias_rows: List[Tuple[str, List[str], Optional[int]]] = []
    records = report.get("library", {}).get("records") or []
    if records and records[0].get("alternate_titles"):
        alias_rows.append((ids_label, records[0]["alternate_titles"], None))
    for source in ("tvdb", "tmdb"):
        reference = report.get("reference", {}).get(source) or {}
        if reference.get("alternate_titles"):
            alias_rows.append((source, reference["alternate_titles"], reference.get("alternate_titles_total")))
    if alias_rows:
        lines.append("ALTERNATE TITLES")
        for label, names, total in alias_rows:
            joined = ", ".join(names)
            if total and total > len(names):
                joined += f" (+{total - len(names)} more)"
            lines.extend(textwrap.wrap(
                joined, width=_WRAP_WIDTH,
                initial_indent=f"  {label:<{label_width}}  ",
                subsequent_indent=" " * (label_width + 4),
            ))
        lines.append("")

    candidates = report.get("candidates", {})
    matched_count = sum(1 for c in candidates.get("items", []) if c["matched"])
    lines.append(
        f"DRIVE CANDIDATES ({candidates.get('shown', 0)} shown, {matched_count} matched, "
        f"{candidates.get('considered', 0)} similar titles checked)"
    )
    for candidate in candidates.get("items", []):
        mark = "✓" if candidate["matched"] else "✗"
        outcome = ("matched " + candidate["reason"]) if candidate["matched"] else ("rejected: " + candidate["reason"])
        drive = f"  [{candidate['drive']}]" if candidate.get("drive") else ""
        year_text = f" ({candidate.get('year')})" if candidate.get("year") else ""
        lines.append(f"  {mark} {candidate.get('title')}{year_text}{drive}")
        lines.append(f"      ids {_id_tags(candidate)}   {outcome}")
        if candidate.get("artwork_types") is not None:
            lines.append(f"      has {', '.join(ARTWORK_LABELS.get(t, t) for t in candidate['artwork_types']) or 'no artwork slots'}")
        for name in candidate.get("files", []):
            lines.append(f"      file {name}")
        if candidate.get("malformed_tags"):
            lines.append(f"      ⚠ unparseable tag(s): {' '.join(candidate['malformed_tags'])}")
        if candidate.get("file_ids"):
            lines.append(f"      ⚠ ids {_id_tags(candidate['file_ids'])} are on the files — Posterflow reads them from the folder name")
        if candidate.get("newest_file"):
            lines.append(f"      newest file {candidate['newest_file']}")
    if candidates.get("omitted"):
        lines.append(f"  … and {candidates['omitted']} more near-miss candidate(s) omitted")
    if not candidates.get("items"):
        lines.append("  no matching or near-miss posters found")
    for hit in report.get("nonpriority_hits") or []:
        hit_year = f" ({hit.get('year')})" if hit.get("year") else ""
        lines.append(f"  ! on NON-PRIORITY drive [{hit.get('drive')}]: {hit.get('title')}{hit_year} — {hit.get('reason')}")
        for name in hit.get("files", []):
            lines.append(f"      file {name}")
    for entry in report.get("unscannable") or []:
        lines.append(f"  ! unscannable on [{entry.get('drive_dir')}]: {entry.get('file')}")
        lines.append(f"      {entry.get('reason')}")
    for close in report.get("close_titles") or []:
        year_text = f" ({close['year']})" if close.get("year") else ""
        lines.append(f"  ~ similar title: {close.get('title')}{year_text}  (similarity {close.get('similarity')})")
    lines.append("")

    lines.append("DRIVES SCANNED")
    drives = report.get("drives", {})
    if drives.get("error"):
        lines.append(f"  ✗ {drives['error']}")
    for drive in drives.get("scanned", []):
        status = "MISSING LOCALLY" if drive.get("missing") else f"synced {drive.get('last_synced') or 'never'}"
        lines.append(f"  [{drive.get('style_type')}] {drive.get('name')} — {status}")
    lines.append(f"  {drives.get('total_assets', 0):,} assets in the scan index")
    lines.append("")

    # Verdicts stay out of the appendix — they're rendered (wrapped) at the top, and their
    # long message strings are the lines that run off the page in a JSON dump.
    lines.append("RAW DATA (JSON)")
    appendix = {key: value for key, value in report.items() if key != "verdicts"}
    lines.append(json.dumps(appendix, indent=1, default=str))
    return "\n".join(lines)


def _first_record_ids(report: Dict[str, Any]) -> Dict[str, Any]:
    return report.get("library", {}).get("effective_ids") or report.get("item", {})
