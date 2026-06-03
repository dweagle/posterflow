import json
import os
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
    log_section_start,
    log_section_end,
    add_job_log_handler,
    remove_job_log_handler,
)
from models.setting import get_setting_value
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    format_start_message,
    format_complete_message,
    mark_job_failed,
    update_job_state,
)
from core.hooks import run_post_job_hook, HOOK_KEY_BORDER


def _build_progress_callback(
    db: Session,
    job: Job,
) -> Callable[[str, int, int, str], None]:
    """Create a standard progress callback for border processing jobs."""
    def border_progress(phase: str, current: int, total: int, message: str) -> None:
        if total > 0:
            progress = int((current / total) * 100)
            target_progress = min(max(progress, 0), 99)
            current_progress = int(job.progress or 0)
            next_progress = max(current_progress, target_progress)
            current_message = str(job.message or "")

            if next_progress != current_progress or message != current_message:
                update_job_state(db, job, progress=next_progress, message=message)

    return border_progress


def run_border_replacer_background_job(job_id: int, dry_run: bool = False, mode: str = "full", triggered_by: str = "manual") -> None:
    """
    Execute border replacer in a background thread.
    Shared orchestration used by poster manager entrypoints.

    Args:
        job_id: The job ID to track
        dry_run: Whether to run in dry-run mode
        mode: "full" or "incremental" - full processes all, incremental only changed
    """
    db = SessionLocal()

    handler_id = add_job_log_handler("border_replacer", job_id, "Border Replacer")
    success = False

    try:
        from services.border_replacer import BorderReplacerService

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.BORDER_REPLACER, f"Job {job_id} not found in database")
            return

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            message=format_start_message("border replacer", dry_run=dry_run),
        )

        log_section_start(LogTags.BORDER_REPLACER, f"Background Border Replacer Job {job_id}")

        border_colors_value = get_setting_value(db, "border_replacer_colors")
        border_width_value = get_setting_value(db, "border_replacer_width")
        remove_borders_value = get_setting_value(db, "border_replacer_remove_borders", "false")

        border_colors = []
        if border_colors_value:
            try:
                border_colors = json.loads(border_colors_value)
            except json.JSONDecodeError:
                log_warning(LogTags.BORDER_REPLACER, "Failed to parse border colors, using empty list")
                border_colors = []

        border_width = 26
        if border_width_value:
            try:
                border_width = int(border_width_value)
            except ValueError:
                log_warning(LogTags.BORDER_REPLACER, f"Invalid border width '{border_width_value}', using default 26")

        remove_borders = str(remove_borders_value).lower() == "true"

        season_mode = get_setting_value(db, "border_replacer_season_mode", "inherit")
        if season_mode not in ("inherit", "remove", "colors"):
            season_mode = "inherit"

        season_colors = []
        season_colors_value = get_setting_value(db, "border_replacer_season_colors")
        if season_colors_value:
            try:
                season_colors = json.loads(season_colors_value)
            except json.JSONDecodeError:
                log_warning(LogTags.BORDER_REPLACER, "Failed to parse season border colors, using empty list")
                season_colors = []

        exclusions_value = get_setting_value(db, "border_replacer_exclusions")
        exclusions = []
        if exclusions_value:
            try:
                exclusions = json.loads(exclusions_value)
            except json.JSONDecodeError:
                log_warning(LogTags.BORDER_REPLACER, "Failed to parse exclusions, using empty list")

        poster_dest = get_setting_value(db, "poster_destination")
        if not poster_dest:
            raise Exception("No destination directory configured. Please configure in Poster Manager settings.")

        if not os.path.exists(poster_dest):
            raise Exception(f"Poster destination directory does not exist: {poster_dest}")

        tmp_dir = os.path.join(poster_dest, "tmp")

        if not os.path.exists(tmp_dir) or not os.listdir(tmp_dir):
            raise Exception(
                f"tmp/ folder not found or empty: {tmp_dir}\n"
                "Border replacer requires the tmp/ folder created by Poster Renamer.\n"
                "Please run Poster Renamer first, or use the full Workflow."
            )

        source_dir = tmp_dir
        destination_dir = poster_dest

        log_info(LogTags.BORDER_REPLACER, "Found tmp/ folder - ready to process")
        log_info(LogTags.BORDER_REPLACER, f"Pattern: tmp/ → {poster_dest} (root)")

        mode_label = "DRY RUN" if dry_run else "PROCESSING"
        action = "Remove borders" if remove_borders else "Add borders"
        log_info(LogTags.BORDER_REPLACER, f"Standalone: {action} ({mode_label})")
        if not remove_borders:
            log_info(LogTags.BORDER_REPLACER, f"Colors: {len(border_colors)}, Width: {border_width}px")
        if season_mode != "inherit":
            season_desc = "remove" if season_mode == "remove" else f"custom colors ({len(season_colors)})"
            log_info(LogTags.BORDER_REPLACER, f"Season poster mode: {season_desc}")
        if exclusions:
            log_info(LogTags.BORDER_REPLACER, f"Exclusions: {len(exclusions)} title(s)")

        service = BorderReplacerService(db)
        result = service.process_posters(
            source_dir=source_dir,
            destination_dir=destination_dir,
            border_colors=border_colors if border_colors else None,
            remove_borders=remove_borders,
            border_width=border_width,
            exclusion_list=exclusions,
            dry_run=dry_run,
            progress_callback=_build_progress_callback(db, job),
            mode=mode,
            season_mode=season_mode,
            season_border_colors=season_colors if season_colors else None,
        )

        if not result["success"]:
            raise Exception(result.get("error", "Unknown error"))

        log_info(LogTags.BORDER_REPLACER, "Preserving tmp/ folder for next incremental run (if exists)")

        changed_count = result.get("changed", result.get("changed_count", 0))
        skipped_count = result.get("skipped", result.get("skipped_count", 0))
        dry_run_suffix = " (Dry Run - No Changes Made)" if dry_run else ""
        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("Border replacer", f"{changed_count} changed, {skipped_count} skipped{dry_run_suffix}"),
            completed_at=datetime.now(timezone.utc),
        )

        success = True
        log_section_end(LogTags.BORDER_REPLACER, "Background Border Replacer Complete")

    except Exception as e:
        log_error(LogTags.BORDER_REPLACER, f"Background border replacer failed: {e}\n{traceback.format_exc()}")
        try:
            mark_job_failed(db, job_id, e)
        except Exception as commit_error:
            log_error(LogTags.BORDER_REPLACER, f"Failed to update job status: {commit_error}\n{traceback.format_exc()}")
            db.rollback()
    finally:
        run_post_job_hook(HOOK_KEY_BORDER, success=success, triggered_by=triggered_by, db=db)
        remove_job_log_handler(handler_id, "border_replacer", success=success)
        db.close()
