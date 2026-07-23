import traceback
from typing import Any

from database import SessionLocal
from models.job import mark_job_failed
from core.job_cancel import JobCancelled
from models.artwork_drive import ArtworkDrive
from services.artwork_sync import ArtworkSyncService
from modules.sync import _build_progress_callback
from services.discord_notifications import send_discord_notification, send_major_error_notification
from core.logging import (
    LogTags,
    log_debug,
    log_warning,
    log_error,
    add_job_log_handler,
    remove_job_log_handler,
)


def run_artwork_sync_job(drive_id: int, job_id: int, triggered_by: str = "manual") -> dict[str, Any]:
    """Sync a single artwork drive. Shared by manual API jobs and scheduler-triggered jobs."""
    handler_id = add_job_log_handler("sync_one", job_id)

    db = SessionLocal()
    try:
        drive = db.query(ArtworkDrive).filter(ArtworkDrive.id == drive_id).first()
        drive_name = drive.name if drive else f"ID:{drive_id}"

        log_debug(LogTags.SYNC, f"Starting artwork sync: '{drive_name}' (job_id={job_id})")

        service = ArtworkSyncService(db)
        result = service.sync_drive(
            drive_id,
            job_id,
            progress_callback=_build_progress_callback(db, job_id, LogTags.SYNC),
        )

        if result.get("success"):
            log_debug(LogTags.SYNC, f"Completed artwork sync '{drive_name}'")
            send_discord_notification(
                db,
                feature_key="sync",
                event_type="success",
                title="Artwork Drive Sync Summary",
                description=f"{drive_name}",
                fields=[
                    {"name": "New", "value": str(int(result.get("added", 0))), "inline": True},
                    {"name": "Replaced", "value": str(int(result.get("updated", 0))), "inline": True},
                    {"name": "Deleted", "value": str(int(result.get("deleted", 0))), "inline": True},
                ],
                color=0x4CAF50,
            )
        else:
            log_warning(LogTags.SYNC, f"Artwork sync failed '{drive_name}': {result.get('error')}")
            error_message = str(result.get("error") or result.get("message") or "Artwork sync failed")
            send_discord_notification(
                db,
                feature_key="sync",
                event_type="error",
                title="Artwork Drive Sync Failed",
                description=error_message,
                fields=[
                    {"name": "Drive", "value": drive_name, "inline": True},
                    {"name": "Job ID", "value": str(job_id), "inline": True},
                ],
                color=0xF44336,
            )
            send_major_error_notification(
                db,
                source="sync.one.artwork",
                message=error_message,
                job_id=job_id,
            )

        return result
    except JobCancelled:
        raise
    except Exception as e:
        log_error(LogTags.SYNC, f"Artwork sync job failed: {str(e)}\n{traceback.format_exc()}")
        send_discord_notification(
            db,
            feature_key="sync",
            event_type="error",
            title="Artwork Drive Sync Failed",
            description=str(e),
            fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
            color=0xF44336,
        )
        send_major_error_notification(
            db,
            source="sync.one.artwork",
            message=str(e),
            job_id=job_id,
        )
        mark_job_failed(db, job_id, e)
        raise
    finally:
        remove_job_log_handler(handler_id)
        db.close()


# Sync-all (poster + artwork) is unified in modules.sync.run_sync_all_job; the single-drive
# artwork sync above stays here.
