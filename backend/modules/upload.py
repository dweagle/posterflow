import filecmp
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import time
import traceback
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.logging import (
    LogTags,
    add_job_log_handler,
    logger,
    log_debug,
    log_error,
    log_info,
    log_section_end,
    log_section_start,
    log_success,
    log_warning,
    remove_job_log_handler,
)
from database import SessionLocal
from models.drive import Drive
from models.setting import get_setting, upsert_setting
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    format_start_message,
    format_complete_message,
    update_job_state,
)
from models.poster import Poster
from services.border_replacer import BorderReplacerService
from services.discord_notifications import send_discord_notification, send_major_error_notification
from services.poster_renamer import PosterRenameService
from services.plex_upload import PlexUploadService
from util.constants import POSTER_ID_PATTERN
from util.data.normalization import normalize_titles
from util.posters.assets import get_assets_files

# Regex to extract edition title from a Radarr-style folder token like {edition-Extended Cut}
_RADARR_EDITION_TOKEN_RE = re.compile(r"\{edition-([^}]+)\}", re.IGNORECASE)


def _extract_edition_from_radarr_path(path: str) -> Optional[str]:
    """Return the edition title embedded in a Radarr file path as an {edition-*} token, or None."""
    if not path:
        return None
    m = _RADARR_EDITION_TOKEN_RE.search(path)
    return m.group(1).strip() if m else None


SETTING_POSTER_DESTINATION = "poster_destination"
SETTING_POSTER_DRIVE_PRIORITY = "poster_drive_priority"
SETTING_PLEX_WEBHOOK_DEDUPE_CACHE = "plex_webhook_dedupe_cache"
SETTING_PLEX_WEBHOOK_ENABLED = "plex_webhook_enabled"
SETTING_PLEX_WEBHOOK_REMOVE_OVERLAY_LABEL = "plex_webhook_remove_overlay_label"
SETTING_PLEX_WEBHOOK_RENAME_THEN_UPLOAD = "plex_webhook_rename_then_upload"
SETTING_PLEX_WEBHOOK_ADOPT_EXISTING_PROCESSED = "plex_webhook_adopt_existing_processed"
SETTING_PLEX_WEBHOOK_RETRY_ATTEMPTS = "plex_webhook_retry_attempts"
SETTING_PLEX_WEBHOOK_RETRY_DELAY_SECONDS = "plex_webhook_retry_delay_seconds"
SETTING_PLEX_WEBHOOK_STATS = "plex_webhook_stats"
SETTING_PLEX_LIBRARY_CONFIG = "plex_library_config"
SETTING_PLEX_UPLOAD_LIBRARY_OVERRIDE = "plex_upload_library_override"
SETTING_PLEX_UPLOAD_FILE_CACHE = "plex_upload_file_cache"
SETTING_PLEX_UPLOAD_MANUAL_DRY_RUN = "plex_upload_manual_dry_run"
SETTING_PLEX_UPLOAD_MANUAL_REAPPLY = "plex_upload_manual_reapply"
SETTING_PLEX_UPLOAD_MANUAL_REMOVE_OVERLAY_LABEL = "plex_upload_manual_remove_overlay_label"
PLEX_WEBHOOK_DEDUPE_WINDOW_SECONDS = 600
PLEX_WEBHOOK_RETRY_ATTEMPTS = 10
PLEX_WEBHOOK_RETRY_DELAY_SECONDS = 30
PLEX_WEBHOOK_RETRY_ATTEMPTS_MAX = 20
PLEX_WEBHOOK_RETRY_DELAY_SECONDS_MAX = 120


def _load_priority_source_dirs(db: Session) -> tuple[list[str], Optional[str]]:
    source_dir_context, source_dirs_error = _load_priority_source_dir_context(db)
    if source_dirs_error:
        return [], source_dirs_error
    return [entry["path"] for entry in source_dir_context], None


def _load_priority_source_dir_context(db: Session) -> tuple[list[Dict[str, Any]], Optional[str]]:
    priority_setting = get_setting(db, SETTING_POSTER_DRIVE_PRIORITY)
    if not priority_setting or not priority_setting.value:
        return [], "No drive priority configured. Configure in Poster Manager → Drive Priority tab."

    try:
        priority_payload = json.loads(priority_setting.value)
    except json.JSONDecodeError:
        return [], "Drive priority configuration is invalid. Reconfigure in Poster Manager → Drive Priority tab."

    if not isinstance(priority_payload, dict):
        return [], "Drive priority configuration is invalid. Reconfigure in Poster Manager → Drive Priority tab."

    raw_drive_ids = priority_payload.get("drive_ids", [])
    if not isinstance(raw_drive_ids, list) or not raw_drive_ids:
        return [], "Drive priority list is empty. Add source drives in Poster Manager → Drive Priority tab."

    drive_ids: list[int] = [int(drive_id) for drive_id in raw_drive_ids if isinstance(drive_id, int)]
    if not drive_ids:
        return [], "Drive priority list has no valid drive IDs. Reconfigure in Poster Manager → Drive Priority tab."

    drives = (
        db.query(Drive)
        .filter(
            Drive.id.in_(drive_ids),
            Drive.subscribed == True,
        )
        .all()
    )
    if not drives:
        return [], "No subscribed drives found in drive priority list. Subscribe drives and retry."

    drives_by_id = {drive.id: drive for drive in drives}
    source_dir_context = []
    for drive_id in drive_ids:
        drive = drives_by_id.get(drive_id)
        if not drive:
            continue

        drive_type = "custom" if drive.is_custom else str(drive.style_type).strip().lower()
        source_dir_context.append(
            {
                "drive_id": drive.id,
                "drive_name": drive.name,
                "drive_type": drive_type,
                "path": str(drive.get_local_path()),
            }
        )

    if not source_dir_context:
        return [], "No valid source folders found from drive priority list."

    return source_dir_context, None


def _coerce_webhook_retry_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _calculate_scaled_progress(
    *,
    processed: int,
    total: int,
    progress_start: int,
    progress_end: int,
    last_progress: int,
) -> tuple[int, int]:
    normalized_total = max(total, 1)
    ratio = min(max(processed / normalized_total, 0.0), 1.0)
    scaled_progress = int(progress_start + ratio * (progress_end - progress_start))
    next_progress = max(last_progress, min(scaled_progress, progress_end))
    return normalized_total, next_progress


def _mark_job_failed(
    db: Session,
    *,
    job_id: int,
    completion_label: str,
    error_message: str,
    failure_context: str,
) -> None:
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            update_job_state(
                db,
                job,
                status=JOB_STATUS_FAILED,
                progress=100,
                message=format_complete_message(completion_label, error_message),
                error=error_message,
                completed_at=datetime.now(timezone.utc),
            )
    except Exception as commit_error:
        log_error(LogTags.UPLOADER, f"Failed to update {failure_context} job status: {commit_error}\n{traceback.format_exc()}")
        db.rollback()


