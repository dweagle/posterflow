import hashlib
import os
import threading
import traceback
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from database import SessionLocal
from core.config import settings as app_settings
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
    finalize_job_cancelled,
)
from core.job_cancel import JobCancelled, check_cancelled
from models.idarr import IdarrRun, create_idarr_run, prune_idarr_run_history, compact_idarr_run_details_history
from models.setting import get_setting, upsert_setting
from services.rclone import RcloneService
from services.sync_base import STEP_INDENT
from services.idarr_runner import IdarrRunner
from services.discord_notifications import send_discord_notification, send_major_error_notification

IDARR_RUN_HISTORY_KEEP_LATEST = 10
# Auto-prune idarr_asset_cache after each run: remove entries with no activity in this many days.
IDARR_CACHE_AUTO_PRUNE_DAYS = 90
# Only run the auto-prune when the cache has at least this many rows (avoids overhead on small installs).
IDARR_CACHE_AUTO_PRUNE_MIN_ROWS = 500
# Max number of pruned entry names to list in the log (with an "…and N more" overflow line).
IDARR_CACHE_AUTO_PRUNE_LOG_CAP = 100
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".psd"}


def _sync_state_key(source_dir: str) -> str:
    """Return a stable settings key scoped to a specific source directory."""
    digest = hashlib.sha256(source_dir.encode()).hexdigest()[:16]
    return f"idarr_last_sync_{digest}"


def _auto_prune_cache(db: Any, scope_token: str | None) -> None:
    """Automatically prune stale idarr_asset_cache rows after a successful run.

    Only runs when the scoped cache row count exceeds ``IDARR_CACHE_AUTO_PRUNE_MIN_ROWS``
    to avoid overhead on small installs. A row is "stale" when it has had no activity —
    checked, updated, or created — within ``IDARR_CACHE_AUTO_PRUNE_DAYS`` days.

    Staleness is judged on ``COALESCE(last_checked_at, updated_at, created_at)`` rather than
    ``last_checked_at`` alone: a freshly created/resolved row hasn't had a TMDB re-check yet, so
    its ``last_checked_at`` is NULL — keying off that would wrongly delete brand-new rows and
    mislabel them as "90 days old".
    """
    from datetime import timedelta
    from sqlalchemy import func as _func
    from models.idarr import IdarrAssetCache

    def _label(title: Any, year: Any) -> str:
        text = str(title or "").strip() or "unknown"
        return f"{text} ({year})" if isinstance(year, int) else f"{text} (None)"

    try:
        if scope_token:
            base = db.query(IdarrAssetCache).filter(
                IdarrAssetCache.asset_key.like(f"%::scope={scope_token}")
            )
        else:
            base = db.query(IdarrAssetCache).filter(
                ~IdarrAssetCache.asset_key.like("%::scope=%")
            )

        if base.count() < IDARR_CACHE_AUTO_PRUNE_MIN_ROWS:
            return

        threshold = datetime.now(timezone.utc) - timedelta(days=IDARR_CACHE_AUTO_PRUNE_DAYS)
        last_activity = _func.coalesce(
            IdarrAssetCache.last_checked_at,
            IdarrAssetCache.updated_at,
            IdarrAssetCache.created_at,
        )
        stale = base.filter(last_activity < threshold)

        # Capture the names before deleting so the log can say exactly what was pruned.
        labels = [
            _label(title, year)
            for (title, year) in stale
            .with_entities(IdarrAssetCache.title, IdarrAssetCache.year)
            .limit(IDARR_CACHE_AUTO_PRUNE_LOG_CAP)
            .all()
        ]

        deleted = stale.delete(synchronize_session=False)
        if deleted:
            log_info(
                LogTags.IDARR,
                f"Auto-pruned {deleted} stale cache entries (no checked/updated/created activity in >{IDARR_CACHE_AUTO_PRUNE_DAYS}d)",
                scope_token=scope_token,
            )
            for label in labels:
                log_info(LogTags.IDARR, f"• Stale cache entry removed: {label}")
            overflow = int(deleted) - len(labels)
            if overflow > 0:
                log_info(LogTags.IDARR, f"…and {overflow} more stale cache entry(ies) removed")
    except Exception as _exc:
        log_warning(LogTags.IDARR, f"Auto cache prune failed (non-fatal): {_exc}")


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
    """Return True if any image file anywhere under source_dir has an mtime or ctime
    strictly after `since`.

    Recurses: asset drives keep every file in logos/, backgrounds/ and squareart/
    subfolders, so a top-level-only scan sees only those directories and misses every
    change on an asset drive. os.walk covers nested layouts and flat drives alike.

    mtime catches content changes (new/replaced files); ctime catches renames/moves and
    permission changes without a content write. Deletions are not detected — nothing is
    left behind with a newer timestamp.
    """
    since_ts = since.timestamp()
    try:
        for root, _dirs, files in os.walk(source_dir):
            for name in files:
                if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
                    continue
                try:
                    st = os.stat(os.path.join(root, name))
                except OSError:
                    continue
                if st.st_mtime > since_ts or st.st_ctime > since_ts:
                    return True
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
        # Outside the try so a stop request propagates instead of being logged as
        # a progress-update warning.
        check_cancelled(job.id)
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


