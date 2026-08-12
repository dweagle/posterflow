import filecmp
import json
import os
import re
import shutil
import traceback

import requests
from typing import Any, Callable, Dict, List, Optional, Tuple

from pathvalidate import is_valid_filename, sanitize_filename

from core.logging import logger, LogTags, log_success, log_error, log_info, log_warning, log_debug, log_phase, log_section_start, log_section_end
from models.setting import get_setting, upsert_setting
from sqlalchemy.orm import Session
from util.arr.client import create_arr_client
from util.constants import illegal_chars_regex
from util.data.construct import (
    generate_title_variants,
    build_slots,
    slot_dest_name,
    SLOT_POSTER,
    SLOT_SEASON,
    SLOT_LOGO,
    SLOT_BACKGROUND,
    SLOT_SQUARE,
)
from util.data.normalization import normalize_titles
from util.posters.assets import get_assets_files, merge_assets
from util.posters.index import build_search_index, create_new_empty_index
from util.posters.match import match_assets_to_media, match_tmdb_collection

MediaItem = Dict[str, Any]
MediaDict = Dict[str, List[MediaItem]]
RenameProgressCallback = Callable[[int, int, str], None]
WorkflowProgressCallback = Callable[[str, int, int, str], None]


def _slots_from_files(files: List[str]) -> Dict[str, Any]:
    """Classify a matched item's (already-pruned) files into slots: first non-season file is the
    poster (later poster variants are dropped), ' - Specials' is season 0, first source wins per slot."""
    poster = None
    seasons: Dict[int, str] = {}
    for f in files:
        name = os.path.basename(f)
        if re.search(r" - Season| - Specials", name):
            if "Season" in name:
                m = re.search(r"Season (\d+)", name)
                if not m:
                    # Drive convention is "- Season 01" (space); the destination's "Season01"
                    # form is written, never read here. Name it rather than dropping it silently.
                    log_warning(
                        LogTags.RENAMER,
                        f"Skipping season poster with a non-standard name (expected '- Season NN'): {name}",
                        file=name,
                    )
                    continue
                n = int(m.group(1))
            else:
                n = 0  # ' - Specials'
            if n not in seasons:
                seasons[n] = f
        elif poster is None:
            poster = f
    return build_slots(poster=poster, seasons=seasons)


def _placements_from_files(
    files: List[str], folder: str, asset_folders: bool, artwork_slots: Optional[Dict[str, str]] = None,
    box_slots: Optional[Dict[str, Any]] = None,
) -> List[tuple]:
    """The ordered (source_path, dest_filename, season_num, slot) work-list for a matched item.
    poster/seasons come from ``box_slots`` (authoritative — the scan already classified them, pruned
    in step with ``files``) when given, else re-derived; logo/background/square come from
    ``artwork_slots`` so artwork rides the same placement while staying out of poster-only stats."""
    if box_slots:
        slots = dict(box_slots)
        slots["seasons"] = dict(box_slots.get("seasons") or {})
    else:
        slots = _slots_from_files(files)
    # When given (even empty), artwork_slots is authoritative for logo/background/square —
    # the Include filter applies to it, while box slots carry the full set for matching.
    if artwork_slots is not None:
        for slot in (SLOT_LOGO, SLOT_BACKGROUND, SLOT_SQUARE):
            slots[slot] = artwork_slots.get(slot)
    out: List[tuple] = []
    if slots[SLOT_POSTER]:
        p = slots[SLOT_POSTER]
        out.append((p, slot_dest_name(SLOT_POSTER, os.path.splitext(p)[1], folder, asset_folders), None, SLOT_POSTER))
    for n in sorted(slots["seasons"]):
        p = slots["seasons"][n]
        out.append((p, slot_dest_name(SLOT_SEASON, os.path.splitext(p)[1], folder, asset_folders, season=n), n, SLOT_SEASON))
    for slot in (SLOT_LOGO, SLOT_BACKGROUND, SLOT_SQUARE):
        p = slots.get(slot)
        if p:
            out.append((p, slot_dest_name(slot, os.path.splitext(p)[1], folder, asset_folders), None, slot))
    return out


def sourced_poster_slots_from_matched(matched: MediaDict) -> Dict[int, Dict[Any, set]]:
    """Per live media item (keyed by ``id(media)``), the poster slots the subscribed drives
    currently source — ``SLOT_POSTER`` plus season numbers (0 = specials) — each mapped to the
    SOURCE file extensions. The poster mirror of ``sourced_types_from_matched`` — the renamer
    hands its own match across so a rename+cleanup run matches the library once and place/prune
    can't disagree. Unions across boxes when several match the same media.

    Extensions ride along because placement copies the source extension, so they also tell
    cleanup which placed filename is the live one (a stale ``poster.png`` beside the live
    ``poster.jpg`` is prunable instead of being kept forever by a slot-only check)."""
    sourced: Dict[int, Dict[Any, set]] = {}
    for items in matched.values():
        for item in items:
            box_slots = (item.get("asset_ref") or {}).get("slots")
            slots = box_slots if box_slots else _slots_from_files(item.get("files") or [])
            provided: Dict[Any, set] = {}
            poster = slots.get(SLOT_POSTER)
            if poster and os.path.isfile(poster):
                provided.setdefault(SLOT_POSTER, set()).add(os.path.splitext(poster)[1].lower())
            for season, path in (slots.get("seasons") or {}).items():
                if path and os.path.isfile(path):
                    provided.setdefault(int(season), set()).add(os.path.splitext(path)[1].lower())
            if provided and item.get("media_ref") is not None:
                merged = sourced.setdefault(id(item["media_ref"]), {})
                for slot, exts in provided.items():
                    merged.setdefault(slot, set()).update(exts)
    return sourced


def subscribed_priority_drives(db: Session) -> List[Any]:
    """The poster drives the renamer would use, in priority order. Raises ValueError when no
    priority list is configured or none of its drives are subscribed."""
    from models.drive import Drive

    setting = get_setting(db, "poster_drive_priority")
    if not setting or not setting.value:
        raise ValueError("No poster drive priority configured")
    try:
        drive_ids = json.loads(setting.value).get("drive_ids", [])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid poster drive priority configuration: {exc}")
    drives = []
    for drive_id in drive_ids:
        drive = db.query(Drive).filter(Drive.id == drive_id, Drive.subscribed == True).first()
        if drive:
            drives.append(drive)
    if not drives:
        raise ValueError("No poster drives in the priority list are subscribed")
    return drives


def sourced_poster_slots_by_media(db: Session, media_dict: MediaDict) -> Dict[int, Dict[Any, set]]:
    """Standalone-cleanup half of the poster sourcing: scan the subscribed priority drives
    and match them to the live media, mirroring artwork's ``sourced_types_by_media``. Raises
    ValueError (no subscribed drives) / OSError (missing or empty drive folder) — expected
    failures callers degrade to 'prune nothing'; a defect propagates."""
    source_dirs = []
    for drive in subscribed_priority_drives(db):
        path = drive.get_local_path(validate=False)
        if not path.is_dir():
            raise OSError(f"Poster drive folder missing for '{drive.name}': {path}")
        source_dirs.append(str(path))
    assets_dict, prefix_index = get_assets_files(source_dirs)
    if not assets_dict:
        raise OSError("Poster drive scan found no assets (empty or unreadable drive folders)")
    matched = match_assets_to_media(
        media_dict, prefix_index, strict_folder_match=False,
        label="poster drive sources", report_near_misses=False,
    )
    return sourced_poster_slots_from_matched(matched)


