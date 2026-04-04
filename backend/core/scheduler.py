from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from sqlalchemy.orm import Session
import json
from typing import Optional, Callable, Any
from database import SessionLocal
from models.schedule import Schedule
from models.job import (
    Job,
    JOB_TYPE_POSTER_WORKFLOW,
    JOB_TYPE_POSTER_RENAMER,
    JOB_TYPE_UNMATCHED_DETECTION,
    JOB_TYPE_BORDER_REPLACER,
    JOB_TYPE_GDRIVE_SYNC,
    JOB_TYPE_IDARR,
    JOB_STATUS_PENDING,
    JOB_STATUSES_ACTIVE,
    job_type_sync_all,
    job_type_sync_group,
)
from models.drive import Drive
from models.idarr import resolve_idarr_scope_token
from models.setting import get_setting_value
from core.config import settings
from core.job_queue import job_queue
from core.logging import (
    LogTags,
    log_info, log_error, log_debug, log_warning,
)
from modules.sync import run_sync_one_job, run_sync_all_job, run_sync_group_job
from modules.flow import run_flow_background_job
from modules.renamer import run_rename_background_job
from modules.unmatched import run_unmatched_detection_background_job
from modules.border import run_border_replacer_background_job
from modules.idarr import run_idarr_background_job
from api.maker_tools import run_maker_monitor_scan_for_schedule
from tzlocal import get_localzone

# Create scheduler instance
scheduler = BackgroundScheduler(
    jobstores={
        'default': SQLAlchemyJobStore(url=settings.database_url)
    },
    executors={
        'default': ThreadPoolExecutor(10)
    },
    job_defaults={
        'coalesce': False,
        'max_instances': 3
    },
    timezone=get_localzone()
)


def _to_apscheduler_day_of_week(day_value: str) -> str:
    """Convert UI weekday tokens to APScheduler-compatible weekday expressions.

    UI schedule values use Sunday-first numeric indexing (0=Sun ... 6=Sat),
    while APScheduler treats numeric weekday values as Monday-first.
    Converting numeric values to weekday names avoids off-by-one day shifts.
    """
    normalized = str(day_value).strip().lower()
    day_names = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

    try:
        day_index = int(normalized)
    except ValueError:
        return normalized

    if 0 <= day_index < len(day_names):
        return day_names[day_index]

    return normalized


def _create_pending_job(db: Session, job_type: str) -> Job:
    job = Job(job_type=job_type, status=JOB_STATUS_PENDING)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _queue_pending_job(
    db: Session,
    job_type: str,
    runner: Callable[..., Any],
    args_builder: Optional[Callable[[Job], tuple[Any, ...]]] = None,
) -> Job:
    job = _create_pending_job(db, job_type)
    runner_args = args_builder(job) if args_builder else (job.id,)
    job_queue.submit(runner, job.id, *runner_args)
    return job


def _run_scheduled_operation(error_message: str, operation: Callable[[Session], None]) -> None:
    db = SessionLocal()
    try:
        operation(db)
    except Exception as e:
        import traceback
        log_error(LogTags.SCHEDULER, f"{error_message}: {str(e)}\n{traceback.format_exc()}")
        raise
    finally:
        db.close()


def sync_one_drive_job(drive_id: int, job_id: int) -> dict:
    """
    Sync a single drive. Used for both scheduled and manual syncs.
    Called by APScheduler for scheduled jobs, or as background task for manual syncs.
    """
    return run_sync_one_job(drive_id, job_id)

def sync_all_drives_job(job_id: int) -> dict:
    """
    Sync all subscribed drives. Used for both scheduled and manual syncs.
    Called by APScheduler for scheduled jobs, or as background task for manual syncs.
    
    Args:
        job_id: ID of the existing job record to track progress
    """
    return run_sync_all_job(job_id)

def sync_drive_group_job(drive_group: str) -> None:
    """
    Background job to sync all drives of a specific group (CL2K, MM2K, or Custom).
    Called by APScheduler for scheduled tasks.
    
    Args:
        drive_group: The group to sync - 'CL2K', 'MM2K', or 'Custom'
    """
    return run_sync_group_job(drive_group)


def sync_drive_group_for_schedule(drive_group: str) -> None:
    """
    Wrapper function for scheduled group syncs.
    Creates a job record and submits execution to the shared queue.
    """
    _run_scheduled_operation(
        f"Scheduled {drive_group} group sync failed",
        lambda db: _queue_pending_job(
            db,
            job_type_sync_group(drive_group),
            run_sync_group_job,
            args_builder=lambda job: (drive_group, job.id),
        ),
    )


