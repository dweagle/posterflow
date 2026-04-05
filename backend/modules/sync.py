import traceback
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session
from database import SessionLocal
from models.job import (
    Job,
    JOB_TYPE_GDRIVE_SYNC,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    mark_job_failed,
    update_job_state,
)
from models.drive import Drive
from services.poster_sync import PosterSyncService
from core.logging import (
    LogTags,
    log_info,
    log_debug,
    log_warning,
    log_error,
    add_job_log_handler,
    remove_job_log_handler,
)
from services.discord_notifications import send_discord_notification, send_major_error_notification
from core.hooks import run_post_job_hook, HOOK_KEY_SYNC_ONE, HOOK_KEY_SYNC_ALL


def _build_progress_callback(
    db: Session,
    job_id: int,
    log_tag: str,
) -> Callable[[str, int, int, str], None]:
    """Create a standard progress callback for sync jobs."""
    def sync_progress(phase: str, current: int, total: int, message: str) -> None:
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job and total > 0:
                progress = int((current / total) * 100)
                job.progress = min(progress, 99)
                job.message = message
                db.commit()
        except Exception as e:
            log_warning(log_tag, f"Error updating progress: {e}")

    return sync_progress


def run_sync_one_job(drive_id: int, job_id: int, triggered_by: str = "manual") -> dict[str, Any]:
    """
    Sync a single drive.
    Shared orchestration used by both manual API jobs and scheduler-triggered jobs.
    """
    handler_id = add_job_log_handler("sync_one", job_id)
    success = False

    db = SessionLocal()
    try:
        drive = db.query(Drive).filter(Drive.id == drive_id).first()
        drive_name = drive.name if drive else f"ID:{drive_id}"

        log_debug(LogTags.SCHEDULER, f"Starting sync: '{drive_name}' (job_id={job_id})")

        service = PosterSyncService(db)
        result = service.sync_drive(
            drive_id,
            job_id,
            progress_callback=_build_progress_callback(db, job_id, LogTags.SCHEDULER),
        )

        if result.get('success'):
            success = True
            log_debug(LogTags.SCHEDULER, f"Completed '{drive_name}'")
            send_discord_notification(
                db,
                feature_key="sync",
                event_type="success",
                title="Google Drive Sync Summary",
                description=f"{drive_name}",
                fields=[
                    {"name": "New", "value": str(int(result.get("added", 0))), "inline": True},
                    {"name": "Replaced", "value": str(int(result.get("updated", 0))), "inline": True},
                    {"name": "Deleted", "value": str(int(result.get("deleted", 0))), "inline": True},
                ],
                color=0x4CAF50,
            )
        else:
            log_warning(LogTags.SCHEDULER, f"Failed '{drive_name}'")
            error_message = str(result.get("error") or result.get("message") or "Sync failed")
            send_discord_notification(
                db,
                feature_key="sync",
                event_type="error",
                title="Google Drive Sync Failed",
                description=error_message,
                fields=[
                    {"name": "Drive", "value": drive_name, "inline": True},
                    {"name": "Job ID", "value": str(job_id), "inline": True},
                ],
                color=0xF44336,
            )
            send_major_error_notification(
                db,
                source="sync.one",
                message=error_message,
                job_id=job_id,
            )

        return result
    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Sync job failed: {str(e)}\n{traceback.format_exc()}")
        send_discord_notification(
            db,
            feature_key="sync",
            event_type="error",
            title="Google Drive Sync Failed",
            description=str(e),
            fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
            color=0xF44336,
        )
        send_major_error_notification(
            db,
            source="sync.one",
            message=str(e),
            job_id=job_id,
        )

        mark_job_failed(db, job_id, e)

        raise
    finally:
        remove_job_log_handler(handler_id)
        run_post_job_hook(HOOK_KEY_SYNC_ONE, success=success, triggered_by=triggered_by, db=db)
        db.close()


