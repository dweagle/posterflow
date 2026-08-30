import hashlib
import json
import os
import re
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_error, log_info, log_warning
from models.plex_upload import PlexUploadRecord
from models.setting import get_setting, upsert_setting
from util.poster_settings import get_poster_destination
from util.arr.client import create_arr_client
from util.constants import POSTER_ID_PATTERN
from util.data.normalization import normalize_titles
from util.media_server.client import MediaServerClient, create_media_server_client
from util.media_server.instances import instance_type, server_label, server_type_label
from util.media_server.types import (
    CAP_PER_LIBRARY_COLLECTIONS,
    CAP_SQUAREART,
    IMAGE_KIND_BACKGROUND,
    IMAGE_KIND_LOGO,
    IMAGE_KIND_POSTER,
    IMAGE_KIND_SQUAREART,
    MediaServerItem,
)
from util.posters.match import collection_title_variants
from util.posters.scanner import artwork_type_of, artwork_flat_base


PlexUploadProgressCallback = Callable[[int, int, Dict[str, int], str], None]

# Why a file never reached a Plex item. Labels claim only what was determined —
# the index covers selected libraries only, so "absent from Plex" is never said.
UNMATCHED_REASONS = ("no_plex_match", "year_mismatch", "not_downloaded", "type_unresolved", "edition_pending")
UNMATCHED_REASON_LABELS = {
    "no_plex_match": "no server match",
    "year_mismatch": "year differs",
    "not_downloaded": "not downloaded",
    "type_unresolved": "type unresolved",
    "edition_pending": "edition pending",
}
# The same reasons spelled out, for the log where there is room to be unambiguous.
UNMATCHED_REASON_DETAIL = {
    "no_plex_match": "nothing in the selected media server libraries matched this title or its IDs "
                     "(absent from the server, titled differently there, or in a library you did not select)",
    "year_mismatch": "the title matched a library item but the server's year is different",
    "not_downloaded": "*arr knows the item but reports no downloaded file/episodes yet",
    "type_unresolved": "no IDs in the path and movie/show/collection could not be told apart",
    "edition_pending": "waiting for a specific movie edition to appear in Plex",
}


def empty_unmatched_reasons() -> Dict[str, int]:
    return {reason: 0 for reason in UNMATCHED_REASONS}


def format_unmatched_reasons(reasons: Optional[Dict[str, Any]]) -> str:
    """'40 no Plex match, 15 not downloaded' — non-zero reasons only, empty string when clean."""
    if not isinstance(reasons, dict):
        return ""
    parts = [
        f"{int(reasons.get(reason, 0)):,} {UNMATCHED_REASON_LABELS[reason]}"
        for reason in UNMATCHED_REASONS
        if int(reasons.get(reason, 0) or 0) > 0
    ]
    return ", ".join(parts)


def format_outcome_breakdown(
    kind: str,
    stats: Dict[str, Any],
    *,
    dry_run: bool,
    scanned_detail: str = "",
) -> List[str]:
    """Per-file outcome block as lines, shared by posters and artwork. Lines rather
    than log calls so each caller emits through its own logger."""
    scanned = int(stats.get("scanned", 0))
    uploaded_files = int(stats.get("uploaded_files", 0))
    already_current = int(stats.get("already_current", 0))
    awaiting = int(stats.get("awaiting_plex", 0))
    errored = int(stats.get("errors", 0))
    unmatched = max(0, scanned - uploaded_files - already_current - awaiting - errored)
    reasons = stats.get("unmatched_reasons") if isinstance(stats.get("unmatched_reasons"), dict) else {}

    lines = [
        f"Outcome per {kind} file (final): {scanned:,} scanned{scanned_detail}",
        f"- {uploaded_files:,} {'would upload' if dry_run else 'uploaded'} — pushed to your media servers this run",
        f"- {already_current:,} already current — matched a server item that already has this exact file",
    ]
    if awaiting:
        lines.append(
            f"- {awaiting:,} awaiting library scan — the show matched, but the server has not scanned that season yet"
        )
    if unmatched:
        lines.append(f"- {unmatched:,} unmatched — no server item to apply them to:")
        lines.extend(
            f"  - {int(count):,} {UNMATCHED_REASON_LABELS.get(reason, reason)}: "
            f"{UNMATCHED_REASON_DETAIL.get(reason, '')}"
            for reason, count in (reasons or {}).items()
            if int(count or 0)
        )
    if errored:
        lines.append(f"- {errored:,} errored — see the per-file errors above")
    return lines


def artwork_summary_lines(artwork_stats: Dict[str, Any], *, dry_run: bool) -> List[str]:
    """Artwork's outcome block — the poster block plus the per-type split when anything uploaded."""
    lines = format_outcome_breakdown("artwork", artwork_stats, dry_run=dry_run)
    by_type = artwork_stats.get("by_type") if isinstance(artwork_stats.get("by_type"), dict) else {}
    if int(artwork_stats.get("uploaded_files", 0)):
        # Sub-bullet of the uploaded line, not whichever bucket lands last.
        lines.insert(
            2,
            f"  - {int(by_type.get('logo', 0))} logos, {int(by_type.get('background', 0))} backgrounds, "
            f"{int(by_type.get('squareart', 0))} squareart",
        )
    return lines


class AssetOutcome(NamedTuple):
    """Result of one asset's match+upload attempt."""
    uploaded: int
    matched: bool
    plex_targets: int  # distinct Plex items matched (2+ = same title in several libraries)
    media_counts: Dict[str, int]
    seasons_missing: int = 0
    skip_reason: Optional[str] = None


