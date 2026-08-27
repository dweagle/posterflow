import datetime
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging import log_debug, log_info, log_warning, log_phase, LogTags, logger
from util.posters.index import build_search_index, create_new_empty_index, search_matches
from util.posters.match import is_match
from util.data.construct import ITEM_SLOTS, SLOT_POSTER
from util.data.normalization import normalize_file_names
from util.posters.scanner import process_files


def get_assets_files(
    source_dirs: str | List[str],
    merge: bool = True,
    exclude_artwork: bool = False,
    artwork_out: Optional[Dict[str, List[str]]] = None,
    per_dir_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[Optional[List[Dict]], Optional[Dict[str, Any]]]:
    """Process one or more directories to extract and organize media assets.

    Args:
        source_dirs (str or List[str]): One or more paths to media source directories.
        merge (bool): Whether to merge/deduplicate assets by content and title.
        exclude_artwork (bool): Skip Artwork Renamer files (logo/background/squareart) so
            poster consumers scanning the shared destination don't count artwork as posters.
        artwork_out (dict|None): When given (keyed by artwork type), collect placed artwork
            item names during the same walk so one traversal feeds both poster and artwork
            indexes. Mutated in place — populated even when the poster result is empty.
        per_dir_callback: Optional callback(index, total, dir_name) fired before each
            source directory is scanned, for job progress reporting.

    Returns:
        Tuple[Optional[List[Dict]], Optional[Dict[str, Any]]]: A tuple containing a flat
            asset list and a search index.
    """
    if isinstance(source_dirs, str):
        source_dirs = [source_dirs]

    final_assets: List[Dict] = []
    prefix_index: Dict[str, Any] = create_new_empty_index()
    source_priority: Dict[str, int] = {sd: i for i, sd in enumerate(source_dirs)}

    start_time = datetime.datetime.now()

    log_phase(LogTags.SCANNER, f"Scanning {len(source_dirs)} poster source(s)")

    for dir_index, source_dir in enumerate(source_dirs):
        if per_dir_callback:
            per_dir_callback(dir_index, len(source_dirs), os.path.basename(str(source_dir).rstrip("/")))
        new_assets = process_files(source_dir, exclude_artwork=exclude_artwork, artwork_out=artwork_out)
        if new_assets:
            if merge:
                merge_assets(new_assets, final_assets, prefix_index, source_priority)
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


def _short_name(path: str) -> str:
    """A filename's distinguishing tail — everything after the last ' - '.

    Drives repeat the whole item identity in every file ("English Teacher (2024) {tmdb-258902}
    {tvdb-421968} {imdb-tt20782190} - logo.png"), so listing three of them restates the same
    ~70 characters three times. The log line already names the item, so only the suffix
    carries information: logo.png / background.jpg / Season 02.jpg.
    """
    base = os.path.basename(path)
    return base.rsplit(" - ", 1)[-1] if " - " in base else base


def _drive_of(path: str, source_priority: Optional[Dict[str, int]]) -> str:
    """The DRIVE a file came from, not its parent folder.

    ``dirname`` is the drive only for a flat layout. For artwork it's the type subfolder, so
    a merge read "from [backgrounds]"; for a nested poster drive it's the item folder.
    """
    if source_priority:
        for source_dir in source_priority:
            try:
                Path(path).relative_to(source_dir)
                return os.path.basename(str(source_dir).rstrip("/")) or str(source_dir)
            except ValueError:
                continue
    parent = os.path.dirname(path)
    # Artwork lives in a type subfolder under its drive — name the drive, not the subfolder.
    if os.path.basename(parent) in ("logos", "backgrounds", "squareart"):
        parent = os.path.dirname(parent)
    return os.path.basename(parent)


def drive_rank(path: str, source_priority: Optional[Dict[str, int]]) -> int:
    """Which drive a file came from, as a priority index (lower wins). Unknown paths sort last."""
    if not source_priority:
        return 0
    for source_dir, idx in source_priority.items():
        try:
            Path(path).relative_to(source_dir)
            return idx
        except ValueError:
            continue
    return len(source_priority)


def merge_slots(
    final: Dict[str, Any], new: Dict[str, Any],
    source_priority: Optional[Dict[str, int]] = None,
) -> None:
    """Merge ``new``'s slots into ``final`` PER SLOT, lowest drive rank winning each one.

    Slot-keyed rather than filename-keyed, because the two sides distinguish their files
    differently: poster files by filename (poster.jpg / Season01.jpg), artwork files by
    DIRECTORY (logos/ / backgrounds/ / squareart/) with an identical stem. A filename-keyed
    merge collapses an item's logo+background+square into one file, since
    ``normalize_file_names`` strips the extension.

    Per-slot (not per-box) so an item's logo can come from one drive and its square from
    another — the artwork priority rule.

    Slot losers are kept on the box as ``slot_runners`` (slot -> [paths], seasons nested)
    so the renamer's drive-usage stats can count matched-but-outranked files — poster and
    seasons feed the poster report, logo/background/square the artwork report.
    """
    def _adopt_runners() -> None:
        # Carry the incoming box's accumulated runner lists through the merge.
        new_runners = new.get("slot_runners")
        if not new_runners:
            return
        final_runners = final.setdefault("slot_runners", {})
        for slot, paths in new_runners.items():
            if slot == "seasons":
                final_seasons_r = final_runners.setdefault("seasons", {})
                for season, season_paths in (paths or {}).items():
                    bucket = final_seasons_r.setdefault(season, [])
                    bucket.extend(p for p in season_paths if p not in bucket)
            else:
                bucket = final_runners.setdefault(slot, [])
                bucket.extend(p for p in (paths or []) if p not in bucket)

    new_slots = new.get("slots")
    if not new_slots:
        return
    final_slots = final.get("slots")
    if not final_slots:
        final["slots"] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in new_slots.items()}
        _adopt_runners()
        return

    _adopt_runners()

    def _slot_runner(slot: str, path: str) -> None:
        final.setdefault("slot_runners", {}).setdefault(slot, []).append(path)

    def _season_runner(season, path: str) -> None:
        final.setdefault("slot_runners", {}).setdefault("seasons", {}).setdefault(season, []).append(path)

    for slot in ITEM_SLOTS:
        incoming = new_slots.get(slot)
        if not incoming:
            continue
        current = final_slots.get(slot)
        if not current or drive_rank(incoming, source_priority) < drive_rank(current, source_priority):
            if current:
                _slot_runner(slot, current)
            final_slots[slot] = incoming
        else:
            _slot_runner(slot, incoming)

    final_seasons = final_slots.setdefault("seasons", {})
    for season, incoming in (new_slots.get("seasons") or {}).items():
        current = final_seasons.get(season)
        if not current or drive_rank(incoming, source_priority) < drive_rank(current, source_priority):
            if current:
                _season_runner(season, current)
            final_seasons[season] = incoming
        else:
            _season_runner(season, incoming)


