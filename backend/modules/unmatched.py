import json
import traceback
from datetime import datetime, timezone
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
from models.setting import get_setting_value, upsert_setting
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    format_start_message,
    format_complete_message,
    mark_job_failed,
    update_job_state,
)
from services.poster_renamer import PosterRenameService
from services.unmatched_assets import UnmatchedAssetsService
from services.discord_notifications import send_discord_notification, send_major_error_notification
from core.hooks import run_post_job_hook, HOOK_KEY_UNMATCHED


def _build_progress_callback(
    db: Session,
    job_id: int,
) -> Callable[[str, int, int, str], None]:
    """Create a standard progress callback for unmatched detection jobs."""
    def unmatched_progress(phase: str, current: int, total: int, message: str) -> None:
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


def run_unmatched_detection_background_job(job_id: int, skip_discord: bool = False, triggered_by: str = "manual") -> None:
    """
    Execute unmatched detection in a background thread.
    Shared orchestration used by poster manager entrypoints.
    Args:
        skip_discord: When True, suppresses individual Discord notifications (e.g. when called from workflow).
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

        destination_dir = get_setting_value(db, "poster_destination")

        if not destination_dir:
            raise Exception("No destination directory configured")

        from pathlib import Path
        if not Path(destination_dir).exists():
            # Clear stale cached stats so the UI reflects reality (empty/gone folder)
            # instead of showing data from the last successful run
            try:
                unmatched_service = UnmatchedAssetsService(db)
                empty = unmatched_service._empty_result()
                upsert_setting(db, "poster_unmatched_stats", json.dumps(empty))
                db.commit()
                log_warning(LogTags.UNMATCHED, "Cleared stale unmatched stats cache — destination folder no longer exists")
            except Exception as clear_err:
                log_warning(LogTags.UNMATCHED, f"Could not clear stale stats cache: {clear_err}")
            raise Exception(f"Destination directory does not exist: {destination_dir}. Run 'Rename Posters' first to create organized posters.")

        update_job_state(db, job, message="Fetching media from instances...", progress=8)

        source_dirs = [destination_dir]

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
            collections=len(media_dict.get('collections', []))
        )

        update_job_state(db, job, message="Comparing posters to media library...", progress=15)

        log_info(LogTags.UNMATCHED, f"Starting detection scan on: {source_dirs[0]}")
        unmatched_service = UnmatchedAssetsService(db)
        result = unmatched_service.detect_unmatched(
            media_dict,
            source_dirs,
            progress_callback=_build_progress_callback(db, job_id),
        )

        summary = result.get('summary', {})
        grand = summary.get('grand_total', {})
        unmatched_count = grand.get('unmatched', 0)
        total_items = grand.get('total', 0)
        percent = grand.get('percent_complete', 0)

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message(
                "Detection",
                f"{unmatched_count:,}/{total_items:,} items missing posters ({percent:.2f}% complete)",
            ),
            completed_at=datetime.now(timezone.utc),
        )

        success = True
        log_section_end(LogTags.UNMATCHED, "Background Detection Complete")
        log_success(
            LogTags.UNMATCHED,
            "Detection completed successfully",
            job_id=job_id,
            unmatched=unmatched_count,
            total=total_items,
            percent_complete=percent
        )

        movies_missing = int(summary.get("movies", {}).get("unmatched", 0))
        shows_missing = int(summary.get("series", {}).get("unmatched", 0))
        seasons_missing = int(summary.get("seasons", {}).get("unmatched", 0))
        collections_missing = int(summary.get("collections", {}).get("unmatched", 0))

        # Persist stats so workflow notification can include them
        try:
            upsert_setting(db, "unmatched_last_stats", json.dumps({
                "unmatched_count": unmatched_count,
                "movies_missing": movies_missing,
                "shows_missing": shows_missing,
                "seasons_missing": seasons_missing,
                "collections_missing": collections_missing,
                "total_items": total_items,
            }))
            db.commit()
        except Exception:  # nosec B110
            pass

        if not skip_discord:
            send_discord_notification(
                db,
                feature_key="unmatched_assets",
                event_type="info",
                title="Unmatched Assets Summary",
                description=f"{unmatched_count:,} total assets missing posters",
                fields=[
                    {"name": "Total", "value": str(unmatched_count), "inline": True},
                    {"name": "Movies", "value": str(movies_missing), "inline": True},
                    {"name": "Shows", "value": str(shows_missing), "inline": True},
                    {"name": "Seasons", "value": str(seasons_missing), "inline": True},
                    {"name": "Collections", "value": str(collections_missing), "inline": True},
                ],
                color=0x64B5F6,
            )

    except Exception as e:
        error_msg = str(e)
        log_error(
            LogTags.UNMATCHED,
            f"Background detection job failed: {error_msg}\n{traceback.format_exc()}",
            job_id=job_id,
            error=error_msg,
            traceback=traceback.format_exc()
        )
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
