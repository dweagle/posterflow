import datetime
import os
from typing import Any, Dict, List, Optional, Tuple

from core.logging import log_debug, log_warning, LogTags, logger
from util.posters.index import build_search_index, create_new_empty_index, search_matches
from util.posters.match import is_match
from util.data.normalization import normalize_file_names
from util.posters.scanner import process_files


def get_assets_files(
    source_dirs: str | List[str],
    merge: bool = True,
) -> Tuple[Optional[List[Dict]], Optional[Dict[str, Any]]]:
    """Process one or more directories to extract and organize media assets.

    Args:
        source_dirs (str or List[str]): One or more paths to media source directories.
        merge (bool): Whether to merge/deduplicate assets by content and title.

    Returns:
        Tuple[Optional[List[Dict]], Optional[Dict[str, Any]]]: A tuple containing a flat
            asset list and a search index.
    """
    if isinstance(source_dirs, str):
        source_dirs = [source_dirs]

    final_assets: List[Dict] = []
    prefix_index: Dict[str, Any] = create_new_empty_index()

    start_time = datetime.datetime.now()

    for source_dir in source_dirs:
        new_assets = process_files(source_dir)
        if new_assets:
            if merge:
                merge_assets(new_assets, final_assets, prefix_index)
            else:
                for asset in new_assets:
                    asset["files"].sort()
                    final_assets.append(asset)
                    build_search_index(prefix_index, asset["title"], asset)

    end_time = datetime.datetime.now()
    elapsed_time = (end_time - start_time).total_seconds()
    items_per_second = len(source_dirs) / elapsed_time if elapsed_time > 0 else 0
    log_debug(LogTags.SCANNER, 
            f"Processed {len(source_dirs)} source directories in {elapsed_time:.2f} seconds "
            f"({items_per_second:.2f} items/s)",
            dir_count=len(source_dirs), elapsed_sec=f"{elapsed_time:.2f}", items_per_sec=f"{items_per_second:.2f}")

    if not final_assets:
        log_warning(LogTags.SCANNER, 
                f"No valid files were found in any of the source directories: {source_dirs}",
                source_dirs=source_dirs)
        return None, None

    return final_assets, prefix_index