class PosterRenameService:
    """Service for renaming posters to Plex-compatible format."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.logger = logger

    def _asset_matches_target(
        self,
        asset: Dict[str, Any],
        *,
        target_media_type: Optional[str],
        target_title_keys: set[str],
        target_year: Optional[int],
        target_tmdb_id: Optional[int],
        target_tvdb_id: Optional[int],
        target_imdb_id: Optional[str],
        target_season_number: Optional[int],
    ) -> bool:
        asset_type = str(asset.get("type", "")).strip().lower()

        if target_media_type == "movie" and asset_type != "movies":
            return False
        if target_media_type == "series" and asset_type != "series":
            return False

        asset_tmdb_raw = asset.get("tmdb_id")
        asset_tvdb_raw = asset.get("tvdb_id")
        asset_imdb_raw = asset.get("imdb_id")
        try:
            asset_tmdb: Optional[int] = int(asset_tmdb_raw) if asset_tmdb_raw is not None else None
        except (ValueError, TypeError):
            asset_tmdb = None
        try:
            asset_tvdb: Optional[int] = int(asset_tvdb_raw) if asset_tvdb_raw is not None else None
        except (ValueError, TypeError):
            asset_tvdb = None
        asset_imdb = str(asset_imdb_raw).strip().lower() if asset_imdb_raw else None

        if target_tmdb_id is not None and asset_tmdb == target_tmdb_id:
            if target_media_type != "series":
                return True
        if target_tvdb_id is not None and asset_tvdb == target_tvdb_id:
            return True
        if target_imdb_id and asset_imdb and asset_imdb == target_imdb_id:
            return True

        # When a specific year is requested and the asset also has a year, require they match.
        # This prevents "3:10 to Yuma (2007)" assets from passing a filter for year=1957
        # when normalize_titles strips years from both sides and can't distinguish them.
        # ID-based early returns above are exempt since IDs already uniquely identify the item.
        asset_year = asset.get("year")
        if target_year is not None and asset_year is not None and asset_year != target_year:
            return False

        normalized_title = str(asset.get("normalized_title") or "").strip().lower()
        if not normalized_title:
            normalized_title = normalize_titles(str(asset.get("title") or ""))

        if target_title_keys and normalized_title not in target_title_keys:
            normalized_alternates = asset.get("normalized_alternate_titles")
            if isinstance(normalized_alternates, list):
                alternate_keys = {
                    str(value).strip().lower()
                    for value in normalized_alternates
                    if isinstance(value, str) and value.strip()
                }
                if not (alternate_keys & target_title_keys):
                    return False
            else:
                return False

        if target_media_type == "series" and target_season_number is not None:
            season_numbers = asset.get("season_numbers")
            if isinstance(season_numbers, list) and season_numbers:
                if target_season_number not in {int(number) for number in season_numbers if isinstance(number, int)}:
                    return False

        return True

    def _filter_assets_for_target(
        self,
        assets_dict: List[Dict[str, Any]],
        *,
        target_media_type: Optional[str],
        target_title: Optional[str],
        target_year: Optional[int],
        target_tmdb_id: Optional[int],
        target_tvdb_id: Optional[int],
        target_imdb_id: Optional[str],
        target_season_number: Optional[int],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        title_keys: set[str] = set()
        if target_title:
            title_keys.add(normalize_titles(target_title))
            if target_year is not None:
                title_keys.add(normalize_titles(f"{target_title} ({target_year})"))

        normalized_imdb = target_imdb_id.strip().lower() if isinstance(target_imdb_id, str) and target_imdb_id.strip() else None

        filtered_assets = [
            asset
            for asset in assets_dict
            if self._asset_matches_target(
                asset,
                target_media_type=target_media_type,
                target_title_keys=title_keys,
                target_year=target_year,
                target_tmdb_id=target_tmdb_id,
                target_tvdb_id=target_tvdb_id,
                target_imdb_id=normalized_imdb,
                target_season_number=target_season_number,
            )
        ]

        filtered_index = create_new_empty_index()
        for asset in filtered_assets:
            build_search_index(filtered_index, str(asset.get("title", "")), asset)

        return filtered_assets, filtered_index

    def _fetch_plex_collections(
        self, 
        url: str, 
        token: str, 
        media_dict: MediaDict,
        instance_name: str = "Plex",
        log_tag: str = LogTags.RENAMER,
        selected_libraries: Optional[List[str]] = None
    ) -> None:
        """
        Fetch collections from a Plex server.
        
        Args:
            url: Plex server URL
            token: Plex authentication token
            media_dict: Dictionary to append collections to
            instance_name: Name of this Plex instance
            log_tag: Tag to use for logging (LogTags constant)
            selected_libraries: List of library keys to include (format: "instance_name:library_key")
        """
        try:
            from plexapi.server import PlexServer
            from util.data.normalization import normalize_titles
            import html
            from unidecode import unidecode
            
            log_info(log_tag, f"Connecting to {instance_name} at {url} to fetch collections...")
            plex = PlexServer(url, token)
            
            # Get all library sections
            for section in plex.library.sections():
                if section.type in ['movie', 'show']:
                    # Check if this library is selected
                    library_key = f"{instance_name}:{section.key}"
                    if selected_libraries is not None and library_key not in selected_libraries:
                        log_debug(log_tag, f"Skipping library '{section.title}' (not selected)")
                        continue
                    
                    log_info(log_tag, f"Fetching collections from {instance_name} library: {section.title}")
                    collections = section.search(libtype='collection')
                    
                    for collection in collections:
                        if getattr(collection, "smart", False):
                            log_debug(log_tag, f"Including smart collection: {collection.title}")
                        
                        title = unidecode(html.unescape(collection.title))
                        normalized_title = normalize_titles(title)
                        
                        # Generate alternate title variants (like DAPS does)
                        title_variants = generate_title_variants(title)
                        
                        # Pre-sanitize folder name to remove illegal chars (matches DAPS behavior)
                        folder = illegal_chars_regex.sub("", title)
                        
                        media_dict["collections"].append({
                            "type": "collections",
                            "title": title,
                            "year": None,
                            "folder": folder,  # Use pre-sanitized folder name
                            "normalized_title": normalized_title,
                            "alternate_titles": title_variants["alternate_titles"],
                            "normalized_alternate_titles": title_variants["normalized_alternate_titles"],
                            "instance": f"{instance_name} ({section.title})",  # Track which library this is from
                            "library_type": section.type,  # Store the actual library type ('movie' or 'show')
                        })
                    
                    log_info(log_tag, f"Found {len(collections)} collections in '{section.title}' ({instance_name})")
                    
        except ImportError:
            log_error(log_tag, "plexapi not installed. Install with: pip install plexapi")
        except Exception as e:
            log_error(log_tag, f"Error connecting to {instance_name}: {e}")

    def _merge_duplicate_series(self, series_list: List[MediaItem], log_tag: str = LogTags.RENAMER) -> List[MediaItem]:
        """
        Merge duplicate series entries from multiple Sonarr instances.
        Combines season lists to include all seasons across all instances (DAPS behavior).
        
        Args:
            series_list: List of series from all Sonarr instances
            log_tag: Tag to use for logging (LogTags constant)
            
        Returns:
            Deduplicated list with merged season data
        """
        if not series_list:
            return []
        
        # Group series by unique identifier (tvdb_id preferred, fallback to title+year)
        series_map: Dict[str, Dict[str, Any]] = {}
        
        for series in series_list:
            # Create unique key - prefer TVDB ID, fallback to title+year
            tvdb_id = series.get("tvdb_id")
            if tvdb_id:
                key = f"tvdb_{tvdb_id}"
            else:
                title = series.get("title", "").lower()
                year = series.get("year", "")
                key = f"{title}_{year}"
            
            if key in series_map:
                # Merge with existing entry
                existing = series_map[key]
                
                # Merge season lists (union of all seasons)
                existing_seasons = existing.get("seasons", [])
                new_seasons = series.get("seasons", [])
                
                # Create dict to deduplicate by season_number
                season_dict = {s["season_number"]: s for s in existing_seasons}
                for season in new_seasons:
                    season_num = season["season_number"]
                    if season_num not in season_dict:
                        season_dict[season_num] = season
                
                existing["seasons"] = sorted(season_dict.values(), key=lambda s: s["season_number"])
                
                # Combine instance names
                existing_instance = existing.get("instance", "")
                new_instance = series.get("instance", "")
                if new_instance and new_instance not in existing_instance:
                    existing["instance"] = f"{existing_instance} & {new_instance}"
                
                log_debug(
                    log_tag,
                    f"Merged '{series.get('title')}' from {new_instance} "
                    f"with {len(new_seasons)} seasons. Total unique seasons: {len(existing['seasons'])}"
                )
            else:
                # First time seeing this series
                series_map[key] = series
        
        merged_list = list(series_map.values())
        
        if len(merged_list) < len(series_list):
            log_info(
                log_tag,
                f"Deduplicated series: {len(series_list)} entries → {len(merged_list)} unique series "
                f"(merged {len(series_list) - len(merged_list)} duplicates from multiple Sonarr instances)"
            )
        
        return merged_list

    def _merge_duplicate_movies(self, movies_list: List[MediaItem], log_tag: str = LogTags.RENAMER) -> List[MediaItem]:
        """
        Merge duplicate movie entries from multiple Radarr instances.
        
        Args:
            movies_list: List of movies from all Radarr instances
            log_tag: Tag to use for logging (LogTags constant)
            
        Returns:
            Deduplicated list
        """
        if not movies_list:
            return []
        
        # Group movies by unique identifier (tmdb_id preferred, fallback to title+year)
        movies_map: Dict[str, Dict[str, Any]] = {}
        
        for movie in movies_list:
            # Create unique key - prefer TMDB ID, fallback to title+year
            tmdb_id = movie.get("tmdb_id")
            if tmdb_id:
                key = f"tmdb_{tmdb_id}"
            else:
                title = movie.get("title", "").lower()
                year = movie.get("year", "")
                key = f"{title}_{year}"
            
            if key in movies_map:
                # Already exists - combine instance names
                existing = movies_map[key]
                existing_instance = existing.get("instance", "")
                new_instance = movie.get("instance", "")
                if new_instance and new_instance not in existing_instance:
                    existing["instance"] = f"{existing_instance} & {new_instance}"
                
                log_debug(
                    log_tag,
                    f"Merged '{movie.get('title')}' from {new_instance}"
                )
            else:
                # First time seeing this movie
                movies_map[key] = movie
        
        merged_list = list(movies_map.values())
        
        if len(merged_list) < len(movies_list):
            log_info(
                log_tag,
                f"Deduplicated movies: {len(movies_list)} entries → {len(merged_list)} unique movies "
                f"(merged {len(movies_list) - len(merged_list)} duplicates from multiple Radarr instances)"
            )
        
        return merged_list

    def _merge_duplicate_collections(self, collections_list: List[MediaItem], log_tag: str = LogTags.RENAMER) -> List[MediaItem]:
        """
        Deduplicate collection entries from multiple Plex libraries.

        The same collection (e.g. 'Netflix Top 10') can appear in both a Movies
        and a TV library, producing identical destination folders.  Keep only the
        first-seen entry per normalized title so downstream matching doesn't try
        to write the same poster.jpg twice.

        Args:
            collections_list: List of collections from all Plex instances/libraries.
            log_tag: Tag to use for logging.

        Returns:
            Deduplicated list.
        """
        if not collections_list:
            return []

        seen: Dict[str, Dict[str, Any]] = {}
        for collection in collections_list:
            # Prefer TMDB ID as key; fall back to normalized title for custom collections
            tmdb_id = collection.get("tmdb_id")
            key = f"tmdb_{tmdb_id}" if tmdb_id else normalize_titles(collection.get("title", ""))
            if key not in seen:
                seen[key] = collection
            else:
                log_debug(
                    log_tag,
                    f"Deduplicated collection: '{collection.get('title')}' "
                    f"(instance: {collection.get('instance', '?')})"
                )

        merged_list = list(seen.values())
        if len(merged_list) < len(collections_list):
            log_info(
                log_tag,
                f"Deduplicated collections: {len(collections_list)} entries → {len(merged_list)} unique "
                f"(removed {len(collections_list) - len(merged_list)} duplicates across Plex libraries)"
            )

        return merged_list

    def process_file(
        self, file: str, new_file_path: str, action_type: str
    ) -> None:
        """
        Perform a file operation (copy, move, hardlink, or symlink) between paths.
        
        Args:
            file: Original file path.
            new_file_path: Destination file path.
            action_type: Operation type: 'copy', 'move', 'hardlink', or 'symlink'.
        """
        try:
            if action_type == "copy":
                # copy2 preserves mtime so filecmp shallow stat-check short-circuits on re-runs.
                shutil.copy2(file, new_file_path)
                # Drop source from page cache — won't be needed again this run.
                try:
                    with open(file, "rb") as f:
                        os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                except (AttributeError, OSError, Exception):
                    pass
            elif action_type == "move":
                shutil.move(file, new_file_path)
            elif action_type == "hardlink":
                os.link(file, new_file_path)
            elif action_type == "symlink":
                os.symlink(file, new_file_path)
        except OSError as e:
            log_error(LogTags.RENAMER, f"Error {action_type}ing file: {e}")

    def rename_files(
        self,
        matched_assets: MediaDict,
        destination_dir: str,
        action_type: str = "copy",
        asset_folders: bool = True,
        dry_run: bool = False,
        progress_callback: Optional[RenameProgressCallback] = None,
        artwork_destination: Optional[str] = None,
        artwork_slot_filter: Optional[set] = None,
    ) -> Tuple[MediaDict, List[str], List[str], Dict[str, tuple], Dict[str, Any]]:
        """
        Rename matched assets to Plex-compatible filenames and handle folder structure.
        
        Args:
            matched_assets: Dictionary of matched poster assets.
            destination_dir: Destination directory for renamed posters.
            action_type: File operation type ('copy', 'move', 'hardlink', 'symlink').
            asset_folders: Whether to create asset folders.
            dry_run: If True, simulate without making changes.
            progress_callback: Optional callback function(current, total, item_name) for progress updates.
            
        Returns:
            Tuple of (output dict, destination files copied/updated, source files processed,
            winning_source_files mapping dest_path -> (source_file_path, title, year,
            tmdb_type, season, tmdb_id, tvdb_id, imdb_id, poster_url)).
        """
        output: MediaDict = {}
        renamed_files = []  # Destination files that were copied/updated
        # Of those, which were artwork — broken down the same way posters are (by media type),
        # plus by artwork type, so the summary can show more than a bare total.
        artwork_renamed: Dict[str, Any] = {
            "total": 0,
            "by_media": {"movies": 0, "series": 0, "collections": 0},
            "by_type": {"logo": 0, "background": 0, "squareart": 0},
        }

        def _tally_artwork(media_type: str, slot: str) -> None:
            artwork_renamed["total"] += 1
            if media_type in artwork_renamed["by_media"]:
                artwork_renamed["by_media"][media_type] += 1
            # On disk the square slot is 'square'; the internal artwork type is 'squareart'.
            slot_key = "squareart" if slot == SLOT_SQUARE else slot
            if slot_key in artwork_renamed["by_type"]:
                artwork_renamed["by_type"][slot_key] += 1
        processed_source_files = []  # Source files that were checked (copied or skipped if identical)
        # dest_path -> (source_file_path, title, year, tmdb_type, season_num)
        winning_source_files: Dict[str, tuple] = {}
        
        asset_types: List[str] = ["collections", "movies", "series"]
        log_info(LogTags.RENAMER, "Renaming assets, please wait...")

        # Artwork with no poster needs no special case: an artwork box lives in the same asset
        # index, so an item whose only source is artwork simply matches as itself.

        # Calculate total items to process
        total_items = sum(len(matched_assets[asset_type]) for asset_type in asset_types)
        current_item = 0
        written_dest_paths: Dict[str, str] = {}  # dest_path -> source filename that won
        
        for asset_type in asset_types:
            output[asset_type] = []
            if matched_assets[asset_type]:
                for item in matched_assets[asset_type]:
                    current_item += 1

                    item_name = f"{item.get('title', 'Unknown')} ({item.get('year', 'N/A')})"
                    item_had_changes = False
                    
                    messages: List[str] = []
                    files = item["files"]
                    folder = item["folder"]
                    
                    # Extract only the folder name (not full path) from media library path
                    if folder:
                        folder = os.path.basename(folder.rstrip("/"))
                    
                    # Sanitize folder name for collections
                    if asset_type == "collections":
                        if not is_valid_filename(folder):
                            folder = sanitize_filename(folder)
                    
                    # Construct destination folder
                    if asset_folders:
                        dest_dir = os.path.join(destination_dir, folder)
                        if not os.path.exists(dest_dir):
                            if not dry_run:
                                os.makedirs(dest_dir)
                    else:
                        dest_dir = destination_dir

                    # Artwork goes straight to the real destination (never the tmp/ staging), so
                    # the border replacer — which only processes tmp/ — never touches it.
                    art_base = artwork_destination or destination_dir
                    artwork_dest_dir = os.path.join(art_base, folder) if asset_folders else art_base

                    # Artwork (logo/background/square) rides the same box as the poster — one
                    # match filled both — so it needs no separate lookup here.
                    # The matched box's slots are authoritative; artwork-only items have no box.
                    box_slots = (item.get("asset_ref") or {}).get("slots")
                    if "_artwork_slots" in item:
                        artwork_slots = item["_artwork_slots"]
                    else:
                        artwork_slots = {
                            s: (box_slots or {}).get(s)
                            for s in (SLOT_LOGO, SLOT_BACKGROUND, SLOT_SQUARE)
                            if (box_slots or {}).get(s)
                        }
                    # The Include selection filters here, at placement — matching sees the full
                    # slot set so cleanup learns everything the drives source.
                    if artwork_slot_filter is not None:
                        artwork_slots = {s: p for s, p in artwork_slots.items() if s in artwork_slot_filter}
                    if artwork_slots:
                        item["_artwork_slots"] = artwork_slots  # let the caller tally artwork placed

                    # Rename each asset file — walk the item's slots (poster/seasons + artwork).
                    for file, new_file_name, _season_num, _slot in _placements_from_files(
                        files, folder, asset_folders, artwork_slots, box_slots
                    ):
                        file_name = os.path.basename(file)
                        # Artwork (logo/background/square) is placed straight to the real dest and
                        # kept out of poster-only tracking (rename messages / style stats); it's
                        # tallied via _artwork_slots.
                        is_artwork = _slot in (SLOT_LOGO, SLOT_BACKGROUND, SLOT_SQUARE)
                        target_dir = artwork_dest_dir if is_artwork else dest_dir
                        if is_artwork and asset_folders and not dry_run:
                            os.makedirs(target_dir, exist_ok=True)
                        new_file_path = os.path.join(target_dir, new_file_name)

                        # Skip if already written to this destination in this run (highest-priority entry wins)
                        if new_file_path in written_dest_paths:
                            src_drive = os.path.basename(os.path.dirname(os.path.dirname(file)))
                            src_folder = os.path.basename(os.path.dirname(file))
                            winning_file = written_dest_paths[new_file_path]
                            log_warning(
                                LogTags.RENAMER,
                                f"Skipping '{item_name}' from [{src_drive}/{src_folder}] — "
                                f"'{new_file_name}' was already written by a higher-priority source this run. "
                                f"This is normal when multiple sources with different file names match the same media. "
                                f"Winner: '{winning_file}' | Skipped: '{file_name}'",
                                media=item_name,
                                source_folder=src_folder,
                                drive=src_drive,
                                dest=new_file_path,
                                winning_file=winning_file,
                                skipped_file=file_name,
                            )
                            continue

                        # Check if destination exists and is different
                        if os.path.lexists(new_file_path):
                            existing_file = os.path.join(target_dir, new_file_name)
                            try:
                                # filecmp short-circuits on matching size+mtime (copy2 preserves mtime).
                                # os.utime() below syncs mtime for unchanged files so next run is stat-only.
                                if not filecmp.cmp(file, existing_file):
                                    if file_name != new_file_name and not is_artwork:
                                        messages.append(
                                            f"{file_name} -renamed-> {new_file_name}"
                                        )
                                    if not dry_run:
                                        if action_type in ["hardlink", "symlink"]:
                                            os.remove(new_file_path)
                                        self.process_file(file, new_file_path, action_type)
                                        renamed_files.append(new_file_path)
                                        if is_artwork:
                                            _tally_artwork(asset_type, _slot)
                                        processed_source_files.append(file)
                                        item_had_changes = True
                                        # Log the file as it's being renamed
                                        log_info(
                                            LogTags.RENAMER,
                                            f"  → {file_name} → {new_file_name}",
                                            source=file_name, dest=new_file_name
                                        )
                                else:
                                    # Identical — sync mtime so next run's stat-check short-circuits.
                                    if not dry_run:
                                        try:
                                            src_stat = os.stat(file)
                                            os.utime(existing_file, (src_stat.st_atime, src_stat.st_mtime))
                                        except OSError:
                                            pass
                                    processed_source_files.append(file)
                            except FileNotFoundError:
                                if not dry_run:
                                    os.remove(new_file_path)
                                    self.process_file(file, new_file_path, action_type)
                                    renamed_files.append(new_file_path)
                                    if is_artwork:
                                        _tally_artwork(asset_type, _slot)
                                    processed_source_files.append(file)
                                    item_had_changes = True
                                    # Log the file as it's being renamed
                                    log_info(
                                        LogTags.RENAMER,
                                        f"  → {file_name} → {new_file_name}",
                                        source=file_name, dest=new_file_name
                                    )
                        else:
                            # Destination doesn't exist - need to copy
                            if file_name != new_file_name and not is_artwork:
                                messages.append(
                                    f"{file_name} -renamed-> {new_file_name}"
                                )
                            if not dry_run:
                                self.process_file(file, new_file_path, action_type)
                                renamed_files.append(new_file_path)
                                if is_artwork:
                                    _tally_artwork(asset_type, _slot)
                                processed_source_files.append(file)
                                item_had_changes = True
                                # Log the file as it's being renamed
                                log_info(
                                    LogTags.RENAMER,
                                    f"  → {file_name} → {new_file_name}",
                                    source=file_name, dest=new_file_name
                                )

                        written_dest_paths[new_file_path] = file_name
                        if not is_artwork:
                            _tmdb_type = "movie" if asset_type == "movies" else ("collection" if asset_type == "collections" else "show")
                            winning_source_files[new_file_path] = (
                                file, item["title"], item.get("year"), _tmdb_type, _season_num,
                                item.get("tmdb_id"), item.get("tvdb_id"), item.get("imdb_id"), item.get("poster_url"),
                                item.get("available"),
                            )

                    if progress_callback:
                        if item_had_changes:
                            progress_callback(current_item, total_items, f"Renaming: {item_name}")
                        else:
                            progress_callback(current_item, total_items, "Evaluating matches...")
                    
                    if messages:
                        output[asset_type].append(
                            {
                                "title": item["title"],
                                "year": item["year"],
                                "folder": item["folder"],
                                "messages": messages,
                            }
                        )
            else:
                log_debug(LogTags.RENAMER, f"No {asset_type} to rename")
        
        return output, renamed_files, processed_source_files, winning_source_files, artwork_renamed

    def _inject_manual_media(self, media_dict: MediaDict, log_tag: str = LogTags.RENAMER) -> None:
        """
        Load manually added media entries from the database and inject them into
        *media_dict* so they participate in the normal poster-renaming pipeline.

        Manual entries represent shows/movies that live in Plex but are not
        managed by Sonarr or Radarr.  They are never deduplicated against
        arr-sourced entries; if the same title appears in both sources the
        arr-sourced entry takes precedence because it arrives first and the
        deduplication step has already run.
        """
        try:
            from models.manual_media import ManualMediaEntry
            from util.data.construct import generate_title_variants

            entries = self.db.query(ManualMediaEntry).all()
            if not entries:
                return

            injected_movies = 0
            injected_series = 0

            for entry in entries:
                title = entry.title or ""
                if not title:
                    continue

                normalized = normalize_titles(title)
                title_variants = generate_title_variants(title)

                id_parts = []
                if entry.imdb_id:
                    id_parts.append(f"{{imdb-{entry.imdb_id}}}")
                if entry.tmdb_id:
                    id_parts.append(f"{{tmdb-{entry.tmdb_id}}}")
                if entry.tvdb_id:
                    id_parts.append(f"{{tvdb-{entry.tvdb_id}}}")
                base_folder = f"{title} ({entry.year})" if entry.year else title
                folder = f"{base_folder} {' '.join(id_parts)}" if id_parts else base_folder

                if entry.media_type == "movie":
                    media_dict["movies"].append({
                        "type": "movies",
                        "title": title,
                        "year": entry.year,
                        "folder": folder,
                        "root_folder": None,
                        "tmdb_id": entry.tmdb_id,
                        "tvdb_id": entry.tvdb_id,
                        "imdb_id": entry.imdb_id,
                        "monitored": True,
                        "normalized_title": normalized,
                        "alternate_titles": title_variants.get("alternate_titles", []),
                        "normalized_alternate_titles": title_variants.get("normalized_alternate_titles", []),
                        "status": "released",
                        "has_file": True,
                        "instance": "Manual",
                        "source": "manual",
                    })
                    injected_movies += 1

                elif entry.media_type == "series":
                    season_numbers: List[int] = []
                    if entry.seasons_json:
                        try:
                            season_numbers = json.loads(entry.seasons_json)
                        except Exception:
                            pass

                    seasons = [
                        {"season_number": s, "season_has_episodes": True, "monitored": True}
                        for s in season_numbers
                    ]

                    media_dict["series"].append({
                        "type": "series",
                        "title": title,
                        "year": entry.year,
                        "folder": folder,
                        "root_folder": None,
                        "tmdb_id": entry.tmdb_id,
                        "tvdb_id": entry.tvdb_id,
                        "imdb_id": entry.imdb_id,
                        "monitored": True,
                        "normalized_title": normalized,
                        "alternate_titles": title_variants.get("alternate_titles", []),
                        "normalized_alternate_titles": title_variants.get("normalized_alternate_titles", []),
                        "seasons": seasons,
                        "status": "continuing",
                        "has_episodes": len(seasons) > 0,
                        "instance": "Manual",
                        "source": "manual",
                    })
                    injected_series += 1

            if injected_movies or injected_series:
                log_info(
                    log_tag,
                    f"Injected manual media: {injected_movies} movies, {injected_series} series",
                    movies=injected_movies, series=injected_series,
                )

        except Exception as exc:
            log_warning(log_tag, f"Failed to load manual media entries: {exc}")

    def get_media_from_instances(self, log_tag: str = LogTags.RENAMER, setting_key: str = "poster_renamer_libraries") -> MediaDict:
        """
        Get media from configured Plex/Radarr/Sonarr instances.
        
        Args:
            log_tag: Tag to use for logging (LogTags constant, default: LogTags.RENAMER)
            setting_key: Settings key for library filter (default: "poster_renamer_libraries")
        
        Returns:
            Dictionary of media by type (movies, series, collections).
        """
        media_dict: MediaDict = {
            "movies": [],
            "series": [],
            "collections": [],
        }

        # Load selected libraries setting
        selected_libraries_setting = get_setting(self.db, setting_key)
        selected_libraries = None
        if selected_libraries_setting and selected_libraries_setting.value:
            try:
                selected_libraries = json.loads(selected_libraries_setting.value)
                if selected_libraries:
                    log_info(log_tag, f"Using library filter: {len(selected_libraries)} libraries selected ({setting_key})")
            except Exception as e:
                log_error(log_tag, f"Error parsing {setting_key}: {e}")

        # Get Plex collections (supports both formats for backward compatibility)
        plex_instances = get_setting(self.db, "plex_instances")
        
        if plex_instances and plex_instances.value:
            # New format: instances array (supports multiple Plex servers)
            try:
                instances = json.loads(plex_instances.value)
                for instance in instances:
                    self._fetch_plex_collections(
                        instance["url"], 
                        instance["api_key"], 
                        media_dict, 
                        instance.get("name", "Plex"),
                        log_tag,
                        selected_libraries
                    )
            except Exception as e:
                log_error(LogTags.RENAMER, f"Error parsing plex_instances: {e}")
        else:
            # Old format: separate url/token fields (single Plex server)
            plex_url_setting = get_setting(self.db, "plex_url")
            plex_token_setting = get_setting(self.db, "plex_token")
            
            if plex_url_setting and plex_token_setting and plex_url_setting.value and plex_token_setting.value:
                self._fetch_plex_collections(
                    plex_url_setting.value, 
                    plex_token_setting.value, 
                    media_dict,
                    "Plex",
                    log_tag,
                    selected_libraries
                )
        
        # Get Radarr instances
        radarr_instances = get_setting(self.db, "radarr_instances")
        if radarr_instances and radarr_instances.value:
            instances = json.loads(radarr_instances.value)
            for instance in instances:
                try:
                    client = create_arr_client(
                        instance["url"],
                        instance["api_key"],
                        "radarr",
                        self.logger
                    )
                    if client and client.connect_status:
                        # Use include_unmonitored=True to match DAPS behavior
                        results = client.get_parsed_media(include_unmonitored=True)
                        if results:
                            # Add instance info to each media item
                            for item in results:
                                item["instance"] = instance.get("name", "Radarr")
                            media_dict["movies"].extend(results)
                        else:
                            log_error(log_tag, f"No Radarr data found from {instance['url']}")
                except Exception as e:
                    log_error(log_tag, f"Error getting Radarr data: {e}")

        # Get Sonarr instances
        sonarr_instances = get_setting(self.db, "sonarr_instances")
        if sonarr_instances and sonarr_instances.value:
            instances = json.loads(sonarr_instances.value)
            for instance in instances:
                try:
                    client = create_arr_client(
                        instance["url"],
                        instance["api_key"],
                        "sonarr",
                        self.logger
                    )
                    if client and client.connect_status:
                        # Use include_unmonitored=True to match DAPS behavior
                        # This includes all seasons regardless of monitored status
                        results = client.get_parsed_media(include_unmonitored=True)
                        if results:
                            # Add instance info to each media item
                            for item in results:
                                item["instance"] = instance.get("name", "Sonarr")
                            media_dict["series"].extend(results)
                        else:
                            log_error(log_tag, f"No Sonarr data found from {instance['url']}")
                except Exception as e:
                    log_error(log_tag, f"Error getting Sonarr data: {e}")

        # Deduplicate and merge media from multiple instances
        # This matches DAPS behavior: combine season lists from multiple Sonarr instances
        media_dict["movies"] = self._merge_duplicate_movies(media_dict["movies"], log_tag)
        media_dict["series"] = self._merge_duplicate_series(media_dict["series"], log_tag)
        media_dict["collections"] = self._merge_duplicate_collections(media_dict["collections"], log_tag)

        # Plex collections carry no TMDB id (unlike Radarr movies / Sonarr series).
        # Match them against TMDB's collection search so they get the same id +
        # poster treatment downstream (stats, unmatched, community lists).
        self._enrich_collections_with_tmdb(media_dict, log_tag)

        # Inject manually added media entries (items in Plex not managed by Sonarr/Radarr)
        self._inject_manual_media(media_dict, log_tag)

        return media_dict

    def _enrich_collections_with_tmdb(
        self, media_dict: MediaDict, log_tag: str = LogTags.RENAMER
    ) -> None:
        """Attach a TMDB id + poster to Plex collections via exact-title matching.

        Plex exposes no TMDB id for collections, so unlike movies/series they reach
        the community Lists tab id-less. For each collection without one we query
        TMDB's collection search and accept only an exact normalized-title match
        (``match_tmdb_collection``); matches gain a ``poster_url`` plus the id on
        ``tmdb_id_ref`` — deliberately NOT ``tmdb_id`` — so the poster matcher (which
        branches on ``media.get("tmdb_id")`` and would switch collections from
        title-based to ID-based matching) is untouched, while ``media_source_refs``
        still surfaces the id for lists/requests via its ``tmdb_id_ref`` fallback.
        This mirrors how series keep their TMDB id off the matcher. Everything else
        stays a "custom" collection. Results (matches and non-matches) are cached so
        scheduled runs don't re-query TMDB every time. A non-match stays cached until
        the collection is renamed in Plex (which changes the title-keyed cache entry).
        """
        collections = media_dict.get("collections", [])
        pending = [c for c in collections if not c.get("tmdb_id") and not c.get("tmdb_id_ref")]
        if not pending:
            return

        api_key_setting = get_setting(self.db, "tmdb_api_key")
        api_key = api_key_setting.value.strip() if api_key_setting and api_key_setting.value else ""
        if not api_key:
            log_info(
                log_tag,
                f"Skipping TMDB collection matching for {len(pending)} collection(s): no TMDB API key configured",
                count=len(pending),
            )
            return

        # Load the persistent cache: normalized title -> {tmdb_id, poster_url}.
        # A null tmdb_id is a remembered non-match (custom collection).
        cache: Dict[str, Dict[str, Any]] = {}
        cache_setting = get_setting(self.db, "poster_collection_tmdb_cache")
        if cache_setting and cache_setting.value:
            try:
                loaded = json.loads(cache_setting.value)
                if isinstance(loaded, dict):
                    cache = loaded
            except json.JSONDecodeError:
                cache = {}

        matched = 0
        cache_dirty = False
        # De-dup TMDB lookups within this run (same collection across libraries).
        run_seen: Dict[str, Optional[Dict[str, Any]]] = {}

        for collection in pending:
            title = str(collection.get("title") or "").strip()
            if not title:
                continue
            norm = normalize_titles(title)

            entry = cache.get(norm)
            if entry is not None:
                if entry.get("tmdb_id"):
                    # tmdb_id_ref (not tmdb_id) keeps this off the poster matcher.
                    collection["tmdb_id_ref"] = entry["tmdb_id"]
                    if entry.get("poster_url"):
                        collection["poster_url"] = entry["poster_url"]
                    matched += 1
                continue

            if norm in run_seen:
                result = run_seen[norm]
            else:
                result = self._search_tmdb_collection(title, api_key, log_tag)
                run_seen[norm] = result

            tmdb_id: Optional[int] = None
            poster_url: Optional[str] = None
            if result:
                raw_id = result.get("id")
                tmdb_id = int(raw_id) if isinstance(raw_id, int) else None
                poster_path = result.get("poster_path")
                if isinstance(poster_path, str) and poster_path:
                    poster_url = f"https://image.tmdb.org/t/p/w185{poster_path}"

            if tmdb_id:
                # tmdb_id_ref (not tmdb_id) keeps this off the poster matcher.
                collection["tmdb_id_ref"] = tmdb_id
                if poster_url:
                    collection["poster_url"] = poster_url
                cache[norm] = {"tmdb_id": tmdb_id, "poster_url": poster_url}
                matched += 1
            else:
                cache[norm] = {"tmdb_id": None, "poster_url": None}
            cache_dirty = True

        if cache_dirty:
            try:
                upsert_setting(self.db, "poster_collection_tmdb_cache", json.dumps(cache))
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                log_warning(log_tag, f"Could not persist collection TMDB cache: {e}", error=str(e))

        log_info(
            log_tag,
            f"TMDB collection matching: {matched}/{len(pending)} collection(s) matched to a TMDB collection",
            matched=matched, total=len(pending),
        )

    def _search_tmdb_collection(
        self, title: str, api_key: str, log_tag: str = LogTags.RENAMER
    ) -> Optional[Dict[str, Any]]:
        """Query TMDB's collection search and return the exact-title match, if any."""
        try:
            response = requests.get(
                "https://api.themoviedb.org/3/search/collection",
                params={"api_key": api_key, "query": title, "include_adult": "false"},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            log_debug(log_tag, f"TMDB collection search failed for '{title}': {e}", title=title, error=str(e))
            return None

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return None
        return match_tmdb_collection(title, results)

    def rename_posters(
        self,
        source_dirs: List[str],
        destination_dir: str,
        action_type: str = "copy",
        asset_folders: bool = True,
        dry_run: bool = False,
        use_temp_folder: bool = False,
        progress_callback: Optional[WorkflowProgressCallback] = None,
        target_media_type: Optional[str] = None,
        target_title: Optional[str] = None,
        target_year: Optional[int] = None,
        target_tmdb_id: Optional[int] = None,
        target_tvdb_id: Optional[int] = None,
        target_imdb_id: Optional[str] = None,
        target_season_number: Optional[int] = None,
        library_setting_key: str = "poster_renamer_libraries",
        media_dict: Optional[MediaDict] = None,
        artwork_boxes: Optional[List[Dict[str, Any]]] = None,
        artwork_slot_filter: Optional[set] = None,
    ) -> Dict[str, Any]:
        """
        Main method to rename posters.

        artwork_boxes: scanned artwork items, merged into the poster asset list so ONE match
        covers an item's poster and its logo/background/square (they're slots on the same box).

        media_dict: pre-fetched media to reuse (the merged Asset Renamer fetches once and
        injects it into both passes); when None, this method fetches it itself.
        
        Args:
            source_dirs: List of source directories containing posters.
            destination_dir: Destination directory for renamed posters.
            action_type: File operation type.
            asset_folders: Whether to use asset folders.
            dry_run: If True, simulate without making changes.
            use_temp_folder: If True, use 'tmp' subdirectory for workflow integration with border_replacer.
            progress_callback: Optional callback function(phase, current, total, message) for progress updates.
            
        Returns:
            Dictionary with results including output messages and stats.
        """
        try:
            # Log section start
            log_section_start(LogTags.RENAMER, "Poster Renamer Starting")
            
            # If using temp folder (for border_replacer workflow), create tmp subdirectory
            actual_destination = destination_dir
            if use_temp_folder:
                actual_destination = os.path.join(destination_dir, "tmp")
                log_info(LogTags.RENAMER, f"Using temp folder for border replacer workflow: {actual_destination}")
            
            # Create destination directory if it doesn't exist (parents included, so a
            # first run creates the destination itself, not just tmp/ inside it)
            if not os.path.exists(actual_destination):
                log_info(LogTags.RENAMER, f"Creating destination directory: {actual_destination}")
                if not dry_run:
                    try:
                        os.makedirs(actual_destination, exist_ok=True)
                    except OSError as e:
                        raise FileNotFoundError(
                            f"Could not create destination directory: {actual_destination} ({e})\n"
                            "Please ensure the destination folder is mounted correctly in Docker."
                        ) from e
            
            if dry_run:
                log_info(LogTags.RENAMER, "DRY RUN - NO CHANGES WILL BE MADE")
            
            # Phase 1: Gather poster assets (15-30%). The merged Asset Renamer job spends
            # 0-15% on its media fetch + artwork scan before this method is entered.
            if progress_callback:
                progress_callback("gathering", 15, 100, "Gathering poster files...")

            def _gather_progress(dir_index: int, dir_total: int, dir_name: str) -> None:
                if progress_callback and dir_total > 0:
                    progress = 15 + int((dir_index / dir_total) * 15)
                    progress_callback("gathering", progress, 100, f"Gathering posters: {dir_name}")

            log_info(LogTags.RENAMER, "Gathering all the posters, please wait...")
            assets_dict, prefix_index = get_assets_files(source_dirs, per_dir_callback=_gather_progress)
            
            if not assets_dict:
                return {
                    "success": False,
                    "error": "No assets found in the source directories"
                }

            if target_media_type or target_title or target_tmdb_id or target_tvdb_id or target_imdb_id:
                assets_dict, prefix_index = self._filter_assets_for_target(
                    assets_dict,
                    target_media_type=target_media_type,
                    target_title=target_title,
                    target_year=target_year,
                    target_tmdb_id=target_tmdb_id,
                    target_tvdb_id=target_tvdb_id,
                    target_imdb_id=target_imdb_id,
                    target_season_number=target_season_number,
                )

                if not assets_dict:
                    return {
                        "success": False,
                        "error": "No source assets matched webhook target",
                    }

                log_info(
                    LogTags.RENAMER,
                    f"Webhook-targeted rename: narrowed scope to {len(assets_dict)} asset group(s)",
                    target_media_type=target_media_type,
                    target_title=target_title,
                    target_year=target_year,
                    target_season_number=target_season_number,
                )

            # Artwork joins the SAME asset list and index, so match_assets_to_media runs once
            # and a matched item's box carries its poster, seasons AND artwork slots. Artwork
            # boxes are type-less and fill disjoint slots, so they never contest a poster.
            if artwork_boxes:
                if progress_callback:
                    progress_callback("merging", 30, 100, "Merging artwork into the poster index...")
                log_phase(LogTags.MERGE, f"Merging {len(artwork_boxes)} artwork item(s) into the poster index")
                for box in artwork_boxes:
                    # log_new=False: the artwork scan already logged each of these per item.
                    merge_assets([box], assets_dict, prefix_index, log_new=False)
                log_info(
                    LogTags.RENAMER,
                    f"Merged {len(artwork_boxes)} artwork item(s) into the asset index",
                    artwork_items=len(artwork_boxes),
                )


            # Phase 1.5: Prepare for processing (track all source files, let rename_files decide what to do)
            if progress_callback:
                progress_callback("preparing", 35, 100, "Preparing files for evaluation...")
            
            # Collect all source file paths - we'll check each one during rename
            all_source_files = set()
            for asset in assets_dict:
                all_source_files.update(asset['files'])
            
            total_files = len(all_source_files)
            log_info(LogTags.RENAMER, f"Found {total_files} source poster files", count=total_files)
            
            # Note: We don't filter assets_dict here based on DB status
            # Instead, we let rename_files check each destination file individually
            # This allows precise detection of missing/changed destination files
            
            # Phase 2: Get media from instances — only a real phase when media wasn't
            # injected (the merged Asset Renamer fetches before calling this method).
            if media_dict is None:
                if progress_callback:
                    progress_callback("fetching", 35, 100, "Fetching media from configured instances...")
                log_info(LogTags.RENAMER, "Fetching media from configured instances...")
                media_dict = self.get_media_from_instances(setting_key=library_setting_key)

            if not any(media_dict.values()):
                return {
                    "success": False,
                    "error": "No media found. Check Settings → Media tab to configure Plex/Radarr/Sonarr."
                }
            
            # Phase 3: Match assets to media (40-50%)
            if progress_callback:
                progress_callback("matching", 40, 100, "Matching assets to media...")
            
            log_info(LogTags.RENAMER, "Matching assets to media, please wait...")
            
            # Log details before matching for debugging
            media_counts = {k: len(v) for k, v in media_dict.items() if v}
            total_media = sum(media_counts.values())
            total_assets = len(assets_dict)
            
            log_info(
                LogTags.RENAMER,
                f"Matching {total_assets} assets against {total_media} media items "
                f"(movies: {media_counts.get('movies', 0)}, series: {media_counts.get('series', 0)}, "
                f"collections: {media_counts.get('collections', 0)})"
            )
            
            matched_assets = match_assets_to_media(
                media_dict,
                prefix_index,
                strict_folder_match=False,
                label="poster + artwork sources" if artwork_boxes else "poster sources",
            )
            
            if not matched_assets or not any(matched_assets.values()):
                # Log detailed diagnostic information
                log_error(LogTags.RENAMER, "No assets matched to media - diagnostic info:")
                log_error(LogTags.RENAMER, f"  Assets found: {total_assets}")
                log_error(LogTags.RENAMER, f"  Media items: {total_media} total")
                for media_type, count in media_counts.items():
                    log_error(LogTags.RENAMER, f"    - {media_type}: {count}")
                
                # Show sample assets and media for debugging
                if assets_dict and len(assets_dict) > 0:
                    sample_asset = assets_dict[0]
                    log_error(
                        LogTags.RENAMER,
                        f"  Sample asset: '{sample_asset.get('title', 'N/A')}' "
                        f"(year: {sample_asset.get('year', 'N/A')}, "
                        f"type: {sample_asset.get('type', 'N/A')}, "
                        f"tmdb: {sample_asset.get('tmdb_id', 'N/A')})"
                    )
                
                for media_type in ['movies', 'series', 'collections']:
                    if media_dict.get(media_type) and len(media_dict[media_type]) > 0:
                        sample_media = media_dict[media_type][0]
                        log_error(
                            LogTags.RENAMER,
                            f"  Sample {media_type}: '{sample_media.get('title', 'N/A')}' "
                            f"(year: {sample_media.get('year', 'N/A')}, "
                            f"tmdb: {sample_media.get('tmdb_id', 'N/A')}, "
                            f"folder: {sample_media.get('folder', 'N/A')})"
                        )
                        break
                
                return {
                    "success": False,
                    "error": f"No assets matched to media. Found {total_assets} assets and {total_media} media items but no matches. Check logs for details."
                }
            
            # Phase 4: Rename files (50-100%)
            if progress_callback:
                progress_callback("renaming", 50, 100, "Renaming and organizing files...")
            
            # Create nested callback for rename progress
            def rename_progress(current: int, total: int, status_message: str) -> None:
                if progress_callback:
                    # Map 0-total to 50-100%
                    progress = 50 + int((current / total) * 50)
                    progress_callback("renaming", progress, 100, status_message)
            
            output, renamed_files, processed_source_files, winning_source_files, artwork_renamed = self.rename_files(
                matched_assets,
                actual_destination,  # Use actual_destination (may be tmp folder)
                action_type,
                asset_folders,
                dry_run,
                progress_callback=rename_progress,
                artwork_destination=destination_dir,  # real dest, so artwork bypasses tmp/ + border
                artwork_slot_filter=artwork_slot_filter,
            )
            
            # Log what actually happened
            files_copied = len(renamed_files)
            files_skipped = len(processed_source_files) - files_copied
            log_info(
                LogTags.RENAMER,
                f"Results: {files_copied} files copied/updated, {files_skipped} already in place",
                copied=files_copied, skipped=files_skipped
            )
            
            # Calculate stats
            total_renamed = sum(len(items) for items in output.values())
            
            # "artwork" = files actually written this run, so it means the same thing as the
            # poster counts (which only include items that changed). The resolved-slot total —
            # how much artwork is matched for the library — is reported separately; conflating
            # the two made a no-op run claim it had placed the whole library.
            artwork_matched = sum(
                len(it.get("_artwork_slots") or {})
                for atype in matched_assets
                for it in matched_assets[atype]
            )
            stats = {
                "total_assets": len(assets_dict),
                "total_media": sum(len(v) for v in media_dict.values()),
                "total_matched": total_renamed,
                "movies": len(output.get("movies", [])),
                "series": len(output.get("series", [])),
                "collections": len(output.get("collections", [])),
                "artwork": artwork_renamed["total"],
                "artwork_by_media": artwork_renamed["by_media"],
                "artwork_by_type": artwork_renamed["by_type"],
                "artwork_matched": artwork_matched,
            }
            
            # Build per-style breakdown from winning source files
            from models.poster import Poster
            from models.drive import Drive
            from datetime import datetime, timezone

            # Drive lookup by source-path prefix (longest wins) — shared by the style
            # breakdown and the processed-marking below. Replaces per-file joins against
            # the poster index's stored paths, which missed whenever a drive's rows were
            # absent or spelled differently than the scan (styles binned as "Unknown",
            # rows left forever unprocessed).
            drives_by_prefix: List[tuple] = []
            for drive in self.db.query(Drive).all():
                prefix = str(drive.get_local_path(validate=False)).rstrip(os.sep) + os.sep
                drives_by_prefix.append((prefix, drive))
            drives_by_prefix.sort(key=lambda entry: (-len(entry[0]), entry[0]))

            def _drive_of(path: str):
                for prefix, mapped_drive in drives_by_prefix:
                    if path.startswith(prefix):
                        return mapped_drive
                return None

            style_counts: Dict[str, int] = {}
            style_fallbacks: Dict[str, List[Dict]] = {}  # style -> list of {title, year, type, season}

            if winning_source_files:
                for dest_path, entry in winning_source_files.items():
                    src_file, title, year, tmdb_type, season, f_tmdb_id, f_tvdb_id, f_imdb_id, f_poster_url, f_available = entry
                    src_drive = _drive_of(str(src_file))
                    style = (src_drive.style_type or "Unknown") if src_drive else "Unknown"

                    style_counts[style] = style_counts.get(style, 0) + 1
                    style_fallbacks.setdefault(style, []).append({
                        "title": title,
                        "year": year,
                        "type": tmdb_type,
                        "season": season,
                        "tmdb_id": f_tmdb_id,
                        "tvdb_id": f_tvdb_id,
                        "imdb_id": f_imdb_id,
                        "poster_url": f_poster_url,
                        "available": f_available,
                    })

            stats["style_counts"] = style_counts
            stats["style_fallbacks"] = style_fallbacks

            # Phase 6: Mark files as processed in DB (if not dry run)
            if not dry_run and processed_source_files:
                if progress_callback:
                    progress_callback("finalizing", 95, 100, "Updating processing status...")
                
                log_info(
                    LogTags.RENAMER,
                    f"Marking {len(processed_source_files)} source files as processed...",
                    count=len(processed_source_files)
                )
                
                now = datetime.now(timezone.utc)
                marked_count = 0
                drives_updated = set()

                # Mark source files that were actually processed (copied or verified as
                # identical) — stamped by (drive, file name), the sync engine's own row
                # identity, batched per drive. The old per-file stored-path lookup missed
                # whenever the index's spelling drifted from the scan's.
                names_by_drive: Dict[str, set] = {}
                for file_path in processed_source_files:
                    src_drive = _drive_of(str(file_path))
                    if src_drive:
                        names_by_drive.setdefault(src_drive.drive_id, set()).add(os.path.basename(file_path))

                for drive_id, names in names_by_drive.items():
                    name_list = sorted(names)
                    drive_marked = 0
                    # Chunked IN() to stay under SQLite's bound-parameter limit.
                    for start in range(0, len(name_list), 500):
                        drive_marked += (
                            self.db.query(Poster)
                            .filter(Poster.drive_id == drive_id, Poster.file_name.in_(name_list[start:start + 500]))
                            .update({"last_processed": now}, synchronize_session=False)
                        )
                    if drive_marked:
                        marked_count += drive_marked
                        drives_updated.add(drive_id)

                self.db.commit()
                
                # Update drive stats
                for drive_id in drives_updated:
                    drive = self.db.query(Drive).filter(Drive.drive_id == drive_id).first()
                    if drive:
                        drive.last_rename_processed = now
                
                self.db.commit()
                
                log_success(
                    LogTags.RENAMER,
                    f"Marked {marked_count} files as processed across {len(drives_updated)} drives",
                    marked=marked_count, drives=len(drives_updated)
                )
            
            # Log completion
            mode = "DRY RUN" if dry_run else action_type.upper()
            log_success(
                LogTags.RENAMER,
                f"Completed: {stats['total_matched']} matched "
                f"(Movies: {stats['movies']}, Series: {stats['series']}, Collections: {stats['collections']}) - Mode: {mode}",
                matched=stats['total_matched'], movies=stats['movies'], 
                series=stats['series'], collections=stats['collections'], mode=mode
            )
            
            log_section_end(LogTags.RENAMER, "Poster Renamer Complete")
            
            # The match verdicts, for cleanup: which artwork types / poster slots the drives
            # source per item. Handing this across means one library match per run and no
            # place/prune disagreement.
            from services.artwork_scan import sourced_types_from_matched
            return {
                "success": True,
                "output": output,
                "renamed_files": renamed_files,
                "stats": stats,
                "dry_run": dry_run,
                "destination_dir": actual_destination,
                "using_temp_folder": use_temp_folder,
                "poster_sourced": sourced_poster_slots_from_matched(matched_assets),
                "artwork_sourced": sourced_types_from_matched(matched_assets) if artwork_boxes else {},
            }
            
        except Exception as e:
            # Provide detailed error context
            error_msg = f"{type(e).__name__}: {str(e)}"
            error_detail = traceback.format_exc()
            log_error(LogTags.RENAMER, f"Error during poster renaming: {error_msg}\n{error_detail}")
            log_section_end(LogTags.RENAMER, "Poster Renamer Failed")
            return {
                "success": False,
                "error": error_msg
            }