class PlexUploadService:
    """Upload organized poster assets to Plex libraries."""

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    # Plex 500s decoding very large images server-side. Downscale outliers past this longest
    # side, or over the byte cap (Kometa skips uploads at ~10MB — we downscale instead).
    MAX_UPLOAD_DIMENSION = 4000
    MAX_UPLOAD_BYTES = 10_000_000
    SETTING_RADARR_INSTANCES = "radarr_instances"
    SETTING_SONARR_INSTANCES = "sonarr_instances"
    SETTING_INSTANCE_LIBRARY_MAP = "plex_upload_instance_library_map"
    DEFAULT_EDITION_MOVIE = "default_edition"
    # Artwork subtypes the upload path handles (uploads route through MediaServerClient.upload_image).
    ARTWORK_KINDS = (IMAGE_KIND_LOGO, IMAGE_KIND_BACKGROUND, IMAGE_KIND_SQUAREART)
    SETTING_UPLOAD_ARTWORK = "plex_upload_artwork"
    ERROR_NO_PLEX_INSTANCES = "No media server instances configured. Configure in Settings → Media tab."
    ERROR_NO_LIBRARIES_SELECTED = "No media server libraries selected. Configure in Settings → Media tab."
    ERROR_INVALID_LIBRARY_CONFIG = "Invalid media server library configuration. Configure in Settings → Media tab."
    ERROR_INDEX_BUILD_FAILED = "Unable to build media server index from configured instances/libraries."
    MESSAGE_NO_POSTER_ASSETS = "No poster assets found to upload."

    def __init__(self, db: Session, upload_delay_ms: int = 50) -> None:
        self.db = db
        self.upload_delay_ms = max(0, upload_delay_ms)
        self._record_cache: Dict[str, Optional[Dict[str, Any]]] = {}  # per-run in-memory cache of DB records
        self._year_discrepancies: List[Dict[str, Any]] = []  # ID-matched uploads where folder year != Plex year
        self._local_assets_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._local_artwork_cache: Dict[str, List[Dict[str, Any]]] = {}
        # Season lists per show, so N season posters don't refetch the same show N times.
        # Cleared at each run entry — webhook retries wait on the server scanning a season.
        self._season_list_cache: Dict[str, List[MediaServerItem]] = {}
        self._arr_availability_cache: Dict[str, Dict[str, Any]] = {}
        self._arr_availability_incomplete: bool = False
        self._arr_instance_scope: Optional[str] = None
        self._expected_edition: Optional[str] = None
        self._series_show_poster_status: Optional[str] = None
        self._logged_missing_show_keys: set[str] = set()
        self._quiet_unmatched_logging: bool = False
        self._preflight_context_cache: Optional[
            Tuple[
                Optional[Dict[str, Any]],
                Optional[Path],
                Optional[Dict[str, Dict[str, List[Any]]]],
                Optional[Dict[str, Any]],
            ]
        ] = None

    def invalidate_preflight_cache(self) -> None:
        self._preflight_context_cache = None

    def invalidate_local_assets_cache(self) -> None:
        self._local_assets_cache = {}
        self._local_artwork_cache = {}

    def invalidate_arr_availability_cache(self) -> None:
        self._arr_availability_cache = {}

    def arr_availability_was_incomplete(self) -> bool:
        return self._arr_availability_incomplete

    def set_arr_instance_scope(self, arr_instance: Optional[str]) -> None:
        """Limit the ARR availability lookup to a single firing instance (webhook path).

        When set, ``_build_arr_availability_index`` only connects to the matching
        instance instead of every configured Radarr/Sonarr instance of that media
        type. Clears any cached availability so the scope takes effect immediately.
        """
        new_scope = arr_instance.strip() if isinstance(arr_instance, str) and arr_instance.strip() else None
        if new_scope != self._arr_instance_scope:
            self._arr_availability_cache = {}
        self._arr_instance_scope = new_scope

    def set_expected_edition(self, edition: Optional[str]) -> None:
        """Constrain movie matching to a specific edition for an edition-change upgrade.

        Pass the new edition title (or DEFAULT_EDITION_MOVIE for an edition removal).
        Until Plex has rescanned and an item with this edition exists, matching reports
        no match, so the webhook retry loop waits instead of uploading to the old item.
        """
        self._expected_edition = edition.strip() if isinstance(edition, str) and edition.strip() else None

    @staticmethod
    def _normalize_edition(edition: Optional[str]) -> str:
        return str(edition or "").strip().lower()

    def invalidate_record_cache(self) -> None:
        self._record_cache = {}

    def _log_unmatched(self, message: str, **context: Any) -> None:
        if self._quiet_unmatched_logging:
            log_debug(LogTags.UPLOADER, message, **context)
        else:
            log_info(LogTags.UPLOADER, message, **context)

    def _begin_upload_run(self, *, single_target: bool) -> None:
        self._year_discrepancies = []
        self._quiet_unmatched_logging = single_target
        # Single-target = a webhook job's many passes share one dedupe scope, so don't reset it.
        if not single_target:
            self._logged_missing_show_keys = set()

    @staticmethod
    def _empty_media_upload_counts() -> Dict[str, int]:
        return {
            "movies": 0,
            "shows": 0,
            "seasons": 0,
            "collections": 0,
        }

    @staticmethod
    def _base_result_stats(scanned: int = 0) -> Dict[str, Any]:
        return {
            "scanned": scanned,
            "matched": 0,
            "uploaded": 0,
            "would_upload": 0,
            "skipped": 0,
            "errors": 0,
            "plex_seasons_missing": 0,
            "plex_targets": 0,
            "multi_library_assets": 0,
            "unmatched_reasons": empty_unmatched_reasons(),
            # Outcome buckets; every scanned file lands in exactly one, summing to scanned.
            "uploaded_files": 0,
            "already_current": 0,
            "awaiting_plex": 0,
        }

    def _no_assets_result(self, message: str) -> Dict[str, Any]:
        return {
            "success": True,
            "message": message,
            "stats": self._base_result_stats(),
        }

    @staticmethod
    def _error_result(error: str) -> Dict[str, Any]:
        return {
            "success": False,
            "error": error,
        }

    def _build_run_stats(
        self,
        local_assets: List[Dict[str, Any]],
        library_totals: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            **self._base_result_stats(scanned=len(local_assets)),
            "main_assets": sum(1 for asset in local_assets if asset.get("asset_type") == "main"),
            "season_assets": sum(1 for asset in local_assets if asset.get("asset_type") == "season"),
            "library_totals": library_totals,
            "media_upload_counts": self._empty_media_upload_counts(),
            "artwork": {
                "scanned": 0,
                "matched": 0,
                "uploaded": 0,
                "would_upload": 0,
                "skipped": 0,
                "errors": 0,
                "by_type": {"logo": 0, "background": 0, "squareart": 0},
                "unmatched_reasons": empty_unmatched_reasons(),
                "uploaded_files": 0,
                "already_current": 0,
            },
        }

    @staticmethod
    def _run_success_result(
        *,
        dry_run: bool,
        stats: Dict[str, Any],
        completed_message: str,
        dry_run_message: str,
    ) -> Dict[str, Any]:
        return {
            "success": True,
            "message": dry_run_message if dry_run else completed_message,
            "dry_run": dry_run,
            "stats": stats,
        }

    def _process_assets_for_upload(
        self,
        *,
        local_assets: List[Dict[str, Any]],
        index: Dict[str, Dict[str, List[Any]]],
        stats: Dict[str, Any],
        dry_run: bool,
        arr_availability: Dict[str, Any],
        remove_overlay_label: bool,
        media_type_filter: Optional[str] = None,
        progress_callback: Optional[PlexUploadProgressCallback] = None,
    ) -> None:
        total_assets = len(local_assets)
        for asset_index, asset in enumerate(local_assets, start=1):
            progress_message = ""
            try:
                outcome = self._upload_asset(
                    asset,
                    index,
                    dry_run,
                    media_type_filter=media_type_filter,
                    arr_availability=arr_availability,
                    remove_overlay_label=remove_overlay_label,
                )
                uploaded_count = outcome.uploaded
                stats["plex_targets"] += outcome.plex_targets
                if outcome.plex_targets > 1:
                    stats["multi_library_assets"] += 1
                for key, value in outcome.media_counts.items():
                    stats["media_upload_counts"][key] += int(value)
                if outcome.matched:
                    stats["matched"] += 1
                    if uploaded_count > 0:
                        stats["uploaded_files"] += 1
                    elif outcome.seasons_missing:
                        stats["awaiting_plex"] += 1
                    else:
                        stats["already_current"] += 1
                elif outcome.skip_reason in stats["unmatched_reasons"]:
                    stats["unmatched_reasons"][outcome.skip_reason] += 1
                if dry_run:
                    stats["would_upload"] += uploaded_count
                else:
                    stats["uploaded"] += uploaded_count
                if uploaded_count == 0:
                    stats["skipped"] += 1
                if outcome.seasons_missing:
                    stats["plex_seasons_missing"] = int(stats.get("plex_seasons_missing", 0)) + outcome.seasons_missing

                file_name = Path(str(asset.get("path") or "")).name
                upload_label = "would upload" if dry_run else "uploaded"
                upload_value = int(stats.get("would_upload" if dry_run else "uploaded", 0))
                progress_message = (
                    f"Processed {asset_index}/{total_assets}: {file_name} | "
                    f"matched={int(stats.get('matched', 0))}, {upload_label}={upload_value}, "
                    f"skipped={int(stats.get('skipped', 0))}, errors={int(stats.get('errors', 0))}"
                )
            except Exception as e:
                stats["errors"] += 1
                log_error(LogTags.UPLOADER, f"Failed processing asset '{asset['path']}': {e}\n{traceback.format_exc()}")
                file_name = Path(str(asset.get("path") or "")).name
                progress_message = (
                    f"Processed {asset_index}/{total_assets}: {file_name} | "
                    f"matched={int(stats.get('matched', 0))}, "
                    f"uploaded={int(stats.get('uploaded', 0))}, "
                    f"skipped={int(stats.get('skipped', 0))}, errors={int(stats.get('errors', 0))}"
                )

            if progress_callback:
                try:
                    progress_callback(
                        asset_index,
                        total_assets,
                        {
                            "matched": int(stats.get("matched", 0)),
                            "uploaded": int(stats.get("uploaded", 0)),
                            "would_upload": int(stats.get("would_upload", 0)),
                            "skipped": int(stats.get("skipped", 0)),
                            "errors": int(stats.get("errors", 0)),
                        },
                        progress_message,
                    )
                except Exception as callback_error:
                    log_warning(LogTags.UPLOADER, f"Upload progress callback failed: {callback_error}")

    def _prepare_upload_context(
        self,
        *,
        force_refresh: bool = False,
    ) -> Tuple[
        Optional[Dict[str, Any]],
        Optional[Path],
        Optional[Dict[str, Dict[str, List[Any]]]],
        Optional[Dict[str, Any]],
    ]:
        if not force_refresh and self._preflight_context_cache is not None:
            return self._preflight_context_cache

        destination_dir = self._get_destination_dir()
        plex_instances = self._get_plex_instances()

        if not plex_instances:
            return self._error_result(self.ERROR_NO_PLEX_INSTANCES), None, None, None

        selected_libraries, selected_libraries_error = self._get_selected_libraries(plex_instances)
        if selected_libraries_error:
            return self._error_result(selected_libraries_error), None, None, None

        index, library_totals = self._build_plex_index(plex_instances, selected_libraries)
        if not index:
            log_warning(
                LogTags.UPLOADER,
                "Upload preflight failed: unable to build index from configured instances/libraries",
                preflight_connectivity_failed=True,
                matching_skipped=True,
            )
            return self._error_result(self.ERROR_INDEX_BUILD_FAILED), None, None, None
        if not library_totals:
            log_warning(
                LogTags.UPLOADER,
                "Upload preflight failed: no reachable media server libraries from configured instances",
                preflight_connectivity_failed=True,
                matching_skipped=True,
            )
            return self._error_result(self.ERROR_INDEX_BUILD_FAILED), None, None, None

        context = (None, destination_dir, index, library_totals)
        self._preflight_context_cache = context
        return context

    def _get_arr_availability_index(self, media_type_filter: Optional[str] = None, *, force_refresh: bool = False) -> Dict[str, Any]:
        cache_key = f"{self._arr_instance_scope or ''}|{str(media_type_filter or '').strip().lower()}"
        if not force_refresh and cache_key in self._arr_availability_cache:
            return self._arr_availability_cache[cache_key]

        availability = self._build_arr_availability_index(media_type_filter=media_type_filter)
        self._arr_availability_cache[cache_key] = availability
        return availability

    def _get_local_assets(self, destination: Path, *, force_refresh: bool = False) -> List[Dict[str, Any]]:
        cache_key = str(destination)
        if not force_refresh and cache_key in self._local_assets_cache:
            return self._local_assets_cache[cache_key]

        discovered_assets = self._discover_local_assets(destination)
        self._local_assets_cache[cache_key] = discovered_assets
        return discovered_assets

    def _get_local_artwork(self, destination: Path, *, force_refresh: bool = False) -> List[Dict[str, Any]]:
        cache_key = str(destination)
        if not force_refresh and cache_key in self._local_artwork_cache:
            return self._local_artwork_cache[cache_key]
        discovered = self._discover_local_artwork(destination)
        self._local_artwork_cache[cache_key] = discovered
        return discovered

    def _load_json_setting(
        self,
        setting_key: str,
        missing_default: Any,
        *,
        invalid_default: Any = None,
        invalid_json_log_level: Optional[str] = None,
    ) -> Any:
        if invalid_default is None:
            invalid_default = missing_default

        setting = get_setting(self.db, setting_key)
        if not setting or not setting.value:
            return missing_default

        try:
            return json.loads(setting.value)
        except json.JSONDecodeError:
            if invalid_json_log_level == "error":
                log_error(LogTags.UPLOADER, f"Invalid JSON in {setting_key} setting")
            elif invalid_json_log_level == "warning":
                log_warning(LogTags.UPLOADER, f"Invalid JSON in {setting_key}")
            return invalid_default

    def run_full_upload(
        self,
        dry_run: bool = False,
        reapply: bool = False,
        remove_overlay_label: bool = False,
        progress_callback: Optional[PlexUploadProgressCallback] = None,
    ) -> Dict[str, Any]:
        self._season_list_cache.clear()
        if reapply and not dry_run:
            self._clear_upload_cache()
            log_info(LogTags.UPLOADER, "Reapply enabled: cleared upload cache before run")
        elif reapply and dry_run:
            log_info(LogTags.UPLOADER, "Reapply requested in dry run: skipped upload cache clear")

        preflight_error, destination_dir, index, library_totals = self._prepare_upload_context()
        if preflight_error:
            return preflight_error

        if destination_dir is None or index is None or library_totals is None:
            return self._error_result("Upload preflight returned incomplete context.")

        local_assets = self._get_local_assets(destination_dir)
        artwork_enabled = self._is_artwork_upload_enabled()
        artwork_assets = self._discover_local_artwork(destination_dir) if artwork_enabled else []
        if not local_assets and not artwork_assets:
            return self._no_assets_result(self.MESSAGE_NO_POSTER_ASSETS)

        stats = self._build_run_stats(local_assets, library_totals)
        self._begin_upload_run(single_target=False)
        arr_availability = self._get_arr_availability_index()
        if local_assets:
            self._process_assets_for_upload(
                local_assets=local_assets,
                index=index,
                stats=stats,
                dry_run=dry_run,
                arr_availability=arr_availability,
                remove_overlay_label=remove_overlay_label,
                progress_callback=progress_callback,
            )
        if artwork_assets:
            stats["artwork"]["scanned"] = len(artwork_assets)
            self._process_artwork_for_upload(
                artwork_assets=artwork_assets,
                index=index,
                stats=stats,
                dry_run=dry_run,
                arr_availability=arr_availability,
                progress_callback=progress_callback,
            )
            # Full runs log artwork from the job layer; run_single_upload has no summary.
        stats["year_discrepancies"] = list(self._year_discrepancies)

        self._persist_upload_cache()

        return self._run_success_result(
            dry_run=dry_run,
            stats=stats,
            completed_message="Asset upload completed",
            dry_run_message="Asset upload dry run completed",
        )

    def run_single_upload(
        self,
        *,
        media_type: str,
        title: str,
        year: Optional[int] = None,
        season_number: Optional[int] = None,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        plex_rating_key: Optional[str] = None,
        dry_run: bool = False,
        reapply: bool = False,
        remove_overlay_label: bool = False,
        include_artwork: bool = False,
        progress_callback: Optional[PlexUploadProgressCallback] = None,
    ) -> Dict[str, Any]:
        self._season_list_cache.clear()
        if reapply and not dry_run:
            removed_entries = self.clear_upload_cache_for_target(
                media_type=media_type,
                title=title,
                year=year,
                season_number=season_number,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,
                imdb_id=imdb_id,
            )
            log_info(
                LogTags.UPLOADER,
                "Reapply enabled: cleared upload cache for single target before run",
                media_type=media_type,
                title=title,
                removed_entries=removed_entries,
            )
        elif reapply and dry_run:
            log_info(
                LogTags.UPLOADER,
                "Reapply requested in dry run: skipped upload cache clear for single target",
                media_type=media_type,
                title=title,
            )

        preflight_error, destination_dir, index, library_totals = self._prepare_upload_context()
        if preflight_error:
            return preflight_error

        if destination_dir is None or index is None or library_totals is None:
            return self._error_result("Upload preflight returned incomplete context.")

        all_assets = self._get_local_assets(destination_dir)
        artwork_all = self._get_local_artwork(destination_dir) if include_artwork else []
        if not all_assets and not artwork_all:
            return self._no_assets_result(self.MESSAGE_NO_POSTER_ASSETS)

        media_type_normalized = media_type.lower().strip()
        local_assets = self._select_local_assets_for_target(
            all_assets,
            media_type=media_type_normalized,
            title=title,
            year=year,
            season_number=season_number,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
        ) if all_assets else []

        # Artwork is per-title (no seasons), so match the item regardless of the
        # triggering season — a season webhook still refreshes the show's artwork.
        target_artwork = self._select_local_assets_for_target(
            artwork_all,
            media_type=media_type_normalized,
            title=title,
            year=year,
            season_number=None,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
        ) if artwork_all else []

        if not local_assets and not target_artwork:
            return self._no_assets_result(f"No local assets found for '{title}'.")

        stats = self._build_run_stats(local_assets, library_totals)
        self._begin_upload_run(single_target=True)
        arr_availability = self._get_arr_availability_index(media_type_filter=media_type_normalized)
        if local_assets:
            self._process_assets_for_upload(
                local_assets=local_assets,
                index=index,
                stats=stats,
                dry_run=dry_run,
                arr_availability=arr_availability,
                remove_overlay_label=remove_overlay_label,
                media_type_filter=media_type_normalized,
                progress_callback=progress_callback,
            )
        if target_artwork:
            stats["artwork"]["scanned"] = len(target_artwork)
            self._process_artwork_for_upload(
                artwork_assets=target_artwork,
                index=index,
                stats=stats,
                dry_run=dry_run,
                arr_availability=arr_availability,
            )
            self._log_artwork_summary(stats, dry_run=dry_run)
        stats["year_discrepancies"] = list(self._year_discrepancies)

        self._persist_upload_cache()

        return self._run_success_result(
            dry_run=dry_run,
            stats=stats,
            completed_message="Single-item upload completed",
            dry_run_message="Single-item dry run completed",
        )

    def is_single_target_fully_cached(
        self,
        *,
        media_type: str,
        title: str,
        year: Optional[int] = None,
        season_number: Optional[int] = None,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        include_artwork: bool = False,
    ) -> bool:
        """Return True when a target resolves to local assets that are already cached for all matched Plex targets.

        When include_artwork is set, the target's artwork (logo/background/squareart) must ALSO
        be cached — so a re-fired webhook whose poster is cached still runs to push new artwork.
        """
        preflight_error, destination_dir, index, _library_totals = self._prepare_upload_context()
        if preflight_error:
            return False
        if destination_dir is None or index is None:
            return False

        all_assets = self._get_local_assets(destination_dir)
        if not all_assets:
            return False

        media_type_normalized = media_type.lower().strip()
        local_assets = self._select_local_assets_for_target(
            all_assets,
            media_type=media_type_normalized,
            title=title,
            year=year,
            season_number=season_number,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
        )
        if not local_assets:
            return False

        arr_availability = self._get_arr_availability_index(media_type_filter=media_type_normalized)
        for asset in local_assets:
            if not self._is_asset_fully_cached_for_targets(
                asset,
                index=index,
                media_type_filter=media_type_normalized,
                arr_availability=arr_availability,
            ):
                return False

        if include_artwork:
            # Artwork is per-title (season_number=None), so a season webhook still checks
            # the show's artwork. No artwork on disk → nothing to upload → still "cached".
            target_artwork = self._select_local_assets_for_target(
                self._get_local_artwork(destination_dir),
                media_type=media_type_normalized,
                title=title,
                year=year,
                season_number=None,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,
                imdb_id=imdb_id,
            )
            for asset in target_artwork:
                if not self._is_asset_fully_cached_for_targets(
                    asset,
                    index=index,
                    media_type_filter=media_type_normalized,
                    arr_availability=arr_availability,
                ):
                    return False

        return True

    def is_series_show_poster_cached(
        self,
        *,
        title: str,
        year: Optional[int] = None,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> bool:
        """True when the show poster is current. Read-only; also records why-not in _series_show_poster_status (current/re_added/not_uploaded/needs_apply) for webhook logging."""
        self._series_show_poster_status = "not_uploaded"
        preflight_error, destination_dir, index, _library_totals = self._prepare_upload_context()
        if preflight_error or destination_dir is None or index is None:
            self._series_show_poster_status = "needs_apply"
            return False

        all_assets = self._get_local_assets(destination_dir)
        if not all_assets:
            return False

        local_assets = self._select_local_assets_for_target(
            all_assets,
            media_type="series",
            title=title,
            year=year,
            season_number=None,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
        )
        if not local_assets:
            return False

        show_main_assets = [asset for asset in local_assets if str(asset.get("asset_type") or "") == "main"]
        if not show_main_assets:
            return False

        arr_availability = self._get_arr_availability_index(media_type_filter="series")
        saw_prior_upload = False
        saw_re_added = False
        for asset in show_main_assets:
            if self._is_asset_fully_cached_for_targets(
                asset,
                index=index,
                media_type_filter="series",
                arr_availability=arr_availability,
            ):
                self._series_show_poster_status = "current"
                return True

            record = self._get_uploaded_record(str(asset.get("path") or ""))
            uploaded_rating_keys = set(record.get("uploaded_to_rating_keys", []))
            if record.get("uploaded_to_libraries") or record.get("uploaded_to_library_keys") or uploaded_rating_keys:
                saw_prior_upload = True
                shows = self._dedupe_plex_items(
                    self._resolve_index_candidates(
                        index["shows"],
                        str(asset.get("media_key") or ""),
                        self._extract_asset_id_keys(asset),
                        asset.get("folder_year"),
                    )
                )
                if any(self._rating_key_indicates_change(self._item_rating_key(s), uploaded_rating_keys) for s in shows):
                    saw_re_added = True

        if saw_re_added:
            self._series_show_poster_status = "re_added"
        elif saw_prior_upload:
            self._series_show_poster_status = "needs_apply"
        else:
            self._series_show_poster_status = "not_uploaded"
        return False

    def _is_asset_fully_cached_for_targets(
        self,
        asset: Dict[str, Any],
        *,
        index: Dict[str, Dict[str, List[Any]]],
        media_type_filter: Optional[str],
        arr_availability: Optional[Dict[str, Any]],
    ) -> bool:
        file_path = str(asset.get("path") or "")
        uploaded_record = self._get_uploaded_record(file_path)
        uploaded_to_libraries = set(uploaded_record.get("uploaded_to_libraries", []))
        uploaded_to_library_keys = set(uploaded_record.get("uploaded_to_library_keys", []))
        uploaded_rating_keys = set(uploaded_record.get("uploaded_to_rating_keys", []))
        uploaded_editions = set(uploaded_record.get("uploaded_editions", []))
        uploaded_media_types = set(uploaded_record.get("uploaded_media_types", []))

        media_key = str(asset.get("media_key") or "")
        folder_year = asset.get("folder_year")
        asset_id_keys = self._extract_asset_id_keys(asset)
        movies_raw = self._resolve_index_candidates(index["movies"], media_key, asset_id_keys, folder_year)
        shows_raw = self._resolve_index_candidates(index["shows"], media_key, asset_id_keys, folder_year)
        collections_raw = self._resolve_index_candidates(index["collections"], media_key, asset_id_keys, folder_year)

        if str(asset.get("asset_type") or "").lower() == "season":
            shows = self._dedupe_plex_items(shows_raw)
            if not shows:
                return False

            season_value = asset.get("season_number")
            if season_value is None:
                return False

            available_season_targets = 0
            for show in shows:
                season_obj = next((s for s in self._seasons_for_show(show) if s.index == season_value), None)
                if not season_obj:
                    continue

                available_season_targets += 1
                library_name = self._item_library_name(show)
                library_key = self._item_library_key(show)
                if not self._is_item_cached_for_library(
                    library_name=library_name,
                    library_key=library_key,
                    uploaded_to_libraries=uploaded_to_libraries,
                    uploaded_to_library_keys=uploaded_to_library_keys,
                ):
                    return False
                # Show re-added since last upload (new ratingKey) — re-apply.
                if self._rating_key_indicates_change(self._item_rating_key(show), uploaded_rating_keys):
                    return False

            return available_season_targets > 0

        inferred_filter, _reason = self._resolve_target_media_type(
            asset,
            media_type_filter=media_type_filter,
            arr_availability=arr_availability,
            movies_raw=movies_raw,
            shows_raw=shows_raw,
            collections_raw=collections_raw,
        )
        if not inferred_filter:
            return False

        candidate_groups = self._candidate_groups_for_filter(
            inferred_filter, movies_raw, shows_raw, collections_raw
        )

        matched_items: List[Any] = []
        for candidates in candidate_groups:
            deduped_candidates = self._dedupe_plex_items(candidates)
            if deduped_candidates:
                matched_items = deduped_candidates
                break

        if not matched_items:
            return False

        for item in matched_items:
            item_type = str(item.item_type or "").lower()
            # Item removed and re-added (fresh ratingKey) — not cached, re-apply.
            if self._rating_key_indicates_change(self._item_rating_key(item), uploaded_rating_keys):
                return False
            library_name = self._item_library_name(item)
            library_key = self._item_library_key(item)
            item_cached_for_library = self._is_item_cached_for_library(
                library_name=library_name,
                library_key=library_key,
                uploaded_to_libraries=uploaded_to_libraries,
                uploaded_to_library_keys=uploaded_to_library_keys,
            )

            if item_type == "movie":
                edition_title = self._movie_edition_title(item)
                if edition_title in uploaded_editions and item_cached_for_library:
                    continue

                if (
                    edition_title == self.DEFAULT_EDITION_MOVIE
                    and not uploaded_editions
                    and item_cached_for_library
                ):
                    continue

                return False

            if not item_cached_for_library:
                return False
            item_media_type = self._classify_plex_item(item)
            if uploaded_media_types and item_media_type not in uploaded_media_types:
                return False

        return True

    def _candidate_media_keys(self, title: str, year: Optional[int]) -> set[str]:
        keys = {normalize_titles(title)}
        if year is not None:
            keys.add(normalize_titles(f"{title} ({year})"))
        return keys

    def _target_asset_id_keys(
        self,
        *,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> set[str]:
        id_keys: set[str] = set()
        if isinstance(tmdb_id, int):
            id_keys.add(f"id:tmdb:{tmdb_id}")
        if isinstance(tvdb_id, int):
            id_keys.add(f"id:tvdb:{tvdb_id}")
        if isinstance(imdb_id, str) and imdb_id.strip():
            id_keys.add(f"id:imdb:{imdb_id.strip().lower()}")
        return id_keys

    def _select_local_assets_for_target(
        self,
        all_assets: List[Dict[str, Any]],
        *,
        media_type: str,
        title: str,
        year: Optional[int] = None,
        season_number: Optional[int] = None,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        media_keys = self._candidate_media_keys(title, year)

        # For collections: source drives often name collections "X Collection"
        # while the destination folder may be just "X" (or vice versa).
        # Add all variants so both naming conventions are matched.
        if media_type == "collection":
            for variant in collection_title_variants(title):
                if variant != title:
                    media_keys |= self._candidate_media_keys(variant, year)

        target_id_keys = self._target_asset_id_keys(tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id)

        local_assets = [asset for asset in all_assets if asset.get("media_key") in media_keys]
        id_matched = False
        if target_id_keys:
            id_matched_assets = [
                asset
                for asset in all_assets
                if bool(target_id_keys & set(self._extract_asset_id_keys(asset)))
            ]
            if id_matched_assets:
                local_assets = id_matched_assets
                id_matched = True

        # Year-based filtering. Only disambiguates title-based matches (e.g.
        # "Hairspray (1988)" vs "(2007)"). A unique ID match is authoritative even
        # when the on-disk folder year disagrees with Plex (e.g. folder "Michael
        # (2025)" vs Plex year 2026), so we must not year-filter ID matches.
        if year is not None and not id_matched:
            local_assets = [
                a for a in local_assets
                if a.get("folder_year") is None or a.get("folder_year") == year
            ]

        # Disambiguate collections vs movies/shows that share a normalized media_key.
        # The convention (from the scanner) is: folder with a year = movie/show,
        # folder without a year = collection. Only applied when multiple assets match.
        if len(local_assets) > 1:
            if media_type == "collection":
                no_year = [a for a in local_assets if a.get("folder_year") is None]
                if no_year:
                    local_assets = no_year
            elif media_type in {"movie", "series"}:
                with_year = [a for a in local_assets if a.get("folder_year") is not None]
                if with_year:
                    local_assets = with_year

        if media_type in {"movie", "collection"}:
            local_assets = [asset for asset in local_assets if asset.get("asset_type") == "main"]
        elif media_type == "series" and season_number is not None:
            local_assets = [
                asset
                for asset in local_assets
                if asset.get("asset_type") == "season" and asset.get("season_number") == season_number
            ]

        return local_assets

    def clear_upload_cache_for_target(
        self,
        *,
        media_type: str,
        title: str,
        year: Optional[int] = None,
        season_number: Optional[int] = None,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> int:
        """Clear upload records only for assets that match a specific webhook target."""
        destination_dir = self._get_destination_dir()
        all_assets = self._get_local_assets(destination_dir)
        if not all_assets:
            return 0

        media_type_normalized = media_type.lower().strip()
        target_assets = self._select_local_assets_for_target(
            all_assets,
            media_type=media_type_normalized,
            title=title,
            year=year,
            season_number=season_number,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
        )

        removed = 0
        for asset in target_assets:
            file_path = str(asset.get("path") or "")
            if not file_path:
                continue
            deleted = self.db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == file_path).delete()
            removed += deleted
            self._record_cache.pop(file_path, None)

        if removed > 0:
            self.db.commit()

        return removed

    def get_cached_editions_for_target(
        self,
        *,
        media_type: str,
        title: str,
        year: Optional[int] = None,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> set[str]:
        """Return the union of uploaded editions from DB records for local assets matching this target.

        Only reads the local asset list and DB cache — no Plex API calls are made, so there are no
        timing issues with Plex scan state.
        """
        try:
            destination_dir = self._get_destination_dir()
        except ValueError:
            return set()

        all_assets = self._get_local_assets(destination_dir)
        if not all_assets:
            return set()

        media_type_normalized = media_type.lower().strip()
        target_assets = self._select_local_assets_for_target(
            all_assets,
            media_type=media_type_normalized,
            title=title,
            year=year,
            season_number=None,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
        )

        result: set[str] = set()
        for asset in target_assets:
            record = self._get_uploaded_record(str(asset.get("path") or ""))
            result.update(record.get("uploaded_editions", []))
        return result

    def _get_destination_dir(self) -> Path:
        destination = Path(get_poster_destination(self.db))
        if not destination.exists() or not destination.is_dir():
            raise ValueError(f"Destination directory does not exist: {destination}")

        return destination

    def _get_plex_instances(self) -> List[Dict[str, str]]:
        instances = self._load_json_setting(
            "plex_instances",
            missing_default=[],
            invalid_json_log_level="error",
        )

        if not isinstance(instances, list):
            return []

        valid: List[Dict[str, str]] = []
        for instance in instances:
            name = str(instance.get("name", "")).strip()
            url = str(instance.get("url", "")).strip()
            api_key = str(instance.get("api_key", "")).strip()
            if name and url and api_key:
                valid.append({"name": name, "url": url, "api_key": api_key, "type": instance_type(instance)})
        return valid

    def _get_selected_libraries(
        self,
        plex_instances: List[Dict[str, str]],
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
        configured_instance_names = {instance["name"] for instance in plex_instances}

        override_enabled, override_configs, override_error = self._load_plex_upload_library_override()
        if override_error:
            return {}, override_error

        if override_enabled:
            selected_override = self._extract_selected_libraries(override_configs, configured_instance_names)
            if not selected_override:
                return {}, "No Asset Upload override libraries selected. Configure on the Asset Upload page or disable override."
            return selected_override, None

        invalid_marker = object()
        raw_configs = self._load_json_setting(
            "plex_library_config",
            missing_default=None,
            invalid_default=invalid_marker,
            invalid_json_log_level="warning",
        )
        if raw_configs is None:
            return {}, self.ERROR_NO_LIBRARIES_SELECTED

        if raw_configs is invalid_marker:
            return {}, self.ERROR_INVALID_LIBRARY_CONFIG

        if not isinstance(raw_configs, list):
            return {}, self.ERROR_INVALID_LIBRARY_CONFIG

        selected = self._extract_selected_libraries(raw_configs, configured_instance_names)

        if not selected:
            return {}, self.ERROR_NO_LIBRARIES_SELECTED

        return selected, None

    def _extract_selected_libraries(
        self,
        configs: List[Dict[str, Any]],
        configured_instance_names: set[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        selected: Dict[str, List[Dict[str, Any]]] = {}
        for config in configs:
            instance_name = str(config.get("instance_name", "")).strip()
            if instance_name not in configured_instance_names:
                continue
            libraries = config.get("libraries", [])
            if not isinstance(libraries, list):
                continue
            enabled_libraries = [lib for lib in libraries if isinstance(lib, dict) and lib.get("enabled", False)]
            if instance_name and enabled_libraries:
                selected[instance_name] = enabled_libraries
        return selected

    def _filter_libraries_for_instance(
        self,
        selected_libraries: Dict[str, List[Dict[str, Any]]],
        arr_instance: Optional[str],
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
        """Restrict selected Plex libraries to those mapped to the firing Arr instance.

        Returns ``(filtered_libraries, error)``. When the instance is unidentified or
        has no mapping configured, the full selected set is returned unchanged (today's
        fan-out behavior). When a mapping exists but resolves to none of the currently
        enabled libraries, an error is returned so the caller surfaces it instead of
        silently uploading nowhere.

        The map is stored under ``plex_upload_instance_library_map`` as
        ``{ "<arr instance>": [ {"plex_instance": "<name>", "library_key": "<key>"}, ... ] }``.
        """
        if not isinstance(arr_instance, str) or not arr_instance.strip():
            return selected_libraries, None

        instance_map = self._load_json_setting(
            self.SETTING_INSTANCE_LIBRARY_MAP,
            missing_default={},
            invalid_json_log_level="warning",
        )
        if not isinstance(instance_map, dict):
            return selected_libraries, None

        mapping = instance_map.get(arr_instance.strip())
        if not isinstance(mapping, list) or not mapping:
            # No mapping for this instance — keep current behavior (all selected libraries).
            return selected_libraries, None

        allowed: Dict[str, set[str]] = {}
        for entry in mapping:
            if not isinstance(entry, dict):
                continue
            plex_instance = str(entry.get("plex_instance", "")).strip()
            library_key = str(entry.get("library_key", "")).strip()
            if plex_instance and library_key:
                allowed.setdefault(plex_instance, set()).add(library_key)

        filtered: Dict[str, List[Dict[str, Any]]] = {}
        for plex_instance, libraries in selected_libraries.items():
            allowed_keys = allowed.get(plex_instance)
            if not allowed_keys:
                continue
            kept = [lib for lib in libraries if str(lib.get("key", "")).strip() in allowed_keys]
            if kept:
                filtered[plex_instance] = kept

        if not filtered:
            return {}, (
                f"Asset Upload instance '{arr_instance}' is mapped to libraries that are not "
                "enabled/selected. Update the instance→library map on the Asset Upload page."
            )

        log_info(
            LogTags.UPLOADER,
            "Asset Upload: scoped libraries to mapped Arr instance",
            arr_instance=arr_instance,
            plex_instances=sorted(filtered.keys()),
            libraries=sum(len(libs) for libs in filtered.values()),
        )
        return filtered, None

    def _load_plex_upload_library_override(self) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        invalid_marker = object()
        parsed = self._load_json_setting(
            "plex_upload_library_override",
            missing_default=None,
            invalid_default=invalid_marker,
        )
        if parsed is None:
            return False, [], None

        if parsed is invalid_marker:
            return False, [], "Invalid Asset Upload library override configuration. Disable override or save it again on the Asset Upload page."

        if not isinstance(parsed, dict):
            return False, [], "Invalid Asset Upload library override configuration. Disable override or save it again on the Asset Upload page."

        enabled = bool(parsed.get("enabled", False))
        configs = parsed.get("configs", [])
        if configs is None:
            configs = []
        if not isinstance(configs, list):
            return False, [], "Invalid Asset Upload library override configuration. Disable override or save it again on the Asset Upload page."

        return enabled, configs, None

    def _discover_local_assets(self, destination: Path) -> List[Dict[str, Any]]:
        assets: List[Dict[str, Any]] = []

        for file_path in destination.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in self.IMAGE_EXTENSIONS:
                continue

            rel_parts = file_path.relative_to(destination).parts
            if any(part.lower() == "tmp" for part in rel_parts):
                continue

            # Artwork (logo/background/squareart) shares the item folders but must never be
            # uploaded as a poster — it's handled by the separate artwork upload pass.
            if artwork_type_of(file_path.name):
                continue

            if file_path.parent == destination:
                parsed = self._parse_root_file(file_path)
            else:
                parsed = self._parse_asset_folder_file(file_path)

            if parsed:
                assets.append(parsed)

        # rglob() order is arbitrary; sort by title, main before seasons, seasons ascending.
        assets.sort(key=self._asset_sort_key)

        log_info(LogTags.UPLOADER, f"Discovered {len(assets)} local poster assets", count=len(assets))
        return assets

    @staticmethod
    def _asset_sort_key(asset: Dict[str, Any]) -> Tuple[str, int, int]:
        media_key = str(asset.get("media_key") or "")
        is_season = 1 if str(asset.get("asset_type") or "") == "season" else 0
        season_number = asset.get("season_number")
        season_rank = season_number if isinstance(season_number, int) else -1
        return (media_key, is_season, season_rank)

    def _parse_asset_folder_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        folder_name = file_path.parent.name
        folder_key = normalize_titles(folder_name)
        stem = file_path.stem.lower()
        display_name = self._humanize_title(folder_name)

        # Detect whether the folder name has a year — used to disambiguate
        # movies/shows (have year) from collections (no year) when they share
        # the same normalized media_key (e.g. "Men in Black" vs "Men in Black (1997)").
        year_match = re.search(r'\((\d{4})\)', folder_name)
        folder_year: Optional[int] = int(year_match.group(1)) if year_match else None

        if stem.startswith("season"):
            season_number = self._parse_season_number(stem)
            if season_number is None:
                return None
            return {
                "path": str(file_path),
                "media_key": folder_key,
                "display_name": display_name,
                "asset_type": "season",
                "season_number": season_number,
                "folder_year": folder_year,
            }

        return {
            "path": str(file_path),
            "media_key": folder_key,
            "display_name": display_name,
            "asset_type": "main",
            "season_number": None,
            "folder_year": folder_year,
        }

    def _parse_root_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        stem = file_path.stem
        lowered = stem.lower()

        season_number = None
        media_title = stem

        if "_season" in lowered:
            idx = lowered.rfind("_season")
            media_title = stem[:idx]
            season_number = self._parse_season_number(lowered[idx + 1 :])

        year_match = re.search(r'\((\d{4})\)', media_title)
        folder_year: Optional[int] = int(year_match.group(1)) if year_match else None

        return {
            "path": str(file_path),
            "media_key": normalize_titles(media_title),
            "display_name": self._humanize_title(media_title),
            "asset_type": "season" if season_number is not None else "main",
            "season_number": season_number,
            "folder_year": folder_year,
        }

    def _parse_season_number(self, value: str) -> Optional[int]:
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    def _is_artwork_upload_enabled(self) -> bool:
        setting = get_setting(self.db, self.SETTING_UPLOAD_ARTWORK)
        return bool(setting and str(setting.value).strip().lower() in ("true", "1", "yes", "on"))

    def _discover_local_artwork(self, destination: Path) -> List[Dict[str, Any]]:
        """Discover placed artwork files (logo/background/squareart) in the destination.

        Produces the same asset shape as poster discovery plus an ``artwork_type`` key, so
        the existing Plex-matching helpers apply unchanged. Artwork is per-title (no seasons).
        """
        assets: List[Dict[str, Any]] = []
        for file_path in destination.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in self.IMAGE_EXTENSIONS:
                continue
            if any(part.lower() == "tmp" for part in file_path.relative_to(destination).parts):
                continue
            parsed = self._parse_local_artwork_file(file_path, destination)
            if parsed:
                assets.append(parsed)
        assets.sort(key=lambda a: (str(a.get("media_key") or ""), str(a.get("artwork_type") or "")))
        log_info(LogTags.UPLOADER, f"Discovered {len(assets)} local artwork files", count=len(assets))
        return assets

    def _parse_local_artwork_file(self, file_path: Path, destination: Path) -> Optional[Dict[str, Any]]:
        artwork_type = artwork_type_of(file_path.name)
        if not artwork_type:
            return None
        if file_path.parent == destination:
            # Flat naming: "Title (Year) {tmdb-1}-square.png" — strip the subtype suffix.
            base = artwork_flat_base(file_path.name)
            if base is None:
                return None
        else:
            # Nested: the item folder name carries the title/year/ids.
            base = file_path.parent.name
        year_match = re.search(r'\((\d{4})\)', base)
        return {
            "path": str(file_path),
            "media_key": normalize_titles(base),
            "display_name": self._humanize_title(base),
            "asset_type": "main",
            "season_number": None,
            "folder_year": int(year_match.group(1)) if year_match else None,
            "artwork_type": artwork_type,
        }

    def _connect_media_server_client(self, instance: Dict[str, str]) -> Optional[MediaServerClient]:
        try:
            client = create_media_server_client(instance, raise_on_error=True)
            self._record_server_identity(client)
            return client
        except Exception as e:
            server_label = server_type_label(instance_type(instance))
            log_error(LogTags.UPLOADER, f"Failed to connect to {server_label} instance '{instance['name']}': {e}")
            return None

    def _record_server_identity(self, client: MediaServerClient) -> None:
        """Persist server_id → instance name/type so cache entries can show which server
        a stored library key belongs to (the key prefixes alone are opaque)."""
        if not client.server_id:
            return
        try:
            setting = get_setting(self.db, "media_server_id_map")
            data = json.loads(setting.value) if setting and setting.value else {}
            if not isinstance(data, dict):
                data = {}
            entry = {
                "name": client.instance_name or "",
                "type": client.server_type,
                # Library titles by local key, for cache-entry labels
                "libraries": {str(l.key): l.title for l in client.get_libraries()},
            }
            if data.get(client.server_id) == entry:
                return
            data[client.server_id] = entry
            upsert_setting(self.db, "media_server_id_map", json.dumps(data))
            # upsert_setting only stages; commit now so a later connect can't stage a duplicate key
            self.db.commit()
        except Exception:  # nosec B110
            # Display-only metadata — never fail a run over it; roll back so later commits still work
            try:
                self.db.rollback()
            except Exception:  # nosec B110
                pass

    def _build_plex_index(
        self,
        plex_instances: List[Dict[str, str]],
        selected_libraries: Dict[str, List[Dict[str, Any]]],
    ) -> Tuple[Dict[str, Dict[str, List[Any]]], List[Dict[str, Any]]]:
        index: Dict[str, Dict[str, List[Any]]] = {
            "movies": {},
            "shows": {},
            "collections": {},
        }
        library_totals: List[Dict[str, Any]] = []

        for instance in plex_instances:
            instance_name = instance["name"]
            client = self._connect_media_server_client(instance)
            if client is None:
                continue

            allowed = selected_libraries.get(instance_name)
            # Jellyfin box sets are server-global — index them once per instance
            per_library_collections = client.supports(CAP_PER_LIBRARY_COLLECTIONS)
            instance_collections_indexed = False
            for library in client.get_libraries():
                if allowed and not self._is_section_allowed(library, allowed):
                    continue
                if library.type not in ("movie", "show"):
                    continue

                if per_library_collections or not instance_collections_indexed:
                    collection_count = self._index_collections(client, library, index["collections"])
                    instance_collections_indexed = True
                else:
                    collection_count = 0

                if library.type == "movie":
                    movie_count = self._index_movies(client, library, index["movies"])
                    library_totals.append(
                        {
                            "instance": instance_name,
                            "library": str(library.title),
                            "section_type": "movie",
                            "items": movie_count,
                            "collections": collection_count,
                        }
                    )
                else:
                    show_count = self._index_shows(client, library, index["shows"])
                    library_totals.append(
                        {
                            "instance": instance_name,
                            "library": str(library.title),
                            "section_type": "show",
                            "items": show_count,
                            "collections": collection_count,
                        }
                    )

            # One line per server so mixed Plex/Jellyfin runs show what came from where
            instance_totals = [t for t in library_totals if t["instance"] == instance_name]
            log_info(
                LogTags.UPLOADER,
                f"Indexed {server_type_label(instance_type(instance))} '{instance_name}': "
                f"{sum(t['items'] for t in instance_totals if t['section_type'] == 'movie'):,} movies, "
                f"{sum(t['items'] for t in instance_totals if t['section_type'] == 'show'):,} shows, "
                f"{sum(t['collections'] for t in instance_totals):,} collections "
                f"across {len(instance_totals)} librar{'y' if len(instance_totals) == 1 else 'ies'}",
            )

        log_info(
            LogTags.UPLOADER,
            "Built media server index",
            movies=len(index["movies"]),
            shows=len(index["shows"]),
            collections=len(index["collections"]),
            instances=sorted({t["instance"] for t in library_totals}),
        )
        return index, library_totals

    def _build_plex_index_targeted(
        self,
        plex_instances: List[Dict[str, str]],
        selected_libraries: Dict[str, List[Dict[str, Any]]],
        *,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
        media_type: Optional[str] = None,
    ) -> Tuple[Dict[str, Dict[str, List[Any]]], List[Dict[str, Any]]]:
        """Build a minimal Plex index scoped to a single title/ID.

        Uses ``section.search(guid=...)`` to fetch only the matching item(s)
        rather than iterating the entire library.  Falls back to a title search
        when no GUID results are found (e.g. legacy Plex agents).  If the
        targeted search yields nothing at all the caller should fall back to
        ``_build_plex_index``.
        """
        # Build the ordered set of provider ids to try (client searches tmdb → tvdb → imdb).
        provider_ids: Dict[str, str] = {}
        if isinstance(tmdb_id, int):
            provider_ids["tmdb"] = str(tmdb_id)
        if isinstance(tvdb_id, int):
            provider_ids["tvdb"] = str(tvdb_id)
        if isinstance(imdb_id, str) and imdb_id.strip():
            provider_ids["imdb"] = imdb_id.strip()

        media_type_normalized = str(media_type or "").lower().strip()
        section_types: set[str] = set()
        if media_type_normalized in {"movie"}:
            section_types = {"movie"}
        elif media_type_normalized in {"series", "show"}:
            section_types = {"show"}
        else:
            section_types = {"movie", "show"}

        index: Dict[str, Dict[str, List[Any]]] = {
            "movies": {},
            "shows": {},
            "collections": {},
        }
        library_totals: List[Dict[str, Any]] = []
        found_any = False

        for instance in plex_instances:
            instance_name = instance["name"]
            client = self._connect_media_server_client(instance)
            if client is None:
                continue

            allowed = selected_libraries.get(instance_name)
            for library in client.get_libraries():
                if allowed and not self._is_section_allowed(library, allowed):
                    continue
                if library.type not in section_types:
                    continue

                # --- Provider-id search; per library, first matching id wins. Title/year
                # are hints for servers without id search (Jellyfin); Plex ignores them ---
                section_items: List[Any] = client.find_by_provider_ids(
                    provider_ids, library.type, library_keys=[library.key], title=title, year=year
                )

                # --- Title fallback (legacy agents or no GUID results) ---
                if not section_items and title:
                    title_results = client.find_by_title(title, library.type, library_keys=[library.key])
                    # Narrow by year when available to reduce false positives.
                    if isinstance(year, int) and title_results:
                        title_results = [
                            item for item in title_results
                            if getattr(item, "year", None) == year
                        ] or title_results
                    section_items.extend(title_results)

                if not section_items:
                    continue

                found_any = True
                section_title = str(library.title)

                if library.type == "movie":
                    indexed = 0
                    for movie in section_items:
                        key = self._movie_folder_key(movie)
                        if key:
                            index["movies"].setdefault(key, []).append(movie)
                        for id_key in self._extract_plex_id_keys(movie):
                            index["movies"].setdefault(id_key, []).append(movie)
                        indexed += 1
                    library_totals.append({
                        "instance": instance_name,
                        "library": section_title,
                        "section_type": "movie",
                        "items": indexed,
                        "collections": 0,
                    })
                elif library.type == "show":
                    indexed = 0
                    for show in section_items:
                        key = self._show_folder_key(show)
                        if key:
                            index["shows"].setdefault(key, []).append(show)
                        for id_key in self._extract_plex_id_keys(show):
                            index["shows"].setdefault(id_key, []).append(show)
                        indexed += 1
                    library_totals.append({
                        "instance": instance_name,
                        "library": section_title,
                        "section_type": "show",
                        "items": indexed,
                        "collections": 0,
                    })

        if found_any:
            log_info(
                LogTags.UPLOADER,
                "Built targeted media server index",
                movies=len(index["movies"]),
                shows=len(index["shows"]),
                provider_ids=provider_ids,
                title=title,
                instances=sorted({t["instance"] for t in library_totals}),
            )
        return (index, library_totals) if found_any else ({}, [])

    def prepare_webhook_context(
        self,
        *,
        tmdb_id: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
        media_type: Optional[str] = None,
        allow_full_fallback: bool = True,
        arr_instance: Optional[str] = None,
    ) -> Optional[str]:
        """Build and cache a targeted Plex index for a webhook job.

        Must be called before ``is_single_target_fully_cached``,
        ``is_series_show_poster_cached``, and ``run_single_upload`` so that all
        three share the same pre-built index without redundant full-library scans.

        Returns an error string if the context cannot be built, or ``None`` on
        success.
        """
        try:
            destination_dir: Optional[Path] = self._get_destination_dir()
        except Exception:
            # destination_dir is validated separately per-operation; falling
            # through here with None lets the subsequent cache/upload checks
            # fail gracefully on their own.
            destination_dir = None

        plex_instances = self._get_plex_instances()

        if not plex_instances:
            return self.ERROR_NO_PLEX_INSTANCES

        selected_libraries, selected_libraries_error = self._get_selected_libraries(plex_instances)
        if selected_libraries_error:
            return selected_libraries_error

        # Scope the upload to the libraries the firing Arr instance feeds. Unmapped
        # instances (or no map configured) keep the full selected set unchanged.
        selected_libraries, instance_scope_error = self._filter_libraries_for_instance(
            selected_libraries, arr_instance
        )
        if instance_scope_error:
            return instance_scope_error

        # Try targeted search first.
        index, library_totals = self._build_plex_index_targeted(
            plex_instances,
            selected_libraries,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            title=title,
            year=year,
            media_type=media_type,
        )

        # Fall back to full index when targeted search found nothing (legacy agents).
        if not index:
            if allow_full_fallback:
                log_info(
                    LogTags.UPLOADER,
                    "Targeted index: no results — falling back to full library index",
                    title=title,
                    media_type=media_type,
                )
                index, library_totals = self._build_plex_index(plex_instances, selected_libraries)
                if not index or not library_totals:
                    return self.ERROR_INDEX_BUILD_FAILED
            else:
                log_info(
                    LogTags.UPLOADER,
                    "Targeted index: no results — will retry with targeted search",
                    title=title,
                    media_type=media_type,
                )
                self._preflight_context_cache = (None, destination_dir, {"movies": {}, "shows": {}, "collections": {}}, [])
                return None

        self._preflight_context_cache = (None, destination_dir, index, library_totals)
        return None

    def _is_section_allowed(self, section: Any, allowed: List[Dict[str, Any]]) -> bool:
        section_key = str(getattr(section, "key", ""))
        section_title = str(getattr(section, "title", ""))

        for library in allowed:
            if str(library.get("key", "")) == section_key:
                return True
            if str(library.get("title", "")) == section_title:
                return True
        return False

    def _index_movies(self, client: MediaServerClient, library: Any, movie_index: Dict[str, List[Any]]) -> int:
        indexed = 0
        try:
            for movie in client.get_library_items(library.key):
                key = self._movie_folder_key(movie)
                if key:
                    movie_index.setdefault(key, []).append(movie)
                for id_key in self._extract_plex_id_keys(movie):
                    movie_index.setdefault(id_key, []).append(movie)
                indexed += 1
        except Exception as e:
            log_warning(LogTags.UPLOADER, f"Failed indexing movie section '{library.title}': {e}")
        return indexed

    def _index_shows(self, client: MediaServerClient, library: Any, show_index: Dict[str, List[Any]]) -> int:
        indexed = 0
        try:
            for show in client.get_library_items(library.key):
                key = self._show_folder_key(show)
                if key:
                    show_index.setdefault(key, []).append(show)
                for id_key in self._extract_plex_id_keys(show):
                    show_index.setdefault(id_key, []).append(show)
                indexed += 1
        except Exception as e:
            log_warning(LogTags.UPLOADER, f"Failed indexing show section '{library.title}': {e}")
        return indexed

    def _index_collections(self, client: MediaServerClient, library: Any, collection_index: Dict[str, List[Any]]) -> int:
        indexed = 0
        try:
            for collection in client.get_collections(library.key):
                key = normalize_titles(collection.title)
                collection_index.setdefault(key, []).append(collection)
                indexed += 1
        except Exception as e:
            log_warning(LogTags.UPLOADER, f"Failed indexing collections for '{library.title}': {e}")
        return indexed

    @staticmethod
    def _title_year_key(item: Any) -> Optional[str]:
        """Normalized fallback key from an item's title (and year when present)."""
        title = item.title
        year = item.year
        if title and year:
            return normalize_titles(f"{title} ({year})")
        if title:
            return normalize_titles(str(title))
        return None

    def _movie_folder_key(self, movie: MediaServerItem) -> Optional[str]:
        try:
            part_file = movie.paths[0]
            return normalize_titles(Path(part_file).parent.name)
        except Exception:
            return self._title_year_key(movie)

    _SEASON_FOLDER_RE = re.compile(r"^(season\s*\d+|specials?|extras?|featurettes?)$", re.IGNORECASE)

    def _show_folder_key(self, show: MediaServerItem) -> Optional[str]:
        # Prefer the show's location paths — available when the bulk library
        # listing includes location data, gives the show folder directly.
        try:
            if show.paths:
                return normalize_titles(Path(show.paths[0]).name)
        except Exception:
            pass

        # Fall back to navigating episode file paths via the native plexapi
        # object (handles older plexapi versions or servers that don't return
        # locations in bulk queries).
        try:
            native = show.native
            seasons = native.seasons()
            if not seasons:
                return normalize_titles(show.title)
            for season in seasons:
                episodes = season.episodes()
                if not episodes:
                    continue
                part_file = episodes[0].media[0].parts[0].file
                episode_path = Path(part_file)
                parent = episode_path.parent
                # Episodes may be stored flat inside the show folder (no season
                # subfolder) or inside a named season subfolder. Detect which
                # layout is in use so we always return the show-level folder name.
                if self._SEASON_FOLDER_RE.match(parent.name):
                    folder_name = parent.parent.name
                else:
                    folder_name = parent.name
                return normalize_titles(folder_name)
            return self._title_year_key(show)
        except Exception:
            return self._title_year_key(show)

    @contextmanager
    def _upload_ready(self, file_path: str):
        """Yield a path safe to upload: the original, or a downscaled temp copy when the image
        is too large for Plex (which 500s on very large images). Gated on longest side AND file
        size (~10MB, the threshold Kometa flags). Temp copy is cleaned up on exit."""
        try:
            from PIL import Image
            with Image.open(file_path) as im:
                width, height = im.size
                fmt = im.format
            file_size = os.path.getsize(file_path)
        except Exception:
            yield file_path  # unreadable / not an image — let Plex decide
            return

        if max(width, height) <= self.MAX_UPLOAD_DIMENSION and file_size <= self.MAX_UPLOAD_BYTES:
            yield file_path
            return

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(file_path)[1] or ".png")
            os.close(fd)
            save_kwargs = {"quality": 95} if fmt == "JPEG" else {}
            # Cap the longest side, then shrink further if the encoded file is still too large
            # (e.g. a dense PNG under the dimension cap but over the byte cap).
            longest = min(max(width, height), self.MAX_UPLOAD_DIMENSION)
            new_size = (width, height)
            for _ in range(6):
                scale = longest / max(width, height)
                new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
                with Image.open(file_path) as im:
                    im.resize(new_size, Image.LANCZOS).save(tmp_path, format=fmt, **save_kwargs)
                if os.path.getsize(tmp_path) <= self.MAX_UPLOAD_BYTES or longest <= 1000:
                    break
                longest = int(longest * 0.85)
            log_info(
                LogTags.UPLOADER,
                f"Downscaled oversized image {width}x{height} ({file_size // 1024} KB) -> "
                f"{new_size[0]}x{new_size[1]} ({os.path.getsize(tmp_path) // 1024} KB) before upload",
                file=file_path,
            )
            yield tmp_path
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _upload_asset(
        self,
        asset: Dict[str, Any],
        index: Dict[str, Dict[str, List[Any]]],
        dry_run: bool,
        media_type_filter: Optional[str] = None,
        arr_availability: Optional[Dict[str, Any]] = None,
        remove_overlay_label: bool = False,
    ) -> AssetOutcome:
        media_key = asset["media_key"]
        file_path = asset["path"]
        asset_label = self._asset_label(asset)
        asset_id_keys = self._extract_asset_id_keys(asset)
        if not asset_id_keys and arr_availability:
            arr_inferred = media_type_filter
            asset_id_keys = self._arr_id_keys_for_asset(asset, arr_availability, inferred_filter=arr_inferred)
        media_counts = self._empty_media_upload_counts()
        uploaded_record = self._get_uploaded_record(file_path)
        uploaded_to_libraries = set(uploaded_record.get("uploaded_to_libraries", []))
        uploaded_to_library_keys = set(uploaded_record.get("uploaded_to_library_keys", []))
        uploaded_rating_keys = set(uploaded_record.get("uploaded_to_rating_keys", []))
        uploaded_editions = set(uploaded_record.get("uploaded_editions", []))
        uploaded_media_types = set(uploaded_record.get("uploaded_media_types", []))

        folder_year = asset.get("folder_year")
        movies_raw = self._resolve_index_candidates(index["movies"], media_key, asset_id_keys, folder_year)
        shows_raw = self._resolve_index_candidates(index["shows"], media_key, asset_id_keys, folder_year)
        collections_raw = self._resolve_index_candidates(index["collections"], media_key, asset_id_keys, folder_year)

        if asset["asset_type"] == "season":
            shows = self._dedupe_plex_items(shows_raw)
            if not shows:
                # Undownloaded shows are absent from Plex because of that — blame *arr,
                # not the match, so seasons agree with the show's own poster.
                available, unavailable_reason = self._asset_has_arr_availability(
                    asset, "series", arr_availability, asset_id_keys=asset_id_keys,
                )
                if media_key not in self._logged_missing_show_keys:
                    self._logged_missing_show_keys.add(media_key)
                    show_label = str(asset.get("display_name") or media_key)
                    cause = "Sonarr reports no downloaded episodes" if not available else "no server show match"
                    log_debug(
                        LogTags.UPLOADER,
                        f"Season posters for '{show_label}' can't be applied yet: {cause}",
                        file=file_path,
                    )
                return AssetOutcome(
                    0, False, 0, media_counts,
                    skip_reason=(
                        "not_downloaded" if not available
                        else self._diagnose_no_match(index, media_key, asset_id_keys, folder_year)
                    ),
                )

            uploaded = 0
            cached_skips = 0
            for show in shows:
                season_number = asset["season_number"]
                library_name = self._item_library_name(show)
                library_key = self._item_library_key(show)
                # Use the show's ratingKey (a single re-added season under an unchanged show isn't caught, but that avoids a seasons() call per cached item).
                rating_key = self._item_rating_key(show)
                rating_key_changed = self._rating_key_indicates_change(rating_key, uploaded_rating_keys)
                if not rating_key_changed and self._is_item_cached_for_library(
                    library_name=library_name,
                    library_key=library_key,
                    uploaded_to_libraries=uploaded_to_libraries,
                    uploaded_to_library_keys=uploaded_to_library_keys,
                ):
                    self._record_rating_key_if_new(
                        file_path,
                        rating_key,
                        uploaded_rating_keys,
                        dry_run=dry_run,
                        library_name=library_name,
                        library_key=library_key,
                        media_type="seasons",
                    )
                    cached_skips += 1
                    continue
                season_obj = next((s for s in self._seasons_for_show(show) if s.index == season_number), None)
                if not season_obj:
                    continue
                if dry_run:
                    log_info(
                        LogTags.UPLOADER,
                        f"Dry run: Uploaded Poster for {self._describe_show_with_season(show.title, season_number)} → {self._item_server_label(show)}",
                        file=file_path,
                        asset=asset_label,
                    )
                    uploaded += 1
                    media_counts["seasons"] += 1
                    continue
                with self._upload_ready(file_path) as up_path:
                    self._client_upload(season_obj, IMAGE_KIND_POSTER, up_path)
                self._drop_file_cache(file_path)
                time.sleep(self.upload_delay_ms / 2000.0)
                if remove_overlay_label:
                    self._remove_overlay_label_if_present(season_obj, file_path=file_path)
                uploaded += 1
                media_counts["seasons"] += 1
                if library_name:
                    uploaded_to_libraries.add(library_name)
                if library_key:
                    uploaded_to_library_keys.add(library_key)
                if rating_key:
                    uploaded_rating_keys.add(rating_key)
                self._mark_uploaded(
                    file_path,
                    library_name=library_name,
                    library_key=library_key,
                    media_type="seasons",
                    rating_key=rating_key,
                )
                log_info(
                    LogTags.UPLOADER,
                    f"Uploaded Poster for {self._describe_show_with_season(show.title, season_number)} → {self._item_server_label(show)}",
                    file=file_path,
                    asset=asset_label,
                )
            if uploaded == 0:
                season_number = asset.get("season_number")
                if cached_skips > 0:
                    log_debug(
                        LogTags.UPLOADER,
                        f"Season asset already uploaded and cached for matched libraries: {asset_label}",
                        file=file_path,
                    )
                elif season_number is not None:
                    library_labels = self._library_labels_for_items(shows)
                    libraries_text = ", ".join(library_labels) if library_labels else "the server"
                    log_debug(
                        LogTags.UPLOADER,
                        f"No Season {int(season_number):02} found in {libraries_text} for {asset.get('display_name', media_key)}",
                        file=file_path,
                    )
                    return AssetOutcome(uploaded, True, len(shows), media_counts, seasons_missing=1)
            if uploaded > 0:
                self._note_year_discrepancy(asset, shows, asset_id_keys, file_path)
            return AssetOutcome(uploaded, True, len(shows), media_counts)

        inferred_filter, resolution_reason = self._resolve_target_media_type(
            asset,
            media_type_filter=media_type_filter,
            arr_availability=arr_availability,
            movies_raw=movies_raw,
            shows_raw=shows_raw,
            collections_raw=collections_raw,
        )
        if not inferred_filter:
            if resolution_reason:
                log_info(
                    LogTags.UPLOADER,
                    f"Skipping ambiguous no-ID asset: {asset_label} ({resolution_reason})",
                    file=file_path,
                )
                return AssetOutcome(0, False, 0, media_counts, skip_reason="type_unresolved")
            self._log_unmatched(f"No server match for asset: {asset_label}", file=file_path)
            return AssetOutcome(
                0, False, 0, media_counts,
                skip_reason=self._diagnose_no_match(index, media_key, asset_id_keys, folder_year),
            )

        available, unavailable_reason = self._asset_has_arr_availability(
            asset,
            inferred_filter,
            arr_availability,
            asset_id_keys=asset_id_keys,
        )
        if not available:
            log_info(
                LogTags.UPLOADER,
                f"Skipping unavailable asset: {asset_label} ({unavailable_reason})",
                file=file_path,
            )
            return AssetOutcome(0, False, 0, media_counts, skip_reason="not_downloaded")
        candidate_groups = self._candidate_groups_for_filter(
            inferred_filter, movies_raw, shows_raw, collections_raw
        )

        matched_items: List[Any] = []
        for candidates in candidate_groups:
            deduped_candidates = self._dedupe_plex_items(candidates)
            if deduped_candidates:
                matched_items = deduped_candidates
                break

        if not matched_items:
            self._log_unmatched(
                f"No server match for asset: {asset_label} (inferred_filter={inferred_filter}, "
                f"movies_raw={len(movies_raw)}, shows_raw={len(shows_raw)}, collections_raw={len(collections_raw)})",
                file=file_path,
            )
            return AssetOutcome(
                0, False, 0, media_counts,
                skip_reason=self._diagnose_no_match(index, media_key, asset_id_keys, folder_year),
            )

        if inferred_filter == "movie" and self._expected_edition is not None:
            expected = self._normalize_edition(self._expected_edition)
            present_editions = [self._movie_edition_title(m) for m in matched_items]
            matched_items = [
                m for m in matched_items
                if self._normalize_edition(self._movie_edition_title(m)) == expected
            ]
            if not matched_items:
                log_info(
                    LogTags.UPLOADER,
                    f"Edition upgrade: Plex does not have edition '{self._expected_edition}' yet for "
                    f"{asset_label} (present: {sorted(set(present_editions))}); deferring upload to retry",
                    file=file_path,
                )
                return AssetOutcome(0, False, 0, media_counts, skip_reason="edition_pending")

        if inferred_filter == "movie" and uploaded_editions:
            live_editions = {self._movie_edition_title(m) for m in matched_items}
            stale_editions = uploaded_editions - live_editions
            if stale_editions:
                log_info(
                    LogTags.UPLOADER,
                    f"Edition change for {asset_label}: cached edition(s) {sorted(stale_editions)} no longer "
                    f"on Plex (live: {sorted(live_editions)}); re-uploading current edition(s)",
                    file=file_path,
                )
                if not dry_run:
                    self.db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == file_path).delete()
                    self.db.commit()
                    self._record_cache.pop(file_path, None)
                uploaded_to_libraries = set()
                uploaded_to_library_keys = set()
                uploaded_rating_keys = set()
                uploaded_editions = set()
                uploaded_media_types = set()

        uploaded = 0
        for item in matched_items:
            item_label = self._describe_plex_item(item)
            item_type = str(item.item_type or "").lower()
            item_media_type = self._classify_plex_item(item)
            library_name = self._item_library_name(item)
            library_key = self._item_library_key(item)
            item_cached_for_library = self._is_item_cached_for_library(
                library_name=library_name,
                library_key=library_key,
                uploaded_to_libraries=uploaded_to_libraries,
                uploaded_to_library_keys=uploaded_to_library_keys,
            )
            rating_key = self._item_rating_key(item)
            rating_key_changed = self._rating_key_indicates_change(rating_key, uploaded_rating_keys)
            item_already_applied = item_cached_for_library and not rating_key_changed

            if item_type == "movie":
                edition_title = self._movie_edition_title(item)
                if edition_title in uploaded_editions and item_already_applied:
                    self._record_rating_key_if_new(
                        file_path,
                        rating_key,
                        uploaded_rating_keys,
                        dry_run=dry_run,
                        library_name=library_name,
                        library_key=library_key,
                        edition_title=edition_title,
                        media_type="movies",
                    )
                    log_debug(
                        LogTags.UPLOADER,
                        f"Skipping cached movie edition upload for {item_label} ({edition_title})",
                        file=file_path,
                    )
                    continue

                if (
                    edition_title == self.DEFAULT_EDITION_MOVIE
                    and not uploaded_editions
                    and item_already_applied
                ):
                    if rating_key:
                        uploaded_rating_keys.add(rating_key)
                    self._mark_uploaded(
                        file_path,
                        library_name=library_name,
                        library_key=library_key,
                        edition_title=edition_title,
                        media_type="movies",
                        rating_key=rating_key,
                    )
                    uploaded_editions.add(edition_title)
                    log_debug(
                        LogTags.UPLOADER,
                        f"Skipping cached default movie edition upload for {item_label}",
                        file=file_path,
                    )
                    continue
            else:
                if item_already_applied and (not uploaded_media_types or item_media_type in uploaded_media_types):
                    self._record_rating_key_if_new(
                        file_path,
                        rating_key,
                        uploaded_rating_keys,
                        dry_run=dry_run,
                        library_name=library_name,
                        library_key=library_key,
                        media_type=item_media_type,
                    )
                    log_debug(
                        LogTags.UPLOADER,
                        f"Skipping cached upload for {item_label} in {library_name} → {self._item_server_label(item)}",
                        file=file_path,
                    )
                    continue

            if dry_run:
                log_info(
                    LogTags.UPLOADER,
                    f"Dry run: Uploaded Poster for {item_label} → {self._item_server_label(item)}",
                    file=file_path,
                    asset=asset_label,
                )
                uploaded += 1
                media_counts[item_media_type] += 1
                continue
            with self._upload_ready(file_path) as up_path:
                self._client_upload(item, IMAGE_KIND_POSTER, up_path)
            self._drop_file_cache(file_path)
            time.sleep(self.upload_delay_ms / 1000.0)
            if remove_overlay_label:
                self._remove_overlay_label_if_present(item, file_path=file_path)
            uploaded += 1
            media_counts[item_media_type] += 1
            if library_name:
                uploaded_to_libraries.add(library_name)
            if library_key:
                uploaded_to_library_keys.add(library_key)
            if rating_key:
                uploaded_rating_keys.add(rating_key)
            if item_type == "movie":
                edition_title = self._movie_edition_title(item)
                uploaded_editions.add(edition_title)
                self._mark_uploaded(
                    file_path,
                    library_name=library_name,
                    library_key=library_key,
                    edition_title=edition_title,
                    media_type="movies",
                    rating_key=rating_key,
                )
            else:
                self._mark_uploaded(
                    file_path,
                    library_name=library_name,
                    library_key=library_key,
                    media_type=item_media_type,
                    rating_key=rating_key,
                )
            log_info(LogTags.UPLOADER, f"Uploaded Poster for {item_label} → {self._item_server_label(item)}", file=file_path, asset=asset_label)

        if uploaded > 0:
            self._note_year_discrepancy(asset, matched_items, asset_id_keys, file_path)

        return AssetOutcome(uploaded, True, len(matched_items), media_counts)

    def _arr_not_downloaded_reason(
        self,
        asset: Dict[str, Any],
        arr_availability: Optional[Dict[str, Any]],
        asset_id_keys: List[str],
    ) -> Optional[str]:
        """*arr's wording when it reports nothing downloaded, else None. Asks both
        namespaces since a missing item usually can't be resolved to movie-vs-show."""
        # Collections carry neither year nor ids; without one, don't claim *arr's answer
        # (an "Alien" collection would inherit the undownloaded movie's label).
        if not arr_availability:
            return None
        if asset.get("folder_year") is None and not asset_id_keys:
            return None
        for media_filter in ("movie", "series"):
            available, reason = self._asset_has_arr_availability(
                asset, media_filter, arr_availability, asset_id_keys=asset_id_keys,
            )
            if not available:
                return reason or "not downloaded"
        return None

    def _diagnose_no_match(
        self,
        index: Dict[str, Dict[str, List[Any]]],
        media_key: str,
        asset_id_keys: List[str],
        folder_year: Optional[int],
    ) -> str:
        """Split "nothing in Plex" from "in Plex under a different year"."""
        if folder_year is None:
            return "no_plex_match"
        for section in ("movies", "shows", "collections"):
            index_map = index.get(section) or {}
            pool: List[Any] = []
            for id_key in asset_id_keys:
                pool.extend(index_map.get(id_key, []))
            pool.extend(index_map.get(media_key, []))
            if pool and not any(getattr(item, "year", None) == folder_year for item in pool):
                return "year_mismatch"
        return "no_plex_match"

    def _upload_artwork_asset(
        self,
        asset: Dict[str, Any],
        index: Dict[str, Dict[str, List[Any]]],
        dry_run: bool,
        arr_availability: Optional[Dict[str, Any]] = None,
    ) -> AssetOutcome:
        """Upload one artwork file (logo/background/squareart) to its matched Plex item(s).

        Reuses the poster path's Plex matching + per-file dedupe; simpler because artwork has
        no seasons or editions and pushes via the client's subtype-specific image upload.
        """
        artwork_type = str(asset.get("artwork_type") or "")
        if artwork_type not in self.ARTWORK_KINDS:
            return AssetOutcome(0, False, 0, {})

        file_path = asset["path"]
        media_key = asset["media_key"]
        asset_label = self._asset_label(asset)
        asset_id_keys = self._extract_asset_id_keys(asset)
        folder_year = asset.get("folder_year")

        movies_raw = self._resolve_index_candidates(index["movies"], media_key, asset_id_keys, folder_year)
        shows_raw = self._resolve_index_candidates(index["shows"], media_key, asset_id_keys, folder_year)
        collections_raw = self._resolve_index_candidates(index["collections"], media_key, asset_id_keys, folder_year)

        inferred_filter, resolution_reason = self._resolve_target_media_type(
            asset,
            media_type_filter=None,
            # Same index posters use; without it movie-vs-show ties are unresolvable.
            arr_availability=arr_availability,
            movies_raw=movies_raw,
            shows_raw=shows_raw,
            collections_raw=collections_raw,
        )

        def _no_match_outcome() -> AssetOutcome:
            """Explain a miss. *arr is asked only here, never before matching — it
            spans both namespaces and would otherwise veto a real Plex match."""
            reason = self._arr_not_downloaded_reason(asset, arr_availability, asset_id_keys)
            if reason:
                log_info(
                    LogTags.UPLOADER,
                    f"Skipping unavailable {artwork_type}: {asset_label} ({reason})",
                    file=file_path,
                )
                return AssetOutcome(0, False, 0, {}, skip_reason="not_downloaded")
            self._log_unmatched(f"No server match for {artwork_type}: {asset_label}", file=file_path)
            return AssetOutcome(
                0, False, 0, {},
                skip_reason=self._diagnose_no_match(index, media_key, asset_id_keys, folder_year),
            )

        if not inferred_filter:
            if not resolution_reason:
                return _no_match_outcome()
            # Artwork used to return silently, leaving the bucket undiagnosable.
            self._log_unmatched(
                f"Skipping ambiguous no-ID {artwork_type}: {asset_label} ({resolution_reason})",
                file=file_path,
            )
            return AssetOutcome(0, False, 0, {}, skip_reason="type_unresolved")

        matched_items: List[Any] = []
        for candidates in self._candidate_groups_for_filter(inferred_filter, movies_raw, shows_raw, collections_raw):
            deduped = self._dedupe_plex_items(candidates)
            if deduped:
                matched_items = deduped
                break
        if not matched_items:
            return _no_match_outcome()

        record = self._get_uploaded_record(file_path)
        uploaded_to_libraries = set(record.get("uploaded_to_libraries", []))
        uploaded_to_library_keys = set(record.get("uploaded_to_library_keys", []))
        uploaded_rating_keys = set(record.get("uploaded_to_rating_keys", []))

        uploaded = 0
        for item in matched_items:
            item_label = self._describe_plex_item(item)
            if not self._item_supports_image_kind(item, artwork_type):
                log_debug(
                    LogTags.UPLOADER,
                    f"Skipping {artwork_type} for {item_label}: not supported by this media server",
                    file=file_path,
                )
                continue
            item_media_type = self._classify_plex_item(item)
            library_name = self._item_library_name(item)
            library_key = self._item_library_key(item)
            rating_key = self._item_rating_key(item)
            rating_key_changed = self._rating_key_indicates_change(rating_key, uploaded_rating_keys)
            cached = self._is_item_cached_for_library(
                library_name=library_name,
                library_key=library_key,
                uploaded_to_libraries=uploaded_to_libraries,
                uploaded_to_library_keys=uploaded_to_library_keys,
            )
            if cached and not rating_key_changed:
                self._record_rating_key_if_new(
                    file_path, rating_key, uploaded_rating_keys, dry_run=dry_run,
                    library_name=library_name, library_key=library_key, media_type=item_media_type,
                )
                continue
            if dry_run:
                log_info(LogTags.UPLOADER, f"Dry run: Uploaded {artwork_type} for {item_label} → {self._item_server_label(item)}", file=file_path, asset=asset_label)
                uploaded += 1
                continue
            with self._upload_ready(file_path) as up_path:
                self._client_upload(item, artwork_type, up_path)
            self._drop_file_cache(file_path)
            time.sleep(self.upload_delay_ms / 1000.0)
            uploaded += 1
            if library_name:
                uploaded_to_libraries.add(library_name)
            if library_key:
                uploaded_to_library_keys.add(library_key)
            if rating_key:
                uploaded_rating_keys.add(rating_key)
            self._mark_uploaded(
                file_path, library_name=library_name, library_key=library_key,
                media_type=item_media_type, rating_key=rating_key,
            )
            log_info(LogTags.UPLOADER, f"Uploaded {artwork_type} for {item_label} → {self._item_server_label(item)}", file=file_path, asset=asset_label)

        return AssetOutcome(uploaded, True, len(matched_items), {})

    def _log_artwork_summary(self, stats: Dict[str, Any], *, dry_run: bool) -> None:
        """One line per run so artwork is visible without reading every per-file line.
        Logged by both the full and single-item paths."""
        for line in artwork_summary_lines(stats["artwork"], dry_run=dry_run):
            log_info(LogTags.UPLOADER, line)

    def _process_artwork_for_upload(
        self,
        *,
        artwork_assets: List[Dict[str, Any]],
        index: Dict[str, Dict[str, List[Any]]],
        stats: Dict[str, Any],
        dry_run: bool,
        arr_availability: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[PlexUploadProgressCallback] = None,
    ) -> None:
        art = stats["artwork"]
        total = len(artwork_assets)
        for i, asset in enumerate(artwork_assets, start=1):
            try:
                outcome = self._upload_artwork_asset(asset, index, dry_run, arr_availability=arr_availability)
                uploaded_count = outcome.uploaded
                if outcome.matched:
                    art["matched"] += 1
                    art["uploaded_files" if uploaded_count > 0 else "already_current"] += 1
                elif outcome.skip_reason in art["unmatched_reasons"]:
                    art["unmatched_reasons"][outcome.skip_reason] += 1
                if dry_run:
                    art["would_upload"] += uploaded_count
                else:
                    art["uploaded"] += uploaded_count
                if uploaded_count == 0:
                    art["skipped"] += 1
                else:
                    atype = str(asset.get("artwork_type") or "")
                    if atype in art["by_type"]:
                        art["by_type"][atype] += uploaded_count
            except Exception as e:
                art["errors"] += 1
                log_error(LogTags.UPLOADER, f"Failed processing artwork '{asset.get('path')}': {e}\n{traceback.format_exc()}")
            if progress_callback:
                try:
                    file_name = Path(str(asset.get("path") or "")).name
                    label = "would upload" if dry_run else "uploaded"
                    value = art["would_upload"] if dry_run else art["uploaded"]
                    progress_callback(
                        i, total,
                        {"matched": art["matched"], "uploaded": art["uploaded"], "would_upload": art["would_upload"], "skipped": art["skipped"], "errors": art["errors"]},
                        f"Artwork {i}/{total}: {file_name} | {label}={value}, "
                        f"already current={art['already_current']}, errors={art['errors']}",
                    )
                except Exception as callback_error:
                    log_warning(LogTags.UPLOADER, f"Artwork upload progress callback failed: {callback_error}")

    def _note_year_discrepancy(
        self,
        asset: Dict[str, Any],
        matched_items: List[Any],
        asset_id_keys: List[Any],
        file_path: str,
    ) -> None:
        """Record (and warn) when a poster was matched to a Plex item by unique ID but the
        on-disk asset folder's year disagrees with the server item's year.

        The upload is correct — the ID is authoritative — but the year mismatch signals
        stale metadata on one side that the user may want to reconcile."""
        folder_year = asset.get("folder_year")
        if folder_year is None or not asset_id_keys:
            return

        for item in matched_items:
            plex_year = getattr(item, "year", None)
            if not isinstance(plex_year, int) or plex_year == folder_year:
                continue

            title = str(asset.get("display_name") or asset.get("media_key") or "Unknown")
            descriptor = {
                "title": title,
                "folder_year": int(folder_year),
                "plex_year": int(plex_year),
            }
            if descriptor not in self._year_discrepancies:
                self._year_discrepancies.append(descriptor)

            log_warning(
                LogTags.UPLOADER,
                f"Year discrepancy: matched '{title}' by ID and uploaded, but server year "
                f"{plex_year} differs from the asset folder's year {folder_year}",
                file=file_path,
                title=title,
                plex_year=plex_year,
                folder_year=folder_year,
            )
            return

    def _seasons_for_show(self, show: MediaServerItem) -> List[MediaServerItem]:
        """Season list for a show, cached per run."""
        key = f"{getattr(show.client, 'server_id', '') or ''}:{show.item_id}"
        cached = self._season_list_cache.get(key)
        if cached is None:
            cached = show.client.get_seasons(show)
            self._season_list_cache[key] = cached
        return cached

    def _item_server_label(self, item: MediaServerItem) -> str:
        """'Plex' / 'Jellyfin', with the instance name when it adds information."""
        client = item.client
        if client is None:
            return ""
        return server_label(
            str(getattr(client, "server_type", "") or ""),
            str(getattr(client, "instance_name", "") or ""),
        )

    def _item_supports_image_kind(self, item: MediaServerItem, kind: str) -> bool:
        """Only squareart is capability-gated (no Jellyfin image type for it)."""
        if kind != IMAGE_KIND_SQUAREART:
            return True
        client = item.client
        return bool(client is None or client.supports(CAP_SQUAREART))

    def _client_upload(self, item: MediaServerItem, kind: str, filepath: str) -> None:
        """Push an image via the item's owning media server client; transport errors propagate."""
        client = item.client
        if client is None or not client.upload_image(item, kind, filepath):
            raise RuntimeError(f"{kind} upload not supported for this media server item")

    def _remove_overlay_label_if_present(self, item: MediaServerItem, *, file_path: str) -> None:
        try:
            native = item.native
            if native is None:
                return
            labels = getattr(native, "labels", None)
            if not labels:
                return

            has_overlay_label = any(str(getattr(label, "tag", "")).strip().lower() == "overlay" for label in labels)
            if not has_overlay_label:
                return

            remove_label = getattr(native, "removeLabel", None)
            if callable(remove_label):
                remove_label(["Overlay"])
        except Exception as e:
            log_warning(
                LogTags.UPLOADER,
                f"Failed to remove Overlay label after upload: {e}",
                file=file_path,
            )

    def _infer_media_type_filter_from_asset(self, asset: Dict[str, Any]) -> Optional[str]:
        file_path = str(asset.get("path", "")).lower()

        # {tvdb-} is a reliable unique signal for TV series.
        # {imdb-} and {tmdb-} are intentionally NOT used here — both movies and
        # TV shows share these IDs, so inferring "movie" from them causes show
        # posters in imdb-keyed folders to be silently skipped (no movie match
        # found in Plex for what is actually a series). Let the Plex index
        # resolution in _resolve_target_media_type handle ambiguous cases.
        if "{tvdb-" in file_path:
            return "series"

        return None

    def _infer_media_type_filter_from_arr(
        self,
        asset: Dict[str, Any],
        arr_availability: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not arr_availability:
            return None

        media_key = str(asset.get("media_key", "")).strip()
        if not media_key:
            return None

        asset_id_keys = self._extract_asset_id_keys(asset)
        movies_index = arr_availability.get("movies", {})
        shows_index = arr_availability.get("shows", {})

        # Check by ID keys first — these are unambiguous
        for id_key in asset_id_keys:
            in_movies_by_id = id_key in movies_index
            in_shows_by_id = id_key in shows_index
            if in_movies_by_id and not in_shows_by_id:
                return "movie"
            if in_shows_by_id and not in_movies_by_id:
                return "series"
            if in_movies_by_id and in_shows_by_id:
                # Same ID in both — genuinely ambiguous even with IDs
                return "ambiguous"

        # Fall back to title-based lookup
        in_movies = media_key in movies_index
        in_shows = media_key in shows_index

        if in_movies and in_shows:
            return "ambiguous"
        if in_movies:
            return "movie"
        if in_shows:
            return "series"
        return None

    def _resolve_movie_show_filter(
        self,
        asset: Dict[str, Any],
        arr_availability: Optional[Dict[str, Any]],
        *,
        has_movies: bool,
        has_shows: bool,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Disambiguate a movie-vs-series asset via the ARR title index, falling
        back to which Plex sections produced matches.

        When ARR reports the title in both catalogs ("ambiguous"), a Plex section
        that found it unambiguously is trusted over the title-based ARR collision;
        otherwise the collision is reported to the caller.
        """
        arr_inferred_filter = self._infer_media_type_filter_from_arr(asset, arr_availability)
        if arr_inferred_filter == "ambiguous":
            if has_movies and not has_shows:
                return "movie", None
            if has_shows and not has_movies:
                return "series", None
            return None, "ARR matched both movie and series"
        if arr_inferred_filter:
            return arr_inferred_filter, None
        if has_movies and has_shows:
            return None, "matched in both movie and show sections"
        if has_movies:
            return "movie", None
        if has_shows:
            return "series", None
        return None, None

    def _resolve_target_media_type(
        self,
        asset: Dict[str, Any],
        *,
        media_type_filter: Optional[str],
        arr_availability: Optional[Dict[str, Any]],
        movies_raw: List[Any],
        shows_raw: List[Any],
        collections_raw: List[Any],
    ) -> Tuple[Optional[str], Optional[str]]:
        if media_type_filter in {"series", "collection"}:
            if media_type_filter != "collection" and self._dedupe_plex_items(collections_raw):
                return "collection", None
            return media_type_filter, None

        if media_type_filter == "movie":
            # Explicit movie filter: honour it directly.  Don't redirect to a
            # collection even when one with the same normalized title exists.
            return "movie", None

        explicit_filter = self._infer_media_type_filter_from_asset(asset)
        if explicit_filter:
            return explicit_filter, None

        if str(asset.get("asset_type", "")).lower() == "season":
            return "series", None

        has_movies = bool(self._dedupe_plex_items(movies_raw))
        has_shows = bool(self._dedupe_plex_items(shows_raw))
        has_collections = bool(self._dedupe_plex_items(collections_raw))

        # Folders that carry a year follow the movie/show naming convention
        # (e.g. "300 (2007) {tmdb-1271}").  Resolve against movies/shows first
        # so a movie poster is not accidentally uploaded to a same-named
        # collection (e.g. "300 Collection") just because both share the
        # normalized media_key "300".
        if asset.get("folder_year") is not None:
            return self._resolve_movie_show_filter(
                asset, arr_availability, has_movies=has_movies, has_shows=has_shows
            )

        # No folder year → collection-style asset; prefer collections when available.
        if has_collections:
            return "collection", None

        return self._resolve_movie_show_filter(
            asset, arr_availability, has_movies=has_movies, has_shows=has_shows
        )

    @staticmethod
    def _candidate_groups_for_filter(
        inferred_filter: Optional[str],
        movies_raw: List[Any],
        shows_raw: List[Any],
        collections_raw: List[Any],
    ) -> List[List[Any]]:
        """Order the raw candidate lists to try for a resolved media-type filter.

        Movies/series map to their own section; everything else (collections and
        any unrecognized filter) falls back to the collections candidates.
        """
        if inferred_filter == "movie":
            return [movies_raw]
        if inferred_filter == "series":
            return [shows_raw]
        return [collections_raw]

    def _extract_asset_id_keys(self, asset: Dict[str, Any]) -> List[str]:
        path = str(asset.get("path", ""))
        if not path:
            return []
        matches = POSTER_ID_PATTERN.findall(path)
        id_keys = []
        for source, value in matches:
            normalized_source = str(source).strip().lower()
            normalized_value = str(value).strip().lower()
            if normalized_source and normalized_value:
                id_keys.append(f"id:{normalized_source}:{normalized_value}")
        return sorted(set(id_keys))

    def _extract_plex_id_keys(self, item: MediaServerItem) -> List[str]:
        id_keys: set[str] = set()
        for source, value in (item.provider_ids or {}).items():
            if source in {"imdb", "tmdb", "tvdb"} and value:
                id_keys.add(f"id:{source}:{value}")
        return sorted(id_keys)

    def _arr_id_keys_for_asset(
        self,
        asset: Dict[str, Any],
        arr_availability: Optional[Dict[str, Any]],
        inferred_filter: Optional[str] = None,
    ) -> List[str]:
        """Return TVDB/TMDB/IMDB id keys sourced from the ARR index for an asset
        that has no embedded ID tokens in its file path.  Used to supplement
        asset_id_keys so GUID-based Plex matching works even when folder names
        don't include {tvdb-…} / {tmdb-…} tokens."""
        if not arr_availability:
            return []
        media_key = str(asset.get("media_key", "")).strip()
        if not media_key:
            return []

        # Build lookup keys in priority order: year-qualified key first (avoids same-title/
        # different-year collision), then plain key as fallback.
        folder_year = asset.get("folder_year")
        year_qualified_key = f"{media_key}::{folder_year}" if folder_year is not None else None
        lookup_keys = ([year_qualified_key] if year_qualified_key else []) + [media_key]

        id_keys: set[str] = set()
        check_shows = inferred_filter in {None, "series"}
        check_movies = inferred_filter in {None, "movie"}

        if check_shows:
            record = None
            for lk in lookup_keys:
                r = arr_availability.get("shows", {}).get(lk)
                if isinstance(r, dict):
                    record = r
                    break
            if isinstance(record, dict):
                tvdb_id = record.get("tvdb_id")
                imdb_id = record.get("imdb_id")
                if isinstance(tvdb_id, int):
                    id_keys.add(f"id:tvdb:{tvdb_id}")
                if isinstance(imdb_id, str) and imdb_id:
                    id_keys.add(f"id:imdb:{imdb_id}")

        if check_movies:
            record = None
            for lk in lookup_keys:
                r = arr_availability.get("movies", {}).get(lk)
                if isinstance(r, dict):
                    record = r
                    break
            if isinstance(record, dict):
                tmdb_id = record.get("tmdb_id")
                imdb_id = record.get("imdb_id")
                if isinstance(tmdb_id, int):
                    id_keys.add(f"id:tmdb:{tmdb_id}")
                if isinstance(imdb_id, str) and imdb_id:
                    id_keys.add(f"id:imdb:{imdb_id}")

        return sorted(id_keys)

    def _resolve_index_candidates(
        self,
        index_map: Dict[str, List[Any]],
        media_key: str,
        asset_id_keys: List[str],
        folder_year: Optional[int] = None,
    ) -> List[Any]:
        def _item_year(item: Any) -> Optional[int]:
            try:
                return getattr(item, "year", None)
            except Exception:
                return None

        if asset_id_keys:
            id_candidates: List[Any] = []
            for id_key in asset_id_keys:
                id_candidates.extend(index_map.get(id_key, []))
            deduped_id_candidates = self._dedupe_plex_items(id_candidates)
            if deduped_id_candidates:
                if folder_year is not None:
                    year_filtered = [item for item in deduped_id_candidates if _item_year(item) == folder_year]
                    if year_filtered:
                        return year_filtered
                    # ID matched but folder year disagrees with Plex (e.g. folder
                    # "Michael (2025)" vs Plex year 2026). A unique ID match is
                    # authoritative — trust it over the folder year rather than
                    # discarding it and falling back to a title+year lookup.
                return deduped_id_candidates
            # IDs were present but nothing matched in the Plex index. Two possible causes:
            # (a) Plex hasn't scanned the item yet — we must not fall back to a same-title
            #     different-year item (e.g. plex_1957 when we want plex_2007).
            # (b) The Plex item exists but has no external GUIDs configured — a plain
            #     title+year match is still safe and correct.
            # Try a year-filtered title-key lookup to distinguish the two cases.
            # If it finds a year-correct item, return it (case b).
            # If nothing matches the year, return [] to trigger a retry (case a).
            if folder_year is not None:
                plain_results = index_map.get(media_key, [])
                if plain_results:
                    year_filtered = [item for item in plain_results if _item_year(item) == folder_year]
                    if year_filtered:
                        return year_filtered
                return []

        plain_results = index_map.get(media_key, [])
        # When the asset has a folder_year, filter plain-key results to only Plex items
        # matching that year. This prevents uploading a 1957 poster to a 2007 Plex item
        # (and vice versa) for same-title movies not tracked in any ARR instance.
        if folder_year is not None and plain_results:
            year_filtered = [item for item in plain_results if _item_year(item) == folder_year]
            if year_filtered:
                return year_filtered
            # No year match — Plex item not yet scanned or metadata year differs.
            # Return empty so the retry mechanism handles it rather than using the wrong movie.
            return []
        return plain_results

    def _asset_has_arr_availability(
        self,
        asset: Dict[str, Any],
        inferred_filter: Optional[str],
        arr_availability: Optional[Dict[str, Any]],
        asset_id_keys: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        if not arr_availability:
            return True, None

        asset_type = str(asset.get("asset_type", "")).lower()
        media_key = str(asset.get("media_key", "")).strip()
        season_number = asset.get("season_number")

        if inferred_filter == "collection":
            return True, None

        if asset_type == "season" or inferred_filter == "series":
            show_record = arr_availability.get("shows", {}).get(media_key)
            if not isinstance(show_record, dict):
                return True, None

            if asset_type == "season" and season_number is not None:
                has_season = bool(show_record.get("seasons", {}).get(int(season_number), False))
                if not has_season:
                    return False, f"no Sonarr episodes for season {int(season_number):02}"
                return True, None

            if not bool(show_record.get("has_episodes", False)):
                return False, "no Sonarr episodes available"
            return True, None

        if inferred_filter == "movie":
            movies_idx = arr_availability.get("movies", {})
            # Build candidate lookup keys in priority order:
            # 1. Asset ID keys (tmdb/imdb) — most specific, unambiguous
            # 2. Year-qualified title key — rules out same-name future remakes
            # 3. Plain title key — broadest fallback
            if asset_id_keys is None:
                asset_id_keys = self._extract_asset_id_keys(asset)
            year_key = f"{media_key}::{asset['folder_year']}" if asset.get("folder_year") else None
            candidate_keys = list(asset_id_keys)
            if year_key:
                candidate_keys.append(year_key)
            candidate_keys.append(media_key)
            movie_record = next(
                (movies_idx[k] for k in candidate_keys if k in movies_idx),
                None,
            )
            if not isinstance(movie_record, dict):
                return True, None
            if not bool(movie_record.get("has_file", False)):
                return False, "no Radarr file available"

        return True, None

    def _build_arr_availability_index(self, media_type_filter: Optional[str] = None) -> Dict[str, Any]:
        movies_index: Dict[str, Dict[str, Any]] = {}
        shows_index: Dict[str, Dict[str, Any]] = {}
        incomplete = False  # set when an ARR instance we meant to include couldn't be reached

        normalized_filter = str(media_type_filter or "").strip().lower()
        include_movies = normalized_filter in {"", "movie"}
        include_shows = normalized_filter in {"", "series", "show"}

        if include_movies:
            for instance in self._availability_arr_instances(self.SETTING_RADARR_INSTANCES):
                client = create_arr_client(instance["url"], instance["api_key"], "radarr", logger=None)
                if not client or not client.connect_status:
                    incomplete = True
                    continue
                for movie in client.get_parsed_media(include_unmonitored=True):
                    movie_keys = self._availability_keys_for_item(
                        title=str(movie.get("title", "")),
                        year=movie.get("year"),
                        folder=str(movie.get("folder", "")),
                        tmdb_id=movie.get("tmdb_id"),
                        imdb_id=movie.get("imdb_id"),
                    )
                    has_file = bool(movie.get("has_file", False))
                    tmdb_id = movie.get("tmdb_id")
                    imdb_id = movie.get("imdb_id")
                    entry: Dict[str, Any] = {
                        "has_file": has_file,
                        "tmdb_id": int(tmdb_id) if isinstance(tmdb_id, int) else None,
                        "imdb_id": str(imdb_id).strip().lower() if isinstance(imdb_id, str) and imdb_id.strip() else None,
                    }
                    for key in movie_keys:
                        existing = movies_index.get(key)
                        # Don't let a no-file entry overwrite an existing file entry
                        # for the same title key (e.g. a future remake with no release
                        # date shadowing an original that already has a file).
                        if existing is None or (has_file and not existing.get("has_file", False)):
                            movies_index[key] = entry

        if include_shows:
            for instance in self._availability_arr_instances(self.SETTING_SONARR_INSTANCES):
                client = create_arr_client(instance["url"], instance["api_key"], "sonarr", logger=None)
                if not client or not client.connect_status:
                    incomplete = True
                    continue
                for show in client.get_parsed_media(include_unmonitored=True):
                    show_keys = self._availability_keys_for_item(
                        title=str(show.get("title", "")),
                        year=show.get("year"),
                        folder=str(show.get("folder", "")),
                        tvdb_id=show.get("tvdb_id"),
                        imdb_id=show.get("imdb_id"),
                    )
                    seasons: Dict[int, bool] = {}
                    for season in show.get("seasons", []) or []:
                        season_number = season.get("season_number")
                        if season_number is None:
                            continue
                        try:
                            seasons[int(season_number)] = bool(season.get("season_has_episodes", False))
                        except (TypeError, ValueError):
                            continue

                    has_episodes = bool(show.get("has_episodes", False))
                    tvdb_id = show.get("tvdb_id")
                    imdb_id = show.get("imdb_id")
                    show_entry: Dict[str, Any] = {
                        "has_episodes": has_episodes,
                        "seasons": seasons,
                        "tvdb_id": int(tvdb_id) if isinstance(tvdb_id, int) else None,
                        "imdb_id": str(imdb_id).strip().lower() if isinstance(imdb_id, str) and imdb_id.strip() else None,
                    }
                    for key in show_keys:
                        existing = shows_index.get(key)
                        if existing is None or (has_episodes and not existing.get("has_episodes", False)):
                            shows_index[key] = show_entry

        self._arr_availability_incomplete = incomplete
        return {
            "movies": movies_index,
            "shows": shows_index,
        }

    def _availability_keys_for_item(
        self,
        *,
        title: str,
        year: Any,
        folder: str,
        tmdb_id: Any = None,
        imdb_id: Any = None,
        tvdb_id: Any = None,
    ) -> set[str]:
        keys: set[str] = set()
        if folder:
            folder_name = Path(folder).name
            plain_folder_key = normalize_titles(folder_name)
            keys.add(plain_folder_key)
            # Also add year-qualified key when folder contains a year so same-title/different-year
            # items (e.g. "3:10 to Yuma (1957)" vs "3:10 to Yuma (2007)") get distinct index entries.
            folder_year_m = re.search(r'\((\d{4})\)', folder_name)
            if folder_year_m:
                keys.add(f"{plain_folder_key}::{int(folder_year_m.group(1))}")
        if title:
            keys.add(normalize_titles(title))
        if title and year is not None:
            # Use "normalized_title::year" format instead of normalize_titles(f"{title} ({year})")
            # because normalize_titles strips years, making the year-qualified form identical to
            # the plain title key and providing no disambiguation benefit.
            keys.add(f"{normalize_titles(title)}::{year}")
        if tmdb_id is not None:
            normalized = str(tmdb_id).strip().lower()
            if normalized and normalized != "0":
                keys.add(f"id:tmdb:{normalized}")
        if imdb_id is not None:
            normalized = str(imdb_id).strip().lower()
            if normalized:
                keys.add(f"id:imdb:{normalized}")
        if tvdb_id is not None:
            normalized = str(tvdb_id).strip().lower()
            if normalized and normalized != "0":
                keys.add(f"id:tvdb:{normalized}")
        return {key for key in keys if key}

    def _get_arr_instances(self, setting_key: str) -> List[Dict[str, str]]:
        instances = self._load_json_setting(
            setting_key,
            missing_default=[],
            invalid_json_log_level="warning",
        )

        if not isinstance(instances, list):
            return []

        valid: List[Dict[str, str]] = []
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            name = str(instance.get("name", "")).strip()
            url = str(instance.get("url", "")).strip()
            api_key = str(instance.get("api_key", "")).strip()
            if name and url and api_key:
                valid.append({"name": name, "url": url, "api_key": api_key})
        return valid

    def _availability_arr_instances(self, setting_key: str) -> List[Dict[str, str]]:
        """Configured instances for an availability build, narrowed to the firing
        instance when an instance scope is active (webhook path). When no scope is set,
        or the scope doesn't match any instance for this setting (e.g. a Sonarr scope
        while building the movie index), the full list is returned unchanged."""
        instances = self._get_arr_instances(setting_key)
        scope = self._arr_instance_scope
        if not scope:
            return instances
        scoped = [i for i in instances if i["name"].strip().lower() == scope.strip().lower()]
        return scoped or instances

    def _dedupe_plex_items(self, items: List[Any]) -> List[Any]:
        deduped: List[Any] = []
        seen: set[str] = set()

        for item in items:
            rating_key = item.item_id or None
            if rating_key is not None:
                identity = f"rating:{rating_key}"
            else:
                library_identity = self._item_library_key(item) or self._item_library_name(item)
                fallback_parts = [
                    str(item.item_type or ""),
                    str(item.title or ""),
                    str(item.year if item.year is not None else ""),
                    str(library_identity or ""),
                ]
                identity = "fallback:" + "|".join(fallback_parts)

            if identity in seen:
                continue

            seen.add(identity)
            deduped.append(item)

        return deduped

    def _asset_label(self, asset: Dict[str, Any]) -> str:
        display_name = str(asset.get("display_name", "")).strip() or str(asset.get("media_key", "unknown"))
        asset_type = str(asset.get("asset_type", "unknown"))
        season_number = asset.get("season_number")

        if asset_type == "season" and season_number is not None:
            return f"{display_name} (Season {int(season_number):02})"
        if asset_type == "main":
            return f"{display_name}"
        return f"{display_name} [{asset_type}]"

    def _humanize_title(self, value: str) -> str:
        cleaned = value.replace("_", " ").replace(".", " ").strip()
        return " ".join(cleaned.split())

    def _describe_show_with_season(self, title: str, season_number: Optional[int]) -> str:
        if season_number is None:
            return title
        return f"{title} (Season {int(season_number):02})"

    def _describe_plex_item(self, item: MediaServerItem) -> str:
        item_type = str(item.item_type or "item")
        title = str(item.title or "Unknown")

        if item_type == "season":
            parent_title = str(item.parent_title or "Unknown")
            season_index = item.index
            if season_index is not None:
                return f"Season: {parent_title} (Season {int(season_index):02})"
            return f"Season: {parent_title}"

        year = item.year
        if year is not None:
            return f"{item_type.title()}: {title} ({year})"
        return f"{item_type.title()}: {title}"

    def _classify_plex_item(self, item: MediaServerItem) -> str:
        item_type = str(item.item_type or "").lower()
        if item_type == "movie":
            return "movies"
        if item_type == "show":
            return "shows"
        if item_type == "collection":
            return "collections"
        return "shows"

    def _library_labels_for_items(self, items: List[Any]) -> List[str]:
        labels: set[str] = set()
        for item in items:
            section_title = str(item.library_name or "").strip()
            if section_title:
                labels.add(section_title)

        return sorted(labels)

    def _item_library_name(self, item: MediaServerItem) -> str:
        return str(item.library_name or "").strip()

    def _item_library_key(self, item: MediaServerItem) -> str:
        return str(item.library_key or "")

    def _is_item_cached_for_library(
        self,
        *,
        library_name: str,
        library_key: str,
        uploaded_to_libraries: set[str],
        uploaded_to_library_keys: set[str],
    ) -> bool:
        # Prefer stable library keys whenever available.
        # Legacy cache entries only had names; we intentionally avoid trusting
        # those when a stable key is present to prevent cross-library collisions.
        if library_key:
            return library_key in uploaded_to_library_keys
        if library_name:
            return library_name in uploaded_to_libraries
        return False

    def _movie_edition_title(self, item: MediaServerItem) -> str:
        if item.edition_title:
            return str(item.edition_title)
        return self.DEFAULT_EDITION_MOVIE

    def _get_uploaded_record(self, file_path: str) -> Dict[str, Any]:
        """Return the upload record for a file path, or an empty record if not found or file changed."""
        # Check the per-run in-memory cache first to avoid redundant DB queries.
        if file_path in self._record_cache:
            cached = self._record_cache[file_path]
            return dict(cached) if cached is not None else self._empty_record()

        db_record = self.db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == file_path).first()
        if not db_record:
            self._record_cache[file_path] = None
            return self._empty_record()

        # Fast mtime pre-check: a single stat() call (no file read) tells us whether
        # the file has changed since last upload. If mtime matches, skip sha256 entirely.
        # This avoids reading thousands of poster files on every upload run, which was
        # the primary cause of multi-GB page cache accumulation.
        stored_mtime = db_record.file_mtime
        if stored_mtime is not None:
            try:
                current_mtime = os.stat(file_path).st_mtime
                if current_mtime == stored_mtime:
                    # File unchanged — trust the cached record without reading the file.
                    result = db_record.to_dict()
                    try:
                        self.db.expunge(db_record)
                    except Exception:  # nosec B110
                        pass
                    self._record_cache[file_path] = result
                    return dict(result)
            except OSError:
                pass

        # mtime not stored or mtime changed — fall back to sha256 comparison.
        stored_hash = db_record.file_hash
        if stored_hash is not None:
            current_hash = self._compute_file_hash(file_path)
            if current_hash is not None and current_hash != stored_hash:
                # File contents have changed — treat like a fresh file.
                try:
                    self.db.expunge(db_record)
                except Exception:  # nosec B110
                    pass
                self._record_cache[file_path] = None
                return self._empty_record()

        result = db_record.to_dict()

        # Hash matched but mtime wasn't stored yet — write it now so future runs
        # can use the fast mtime pre-check and skip the sha256 file read entirely.
        # Without this, already-cached files (which never go through _mark_uploaded)
        # would fall through to sha256 on every run indefinitely.
        if stored_mtime is None:
            try:
                current_mtime = os.stat(file_path).st_mtime
                db_record.file_mtime = current_mtime
                self.db.commit()
                result["file_mtime"] = current_mtime
            except Exception:  # nosec B110
                pass

        # Evict the ORM object from the session identity map immediately after extracting
        # the data we need. This prevents thousands of PlexUploadRecord objects from
        # accumulating in the identity map across a full upload run (one per poster file).
        try:
            self.db.expunge(db_record)
        except Exception:  # nosec B110
            pass
        self._record_cache[file_path] = result
        return dict(result)

    def _empty_record(self) -> Dict[str, Any]:
        return {
            "uploaded_to_libraries": [],
            "uploaded_to_library_keys": [],
            "uploaded_to_rating_keys": [],
            "uploaded_editions": [],
            "uploaded_media_types": [],
        }

    @staticmethod
    def _item_rating_key(item: MediaServerItem) -> Optional[str]:
        """Return an item's server id (Plex ratingKey) as a string, or None when unavailable."""
        rating_key = str(item.item_id or "").strip()
        return rating_key or None

    @staticmethod
    def _rating_key_indicates_change(rating_key: Optional[str], uploaded_rating_keys: set[str]) -> bool:
        """True when this item's ratingKey is unseen (re-added). False if no keys recorded yet (baseline) or key unreadable."""
        if not uploaded_rating_keys:
            return False
        if rating_key is None:
            return False
        return rating_key not in uploaded_rating_keys

    def _record_rating_key_if_new(
        self,
        file_path: str,
        rating_key: Optional[str],
        uploaded_rating_keys: set[str],
        *,
        dry_run: bool,
        library_name: Optional[str] = None,
        library_key: Optional[str] = None,
        edition_title: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> None:
        """Backfill a ratingKey onto an already-applied item (no re-upload), so a later re-add is detectable. Skips DB write on dry run."""
        if not rating_key or rating_key in uploaded_rating_keys:
            return
        uploaded_rating_keys.add(rating_key)
        if dry_run:
            return
        self._mark_uploaded(
            file_path,
            library_name=library_name,
            library_key=library_key,
            edition_title=edition_title,
            media_type=media_type,
            rating_key=rating_key,
        )

    def _compute_file_hash(self, file_path: str) -> Optional[str]:
        """Compute sha256 hex digest of file contents. Returns None if the file is unreadable."""
        try:
            h = hashlib.sha256()
            with open(file_path, "rb") as f:
                fd = f.fileno()
                # Hint to the kernel: sequential read ahead is helpful.
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_SEQUENTIAL)
                except (AttributeError, OSError):
                    pass
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
                # Drop from page cache — avoid accumulation across thousands of poster files.
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                except (AttributeError, OSError):
                    pass
            return h.hexdigest()
        except Exception:
            return None

    @staticmethod
    def _drop_file_cache(file_path: str) -> None:
        """Advise the kernel to evict a file's pages from the page cache after upload."""
        try:
            with open(file_path, "rb") as f:
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        except Exception:  # nosec B110
            pass

    def _mark_uploaded(
        self,
        file_path: str,
        library_name: Optional[str] = None,
        library_key: Optional[str] = None,
        edition_title: Optional[str] = None,
        media_type: Optional[str] = None,
        rating_key: Optional[str] = None,
    ) -> None:
        existing_record = self._get_uploaded_record(file_path)
        libraries = set(existing_record["uploaded_to_libraries"])
        library_keys = set(existing_record.get("uploaded_to_library_keys", []))
        rating_keys = set(existing_record.get("uploaded_to_rating_keys", []))
        editions = set(existing_record["uploaded_editions"])
        media_types = set(existing_record["uploaded_media_types"])

        if library_name:
            libraries.add(library_name)
        if library_key:
            library_keys.add(library_key)
        if rating_key:
            rating_keys.add(str(rating_key))
        if edition_title:
            editions.add(edition_title)
        if media_type:
            media_types.add(media_type)

        file_hash = self._compute_file_hash(file_path)
        try:
            file_mtime: Optional[float] = os.stat(file_path).st_mtime
        except OSError:
            file_mtime = None

        db_record = self.db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == file_path).first()
        if db_record:
            db_record.file_hash = file_hash
            db_record.file_mtime = file_mtime
            db_record.uploaded_to_libraries = json.dumps(sorted(libraries))
            db_record.uploaded_to_library_keys = json.dumps(sorted(library_keys))
            db_record.uploaded_to_rating_keys = json.dumps(sorted(rating_keys))
            db_record.uploaded_editions = json.dumps(sorted(editions))
            db_record.uploaded_media_types = json.dumps(sorted(media_types))
        else:
            db_record = PlexUploadRecord(
                file_path=file_path,
                file_hash=file_hash,
                file_mtime=file_mtime,
                uploaded_to_libraries=json.dumps(sorted(libraries)),
                uploaded_to_library_keys=json.dumps(sorted(library_keys)),
                uploaded_to_rating_keys=json.dumps(sorted(rating_keys)),
                uploaded_editions=json.dumps(sorted(editions)),
                uploaded_media_types=json.dumps(sorted(media_types)),
            )
            self.db.add(db_record)

        self.db.commit()

        updated = {
            "uploaded_to_libraries": sorted(libraries),
            "uploaded_to_library_keys": sorted(library_keys),
            "uploaded_to_rating_keys": sorted(rating_keys),
            "uploaded_editions": sorted(editions),
            "uploaded_media_types": sorted(media_types),
        }
        if file_hash is not None:
            updated["file_hash"] = file_hash
        if file_mtime is not None:
            updated["file_mtime"] = file_mtime
        self._record_cache[file_path] = updated

    def _persist_upload_cache(self) -> None:
        """Prune DB records for local files that no longer exist on disk."""
        rows = self.db.query(PlexUploadRecord.file_path).all()
        stale_paths: list[str] = []
        existing_path_count = 0

        for row in rows:
            if Path(row.file_path).exists():
                existing_path_count += 1
            else:
                stale_paths.append(row.file_path)

        if not stale_paths:
            return

        if not existing_path_count:
            log_warning(
                LogTags.UPLOADER,
                "Skipping stale upload record pruning: file paths are not resolvable in this runtime",
                total_records=len(rows),
                stale_candidates=len(stale_paths),
            )
            return

        self.db.query(PlexUploadRecord).filter(
            PlexUploadRecord.file_path.in_(stale_paths)
        ).delete(synchronize_session=False)
        for path in stale_paths:
            self._record_cache.pop(path, None)

        self.db.commit()

        log_info(
            LogTags.UPLOADER,
            f"Pruned {len(stale_paths)} stale upload records",
            removed_stale=len(stale_paths),
            remaining=existing_path_count,
        )

    def _clear_upload_cache(self) -> None:
        self.db.query(PlexUploadRecord).delete()
        self.db.commit()
        self._record_cache = {}
