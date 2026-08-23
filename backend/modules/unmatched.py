import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session
from database import SessionLocal
from core.logging import (
    LogTags,
    log_info,
    log_warning,
    log_error,
    log_success,
    log_section_start,
    log_section_end,
    add_job_log_handler,
    remove_job_log_handler,
)
from models.setting import upsert_setting, get_setting_value
from util.poster_settings import get_asset_include, get_poster_destination
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    format_start_message,
    format_complete_message,
    mark_job_failed,
    update_job_state,
    finalize_job_cancelled,
)
from core.job_cancel import JobCancelled, check_cancelled
from services.poster_renamer import PosterRenameService
from services.unmatched_assets import UnmatchedAssetsService
from services.artwork_scan import ARTWORK_TYPES
from services.community_reconcile import reconcile_community_lists
from services.discord_notifications import send_discord_notification, send_major_error_notification
from core.hooks import run_post_job_hook, HOOK_KEY_UNMATCHED


def _build_progress_callback(
    db: Session,
    job_id: int,
) -> Callable[[str, int, int, str], None]:
    """Create a standard progress callback for unmatched detection jobs."""
    def unmatched_progress(phase: str, current: int, total: int, message: str) -> None:
        check_cancelled(job_id)
        try:
            job_obj = db.query(Job).filter(Job.id == job_id).first()
            if job_obj and total > 0:
                progress = int((current / total) * 100)
                target_progress = min(max(progress, 0), 99)
                current_progress = int(job_obj.progress or 0)
                next_progress = max(current_progress, target_progress)
                current_message = str(job_obj.message or "")

                if next_progress != current_progress or message != current_message:
                    update_job_state(db, job_obj, progress=next_progress, message=message)
        except Exception as e:
            log_warning(LogTags.UNMATCHED, f"Error updating progress: {e}")

    return unmatched_progress




def _run_detection(db: Session, job: Job, job_id: int, media_dict: dict, destination_dir: str, skip_discord: bool, *, check_posters: bool, artwork_types: list, asset_folders: bool) -> str:
    """Unmatched detection scoped to the enabled asset types: posters (if check_posters) and the
    given artwork_types. One media fetch feeds both. Poster-specific follow-ups (community
    reconcile, Discord, last-stats) run only when posters were checked. Returns the summary."""
    update_job_state(db, job, message="Comparing library to organized assets...", progress=15)

    log_info(LogTags.UNMATCHED, f"Starting detection scan on: {destination_dir}")
    result = UnmatchedAssetsService(db).detect_unmatched(
        media_dict,
        [destination_dir],
        progress_callback=_build_progress_callback(db, job_id),
        check_posters=check_posters,
        artwork_types=artwork_types,
        asset_folders=asset_folders,
    )

    parts = []
    poster_fields: list = []
    artwork_fields: list = []
    desc_bits: list = []
    stats_payload: dict = {}

    if check_posters:
        summary = result.get('summary', {})
        grand = summary.get('grand_total', {})
        unmatched_count = grand.get('unmatched', 0)
        total_items = grand.get('total', 0)
        percent = grand.get('percent_complete', 0)

        log_success(
            LogTags.UNMATCHED,
            "Detection completed successfully",
            job_id=job_id,
            unmatched=unmatched_count,
            total=total_items,
            percent_complete=percent,
        )

        # Headless cross-sync: now that the unmatched set is fresh, drop any of
        # this user's published community-list items whose posters now exist
        # locally (resolved outside the request/list path). Best-effort.
        reconcile_community_lists(db, {"unmatched"})

        movies_missing = int(summary.get("movies", {}).get("unmatched", 0))
        shows_missing = int(summary.get("series", {}).get("unmatched", 0))
        seasons_missing = int(summary.get("seasons", {}).get("unmatched", 0))
        collections_missing = int(summary.get("collections", {}).get("unmatched", 0))

        stats_payload.update({
            "unmatched_count": unmatched_count,
            "movies_missing": movies_missing,
            "shows_missing": shows_missing,
            "seasons_missing": seasons_missing,
            "collections_missing": collections_missing,
            "total_items": total_items,
        })
        poster_fields = [
            {"name": "Total", "value": str(unmatched_count), "inline": True},
            {"name": "Movies", "value": str(movies_missing), "inline": True},
            {"name": "Shows", "value": str(shows_missing), "inline": True},
            {"name": "Seasons", "value": str(seasons_missing), "inline": True},
            {"name": "Collections", "value": str(collections_missing), "inline": True},
        ]
        desc_bits.append(f"{unmatched_count:,} missing posters")
        parts.append(f"{unmatched_count:,}/{total_items:,} items missing posters ({percent:.2f}% complete)")

    if artwork_types and result.get("artwork"):
        art = result["artwork"]
        total_missing = sum(int(art[t]["summary"]["grand_total"]["unmatched"]) for t in artwork_types)
        log_success(LogTags.UNMATCHED, "Artwork detection completed successfully", job_id=job_id, missing=total_missing)

        artwork_stats: dict = {}
        for t in artwork_types:
            gt = art[t]["summary"]["grand_total"]
            missing = int(gt.get("unmatched", 0))
            total = int(gt.get("total", 0))
            artwork_stats[t] = {"missing": missing, "total": total}
            artwork_fields.append({
                "name": t.capitalize(),
                "value": f"{missing:,} missing of {total:,}",
                "inline": True,
            })
        stats_payload["artwork_missing"] = total_missing
        stats_payload["artwork"] = artwork_stats
        desc_bits.append(f"{total_missing:,} missing artwork")
        parts.append(f"{total_missing:,} items missing artwork ({', '.join(artwork_types)})")

    # Persist stats so the workflow notification can include them. Merge into the existing
    # payload so a single-sided run doesn't wipe the other side's last-known numbers.
    if stats_payload:
        try:
            existing = {}
            raw = get_setting_value(db, "unmatched_last_stats")
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    existing = parsed
            existing.update(stats_payload)
            upsert_setting(db, "unmatched_last_stats", json.dumps(existing))
            db.commit()
        except Exception:  # nosec B110
            pass

    if not skip_discord and (poster_fields or artwork_fields):
        send_discord_notification(
            db,
            feature_key="unmatched_assets",
            event_type="info",
            title="Unmatched Assets Summary",
            description=" · ".join(desc_bits),
            fields=poster_fields + artwork_fields,
            color=0x64B5F6,
        )

    return "; ".join(parts) or "no asset types enabled to check"