# Ignored re-drops go up unchanged alongside the renamed files.
_TARGETED_UPLOAD_STATUSES = ("ready", "ignored")


def _resolve_targeted_upload_target(config_data: dict[str, Any]) -> dict[str, str] | None:
    """Resolve the sync target a targeted run should upload to."""
    sync_target_index = int(config_data.get("sync_target_index") or 0)
    raw_targets = config_data.get("sync_targets")
    sync_targets = [t for t in raw_targets if isinstance(t, dict)] if isinstance(raw_targets, list) else []
    if not sync_targets or sync_target_index >= len(sync_targets):
        return None

    selected_target = sync_targets[sync_target_index]
    personal_drive_id = str(selected_target.get("personal_drive_id") or "").strip()
    source_dir = str(selected_target.get("source_dir") or "").strip()
    if not personal_drive_id or not source_dir:
        return None

    return {
        "personal_drive_id": personal_drive_id,
        "source_dir": source_dir,
        "label": str(selected_target.get("label") or "").strip() or personal_drive_id,
    }


def _queue_idarr_targeted_upload(
    db: Any,
    config_data: dict[str, Any],
    run: Any,
    ready_count: int,
    triggered_by_job_id: int,
) -> int | None:
    """Queue the single-item upload as its own job so it gets its own log and progress.

    Mirroring the whole scope folder to push one poster costs a full remote listing plus a check
    pass over every file, so single-item drops copy their own files instead — in a separate job,
    the same way the full personal sync has always been separate from the rename that triggered it.
    """
    from core.job_queue import job_queue
    from models.job import JOB_TYPE_IDARR

    target = _resolve_targeted_upload_target(config_data)
    if not target:
        log_warning(
            LogTags.IDARR,
            "Single-item upload: sync target is missing a personal drive ID or sync folder, skipping upload",
            triggered_by=triggered_by_job_id,
        )
        return None

    upload_job_id: int | None = None
    try:
        upload_job = Job(
            job_type=JOB_TYPE_IDARR,
            status="pending",
            progress=0,
            message=f"Queued single-item upload (after IDarr job {triggered_by_job_id})",
        )
        db.add(upload_job)
        db.commit()
        db.refresh(upload_job)
        upload_job_id = upload_job.id

        # Recorded before the job is submitted so the run-result endpoint can always tell the
        # UI that an upload is still pending, even if the worker picks it up immediately.
        _set_run_detail(db, run, "targeted_upload_job_id", upload_job_id)

        log_info(
            LogTags.IDARR,
            f"Single-item upload: queued job {upload_job_id} for {ready_count} file(s) to '{target['label']}'",
            triggered_by=triggered_by_job_id,
            drive_id=target["personal_drive_id"],
        )
        job_queue.submit(
            run_idarr_targeted_upload_job,
            upload_job_id,
            upload_job_id,
            {
                **target,
                "idarr_run_id": int(run.id),
                "triggered_by_job_id": triggered_by_job_id,
            },
        )
        return upload_job_id
    except Exception as exc:
        log_error(
            LogTags.IDARR,
            f"Single-item upload: failed to queue upload job: {exc}\n{traceback.format_exc()}",
            triggered_by=triggered_by_job_id,
        )
        try:
            db.rollback()
            # The row may already be recorded on the run; fail it so nothing waits on a job
            # that will never be picked up.
            if upload_job_id is not None:
                mark_job_failed(db, upload_job_id, exc)
        except Exception:  # nosec B110
            pass
        return None


