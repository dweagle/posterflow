"""Background job wrapper for the single-item match report.

The build pulls the library, probes TMDB/TVDB and the media servers, and rescans every
priority drive, which runs for minutes on a large library. As a plain request that dies
at any reverse proxy's timeout, so the modal queues this job and polls it instead.
"""
import traceback
from datetime import datetime, timezone
from typing import Any, Dict

from database import SessionLocal
from core.job_cancel import JobCancelled, check_cancelled
from core.logging import LogTags, log_error, log_info, log_user_action
from models.job import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_RUNNING,
    Job,
    finalize_job_cancelled,
    mark_job_failed,
    update_job_state,
)
from services.match_report import build_match_report, render_match_report_text, report_filename
from services.match_report_store import store_report


def item_label(item: Dict[str, Any]) -> str:
    title = str(item.get("title") or "item")
    return f"{title} ({item['year']})" if item.get("year") else title


def run_match_report_background_job(job_id: int, item: Dict[str, Any]) -> None:
    db = SessionLocal()
    label = item_label(item)
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.UNMATCHED, f"Match report job {job_id} not found in database")
            return

        update_job_state(db, job, status=JOB_STATUS_RUNNING, message=f"Building match report for {label}", progress=0)

        def progress(message: str, percent: int) -> None:
            check_cancelled(job_id)
            update_job_state(db, job, message=message, progress=max(0, min(99, int(percent))))

        report = build_match_report(db, item, progress=progress)
        store_report(job_id, {
            "report": report,
            "report_text": render_match_report_text(report),
            "filename": report_filename(item),
        })
        log_user_action(f"Generated match report for '{item.get('title')}'")
        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=f"Match report ready for {label}",
            completed_at=datetime.now(timezone.utc),
        )
    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_info(LogTags.UNMATCHED, f"Match report for {label} stopped by user (job_id={job_id})")
        raise
    except Exception as exc:
        log_error(
            LogTags.UNMATCHED,
            f"Error building match report for {label}: {exc}\n{traceback.format_exc()}",
            job_id=job_id,
            error=str(exc),
        )
        db.rollback()
        try:
            mark_job_failed(db, job_id, exc)
        except Exception as commit_error:
            log_error(LogTags.UNMATCHED, f"Failed to update match report job status: {commit_error}")
            db.rollback()
    finally:
        db.close()