def run_sync_all_job(job_id: int, skip_discord: bool = False, triggered_by: str = "manual") -> dict[str, Any]:
    """
    Sync all subscribed drives.
    Shared orchestration used by both manual API jobs and scheduler-triggered jobs.
    Args:
        skip_discord: When True, suppresses individual Discord notifications (e.g. when called from workflow).
    """
    handler_id = add_job_log_handler("sync_all", job_id)
    success = False

    db = SessionLocal()
    try:
        log_debug(LogTags.SCHEDULER, f"Starting sync for all drives (job_id={job_id})")

        drives = db.query(Drive).filter(Drive.subscribed == True).all()

        if not drives:
            log_info(LogTags.SCHEDULER, "No subscribed drives to sync")
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100, message="No subscribed drives found")
            return {"success": True, "message": "No subscribed drives"}

        drive_ids = [drive.id for drive in drives]
        log_debug(LogTags.SCHEDULER, f"Syncing {len(drive_ids)} subscribed drives")

        sync_service = PosterSyncService(db)
        result = sync_service.sync_multiple_drives(
            drive_ids,
            job_id,
            max_workers=1,
            progress_callback=_build_progress_callback(db, job_id, LogTags.SCHEDULER),
        )

        if result.get('success'):
            success = True
            log_debug(LogTags.SCHEDULER, f"Completed sync: {result.get('message', 'Done')}")
            if not skip_discord:
                send_discord_notification(
                    db,
                    feature_key="sync",
                    event_type="success",
                    title="Google Drive Sync Summary",
                    description=f"{int(result.get('drives_synced', 0))} drive(s) processed",
                    fields=[
                        {"name": "New", "value": str(int(result.get("added", 0))), "inline": True},
                        {"name": "Replaced", "value": str(int(result.get("updated", 0))), "inline": True},
                        {"name": "Deleted", "value": str(int(result.get("deleted", 0))), "inline": True},
                    ],
                    color=0x4CAF50,
                )
        else:
            log_warning(LogTags.SCHEDULER, f"Sync failed: {result.get('message', 'Error')}")
            error_message = str(result.get("error") or result.get("message") or "Sync failed")
            if not skip_discord:
                send_discord_notification(
                    db,
                    feature_key="sync",
                    event_type="error",
                    title="Google Drive Sync Failed",
                    description=error_message,
                    fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
                    color=0xF44336,
                )
                send_major_error_notification(
                    db,
                    source="sync.all",
                    message=error_message,
                    job_id=job_id,
                )

        return result
    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Sync job failed: {str(e)}\n{traceback.format_exc()}")
        if not skip_discord:
            send_discord_notification(
                db,
                feature_key="sync",
                event_type="error",
                title="Google Drive Sync Failed",
                description=str(e),
                fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
                color=0xF44336,
            )
            send_major_error_notification(
                db,
                source="sync.all",
                message=str(e),
                job_id=job_id,
            )

        mark_job_failed(db, job_id, e)

        raise
    finally:
        remove_job_log_handler(handler_id)
        run_post_job_hook(HOOK_KEY_SYNC_ALL, success=success, triggered_by=triggered_by, db=db)
        db.close()


def run_sync_group_job(drive_group: str, job_id: Optional[int] = None, triggered_by: str = "manual") -> None:
    """
    Sync all drives for a specific group (CL2K, MM2K, Custom), regardless of
    subscription status. Subscription is for the daily workflow; group syncs
    run all drives of that type unconditionally.
    """
    success = False
    db = SessionLocal()
    try:
        log_debug(LogTags.SCHEDULER, f"Starting scheduled sync for {drive_group} drives")

        if drive_group == "Custom":
            drives = db.query(Drive).filter(
                Drive.is_custom == True,
            ).all()
        else:
            drives = db.query(Drive).filter(
                Drive.style_type == drive_group,
                Drive.is_custom == False,
            ).all()

        if not drives:
            log_info(LogTags.SCHEDULER, f"No {drive_group} drives found to sync")
            if job_id is not None:
                job = db.query(Job).filter(Job.id == job_id).first()
                if job:
                    update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100, message=f"No {drive_group} drives found")
            return

        if job_id is not None:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = JOB_STATUS_RUNNING
                job.message = f"Starting sync of {drive_group} drives"
                db.commit()
            else:
                job = Job(
                    job_type=JOB_TYPE_GDRIVE_SYNC,
                    status=JOB_STATUS_RUNNING,
                )
                db.add(job)
                db.commit()
                db.refresh(job)
        else:
            job = Job(
                job_type=JOB_TYPE_GDRIVE_SYNC,
                status=JOB_STATUS_RUNNING,
            )
            db.add(job)
            db.commit()
            db.refresh(job)

        drive_ids = [drive.id for drive in drives]
        log_debug(LogTags.SCHEDULER, f"Syncing {len(drive_ids)} {drive_group} drives")

        sync_service = PosterSyncService(db)
        result = sync_service.sync_multiple_drives(
            drive_ids,
            job.id,
            max_workers=1,
            progress_callback=_build_progress_callback(db, job.id, LogTags.SCHEDULER),
        )

        if result.get('success'):
            success = True
        log_debug(LogTags.SCHEDULER, f"Sync completed: {result.get('message', 'Done')}")

    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Scheduled {drive_group} sync failed: {str(e)}\n{traceback.format_exc()}")
        if job_id is not None:
            mark_job_failed(db, job_id, e)
        raise
    finally:
        run_post_job_hook(HOOK_KEY_SYNC_ALL, success=success, triggered_by=triggered_by, db=db)
        db.close()