def _set_run_detail(db: Any, run: Any, key: str, value: Any) -> None:
    """Write one key into an IdarrRun's details payload and commit."""
    try:
        details = json.loads(run.details_json) if run.details_json else {}
    except json.JSONDecodeError:
        details = {}
    if not isinstance(details, dict):
        details = {}
    details[key] = value
    run.details_json = json.dumps(details)
    db.commit()


def run_idarr_targeted_upload_job(job_id: int, config_data: dict[str, Any]) -> None:
    """Upload the files a targeted (quick add) run produced, one at a time.

    Deliberately unlike the full sync: pending/conflicted files are held back, and the last-sync
    timestamp is left alone so a later full sync still sees everything it has to reconcile
    (deletes and stale renames included).
    """
    db = SessionLocal()
    handler_id = add_job_log_handler("idarr", job_id, "IDarr single-item upload")
    success = False

    try:
        log_section_start(LogTags.IDARR, f"IDarr Single-Item Upload Started (job_id={job_id})")

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.IDARR, f"Job {job_id} not found")
            return

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            progress=5,
            message=format_start_message("IDarr single-item upload"),
        )

        personal_drive_id = str(config_data.get("personal_drive_id") or "").strip()
        source_dir = Path(str(config_data.get("source_dir") or "").strip())
        label = str(config_data.get("label") or "").strip() or personal_drive_id
        triggered_by_job_id = config_data.get("triggered_by_job_id")

        run = db.query(IdarrRun).filter(IdarrRun.id == int(config_data.get("idarr_run_id") or 0)).first()
        if not run:
            raise ValueError(f"IDarr run {config_data.get('idarr_run_id')} not found")

        try:
            details = json.loads(run.details_json) if run.details_json else {}
        except json.JSONDecodeError:
            details = {}
        outcomes = [row for row in (details.get("targeted_outcomes") or []) if isinstance(row, dict)]
        ready = [row for row in outcomes if str(row.get("status") or "") in _TARGETED_UPLOAD_STATUSES]
        held_back = [row for row in outcomes if str(row.get("status") or "") not in _TARGETED_UPLOAD_STATUSES]

        log_info(
            LogTags.IDARR,
            f"Uploading {len(ready)} file(s) from '{source_dir}' to '{label}' (from IDarr job {triggered_by_job_id})",
            drive_id=personal_drive_id,
        )
        for row in held_back:
            log_warning(
                LogTags.IDARR,
                f"{STEP_INDENT}Held back '{row.get('source_filename')}': {row.get('status')} "
                f"({row.get('reason') or 'no reason given'}) — stays in the sync folder until it is resolved",
            )
        for row in ready:
            if str(row.get("status") or "") == "ignored":
                log_info(LogTags.IDARR, f"{STEP_INDENT}'{row.get('source_filename')}' is on the ignore list — uploading unchanged")

        # One rclone process for the whole drop: paying startup, auth and connection setup per
        # file made a multi-file drop crawl (~2s each), and it gave up rclone's parallel transfers.
        by_path = {
            str(row.get("relative_path") or row.get("final_filename") or "").strip(): row
            for row in ready
        }
        done = [0]

        def _file_done(relative_path: str, action: str) -> None:
            check_cancelled(job_id)
            done[0] += 1
            log_info(LogTags.IDARR, f"{STEP_INDENT}[{done[0]}/{len(ready)}] {relative_path}: {action}")
            update_job_state(
                db,
                job,
                progress=min(95, int(5 + done[0] / max(len(ready), 1) * 90)),
                message=_sanitize_message(f"Uploaded {done[0]}/{len(ready)} to {label}: {Path(relative_path).name}"),
            )

        check_cancelled(job_id)
        update_job_state(
            db,
            job,
            progress=10,
            message=_sanitize_message(f"Uploading {len(ready)} file(s) to {label}..."),
        )
        upload_result = RcloneService().upload_files(
            source_dir,
            personal_drive_id,
            list(by_path.keys()),
            drive_name=label,
            file_done_callback=_file_done,
        )

        failed_paths = upload_result.get("failed") or {}
        uploaded = 0
        failed = 0
        for relative_path, row in by_path.items():
            error = failed_paths.get(relative_path)
            if error:
                row["status"] = "upload_failed"
                row["reason"] = str(error)
                failed += 1
                log_error(LogTags.IDARR, f"{STEP_INDENT}Upload failed for '{relative_path}': {row['reason']}")
            else:
                row["uploaded"] = True
                if str(row.get("status") or "") == "ready":
                    row["status"] = "uploaded"
                uploaded += 1

        details["targeted_outcomes"] = outcomes
        run.details_json = json.dumps(details)
        db.commit()

        ignored_as_is = sum(1 for row in ready if row.get("status") == "ignored" and row.get("uploaded"))
        summary = f"uploaded={uploaded}, failed={failed}, held_back={len(held_back)}"
        if ignored_as_is:
            summary += f", ignored_as_is={ignored_as_is}"
        if failed:
            log_warning(LogTags.IDARR, f"Single-item upload finished with failures: {summary}")
        else:
            log_success(LogTags.IDARR, f"Single-item upload complete: {summary}")

        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("IDarr single-item upload", summary),
            completed_at=datetime.now(timezone.utc),
        )
        # The last-sync timestamp is intentionally not touched here: stamping it would hide
        # older local changes from the next full sync's change check.
        success = True
        log_section_end(LogTags.IDARR, f"IDarr Single-Item Upload Completed (job_id={job_id})")
    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_section_end(LogTags.IDARR, f"IDarr Single-Item Upload Stopped (job_id={job_id})")
    except Exception as exc:
        log_error(LogTags.IDARR, f"IDarr single-item upload failed: {exc}\n{traceback.format_exc()}")
        mark_job_failed(db, job_id, exc)
        log_section_end(LogTags.IDARR, f"IDarr Single-Item Upload Failed (job_id={job_id})")
    finally:
        remove_job_log_handler(handler_id, job_type="idarr", success=success)
        db.close()