def merge_assets(
    new_assets: List[Dict], final_assets: List[Dict], prefix_index: Dict,
    source_priority: Optional[Dict[str, int]] = None,
    log_new: bool = True,
    tmdb_year_guard: bool = False,
) -> None:
    """Merge new asset entries into the final asset list, collapsing duplicates,
    handling upgrades, and indexing.

    Args:
        new_assets (List[Dict]): List of new asset dictionaries.
        final_assets (List[Dict]): List to append/merge assets into.
        prefix_index (Dict): Index for fast search/lookup.
        log_new (bool): Log a line per NEWLY appended (unmerged) asset. On by default because
            the poster scanner logs nothing per item, so this is a poster item's only record.
            Artwork callers pass False — the artwork scan already logs each item, with better
            detail (its slots and the real drive name).
        tmdb_year_guard (bool): Reject a match made on tmdb id ALONE when both sides carry
            years that disagree. Movie and TV tmdb ids share numbers, and a type-less
            artwork box with no tvdb tag counts as namespace-compatible with anything — the
            year is the only tiebreak. Artwork callers set this.
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
            if (
                is_matched
                and tmdb_year_guard
                and reason == "by tmdb_id"
                and new.get("year") and final.get("year")
                and new["year"] != final["year"]
            ):
                log_debug(
                    LogTags.SCANNER,
                    f"Year guard: not merging '{new['title']}' ({new['year']}) with "
                    f"'{final['title']}' ({final['year']}) on a tmdb id alone — "
                    f"movie/TV tmdb namespaces can share the number",
                    new_title=new["title"], new_year=new["year"],
                    existing_title=final["title"], existing_year=final["year"],
                )
                continue
            if is_matched and (
                # A type-less box is artwork — a filename can't say movie vs series, so it
                # fits whatever item it matched. Without this it only merged into a box with
                # season_numbers (series), so MOVIE and COLLECTION artwork never joined its
                # poster and was never placed.
                not final.get("type")
                or not new.get("type")
                or final["type"] == new["type"]
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
                
                # Slots merge per-slot by drive priority; files stay filename-keyed above.
                merge_slots(final, new, source_priority)

                if source_priority:
                    final["files"].sort(key=lambda f: (drive_rank(f, source_priority), f))
                else:
                    final["files"].sort()
                post_files = list(final["files"])
                post_file_count = len(post_files)
                
                # Merge IDs
                for key in ["tmdb_id", "tvdb_id", "imdb_id"]:
                    if not final.get(key) and new.get(key):
                        final[key] = new[key]
                
                # Header carries the reason (was its own line, alongside a "Files: N → N"
                # counter that read "1 → 1" exactly when a duplicate was skipped). What
                # changed follows on ONE detail line instead of being wrapped across two.
                src_parent = _drive_of(new["files"][0], source_priority)
                pre_basenames = {os.path.basename(f): f for f in pre_files}
                post_basenames = {os.path.basename(f): f for f in post_files}
                new_basenames = {os.path.basename(f): f for f in new["files"]}

                # Files the incoming drive also had, where the existing (higher priority) copy won.
                kept_names = []
                kept_from_drive = None
                for pre_base, pre_full in pre_basenames.items():
                    if pre_full == post_basenames.get(pre_base) and pre_base in new_basenames:
                        if pre_full != new_basenames[pre_base]:
                            kept_names.append(_short_name(pre_full))
                            if not kept_from_drive:
                                kept_from_drive = _drive_of(pre_full, source_priority)

                added = sorted(_short_name(p) for b, p in post_basenames.items() if b not in pre_basenames)

                # Say WHAT the incoming box carries. "(movies) - from [Drive]" read as "this
                # item's poster came from [Drive]" even when the box was artwork joining a
                # poster it never contested.
                incoming_slots = new.get("slots") or {}
                incoming_kind = (
                    "poster" if incoming_slots.get(SLOT_POSTER) or incoming_slots.get("seasons")
                    or not incoming_slots else "artwork"
                )
                year = f" ({final['year']})" if final.get("year") else ""
                log_debug(
                    LogTags.MERGE,
                    f"{final['title']}{year} ({final['type'] or 'artwork'}) - "
                    f"merged {incoming_kind} from [{src_parent}]: Reason {reason}",
                    title=final["title"], type=final["type"], source=src_parent, merged=incoming_kind,
                    files_before=pre_file_count, files_after=post_file_count, reason=reason,
                )

                detail = []
                if added:
                    detail.append(f"Added {len(added)} file(s) from [{src_parent}]: {', '.join(added)}")
                if kept_names and kept_from_drive:
                    # Name the files kept — the old line said "poster" for what was often a logo.
                    detail.append(
                        f"Kept {len(kept_names)} higher priority file(s) from [{kept_from_drive}] "
                        f"over [{src_parent}]: {', '.join(sorted(kept_names))}"
                    )
                if detail:
                    # No icon, indented under the header — same style as before.
                    logger.debug(f"[{LogTags.MERGE:^9}]         - {'; '.join(detail)}")

                merged = True
                break
        
        if not merged:
            final_assets.append(new)
            build_search_index(prefix_index, new["title"], new)
            if not log_new:
                continue
            src_parent = _drive_of(new["files"][0], source_priority)
            new_year = f" ({new['year']})" if new.get("year") else ""
            log_debug(LogTags.SCANNER,
                f"{new['title']}{new_year} ({new['type'] or 'artwork'}), "
                f"{len(new['files'])} file(s) from [{src_parent}]",
                title=new['title'],
                year=new.get('year'),
                type=new['type'],
                file_count=len(new['files']),
                source=src_parent
            )
