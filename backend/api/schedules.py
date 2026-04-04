from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.schedule import Schedule
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime
import re
import traceback
from core.scheduler import update_schedules, get_schedule_next_run, scheduler
from core.logging import LogTags, log_info, log_warning, log_error

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

SYNC_JOB_TYPES = {"gdrive_sync", "sync"}
IDARR_SCOPE_PATTERN = re.compile(r"^idarr_target_(\d+)$")
VALID_JOB_TYPES = {
    "gdrive_sync",
    "sync",  # backward compatibility
    "poster_workflow",
    "poster_renamer",
    "unmatched_assets",
    "border_replacer",
    "idarr",
    "maker_monitor",
}


class ScheduleResponse(BaseModel):
    id: int
    job_type: str
    name: str
    enabled: bool
    drive_id: Optional[int]
    drive_group: Optional[str]
    schedule_type: str
    schedule_value: Optional[str]
    job_config: Optional[str]
    last_run: Optional[datetime]
    next_run: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ScheduleCreate(BaseModel):
    job_type: str
    name: str
    enabled: bool
    drive_id: Optional[int] = None
    drive_group: Optional[str] = None
    schedule_type: str
    schedule_value: Optional[str] = None
    job_config: Optional[str] = None


class ScheduleUpdate(BaseModel):
    job_type: Optional[str] = None
    name: Optional[str] = None
    enabled: Optional[bool] = None
    drive_id: Optional[int] = None
    drive_group: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule_value: Optional[str] = None
    job_config: Optional[str] = None


@router.get("/", response_model=List[ScheduleResponse])
def get_schedules(job_type: Optional[str] = None, db: Session = Depends(get_db)) -> List[ScheduleResponse]:
    """Get all schedules or filter by job_type"""
    query = db.query(Schedule)
    if job_type:
        if job_type in SYNC_JOB_TYPES:
            query = query.filter(Schedule.job_type.in_(SYNC_JOB_TYPES))
        else:
            query = query.filter(Schedule.job_type == job_type)

    schedules = query.all()

    if scheduler.running:
        for schedule in schedules:
            if schedule.enabled:
                schedule.next_run = get_schedule_next_run(schedule.id)
            else:
                schedule.next_run = None
    else:
        for schedule in schedules:
            schedule.next_run = None

    return schedules


@router.post("/", response_model=ScheduleResponse)
def create_schedule(schedule_data: ScheduleCreate, db: Session = Depends(get_db)) -> ScheduleResponse:
    """Create a new schedule"""
    if schedule_data.job_type not in VALID_JOB_TYPES:
        log_error(LogTags.SCHEDULER, f"Cannot create schedule: Invalid job_type '{schedule_data.job_type}'", job_type=schedule_data.job_type)
        raise HTTPException(status_code=400, detail=f"Invalid job_type. Must be one of: {', '.join(sorted(VALID_JOB_TYPES))}")

    schedule_job_type = schedule_data.job_type
    is_sync_job = schedule_job_type in SYNC_JOB_TYPES

    if schedule_data.drive_id is not None:
        if not is_sync_job:
            log_error(LogTags.SCHEDULER, "Cannot create schedule: drive_id is only valid for drive sync schedules", job_type=schedule_job_type)
            raise HTTPException(status_code=400, detail="drive_id is only valid for gdrive_sync schedules")
        from models.drive import Drive
        drive = db.query(Drive).filter(Drive.id == schedule_data.drive_id).first()
        if not drive:
            log_error(LogTags.SCHEDULER, f"Cannot create schedule: Invalid drive_id {schedule_data.drive_id}", drive_id=schedule_data.drive_id)
            raise HTTPException(status_code=400, detail=f"Drive with ID {schedule_data.drive_id} not found")

    if schedule_data.drive_group is not None:
        if is_sync_job:
            valid_groups = ['CL2K', 'MM2K', 'Custom']
            if schedule_data.drive_group not in valid_groups:
                log_error(LogTags.SCHEDULER, f"Cannot create schedule: Invalid drive_group '{schedule_data.drive_group}'", drive_group=schedule_data.drive_group)
                raise HTTPException(status_code=400, detail=f"Invalid drive_group. Must be one of: {', '.join(valid_groups)}")
        elif schedule_job_type == 'idarr':
            if not IDARR_SCOPE_PATTERN.match(schedule_data.drive_group):
                log_error(LogTags.SCHEDULER, f"Cannot create schedule: Invalid idarr scope '{schedule_data.drive_group}'", drive_group=schedule_data.drive_group)
                raise HTTPException(status_code=400, detail="Invalid idarr scope. Expected format: idarr_target_<index>")
        else:
            log_error(LogTags.SCHEDULER, "Cannot create schedule: drive_group is only valid for drive sync schedules", job_type=schedule_job_type)
            raise HTTPException(status_code=400, detail="drive_group is only valid for gdrive_sync schedules")

    valid_types = ['hourly', 'daily', 'multiple_daily', 'weekly', 'multiple_days', 'monthly', 'cron']
    if schedule_data.schedule_type not in valid_types:
        log_error(LogTags.SCHEDULER, f"Cannot create schedule: Invalid schedule_type '{schedule_data.schedule_type}'", schedule_type=schedule_data.schedule_type)
        raise HTTPException(status_code=400, detail=f"Invalid schedule_type. Must be one of: {', '.join(valid_types)}")

    schedule = Schedule(
        job_type=schedule_job_type,
        name=schedule_data.name,
        enabled=schedule_data.enabled,
        drive_id=schedule_data.drive_id,
        drive_group=schedule_data.drive_group,
        schedule_type=schedule_data.schedule_type,
        schedule_value=schedule_data.schedule_value,
        job_config=schedule_data.job_config,
    )
    db.add(schedule)

    try:
        db.commit()
        db.refresh(schedule)
    except Exception as e:
        db.rollback()
        log_error(LogTags.SCHEDULER, f"Database error creating schedule: {e}\n{traceback.format_exc()}", schedule_name=schedule_data.name)
        raise HTTPException(status_code=500, detail=f"Failed to create schedule: {str(e)}")

    try:
        update_schedules()
    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Failed to update scheduler after creating schedule: {e}\n{traceback.format_exc()}", schedule_id=schedule.id)
        log_warning(LogTags.SCHEDULER, f"Schedule '{schedule.name}' created but may not run until scheduler is restarted", schedule_id=schedule.id)

    try:
        next_run = get_schedule_next_run(schedule.id) if schedule.enabled else None
        schedule.next_run = next_run
    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Failed to get next run time for schedule {schedule.id}: {e}", schedule_id=schedule.id)
        next_run = None
        schedule.next_run = None

    if next_run:
        log_info(LogTags.SCHEDULER, f"Created schedule '{schedule.name}': Next run at {next_run.strftime('%H:%M %Z')}")
    else:
        log_info(LogTags.SCHEDULER, f"Created schedule '{schedule.name}' (disabled or no next run)")

    return schedule


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule(schedule_id: int, db: Session = Depends(get_db)) -> ScheduleResponse:
    """Get a specific schedule by ID"""
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if scheduler.running:
        if schedule.enabled:
            try:
                schedule.next_run = get_schedule_next_run(schedule.id)
            except Exception as e:
                log_error(LogTags.SCHEDULER, f"Failed to get next run time for schedule {schedule.id}: {e}", schedule_id=schedule.id)
                schedule.next_run = None
        else:
            schedule.next_run = None
    else:
        schedule.next_run = None

    return schedule


