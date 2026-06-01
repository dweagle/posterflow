import hashlib
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
from models.setting import get_setting, upsert_setting
from services.rclone import RcloneService
from services.idarr_runner import IdarrRunner
from services.discord_notifications import send_discord_notification, send_major_error_notification

IDARR_RUN_HISTORY_KEEP_LATEST = 10
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".psd"}


def _sync_state_key(source_dir: str) -> str:
    """Return a stable settings key scoped to a specific source directory."""
    digest = hashlib.sha256(source_dir.encode()).hexdigest()[:16]
    return f"idarr_last_sync_{digest}"


def _get_last_sync_time(db: Any, source_dir: str) -> datetime | None:
    """Return the UTC datetime of the last successful personal sync for source_dir, or None."""
    key = _sync_state_key(source_dir)
    setting = get_setting(db, key)
    if not setting or not setting.value:
        return None
    try:
        return datetime.fromisoformat(setting.value)
    except (ValueError, TypeError):
        return None


def _update_last_sync_time(db: Any, source_dir: str) -> None:
    """Persist the current UTC time as the last successful personal sync time for source_dir."""
    key = _sync_state_key(source_dir)
    now_iso = datetime.now(timezone.utc).isoformat()
    upsert_setting(db, key, now_iso)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _has_files_newer_than(source_dir: Path, since: datetime) -> bool:
    """Return True if any image file in source_dir has an mtime or ctime strictly after `since`.

    mtime catches content changes (new/replaced files).
    ctime catches renames and permission changes without a content write.
    """
    since_ts = since.timestamp()
    try:
        for entry in source_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                st = entry.stat()
                if st.st_mtime > since_ts or st.st_ctime > since_ts:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False

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
        except Exception:  # nosec B110
            pass


def _run_idarr_personal_sync_inline(
    db: Any,
    job: Job,
    personal_drive_id: str,
    source_dir: Path,
    progress_start: int,
    progress_end: int,
    label: str,
) -> None:
    """Run IDarr personal sync inline as part of an existing job (e.g. workflow step)."""
    rclone = RcloneService()
    span = max(1, progress_end - progress_start)
    last_progress_emit = [progress_start]

    def _sync_progress(current: int, total: int, phase: str, message: str) -> None:
        try:
            ratio = min(max(current / max(total, 1), 0.0), 1.0)
            if phase == "checking":
                scaled = int(progress_start + ratio * span * 0.4)
            elif phase in {"uploading", "uploading_stats", "uploading_file"}:
                scaled = int(progress_start + span * 0.4 + ratio * span * 0.6)
            elif phase == "complete":
                scaled = progress_end
            else:
                scaled = progress_start
            next_progress = max(last_progress_emit[0], min(scaled, progress_end))
            msg = _sanitize_message(f"{label} sync: {message}" if message else f"{label}: syncing to personal drive...")
            update_job_state(db, job, progress=next_progress, message=msg)
            last_progress_emit[0] = next_progress
        except Exception as cb_exc:
            log_warning(LogTags.IDARR, f"Inline sync progress callback error: {cb_exc}")

    result = rclone.upload_folder(
        local_path=source_dir,
        drive_id=personal_drive_id,
        drive_name="personal-drive",
        mode="sync",
        progress_callback=_sync_progress,
    )

    if not result.get("success", False):
        raise RuntimeError(f"Personal sync for '{label}' failed: {result.get('error', 'rclone error')}")

    log_success(LogTags.IDARR, f"Personal sync complete for '{label}'", drive_id=personal_drive_id)


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

        # Queue personal drive sync if requested and there is something to upload
        _sync_after = bool(config_data.get("sync_after_run"))
        _force_sync = bool(config_data.get("force_sync_after_run"))
        if _sync_after and not bool(config_data.get("dry_run")):
            _source_dir_str = str(config_data.get("source_dir") or "").strip()
            _should_sync = False
            _sync_reason = ""

            if _force_sync:
                _should_sync = True
                _sync_reason = "forced"
            elif renamed_count > 0:
                _should_sync = True
                _sync_reason = f"{renamed_count} file(s) renamed"
            elif _source_dir_str:
                _last_sync = _get_last_sync_time(db, _source_dir_str)
                if _last_sync is None:
                    _should_sync = True
                    _sync_reason = "no previous sync recorded"
                elif _has_files_newer_than(Path(_source_dir_str), _last_sync):
                    _should_sync = True
                    _sync_reason = "file(s) modified since last sync"

            if _should_sync:
                log_info(LogTags.IDARR, f"sync_after_run: queuing personal sync ({_sync_reason})", job_id=job_id)
                _queue_idarr_sync_after_run(db, config_data, job_id)
            else:
                log_info(LogTags.IDARR, "sync_after_run: skipping personal sync — nothing changed since last sync", job_id=job_id)

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

        # Record the sync time so the mtime gate can skip future runs with no changes
        try:
            _update_last_sync_time(db, str(source_dir))
        except Exception as ts_exc:
            log_warning(LogTags.IDARR, f"Failed to record last sync time: {ts_exc}")

        success = True
    except Exception as exc:
        log_error(LogTags.IDARR, f"IDarr personal sync job failed: {exc}\n{traceback.format_exc()}")
        mark_job_failed(db, job_id, exc)
    finally:
        remove_job_log_handler(handler_id, job_type="idarr", success=success)
        db.close()