def run_idarr_file_upload_job(job_id: int, config_data: dict[str, Any]) -> None:
    """Push named files from a sync folder to its drive unchanged (ignored quick-add drops)."""
    db = SessionLocal()
    handler_id = add_job_log_handler("idarr", job_id, "IDarr file upload")
    success = False

    try:
        log_section_start(LogTags.IDARR, f"IDarr File Upload Started (job_id={job_id})")

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.IDARR, f"Job {job_id} not found")
            return

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            progress=5,
            message=format_start_message("IDarr file upload"),
        )

        personal_drive_id = str(config_data.get("personal_drive_id") or "").strip()
        source_dir = Path(str(config_data.get("source_dir") or "").strip())
        label = str(config_data.get("label") or "").strip() or personal_drive_id
        relative_paths = [str(path).strip() for path in (config_data.get("relative_paths") or []) if str(path).strip()]
        if not personal_drive_id or not relative_paths:
            raise ValueError("A personal drive ID and at least one file are required")

        log_info(
            LogTags.IDARR,
            f"Uploading {len(relative_paths)} file(s) unchanged from '{source_dir}' to '{label}'",
            drive_id=personal_drive_id,
        )
        done = [0]

        def _file_done(relative_path: str, action: str) -> None:
            check_cancelled(job_id)
            done[0] += 1
            log_info(LogTags.IDARR, f"{STEP_INDENT}[{done[0]}/{len(relative_paths)}] {relative_path}: {action}")
            update_job_state(
                db,
                job,
                progress=min(95, int(5 + done[0] / len(relative_paths) * 90)),
                message=_sanitize_message(f"Uploaded {done[0]}/{len(relative_paths)} to {label}: {Path(relative_path).name}"),
            )

        check_cancelled(job_id)
        upload_result = RcloneService().upload_files(
            source_dir,
            personal_drive_id,
            relative_paths,
            drive_name=label,
            file_done_callback=_file_done,
        )

        failed_paths = upload_result.get("failed") or {}
        for relative_path, error in failed_paths.items():
            log_error(LogTags.IDARR, f"{STEP_INDENT}Upload failed for '{relative_path}': {error}")
        if failed_paths:
            raise RuntimeError(
                f"Upload failed for {len(failed_paths)} file(s): "
                + "; ".join(f"{Path(path).name}: {error}" for path, error in failed_paths.items())
            )

        summary = f"uploaded={len(relative_paths)}"
        log_success(LogTags.IDARR, f"File upload complete: {summary}")
        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("IDarr file upload", summary),
            completed_at=datetime.now(timezone.utc),
        )
        success = True
        log_section_end(LogTags.IDARR, f"IDarr File Upload Completed (job_id={job_id})")
    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_section_end(LogTags.IDARR, f"IDarr File Upload Stopped (job_id={job_id})")
    except Exception as exc:
        log_error(LogTags.IDARR, f"IDarr file upload failed: {exc}\n{traceback.format_exc()}")
        mark_job_failed(db, job_id, exc)
        log_section_end(LogTags.IDARR, f"IDarr File Upload Failed (job_id={job_id})")
    finally:
        remove_job_log_handler(handler_id, job_type="idarr", success=success)
        db.close()


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
            # Same weighting as the standalone sync job: the upload dominates wall time.
            if phase == "listing":
                listing_ratio = min(current / max(2 * max(total, 1), 1), 1.0)
                scaled = int(progress_start + listing_ratio * span * 0.10)
            elif phase == "checking":
                scaled = int(progress_start + span * 0.10 + ratio * span * 0.04)
            elif phase in {"uploading", "uploading_stats", "uploading_file"}:
                scaled = int(progress_start + span * 0.14 + ratio * span * 0.86)
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