def _run_webhook_preupload_rename_pass(
    db: Session,
    job: Job,
    parsed_payload: Dict[str, Any],
    dry_run: bool = False,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "fast_path_skipped": False,
        "rename": {
            "attempted": False,
            "success": False,
            "matched": 0,
            "movies": 0,
            "series": 0,
            "collections": 0,
        },
        "border": {
            "attempted": False,
            "success": False,
            "changed": 0,
            "skipped": 0,
            "enabled": False,
            "dry_run": dry_run,
        },
        "tmp_promotion": {
            "attempted": False,
            "copied": 0,
            "skipped": 0,
            "errors": 0,
            "dry_run_skipped": False,
        },
    }

    destination_setting = get_setting(db, SETTING_POSTER_DESTINATION)
    destination_dir = destination_setting.value.strip() if destination_setting and destination_setting.value else ""
    if not destination_dir:
        log_warning(
            LogTags.UPLOADER,
            "Webhook rename-then-upload is enabled but poster destination is not configured; skipping rename prep",
        )
        return summary

    source_dirs, source_dirs_error = _load_priority_source_dirs(db)
    if source_dirs_error:
        log_warning(
            LogTags.UPLOADER,
            f"Webhook rename-then-upload source discovery failed; skipping rename prep: {source_dirs_error}",
        )
        return summary

    destination_assets_cache: Optional[list[Dict[str, Any]]] = None

    def _get_destination_assets(force_refresh: bool = False) -> list[Dict[str, Any]]:
        nonlocal destination_assets_cache
        if destination_assets_cache is not None and not force_refresh:
            return destination_assets_cache

        try:
            service = PlexUploadService(db)
            destination_assets_cache = service._discover_local_assets(Path(destination_dir))
        except Exception:
            destination_assets_cache = []
        return destination_assets_cache

    def _target_id_keys(payload: Dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        tmdb_id = payload.get("tmdb_id")
        tvdb_id = payload.get("tvdb_id")
        imdb_id = payload.get("imdb_id")
        if isinstance(tmdb_id, int):
            keys.add(f"tmdb:{tmdb_id}")
        if isinstance(tvdb_id, int):
            keys.add(f"tvdb:{tvdb_id}")
        if isinstance(imdb_id, str) and imdb_id.strip():
            keys.add(f"imdb:{imdb_id.strip().lower()}")
        return keys

    def _asset_id_keys_from_path(path_value: str) -> set[str]:
        matches = POSTER_ID_PATTERN.findall(path_value)
        if not matches:
            return set()
        return {f"{source.strip().lower()}:{value.strip().lower()}" for source, value in matches}

    def _destination_has_exact_target_asset() -> bool:
        target_ids = _target_id_keys(parsed_payload)
        if not target_ids:
            return False

        media_type = str(parsed_payload.get("media_type") or "").strip().lower()
        season_number = parsed_payload.get("season_number")

        destination_assets = _get_destination_assets()

        exact_id_assets = [
            asset
            for asset in destination_assets
            if bool(target_ids & _asset_id_keys_from_path(str(asset.get("path") or "")))
        ]
        if not exact_id_assets:
            return False

        if media_type == "movie":
            return any(asset.get("asset_type") == "main" for asset in exact_id_assets)
        if media_type == "series":
            if isinstance(season_number, int):
                return any(
                    asset.get("asset_type") == "season" and asset.get("season_number") == season_number
                    for asset in exact_id_assets
                )
            return any(asset.get("asset_type") == "main" for asset in exact_id_assets)
        return any(asset.get("asset_type") == "main" for asset in exact_id_assets)

    def _destination_has_basic_target_asset() -> bool:
        media_type = str(parsed_payload.get("media_type") or "").strip().lower()
        season_number = parsed_payload.get("season_number")

        target_title = str(parsed_payload.get("title") or "").strip()
        target_year = parsed_payload.get("year")
        title_keys: set[str] = set()
        if target_title:
            title_keys.add(normalize_titles(target_title))
            if isinstance(target_year, int):
                title_keys.add(normalize_titles(f"{target_title} ({target_year})"))

        if not title_keys:
            return False

        destination_assets = _get_destination_assets()

        matching_assets = [
            asset
            for asset in destination_assets
            if str(asset.get("media_key") or "").strip() in title_keys
        ]

        if not matching_assets:
            return False

        if media_type == "movie":
            return any(asset.get("asset_type") == "main" for asset in matching_assets)

        if media_type == "series":
            if isinstance(season_number, int):
                return any(
                    asset.get("asset_type") == "season" and int(asset.get("season_number") or -1) == season_number
                    for asset in matching_assets
                )
            return any(asset.get("asset_type") == "main" for asset in matching_assets)

        return any(asset.get("asset_type") == "main" for asset in matching_assets)

    def _source_target_is_unchanged_since_last_processed() -> bool:
        target_ids = _target_id_keys(parsed_payload)
        if not target_ids:
            return False

        try:
            source_assets, _source_index = get_assets_files(source_dirs, logger)
        except Exception:
            return False

        rename_service = PosterRenameService(db)
        filtered_assets, _filtered_index = rename_service._filter_assets_for_target(
            source_assets,
            target_media_type=parsed_payload.get("media_type"),
            target_title=parsed_payload.get("title"),
            target_year=parsed_payload.get("year"),
            target_tmdb_id=parsed_payload.get("tmdb_id"),
            target_tvdb_id=parsed_payload.get("tvdb_id"),
            target_imdb_id=parsed_payload.get("imdb_id"),
            target_season_number=parsed_payload.get("season_number"),
        )

        if len(filtered_assets) != 1:
            return False

        target_asset = filtered_assets[0]
        asset_files = target_asset.get("files") if isinstance(target_asset.get("files"), list) else []
        if len(asset_files) != 1:
            return False

        source_file = str(Path(str(asset_files[0])).resolve())
        source_poster = db.query(Poster).filter(Poster.file_path == source_file).first()
        if not source_poster or source_poster.last_processed is None:
            return False

        try:
            current_mtime = float(os.path.getmtime(source_file))
        except Exception:
            return False

        if source_poster.file_mtime is not None and abs(float(source_poster.file_mtime) - current_mtime) > 0.0001:
            return False

        last_processed = source_poster.last_processed
        if last_processed is None:
            return False
        if last_processed.tzinfo is None:
            last_processed = last_processed.replace(tzinfo=timezone.utc)

        current_mtime_dt = datetime.fromtimestamp(current_mtime, timezone.utc)
        if current_mtime_dt > last_processed:
            return False

        return True

    if _destination_has_exact_target_asset() and _source_target_is_unchanged_since_last_processed():
        summary["fast_path_skipped"] = True
        log_info(
            LogTags.UPLOADER,
            "Webhook pre-upload fast-path: exact target already current; skipping rename/border prep",
            title=parsed_payload.get("title"),
            media_type=parsed_payload.get("media_type"),
            season_number=parsed_payload.get("season_number"),
        )
        return summary

    adopt_existing_processed = _is_webhook_adopt_existing_processed_enabled(db)
    if adopt_existing_processed and _destination_has_basic_target_asset():
        summary["fast_path_skipped"] = True
        log_info(
            LogTags.UPLOADER,
            "Webhook pre-upload adopt-existing fast-path: destination already has target asset; skipping rename/border prep",
            title=parsed_payload.get("title"),
            media_type=parsed_payload.get("media_type"),
            season_number=parsed_payload.get("season_number"),
        )
        return summary

    update_job_state(
        db,
        job,
        status=JOB_STATUS_RUNNING,
        message=format_start_message("webhook upload", qualifier=f"for {parsed_payload.get('title') or 'item'}"),
        progress=8,
    )
    log_info(
        LogTags.UPLOADER,
        "Webhook rename-then-upload enabled: running pre-upload rename pass",
        title=parsed_payload.get("title"),
        media_type=parsed_payload.get("media_type"),
        source_dirs=len(source_dirs),
        dry_run=dry_run,
    )

    rename_service = PosterRenameService(db)
    summary["rename"]["attempted"] = True
    rename_result = rename_service.rename_posters(
        source_dirs=source_dirs,
        destination_dir=destination_dir,
        action_type="copy",
        asset_folders=True,
        dry_run=dry_run,
        use_temp_folder=True,
        target_media_type=parsed_payload.get("media_type"),
        target_title=parsed_payload.get("title"),
        target_year=parsed_payload.get("year"),
        target_tmdb_id=parsed_payload.get("tmdb_id"),
        target_tvdb_id=parsed_payload.get("tvdb_id"),
        target_imdb_id=parsed_payload.get("imdb_id"),
        target_season_number=parsed_payload.get("season_number"),
    )

    auto_run_border_enabled = (
        str(get_setting(db, "auto_run_border").value if get_setting(db, "auto_run_border") else "false").lower()
        == "true"
    )
    summary["border"]["enabled"] = auto_run_border_enabled

    def _promote_tmp_staging_to_destination() -> None:
        summary["tmp_promotion"]["attempted"] = True
        tmp_source_dir = os.path.join(destination_dir, "tmp")
        if not os.path.exists(tmp_source_dir):
            log_warning(
                LogTags.UPLOADER,
                "Webhook pre-upload tmp promotion skipped because tmp staging folder was not found",
                title=parsed_payload.get("title"),
                staging_dir=tmp_source_dir,
            )
            return

        copied_count = 0
        skipped_count = 0
        error_count = 0

        for item in os.listdir(tmp_source_dir):
            src_path = os.path.join(tmp_source_dir, item)
            dest_path = os.path.join(destination_dir, item)

            try:
                if os.path.isdir(src_path):
                    os.makedirs(dest_path, exist_ok=True)

                    for root, dirs, files in os.walk(src_path):
                        rel_dir = os.path.relpath(root, src_path)
                        dest_root = os.path.join(dest_path, rel_dir) if rel_dir != "." else dest_path

                        for dir_name in dirs:
                            os.makedirs(os.path.join(dest_root, dir_name), exist_ok=True)

                        for file_name in files:
                            src_file = os.path.join(root, file_name)
                            dest_file = os.path.join(dest_root, file_name)

                            if os.path.exists(dest_file) and filecmp.cmp(src_file, dest_file, shallow=False):
                                skipped_count += 1
                                continue

                            shutil.copy2(src_file, dest_file)
                            copied_count += 1
                elif os.path.isfile(src_path):
                    if os.path.exists(dest_path) and filecmp.cmp(src_path, dest_path, shallow=False):
                        skipped_count += 1
                    else:
                        shutil.copy2(src_path, dest_path)
                        copied_count += 1
            except Exception as copy_error:
                error_count += 1
                log_warning(
                    LogTags.UPLOADER,
                    f"Webhook pre-upload tmp promotion failed for item '{item}': {copy_error}",
                    title=parsed_payload.get("title"),
                    staging_dir=tmp_source_dir,
                )

        log_info(
            LogTags.UPLOADER,
            "Webhook pre-upload tmp promotion complete",
            title=parsed_payload.get("title"),
            copied=copied_count,
            skipped=skipped_count,
            errors=error_count,
        )
        summary["tmp_promotion"]["copied"] = copied_count
        summary["tmp_promotion"]["skipped"] = skipped_count
        summary["tmp_promotion"]["errors"] = error_count

    def _run_standard_border_pass_if_enabled() -> None:
        if not auto_run_border_enabled:
            return

        tmp_source_dir = os.path.join(destination_dir, "tmp")
        if not os.path.exists(tmp_source_dir):
            log_warning(
                LogTags.UPLOADER,
                "Webhook pre-upload border pass skipped because tmp staging folder was not found",
                title=parsed_payload.get("title"),
                staging_dir=tmp_source_dir,
            )
            return

        summary["border"]["attempted"] = True

        _colors_s = get_setting(db, "border_replacer_colors")
        try:
            colors = json.loads(_colors_s.value) if _colors_s and _colors_s.value else []
        except json.JSONDecodeError:
            colors = []

        _exclusions_s = get_setting(db, "border_replacer_exclusions")
        try:
            exclusions = json.loads(_exclusions_s.value) if _exclusions_s and _exclusions_s.value else []
        except json.JSONDecodeError:
            exclusions = []

        width_setting = get_setting(db, "border_replacer_width")
        try:
            border_width = int(width_setting.value) if width_setting and width_setting.value else 26
        except ValueError:
            border_width = 26

        remove_setting = get_setting(db, "border_replacer_remove_borders")
        remove_borders = bool(
            remove_setting
            and remove_setting.value
            and remove_setting.value.strip().lower() == "true"
        )

        mode_setting = get_setting(db, "border_replacer_mode")
        mode = mode_setting.value if mode_setting and mode_setting.value in {"full", "incremental"} else "incremental"

        border_service = BorderReplacerService(db)
        border_result = border_service.process_posters(
            source_dir=tmp_source_dir,
            destination_dir=destination_dir,
            border_colors=colors if isinstance(colors, list) and colors else None,
            remove_borders=remove_borders,
            border_width=border_width,
            exclusion_list=exclusions if isinstance(exclusions, list) else [],
            dry_run=dry_run,
            mode=mode,
        )

        if border_result.get("success", False):
            changed_count = int(border_result.get("changed", border_result.get("changed_count", 0)))
            skipped_count = int(border_result.get("skipped", border_result.get("skipped_count", 0)))
            summary["border"]["success"] = True
            summary["border"]["changed"] = changed_count
            summary["border"]["skipped"] = skipped_count
            log_success(
                LogTags.UPLOADER,
                "Webhook pre-upload border pass completed",
                title=parsed_payload.get("title"),
                changed=changed_count,
                skipped=skipped_count,
            )
            return

        border_error = str(border_result.get("error") or "Unknown border error").strip() or "Unknown border error"
        log_warning(
            LogTags.UPLOADER,
            f"Webhook pre-upload border pass failed ({border_error}); continuing with upload",
            title=parsed_payload.get("title"),
            error=border_error,
        )

    if rename_result.get("success", False):
        stats = rename_result.get("stats", {}) if isinstance(rename_result.get("stats", {}), dict) else {}
        summary["rename"]["success"] = True
        summary["rename"]["matched"] = int(stats.get("total_matched", 0))
        summary["rename"]["movies"] = int(stats.get("movies", 0))
        summary["rename"]["series"] = int(stats.get("series", 0))
        summary["rename"]["collections"] = int(stats.get("collections", 0))

        if auto_run_border_enabled:
            _run_standard_border_pass_if_enabled()
        else:
            if dry_run:
                summary["tmp_promotion"]["dry_run_skipped"] = True
                log_info(
                    LogTags.UPLOADER,
                    "Webhook pre-upload tmp promotion skipped in dry run mode",
                    title=parsed_payload.get("title"),
                )
            else:
                _promote_tmp_staging_to_destination()

        log_success(
            LogTags.UPLOADER,
            "Webhook pre-upload rename pass completed",
            title=parsed_payload.get("title"),
            matched=int(stats.get("total_matched", 0)),
            movies=int(stats.get("movies", 0)),
            series=int(stats.get("series", 0)),
            collections=int(stats.get("collections", 0)),
        )
        return summary

    rename_error = str(rename_result.get("error") or "Unknown rename error").strip() or "Unknown rename error"
    log_warning(
        LogTags.UPLOADER,
        f"Webhook pre-upload rename pass failed ({rename_error}); continuing with direct upload attempt",
        title=parsed_payload.get("title"),
        error=rename_error,
    )
    return summary


def _extract_season_number_from_file_name(file_name: str) -> Optional[int]:
    base_name = Path(file_name).stem.strip().lower()
    if not base_name:
        return None

    match = re.search(r"\bseason[\s._-]*(\d{1,3})\b", base_name, re.IGNORECASE)
    if not match:
        match = re.search(r"\bs(\d{1,3})\b", base_name, re.IGNORECASE)

    if not match:
        return None

    try:
        return int(match.group(1))
    except (ValueError, IndexError):
        return None

def _is_webhook_enabled(db: Session) -> bool:
    return _read_bool_setting(db, SETTING_PLEX_WEBHOOK_ENABLED)


def _read_bool_setting(db: Session, key: str, *, default: bool = False) -> bool:
    setting = get_setting(db, key)
    if not setting or setting.value is None:
        return default
    return setting.value.strip().lower() == "true"


def _get_manual_dry_run_enabled(db: Session) -> bool:
    return _read_bool_setting(db, SETTING_PLEX_UPLOAD_MANUAL_DRY_RUN, default=True)


def _get_manual_reapply_enabled(db: Session) -> bool:
    return _read_bool_setting(db, SETTING_PLEX_UPLOAD_MANUAL_REAPPLY, default=False)


def _get_manual_remove_overlay_label_enabled(db: Session) -> bool:
    return _read_bool_setting(db, SETTING_PLEX_UPLOAD_MANUAL_REMOVE_OVERLAY_LABEL, default=False)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_webhook_remove_overlay_label_enabled(db: Session) -> bool:
    return _read_bool_setting(db, SETTING_PLEX_WEBHOOK_REMOVE_OVERLAY_LABEL)


def _is_webhook_rename_then_upload_enabled(db: Session) -> bool:
    return _read_bool_setting(db, SETTING_PLEX_WEBHOOK_RENAME_THEN_UPLOAD)


def _is_webhook_adopt_existing_processed_enabled(db: Session) -> bool:
    return _read_bool_setting(db, SETTING_PLEX_WEBHOOK_ADOPT_EXISTING_PROCESSED)


def _get_webhook_retry_attempts(db: Session) -> int:
    setting = get_setting(db, SETTING_PLEX_WEBHOOK_RETRY_ATTEMPTS)
    if not setting or setting.value is None:
        return PLEX_WEBHOOK_RETRY_ATTEMPTS
    return _coerce_webhook_retry_int(
        setting.value,
        default=PLEX_WEBHOOK_RETRY_ATTEMPTS,
        minimum=1,
        maximum=PLEX_WEBHOOK_RETRY_ATTEMPTS_MAX,
    )


def _get_webhook_retry_delay_seconds(db: Session) -> int:
    setting = get_setting(db, SETTING_PLEX_WEBHOOK_RETRY_DELAY_SECONDS)
    if not setting or setting.value is None:
        return PLEX_WEBHOOK_RETRY_DELAY_SECONDS
    return _coerce_webhook_retry_int(
        setting.value,
        default=PLEX_WEBHOOK_RETRY_DELAY_SECONDS,
        minimum=1,
        maximum=PLEX_WEBHOOK_RETRY_DELAY_SECONDS_MAX,
    )


def _default_webhook_stats() -> Dict[str, Any]:
    return {
        "received": 0,
        "queued": 0,
        "duplicates": 0,
        "skipped_test": 0,
        "skipped_cached": 0,
        "rejected_disabled": 0,
        "parse_errors": 0,
        "internal_errors": 0,
        "last_event_at": None,
        "last_queued_at": None,
        "last_error": None,
    }


def _load_webhook_stats(db: Session) -> Dict[str, Any]:
    setting = get_setting(db, SETTING_PLEX_WEBHOOK_STATS)
    if not setting or not setting.value:
        return _default_webhook_stats()
    try:
        loaded = json.loads(setting.value)
    except json.JSONDecodeError:
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    defaults = _default_webhook_stats()
    defaults.update({k: v for k, v in loaded.items() if k in defaults})
    return defaults


def _load_library_configs_setting(db: Session, setting_key: str) -> list[Dict[str, Any]]:
    setting = get_setting(db, setting_key)
    if not setting or not setting.value:
        return []
    try:
        parsed = json.loads(setting.value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return parsed


def _load_plex_upload_library_override(db: Session) -> Dict[str, Any]:
    setting = get_setting(db, SETTING_PLEX_UPLOAD_LIBRARY_OVERRIDE)
    if not setting or not setting.value:
        return {"enabled": False, "configs": []}
    try:
        parsed = json.loads(setting.value)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    enabled = bool(parsed.get("enabled", False))
    configs = parsed.get("configs", [])
    if not isinstance(configs, list):
        configs = []

    return {
        "enabled": enabled,
        "configs": configs,
    }


def _load_plex_upload_file_cache(db: Session) -> Dict[str, Dict[str, Any]]:
    from models.plex_upload import PlexUploadRecord
    records = db.query(PlexUploadRecord).all()
    return {record.file_path: record.to_dict() for record in records}


def _cache_summary(cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    entries = []
    total_library_refs = 0
    total_edition_refs = 0

    for file_path, value in cache.items():
        libraries = value.get("uploaded_to_libraries", [])
        editions = value.get("uploaded_editions", [])
        media_types = value.get("uploaded_media_types", [])
        total_library_refs += len(libraries)
        total_edition_refs += len(editions)
        entries.append(
            {
                "file_path": file_path,
                "uploaded_to_libraries": libraries,
                "uploaded_to_library_keys": value.get("uploaded_to_library_keys", []),
                "uploaded_editions": editions,
                "uploaded_media_types": media_types,
            }
        )

    return {
        "entries_count": len(entries),
        "total_library_refs": total_library_refs,
        "total_edition_refs": total_edition_refs,
        "entries": entries,
    }



def _fast_cache_summary(db: Session) -> Dict[str, Any]:
    """
    Build a cache summary using targeted SQL queries instead of loading all records.

    Returns the same shape as _cache_summary but only fetches the last 10 entries
    for the recent-entries display widget.  Aggregate counts use SQL so the event
    loop is not held up iterating thousands of ORM objects.
    """
    from models.plex_upload import PlexUploadRecord

    entries_count = db.query(func.count()).select_from(PlexUploadRecord).scalar() or 0

    total_library_refs = (
        db.query(
            func.coalesce(
                func.sum(func.json_array_length(PlexUploadRecord.uploaded_to_libraries)),
                0,
            )
        ).scalar()
        or 0
    )

    total_edition_refs = (
        db.query(
            func.coalesce(
                func.sum(func.json_array_length(PlexUploadRecord.uploaded_editions)),
                0,
            )
        ).scalar()
        or 0
    )

    recent_records_desc = (
        db.query(PlexUploadRecord)
        .order_by(PlexUploadRecord.id.desc())
        .limit(10)
        .all()
    )
    # Reverse back to ascending order so the frontend's .slice(-10).reverse() works correctly
    recent_records = list(reversed(recent_records_desc))

    entries = [
        {
            "file_path": record.file_path,
            **record.to_dict(),
        }
        for record in recent_records
    ]

    return {
        "entries_count": entries_count,
        "total_library_refs": int(total_library_refs),
        "total_edition_refs": int(total_edition_refs),
        "entries": entries,
    }


def _export_title_from_file_path(file_path: str) -> str:
    path = file_path.strip().rstrip("/")
    if not path:
        return "Unknown"

    parts = [part for part in path.split("/") if part]
    if not parts:
        return "Unknown"

    filename = parts[-1].lower()
    if filename.startswith("season") and len(parts) >= 2:
        return parts[-2]
    if filename == "poster.jpg" and len(parts) >= 2:
        return parts[-2]

    stem = parts[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return stem


def _export_season_number_from_file_path(file_path: str) -> Optional[int]:
    path = file_path.strip().rstrip("/")
    if not path:
        return None

    parts = [part for part in path.split("/") if part]
    if not parts:
        return None

    filename = parts[-1].lower()
    if not filename.startswith("season"):
        return None

    digits = "".join(ch for ch in filename if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _classify_cache_media_type(file_path: str, value: Dict[str, Any]) -> str:
    lowered = file_path.lower()
    if "/season" in lowered:
        return "seasons"

    media_types = value.get("uploaded_media_types", [])
    if isinstance(media_types, list):
        normalized_media_types = {
            str(media_type).strip().lower()
            for media_type in media_types
            if isinstance(media_type, str) and media_type.strip()
        }
        if "collections" in normalized_media_types:
            return "collections"
        if "movies" in normalized_media_types:
            return "movies"
        if "seasons" in normalized_media_types:
            return "seasons"
        if "shows" in normalized_media_types:
            return "shows"

    editions = value.get("uploaded_editions", [])
    if isinstance(editions, list) and any(isinstance(edition, str) and edition.strip() for edition in editions):
        return "movies"

    if "{tvdb-" in lowered:
        return "shows"

    return "collections"


def _build_cache_grouped_by_library(cache: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for file_path, value in cache.items():
        libraries = value.get("uploaded_to_libraries", [])
        editions = value.get("uploaded_editions", [])
        if not isinstance(libraries, list):
            libraries = []
        if not isinstance(editions, list):
            editions = []

        media_type = _classify_cache_media_type(file_path, value)
        title = _export_title_from_file_path(file_path)
        season_number = _export_season_number_from_file_path(file_path)
        entry = {
            "title": title,
            "file_path": file_path,
            "uploaded_editions": editions,
            "uploaded_media_types": value.get("uploaded_media_types", []),
            "season_number": season_number,
        }

        for library in libraries:
            if not isinstance(library, str) or not library.strip():
                continue
            library_name = library.strip()
            if library_name not in grouped:
                grouped[library_name] = {
                    "collections": [],
                    "movies": [],
                    "shows_by_title": {},
                }
            if media_type == "collections":
                grouped[library_name]["collections"].append(entry)
            elif media_type == "movies":
                grouped[library_name]["movies"].append(entry)
            elif media_type in {"shows", "seasons"}:
                shows_by_title = grouped[library_name]["shows_by_title"]
                show_group = shows_by_title.get(title)
                if not isinstance(show_group, dict):
                    show_group = {
                        "title": title,
                        "show_posters": [],
                        "seasons": [],
                    }
                    shows_by_title[title] = show_group

                if media_type == "shows":
                    show_group["show_posters"].append(entry)
                else:
                    show_group["seasons"].append(entry)

    sorted_grouped: Dict[str, Dict[str, Any]] = {}
    for library_name in sorted(grouped):
        library_groups = grouped[library_name]
        sorted_collections = sorted(library_groups["collections"], key=lambda item: item["title"].lower())
        sorted_movies = sorted(library_groups["movies"], key=lambda item: item["title"].lower())
        shows_by_title = library_groups.get("shows_by_title", {})
        sorted_shows = []
        for show_title in sorted(shows_by_title):
            show_group = shows_by_title[show_title]
            show_posters = sorted(show_group.get("show_posters", []), key=lambda item: item["file_path"].lower())
            seasons = sorted(
                show_group.get("seasons", []),
                key=lambda item: (
                    int(item.get("season_number") or 0),
                    item.get("file_path", "").lower(),
                ),
            )
            sorted_shows.append(
                {
                    "title": show_title,
                    "show_posters": show_posters,
                    "seasons": seasons,
                    "counts": {
                        "show_posters": len(show_posters),
                        "seasons": len(seasons),
                    },
                }
            )

        shows_total = sum(group["counts"]["show_posters"] for group in sorted_shows)
        seasons_total = sum(group["counts"]["seasons"] for group in sorted_shows)
        sorted_grouped[library_name] = {
            "totals": {
                "collections": len(sorted_collections),
                "movies": len(sorted_movies),
                "shows": shows_total,
                "seasons": seasons_total,
            },
            "collections": sorted_collections,
            "movies": sorted_movies,
            "shows": sorted_shows,
        }

    return sorted_grouped


def _build_library_totals(grouped_by_library: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    per_library: Dict[str, Dict[str, int]] = {}
    totals = {
        "libraries": 0,
        "collections": 0,
        "movies": 0,
        "shows": 0,
        "seasons": 0,
    }

    for library_name, grouped in grouped_by_library.items():
        grouped_totals = grouped.get("totals", {}) if isinstance(grouped, dict) else {}
        collections_count = int(grouped_totals.get("collections", 0)) if isinstance(grouped_totals, dict) else 0
        movies_count = int(grouped_totals.get("movies", 0)) if isinstance(grouped_totals, dict) else 0
        shows_count = int(grouped_totals.get("shows", 0)) if isinstance(grouped_totals, dict) else 0
        seasons_count = int(grouped_totals.get("seasons", 0)) if isinstance(grouped_totals, dict) else 0

        per_library[library_name] = {
            "collections": collections_count,
            "movies": movies_count,
            "shows": shows_count,
            "seasons": seasons_count,
        }

        totals["libraries"] += 1
        totals["collections"] += collections_count
        totals["movies"] += movies_count
        totals["shows"] += shows_count
        totals["seasons"] += seasons_count

    return {
        "totals": totals,
        "per_library": per_library,
    }


def _build_cache_export_payload(cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    grouped_by_library = _build_cache_grouped_by_library(cache)
    return {
        "exported_at": _utc_now_iso(),
        "entries_count": len(cache),
        "library_totals": _build_library_totals(grouped_by_library),
        "grouped_by_library": grouped_by_library,
    }


def _build_cache_clear_response(
    cache: Dict[str, Dict[str, Any]],
    *,
    removed: int,
    cleared_file_path: Optional[str],
) -> Dict[str, Any]:
    summary = _cache_summary(cache)
    summary.update(
        {
            "success": True,
            "removed": removed,
            "cleared_file_path": cleared_file_path,
        }
    )
    return summary


def _save_webhook_stats(db: Session, stats: Dict[str, Any]) -> None:
    upsert_setting(db, SETTING_PLEX_WEBHOOK_STATS, json.dumps(stats))
    db.commit()


def _increment_webhook_stat(db: Session, key: str, *, last_error: Optional[str] = None, queued: bool = False) -> None:
    stats = _load_webhook_stats(db)
    if key in stats and isinstance(stats[key], int):
        stats[key] += 1
    stats["last_event_at"] = _utc_now_iso()
    if queued:
        stats["last_queued_at"] = stats["last_event_at"]
    if last_error:
        stats["last_error"] = last_error
    _save_webhook_stats(db, stats)


def _reset_webhook_stats(db: Session) -> Dict[str, Any]:
    stats = _default_webhook_stats()
    _save_webhook_stats(db, stats)
    return stats


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_arr_webhook_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(payload.get("eventType") or payload.get("event") or "unknown").strip().lower()

    if event_type == "test":
        return {
            "skip": True,
            "reason": "ARR test webhook received",
            "event_type": event_type,
        }

    if isinstance(payload.get("movie"), dict):
        movie = payload["movie"]
        title = str(movie.get("title") or payload.get("movieTitle") or "").strip()
        year = _to_int(movie.get("year") or payload.get("movieYear"))
        if not title:
            raise ValueError("Missing movie title in webhook payload")

        movie_file = payload.get("movieFile") or {}
        movie_file_path = str(movie_file.get("path") or movie_file.get("relativePath") or "").strip()

        return {
            "event_type": event_type,
            "media_type": "movie",
            "title": title,
            "year": year,
            "season_number": None,
            "source": "radarr",
            "tmdb_id": _to_int(movie.get("tmdbId") or payload.get("tmdbId")),
            "imdb_id": str(movie.get("imdbId") or payload.get("imdbId") or "").strip() or None,
            "tvdb_id": None,
            "is_upgrade": bool(payload.get("isUpgrade", False)),
            "movie_file_path": movie_file_path,
        }

    if isinstance(payload.get("series"), dict):
        series = payload["series"]
        title = str(series.get("title") or payload.get("seriesTitle") or "").strip()
        year = _to_int(series.get("year"))
        if not title:
            raise ValueError("Missing series title in webhook payload")

        season_number = _to_int(payload.get("seasonNumber"))
        episodes = payload.get("episodes")
        if season_number is None and isinstance(episodes, list) and episodes:
            first_episode = episodes[0]
            if isinstance(first_episode, dict):
                season_number = _to_int(first_episode.get("seasonNumber"))

        return {
            "event_type": event_type,
            "media_type": "series",
            "title": title,
            "year": year,
            "season_number": season_number,
            "source": "sonarr",
            "tmdb_id": None,
            "imdb_id": str(series.get("imdbId") or payload.get("imdbId") or "").strip() or None,
            "tvdb_id": _to_int(series.get("tvdbId") or payload.get("tvdbId")),
        }

    raise ValueError("Unsupported webhook payload: expected Radarr movie or Sonarr series object")


def _load_webhook_dedupe_cache(db: Session) -> Dict[str, Any]:
    cache_setting = get_setting(db, SETTING_PLEX_WEBHOOK_DEDUPE_CACHE)
    try:
        cache = json.loads(cache_setting.value) if cache_setting and cache_setting.value else {}
    except json.JSONDecodeError:
        cache = {}

    if not isinstance(cache, dict):
        return {}

    return cache


def _prune_webhook_dedupe_cache(cache: Dict[str, Any], cutoff_ts: int) -> Dict[str, int]:
    return {
        key: value
        for key, value in cache.items()
        if isinstance(value, int) and value >= cutoff_ts
    }


def _load_pruned_webhook_dedupe_cache(db: Session, cutoff_ts: int) -> Dict[str, int]:
    cache = _load_webhook_dedupe_cache(db)
    return _prune_webhook_dedupe_cache(cache, cutoff_ts)


def _is_duplicate_webhook_event(db: Session, parsed_payload: Dict[str, Any]) -> bool:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff_ts = now_ts - PLEX_WEBHOOK_DEDUPE_WINDOW_SECONDS

    # Season number IS included in the dedupe key for series.
    # Sonarr fires one webhook per episode import, so all per-episode webhooks for
    # the same season collapse to a single job (same season_number → same key).
    # Different seasons of the same show each get their own dedupe slot, so Season 2
    # arriving within the window after Season 1 is NOT treated as a duplicate.
    # For movies, season_number is always None, so this has no effect on movie dedup.
    dedupe_key = ":".join(
        [
            parsed_payload.get("source", "unknown"),
            parsed_payload.get("event_type", "unknown"),
            parsed_payload.get("media_type", "unknown"),
            normalize_titles(parsed_payload.get("title", "")),
            str(parsed_payload.get("year") or ""),
            str(parsed_payload.get("season_number") if parsed_payload.get("season_number") is not None else ""),
        ]
    )

    pruned_cache = _load_pruned_webhook_dedupe_cache(db, cutoff_ts)

    is_duplicate = dedupe_key in pruned_cache
    pruned_cache[dedupe_key] = now_ts

    upsert_setting(db, SETTING_PLEX_WEBHOOK_DEDUPE_CACHE, json.dumps(pruned_cache))
    db.commit()

    return is_duplicate


def _clear_webhook_dedupe_cache(
    db: Session,
    *,
    clear_all: bool = False,
    source: Optional[str] = None,
    event_type: Optional[str] = None,
    media_type: Optional[str] = None,
    title: Optional[str] = None,
    year: Optional[int] = None,
    season_number: Optional[int] = None,
) -> Dict[str, Any]:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff_ts = now_ts - PLEX_WEBHOOK_DEDUPE_WINDOW_SECONDS

    source_key = source.strip().lower() if isinstance(source, str) and source.strip() else None
    event_key = event_type.strip().lower() if isinstance(event_type, str) and event_type.strip() else None
    media_key = media_type.strip().lower() if isinstance(media_type, str) and media_type.strip() else None
    title_key = normalize_titles(title) if isinstance(title, str) and title.strip() else None
    year_key = str(year) if isinstance(year, int) else None
    season_key = str(season_number) if isinstance(season_number, int) else None

    pruned_cache = _load_pruned_webhook_dedupe_cache(db, cutoff_ts)

    filtered_cache: Dict[str, int] = {}
    removed = 0

    for dedupe_key, timestamp in pruned_cache.items():
        if clear_all:
            removed += 1
            continue

        # Support both the current 6-part key (source:event:media:title:year:season)
        # and legacy 5-part keys (source:event:media:title:year) from before season was added.
        parts = str(dedupe_key).split(":", 5)
        if len(parts) == 6:
            key_source, key_event, key_media, key_title, key_year, key_season = parts
        elif len(parts) == 5:
            key_source, key_event, key_media, key_title, key_year = parts
            key_season = None
        else:
            filtered_cache[dedupe_key] = timestamp
            continue

        if source_key and key_source != source_key:
            filtered_cache[dedupe_key] = timestamp
            continue
        if event_key and key_event != event_key:
            filtered_cache[dedupe_key] = timestamp
            continue
        if media_key and key_media != media_key:
            filtered_cache[dedupe_key] = timestamp
            continue
        if title_key and key_title != title_key:
            filtered_cache[dedupe_key] = timestamp
            continue
        if year_key and key_year != year_key:
            filtered_cache[dedupe_key] = timestamp
            continue
        # Only filter by season when the entry has a season component; legacy 5-part
        # entries (key_season is None) are left untouched when a season filter is given.
        if season_key and key_season is not None and key_season != season_key:
            filtered_cache[dedupe_key] = timestamp
            continue

        removed += 1

    upsert_setting(db, SETTING_PLEX_WEBHOOK_DEDUPE_CACHE, json.dumps(filtered_cache))
    db.commit()

    return {
        "success": True,
        "removed": removed,
        "remaining": len(filtered_cache),
        "window_seconds": PLEX_WEBHOOK_DEDUPE_WINDOW_SECONDS,
        "cleared_all": clear_all,
    }


def _search_webhook_dedupe_entries(
    db: Session,
    *,
    query: str = "",
    media_type: Optional[str] = None,
    limit: int = 25,
) -> Dict[str, Any]:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff_ts = now_ts - PLEX_WEBHOOK_DEDUPE_WINDOW_SECONDS

    query_normalized = normalize_titles(query.strip()) if isinstance(query, str) and query.strip() else ""
    media_type_normalized = media_type.strip().lower() if isinstance(media_type, str) and media_type.strip() else None
    safe_limit = max(1, min(int(limit), 100))

    pruned_cache = _load_pruned_webhook_dedupe_cache(db, cutoff_ts)

    entries: list[Dict[str, Any]] = []
    for dedupe_key, timestamp in pruned_cache.items():
        # Support both the current 6-part key (source:event:media:title:year:season)
        # and legacy 5-part keys (source:event:media:title:year) from before season was added.
        parts = str(dedupe_key).split(":", 5)
        if len(parts) == 6:
            key_source, key_event, key_media, key_title, key_year, key_season = parts
        elif len(parts) == 5:
            key_source, key_event, key_media, key_title, key_year = parts
            key_season = None
        else:
            continue

        if media_type_normalized and key_media != media_type_normalized:
            continue

        haystack = " ".join([key_source, key_event, key_media, key_title, key_year]).strip()
        if query_normalized and query_normalized not in haystack:
            continue

        year_value = int(key_year) if key_year.isdigit() else None
        season_value = int(key_season) if key_season is not None and key_season.isdigit() else None
        last_seen = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat()

        entries.append(
            {
                "source": key_source,
                "event_type": key_event,
                "media_type": key_media,
                "title": key_title,
                "year": year_value,
                "season_number": season_value,
                "dedupe_key": dedupe_key,
                "last_seen_at": last_seen,
            }
        )

    entries.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    limited_entries = entries[:safe_limit]

    return {
        "query": query,
        "media_type": media_type_normalized,
        "count": len(limited_entries),
        "total": len(entries),
        "window_seconds": PLEX_WEBHOOK_DEDUPE_WINDOW_SECONDS,
        "items": limited_entries,
    }


def run_plex_upload_background_job(
    job_id: int,
    dry_run: bool = False,
    reapply: bool = False,
    remove_overlay_label: bool = False,
) -> None:
    db = SessionLocal()
    handler_id = add_job_log_handler("plex_upload", job_id, "Plex Upload")
    success = False

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.UPLOADER, f"Plex upload job {job_id} not found in database")
            return

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            message=format_start_message(
                "Plex upload",
                dry_run=dry_run,
                qualifier="(reapply)" if reapply else None,
            ),
            progress=5,
        )

        log_section_start(LogTags.UPLOADER, f"Plex Upload Job {job_id}")

        service = PlexUploadService(db)

        callback_label = "Plex dry run" if dry_run else "Plex upload"
        callback_start = 10
        callback_end = 95
        last_progress = int(job.progress or 0)
        last_emit_time = 0.0

        def progress_callback(processed: int, total: int, stats: Dict[str, int], message: str) -> None:
            nonlocal last_progress, last_emit_time
            normalized_total, next_progress = _calculate_scaled_progress(
                processed=processed,
                total=total,
                progress_start=callback_start,
                progress_end=callback_end,
                last_progress=last_progress,
            )
            now = time.monotonic()
            should_emit = (
                processed == normalized_total
                or next_progress > last_progress
                or (now - last_emit_time) >= 1.25
            )
            if not should_emit:
                return

            upload_label = "would_upload" if dry_run else "uploaded"
            upload_count = int(stats.get(upload_label, 0))
            state_message = (
                f"{callback_label}: {processed}/{normalized_total} processed | "
                f"matched={int(stats.get('matched', 0))}, "
                f"{upload_label}={upload_count}, "
                f"skipped={int(stats.get('skipped', 0))}, "
                f"errors={int(stats.get('errors', 0))}"
            )
            if message:
                state_message = message

            update_job_state(
                db,
                job,
                progress=next_progress,
                message=state_message,
            )
            last_progress = next_progress
            last_emit_time = now

        result = service.run_full_upload(
            dry_run=dry_run,
            reapply=reapply,
            remove_overlay_label=remove_overlay_label,
            progress_callback=progress_callback,
        )

        if not result.get("success", False):
            raise Exception(result.get("error", "Plex upload failed"))

        stats = result.get("stats", {})
        uploaded_label = "would_upload" if dry_run else "uploaded"
        uploaded_count = int(stats.get(uploaded_label, 0))
        matched_count = int(stats.get("matched", 0))
        scanned_count = int(stats.get("scanned", 0))
        raw_candidates = int(stats.get("candidate_matches_raw", 0))
        unique_candidates = int(stats.get("candidate_matches_unique", 0))
        main_assets = int(stats.get("main_assets", 0))
        season_assets = int(stats.get("season_assets", 0))
        library_totals = stats.get("library_totals", [])
        media_upload_counts = stats.get("media_upload_counts", {}) if isinstance(stats.get("media_upload_counts", {}), dict) else {}
        movies_uploaded = int(media_upload_counts.get("movies", 0))
        shows_uploaded = int(media_upload_counts.get("shows", 0))
        seasons_uploaded = int(media_upload_counts.get("seasons", 0))
        collections_uploaded = int(media_upload_counts.get("collections", 0))

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message(
                "Plex upload",
                (
                    f"({'dry run' if dry_run else 'live'}): "
                    f"{uploaded_count:,} uploaded, {matched_count:,}/{scanned_count:,} matched, "
                    f"candidates {unique_candidates:,}/{raw_candidates:,} unique/raw"
                ),
            ),
            completed_at=datetime.now(timezone.utc),
        )

        log_success(
            LogTags.UPLOADER,
            "Plex upload completed",
            dry_run=dry_run,
            reapply=reapply,
            remove_overlay_label=remove_overlay_label,
            scanned=scanned_count,
            matched=matched_count,
            uploaded=uploaded_count,
            candidates_raw=raw_candidates,
            candidates_unique=unique_candidates,
            assets_main=main_assets,
            assets_season=season_assets,
            movies=movies_uploaded,
            shows=shows_uploaded,
            seasons=seasons_uploaded,
            collections=collections_uploaded,
            skipped=int(stats.get("skipped", 0)),
            errors=int(stats.get("errors", 0)),
        )

        summary_label = "would_upload" if dry_run else "uploaded"
        log_info(
            LogTags.UPLOADER,
            (
                f"Upload totals (final): {summary_label} | "
                f"movies={movies_uploaded}, shows={shows_uploaded}, "
                f"seasons={seasons_uploaded}, collections={collections_uploaded}, total={uploaded_count}"
            ),
        )

        if isinstance(library_totals, list) and library_totals:
            log_info(LogTags.UPLOADER, "Library summary (final)")
            for row in sorted(
                library_totals,
                key=lambda entry: (
                    str(entry.get("instance", "")),
                    str(entry.get("library", "")),
                    str(entry.get("section_type", "")),
                ),
            ):
                instance_name = str(row.get("instance", "Unknown"))
                library_name = str(row.get("library", "Unknown"))
                section_type = str(row.get("section_type", "unknown"))
                items = int(row.get("items", 0))
                collections = int(row.get("collections", 0))
                log_info(
                    LogTags.UPLOADER,
                    f"- {instance_name} / {library_name} ({section_type}) | items={items}, collections={collections}",
                )

        log_section_end(LogTags.UPLOADER, "Plex Upload Complete")
        success = True

    except Exception as e:
        log_error(LogTags.UPLOADER, f"Plex upload job failed: {e}\n{traceback.format_exc()}")
        send_major_error_notification(
            db,
            source="plex_upload.full",
            message=str(e),
            job_id=job_id,
        )
        _mark_job_failed(
            db,
            job_id=job_id,
            completion_label="Plex upload failed",
            error_message=str(e),
            failure_context="plex upload",
        )
    finally:
        remove_job_log_handler(handler_id, "plex_upload", success=success)
        db.close()

def _has_destination_assets_for_target(
    service: PlexUploadService,
    media_type: str | None,
    title: str | None,
    parsed_payload: Dict[str, Any],
    target_season_number: Optional[int],
) -> bool:
    try:
        destination_dir = service._get_destination_dir()
        destination_assets = service._get_local_assets(destination_dir)
        matched_assets = service._select_local_assets_for_target(
            destination_assets,
            media_type=str(media_type or "").strip().lower(),
            title=str(title or "").strip(),
            year=parsed_payload.get("year"),
            season_number=target_season_number,
            tmdb_id=parsed_payload.get("tmdb_id"),
            tvdb_id=parsed_payload.get("tvdb_id"),
            imdb_id=parsed_payload.get("imdb_id"),
        )
        return bool(matched_assets)
    except Exception:
        return False


def _build_no_local_assets_warning_message(media_type: str | None, title: str | None) -> str:
    return (
        "No local assets found for webhook target after pre-upload steps. "
        "Add the source drive in Poster Manager \u2192 Drive Priority or place the poster in destination. "
        f"Target: {media_type}: {title}"
    )


def _complete_no_local_assets_warning(
    db: Session,
    job: Job,
    job_id: int,
    media_type: str | None,
    title: str | None,
    season_number: int | None,
) -> None:
    warning_message = _build_no_local_assets_warning_message(media_type, title)

    update_job_state(
        db,
        job,
        status=JOB_STATUS_COMPLETED,
        progress=100,
        message=format_complete_message(
            "Webhook skipped (no local assets)",
            f"{title}: no local source poster found in drive priority",
        ),
        completed_at=datetime.now(timezone.utc),
    )

    log_warning(
        LogTags.UPLOADER,
        f"Webhook completed with warning: no local assets found for {str(media_type or 'item').upper()} - {title}",
        media_type=media_type,
        title=title,
        season_number=season_number,
        warning=warning_message,
    )

    send_discord_notification(
        db,
        feature_key="plex_upload",
        event_type="info",
        title="Plex Upload Warning",
        description=warning_message,
        fields=[
            {"name": "Media", "value": str(media_type or "unknown"), "inline": True},
            {"name": "Title", "value": str(title or "unknown"), "inline": True},
            {"name": "Job ID", "value": str(job_id), "inline": True},
        ],
        color=0xFFB74D,
    )

    log_section_end(LogTags.UPLOADER, "Plex Webhook Upload Complete")


def _build_destination_presence_checks(
    webhook_targets: list[Dict[str, Any]],
    service: PlexUploadService,
    media_type: str | None,
    title: str | None,
    parsed_payload: Dict[str, Any],
) -> list[Dict[str, Any]]:
    checks: list[Dict[str, Any]] = []
    for target in webhook_targets:
        target_season_number = target.get("season_number")
        exists_in_destination = _has_destination_assets_for_target(
            service, media_type, title, parsed_payload, target_season_number
        )
        checks.append(
            {
                "season_number": target_season_number,
                "label": target.get("label") or "target",
                "exists": exists_in_destination,
            }
        )
    return checks


def _invalidate_retry_caches(service: PlexUploadService) -> None:
    service.invalidate_preflight_cache()
    service.invalidate_arr_availability_cache()


def _handle_radarr_upgrade_edition_check(
    service: PlexUploadService,
    parsed_payload: Dict[str, Any],
    media_type: str,
    title: str,
) -> None:
    """For Radarr upgrade events, check if the file path signals an edition change and clear the
    upload cache if so, ensuring the pre-gate subsequently returns False and the upload runs.

    Handles two edition-change scenarios:
      - No edition → edition: new file has {edition-*} token not yet in cache → clear cache.
      - Edition → no edition: new file has no {edition-*} token but cache has prior real editions
        → edition was removed; Plex creates a new no-edition item that needs a poster → clear cache.

    A quality-only upgrade (no prior edition, no new edition) leaves the cache untouched.

    Uses only local DB data — no Plex API calls — so there is no timing dependency on Plex
    having rescanned the new file.
    """
    movie_file_path = str(parsed_payload.get("movie_file_path") or "")
    edition_hint = _extract_edition_from_radarr_path(movie_file_path)

    if not edition_hint:
        # No {edition-*} token in the new file path. This is either:
        #   (a) a quality-only upgrade for a non-edition movie — cache is still valid, or
        #   (b) the edition was REMOVED (edition → no-edition) — Plex will create a new
        #       no-edition library item that has no poster yet, so we must clear the cache.
        # Distinguish the two cases by checking whether any real editions are cached.
        cached_editions = service.get_cached_editions_for_target(
            media_type=media_type,
            title=title,
            year=parsed_payload.get("year"),
            tmdb_id=parsed_payload.get("tmdb_id"),
            tvdb_id=parsed_payload.get("tvdb_id"),
            imdb_id=parsed_payload.get("imdb_id"),
        )
        real_cached_editions = {e for e in cached_editions if e != PlexUploadService.DEFAULT_EDITION_MOVIE}

        if not real_cached_editions:
            # Case (a): no prior edition, no new edition — quality-only upgrade.
            log_debug(
                LogTags.UPLOADER,
                "Radarr upgrade: no edition token in file path and no prior editions cached — skipping cache check (quality-only upgrade)",
                title=title,
                movie_file_path=movie_file_path or "(not provided)",
            )
            return

        # Case (b): prior editions found but new file has no edition — edition was removed.
        # Plex will create a new no-edition entry; clear the cache to force a re-upload.
        log_info(
            LogTags.UPLOADER,
            f"Radarr upgrade: no edition in new file path but prior editions cached {sorted(real_cached_editions)} — edition removed, clearing cache to force re-upload",
            title=title,
            cached_editions=sorted(real_cached_editions),
        )
        removed = service.clear_upload_cache_for_target(
            media_type=media_type,
            title=title,
            year=parsed_payload.get("year"),
            tmdb_id=parsed_payload.get("tmdb_id"),
            tvdb_id=parsed_payload.get("tvdb_id"),
            imdb_id=parsed_payload.get("imdb_id"),
        )
        log_info(
            LogTags.UPLOADER,
            "Radarr upgrade: upload cache cleared due to edition removal",
            title=title,
            removed_entries=removed,
        )
        return

    cached_editions = service.get_cached_editions_for_target(
        media_type=media_type,
        title=title,
        year=parsed_payload.get("year"),
        tmdb_id=parsed_payload.get("tmdb_id"),
        tvdb_id=parsed_payload.get("tvdb_id"),
        imdb_id=parsed_payload.get("imdb_id"),
    )
    if edition_hint in cached_editions:
        log_debug(
            LogTags.UPLOADER,
            "Radarr upgrade: edition already cached — no cache invalidation needed",
            title=title,
            edition=edition_hint,
        )
        return

    log_info(
        LogTags.UPLOADER,
        f"Radarr upgrade: new edition '{edition_hint}' not in upload cache — clearing to force re-upload",
        title=title,
        edition_hint=edition_hint,
        cached_editions=sorted(cached_editions),
    )

    removed = service.clear_upload_cache_for_target(
        media_type=media_type,
        title=title,
        year=parsed_payload.get("year"),
        tmdb_id=parsed_payload.get("tmdb_id"),
        tvdb_id=parsed_payload.get("tvdb_id"),
        imdb_id=parsed_payload.get("imdb_id"),
    )
    log_info(
        LogTags.UPLOADER,
        "Radarr upgrade: upload cache cleared for re-upload",
        title=title,
        removed_entries=removed,
    )


def run_plex_webhook_background_job(
    job_id: int,
    parsed_payload: Dict[str, Any],
    remove_overlay_label: bool = False,
    rename_then_upload: bool = False,
    retry_attempts: int = PLEX_WEBHOOK_RETRY_ATTEMPTS,
    retry_delay_seconds: int = PLEX_WEBHOOK_RETRY_DELAY_SECONDS,
) -> None:
    db = SessionLocal()
    handler_id = add_job_log_handler("plex_upload", job_id, "Plex Upload Webhook")
    success = False
    title: str | None = None
    media_type: str | None = None
    season_number: int | None = None

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.UPLOADER, f"Plex webhook job {job_id} not found in database")
            return

        title = parsed_payload.get("title")
        media_type = parsed_payload.get("media_type")
        season_number = parsed_payload.get("season_number")

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            message=format_start_message("webhook upload", qualifier=f"for {media_type}: {title}"),
            progress=5,
        )

        log_section_start(LogTags.UPLOADER, f"Plex Webhook Upload Job {job_id}")
        season_label = f" S{int(season_number):02d}" if isinstance(season_number, int) else ""
        year_label = f" ({int(parsed_payload.get('year'))})" if isinstance(parsed_payload.get("year"), int) else ""
        log_info(
            LogTags.UPLOADER,
            f"Webhook target received: {str(media_type or 'item').upper()} - {title}{year_label}{season_label}",
            media_type=media_type,
            title=title,
            year=parsed_payload.get("year"),
            season_number=season_number,
            tmdb_id=parsed_payload.get("tmdb_id"),
            tvdb_id=parsed_payload.get("tvdb_id"),
            imdb_id=parsed_payload.get("imdb_id"),
            event_type=parsed_payload.get("event_type"),
            source=parsed_payload.get("source"),
        )

        service = PlexUploadService(db)

        # For Radarr upgrade events, use the movieFile path to detect edition changes without
        # relying on Plex scan state (which may lag behind the actual file import).
        if parsed_payload.get("source") == "radarr" and parsed_payload.get("is_upgrade"):
            _handle_radarr_upgrade_edition_check(service, parsed_payload, media_type, title)

        webhook_targets: list[Dict[str, Any]] = [{"season_number": season_number, "label": "primary"}]
        if media_type == "series" and isinstance(season_number, int):
            show_cached = service.is_series_show_poster_cached(
                title=title,
                year=parsed_payload.get("year"),
                tmdb_id=parsed_payload.get("tmdb_id"),
                tvdb_id=parsed_payload.get("tvdb_id"),
                imdb_id=parsed_payload.get("imdb_id"),
            )
            if show_cached:
                webhook_targets = [
                    {"season_number": season_number, "label": f"season-{int(season_number):02d}"},
                ]
                log_info(
                    LogTags.UPLOADER,
                    "Webhook target strategy: show poster cache is present; processing requested season only",
                    media_type=media_type,
                    title=title,
                    season_number=season_number,
                    targets=len(webhook_targets),
                )
            else:
                webhook_targets = [
                    {"season_number": season_number, "label": f"season-{int(season_number):02d}"},
                    {"season_number": None, "label": "series-bootstrap"},
                ]
                log_info(
                    LogTags.UPLOADER,
                    "Webhook target strategy: show poster cache is missing; processing season plus full-series bootstrap",
                    media_type=media_type,
                    title=title,
                    season_number=season_number,
                    targets=len(webhook_targets),
                )

        target_cache_checks: list[Dict[str, Any]] = []
        for target in webhook_targets:
            target_season_number = target.get("season_number")
            cached = service.is_single_target_fully_cached(
                media_type=media_type,
                title=title,
                year=parsed_payload.get("year"),
                season_number=target_season_number,
                tmdb_id=parsed_payload.get("tmdb_id"),
                tvdb_id=parsed_payload.get("tvdb_id"),
                imdb_id=parsed_payload.get("imdb_id"),
            )
            target_cache_checks.append(
                {
                    "season_number": target_season_number,
                    "label": target.get("label") or "target",
                    "cached": cached,
                }
            )

        for check in target_cache_checks:
            log_debug(
                LogTags.UPLOADER,
                "Webhook cache check",
                media_type=media_type,
                title=title,
                target=check.get("label"),
                season_number=check.get("season_number"),
                cached=bool(check.get("cached", False)),
            )

        if target_cache_checks and all(bool(check.get("cached", False)) for check in target_cache_checks):
            _increment_webhook_stat(db, "skipped_cached")
            update_job_state(
                db,
                job,
                status=JOB_STATUS_COMPLETED,
                progress=100,
                message=format_complete_message(
                    "Webhook skipped (already uploaded)",
                    f"{title} already uploaded for matched Plex libraries",
                ),
                completed_at=datetime.now(timezone.utc),
            )

            log_info(
                LogTags.UPLOADER,
                "Webhook cache gate: target already uploaded for matched libraries; skipping pre-upload prep and upload",
                media_type=media_type,
                title=title,
                season_number=season_number,
                targets_checked=len(target_cache_checks),
            )
            log_section_end(LogTags.UPLOADER, "Plex Webhook Upload Complete")
            success = True
            return

        destination_presence_checks = _build_destination_presence_checks(webhook_targets, service, media_type, title, parsed_payload)

        for check in destination_presence_checks:
            log_debug(
                LogTags.UPLOADER,
                "Webhook destination asset check",
                media_type=media_type,
                title=title,
                target=check.get("label"),
                season_number=check.get("season_number"),
                exists=bool(check.get("exists", False)),
            )

        destination_has_any_target = any(bool(check.get("exists", False)) for check in destination_presence_checks)

        if not destination_has_any_target and rename_then_upload:
            rename_summary = _run_webhook_preupload_rename_pass(db, job, parsed_payload, dry_run=False)
            service.invalidate_local_assets_cache()

            rename_info = rename_summary.get("rename", {}) if isinstance(rename_summary, dict) else {}
            rename_matched = int(rename_info.get("matched", 0)) if isinstance(rename_info, dict) else 0
            if rename_matched <= 0:
                log_warning(
                    LogTags.UPLOADER,
                    "Webhook skipped upload passes: rename pre-upload found no matching source assets; skipping destination re-check",
                    media_type=media_type,
                    title=title,
                    season_number=season_number,
                    rename_attempted=bool(rename_info.get("attempted", False)) if isinstance(rename_info, dict) else False,
                    rename_matched=rename_matched,
                )
                _complete_no_local_assets_warning(db, job, job_id, media_type, title, season_number)
                success = True
                return

            destination_presence_checks = _build_destination_presence_checks(webhook_targets, service, media_type, title, parsed_payload)

            destination_has_any_target = any(bool(check.get("exists", False)) for check in destination_presence_checks)

            if not destination_has_any_target:
                log_warning(
                    LogTags.UPLOADER,
                    "Webhook skipped upload passes: target posters not present in destination after rename pre-upload",
                    media_type=media_type,
                    title=title,
                    season_number=season_number,
                    rename_attempted=bool(rename_summary.get("rename", {}).get("attempted", False)),
                    rename_matched=int(rename_summary.get("rename", {}).get("matched", 0)),
                )
                _complete_no_local_assets_warning(db, job, job_id, media_type, title, season_number)
                success = True
                return

        if not destination_has_any_target and not rename_then_upload:
            log_warning(
                LogTags.UPLOADER,
                "Webhook skipped upload passes: target posters not present in destination and rename pre-upload is disabled",
                media_type=media_type,
                title=title,
                season_number=season_number,
            )
            _complete_no_local_assets_warning(db, job, job_id, media_type, title, season_number)
            success = True
            return

        attempts = _coerce_webhook_retry_int(
            retry_attempts,
            default=PLEX_WEBHOOK_RETRY_ATTEMPTS,
            minimum=1,
            maximum=PLEX_WEBHOOK_RETRY_ATTEMPTS_MAX,
        )
        delay_seconds = _coerce_webhook_retry_int(
            retry_delay_seconds,
            default=PLEX_WEBHOOK_RETRY_DELAY_SECONDS,
            minimum=1,
            maximum=PLEX_WEBHOOK_RETRY_DELAY_SECONDS_MAX,
        )

        result = None
        saw_retryable_preflight_failure = False
        saw_non_preflight_processing = False

        def _is_retryable_preflight_error(error_text: str) -> bool:
            normalized = str(error_text or "").strip().lower()
            if not normalized:
                return False
            return (
                normalized == str(PlexUploadService.ERROR_INDEX_BUILD_FAILED).strip().lower()
                or "failed to connect to plex instance" in normalized
            )

        for attempt in range(1, attempts + 1):
            _invalidate_retry_caches(service)

            if attempt > 1:
                retry_progress = min(5 + (attempt * 10), 40)
                update_job_state(
                    db,
                    job,
                    status=JOB_STATUS_RUNNING,
                    message=format_start_message(
                        "webhook upload",
                        qualifier=f"(retry {attempt}/{attempts}) for {media_type}: {title}",
                    ),
                    progress=max(int(job.progress or 0), retry_progress),
                )

            callback_start = max(45, int(job.progress or 0))
            callback_end = 90
            last_progress = int(job.progress or 0)

            def progress_callback(processed: int, total: int, stats: Dict[str, int], _message: str) -> None:
                nonlocal last_progress
                normalized_total, next_progress = _calculate_scaled_progress(
                    processed=processed,
                    total=total,
                    progress_start=callback_start,
                    progress_end=callback_end,
                    last_progress=last_progress,
                )
                if next_progress == last_progress and processed < normalized_total:
                    return

                update_job_state(
                    db,
                    job,
                    progress=next_progress,
                    message=(
                        f"Webhook upload: {processed}/{normalized_total} processed for {media_type}: {title} | "
                        f"matched={int(stats.get('matched', 0))}, "
                        f"uploaded={int(stats.get('uploaded', 0))}, "
                        f"skipped={int(stats.get('skipped', 0))}, "
                        f"errors={int(stats.get('errors', 0))}"
                    ),
                )
                last_progress = next_progress

            aggregated_stats: Dict[str, int] = {
                "scanned": 0,
                "matched": 0,
                "uploaded": 0,
                "candidate_matches_raw": 0,
                "candidate_matches_unique": 0,
                "skipped": 0,
                "errors": 0,
            }

            for target in webhook_targets:
                target_season_number = target.get("season_number")
                target_label = str(target.get("label") or "target")
                log_info(
                    LogTags.UPLOADER,
                    "Webhook upload pass start",
                    attempt=attempt,
                    max_attempts=attempts,
                    media_type=media_type,
                    title=title,
                    target=target_label,
                    season_number=target_season_number,
                )

                target_result = service.run_single_upload(
                    media_type=media_type,
                    title=title,
                    year=parsed_payload.get("year"),
                    season_number=target_season_number,
                    tmdb_id=parsed_payload.get("tmdb_id"),
                    tvdb_id=parsed_payload.get("tvdb_id"),
                    imdb_id=parsed_payload.get("imdb_id"),
                    dry_run=False,
                    remove_overlay_label=remove_overlay_label,
                    progress_callback=progress_callback,
                )

                if not target_result.get("success", False):
                    target_error = str(target_result.get("error", "Plex webhook upload failed"))
                    if _is_retryable_preflight_error(target_error) and attempt < attempts:
                        saw_retryable_preflight_failure = True
                        log_warning(
                            LogTags.UPLOADER,
                            f"Webhook preflight failed for target; retrying in {delay_seconds}s",
                            attempt=attempt,
                            max_attempts=attempts,
                            title=title,
                            media_type=media_type,
                            target=target_label,
                            season_number=target_season_number,
                            error=target_error,
                        )
                        log_info(
                            LogTags.UPLOADER,
                            "Webhook attempt skipped matching/upload due to Plex connectivity preflight failure",
                            attempt=attempt,
                            max_attempts=attempts,
                            title=title,
                            media_type=media_type,
                            target=target_label,
                            season_number=target_season_number,
                            preflight_connectivity_failed=True,
                            matching_skipped=True,
                        )
                        time.sleep(delay_seconds)
                        result = {
                            "success": False,
                            "error": target_error,
                            "retry_scheduled": True,
                        }
                        break
                    raise Exception(target_error)

                target_stats = target_result.get("stats", {})
                saw_non_preflight_processing = True
                log_info(
                    LogTags.UPLOADER,
                    "Webhook upload pass complete",
                    attempt=attempt,
                    max_attempts=attempts,
                    media_type=media_type,
                    title=title,
                    target=target_label,
                    season_number=target_season_number,
                    scanned=int(target_stats.get("scanned", 0)),
                    matched=int(target_stats.get("matched", 0)),
                    uploaded=int(target_stats.get("uploaded", 0)),
                    skipped=int(target_stats.get("skipped", 0)),
                    errors=int(target_stats.get("errors", 0)),
                )
                for key in aggregated_stats:
                    aggregated_stats[key] += int(target_stats.get(key, 0))

            if result and result.get("retry_scheduled"):
                continue

            result = {
                "success": True,
                "stats": aggregated_stats,
            }

            if not result.get("success", False):
                raise Exception(result.get("error", "Plex webhook upload failed"))

            stats = result.get("stats", {})
            scanned_count = int(stats.get("scanned", 0))
            matched_count = int(stats.get("matched", 0))
            uploaded_count = int(stats.get("uploaded", 0))

            if scanned_count == 0:
                no_local_assets_warning = _build_no_local_assets_warning_message(media_type, title)
                log_warning(
                    LogTags.UPLOADER,
                    "Webhook skipped: no local assets found for target after pre-upload steps",
                    attempt=attempt,
                    max_attempts=attempts,
                    title=title,
                    media_type=media_type,
                    season_number=season_number,
                    warning=no_local_assets_warning,
                )
                _complete_no_local_assets_warning(db, job, job_id, media_type, title, season_number)
                success = True
                return

            if uploaded_count > 0:
                break

            if matched_count > 0:
                log_info(
                    LogTags.UPLOADER,
                    "Webhook matched Plex target with no upload; treating as already up-to-date",
                    attempt=attempt,
                    max_attempts=attempts,
                    title=title,
                    media_type=media_type,
                    season_number=season_number,
                    matched=matched_count,
                    uploaded=uploaded_count,
                )
                break

            if attempt < attempts:
                log_info(
                    LogTags.UPLOADER,
                    f"Webhook upload had no Plex match yet, retrying in {delay_seconds}s",
                    attempt=attempt,
                    max_attempts=attempts,
                    title=title,
                    media_type=media_type,
                )
                time.sleep(delay_seconds)

        if not result.get("success", False):
            raise Exception(result.get("error", "Plex webhook upload failed"))

        stats = result.get("stats", {})
        uploaded_count = int(stats.get("uploaded", 0))
        matched_count = int(stats.get("matched", 0))
        scanned_count = int(stats.get("scanned", 0))
        raw_candidates = int(stats.get("candidate_matches_raw", 0))
        unique_candidates = int(stats.get("candidate_matches_unique", 0))

        if matched_count == 0 and uploaded_count == 0:
            if saw_retryable_preflight_failure and not saw_non_preflight_processing:
                log_error(
                    LogTags.UPLOADER,
                    "Webhook failed after retries: Plex connectivity preflight failed on all attempts; matching never ran",
                    attempts=attempts,
                    media_type=media_type,
                    title=title,
                    season_number=season_number,
                    preflight_connectivity_failed=True,
                    matching_skipped=True,
                )
            else:
                log_warning(
                    LogTags.UPLOADER,
                    "Webhook failed after retries: Plex reachable but no matches were found",
                    attempts=attempts,
                    media_type=media_type,
                    title=title,
                    season_number=season_number,
                    preflight_connectivity_failed=False,
                    matching_skipped=False,
                    matched=matched_count,
                    uploaded=uploaded_count,
                )
            raise Exception(
                f"No Plex match found after {attempts} attempt(s) for {media_type}: {title}"
            )

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=(
                format_complete_message(
                    "Webhook Plex upload",
                    f"{title} ({uploaded_count:,} uploaded, {matched_count:,}/{scanned_count:,} matched, candidates {unique_candidates:,}/{raw_candidates:,} unique/raw)",
                )
                if uploaded_count > 0
                else format_complete_message(
                    "Webhook matched Plex item but performed no upload",
                    f"{title} ({uploaded_count:,} uploaded, {matched_count:,}/{scanned_count:,} matched, candidates {unique_candidates:,}/{raw_candidates:,} unique/raw)",
                )
            ),
            completed_at=datetime.now(timezone.utc),
        )

        if matched_count > 0 and uploaded_count == 0:
            log_warning(
                LogTags.UPLOADER,
                "Webhook matched Plex item but performed no upload",
                media_type=media_type,
                title=title,
                season_number=season_number,
                matched=matched_count,
                scanned=scanned_count,
            )

        log_success(
            LogTags.UPLOADER,
            f"Webhook Plex upload completed for {str(media_type or 'item').upper()} - {title}",
            media_type=media_type,
            title=title,
            season_number=season_number,
            remove_overlay_label=remove_overlay_label,
            scanned=scanned_count,
            matched=matched_count,
            uploaded=uploaded_count,
            candidates_raw=raw_candidates,
            candidates_unique=unique_candidates,
            skipped=int(stats.get("skipped", 0)),
            errors=int(stats.get("errors", 0)),
        )

        season_text = f" Season {int(season_number):02d}" if isinstance(season_number, int) else ""
        media_label = str(media_type or "item").strip().lower()
        pretty_media = "Movie" if media_label == "movie" else "Show" if media_label == "show" else "Season" if media_label == "season" else media_label.title()

        if uploaded_count > 0:
            send_discord_notification(
                db,
                feature_key="plex_upload",
                event_type="success",
                title="Plex Upload",
                description=f"Uploaded {pretty_media}: {title}{season_text}",
                fields=[
                    {"name": "Media", "value": pretty_media, "inline": True},
                    {"name": "Title", "value": str(title), "inline": True},
                    {"name": "Uploaded", "value": str(uploaded_count), "inline": True},
                ],
                color=0x4CAF50,
            )

        log_section_end(LogTags.UPLOADER, "Plex Webhook Upload Complete")
        success = True

    except Exception as e:
        log_error(LogTags.UPLOADER, f"Plex webhook upload failed: {e}\n{traceback.format_exc()}")
        send_discord_notification(
            db,
            feature_key="plex_upload",
            event_type="error",
            title="Plex Upload Failed",
            description=str(e),
            fields=[
                {"name": "Media", "value": str(media_type or "unknown"), "inline": True},
                {"name": "Title", "value": str(title or "unknown"), "inline": True},
                {"name": "Job ID", "value": str(job_id), "inline": True},
            ],
            color=0xF44336,
        )
        send_major_error_notification(
            db,
            source="plex_upload.webhook",
            message=str(e),
            job_id=job_id,
        )
        _mark_job_failed(
            db,
            job_id=job_id,
            completion_label="Plex webhook upload failed",
            error_message=str(e),
            failure_context="plex webhook",
        )
    finally:
        remove_job_log_handler(handler_id, "plex_upload", success=success)
        db.close()


def run_plex_single_manual_background_job(
    job_id: int,
    payload: Dict[str, Any],
) -> None:
    db = SessionLocal()
    handler_id = add_job_log_handler("plex_upload", job_id, "Plex Upload Single")
    success = False

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.UPLOADER, f"Plex single upload job {job_id} not found in database")
            return

        title = str(payload.get("title") or "").strip()
        media_type = str(payload.get("media_type") or "").strip().lower()
        season_number = payload.get("season_number")
        dry_run = bool(payload.get("dry_run", False))
        reapply = bool(payload.get("reapply", False))
        remove_overlay_label = bool(payload.get("remove_overlay_label", False))

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            message=format_start_message("manual single upload", qualifier=f"for {media_type}: {title}"),
            progress=8,
        )

        log_section_start(LogTags.UPLOADER, f"Plex Single Upload Job {job_id}")

        preupload_payload = {
            "media_type": media_type,
            "title": title,
            "year": payload.get("year"),
            "season_number": season_number,
            "tmdb_id": payload.get("tmdb_id"),
            "tvdb_id": payload.get("tvdb_id"),
            "imdb_id": payload.get("imdb_id"),
        }
        preupload_summary = _run_webhook_preupload_rename_pass(db, job, preupload_payload, dry_run=dry_run)
        if not isinstance(preupload_summary, dict):
            preupload_summary = {}

        service = PlexUploadService(db)
        callback_start = max(35, int(job.progress or 0))
        callback_end = 95
        last_progress = int(job.progress or 0)

        def progress_callback(processed: int, total: int, stats: Dict[str, int], _message: str) -> None:
            nonlocal last_progress
            normalized_total, next_progress = _calculate_scaled_progress(
                processed=processed,
                total=total,
                progress_start=callback_start,
                progress_end=callback_end,
                last_progress=last_progress,
            )
            if next_progress == last_progress and processed < normalized_total:
                return

            upload_label = "would_upload" if dry_run else "uploaded"
            update_job_state(
                db,
                job,
                progress=next_progress,
                message=(
                    f"Manual single upload: {processed}/{normalized_total} processed for {media_type}: {title} | "
                    f"matched={int(stats.get('matched', 0))}, "
                    f"{upload_label}={int(stats.get(upload_label, 0))}, "
                    f"skipped={int(stats.get('skipped', 0))}, "
                    f"errors={int(stats.get('errors', 0))}"
                ),
            )
            last_progress = next_progress

        result = service.run_single_upload(
            media_type=media_type,
            title=title,
            year=payload.get("year"),
            season_number=season_number,
            tmdb_id=payload.get("tmdb_id"),
            tvdb_id=payload.get("tvdb_id"),
            imdb_id=payload.get("imdb_id"),
            dry_run=dry_run,
            reapply=reapply,
            remove_overlay_label=remove_overlay_label,
            progress_callback=progress_callback,
        )

        if not result.get("success", False):
            raise Exception(result.get("error", "Plex single upload failed"))

        stats = result.get("stats", {})
        uploaded_label = "would_upload" if dry_run else "uploaded"
        uploaded_count = int(stats.get(uploaded_label, 0))
        matched_count = int(stats.get("matched", 0))
        scanned_count = int(stats.get("scanned", 0))
        raw_candidates = int(stats.get("candidate_matches_raw", 0))
        unique_candidates = int(stats.get("candidate_matches_unique", 0))
        preupload_rename = preupload_summary.get("rename", {}) if isinstance(preupload_summary.get("rename", {}), dict) else {}
        preupload_border = preupload_summary.get("border", {}) if isinstance(preupload_summary.get("border", {}), dict) else {}

        preupload_details = ""
        if dry_run:
            rename_matched = int(preupload_rename.get("matched", 0))
            if bool(preupload_border.get("enabled", False)):
                border_changed = int(preupload_border.get("changed", 0))
                preupload_details = f", prep: rename matched {rename_matched:,}, border would_change {border_changed:,}"
            else:
                preupload_details = f", prep: rename matched {rename_matched:,}, border disabled"

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message(
                "Manual single Plex upload",
                f"{title} ({uploaded_count:,} {'would upload' if dry_run else 'uploaded'}, {matched_count:,}/{scanned_count:,} matched, candidates {unique_candidates:,}/{raw_candidates:,} unique/raw{preupload_details})",
            ),
            completed_at=datetime.now(timezone.utc),
        )

        log_success(
            LogTags.UPLOADER,
            "Manual single Plex upload completed",
            media_type=media_type,
            title=title,
            season_number=season_number,
            dry_run=dry_run,
            remove_overlay_label=remove_overlay_label,
            scanned=scanned_count,
            matched=matched_count,
            uploaded=uploaded_count,
            candidates_raw=raw_candidates,
            candidates_unique=unique_candidates,
            skipped=int(stats.get("skipped", 0)),
            errors=int(stats.get("errors", 0)),
            preupload_rename_matched=int(preupload_rename.get("matched", 0)),
            preupload_border_changed=int(preupload_border.get("changed", 0)),
            preupload_border_enabled=bool(preupload_border.get("enabled", False)),
        )
        log_section_end(LogTags.UPLOADER, "Plex Single Upload Complete")
        success = True

    except Exception as e:
        log_error(LogTags.UPLOADER, f"Plex single upload failed: {e}\n{traceback.format_exc()}")
        _mark_job_failed(
            db,
            job_id=job_id,
            completion_label="Plex single upload failed",
            error_message=str(e),
            failure_context="plex single upload",
        )
    finally:
        remove_job_log_handler(handler_id, "plex_upload", success=success)
        db.close()

__all__ = [
    "run_plex_upload_background_job",
    "run_plex_webhook_background_job",
    "run_plex_single_manual_background_job",
]
