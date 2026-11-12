import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from sqlalchemy.orm import Session

from database import SessionLocal
from core.logging import (
    LogTags,
    log_info,
    log_error,
    log_section_start,
    log_section_end,
    add_job_log_handler,
    remove_job_log_handler,
)
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
from core.hooks import run_post_job_hook, HOOK_KEY_OVERLAYS


def _build_progress_callback(db: Session, job: Job) -> Callable[[str, int, int, str], None]:
    def progress(phase: str, current: int, total: int, message: str) -> None:
        check_cancelled(job.id)
        if total > 0:
            target = min(max(int((current / total) * 100), 0), 99)
            current_progress = int(job.progress or 0)
            next_progress = max(current_progress, target)
            if next_progress != current_progress or message != str(job.message or ""):
                update_job_state(db, job, progress=next_progress, message=message)
    return progress


def summarize(stats: Dict[str, Any]) -> str:
    return (
        f"{stats['applied']} applied, {stats['refreshed']} refreshed, {stats['restored']} restored, "
        f"{stats['current']} current, {stats['no_poster']} without a Posterflow poster, {stats['failed']} failed"
    )


def run_overlays_background_job(job_id: int, dry_run: bool = False, mode: str = "incremental", triggered_by: str = "manual") -> None:
    """Apply overlays to the posters on the configured media servers. mode "rebuild" re-uploads
    every badge even when nothing changed and asks TMDB again for every title; "restore" puts
    every badged item back to its clean poster instead, whether overlays are on or off."""
    db = SessionLocal()
    handler_id = add_job_log_handler("overlays", job_id, "Overlays")
    success = False

    try:
        from services.overlays.server import build_server_engine

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.OVERLAYS, f"Job {job_id} not found in database")
            return

        update_job_state(db, job, status=JOB_STATUS_RUNNING, message=format_start_message("overlays", dry_run=dry_run))
        log_section_start(LogTags.OVERLAYS, f"Background Overlays Job {job_id}")

        engine = build_server_engine(db)
        if engine is None:
            # Overlays are switched off and nothing is badged: a valid no-op, not a failure
            # (a workflow step left enabled with the switch off lands here every run).
            log_info(LogTags.OVERLAYS, "Overlays are off and no item carries a badge — nothing to do")
            update_job_state(
                db,
                job,
                status=JOB_STATUS_COMPLETED,
                progress=100,
                message=format_complete_message("Overlays", "off, nothing to do"),
                completed_at=datetime.now(timezone.utc),
            )
            success = True
            log_section_end(LogTags.OVERLAYS, "Background Overlays Complete")
            return

        mode = mode if mode in ("full", "incremental", "rebuild", "restore") else "incremental"
        try:
            if mode == "restore":
                stats = engine.restore_all(dry_run=dry_run, progress=_build_progress_callback(db, job), job_id=job_id)
            else:
                stats = engine.run(mode=mode, dry_run=dry_run, progress=_build_progress_callback(db, job), job_id=job_id)
        finally:
            if not dry_run:
                engine.flush()
            else:
                engine.log_summary()

        suffix = " (Dry Run - No Changes Made)" if dry_run else ""
        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("Overlays", f"{summarize(stats)}{suffix}"),
            completed_at=datetime.now(timezone.utc),
        )
        success = True
        log_section_end(LogTags.OVERLAYS, "Background Overlays Complete")

    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_section_end(LogTags.OVERLAYS, "Background Overlays Stopped")
        raise
    except Exception as e:
        log_error(LogTags.OVERLAYS, f"Background overlays job failed: {e}\n{traceback.format_exc()}")
        db.rollback()
        try:
            mark_job_failed(db, job_id, e)
        except Exception as commit_error:
            log_error(LogTags.OVERLAYS, f"Failed to update job status: {commit_error}\n{traceback.format_exc()}")
            db.rollback()
    finally:
        run_post_job_hook(HOOK_KEY_OVERLAYS, success=success, triggered_by=triggered_by, db=db)
        remove_job_log_handler(handler_id, "overlays", success=success)
        db.close()
