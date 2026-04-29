import traceback
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from database import SessionLocal
from core.logging import (
    LogTags,
    add_job_log_handler,
    log_error,
    log_info,
    log_success,
    log_warning,
    log_section_start,
    log_section_end,
    remove_job_log_handler,
)
from models.job import (
    Job,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_RUNNING,
    format_complete_message,
    format_start_message,
    mark_job_failed,
    update_job_state,
)
from models.idarr import create_idarr_run, prune_idarr_run_history, compact_idarr_run_details_history
from services.rclone import RcloneService
from services.idarr_runner import IdarrRunner
from services.discord_notifications import send_discord_notification, send_major_error_notification

IDARR_RUN_HISTORY_KEEP_LATEST = 10

def _sanitize_message(message: str, max_len: int = 220) -> str:
    value = " ".join(message.split())
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 3]}..."


def _build_progress_callback(db: Any, job: Job) -> Callable[[str, int, int, str], None]:
    def report_progress(_phase: str, current: int, total: int, message: str) -> None:
        try:
            normalized_total = total if total and total > 0 else 100
            percent = int((current / normalized_total) * 100)
            clamped_percent = max(0, min(percent, 99))
            current_progress = int(job.progress or 0)
            next_progress = max(current_progress, clamped_percent)
            next_message = _sanitize_message(message)
            current_message = str(job.message or "")

            if next_progress == current_progress and next_message == current_message:
                return

            update_job_state(
                db,
                job,
                progress=next_progress,
                message=next_message,
            )
        except Exception as exc:
            log_warning(LogTags.IDARR, f"Failed to update IDarr job progress: {exc}")

    return report_progress


def _queue_idarr_sync_after_run(db: Any, config_data: dict[str, Any], triggered_by_job_id: int) -> None:
    """Queue an IDarr personal sync job after a successful IDarr run."""
    from core.job_queue import job_queue
    from models.job import JOB_TYPE_IDARR

    try:
        sync_target_index = int(config_data.get("sync_target_index") or 0)
        raw_targets = config_data.get("sync_targets")
        sync_targets = [t for t in raw_targets if isinstance(t, dict)] if isinstance(raw_targets, list) else []

        if not sync_targets or sync_target_index >= len(sync_targets):
            log_warning(LogTags.IDARR, "sync_after_run: no valid sync target found, skipping personal sync", triggered_by=triggered_by_job_id)
            return

        selected_target = sync_targets[sync_target_index]
        personal_drive_id = str(selected_target.get("personal_drive_id") or "").strip()
        source_dir = str(selected_target.get("source_dir") or "").strip()

        if not personal_drive_id or not source_dir:
            log_warning(LogTags.IDARR, "sync_after_run: sync target missing personal_drive_id or source_dir, skipping", triggered_by=triggered_by_job_id)
            return

        sync_config = {
            "personal_drive_id": personal_drive_id,
            "source_dir": source_dir,
            "sync_target_index": sync_target_index,
        }

        from models.job import Job
        new_job = Job(
            job_type=JOB_TYPE_IDARR,
            status="pending",
            progress=0,
            message=f"Queued personal sync (after IDarr job {triggered_by_job_id})",
        )
        db.add(new_job)
        db.commit()
        db.refresh(new_job)
        new_job_id = new_job.id

        log_info(LogTags.IDARR, f"sync_after_run: queuing personal sync job {new_job_id}", triggered_by=triggered_by_job_id, drive_id=personal_drive_id)
        job_queue.submit(run_idarr_sync_background_job, new_job_id, new_job_id, sync_config)

    except Exception as exc:
        log_error(LogTags.IDARR, f"sync_after_run: failed to queue personal sync: {exc}\n{traceback.format_exc()}", triggered_by=triggered_by_job_id)
        try:
            db.rollback()
        except Exception:
            pass