def _prune_idarr_duplicates(config_data: dict[str, Any]) -> None:
    """Delete files in config/idarr/duplicates older than `duplicates_retention_days`
    (0 = keep forever). Runs at the start of each IDarr job so the archive of overwritten
    files (from IDarr renames and Artwork Finder overwrites) doesn't grow without bound."""
    try:
        days = int(config_data.get("duplicates_retention_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return
    duplicates_dir = app_settings.config_dir / "idarr" / "duplicates"
    if not duplicates_dir.is_dir():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    removed = 0
    for f in duplicates_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as exc:
            log_warning(LogTags.IDARR, f"Could not delete old duplicate '{f.name}': {exc}")
    if removed:
        log_info(LogTags.IDARR,
                 f"Cleared {removed} duplicate file(s) older than {days} day(s) from {duplicates_dir}")


# IDarr runs must never overlap (shared source folder + cache table). The default job
# queue is single-worker so this never blocks, but max_concurrent_jobs can be raised for
# drive syncs — this lock keeps queued targeted runs serialized regardless.
_IDARR_JOB_LOCK = threading.Lock()


def run_idarr_background_job(job_id: int, config_data: dict[str, Any]) -> None:
    with _IDARR_JOB_LOCK:
        _run_idarr_background_job_locked(job_id, config_data)


def _run_idarr_background_job_locked(job_id: int, config_data: dict[str, Any]) -> None:
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

        _prune_idarr_duplicates(config_data)

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
            healed = int(stats.get("collisions_healed") or 0)
            healed_part = f", healed={healed}" if healed else ""
            log_success(
                LogTags.IDARR,
                (
                    "Summary report: "
                    f"total={int(stats.get('total_assets') or 0)}, "
                    f"processed={int(stats.get('processed_assets') or 0)}, "
                    f"renamed={int(stats.get('files_renamed') or stats.get('renamed') or 0)}, "
                    f"skipped={int(stats.get('skipped') or 0)}, "
                    f"unmatched={unmatched_count}"
                    f"{healed_part}, "
                    f"elapsed={elapsed}"
                ),
            )
        else:
            log_success(LogTags.IDARR, "Native IDarr complete")

        targeted_run = bool(config_data.get("source_filenames"))

        idarr_run = create_idarr_run(
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
        _auto_prune_cache(db, str(config_data.get("scope_token") or "").strip() or None)
        db.commit()

        # A targeted run pushes its own files rather than mirroring the whole scope folder, and
        # does it in a separate job so the upload has its own log and progress bar. Queued before
        # this job reports completion: a caller polling the run result in between would otherwise
        # see a finished rename with no upload pending and stop watching.
        if targeted_run and bool(config_data.get("sync_after_run")) and not bool(config_data.get("dry_run")):
            _targeted_outcomes = [
                row for row in ((result.details or {}).get("targeted_outcomes") or []) if isinstance(row, dict)
            ]
            _ready_count = sum(1 for row in _targeted_outcomes if str(row.get("status") or "") in _TARGETED_UPLOAD_STATUSES)
            _held_count = len(_targeted_outcomes) - _ready_count
            if _ready_count:
                _queue_idarr_targeted_upload(db, config_data, idarr_run, _ready_count, job_id)
            else:
                log_info(
                    LogTags.IDARR,
                    f"Single-item upload: nothing to upload — {_held_count} file(s) held back for a manual match",
                    job_id=job_id,
                )

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

        if not config_data.get("source_filenames"):
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

        # Queue personal drive sync if requested and there is something to upload. Targeted runs
        # already uploaded their own files above, so they never queue the full mirror sync.
        _sync_after = bool(config_data.get("sync_after_run"))
        _force_sync = bool(config_data.get("force_sync_after_run"))
        if _sync_after and not bool(config_data.get("dry_run")) and not targeted_run:
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

    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_section_end(LogTags.IDARR, f"IDarr Job Stopped (job_id={job_id})")
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
            check_cancelled(job_id)
            try:
                normalized_total = max(total, 1)
                ratio = min(max(current / normalized_total, 0.0), 1.0)
                # Bands track wall time, not phase count: listing+checking take seconds,
                # the upload takes tens of minutes, so it gets most of the bar.
                if phase == "listing":
                    listing_ratio = min(current / max(2 * normalized_total, 1), 1.0)
                    scaled_progress = int(20 + listing_ratio * 10)
                elif phase == "checking":
                    scaled_progress = int(30 + ratio * 4)
                elif phase in {"uploading", "uploading_stats", "uploading_file"}:
                    scaled_progress = int(34 + ratio * 65)
                elif phase == "complete":
                    scaled_progress = 99
                else:
                    scaled_progress = max(int(job.progress or 0), 20)
                current_progress = int(job.progress or 0)
                next_progress = max(current_progress, min(scaled_progress, 99))

                if phase == "listing":
                    sync_message = _sanitize_message(
                        message or "Listing remote and local files..."
                    )
                elif phase == "checking":
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

                # uploading_stats is already rate-limited (rclone --stats 1s) and carries the live count,
                # so let it through even when the % hasn't moved; uploading_file fires per file and stays gated.
                if next_progress == last_progress_emit and phase not in {
                    "complete",
                    "checking",
                    "listing",
                    "uploading_stats",
                }:
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
    except JobCancelled:
        finalize_job_cancelled(db, job_id)
        log_info(LogTags.IDARR, f"IDarr personal sync stopped by user (job_id={job_id})")
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
            check_cancelled(job_id)
            scope_start_progress = int((run_num / total_scopes) * 95) + 1
            scope_end_progress = int(((run_num + 1) / total_scopes) * 95)
            scope_label = str(target.get("label") or f"Target {scope_idx + 1}").strip()
            source_dir = str(target.get("source_dir") or "").strip()

            # Visual break between scope runs (tagged so it lands in the IDARR job log too,
            # not just the main log). Skipped before the first — it follows the job banner.
            if run_num > 0:
                log_info(LogTags.IDARR, "─" * 60, job_id=job_id)
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
                "is_asset_drive": bool(target.get("is_asset_drive", False)),
                "is_psd_drive": bool(target.get("is_psd_drive", False)),
            }

            rename_end_progress = (
                int(scope_start_progress + (scope_end_progress - scope_start_progress) * 0.7)
                if sync_after_run else scope_end_progress
            )
            scope_span = max(1, rename_end_progress - scope_start_progress)

            def _make_progress_callback(s_start: int, s_span: int, label: str) -> Any:
                def report_progress(_phase: str, current: int, total: int, message: str) -> None:
                    check_cancelled(job_id)
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

            # Personal sync fires on an explicit opt-in when there's something to push:
            # files renamed, forced, no prior sync, or an image anywhere under the source
            # dir (recursing into asset-drive subfolders) is newer than the last sync.
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
            elif sync_after_run and not dry_run:
                log_info(LogTags.IDARR, f"Inline sync skipped for '{scope_label}': no files changed since last sync", job_id=job_id)
            update_job_state(db, job, progress=scope_end_progress, message=f"Scope '{scope_label}' complete")

        update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100,
                         message=format_complete_message("IDarr workflow step", f"{total_renamed} renamed across {total_scopes} scope(s)"),
                         completed_at=datetime.now(timezone.utc))
        success = True
        log_section_end(LogTags.IDARR, f"IDarr workflow step completed (job_id={job_id}, renamed={total_renamed})")

    except JobCancelled:
        finalize_job_cancelled(db, job_id)
        log_info(LogTags.IDARR, f"IDarr workflow step stopped by user (job_id={job_id})")
        raise
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