def run_workflow_for_schedule() -> None:
    """Wrapper for scheduled poster workflow runs."""
    _run_scheduled_operation(
        "Scheduled workflow failed",
        lambda db: _queue_pending_job(
            db,
            JOB_TYPE_POSTER_WORKFLOW,
            run_flow_background_job,
            args_builder=lambda job: (job.id, False),
        ),
    )


def run_poster_rename_for_schedule() -> None:
    """Wrapper for scheduled Poster Renamer runs."""
    def _submit(db: Session) -> None:
        destination = get_setting_value(db, "poster_destination", "/posters/assets")
        action_type = get_setting_value(db, "poster_action_type", "copy")
        asset_folders_value = get_setting_value(db, "poster_asset_folders", "true")
        asset_folders = str(asset_folders_value).lower() == "true"

        config_data = {
            "destination": destination,
            "action_type": action_type,
            "asset_folders": asset_folders,
            "dry_run": False,
            "match_threshold": 0.8,
        }

        _queue_pending_job(
            db,
            JOB_TYPE_POSTER_RENAMER,
            run_rename_background_job,
            args_builder=lambda job: (job.id, config_data),
        )

    _run_scheduled_operation("Scheduled Poster Renamer failed", _submit)


def run_unmatched_assets_for_schedule() -> None:
    """Wrapper for scheduled unmatched assets detection runs."""
    _run_scheduled_operation(
        "Scheduled unmatched detection failed",
        lambda db: _queue_pending_job(
            db,
            JOB_TYPE_UNMATCHED_DETECTION,
            run_unmatched_detection_background_job,
            args_builder=lambda job: (job.id,),
        ),
    )


def run_border_replacer_for_schedule() -> None:
    """Wrapper for scheduled border replacer runs."""
    def _submit(db: Session) -> None:
        mode = get_setting_value(db, "border_replacer_mode", "incremental")
        if mode not in ["full", "incremental"]:
            mode = "incremental"

        _queue_pending_job(
            db,
            JOB_TYPE_BORDER_REPLACER,
            run_border_replacer_background_job,
            args_builder=lambda job: (job.id, False, mode),
        )

    _run_scheduled_operation("Scheduled border replacer failed", _submit)


def run_idarr_for_schedule(idarr_scope: Optional[str] = None, sync_after_run: bool = False) -> None:
    """Wrapper for scheduled IDarr runs."""

    def _submit(db: Session) -> None:
        existing_idarr_job = (
            db.query(Job)
            .filter(
                Job.job_type == JOB_TYPE_IDARR,
                Job.status.in_(JOB_STATUSES_ACTIVE),
            )
            .order_by(Job.started_at.desc(), Job.id.desc())
            .first()
        )
        if existing_idarr_job:
            log_warning(
                LogTags.SCHEDULER,
                f"Skipping scheduled IDarr run: job {existing_idarr_job.id} is already {existing_idarr_job.status}",
                existing_job_id=existing_idarr_job.id,
                status=existing_idarr_job.status,
            )
            return

        config_raw = get_setting_value(db, "maker_tools_idarr_config", "")
        if not str(config_raw or "").strip():
            log_warning(LogTags.SCHEDULER, "Skipping scheduled IDarr run: maker_tools_idarr_config is not configured")
            return

        try:
            config_data = json.loads(config_raw)
        except Exception:
            log_warning(LogTags.SCHEDULER, "Skipping scheduled IDarr run: maker_tools_idarr_config is invalid JSON")
            return

        if not isinstance(config_data, dict):
            log_warning(LogTags.SCHEDULER, "Skipping scheduled IDarr run: maker_tools_idarr_config payload is invalid")
            return

        tmdb_api_key = str(config_data.get("tmdb_api_key") or "").strip()
        if not tmdb_api_key:
            log_warning(LogTags.SCHEDULER, "Skipping scheduled IDarr run: tmdb_api_key is missing")
            return

        raw_targets = config_data.get("sync_targets")
        sync_targets = [item for item in raw_targets if isinstance(item, dict)] if isinstance(raw_targets, list) else []
        selected_target: Optional[dict[str, Any]] = None
        selected_target_index = 0

        requested_index: Optional[int] = None
        if idarr_scope:
            scope_value = str(idarr_scope).strip()
            if scope_value.startswith("idarr_target_"):
                try:
                    requested_index = int(scope_value[len("idarr_target_"):])
                except ValueError:
                    requested_index = None

        if requested_index is not None and 0 <= requested_index < len(sync_targets):
            candidate_target = sync_targets[requested_index]
            candidate_source_dir = str(candidate_target.get("source_dir") or "").strip()
            if candidate_source_dir:
                selected_target = candidate_target
                selected_target_index = requested_index
            else:
                log_warning(
                    LogTags.SCHEDULER,
                    f"IDarr schedule scope target {requested_index} has no source_dir; falling back to first valid target",
                    requested_index=requested_index,
                )

        if not selected_target:
            for index, target in enumerate(sync_targets):
                source_dir = str(target.get("source_dir") or "").strip()
                if source_dir:
                    selected_target = target
                    selected_target_index = index
                    break

        if not selected_target:
            log_warning(LogTags.SCHEDULER, "Skipping scheduled IDarr run: no sync target with source_dir configured")
            return

        source_dir = str(selected_target.get("source_dir") or "").strip()
        scheduled_config_data = dict(config_data)
        scheduled_config_data["dry_run"] = False
        scheduled_config_data["source_dir"] = source_dir
        scheduled_config_data["sync_target_index"] = selected_target_index
        scheduled_config_data["scope_token"] = resolve_idarr_scope_token(selected_target, selected_target_index)
        scheduled_config_data["sync_after_run"] = sync_after_run

        _queue_pending_job(
            db,
            JOB_TYPE_IDARR,
            run_idarr_background_job,
            args_builder=lambda job: (job.id, scheduled_config_data),
        )

    _run_scheduled_operation("Scheduled IDarr failed", _submit)