def run_idarr_background_job(job_id: int, config_data: dict[str, Any]) -> None:
    db = SessionLocal()
    handler_id = add_job_log_handler("idarr", job_id, "IDarr")
    success = False

    try:
        log_section_start(LogTags.IDARR, f"IDarr Job Started (job_id={job_id})")

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.IDARR, f"Job {job_id} not found")
            return

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            progress=1,
            message=format_start_message("IDarr rename"),
        )

        update_job_state(db, job, progress=5, message="Validating native IDarr runtime...")

        idarr_service = IdarrRunner(db)

        update_job_state(db, job, progress=10, message="Running native IDarr...")
        result = idarr_service.run(
            config_data,
            progress_callback=_build_progress_callback(db, job),
        )

        if not result.success:
            raise RuntimeError(result.message)

        if result.warnings:
            for warning in result.warnings:
                log_warning(LogTags.IDARR, warning)

        unmatched_count = len(result.unmatched_assets) if result.unmatched_assets else 0

        if result.stats:
            stats = result.stats
            elapsed = str(stats.get("elapsed") or f"{int(stats.get('elapsed_seconds') or 0)}s")
            log_success(
                LogTags.IDARR,
                (
                    "Summary report: "
                    f"total={int(stats.get('total_assets') or 0)}, "
                    f"processed={int(stats.get('processed_assets') or 0)}, "
                    f"renamed={int(stats.get('files_renamed') or stats.get('renamed') or 0)}, "
                    f"skipped={int(stats.get('skipped') or 0)}, "
                    f"unmatched={unmatched_count}, "
                    f"elapsed={elapsed}"
                ),
            )
        else:
            log_success(LogTags.IDARR, "Native IDarr complete")

        create_idarr_run(
            db,
            job_id=job_id,
            success=True,
            source_dir=str(config_data.get("source_dir") or "").strip() or None,
            destination_dir=str(config_data.get("destination_dir", "")).strip() or None,
            scope_token=str(config_data.get("scope_token") or "").strip() or None,
            stats_json=json.dumps(result.stats or {}),
            details_json=json.dumps(result.details or {}),
            warnings_json=json.dumps(result.warnings or []),
            unmatched_count=unmatched_count,
        )
        prune_idarr_run_history(
            db,
            keep_latest=IDARR_RUN_HISTORY_KEEP_LATEST,
            scope_token=str(config_data.get("scope_token") or "").strip() or None,
        )
        compact_idarr_run_details_history(
            db,
            keep_full_latest=1,
            scope_token=str(config_data.get("scope_token") or "").strip() or None,
        )
        db.commit()

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("IDarr native rename", f"unmatched={unmatched_count}"),
            completed_at=datetime.now(timezone.utc),
        )

        stats = result.stats or {}
        renamed_count = int(stats.get("files_renamed") or stats.get("renamed") or 0)
        skipped_count = int(stats.get("skipped") or 0)
        pending_count = int(stats.get("unmatched_assets") or unmatched_count)
        elapsed = str(stats.get("elapsed") or f"{int(stats.get('elapsed_seconds') or 0)}s")

        # Build renamed file list from file_operations in details
        details_data = result.details or {}
        file_ops: list[dict] = details_data.get("file_operations", []) or []
        renamed_ops = [
            op for op in file_ops
            if str(op.get("status", "")).lower() == "success"
        ]

        BUDGET = 1000  # Discord field value limit is 1024 chars
        renamed_lines: list[str] = []
        used = 0
        shown = 0
        for op in renamed_ops:
            from_name = Path(str(op.get("from_path", ""))).name
            to_name = Path(str(op.get("to_path", ""))).name
            if from_name and to_name and from_name != to_name:
                line = f"• `{from_name}` → `{to_name}`"
            elif to_name:
                line = f"• `{to_name}`"
            else:
                continue
            cost = len(line) + 1  # +1 for \n
            remaining = len(renamed_ops) - shown
            overflow_line = f"_...and {remaining} more_"
            # Stop if this line + overflow hint won't fit
            if used + cost + len(overflow_line) + 1 > BUDGET:
                renamed_lines.append(overflow_line)
                break
            renamed_lines.append(line)
            used += cost
            shown += 1

        renamed_value = "\n".join(renamed_lines) if renamed_lines else "_None_"

        discord_fields = [
            {"name": "Renamed", "value": str(renamed_count), "inline": True},
            {"name": "Skipped", "value": str(skipped_count), "inline": True},
            {"name": "Pending/Unresolved", "value": str(pending_count), "inline": True},
            {"name": "Elapsed", "value": elapsed, "inline": True},
            {"name": "Job ID", "value": str(job_id), "inline": True},
        ]
        if renamed_lines:
            discord_fields.append({"name": "Renamed Files", "value": renamed_value, "inline": False})

        send_discord_notification(
            db,
            feature_key="idarr",
            event_type="success",
            title="IDarr Summary",
            description=(
                f"IDarr processing complete: {renamed_count} renamed"
                + (f", {pending_count} unresolved" if pending_count else "")
            ),
            fields=discord_fields,
            color=0x4CAF50,
        )

        success = True
        log_section_end(LogTags.IDARR, f"IDarr Job Completed (job_id={job_id})")

        # Queue personal drive sync if requested and files were actually renamed
        if bool(config_data.get("sync_after_run")) and not bool(config_data.get("dry_run")) and renamed_count > 0:
            _queue_idarr_sync_after_run(db, config_data, job_id)
        elif bool(config_data.get("sync_after_run")) and not bool(config_data.get("dry_run")) and renamed_count == 0:
            log_info(LogTags.IDARR, "sync_after_run: skipping personal sync — no files were renamed", job_id=job_id)

    except Exception as exc:
        log_error(LogTags.IDARR, f"IDarr background job failed: {exc}\n{traceback.format_exc()}")
        send_discord_notification(
            db,
            feature_key="idarr",
            event_type="error",
            title="IDarr Failed",
            description=str(exc),
            fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
            color=0xF44336,
        )
        send_major_error_notification(
            db,
            source="idarr",
            message=str(exc),
            job_id=job_id,
        )
        try:
            create_idarr_run(
                db,
                job_id=job_id,
                success=False,
                source_dir=str(config_data.get("source_dir") or "").strip() or None,
                destination_dir=str(config_data.get("destination_dir", "")).strip() or None,
                scope_token=str(config_data.get("scope_token") or "").strip() or None,
                stats_json=None,
                details_json=None,
                warnings_json=json.dumps([str(exc)]),
                unmatched_count=0,
            )
            prune_idarr_run_history(
                db,
                keep_latest=IDARR_RUN_HISTORY_KEEP_LATEST,
                scope_token=str(config_data.get("scope_token") or "").strip() or None,
            )
            compact_idarr_run_details_history(
                db,
                keep_full_latest=1,
                scope_token=str(config_data.get("scope_token") or "").strip() or None,
            )
            db.commit()
        except Exception:
            db.rollback()
        mark_job_failed(db, job_id, exc)
        log_section_end(LogTags.IDARR, f"IDarr Job Failed (job_id={job_id})")
    finally:
        remove_job_log_handler(handler_id, job_type="idarr", success=success)
        db.close()