def merge_assets(
    new_assets: List[Dict], final_assets: List[Dict], prefix_index: Dict
) -> None:
    """Merge new asset entries into the final asset list, collapsing duplicates,
    handling upgrades, and indexing.

    Args:
        new_assets (List[Dict]): List of new asset dictionaries.
        final_assets (List[Dict]): List to append/merge assets into.
        prefix_index (Dict): Index for fast search/lookup.
    """
    for new in new_assets:
        search_matched_assets = search_matches(prefix_index, new["title"])

        # Supplement with ID-based search to catch cases where title prefix differs
        # (e.g., "Ready or Not 2 Here I Come" vs "Ready or Not Here I Come" with same TMDB ID).
        # Filter to same type only — TMDB uses separate namespaces for movies and TV shows but
        # stores them as plain integers, so the same number can exist for both a movie and a series.
        new_tmdb_id = new.get("tmdb_id")
        new_tvdb_id = new.get("tvdb_id")
        new_type = new.get("type")
        id_candidates = []
        if new_tmdb_id:
            id_candidates = [
                a for a in search_matches(prefix_index, new["title"], tmdb_id=new_tmdb_id)
                if a.get("type") == new_type
            ]
        elif new_tvdb_id:
            id_candidates = [
                a for a in search_matches(prefix_index, new["title"], tvdb_id=new_tvdb_id)
                if a.get("type") == new_type
            ]
        if id_candidates:
            seen_ids = {id(a) for a in id_candidates}
            search_matched_assets = id_candidates + [a for a in search_matched_assets if id(a) not in seen_ids]

        merged = False
        
        for final in search_matched_assets:
            # Check if assets are from different directories
            new_dirs = {os.path.dirname(f) for f in new["files"]}
            final_dirs = {os.path.dirname(f) for f in final["files"]}
            if new_dirs & final_dirs:
                continue

            is_matched, reason = is_match(final, new)
            if is_matched and (
                final["type"] == new["type"]
                or final.get("season_numbers")
                or new.get("season_numbers")
            ):
                if new.get("season_numbers") or final.get("season_numbers"):
                    final["type"] = "series"
                
                # Track files before merge for logging
                pre_files = list(final["files"])
                pre_file_count = len(pre_files)
                
                # Merge files and track changes
                # Priority: Keep existing files (from higher priority drives), only add new unique files
                for new_file in new["files"]:
                    normalized_new_file = normalize_file_names(
                        os.path.basename(new_file)
                    )
                    found_match = False
                    
                    for final_file in final["files"]:
                        normalized_final_file = normalize_file_names(
                            os.path.basename(final_file)
                        )
                        if normalized_final_file == normalized_new_file:
                            # File already exists from higher priority drive - keep it, skip the new one
                            found_match = True
                            break
                    
                    if not found_match:
                        # New unique file - add it
                        final["files"].append(new_file)

                # Merge season numbers for series
                new_season_numbers = new.get("season_numbers")
                if new_season_numbers:
                    final_season_numbers = final.get("season_numbers")
                    if final_season_numbers:
                        final["season_numbers"] = list(
                            set(final_season_numbers + new_season_numbers)
                        )
                    else:
                        final["season_numbers"] = new_season_numbers
                
                final["files"].sort()
                post_files = list(final["files"])
                post_file_count = len(post_files)
                
                # Merge IDs
                for key in ["tmdb_id", "tvdb_id", "imdb_id"]:
                    if not final.get(key) and new.get(key):
                        final[key] = new[key]
                
                # Build detailed debug output
                src_parent = os.path.basename(os.path.dirname(new["files"][0]))
                reason_str = f"  Reason: {reason}."
                files_str = f"  Files: {pre_file_count} → {post_file_count}"
                
                # Track what changed
                pre_basenames = {os.path.basename(f): f for f in pre_files}
                post_basenames = {os.path.basename(f): f for f in post_files}
                new_basenames = {os.path.basename(f): f for f in new["files"]}
                
                upgrade_lines = []
                kept_count = 0
                kept_from_drive = None
                
                # Check for files that were kept (existed in pre, still exists in post, but new drive had a different version)
                for pre_base, pre_full in pre_basenames.items():
                    if pre_base in post_basenames:
                        post_full = post_basenames[pre_base]
                        if pre_base in new_basenames:
                            new_full = new_basenames[pre_base]
                            if pre_full == post_full and pre_full != new_full:
                                # Kept higher priority file, skipped lower priority
                                kept_count += 1
                                if not kept_from_drive:
                                    kept_from_drive = os.path.basename(os.path.dirname(pre_full))
                
                # Check for newly added files
                for post_base, post_full in post_basenames.items():
                    if post_base not in pre_basenames:
                        post_dir = os.path.basename(os.path.dirname(post_full))
                        upgrade_lines.append(
                            f"    - Added:    {post_base} [{post_dir}]"
                        )
                
                if kept_count > 0 and kept_from_drive:
                    upgrade_lines.insert(0, f"- Kept {kept_count} higher priority file(s) from [{kept_from_drive}],")
                    upgrade_lines.append(f"  not using poster from [{src_parent}]")
                
                # Format as separate log entries with indentation
                base_msg = f"'{final['title']}' ({final['type']}) from [{src_parent}]"
                
                # Main line with icon
                log_debug(
                    LogTags.SCANNER,
                    base_msg,
                    title=final['title'],
                    type=final['type'],
                    source=src_parent
                )
                
                # Details line without icon (custom format)
                logger.debug(f"[{LogTags.SCANNER:^15}]       {reason_str},   {files_str}")
                
                # Additional details (if any) with progressive indentation
                if upgrade_lines:
                    for line in upgrade_lines:
                        logger.debug(f"[{LogTags.SCANNER:^15}]         {line}")
                
                merged = True
                break
        
        if not merged:
            final_assets.append(new)
            build_search_index(prefix_index, new["title"], new)
            src_parent = os.path.basename(os.path.dirname(new["files"][0]))
            log_debug(LogTags.SCANNER,
                f"'{new['title']}' ({new['type']}), {len(new['files'])} file(s) from [{src_parent}]",
                title=new['title'],
                type=new['type'],
                file_count=len(new['files']),
                source=src_parent
            )