@router.put("/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(
    schedule_id: int,
    schedule_update: ScheduleUpdate,
    db: Session = Depends(get_db)
) -> ScheduleResponse:
    """Update an existing schedule"""
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    fields_set = schedule_update.model_fields_set
    effective_job_type = schedule.job_type

    if "job_type" in fields_set:
        if schedule_update.job_type is None:
            log_error(LogTags.SCHEDULER, f"Cannot update schedule {schedule_id}: job_type cannot be null", schedule_id=schedule_id)
            raise HTTPException(status_code=400, detail="job_type cannot be null")
        if schedule_update.job_type not in VALID_JOB_TYPES:
            log_error(LogTags.SCHEDULER, f"Cannot update schedule {schedule_id}: Invalid job_type '{schedule_update.job_type}'", schedule_id=schedule_id, job_type=schedule_update.job_type)
            raise HTTPException(status_code=400, detail=f"Invalid job_type. Must be one of: {', '.join(sorted(VALID_JOB_TYPES))}")
        effective_job_type = schedule_update.job_type

    is_sync_job = effective_job_type in SYNC_JOB_TYPES

    if "drive_id" in fields_set and schedule_update.drive_id is not None:
        if not is_sync_job:
            log_error(LogTags.SCHEDULER, "Cannot update schedule: drive_id is only valid for drive sync schedules", schedule_id=schedule_id, job_type=effective_job_type)
            raise HTTPException(status_code=400, detail="drive_id is only valid for gdrive_sync schedules")
        from models.drive import Drive
        drive = db.query(Drive).filter(Drive.id == schedule_update.drive_id).first()
        if not drive:
            log_error(LogTags.SCHEDULER, f"Cannot update schedule {schedule_id}: Invalid drive_id {schedule_update.drive_id}", schedule_id=schedule_id, drive_id=schedule_update.drive_id)
            raise HTTPException(status_code=400, detail=f"Drive with ID {schedule_update.drive_id} not found")

    if "drive_group" in fields_set and schedule_update.drive_group is not None:
        if is_sync_job:
            valid_groups = ['CL2K', 'MM2K', 'Custom']
            if schedule_update.drive_group not in valid_groups:
                log_error(LogTags.SCHEDULER, f"Cannot update schedule {schedule_id}: Invalid drive_group '{schedule_update.drive_group}'", schedule_id=schedule_id, drive_group=schedule_update.drive_group)
                raise HTTPException(status_code=400, detail=f"Invalid drive_group. Must be one of: {', '.join(valid_groups)}")
        elif effective_job_type == 'idarr':
            if not IDARR_SCOPE_PATTERN.match(schedule_update.drive_group):
                log_error(LogTags.SCHEDULER, f"Cannot update schedule {schedule_id}: Invalid idarr scope '{schedule_update.drive_group}'", schedule_id=schedule_id, drive_group=schedule_update.drive_group)
                raise HTTPException(status_code=400, detail="Invalid idarr scope. Expected format: idarr_target_<index>")
        else:
            log_error(LogTags.SCHEDULER, "Cannot update schedule: drive_group is only valid for drive sync schedules", schedule_id=schedule_id, job_type=effective_job_type)
            raise HTTPException(status_code=400, detail="drive_group is only valid for gdrive_sync schedules")

    if "schedule_type" in fields_set and schedule_update.schedule_type is not None:
        valid_types = ['hourly', 'daily', 'multiple_daily', 'weekly', 'multiple_days', 'monthly', 'cron']
        if schedule_update.schedule_type not in valid_types:
            log_error(LogTags.SCHEDULER, f"Cannot update schedule {schedule_id}: Invalid schedule_type '{schedule_update.schedule_type}'", schedule_id=schedule_id, schedule_type=schedule_update.schedule_type)
            raise HTTPException(status_code=400, detail=f"Invalid schedule_type. Must be one of: {', '.join(valid_types)}")

    if ("drive_id" in fields_set or "drive_group" in fields_set or "job_type" in fields_set) and not is_sync_job and effective_job_type != 'idarr':
        if ("drive_id" in fields_set and schedule_update.drive_id is not None) or ("drive_group" in fields_set and schedule_update.drive_group is not None):
            log_error(LogTags.SCHEDULER, "Cannot update schedule: drive targeting fields are only valid for drive sync schedules", schedule_id=schedule_id, job_type=effective_job_type)
            raise HTTPException(status_code=400, detail="drive_id and drive_group are only valid for gdrive_sync schedules")

    if "job_type" in fields_set:
        schedule.job_type = schedule_update.job_type
    if "name" in fields_set:
        schedule.name = schedule_update.name
    if "enabled" in fields_set:
        schedule.enabled = schedule_update.enabled
    if "drive_id" in fields_set:
        schedule.drive_id = schedule_update.drive_id
    if "drive_group" in fields_set:
        schedule.drive_group = schedule_update.drive_group
    if "schedule_type" in fields_set:
        schedule.schedule_type = schedule_update.schedule_type
    if "schedule_value" in fields_set:
        schedule.schedule_value = schedule_update.schedule_value
    if "job_config" in fields_set:
        schedule.job_config = schedule_update.job_config

    try:
        db.commit()
        db.refresh(schedule)
    except Exception as e:
        db.rollback()
        log_error(LogTags.SCHEDULER, f"Database error updating schedule {schedule_id}: {e}\n{traceback.format_exc()}", schedule_id=schedule_id)
        raise HTTPException(status_code=500, detail=f"Failed to update schedule: {str(e)}")

    try:
        update_schedules()
    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Failed to update scheduler after updating schedule: {e}\n{traceback.format_exc()}", schedule_id=schedule.id)
        log_warning(LogTags.SCHEDULER, f"Schedule '{schedule.name}' updated but changes may not take effect until scheduler is restarted", schedule_id=schedule.id)

    try:
        next_run = get_schedule_next_run(schedule.id) if schedule.enabled else None
        schedule.next_run = next_run
    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Failed to get next run time for schedule {schedule.id}: {e}", schedule_id=schedule.id)
        next_run = None
        schedule.next_run = None

    if next_run:
        log_info(LogTags.SCHEDULER, f"Saved schedule '{schedule.name}': Next run at {next_run.strftime('%H:%M %Z')}")
    else:
        log_info(LogTags.SCHEDULER, f"Saved schedule '{schedule.name}' (disabled or no next run)")

    return schedule


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    """Delete a schedule"""
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    schedule_name = schedule.name
    schedule_id_for_log = schedule.id
    db.delete(schedule)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log_error(LogTags.SCHEDULER, f"Database error deleting schedule {schedule_id}: {e}\n{traceback.format_exc()}", schedule_id=schedule_id)
        raise HTTPException(status_code=500, detail=f"Failed to delete schedule: {str(e)}")

    log_info(LogTags.SCHEDULER, f"Deleted schedule: '{schedule_name}'")

    try:
        update_schedules()
    except Exception as e:
        log_error(LogTags.SCHEDULER, f"Failed to update scheduler after deleting schedule: {e}\n{traceback.format_exc()}", schedule_id=schedule_id_for_log)
        log_warning(LogTags.SCHEDULER, f"Schedule '{schedule_name}' deleted from database but may still be in scheduler until restart", schedule_id=schedule_id_for_log)

    return {"message": "Schedule deleted successfully"}