def run_idarr_sync_background_job(job_id: int, config_data: dict[str, Any]) -> None:
    db = SessionLocal()
    handler_id = add_job_log_handler("idarr", job_id, "IDarr personal sync")
    success = False

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.IDARR, f"Job {job_id} not found")
            return

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            progress=5,
            message=format_start_message("IDarr personal sync"),
        )

        personal_drive_id = str(config_data.get("personal_drive_id", "")).strip()
        source_dir = Path(str(config_data.get("source_dir", "")).strip())
        sync_mode = "sync"

        if not personal_drive_id:
            raise ValueError("Personal drive ID is required")

        if not source_dir.exists() or not source_dir.is_dir():
            raise ValueError(f"Source directory not found: {source_dir}")

        log_info(LogTags.IDARR, f"Starting IDarr personal sync from '{source_dir}' to personal drive folder ID '{personal_drive_id}' using mode '{sync_mode}'")
        update_job_state(db, job, progress=20, message="Uploading to personal drive...")

        rclone = RcloneService()

        last_progress_emit = int(job.progress or 0)
        last_uploaded_file_message = ""

        def upload_progress_callback(current: int, total: int, phase: str, message: str) -> None:
            nonlocal last_progress_emit, last_uploaded_file_message
            try:
                normalized_total = max(total, 1)
                ratio = min(max(current / normalized_total, 0.0), 1.0)
                if phase == "checking":
                    scaled_progress = int(20 + ratio * 55)
                elif phase in {"uploading", "uploading_stats", "uploading_file"}:
                    scaled_progress = int(75 + ratio * 24)
                elif phase == "complete":
                    scaled_progress = 99
                else:
                    scaled_progress = max(int(job.progress or 0), 20)
                current_progress = int(job.progress or 0)
                next_progress = max(current_progress, min(scaled_progress, 99))

                if phase == "checking":
                    sync_message = _sanitize_message(
                        f"Scanning upload status: {current}/{normalized_total} files checked"
                    )
                elif phase == "uploading_file":
                    sync_message = _sanitize_message(message or "Uploading file...")
                    last_uploaded_file_message = sync_message
                elif phase == "uploading_stats":
                    stats_message = _sanitize_message(
                        f"Uploading to personal drive: {current}/{normalized_total} files transferred"
                    )
                    if last_uploaded_file_message:
                        sync_message = _sanitize_message(f"{stats_message} | {last_uploaded_file_message}")
                    else:
                        sync_message = stats_message
                elif phase == "uploading":
                    sync_message = _sanitize_message(
                        f"Uploading to personal drive: {current}/{normalized_total} files processed"
                    )
                elif phase == "complete":
                    sync_message = _sanitize_message("Upload verification complete")
                else:
                    sync_message = _sanitize_message(
                        f"Uploading to personal drive: {current}/{normalized_total}"
                    )

                if message and phase in {"uploading", "start", "checking"}:
                    sync_message = _sanitize_message(message)

                if next_progress == last_progress_emit and sync_message == str(job.message or ""):
                    return

                if next_progress == last_progress_emit and phase not in {"complete", "checking"}:
                    return

                update_job_state(
                    db,
                    job,
                    progress=next_progress,
                    message=sync_message,
                )
                last_progress_emit = next_progress
            except Exception as callback_error:
                log_warning(LogTags.IDARR, f"Failed to update IDarr personal sync progress: {callback_error}")

        result = rclone.upload_folder(
            local_path=source_dir,
            drive_id=personal_drive_id,
            drive_name="personal-drive",
            mode=sync_mode,
            progress_callback=upload_progress_callback,
        )

        if not result.get("success", False):
            error = result.get("error", "rclone upload failed")
            raise RuntimeError(error)

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("IDarr personal sync", f"{sync_mode} to personal drive"),
            completed_at=datetime.now(timezone.utc),
        )
        log_success(LogTags.IDARR, f"IDarr personal sync complete: {source_dir} -> personal drive ({sync_mode})")
        success = True
    except Exception as exc:
        log_error(LogTags.IDARR, f"IDarr personal sync job failed: {exc}\n{traceback.format_exc()}")
        mark_job_failed(db, job_id, exc)
    finally:
        remove_job_log_handler(handler_id, job_type="idarr", success=success)
        db.close()
