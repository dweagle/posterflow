import hashlib
import json
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_error, log_info, log_warning
from models.plex_upload import PlexUploadRecord
from models.setting import get_setting, upsert_setting
from util.arr.client import create_arr_client
from util.constants import POSTER_ID_PATTERN
from util.data.normalization import normalize_titles
from util.posters.match import collection_title_variants


PlexUploadProgressCallback = Callable[[int, int, Dict[str, int], str], None]


class PlexUploadService:
    """Upload organized poster assets to Plex libraries."""

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    SETTING_RADARR_INSTANCES = "radarr_instances"
    SETTING_SONARR_INSTANCES = "sonarr_instances"
    DEFAULT_EDITION_MOVIE = "default_edition"
    ERROR_NO_PLEX_INSTANCES = "No Plex instances configured. Configure in Settings → Media tab."
    ERROR_NO_LIBRARIES_SELECTED = "No Plex libraries selected. Configure in Settings → Media tab."
    ERROR_INVALID_LIBRARY_CONFIG = "Invalid Plex library configuration. Configure in Settings → Media tab."
    ERROR_INDEX_BUILD_FAILED = "Unable to build Plex index from configured instances/libraries."
    MESSAGE_NO_POSTER_ASSETS = "No poster assets found to upload."

    def __init__(self, db: Session, upload_delay_ms: int = 50) -> None:
        self.db = db
        self.upload_delay_ms = max(0, upload_delay_ms)
        self._record_cache: Dict[str, Optional[Dict[str, Any]]] = {}  # per-run in-memory cache of DB records
        self._local_assets_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._arr_availability_cache: Dict[str, Dict[str, Any]] = {}
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

    def invalidate_arr_availability_cache(self) -> None:
        self._arr_availability_cache = {}

    def invalidate_record_cache(self) -> None:
        self._record_cache = {}

    @staticmethod
    def _empty_media_upload_counts() -> Dict[str, int]:
        return {
            "movies": 0,
            "shows": 0,
            "seasons": 0,
            "collections": 0,
        }

    @staticmethod
    def _base_result_stats(scanned: int = 0) -> Dict[str, int]:
        return {
            "scanned": scanned,
            "matched": 0,
            "uploaded": 0,
            "would_upload": 0,
            "skipped": 0,
            "errors": 0,
            "plex_seasons_missing": 0,
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
            "candidate_matches_raw": 0,
            "candidate_matches_unique": 0,
            "main_assets": sum(1 for asset in local_assets if asset.get("asset_type") == "main"),
            "season_assets": sum(1 for asset in local_assets if asset.get("asset_type") == "season"),
            "library_totals": library_totals,
            "media_upload_counts": self._empty_media_upload_counts(),
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
                uploaded_count, matched, raw_candidates, unique_candidates, media_counts, seasons_missing = self._upload_asset(
                    asset,
                    index,
                    dry_run,
                    media_type_filter=media_type_filter,
                    arr_availability=arr_availability,
                    remove_overlay_label=remove_overlay_label,
                )
                stats["candidate_matches_raw"] += raw_candidates
                stats["candidate_matches_unique"] += unique_candidates
                for key, value in media_counts.items():
                    stats["media_upload_counts"][key] += int(value)
                if matched:
                    stats["matched"] += 1
                if dry_run:
                    stats["would_upload"] += uploaded_count
                else:
                    stats["uploaded"] += uploaded_count
                if uploaded_count == 0:
                    stats["skipped"] += 1
                if seasons_missing:
                    stats["plex_seasons_missing"] = int(stats.get("plex_seasons_missing", 0)) + seasons_missing

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
                    log_warning(LogTags.UPLOADER, f"Plex upload progress callback failed: {callback_error}")

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
                "Plex preflight failed: unable to build index from configured instances/libraries",
                preflight_connectivity_failed=True,
                matching_skipped=True,
            )
            return self._error_result(self.ERROR_INDEX_BUILD_FAILED), None, None, None
        if not library_totals:
            log_warning(
                LogTags.UPLOADER,
                "Plex preflight failed: no reachable Plex libraries from configured instances",
                preflight_connectivity_failed=True,
                matching_skipped=True,
            )
            return self._error_result(self.ERROR_INDEX_BUILD_FAILED), None, None, None

        context = (None, destination_dir, index, library_totals)
        self._preflight_context_cache = context
        return context

    def _get_arr_availability_index(self, media_type_filter: Optional[str] = None, *, force_refresh: bool = False) -> Dict[str, Any]:
        cache_key = str(media_type_filter or "").strip().lower()
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
        if reapply and not dry_run:
            self._clear_upload_cache()
            log_info(LogTags.UPLOADER, "Reapply enabled: cleared Plex upload cache before run")
        elif reapply and dry_run:
            log_info(LogTags.UPLOADER, "Reapply requested in dry run: skipped Plex upload cache clear")

        preflight_error, destination_dir, index, library_totals = self._prepare_upload_context()
        if preflight_error:
            return preflight_error

        if destination_dir is None or index is None or library_totals is None:
            return self._error_result("Upload preflight returned incomplete context.")

        local_assets = self._get_local_assets(destination_dir)
        if not local_assets:
            return self._no_assets_result(self.MESSAGE_NO_POSTER_ASSETS)

        stats = self._build_run_stats(local_assets, library_totals)
        arr_availability = self._get_arr_availability_index()
        self._process_assets_for_upload(
            local_assets=local_assets,
            index=index,
            stats=stats,
            dry_run=dry_run,
            arr_availability=arr_availability,
            remove_overlay_label=remove_overlay_label,
            progress_callback=progress_callback,
        )

        self._persist_upload_cache()

        return self._run_success_result(
            dry_run=dry_run,
            stats=stats,
            completed_message="Plex upload completed",
            dry_run_message="Plex upload dry run completed",
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
        plex_rating_key: Optional[int] = None,
        dry_run: bool = False,
        reapply: bool = False,
        remove_overlay_label: bool = False,
        progress_callback: Optional[PlexUploadProgressCallback] = None,
    ) -> Dict[str, Any]:
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
                "Reapply enabled: cleared Plex upload cache for single target before run",
                media_type=media_type,
                title=title,
                removed_entries=removed_entries,
            )
        elif reapply and dry_run:
            log_info(
                LogTags.UPLOADER,
                "Reapply requested in dry run: skipped Plex upload cache clear for single target",
                media_type=media_type,
                title=title,
            )

        preflight_error, destination_dir, index, library_totals = self._prepare_upload_context()
        if preflight_error:
            return preflight_error

        if destination_dir is None or index is None or library_totals is None:
            return self._error_result("Upload preflight returned incomplete context.")

        all_assets = self._get_local_assets(destination_dir)
        if not all_assets:
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
        )

        if not local_assets:
            return self._no_assets_result(f"No local assets found for '{title}'.")

        stats = self._build_run_stats(local_assets, library_totals)
        arr_availability = self._get_arr_availability_index(media_type_filter=media_type_normalized)
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

        self._persist_upload_cache()

        return self._run_success_result(
            dry_run=dry_run,
            stats=stats,
            completed_message="Plex single-item upload completed",
            dry_run_message="Plex single-item dry run completed",
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
    ) -> bool:
        """Return True when a target resolves to local assets that are already cached for all matched Plex targets."""
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
        """Return True when the main show poster asset is already cached for all matched Plex targets."""
        preflight_error, destination_dir, index, _library_totals = self._prepare_upload_context()
        if preflight_error:
            return False
        if destination_dir is None or index is None:
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
        for asset in show_main_assets:
            if self._is_asset_fully_cached_for_targets(
                asset,
                index=index,
                media_type_filter="series",
                arr_availability=arr_availability,
            ):
                return True

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
        uploaded_editions = set(uploaded_record.get("uploaded_editions", []))
        uploaded_media_types = set(uploaded_record.get("uploaded_media_types", []))

        media_key = str(asset.get("media_key") or "")
        asset_id_keys = self._extract_asset_id_keys(asset)
        movies_raw = self._resolve_index_candidates(index["movies"], media_key, asset_id_keys)
        shows_raw = self._resolve_index_candidates(index["shows"], media_key, asset_id_keys)
        collections_raw = self._resolve_index_candidates(index["collections"], media_key, asset_id_keys)

        if str(asset.get("asset_type") or "").lower() == "season":
            shows = self._dedupe_plex_items(shows_raw)
            if not shows:
                return False

            season_value = asset.get("season_number")
            if season_value is None:
                return False

            available_season_targets = 0
            for show in shows:
                season_obj = next((s for s in show.seasons() if s.index == season_value), None)
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

        candidate_groups: List[List[Any]]
        if inferred_filter == "movie":
            candidate_groups = [movies_raw]
        elif inferred_filter == "series":
            candidate_groups = [shows_raw]
        elif inferred_filter == "collection":
            candidate_groups = [collections_raw]
        else:
            candidate_groups = [collections_raw]

        matched_items: List[Any] = []
        for candidates in candidate_groups:
            deduped_candidates = self._dedupe_plex_items(candidates)
            if deduped_candidates:
                matched_items = deduped_candidates
                break

        if not matched_items:
            return False

        for item in matched_items:
            item_type = str(getattr(item, "type", "")).lower()
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
        if target_id_keys:
            id_matched_assets = [
                asset
                for asset in all_assets
                if bool(target_id_keys & set(self._extract_asset_id_keys(asset)))
            ]
            if id_matched_assets:
                local_assets = id_matched_assets

        # Year-based filtering
        if year is not None:
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
        setting = get_setting(self.db, "poster_destination")
        if not setting or not setting.value:
            raise ValueError("No destination directory configured. Configure in Poster Manager settings.")

        destination = Path(setting.value)
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
                valid.append({"name": name, "url": url, "api_key": api_key})
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
                return {}, "No Plex Upload override libraries selected. Configure on Plex Upload page or disable override."
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
            return False, [], "Invalid Plex Upload library override configuration. Disable override or save it again on Plex Upload page."

        if not isinstance(parsed, dict):
            return False, [], "Invalid Plex Upload library override configuration. Disable override or save it again on Plex Upload page."

        enabled = bool(parsed.get("enabled", False))
        configs = parsed.get("configs", [])
        if configs is None:
            configs = []
        if not isinstance(configs, list):
            return False, [], "Invalid Plex Upload library override configuration. Disable override or save it again on Plex Upload page."

        return enabled, configs, None

    def _discover_local_assets(self, destination: Path) -> List[Dict[str, Any]]:
        assets: List[Dict[str, Any]] = []

        for file_path in destination.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in self.IMAGE_EXTENSIONS:
                continue

            rel_parts = file_path.relative_to(destination).parts
            if any(part.lower() == "tmp" for part in rel_parts):
                continue

            if file_path.parent == destination:
                parsed = self._parse_root_file(file_path)
            else:
                parsed = self._parse_asset_folder_file(file_path)

            if parsed:
                assets.append(parsed)

        log_info(LogTags.UPLOADER, f"Discovered {len(assets)} local poster assets", count=len(assets))
        return assets

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

    def _build_plex_index(
        self,
        plex_instances: List[Dict[str, str]],
        selected_libraries: Dict[str, List[Dict[str, Any]]],
    ) -> Tuple[Dict[str, Dict[str, List[Any]]], List[Dict[str, Any]]]:
        try:
            from plexapi.server import PlexServer
        except ImportError as e:
            log_error(LogTags.UPLOADER, f"plexapi not installed: {e}")
            return {}, []

        index: Dict[str, Dict[str, List[Any]]] = {
            "movies": {},
            "shows": {},
            "collections": {},
        }
        library_totals: List[Dict[str, Any]] = []

        for instance in plex_instances:
            instance_name = instance["name"]
            try:
                plex = PlexServer(instance["url"], instance["api_key"])
            except Exception as e:
                log_error(LogTags.UPLOADER, f"Failed to connect to Plex instance '{instance_name}': {e}")
                continue

            allowed = selected_libraries.get(instance_name)
            for section in plex.library.sections():
                if allowed and not self._is_section_allowed(section, allowed):
                    continue

                if section.type == "movie":
                    movie_count = self._index_movies(section, index["movies"])
                    collection_count = self._index_collections(section, index["collections"])
                    library_totals.append(
                        {
                            "instance": instance_name,
                            "library": str(section.title),
                            "section_type": "movie",
                            "items": movie_count,
                            "collections": collection_count,
                        }
                    )
                elif section.type == "show":
                    show_count = self._index_shows(section, index["shows"])
                    collection_count = self._index_collections(section, index["collections"])
                    library_totals.append(
                        {
                            "instance": instance_name,
                            "library": str(section.title),
                            "section_type": "show",
                            "items": show_count,
                            "collections": collection_count,
                        }
                    )

        log_info(
            LogTags.UPLOADER,
            "Built Plex index",
            movies=len(index["movies"]),
            shows=len(index["shows"]),
            collections=len(index["collections"]),
        )
        return index, library_totals

    def _is_section_allowed(self, section: Any, allowed: List[Dict[str, Any]]) -> bool:
        section_key = str(getattr(section, "key", ""))
        section_title = str(getattr(section, "title", ""))

        for library in allowed:
            if str(library.get("key", "")) == section_key:
                return True
            if str(library.get("title", "")) == section_title:
                return True
        return False

    def _index_movies(self, section: Any, movie_index: Dict[str, List[Any]]) -> int:
        indexed = 0
        try:
            for movie in section.all():
                key = self._movie_folder_key(movie)
                if key:
                    movie_index.setdefault(key, []).append(movie)
                for id_key in self._extract_plex_id_keys(movie):
                    movie_index.setdefault(id_key, []).append(movie)
                indexed += 1
        except Exception as e:
            log_warning(LogTags.UPLOADER, f"Failed indexing movie section '{section.title}': {e}")
        return indexed

    def _index_shows(self, section: Any, show_index: Dict[str, List[Any]]) -> int:
        indexed = 0
        try:
            for show in section.all():
                key = self._show_folder_key(show)
                if key:
                    show_index.setdefault(key, []).append(show)
                for id_key in self._extract_plex_id_keys(show):
                    show_index.setdefault(id_key, []).append(show)
                indexed += 1
        except Exception as e:
            log_warning(LogTags.UPLOADER, f"Failed indexing show section '{section.title}': {e}")
        return indexed

    def _index_collections(self, section: Any, collection_index: Dict[str, List[Any]]) -> int:
        indexed = 0
        try:
            for collection in section.collections():
                key = normalize_titles(collection.title)
                collection_index.setdefault(key, []).append(collection)
                indexed += 1
        except Exception as e:
            log_warning(LogTags.UPLOADER, f"Failed indexing collections for '{section.title}': {e}")
        return indexed

    def _movie_folder_key(self, movie: Any) -> Optional[str]:
        try:
            part_file = movie.media[0].parts[0].file
            return normalize_titles(Path(part_file).parent.name)
        except Exception:
            title = getattr(movie, "title", None)
            year = getattr(movie, "year", None)
            if title and year:
                return normalize_titles(f"{title} ({year})")
            if title:
                return normalize_titles(str(title))
            return None

    def _show_folder_key(self, show: Any) -> Optional[str]:
        try:
            seasons = show.seasons()
            if not seasons:
                return normalize_titles(show.title)
            episodes = seasons[0].episodes()
            if not episodes:
                return normalize_titles(show.title)
            part_file = episodes[0].media[0].parts[0].file
            return normalize_titles(Path(part_file).parent.parent.name)
        except Exception:
            title = getattr(show, "title", None)
            year = getattr(show, "year", None)
            if title and year:
                return normalize_titles(f"{title} ({year})")
            if title:
                return normalize_titles(str(title))
            return None

    def _upload_asset(
        self,
        asset: Dict[str, Any],
        index: Dict[str, Dict[str, List[Any]]],
        dry_run: bool,
        media_type_filter: Optional[str] = None,
        arr_availability: Optional[Dict[str, Any]] = None,
        remove_overlay_label: bool = False,
    ) -> Tuple[int, bool, int, int, Dict[str, int], int]:
        media_key = asset["media_key"]
        file_path = asset["path"]
        asset_label = self._asset_label(asset)
        asset_id_keys = self._extract_asset_id_keys(asset)
        media_counts = self._empty_media_upload_counts()
        uploaded_record = self._get_uploaded_record(file_path)
        uploaded_to_libraries = set(uploaded_record.get("uploaded_to_libraries", []))
        uploaded_to_library_keys = set(uploaded_record.get("uploaded_to_library_keys", []))
        uploaded_editions = set(uploaded_record.get("uploaded_editions", []))
        uploaded_media_types = set(uploaded_record.get("uploaded_media_types", []))

        movies_raw = self._resolve_index_candidates(index["movies"], media_key, asset_id_keys)
        shows_raw = self._resolve_index_candidates(index["shows"], media_key, asset_id_keys)
        collections_raw = self._resolve_index_candidates(index["collections"], media_key, asset_id_keys)

        if asset["asset_type"] == "season":
            shows = self._dedupe_plex_items(shows_raw)
            if not shows:
                log_debug(LogTags.UPLOADER, f"No show match for season asset: {asset_label}", file=file_path)
                return 0, False, 0, 0, media_counts, 0

            uploaded = 0
            cached_skips = 0
            for show in shows:
                season_number = asset["season_number"]
                library_name = self._item_library_name(show)
                library_key = self._item_library_key(show)
                if self._is_item_cached_for_library(
                    library_name=library_name,
                    library_key=library_key,
                    uploaded_to_libraries=uploaded_to_libraries,
                    uploaded_to_library_keys=uploaded_to_library_keys,
                ):
                    log_debug(
                        LogTags.UPLOADER,
                        f"Skipping cached season upload for {asset_label} in {library_name}",
                        file=file_path,
                    )
                    cached_skips += 1
                    continue
                season_obj = next((s for s in show.seasons() if s.index == season_number), None)
                if not season_obj:
                    continue
                if dry_run:
                    log_info(
                        LogTags.UPLOADER,
                        f"Dry run: Uploaded {self._describe_show_with_season(show.title, season_number)}",
                        file=file_path,
                        asset=asset_label,
                    )
                    uploaded += 1
                    media_counts["seasons"] += 1
                    continue
                season_obj.uploadPoster(filepath=file_path)
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
                self._mark_uploaded(
                    file_path,
                    library_name=library_name,
                    library_key=library_key,
                    media_type="seasons",
                )
                log_info(
                    LogTags.UPLOADER,
                    f"Uploaded {self._describe_show_with_season(show.title, season_number)}",
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
                    libraries_text = ", ".join(library_labels) if library_labels else "Plex"
                    log_info(
                        LogTags.UPLOADER,
                        f"No Season {int(season_number):02} found in {libraries_text} for {asset.get('display_name', media_key)}",
                        file=file_path,
                    )
                    return uploaded, True, len(shows_raw), len(shows), media_counts, 1
            return uploaded, True, len(shows_raw), len(shows), media_counts, 0

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
                ambiguous_raw_candidates = len(movies_raw) + len(shows_raw)
                return 0, False, ambiguous_raw_candidates, 0, media_counts, 0
            log_info(LogTags.UPLOADER, f"No Plex match for asset: {asset_label}", file=file_path)
            return 0, False, 0, 0, media_counts, 0

        available, unavailable_reason = self._asset_has_arr_availability(
            asset,
            inferred_filter,
            arr_availability,
        )
        if not available:
            log_info(
                LogTags.UPLOADER,
                f"Skipping unavailable asset: {asset_label} ({unavailable_reason})",
                file=file_path,
            )
            return 0, False, 0, 0, media_counts, 0
        candidate_groups: List[List[Any]] = []
        if inferred_filter == "movie":
            candidate_groups = [movies_raw]
        elif inferred_filter == "series":
            candidate_groups = [shows_raw]
        elif inferred_filter == "collection":
            candidate_groups = [collections_raw]
        else:
            candidate_groups = [collections_raw]

        raw_candidate_count = 0
        matched_items: List[Any] = []
        for candidates in candidate_groups:
            deduped_candidates = self._dedupe_plex_items(candidates)
            if deduped_candidates:
                raw_candidate_count = len(candidates)
                matched_items = deduped_candidates
                break

        if not matched_items:
            log_info(
                LogTags.UPLOADER,
                f"No Plex match for asset: {asset_label} (inferred_filter={inferred_filter}, "
                f"movies_raw={len(movies_raw)}, shows_raw={len(shows_raw)}, collections_raw={len(collections_raw)})",
                file=file_path,
            )
            return 0, False, raw_candidate_count, 0, media_counts, 0

        uploaded = 0
        for item in matched_items:
            item_label = self._describe_plex_item(item)
            item_type = str(getattr(item, "type", "")).lower()
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
                    log_debug(
                        LogTags.UPLOADER,
                        f"Skipping cached movie edition upload for {item_label} ({edition_title})",
                        file=file_path,
                    )
                    continue

                if (
                    edition_title == self.DEFAULT_EDITION_MOVIE
                    and not uploaded_editions
                    and item_cached_for_library
                ):
                    self._mark_uploaded(
                        file_path,
                        library_name=library_name,
                        library_key=library_key,
                        edition_title=edition_title,
                        media_type="movies",
                    )
                    uploaded_editions.add(edition_title)
                    log_debug(
                        LogTags.UPLOADER,
                        f"Skipping cached default movie edition upload for {item_label}",
                        file=file_path,
                    )
                    continue
            else:
                item_media_type = self._classify_plex_item(item)
                if item_cached_for_library and (not uploaded_media_types or item_media_type in uploaded_media_types):
                    log_debug(
                        LogTags.UPLOADER,
                        f"Skipping cached upload for {item_label} in {library_name}",
                        file=file_path,
                    )
                    continue

            if dry_run:
                log_info(
                    LogTags.UPLOADER,
                    f"Dry run: Uploaded {item_label}",
                    file=file_path,
                    asset=asset_label,
                )
                uploaded += 1
                media_counts[self._classify_plex_item(item)] += 1
                continue
            item.uploadPoster(filepath=file_path)
            self._drop_file_cache(file_path)
            time.sleep(self.upload_delay_ms / 1000.0)
            if remove_overlay_label:
                self._remove_overlay_label_if_present(item, file_path=file_path)
            uploaded += 1
            media_counts[self._classify_plex_item(item)] += 1
            if item_type == "movie":
                edition_title = self._movie_edition_title(item)
                if library_name:
                    uploaded_to_libraries.add(library_name)
                if library_key:
                    uploaded_to_library_keys.add(library_key)
                uploaded_editions.add(edition_title)
                self._mark_uploaded(
                    file_path,
                    library_name=library_name,
                    library_key=library_key,
                    edition_title=edition_title,
                    media_type="movies",
                )
            else:
                if library_name:
                    uploaded_to_libraries.add(library_name)
                if library_key:
                    uploaded_to_library_keys.add(library_key)
                self._mark_uploaded(
                    file_path,
                    library_name=library_name,
                    library_key=library_key,
                    media_type=self._classify_plex_item(item),
                )
            log_info(LogTags.UPLOADER, f"Uploaded {item_label}", file=file_path, asset=asset_label)

        return uploaded, True, raw_candidate_count, len(matched_items), media_counts, 0

    def _remove_overlay_label_if_present(self, item: Any, *, file_path: str) -> None:
        try:
            labels = getattr(item, "labels", None)
            if not labels:
                return

            has_overlay_label = any(str(getattr(label, "tag", "")).strip().lower() == "overlay" for label in labels)
            if not has_overlay_label:
                return

            remove_label = getattr(item, "removeLabel", None)
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

        # No folder year → collection-style asset; prefer collections when available.
        if has_collections:
            return "collection", None

        arr_inferred_filter = self._infer_media_type_filter_from_arr(asset, arr_availability)
        if arr_inferred_filter == "ambiguous":
            # ARR matched both types by title, but Plex may have already disambiguated
            # via ID-based section lookup. If Plex unambiguously found it in only one
            # section, trust that result over the title-based ARR collision.
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

    def _extract_plex_id_keys(self, item: Any) -> List[str]:
        id_keys: set[str] = set()

        raw_guids = getattr(item, "guids", None) or []
        for guid in raw_guids:
            guid_value = str(getattr(guid, "id", guid)).strip().lower()
            if not guid_value:
                continue

            if "://" in guid_value:
                source, value = guid_value.split("://", 1)
            elif ":" in guid_value:
                source, value = guid_value.split(":", 1)
            else:
                continue

            source = source.strip().lower()
            value = value.strip().lower()
            if source in {"imdb", "tmdb", "tvdb"} and value:
                id_keys.add(f"id:{source}:{value}")

        for source in ("imdb", "tmdb", "tvdb"):
            attr_name = f"{source}id"
            value = getattr(item, attr_name, None)
            if value is None:
                continue
            normalized_value = str(value).strip().lower()
            if normalized_value and normalized_value != "0":
                id_keys.add(f"id:{source}:{normalized_value}")

        return sorted(id_keys)

    def _resolve_index_candidates(
        self,
        index_map: Dict[str, List[Any]],
        media_key: str,
        asset_id_keys: List[str],
    ) -> List[Any]:
        if asset_id_keys:
            id_candidates: List[Any] = []
            for id_key in asset_id_keys:
                id_candidates.extend(index_map.get(id_key, []))
            deduped_id_candidates = self._dedupe_plex_items(id_candidates)
            if deduped_id_candidates:
                return deduped_id_candidates

        return index_map.get(media_key, [])

    def _asset_has_arr_availability(
        self,
        asset: Dict[str, Any],
        inferred_filter: Optional[str],
        arr_availability: Optional[Dict[str, Any]],
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
            asset_id_keys = self._extract_asset_id_keys(asset)
            year_key = normalize_titles(f"{asset.get('display_name', media_key)} ({asset['folder_year']})") if asset.get("folder_year") else None
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

        normalized_filter = str(media_type_filter or "").strip().lower()
        include_movies = normalized_filter in {"", "movie"}
        include_shows = normalized_filter in {"", "series", "show"}

        if include_movies:
            for instance in self._get_arr_instances(self.SETTING_RADARR_INSTANCES):
                client = create_arr_client(instance["url"], instance["api_key"], "radarr", logger=None)
                if not client or not client.connect_status:
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
                    for key in movie_keys:
                        existing = movies_index.get(key)
                        # Don't let a no-file entry overwrite an existing file entry
                        # for the same title key (e.g. a future remake with no release
                        # date shadowing an original that already has a file).
                        if existing is None or (has_file and not existing.get("has_file", False)):
                            movies_index[key] = {"has_file": has_file}

        if include_shows:
            for instance in self._get_arr_instances(self.SETTING_SONARR_INSTANCES):
                client = create_arr_client(instance["url"], instance["api_key"], "sonarr", logger=None)
                if not client or not client.connect_status:
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
                    for key in show_keys:
                        shows_index[key] = {
                            "has_episodes": has_episodes,
                            "seasons": seasons,
                        }

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
            keys.add(normalize_titles(Path(folder).name))
        if title:
            keys.add(normalize_titles(title))
        if title and year is not None:
            keys.add(normalize_titles(f"{title} ({year})"))
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

    def _dedupe_plex_items(self, items: List[Any]) -> List[Any]:
        deduped: List[Any] = []
        seen: set[str] = set()

        for item in items:
            rating_key = getattr(item, "ratingKey", None)
            if rating_key is not None:
                identity = f"rating:{rating_key}"
            else:
                library_identity = self._item_library_key(item) or self._item_library_name(item)
                identity = "fallback:" + "|".join(
                    [
                        str(getattr(item, "type", "")),
                        str(getattr(item, "title", "")),
                        str(getattr(item, "year", "")),
                        str(library_identity or ""),
                    ]
                )

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

    def _describe_plex_item(self, item: Any) -> str:
        item_type = str(getattr(item, "type", "item"))
        title = str(getattr(item, "title", "Unknown"))

        if item_type == "season":
            parent_title = str(getattr(item, "parentTitle", "Unknown"))
            season_index = getattr(item, "index", None)
            if season_index is not None:
                return f"Season: {parent_title} (Season {int(season_index):02})"
            return f"Season: {parent_title}"

        year = getattr(item, "year", None)
        if year is not None:
            return f"{item_type.title()}: {title} ({year})"
        return f"{item_type.title()}: {title}"

    def _classify_plex_item(self, item: Any) -> str:
        item_type = str(getattr(item, "type", "")).lower()
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
            section_title = str(getattr(item, "librarySectionTitle", "")).strip()

            if section_title:
                labels.add(section_title)

        return sorted(labels)

    def _item_library_name(self, item: Any) -> str:
        return str(getattr(item, "librarySectionTitle", "")).strip()

    def _item_library_key(self, item: Any) -> str:
        server = getattr(item, "_server", None)
        server_id = str(getattr(server, "machineIdentifier", "") or "").strip()
        section_id = str(getattr(item, "librarySectionID", "") or "").strip()
        section_key = str(getattr(item, "librarySectionKey", "") or "").strip()

        section_identity = section_key or section_id
        if server_id and section_identity:
            return f"{server_id}:{section_identity}"
        if section_identity:
            return section_identity
        return ""

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

    def _movie_edition_title(self, item: Any) -> str:
        edition_title = getattr(item, "editionTitle", None)
        if edition_title:
            return str(edition_title)
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
            except (OSError, Exception):
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
            "uploaded_editions": [],
            "uploaded_media_types": [],
        }

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
        except (AttributeError, OSError, Exception):
            pass

    def _mark_uploaded(
        self,
        file_path: str,
        library_name: Optional[str] = None,
        library_key: Optional[str] = None,
        edition_title: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> None:
        existing_record = self._get_uploaded_record(file_path)
        libraries = set(existing_record["uploaded_to_libraries"])
        library_keys = set(existing_record.get("uploaded_to_library_keys", []))
        editions = set(existing_record["uploaded_editions"])
        media_types = set(existing_record["uploaded_media_types"])

        if library_name:
            libraries.add(library_name)
        if library_key:
            library_keys.add(library_key)
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
            db_record.uploaded_editions = json.dumps(sorted(editions))
            db_record.uploaded_media_types = json.dumps(sorted(media_types))
        else:
            db_record = PlexUploadRecord(
                file_path=file_path,
                file_hash=file_hash,
                file_mtime=file_mtime,
                uploaded_to_libraries=json.dumps(sorted(libraries)),
                uploaded_to_library_keys=json.dumps(sorted(library_keys)),
                uploaded_editions=json.dumps(sorted(editions)),
                uploaded_media_types=json.dumps(sorted(media_types)),
            )
            self.db.add(db_record)

        self.db.commit()

        updated = {
            "uploaded_to_libraries": sorted(libraries),
            "uploaded_to_library_keys": sorted(library_keys),
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
                "Skipping stale Plex upload record pruning: file paths are not resolvable in this runtime",
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
            f"Pruned {len(stale_paths)} stale Plex upload records",
            removed_stale=len(stale_paths),
            remaining=existing_path_count,
        )

    def _clear_upload_cache(self) -> None:
        self.db.query(PlexUploadRecord).delete()
        self.db.commit()
        self._record_cache = {}