def run_maker_monitor_for_schedule() -> None:
    """Wrapper for scheduled Maker Monitor runs."""
    _run_scheduled_operation(
        "Scheduled Maker Monitor failed",
        lambda db: run_maker_monitor_scan_for_schedule(db),
    )


def sync_one_drive_for_schedule(drive_id: int) -> None:
    """
    Wrapper function for scheduled single-drive syncs.
    Creates a job record and calls sync_one_drive_job.
    """
    _run_scheduled_operation(
        "Scheduled single drive sync failed",
        lambda db: _queue_pending_job(
            db,
            JOB_TYPE_GDRIVE_SYNC,
            sync_one_drive_job,
            args_builder=lambda job: (drive_id, job.id),
        ),
    )

def sync_all_drives_for_schedule() -> None:
    """
    Wrapper function for scheduled all-drives syncs.
    Creates a job record and calls sync_all_drives_job.
    """
    def _submit(db: Session) -> None:
        drives_count = db.query(Drive).filter(Drive.subscribed == True).count()
        _queue_pending_job(
            db,
            job_type_sync_all(drives_count),
            sync_all_drives_job,
            args_builder=lambda job: (job.id,),
        )

    _run_scheduled_operation("Scheduled all drives sync failed", _submit)

def update_schedules() -> None:
    """
    Update APScheduler jobs from database schedules.
    Call this whenever schedules are created/updated/deleted.
    """
    db = SessionLocal()
    try:
        # Remove all existing jobs
        for job in scheduler.get_jobs():
            scheduler.remove_job(job.id)
        
        # Load enabled schedules from database
        schedules = db.query(Schedule).filter(Schedule.enabled == True).all()
        
        for schedule in schedules:
            job_id = f'schedule_{schedule.id}'

            if schedule.job_type in ['gdrive_sync', 'sync']:
                if schedule.drive_id:
                    job_func = sync_one_drive_for_schedule
                    job_args = [schedule.drive_id]
                elif schedule.drive_group:
                    job_func = sync_drive_group_for_schedule
                    job_args = [schedule.drive_group]
                else:
                    job_func = sync_all_drives_for_schedule
                    job_args = []
            elif schedule.job_type == 'poster_workflow':
                job_func = run_workflow_for_schedule
                job_args = []
            elif schedule.job_type == 'poster_renamer':
                job_func = run_poster_rename_for_schedule
                job_args = []
            elif schedule.job_type == 'unmatched_assets':
                job_func = run_unmatched_assets_for_schedule
                job_args = []
            elif schedule.job_type == 'border_replacer':
                job_func = run_border_replacer_for_schedule
                job_args = []
            elif schedule.job_type == 'idarr':
                job_func = run_idarr_for_schedule
                schedule_sync_after = False
                if schedule.job_config:
                    try:
                        jc = json.loads(schedule.job_config)
                        schedule_sync_after = bool(jc.get("sync_after_run", False))
                    except Exception:
                        pass
                job_args = [schedule.drive_group, schedule_sync_after]
            elif schedule.job_type == 'maker_monitor':
                job_func = run_maker_monitor_for_schedule
                job_args = []
            else:
                log_warning(
                    LogTags.SCHEDULER,
                    f"Skipping unsupported schedule job_type '{schedule.job_type}'",
                    schedule_id=schedule.id,
                    job_type=schedule.job_type,
                )
                continue

                # Convert schedule to APScheduler trigger
            if schedule.schedule_type == 'hourly':
                # schedule_value contains minute offset (0-59)
                minute = int(schedule.schedule_value) if schedule.schedule_value else 0
                job = scheduler.add_job(
                    job_func,
                    'cron',
                    args=job_args,
                    minute=minute,
                    id=job_id,
                    replace_existing=True
                )
                log_debug(LogTags.SCHEDULER, f"Loaded schedule '{schedule.name}': Next run at {job.next_run_time}")

            elif schedule.schedule_type == 'daily' and schedule.schedule_value:
                # schedule_value format: "14:30"
                hour, minute = map(int, schedule.schedule_value.split(':'))
                job = scheduler.add_job(
                    job_func,
                    'cron',
                    args=job_args,
                    hour=hour,
                    minute=minute,
                    id=job_id,
                    replace_existing=True
                )
                log_debug(LogTags.SCHEDULER, f"Loaded schedule '{schedule.name}': Next run at {job.next_run_time}")

            elif schedule.schedule_type == 'multiple_daily' and schedule.schedule_value:
                # schedule_value format: "07:00,19:00" (comma-separated times)
                # Create a separate APScheduler job for each time to avoid cartesian product issues
                times = schedule.schedule_value.split(',')
                next_runs = []
                for idx, time_str in enumerate(times):
                    h, m = map(int, time_str.strip().split(':'))
                    sub_job_id = f'{job_id}_time{idx}'
                    job = scheduler.add_job(
                        job_func,
                        'cron',
                        args=job_args,
                        hour=h,
                        minute=m,
                        id=sub_job_id,
                        replace_existing=True
                    )
                    next_runs.append(job.next_run_time)
                earliest_run = min(next_runs) if next_runs else None
                if earliest_run:
                    log_debug(LogTags.SCHEDULER, f"Loaded schedule '{schedule.name}': Next run at {earliest_run}")

            elif schedule.schedule_type == 'weekly' and schedule.schedule_value:
                # schedule_value format: "1:14:30" or "1:14:30,18:30" (day:times where day is 0-6)
                # Create separate jobs for each time to avoid Cartesian product
                parts = schedule.schedule_value.split(':', 1)
                day = _to_apscheduler_day_of_week(parts[0])
                times = parts[1].split(',') if len(parts) > 1 else []
                next_runs = []

                for idx, time_str in enumerate(times):
                    time_parts = time_str.strip().split(':')
                    if len(time_parts) >= 2:
                        h = int(time_parts[0])
                        m = int(time_parts[1])
                        sub_job_id = f'{job_id}_time{idx}' if len(times) > 1 else job_id
                        job = scheduler.add_job(
                            job_func,
                            'cron',
                            args=job_args,
                            hour=h,
                            minute=m,
                            day_of_week=day,
                            id=sub_job_id,
                            replace_existing=True
                        )
                        next_runs.append(job.next_run_time)
                earliest_run = min(next_runs) if next_runs else None
                if earliest_run:
                    log_debug(LogTags.SCHEDULER, f"Loaded schedule '{schedule.name}': Next run at {earliest_run}")

            elif schedule.schedule_type == 'monthly' and schedule.schedule_value:
                # schedule_value format: "15:14:30" or "15:14:30,18:30" (day:times)
                # Create separate jobs for each time to avoid Cartesian product
                parts = schedule.schedule_value.split(':', 1)
                day_of_month = int(parts[0])
                times = parts[1].split(',') if len(parts) > 1 else []
                next_runs = []

                for idx, time_str in enumerate(times):
                    time_parts = time_str.strip().split(':')
                    if len(time_parts) >= 2:
                        h = int(time_parts[0])
                        m = int(time_parts[1])
                        sub_job_id = f'{job_id}_time{idx}' if len(times) > 1 else job_id
                        job = scheduler.add_job(
                            job_func,
                            'cron',
                            args=job_args,
                            hour=h,
                            minute=m,
                            day=day_of_month,
                            id=sub_job_id,
                            replace_existing=True
                        )
                        next_runs.append(job.next_run_time)
                earliest_run = min(next_runs) if next_runs else None
                if earliest_run:
                    log_debug(LogTags.SCHEDULER, f"Loaded schedule '{schedule.name}': Next run at {earliest_run}")

            elif schedule.schedule_type == 'multiple_days' and schedule.schedule_value:
                # schedule_value format: "1:07:00,19:00|5:09:00" (day:times|day:times)
                # Create separate APScheduler jobs for each day+time combination
                day_schedules = schedule.schedule_value.split('|')
                job_idx = 0
                next_runs = []

                for day_sched in day_schedules:
                    if ':' not in day_sched:
                        continue
                    parts = day_sched.split(':', 1)
                    day = _to_apscheduler_day_of_week(parts[0])
                    times = parts[1].split(',') if len(parts) > 1 else []

                    for time_str in times:
                        if time_str.strip():
                            time_parts = time_str.strip().split(':')
                            if len(time_parts) >= 2:
                                h = int(time_parts[0])
                                m = int(time_parts[1])
                                sub_job_id = f'{job_id}_slot{job_idx}'
                                job = scheduler.add_job(
                                    job_func,
                                    'cron',
                                    args=job_args,
                                    hour=h,
                                    minute=m,
                                    day_of_week=day,
                                    id=sub_job_id,
                                    replace_existing=True
                                )
                                next_runs.append(job.next_run_time)
                                job_idx += 1
                earliest_run = min(next_runs) if next_runs else None
                if earliest_run:
                    log_debug(LogTags.SCHEDULER, f"Loaded schedule '{schedule.name}': Next run at {earliest_run}")

            elif schedule.schedule_type == 'cron' and schedule.schedule_value:
                # Raw cron expression - user has full control
                # Format: "minute hour day month day_of_week"
                # Note: Comma-separated values will create Cartesian products (standard cron behavior)
                parts = schedule.schedule_value.split()
                if len(parts) >= 5:
                    job = scheduler.add_job(
                        job_func,
                        'cron',
                        args=job_args,
                        minute=parts[0],
                        hour=parts[1],
                        day=parts[2],
                        month=parts[3],
                        day_of_week=parts[4],
                        id=job_id,
                        replace_existing=True
                    )
                    log_debug(LogTags.SCHEDULER, f"Loaded schedule '{schedule.name}': Next run at {job.next_run_time}")
        
        log_debug(LogTags.SCHEDULER, f"Reloaded {len(schedules)} enabled schedule(s)")
        
    except Exception as e:
        import traceback
        log_error(LogTags.SCHEDULER, f"Failed to update schedules: {str(e)}\n{traceback.format_exc()}")
        raise
    finally:
        db.close()

def start_scheduler() -> None:
    """Start the APScheduler background scheduler"""
    if not scheduler.running:
        scheduler.start()
        log_info(LogTags.SCHEDULER, "Started APScheduler")
        # Load schedules from database
        update_schedules()

def stop_scheduler() -> None:
    """Stop the APScheduler background scheduler"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log_info(LogTags.SCHEDULER, "Stopped APScheduler")

def get_schedule_next_run(schedule_id: int) -> Optional[object]:
    """
    Get the next run time for a specific schedule.
    Returns the next run time or None if schedule not found in scheduler.
    """
    try:
        jobs = scheduler.get_jobs()
        schedule_job_id = f'schedule_{schedule_id}'
        
        # Find all jobs related to this schedule (could be multiple for multi-time schedules)
        next_runs = []
        for job in jobs:
            if job.id == schedule_job_id or job.id.startswith(f'{schedule_job_id}_'):
                if job.next_run_time:
                    next_runs.append(job.next_run_time)
        
        # Return the earliest next run time
        return min(next_runs) if next_runs else None
    except Exception as e:
        import traceback
        log_error(LogTags.SCHEDULER, f"Failed to get next run time for schedule {schedule_id}: {str(e)}\n{traceback.format_exc()}")
        return None