def run_idarr_workflow_step(job_id: int, run_config: dict[str, Any]) -> None:
    """
    Run IDarr rename for selected scopes as part of the poster workflow.
    Called as a child job via _promote_child_progress_to_parent.

    run_config keys:
        idarr_config: dict   - full maker_tools_idarr_config from settings
        scope_indices: list  - which sync_target indices to run
        dry_run: bool        - dry run mode
        sync_after_run: bool - queue personal sync after each scope (if files renamed)
    """
    db = SessionLocal()
    handler_id = add_job_log_handler("idarr", job_id, "IDarr workflow step")
    success = False

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.IDARR, f"Workflow IDarr job {job_id} not found")
            return

        update_job_state(db, job, status=JOB_STATUS_RUNNING, progress=1, message=format_start_message("IDarr workflow step"))

        idarr_config: dict[str, Any] = run_config.get("idarr_config") or {}
        scope_indices: list[int] = [int(i) for i in (run_config.get("scope_indices") or []) if isinstance(i, (int, float))]
        dry_run: bool = bool(run_config.get("dry_run", False))
        sync_after_run: bool = bool(run_config.get("sync_after_run", False))

        raw_targets = idarr_config.get("sync_targets") or []
        all_targets = [t for t in raw_targets if isinstance(t, dict)]

        if not all_targets:
            update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100,
                             message="IDarr skipped: no sync targets configured",
                             completed_at=datetime.now(timezone.utc))
            success = True
            log_info(LogTags.IDARR, "Workflow IDarr step: no sync targets configured, skipping", job_id=job_id)
            return

        # Determine which targets to run
        if scope_indices:
            selected = [(i, all_targets[i]) for i in scope_indices if 0 <= i < len(all_targets)]
        else:
            selected = list(enumerate(all_targets))

        if not selected:
            update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100,
                             message="IDarr skipped: selected scopes not found",
                             completed_at=datetime.now(timezone.utc))
            success = True
            log_warning(LogTags.IDARR, "Workflow IDarr step: no valid scopes to run", job_id=job_id, scope_indices=scope_indices)
            return

        total_scopes = len(selected)
        log_info(LogTags.IDARR, f"Workflow IDarr step: running {total_scopes} scope(s)", job_id=job_id, scopes=[i for i, _ in selected])

        idarr_service = IdarrRunner(db)
        total_renamed = 0

        for run_num, (scope_idx, target) in enumerate(selected):
            scope_start_progress = int((run_num / total_scopes) * 95) + 1
            scope_end_progress = int(((run_num + 1) / total_scopes) * 95)
            scope_label = str(target.get("label") or f"Target {scope_idx + 1}").strip()
            source_dir = str(target.get("source_dir") or "").strip()

            log_info(LogTags.IDARR, f"Running IDarr scope {run_num + 1}/{total_scopes}: '{scope_label}'",
                     job_id=job_id, scope_idx=scope_idx, source_dir=source_dir)
            update_job_state(db, job, progress=scope_start_progress,
                             message=f"IDarr ({run_num + 1}/{total_scopes}): {scope_label}...")

            # Read the TMDB API key from its canonical settings location
            tmdb_setting = get_setting(db, "tmdb_api_key")
            tmdb_api_key = str(tmdb_setting.value or "").strip() if tmdb_setting else ""

            scope_config: dict[str, Any] = {
                **idarr_config,
                "source_dir": source_dir,
                "scope_token": target.get("scope_token", ""),
                "sync_target_index": scope_idx,
                "dry_run": dry_run,
                "sync_after_run": False,  # sync is handled inline below
                "tmdb_api_key": tmdb_api_key,
            }

            rename_end_progress = (
                int(scope_start_progress + (scope_end_progress - scope_start_progress) * 0.7)
                if sync_after_run else scope_end_progress
            )
            scope_span = max(1, rename_end_progress - scope_start_progress)

            def _make_progress_callback(s_start: int, s_span: int, label: str) -> Any:
                def report_progress(_phase: str, current: int, total: int, message: str) -> None:
                    try:
                        pct = int((current / max(total, 1)) * 100)
                        mapped = s_start + int((pct / 100) * s_span)
                        mapped = max(s_start, min(mapped, s_start + s_span - 1))
                        msg = _sanitize_message(f"{label}: {message}" if message else label)
                        update_job_state(db, job, progress=mapped, message=msg)
                    except Exception as cb_exc:
                        log_warning(LogTags.IDARR, f"Workflow IDarr progress callback error: {cb_exc}")
                return report_progress

            result = idarr_service.run(scope_config, progress_callback=_make_progress_callback(scope_start_progress, scope_span, scope_label))

            if not result.success:
                raise RuntimeError(f"IDarr scope '{scope_label}' failed: {result.message}")

            scope_stats = result.stats or {}
            scope_renamed = int(scope_stats.get("files_renamed") or scope_stats.get("renamed") or 0)
            total_renamed += scope_renamed
            log_success(LogTags.IDARR, f"IDarr scope '{scope_label}' complete: {scope_renamed} renamed", job_id=job_id)

            # Run personal sync inline if requested and files were renamed (or forced),
            # or if any image in the source dir is newer than the last recorded sync time.
            _force_scope_sync = bool(idarr_config.get("force_sync_after_run"))
            _should_sync_scope = scope_renamed > 0 or _force_scope_sync
            if not _should_sync_scope and sync_after_run and not dry_run and source_dir:
                _last_sync = _get_last_sync_time(db, source_dir)
                if _last_sync is None:
                    _should_sync_scope = True
                    log_info(LogTags.IDARR, f"Workflow sync trigger for '{scope_label}': no previous sync recorded", job_id=job_id)
                elif _has_files_newer_than(Path(source_dir), _last_sync):
                    _should_sync_scope = True
                    log_info(LogTags.IDARR, f"Workflow sync trigger for '{scope_label}': file(s) modified since last sync", job_id=job_id)
            if sync_after_run and not dry_run and _should_sync_scope:
                personal_drive_id = str(target.get("personal_drive_id") or "").strip()
                source_path = Path(source_dir)
                if personal_drive_id and source_path.exists():
                    log_info(LogTags.IDARR, f"Running inline personal sync for '{scope_label}'", job_id=job_id, drive_id=personal_drive_id)
                    update_job_state(db, job, progress=rename_end_progress,
                                     message=f"Syncing '{scope_label}' to personal drive...")
                    _run_idarr_personal_sync_inline(db, job, personal_drive_id, source_path,
                                                    rename_end_progress, scope_end_progress, scope_label)
                    try:
                        _update_last_sync_time(db, source_dir)
                    except Exception as _ts_exc:
                        log_warning(LogTags.IDARR, f"Failed to record last sync time for '{scope_label}': {_ts_exc}")
                else:
                    log_warning(LogTags.IDARR, f"Inline sync skipped for '{scope_label}': missing personal_drive_id or source dir",
                                job_id=job_id)
            update_job_state(db, job, progress=scope_end_progress, message=f"Scope '{scope_label}' complete")

        update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100,
                         message=format_complete_message("IDarr workflow step", f"{total_renamed} renamed across {total_scopes} scope(s)"),
                         completed_at=datetime.now(timezone.utc))
        success = True
        log_section_end(LogTags.IDARR, f"IDarr workflow step completed (job_id={job_id}, renamed={total_renamed})")

    except Exception as exc:
        log_error(LogTags.IDARR, f"IDarr workflow step failed: {exc}\n{traceback.format_exc()}", job_id=job_id)
        try:
            mark_job_failed(db, job_id, exc)
        except Exception:
            pass
        raise
    finally:
        remove_job_log_handler(handler_id, job_type="idarr", success=success)
        db.close()