def run_unmatched_detection_background_job(
    job_id: int,
    skip_discord: bool = False,
    triggered_by: str = "manual",
) -> None:
    """
    Execute unmatched detection in a background thread — ONE slot-aware pass over a single
    media fetch covers posters AND artwork (logo/background/square), mirroring how the Asset
    Renamer handles posters and artwork together. There is no separate artwork detection.

    Args:
        skip_discord: When True, suppresses individual Discord notifications (e.g. from workflow).
    """
    db = SessionLocal()

    handler_id = add_job_log_handler("unmatched_assets", job_id, "Unmatched Assets Detection")
    success = False

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.UNMATCHED, f"Job {job_id} not found in database")
            return

        update_job_state(db, job, status=JOB_STATUS_RUNNING, message=format_start_message("unmatched detection"), progress=0)
        log_section_start(LogTags.UNMATCHED, f"Background Detection Job {job_id}")

        # Check only the asset types enabled in the Asset Renamer's Include selection.
        include = get_asset_include(db)
        check_posters = "posters" in include
        artwork_types = [t for t in ARTWORK_TYPES if t in include]
        if not check_posters and not artwork_types:
            log_warning(LogTags.UNMATCHED, "Include selection is empty — nothing to detect")
            update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100, message="No asset types selected — nothing to detect")
            success = True
            return
        log_info(LogTags.UNMATCHED, f"Detecting unmatched for: {', '.join(include)}")

        destination_dir = get_poster_destination(db)

        if not Path(destination_dir).exists():
            # Clear stale poster stats so the UI reflects reality (empty/gone folder).
            if check_posters:
                try:
                    empty = UnmatchedAssetsService(db)._empty_result()
                    upsert_setting(db, "poster_unmatched_stats", json.dumps(empty))
                    db.commit()
                    log_warning(LogTags.UNMATCHED, "Cleared stale unmatched stats cache — destination folder no longer exists")
                except Exception as clear_err:
                    log_warning(LogTags.UNMATCHED, f"Could not clear stale stats cache: {clear_err}")
            raise Exception(f"Destination directory does not exist: {destination_dir}. Run 'Rename Posters' first to create organized assets.")

        update_job_state(db, job, message="Fetching media from instances...", progress=8)

        log_info(LogTags.UNMATCHED, "Fetching media library data from Plex/Radarr/Sonarr instances...")
        rename_service = PosterRenameService(db)
        media_dict = rename_service.get_media_from_instances(log_tag=LogTags.UNMATCHED, setting_key="unmatched_assets_libraries")

        if not any(media_dict.values()):
            raise Exception("No media found. Configure media sources in Settings → Media tab.")

        total_media = sum(len(v) for v in media_dict.values())
        log_info(
            LogTags.UNMATCHED,
            f"Fetched {total_media:,} media items from configured instances",
            total=total_media,
            movies=len(media_dict.get('movies', [])),
            series=len(media_dict.get('series', [])),
            collections=len(media_dict.get('collections', [])),
        )

        asset_folders_value = get_setting_value(db, "poster_asset_folders")
        asset_folders = asset_folders_value.lower() == "true" if asset_folders_value else True

        # Detect only the enabled asset types (posters and/or artwork), one media fetch for all.
        summary = _run_detection(db, job, job_id, media_dict, destination_dir, skip_discord, check_posters=check_posters, artwork_types=artwork_types, asset_folders=asset_folders)

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("Detection", summary),
            completed_at=datetime.now(timezone.utc),
        )

        success = True
        log_section_end(LogTags.UNMATCHED, "Background Detection Complete")

    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_info(LogTags.UNMATCHED, f"Unmatched detection stopped by user (job_id={job_id})")
        raise
    except Exception as e:
        error_msg = str(e)
        log_error(
            LogTags.UNMATCHED,
            f"Background detection job failed: {error_msg}\n{traceback.format_exc()}",
            job_id=job_id,
            error=error_msg,
            traceback=traceback.format_exc()
        )
        db.rollback()  # reset a possibly-poisoned session before reusing it
        if not skip_discord:
            send_discord_notification(
                db,
                feature_key="unmatched_assets",
                event_type="error",
                title="Unmatched Detection Failed",
                description=error_msg,
                fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
                color=0xF44336,
            )
            send_major_error_notification(
                db,
                source="unmatched_detection",
                message=error_msg,
                job_id=job_id,
            )
        try:
            mark_job_failed(db, job_id, e)
        except Exception as commit_error:
            log_error(LogTags.UNMATCHED, f"Failed to update job status: {commit_error}\n{traceback.format_exc()}")
            db.rollback()
    finally:
        run_post_job_hook(HOOK_KEY_UNMATCHED, success=success, triggered_by=triggered_by, db=db)
        remove_job_log_handler(handler_id, "unmatched_assets", success=success)
        db.close()
