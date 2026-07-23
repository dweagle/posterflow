"""Service for detecting unmatched media (media without posters)."""

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from sqlalchemy.orm import Session

from core.logging import LogTags, log_section_start, log_section_end, log_success, log_error, log_warning, log_info, log_debug, log_step
from models.setting import get_setting, upsert_setting
from util.constants import season_pattern
from util.posters.assets import get_assets_files
from util.posters.scanner import _is_asset_folders
from util.posters.index import search_matches
from util.posters.match import (
    collection_title_variants, is_match, match_assets_to_media, media_source_refs,
)
from services.artwork_scan import (
    scan_destination_artwork, build_artwork_index, assets_from_names, ARTWORK_TYPES,
)


class UnmatchedAssetsService:
    """Service for finding media items that don't have matching poster assets."""

    ProgressCallback = Callable[[str, int, int, str], None]

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _format_percent(percent: float) -> str:
        """Format completion percentage with two decimal places for logs/UI parity."""
        return f"{percent:.2f}%"

    def _get_list_setting(self, key: str) -> List[str]:
        """Load a list setting from DB, supporting JSON arrays and newline/comma-separated strings."""
        setting = get_setting(self.db, key)
        if not setting or not setting.value:
            return []

        raw_value = setting.value.strip()
        if not raw_value:
            return []

        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            log_debug(LogTags.UNMATCHED, f"Invalid JSON for list setting '{key}', using delimiter parsing fallback")

        values = [item.strip() for item in re.split(r"[\n,]+", raw_value) if item.strip()]
        return values

    def _get_bool_setting(self, key: str, default: bool = False) -> bool:
        """Load a boolean setting from DB."""
        setting = get_setting(self.db, key)
        if not setting or setting.value is None:
            return default
        return str(setting.value).strip().lower() in {"true", "1", "yes", "on"}

    @staticmethod
    def _matches_ignored_root(root_folder: str | None, ignore_root_folders: List[str]) -> bool:
        """Return True if root folder matches ignored root folder names or paths."""
        if not root_folder or not ignore_root_folders:
            return False

        normalized_root = root_folder.rstrip("/")
        root_name = os.path.basename(normalized_root).lower()
        ignore_set = {entry.strip().rstrip("/").lower() for entry in ignore_root_folders if entry.strip()}
        normalized_location = normalized_root.lower()

        return root_name in ignore_set or normalized_location in ignore_set

    def _apply_unmatched_filters(
        self,
        media_dict: Dict[str, List[Dict[str, Any]]],
        ignore_root_folders: List[str],
        ignore_collections: List[str],
        ignore_unmonitored: bool,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Apply unmatched-assets-specific ignore filters to media payload."""
        filtered_media: Dict[str, List[Dict[str, Any]]] = {
            "movies": [],
            "series": [],
            "collections": [],
        }

        ignored_collection_names = {
            value.strip().lower() for value in ignore_collections if value.strip()
        }

        filtered_unmonitored_movies = 0
        filtered_unmonitored_series = 0
        filtered_root_movies = 0
        filtered_root_series = 0
        filtered_collections = 0
        ignored_by_reason: Dict[str, List[str]] = {
            "unmonitored_movies": [],
            "unmonitored_series": [],
            "root_movies": [],
            "root_series": [],
            "collections": [],
        }

        def build_item_label(item: Dict[str, Any], include_root: bool = False) -> str:
            title = str(item.get("title", "Unknown")).strip() or "Unknown"
            year = item.get("year")
            instance = item.get("instance")
            root_folder = item.get("root_folder") or item.get("folder")

            parts = [f"{title} ({year})" if year else title]
            if instance:
                parts.append(f"instance={instance}")
            if include_root and root_folder:
                parts.append(f"root={root_folder}")

            return " | ".join(parts)

        for movie in media_dict.get("movies", []):
            if ignore_unmonitored and movie.get("monitored") is False:
                filtered_unmonitored_movies += 1
                ignored_by_reason["unmonitored_movies"].append(build_item_label(movie))
                continue
            movie_root_folder = movie.get("root_folder") or movie.get("folder")
            if self._matches_ignored_root(movie_root_folder, ignore_root_folders):
                filtered_root_movies += 1
                ignored_by_reason["root_movies"].append(build_item_label(movie, include_root=True))
                continue
            filtered_media["movies"].append(movie)

        for series in media_dict.get("series", []):
            if ignore_unmonitored and series.get("monitored") is False:
                filtered_unmonitored_series += 1
                ignored_by_reason["unmonitored_series"].append(build_item_label(series))
                continue
            series_root_folder = series.get("root_folder") or series.get("folder")
            if self._matches_ignored_root(series_root_folder, ignore_root_folders):
                filtered_root_series += 1
                ignored_by_reason["root_series"].append(build_item_label(series, include_root=True))
                continue

            if ignore_unmonitored:
                original_seasons = series.get("seasons", [])
                monitored_seasons = [
                    season for season in original_seasons if season.get("monitored") is not False
                ]
                filtered_series = dict(series)
                filtered_series["seasons"] = monitored_seasons
                filtered_series["season_numbers"] = [
                    season.get("season_number") for season in monitored_seasons
                ]
                filtered_media["series"].append(filtered_series)
            else:
                filtered_media["series"].append(series)

        for collection in media_dict.get("collections", []):
            collection_title = str(collection.get("title", "")).strip().lower()
            if collection_title and collection_title in ignored_collection_names:
                filtered_collections += 1
                ignored_by_reason["collections"].append(build_item_label(collection))
                continue
            filtered_media["collections"].append(collection)

        if ignore_root_folders:
            log_info(
                LogTags.UNMATCHED,
                "Applied ignore root folders filter",
                ignored_roots=ignore_root_folders,
                skipped_movies=filtered_root_movies,
                skipped_series=filtered_root_series,
            )

        if ignore_collections:
            log_info(
                LogTags.UNMATCHED,
                "Applied ignore collections filter",
                ignored_collections=ignore_collections,
                skipped_collections=filtered_collections,
            )

        if ignore_unmonitored:
            log_info(
                LogTags.UNMATCHED,
                "Applied ignore unmonitored filter",
                skipped_movies=filtered_unmonitored_movies,
                skipped_series=filtered_unmonitored_series,
            )

        # Verbose debug details so users can inspect exactly what was ignored.
        # Keep previews bounded to avoid excessively large logs for big libraries.
        debug_preview_limit = 50
        ignored_total = sum(len(items) for items in ignored_by_reason.values())
        if ignored_total > 0:
            log_debug(
                LogTags.UNMATCHED,
                "Ignored-item debug summary",
                ignored_total=ignored_total,
                ignored_unmonitored_movies=len(ignored_by_reason["unmonitored_movies"]),
                ignored_unmonitored_series=len(ignored_by_reason["unmonitored_series"]),
                ignored_root_movies=len(ignored_by_reason["root_movies"]),
                ignored_root_series=len(ignored_by_reason["root_series"]),
                ignored_collections=len(ignored_by_reason["collections"]),
            )

            for reason, items in ignored_by_reason.items():
                if not items:
                    continue
                preview_items = items[:debug_preview_limit]
                omitted_count = max(0, len(items) - len(preview_items))
                log_debug(
                    LogTags.UNMATCHED,
                    f"Ignored details [{reason}]",
                    count=len(items),
                    preview=preview_items,
                    omitted=omitted_count,
                )

        return filtered_media

    def _build_stats_table_lines(self, stats: Dict[str, Any]) -> List[str]:
        """Build readable ASCII statistics table lines for unmatched detection logs."""
        rows: List[Tuple[str, int, int, str]] = [
            (
                "Movies",
                stats["movies"]["total"],
                stats["movies"]["unmatched"],
                self._format_percent(stats["movies"]["percent_complete"]),
            ),
            (
                "Series",
                stats["series"]["total"],
                stats["series"]["unmatched"],
                self._format_percent(stats["series"]["percent_complete"]),
            ),
            (
                "Seasons",
                stats["seasons"]["total"],
                stats["seasons"]["unmatched"],
                self._format_percent(stats["seasons"]["percent_complete"]),
            ),
            (
                "Collections",
                stats["collections"]["total"],
                stats["collections"]["unmatched"],
                self._format_percent(stats["collections"]["percent_complete"]),
            ),
            (
                "Grand Total",
                stats["grand_total"]["total"],
                stats["grand_total"]["unmatched"],
                self._format_percent(stats["grand_total"]["percent_complete"]),
            ),
        ]

        filtered_rows = rows[:-2] + [rows[-2], rows[-1]] if stats["collections"]["total"] > 0 else rows[:-2] + [rows[-1]]

        type_width = max(len("Type"), *(len(row[0]) for row in filtered_rows))
        total_width = max(len("Total"), *(len(f"{row[1]:,}") for row in filtered_rows))
        unmatched_width = max(len("Unmatched"), *(len(f"{row[2]:,}") for row in filtered_rows))
        percent_width = max(len("Percent Complete"), *(len(row[3]) for row in filtered_rows))

        def border() -> str:
            return (
                f"|{'-' * (type_width + 2)}"
                f"|{'-' * (total_width + 2)}"
                f"|{'-' * (unmatched_width + 2)}"
                f"|{'-' * (percent_width + 2)}|"
            )

        header = (
            f"| {'Type':<{type_width}} "
            f"| {'Total':>{total_width}} "
            f"| {'Unmatched':>{unmatched_width}} "
            f"| {'Percent Complete':>{percent_width}} |"
        )

        lines = [
            "Statistics",
            border(),
            header,
            border(),
        ]

        for label, total, unmatched, percent in filtered_rows:
            lines.append(
                f"| {label:<{type_width}} "
                f"| {total:>{total_width},} "
                f"| {unmatched:>{unmatched_width},} "
                f"| {percent:>{percent_width}} |"
            )
            lines.append(border())

        return lines

    def detect_unmatched(
        self,
        media_dict: Dict[str, List[Dict[str, Any]]],
        source_dirs: List[str],
        progress_callback: ProgressCallback | None = None,
        check_posters: bool = True,
        artwork_types: List[str] | None = None,
        asset_folders: bool = True,
    ) -> Dict[str, Any]:
        """Unmatched detection over the organized DESTINATION in ONE walk + ONE pass, scoped to
        the asset types the user enabled: posters if ``check_posters``, and only the
        ``artwork_types`` given (subset of logo/background/square). Returns the poster result
        (when check_posters) plus ``artwork`` (when any artwork_types)."""
        return self._detect_combined(media_dict, source_dirs, progress_callback, check_posters, artwork_types, asset_folders)

    def _match_poster_item(self, media: Dict[str, Any], media_type: str, prefix_index: Dict[str, Any],
                           unmatched: Dict[str, List[Dict[str, Any]]], match_stats: Dict[str, Any]) -> None:
        """Poster per-item match — moved verbatim from the old poster loop (logging unchanged).
        Assumes eligibility already passed; mutates ``unmatched`` / ``match_stats``."""
        match_stats[media_type]["processed"] += 1

        # Look for matching assets
        found = False
        match_method = None
        tmdb_id = media.get("tmdb_id")
        tvdb_id = media.get("tvdb_id")

        # Try ID-based search first
        if tmdb_id or tvdb_id:
            try:
                id_assets = search_matches(
                    prefix_index,
                    media.get("title", ""),
                    tmdb_id=tmdb_id,
                    tvdb_id=tvdb_id,
                )
            except Exception as e:
                log_error(LogTags.UNMATCHED, f"Search failed for '{media.get('title')}': {e}",
                         title=media.get('title'), error=str(e))
                id_assets = []

            # Verify ID bucket hits via is_match before trusting
            verified_id_asset = (
                next(
                    (ia for ia in id_assets if is_match(ia, media)[0]),
                    None,
                )
                if id_assets else None
            )
            if verified_id_asset:
                asset = verified_id_asset
                found = True
                match_method = f"ID ({tmdb_id or tvdb_id})"

                # For series, check for missing seasons or main poster
                if media_type == "series":
                    media_seasons = [
                        s["season_number"]
                        for s in media.get("seasons", [])
                        if s.get("season_has_episodes")
                    ]
                    asset_seasons = asset.get("season_numbers", [])
                    missing_seasons = [
                        s for s in media_seasons if s not in asset_seasons
                    ]

                    # Check if main poster exists (no season number in filename)
                    has_main_poster = any(
                        not season_pattern.search(os.path.basename(f))
                        for f in asset.get("files", [])
                    )

                    if missing_seasons or not has_main_poster:
                        match_stats[media_type]["partial"] += 1
                        log_debug(
                            LogTags.UNMATCHED,
                            f"  ⚠ Partial match: {media['title']} ({media.get('year')}) - "
                            f"missing {len(missing_seasons)} seasons, main poster: {has_main_poster}",
                            title=media['title'],
                            missing_seasons=missing_seasons,
                            has_main=has_main_poster
                        )
                        unmatched[media_type].append({
                            "title": media.get("title"),
                            "year": media.get("year"),
                            "missing_seasons": missing_seasons,
                            "missing_main_poster": not has_main_poster,
                            "instance": media.get("instance", "Unknown"),
                            **media_source_refs(media),
                        })
                    else:
                        match_stats[media_type]["matched"] += 1
                        log_debug(
                            LogTags.UNMATCHED,
                            f"  ✓ Matched: {media['title']} ({media.get('year')}) via {match_method}",
                            title=media['title'],
                            year=media.get('year'),
                            method=match_method
                        )
                else:
                    # For movies and collections, simple match
                    match_stats[media_type]["matched"] += 1
                    log_debug(
                        LogTags.UNMATCHED,
                        f"  ✓ Matched: {media['title']} ({media.get('year')}) via {match_method}",
                        title=media['title'],
                        year=media.get('year'),
                        method=match_method
                    )

        # If no ID match, try title-based search
        if not found:
            base_title = media.get("title") or ""
            if media_type == "collections":
                titles_to_try = collection_title_variants(base_title) + media.get("alternate_titles", [])
            else:
                titles_to_try = [base_title] + media.get("alternate_titles", [])
            for title in titles_to_try:
                try:
                    candidates = search_matches(prefix_index, title)
                except Exception as e:
                    log_error(LogTags.UNMATCHED, f"Title search failed for '{title}': {e}",
                             title=title, error=str(e))
                    continue

                for candidate in candidates:
                    is_matched, reason = is_match(candidate, media)
                    if is_matched:
                        found = True
                        match_method = reason
                        log_debug(
                            LogTags.UNMATCHED,
                            f"  ✓ Matched {reason}: {media['title']} ({media.get('year')})",
                            title=media['title'],
                            year=media.get('year'),
                            method=reason
                        )

                        # For series, check seasons (following DAPS logic: only add if missing seasons)
                        if media_type == "series":
                            media_seasons = [
                                s["season_number"]
                                for s in media.get("seasons", [])
                                if s.get("season_has_episodes")
                            ]
                            asset_seasons = candidate.get("season_numbers", [])
                            missing_seasons = [
                                s for s in media_seasons if s not in asset_seasons
                            ]
                            # Following DAPS: only add to unmatched if there are missing seasons
                            if missing_seasons:
                                match_stats[media_type]["partial"] += 1
                                has_main_poster = any(
                                    not season_pattern.search(os.path.basename(f))
                                    for f in candidate.get("files", [])
                                )
                                log_debug(
                                    LogTags.UNMATCHED,
                                    f"  ⚠ Partial match: {media['title']} - missing {len(missing_seasons)} seasons",
                                    missing_seasons=missing_seasons
                                )
                                unmatched[media_type].append({
                                    "title": media.get("title"),
                                    "year": media.get("year"),
                                    "missing_seasons": missing_seasons,
                                    "missing_main_poster": not has_main_poster,
                                    "instance": media.get("instance", "Unknown"),
                                    **media_source_refs(media),
                                })
                            else:
                                match_stats[media_type]["matched"] += 1
                        else:
                            match_stats[media_type]["matched"] += 1
                        break
                if found:
                    break

        # If still not found, add to unmatched
        if not found:
            match_stats[media_type]["unmatched"] += 1
            log_debug(
                LogTags.UNMATCHED,
                f"  ✗ No match: {media['title']} ({media.get('year')})",
                title=media['title'],
                year=media.get('year'),
                instance=media.get('instance')
            )
            if media_type == "series":
                unmatched[media_type].append({
                    "title": media.get("title"),
                    "year": media.get("year"),
                    "missing_seasons": [
                        s["season_number"]
                        for s in media.get("seasons", [])
                        if s.get("season_has_episodes")
                    ],
                    "missing_main_poster": True,
                    "instance": media.get("instance", "Unknown"),
                    **media_source_refs(media),
                })
            else:
                unmatched[media_type].append({
                    "title": media.get("title"),
                    "year": media.get("year"),
                    "instance": media.get("instance", "Unknown"),
                    **media_source_refs(media),
                })

    def _match_artwork_item(self, media: Dict[str, Any], media_type: str, matches: Dict[int, str],
                            unmatched: Dict[str, List[Dict[str, Any]]], stats: Dict[str, int]) -> tuple:
        """Artwork per-item match for ONE type; mutates ``unmatched``/``stats`` (no per-item log —
        the driver logs the combined artwork line). Returns ``(matched, reason)``.

        ``matches`` is the pre-computed {id(media): reason} from the shared matcher, so artwork
        matches exactly the way posters do instead of through a parallel implementation."""
        reason = matches.get(id(media))
        asset = reason is not None
        if asset:
            stats["matched"] += 1
            return True, reason
        stats["unmatched"] += 1
        entry = {
            "title": media.get("title"),
            "year": media.get("year"),
            "instance": media.get("instance", "Unknown"),
            **media_source_refs(media),
        }
        if media_type == "series":
            # No season concept for artwork: the whole show is the "main".
            entry["missing_seasons"] = []
            entry["missing_main_poster"] = True
        unmatched[media_type].append(entry)
        return False, reason

    def _artwork_item_line(self, media: Dict[str, Any], matched_types: List[str], missing_types: List[str],
                           reason: str | None, compact: bool) -> str:
        """One artwork line per item. Compact (indented, no title) when a poster line is above it;
        self-contained (title + via-reason) for an artwork-only run."""
        matched_str = ", ".join(matched_types) if matched_types else "none"
        missing_str = ", ".join(missing_types) if missing_types else "none"
        if compact:
            return f"      ↳ artwork — matched: {matched_str} · missing: {missing_str}"
        title = media.get("title") or "unknown"
        year = f" ({media['year']})" if isinstance(media.get("year"), int) else ""
        # Shared-matcher reasons read "by tmdb_id"; "via by tmdb_id" is clumsy.
        via = f" via {reason.removeprefix('by ')}" if reason else ""
        return f"{title}{year}{via} — matched: {matched_str} · missing: {missing_str}"

    def _build_combined_stats_table_lines(self, summaries: Dict[str, Dict[str, Any]]) -> List[str]:
        """One ASCII stats table with a column per asset type (same box style as the poster
        table). ``summaries`` maps a column label (e.g. 'Poster'/'Logo') -> that type's summary.
        Cells are ``complete/total``; the Seasons row is poster-only (``—`` for artwork)."""
        columns = list(summaries.keys())
        has_poster = "Poster" in columns
        any_collections = any(s.get("collections", {}).get("total", 0) > 0 for s in summaries.values())
        row_defs = [("movies", "Movies"), ("series", "Series"), ("seasons", "Seasons"),
                    ("collections", "Collections"), ("grand_total", "Grand Total")]

        def cell(col: str, key: str) -> str:
            if key == "seasons" and col != "Poster":
                return "—"
            sec = summaries[col].get(key) or {}
            total = int(sec.get("total", 0))
            unm = int(sec.get("unmatched", 0))
            return f"{total - unm:,}/{total:,}"

        rows: List[Tuple[str, List[str]]] = []
        for key, label in row_defs:
            if key == "collections" and not any_collections:
                continue
            if key == "seasons" and not has_poster:
                continue
            rows.append((label, [cell(col, key) for col in columns]))

        # Overall completion per asset type, closing out the table.
        rows.append((
            "Percent",
            [self._format_percent(float((summaries[col].get("grand_total") or {}).get("percent_complete", 100.0)))
             for col in columns],
        ))

        type_width = max([len("Type")] + [len(r[0]) for r in rows])
        col_widths = [max([len(col)] + [len(r[1][i]) for r in rows]) for i, col in enumerate(columns)]

        def border() -> str:
            return "|" + f"{'-' * (type_width + 2)}|" + "".join(f"{'-' * (w + 2)}|" for w in col_widths)

        header = f"| {'Type':<{type_width}} " + "".join(f"| {col:>{w}} " for col, w in zip(columns, col_widths)) + "|"
        lines = ["Statistics", border(), header, border()]
        for label, cells in rows:
            lines.append(f"| {label:<{type_width}} " + "".join(f"| {c:>{w}} " for c, w in zip(cells, col_widths)) + "|")
            lines.append(border())
        return lines

    def _log_combined_by_library(self, summaries: Dict[str, Dict[str, Any]]) -> None:
        """One 'Statistics by library instance' block spanning all asset-type columns."""
        columns = list(summaries.keys())
        order: Dict[str, Dict[str, Any]] = {}
        for col in columns:
            for inst, inst_stats in (summaries[col].get("by_library") or {}).items():
                order.setdefault(inst, inst_stats)
        if not order:
            return
        log_info(LogTags.UNMATCHED, "Statistics by library instance:")
        for inst, inst_stats in order.items():
            for media_type in ["movies", "series", "collections"]:
                if media_type not in inst_stats:
                    continue
                segs = []
                for col in columns:
                    ts = (summaries[col].get("by_library") or {}).get(inst, {}).get(media_type)
                    if ts:
                        segs.append(f"{col.lower()} {ts['total'] - ts['unmatched']}/{ts['total']}")
                if segs:
                    log_info(LogTags.UNMATCHED, f"  {inst} ({media_type}): " + " · ".join(segs))

    def _detect_combined(
        self,
        media_dict: Dict[str, List[Dict[str, Any]]],
        source_dirs: List[str],
        progress_callback: ProgressCallback | None = None,
        check_posters: bool = True,
        artwork_types: List[str] | None = None,
        asset_folders: bool = True,
    ) -> Dict[str, Any]:
        """One destination walk + one library pass covering posters and/or artwork. Poster
        results and per-artwork-type results are computed and stored exactly as before; only the
        scan plumbing and per-item/aggregate logging are unified."""
        start_time = time.time()
        enabled_art = [t for t in ARTWORK_TYPES if t in (artwork_types or [])]
        destination_dir = source_dirs[0] if source_dirs else ""

        try:
            if check_posters:
                log_section_start(LogTags.UNMATCHED, "Unmatched Assets Detection Starting")
            log_info(
                LogTags.UNMATCHED,
                "Starting unmatched detection",
                source_dirs=source_dirs,
                check_posters=check_posters,
                artwork_types=enabled_art,
                media_counts={k: len(v) for k, v in media_dict.items()},
            )

            ignore_root_folders = self._get_list_setting("unmatched_ignore_root_folders")
            ignore_collections = self._get_list_setting("unmatched_ignore_collections")
            ignore_unmonitored = self._get_bool_setting("unmatched_ignore_unmonitored", default=False)
            media_dict = self._apply_unmatched_filters(
                media_dict,
                ignore_root_folders=ignore_root_folders,
                ignore_collections=ignore_collections,
                ignore_unmonitored=ignore_unmonitored,
            )
            log_info(
                LogTags.UNMATCHED,
                "Unmatched filters loaded",
                ignore_root_folders=ignore_root_folders,
                ignore_collections=ignore_collections,
                ignore_unmonitored=ignore_unmonitored,
                filtered_media_counts={k: len(v) for k, v in media_dict.items()},
            )

            # --- One walk ---
            # both  → poster walk (get_assets_files) also collects artwork names (single traversal)
            # posters-only → poster walk, no artwork
            # artwork-only → scan_destination_artwork (its own single walk); no poster walk
            if progress_callback:
                progress_callback("scanning", 0, 100, "Scanning destination assets...")
            log_step(LogTags.UNMATCHED, 1, 2, "Scanning destination assets...")
            poster_assets: List[Dict[str, Any]] = []
            prefix_index: Dict[str, Any] | None = None
            names: Dict[str, List[str]] | None = {t: [] for t in ARTWORK_TYPES} if (enabled_art and check_posters) else None
            try:
                if check_posters:
                    poster_assets, prefix_index = get_assets_files(
                        source_dirs, merge=False, exclude_artwork=True, artwork_out=names,
                    )
                    poster_assets = poster_assets or []
            except Exception as e:
                log_error(LogTags.UNMATCHED, f"Failed to scan destination assets: {e}", source_dirs=source_dirs, error=str(e))
                if progress_callback:
                    progress_callback("error", 0, 100, f"Scan failed: {e}")
                return self._combined_failure(check_posters, bool(enabled_art))

            poster_match_active = check_posters and bool(poster_assets)

            # Artwork indexes: from the shared walk when posters ran (fallback re-scan on structure
            # mismatch), else its own walk for an artwork-only run.
            artwork_indexes: Dict[str, Any] = {}
            artwork_unmatched: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            artwork_log_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
            artwork_by_type: Dict[str, List[Dict[str, Any]]] = {}
            if enabled_art:
                if names is not None and _is_asset_folders(destination_dir) == asset_folders:
                    artwork_by_type = assets_from_names(names)
                else:
                    artwork_by_type = scan_destination_artwork(destination_dir, asset_folders)
                # ONE combined index over every placed type (an item's logo/background/square
                # share its identity, so one box carries which types are placed) and ONE match
                # through the SAME matcher posters use — not one full-library pass per type.
                _combined: Dict[str, Dict[str, Any]] = {}
                for t in enabled_art:
                    for it in artwork_by_type.get(t, []):
                        key = "|".join(str(it.get(k)) for k in ("normalized_title", "year", "tmdb_id", "tvdb_id", "imdb_id"))
                        box = _combined.setdefault(key, {**it, "_placed_types": set()})
                        box["_placed_types"].add(t)
                _idx = build_artwork_index(list(_combined.values()))
                _matched_placed = match_assets_to_media(media_dict, _idx, label="placed artwork", report_near_misses=False)
                for t in enabled_art:
                    artwork_indexes[t] = {
                        id(it["media_ref"]): (it.get("match_reason") or "")
                        for items in _matched_placed.values()
                        for it in items
                        if it.get("media_ref") is not None
                        and t in ((it.get("asset_ref") or {}).get("_placed_types") or set())
                    }
                    artwork_unmatched[t] = {"movies": [], "series": [], "collections": []}
                    artwork_log_stats[t] = {mt: {"matched": 0, "unmatched": 0, "skipped": 0} for mt in ["movies", "series", "collections"]}

            if check_posters:
                if poster_assets:
                    log_success(LogTags.UNMATCHED, f"Found {len(poster_assets):,} organized poster assets", count=len(poster_assets))
                else:
                    log_warning(LogTags.UNMATCHED, "No poster assets found in destination folder")
            if enabled_art:
                total_art = sum(len(artwork_by_type.get(t, [])) for t in enabled_art)
                log_success(LogTags.UNMATCHED, f"Found {total_art:,} artwork asset(s) on disk across {len(enabled_art)} type(s)", count=total_art)

            unmatched: Dict[str, List[Dict[str, Any]]] = {"movies": [], "series": [], "collections": []}
            match_stats = {
                "movies": {"processed": 0, "matched": 0, "unmatched": 0, "skipped": 0},
                "series": {"processed": 0, "matched": 0, "unmatched": 0, "partial": 0, "skipped": 0},
                "collections": {"processed": 0, "matched": 0, "unmatched": 0, "skipped": 0},
            }

            # --- One pass over the library ---
            run_loop = poster_match_active or bool(enabled_art)
            if run_loop:
                if progress_callback:
                    progress_callback("matching", 20, 100, "Comparing media to destination assets...")
                log_step(LogTags.UNMATCHED, 2, 2, "Comparing media to destination assets...")
                total_media_items = sum(len(media_dict.get(mt, [])) for mt in ["movies", "series", "collections"])
                processed_count = 0
                for media_type in ["movies", "series", "collections"]:
                    type_start = time.time()
                    # Process/log items alphabetically by title (matches the scanner's ordering).
                    media_list = sorted(media_dict.get(media_type, []), key=lambda m: (m.get("title") or "").lower())
                    if not media_list:
                        log_info(LogTags.UNMATCHED, f"  {media_type.title()}: No items to process - skipping")
                        continue
                    log_info(LogTags.UNMATCHED, f"  Processing {media_type}: {len(media_list):,} items...")

                    for idx, media in enumerate(media_list, 1):
                        processed_count += 1
                        if progress_callback and (processed_count % 10 == 0 or processed_count == total_media_items):
                            progress_pct = 20 + int((processed_count / total_media_items) * 70)
                            progress_callback("matching", progress_pct, 100, f"Matching {media_type}: {idx}/{len(media_list)}")

                        if not media.get("title"):
                            if poster_match_active:
                                match_stats[media_type]["skipped"] += 1
                            for t in enabled_art:
                                artwork_log_stats[t][media_type]["skipped"] += 1
                            continue

                        if media_type in ["series", "movies"] and not self._should_have_poster(media, media_type):
                            if poster_match_active:
                                match_stats[media_type]["skipped"] += 1
                            for t in enabled_art:
                                artwork_log_stats[t][media_type]["skipped"] += 1
                            log_debug(LogTags.UNMATCHED, f"  Skipping {media['title']} ({media.get('year')}) - status: {(media.get('status') or '').lower()}, not in library")
                            continue

                        if poster_match_active:
                            self._match_poster_item(media, media_type, prefix_index, unmatched, match_stats)

                        if enabled_art:
                            matched_types: List[str] = []
                            missing_types: List[str] = []
                            item_reason: str | None = None
                            for t in enabled_art:
                                matched, reason = self._match_artwork_item(media, media_type, artwork_indexes[t], artwork_unmatched[t], artwork_log_stats[t][media_type])
                                (matched_types if matched else missing_types).append(t)
                                if matched and item_reason is None and reason:
                                    item_reason = reason
                            log_debug(LogTags.UNMATCHED, self._artwork_item_line(media, matched_types, missing_types, item_reason, compact=poster_match_active))

                    if poster_match_active:
                        stats = match_stats[media_type]
                        type_time = time.time() - type_start
                        partial_str = f", {stats['partial']:,} partial" if media_type == 'series' else ''
                        log_success(
                            LogTags.UNMATCHED,
                            f"  {media_type.title()} complete: {stats['matched']:,} matched, {stats['unmatched']:,} unmatched{partial_str}, {stats['skipped']:,} skipped | {type_time:.2f}s",
                            media_type=media_type, **stats, time_sec=f"{type_time:.2f}",
                        )

            if progress_callback:
                progress_callback("calculating", 90, 100, "Calculating statistics...")

            # --- Poster finalize (store) ---
            poster_result: Dict[str, Any] = {}
            poster_summary = None
            if check_posters:
                if poster_match_active:
                    poster_summary = self._calculate_stats(unmatched, media_dict)
                    try:
                        self._save_results(poster_summary, unmatched)
                    except Exception as e:
                        log_error(LogTags.UNMATCHED, f"Failed to save results to database: {e}", error=str(e))
                    poster_result = {"summary": poster_summary, "unmatched": unmatched, "last_run": datetime.now(timezone.utc).isoformat()}
                else:
                    empty = self._empty_result()
                    try:
                        self._save_results(empty["summary"], empty["unmatched"])
                    except Exception as e:
                        log_debug(LogTags.UNMATCHED, f"Failed to persist empty poster result: {e}")
                    poster_result = empty

            # --- Artwork finalize (store) + per-type complete lines ---
            artwork_payload: Dict[str, Any] | None = None
            artwork_summaries: Dict[str, Dict[str, Any]] = {}
            if enabled_art:
                now_iso = datetime.now(timezone.utc).isoformat()
                artwork_payload = self.get_cached_artwork_results()
                artwork_payload["last_run"] = now_iso
                for t in enabled_art:
                    summary = self._calculate_artwork_stats(artwork_unmatched[t], media_dict)
                    artwork_payload[t] = {"summary": summary, "unmatched": artwork_unmatched[t], "last_run": now_iso}
                    artwork_summaries[t] = summary
                try:
                    self._save_artwork_results(artwork_payload)
                except Exception as e:
                    log_error(LogTags.UNMATCHED, f"Failed to save artwork results to database: {e}", error=str(e))
                for t in enabled_art:
                    for media_type in ["movies", "series", "collections"]:
                        st = artwork_log_stats[t][media_type]
                        log_success(LogTags.UNMATCHED, f"  {media_type.title()} complete ({t}): {st['matched']:,} matched, {st['unmatched']:,} missing, {st['skipped']:,} skipped")

            # --- Combined stats output ---
            if enabled_art:
                columns: Dict[str, Dict[str, Any]] = {}
                if poster_summary is not None:
                    columns["Poster"] = poster_summary
                for t in enabled_art:
                    columns[t.capitalize()] = artwork_summaries[t]
                self._log_combined_by_library(columns)
            elif poster_summary is not None:
                if poster_summary.get("by_library"):
                    log_info(LogTags.UNMATCHED, "Statistics by library instance:")
                    for instance_name, instance_stats in poster_summary["by_library"].items():
                        for media_type in ["movies", "series", "collections"]:
                            if media_type in instance_stats:
                                ts = instance_stats[media_type]
                                log_info(
                                    LogTags.UNMATCHED,
                                    f"  {instance_name} ({media_type}): {ts['total'] - ts['unmatched']}/{ts['total']} complete ({ts['percent_complete']:.2f}%)",
                                    instance=instance_name, media_type=media_type, total=ts['total'],
                                    matched=ts['total'] - ts['unmatched'], unmatched=ts['unmatched'],
                                )

            total_time = time.time() - start_time
            # Completion message carries the counts (HEAD parity), one part per asset type.
            parts = []
            if poster_summary is not None:
                g = poster_summary["grand_total"]
                parts.append(f"posters {g['total'] - g['unmatched']:,}/{g['total']:,} ({g['percent_complete']:.2f}%)")
            for t in enabled_art:
                g = (artwork_summaries.get(t) or {}).get("grand_total") or {}
                parts.append(f"{t} {g.get('total', 0) - g.get('unmatched', 0):,}/{g.get('total', 0):,}")
            completion_msg = f"Complete: {', '.join(parts)} matched" if parts else "Detection complete"
            if progress_callback:
                progress_callback("completed", 100, 100, completion_msg)
            if check_posters:
                log_section_end(LogTags.UNMATCHED, "Detection Complete")
            log_success(LogTags.UNMATCHED, f"Detection complete in {total_time:.2f}s", total_time_sec=f"{total_time:.2f}")

            if enabled_art:
                for line in self._build_combined_stats_table_lines(columns):
                    log_info(LogTags.UNMATCHED, line)
            elif poster_summary is not None:
                for line in self._build_stats_table_lines(poster_summary):
                    log_info(LogTags.UNMATCHED, line)

            result: Dict[str, Any] = poster_result if check_posters else {}
            if enabled_art:
                result = {**result, "artwork": artwork_payload}
            return result

        except Exception as e:
            log_error(LogTags.UNMATCHED, f"Unmatched detection failed: {e}", error=str(e))
            if check_posters:
                log_section_end(LogTags.UNMATCHED, "Detection Failed")
            if progress_callback:
                progress_callback("error", 0, 100, f"Detection failed: {e}")
            return self._combined_failure(check_posters, bool(enabled_art))

    def _combined_failure(self, check_posters: bool, has_artwork: bool) -> Dict[str, Any]:
        """Failure fallback: empty poster result (if checked) + cached artwork (if checked)."""
        result: Dict[str, Any] = self._empty_result() if check_posters else {}
        if check_posters:
            # Persist the empty result so stored stats zero out instead of going stale.
            try:
                self._save_results(result["summary"], result["unmatched"])
            except Exception as e:
                log_debug(LogTags.UNMATCHED, f"Failed to persist empty result after scan error: {e}")
        if has_artwork:
            result = {**result, "artwork": self.get_cached_artwork_results()}
        return result

    @staticmethod
    def _should_have_poster(media: Dict[str, Any], media_type: str) -> bool:
        """A poster is expected when the item is released or already downloaded to the library.

        Single source of truth shared by detection and stats so the two never drift.
        """
        status = (media.get("status") or "").lower()
        if media_type == "movies":
            return status in {"released", "physicalrelease"} or bool(media.get("has_file", False))
        if media_type == "series":
            return status in {"ended", "continuing"} or bool(media.get("has_episodes", False))
        return True

    def _calculate_stats(
        self,
        unmatched: Dict[str, List[Dict[str, Any]]],
        media_dict: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Calculate statistics for unmatched media, including breakdown by library instance and status."""
        
        log_debug(LogTags.UNMATCHED, "Calculating statistics...")
        
        # Only count items a poster is expected for (released or in library), so the
        # totals/percentages match exactly what detection evaluated.
        eligible_movies = [m for m in media_dict.get("movies", []) if self._should_have_poster(m, "movies")]
        eligible_series = [s for s in media_dict.get("series", []) if self._should_have_poster(s, "series")]

        # Calculate overall stats (existing logic)
        # Movies
        unmatched_movies = len(unmatched.get("movies", []))
        total_movies = len(eligible_movies)
        percent_movies = (
            ((total_movies - unmatched_movies) / total_movies * 100)
            if total_movies
            else 100.0
        )

        # Calculate movies by status (released vs unreleased/upcoming)
        movies_released = sum(
            1 for movie in media_dict.get("movies", [])
            if movie.get("status", "").lower() in ["released", "physicalrelease", "incinemas"]
        )
        movies_unreleased = sum(
            1 for movie in media_dict.get("movies", [])
            if movie.get("status", "").lower() in ["announced", "upcoming", "tba"]
        )

        # Series (main posters only)
        unmatched_series = sum(
            1 for item in unmatched.get("series", [])
            if item.get("missing_main_poster", False)
        )
        total_series = len(eligible_series)
        percent_series = (
            ((total_series - unmatched_series) / total_series * 100)
            if total_series
            else 100.0
        )

        # Calculate series by status (continuing/ended vs upcoming)
        series_continuing = sum(
            1 for show in media_dict.get("series", [])
            if show.get("status", "").lower() in ["continuing", "ended"]
        )
        series_upcoming = sum(
            1 for show in media_dict.get("series", [])
            if show.get("status", "").lower() in ["upcoming"]
        )

        # Seasons
        unmatched_seasons = sum(
            len(item.get("missing_seasons", []))
            for item in unmatched.get("series", [])
        )
        total_seasons = sum(
            len([
                s for s in media.get("seasons", [])
                if s.get("season_has_episodes")
            ])
            for media in eligible_series
        )
        percent_seasons = (
            ((total_seasons - unmatched_seasons) / total_seasons * 100)
            if total_seasons
            else 100.0
        )

        # Collections
        unmatched_collections = len(unmatched.get("collections", []))
        total_collections = len(media_dict.get("collections", []))
        percent_collections = (
            ((total_collections - unmatched_collections) / total_collections * 100)
            if total_collections
            else 100.0
        )

        # Grand total
        grand_total = total_movies + total_series + total_seasons + total_collections
        grand_unmatched = (
            unmatched_movies + unmatched_series + unmatched_seasons + unmatched_collections
        )
        grand_percent = (
            ((grand_total - grand_unmatched) / grand_total * 100)
            if grand_total
            else 100.0
        )

        # Calculate stats by library instance
        by_library = self._calculate_stats_by_library(unmatched, media_dict)

        return {
            "movies": {
                "total": total_movies,
                "unmatched": unmatched_movies,
                "percent_complete": percent_movies,
                "released": movies_released,
                "unreleased": movies_unreleased,
            },
            "series": {
                "total": total_series,
                "unmatched": unmatched_series,
                "percent_complete": percent_series,
                "continuing": series_continuing,
                "upcoming": series_upcoming,
            },
            "seasons": {
                "total": total_seasons,
                "unmatched": unmatched_seasons,
                "percent_complete": percent_seasons,
            },
            "collections": {
                "total": total_collections,
                "unmatched": unmatched_collections,
                "percent_complete": percent_collections,
            },
            "grand_total": {
                "total": grand_total,
                "unmatched": grand_unmatched,
                "percent_complete": grand_percent,
            },
            "by_library": by_library,
        }
    
    def _calculate_stats_by_library(
        self,
        unmatched: Dict[str, List[Dict[str, Any]]],
        media_dict: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Dict[str, Any]]:
        """Calculate statistics grouped by library instance for each media type."""
        by_library = {}
        
        # Process each media type
        for media_type in ["movies", "series", "collections"]:
            media_list = media_dict.get(media_type, [])
            unmatched_list = unmatched.get(media_type, [])
            
            # Group media by instance
            instance_totals = {}
            instance_library_types = {}  # Track library_type for collections
            for media in media_list:
                # Match the overall totals: only count items a poster is expected for.
                if media_type in ("movies", "series") and not self._should_have_poster(media, media_type):
                    continue
                instance = media.get("instance", "Unknown")
                if instance not in instance_totals:
                    instance_totals[instance] = 0
                instance_totals[instance] += 1
                
                # Store library_type for collections (from Plex API)
                if media_type == "collections" and "library_type" in media:
                    instance_library_types[instance] = media["library_type"]
            
            # Group unmatched by instance
            instance_unmatched = {}
            for item in unmatched_list:
                instance = item.get("instance", "Unknown")
                if instance not in instance_unmatched:
                    instance_unmatched[instance] = 0
                
                # For series, count based on type (main poster vs just seasons)
                if media_type == "series":
                    if item.get("missing_main_poster", False):
                        instance_unmatched[instance] += 1
                else:
                    instance_unmatched[instance] += 1
            
            # Build stats for each instance
            for instance in instance_totals:
                if instance not in by_library:
                    by_library[instance] = {}
                
                total = instance_totals[instance]
                missing = instance_unmatched.get(instance, 0)
                
                stats = {
                    "total": total,
                    "unmatched": missing,
                    "percent_complete": ((total - missing) / total * 100) if total else 100.0,
                }
                
                # Add library_type for collections
                if media_type == "collections" and instance in instance_library_types:
                    stats["library_type"] = instance_library_types[instance]
                
                by_library[instance][media_type] = stats
        
        return by_library

    def _save_results(
        self,
        stats: Dict[str, Dict[str, Any]],
        unmatched: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        """Save unmatched detection results to database."""
        try:
            result_data = {
                "summary": stats,
                "unmatched": unmatched,
                "last_run": datetime.now(timezone.utc).isoformat(),
            }

            try:
                upsert_setting(self.db, "poster_unmatched_stats", json.dumps(result_data))
                log_debug(LogTags.DATABASE, "Saved poster_unmatched_stats to database")
            except (TypeError, ValueError) as e:
                log_error(LogTags.DATABASE, f"Failed to serialize results to JSON: {e}", error=str(e))
                raise
            except Exception as e:
                log_error(LogTags.DATABASE, f"Failed to save poster_unmatched_stats: {e}", error=str(e))
                raise

            try:
                self.db.commit()
                log_debug(LogTags.DATABASE, "Results committed successfully")
            except Exception as e:
                log_error(LogTags.DATABASE, f"Failed to commit results to database: {e}", error=str(e))
                self.db.rollback()
                raise
        
        except Exception as e:
            log_error(LogTags.DATABASE, f"Error saving unmatched results: {e}", error=str(e))
            raise

    def get_cached_results(self) -> Dict[str, Any]:
        """Get cached unmatched detection results from database."""
        try:
            setting = get_setting(self.db, "poster_unmatched_stats")
        except Exception as e:
            log_error(LogTags.DATABASE, f"Failed to query cached results: {e}", error=str(e))
            return self._empty_result()

        if setting:
            try:
                result = json.loads(setting.value)
                return result
            except json.JSONDecodeError as e:
                log_error(LogTags.DATABASE, f"Failed to parse cached results (invalid JSON): {e}", error=str(e))
                return self._empty_result()
            except Exception as e:
                log_error(LogTags.DATABASE, f"Unexpected error parsing cached results: {e}", error=str(e))
                return self._empty_result()

        # Only log when cache is missing (first run or after clear)
        log_debug(LogTags.DATABASE, "No cached results found")
        return self._empty_result()

    def _empty_result(self) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            "summary": {
                "movies": {"total": 0, "unmatched": 0, "percent_complete": 100.0, "released": 0, "unreleased": 0},
                "series": {"total": 0, "unmatched": 0, "percent_complete": 100.0, "continuing": 0, "upcoming": 0},
                "seasons": {"total": 0, "unmatched": 0, "percent_complete": 100.0},
                "collections": {"total": 0, "unmatched": 0, "percent_complete": 100.0},
                "grand_total": {"total": 0, "unmatched": 0, "percent_complete": 100.0},
                "by_library": {},
            },
            "unmatched": {
                "movies": [],
                "series": [],
                "collections": []
            },
            "last_run": None
        }

    # ── Artwork (logo/background/squareart) unmatched detection ──────────────────
    # Artwork is just another asset: the SAME detector, sharing the poster path's
    # filters/eligibility/by-library stats. It differs only in scanning the destination
    # per artwork type and having no season dimension (artwork is per-title).

    SETTING_ARTWORK_UNMATCHED_STATS = "artwork_unmatched_stats"

    def _calculate_artwork_stats(self, unmatched: Dict[str, List[Dict[str, Any]]], media_dict: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        eligible_movies = [m for m in media_dict.get("movies", []) if self._should_have_poster(m, "movies")]
        eligible_series = [s for s in media_dict.get("series", []) if self._should_have_poster(s, "series")]

        unmatched_movies = len(unmatched.get("movies", []))
        total_movies = len(eligible_movies)
        percent_movies = ((total_movies - unmatched_movies) / total_movies * 100) if total_movies else 100.0
        movies_released = sum(1 for m in media_dict.get("movies", []) if (m.get("status") or "").lower() in ("released", "physicalrelease", "incinemas"))
        movies_unreleased = sum(1 for m in media_dict.get("movies", []) if (m.get("status") or "").lower() in ("announced", "upcoming", "tba"))

        unmatched_series = sum(1 for s in unmatched.get("series", []) if s.get("missing_main_poster", False))
        total_series = len(eligible_series)
        percent_series = ((total_series - unmatched_series) / total_series * 100) if total_series else 100.0
        series_continuing = sum(1 for s in media_dict.get("series", []) if (s.get("status") or "").lower() in ("continuing", "ended"))
        series_upcoming = sum(1 for s in media_dict.get("series", []) if (s.get("status") or "").lower() in ("upcoming",))

        unmatched_collections = len(unmatched.get("collections", []))
        total_collections = len(media_dict.get("collections", []))
        percent_collections = ((total_collections - unmatched_collections) / total_collections * 100) if total_collections else 100.0

        grand_total = total_movies + total_series + total_collections
        grand_unmatched = unmatched_movies + unmatched_series + unmatched_collections
        grand_percent = ((grand_total - grand_unmatched) / grand_total * 100) if grand_total else 100.0

        return {
            "movies": {"total": total_movies, "unmatched": unmatched_movies, "percent_complete": percent_movies, "released": movies_released, "unreleased": movies_unreleased},
            "series": {"total": total_series, "unmatched": unmatched_series, "percent_complete": percent_series, "continuing": series_continuing, "upcoming": series_upcoming},
            # Artwork has no season dimension — zeroed so the shared UI hides the Seasons card.
            "seasons": {"total": 0, "unmatched": 0, "percent_complete": 100.0},
            "collections": {"total": total_collections, "unmatched": unmatched_collections, "percent_complete": percent_collections},
            "grand_total": {"total": grand_total, "unmatched": grand_unmatched, "percent_complete": grand_percent},
            "by_library": self._calculate_stats_by_library(unmatched, media_dict),
        }

    @staticmethod
    def _empty_artwork_type_result() -> Dict[str, Any]:
        empty_bucket = {"total": 0, "unmatched": 0, "percent_complete": 100.0}
        return {
            "summary": {
                "movies": {**empty_bucket, "released": 0, "unreleased": 0},
                "series": {**empty_bucket, "continuing": 0, "upcoming": 0},
                "seasons": dict(empty_bucket),
                "collections": dict(empty_bucket),
                "grand_total": dict(empty_bucket),
                "by_library": {},
            },
            "unmatched": {"movies": [], "series": [], "collections": []},
            "last_run": None,
        }

    def _empty_artwork_payload(self) -> Dict[str, Any]:
        payload = {t: self._empty_artwork_type_result() for t in ARTWORK_TYPES}
        payload["last_run"] = None
        return payload

    def _save_artwork_results(self, payload: Dict[str, Any]) -> None:
        upsert_setting(self.db, self.SETTING_ARTWORK_UNMATCHED_STATS, json.dumps(payload))
        self.db.commit()

    def get_cached_artwork_results(self) -> Dict[str, Any]:
        try:
            setting = get_setting(self.db, self.SETTING_ARTWORK_UNMATCHED_STATS)
            if setting and setting.value:
                return json.loads(setting.value)
        except Exception as e:
            log_error(LogTags.DATABASE, f"Failed to read artwork_unmatched_stats: {e}")
        return self._empty_artwork_payload()
