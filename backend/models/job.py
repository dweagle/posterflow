from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from datetime import datetime, timezone, timedelta
from typing import Optional
from database import Base


JOB_TYPE_POSTER_WORKFLOW = "Poster Workflow"
JOB_TYPE_POSTER_RENAMER = "Poster Renamer"
JOB_TYPE_BORDER_REPLACER = "Border Replacer"
JOB_TYPE_UNMATCHED_DETECTION = "Unmatched Detection"
JOB_TYPE_IDARR = "idarr"
JOB_TYPE_MAKER_MONITOR = "maker_monitor"
JOB_TYPE_PLEX_UPLOAD = "Plex Upload"
JOB_TYPE_PLEX_UPLOAD_SINGLE = "Plex Upload Single"
JOB_TYPE_PLEX_UPLOAD_WEBHOOK = "Plex Upload Webhook"
JOB_TYPE_GDRIVE_SYNC = "gdrive_sync"
JOB_TYPE_SYNC_ONE_PREFIX = "Sync: "
JOB_TYPE_SYNC_ALL_PREFIX = "Sync All"

JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

JOB_STATUSES_ACTIVE = [JOB_STATUS_RUNNING, JOB_STATUS_PENDING]
JOB_STATUSES_RECENT_TERMINAL = [JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED]

JOB_HISTORY_KEEP_COMPLETED_DAYS = 30
JOB_HISTORY_KEEP_FAILED_DAYS = 14
JOB_HISTORY_MAX_PER_TYPE = 750


def job_type_sync_one(drive_name: str) -> str:
    return f"{JOB_TYPE_SYNC_ONE_PREFIX}{drive_name}"


def job_type_sync_all(drives_count: Optional[int] = None) -> str:
    if drives_count and drives_count > 0:
        return f"{JOB_TYPE_SYNC_ALL_PREFIX} ({drives_count} drives)"
    return JOB_TYPE_SYNC_ALL_PREFIX


def job_type_sync_group(drive_group: str) -> str:
    return f"Sync Group ({drive_group})"


def format_start_message(action: str, *, dry_run: bool = False, qualifier: Optional[str] = None) -> str:
    """Build a consistent job start message."""
    message = f"Starting {action}"
    if qualifier:
        message = f"{message} {qualifier}"
    if dry_run:
        message = f"{message} (dry run)"
    return f"{message}..."


def format_complete_message(action: str, details: Optional[str] = None) -> str:
    """Build a consistent job completion message."""
    message = f"{action} complete"
    if details:
        message = f"{message}: {details}"
    return message


def format_workflow_step(step: int, total: int, text: str) -> str:
    """Build a workflow step status message."""
    return f"Step {step}/{total}: {text}"


def format_workflow_step_complete(step: int, total: int, text: str) -> str:
    """Build a workflow step completion status message."""
    return f"Step {step}/{total} complete: {text}"

class Job(Base):
    """
    Tracks background job status (sync, matching, processing).
    Used to display progress in dashboard.
    """
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String, nullable=False)  # "sync", "match", "process"
    status = Column(String, nullable=False)  # "pending", "running", "completed", "failed"
    progress = Column(Integer, default=0)  # 0-100 percentage
    message = Column(Text, nullable=True)  # Current status message
    error = Column(Text, nullable=True)  # Error details if failed
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Job(type='{self.job_type}', status='{self.status}', progress={self.progress})>"


def prune_job_history(
    db: Session,
    *,
    keep_completed_days: int = JOB_HISTORY_KEEP_COMPLETED_DAYS,
    keep_failed_days: int = JOB_HISTORY_KEEP_FAILED_DAYS,
    max_per_job_type: int = JOB_HISTORY_MAX_PER_TYPE,
) -> int:
    """Prune terminal job history by age and per-job-type cap.

    Rules:
    - Completed jobs older than keep_completed_days are deleted.
    - Failed jobs older than keep_failed_days are deleted.
    - Remaining terminal rows are capped to max_per_job_type per exact job_type.
    """
    now = datetime.now(timezone.utc)
    completed_cutoff = now - timedelta(days=max(0, int(keep_completed_days)))
    failed_cutoff = now - timedelta(days=max(0, int(keep_failed_days)))

    deleted_total = 0

    deleted_total += db.query(Job).filter(
        Job.status == JOB_STATUS_COMPLETED,
        Job.completed_at.isnot(None),
        Job.completed_at < completed_cutoff,
    ).delete(synchronize_session=False)

    deleted_total += db.query(Job).filter(
        Job.status == JOB_STATUS_FAILED,
        Job.completed_at.isnot(None),
        Job.completed_at < failed_cutoff,
    ).delete(synchronize_session=False)

    # Cancelled jobs follow the same short retention as failed jobs.
    deleted_total += db.query(Job).filter(
        Job.status == JOB_STATUS_CANCELLED,
        Job.completed_at.isnot(None),
        Job.completed_at < failed_cutoff,
    ).delete(synchronize_session=False)

    # Also prune legacy failed jobs where completed_at was never set
    deleted_total += db.query(Job).filter(
        Job.status == JOB_STATUS_FAILED,
        Job.completed_at.is_(None),
        Job.started_at < failed_cutoff,
    ).delete(synchronize_session=False)

    per_type_limit = max(1, int(max_per_job_type))
    job_types = [
        row[0]
        for row in db.query(Job.job_type)
        .filter(Job.status.in_(JOB_STATUSES_RECENT_TERMINAL))
        .distinct()
        .all()
        if row[0]
    ]

    for job_type in job_types:
        keep_ids = [
            row[0]
            for row in db.query(Job.id)
            .filter(Job.job_type == job_type, Job.status.in_(JOB_STATUSES_RECENT_TERMINAL))
            .order_by(Job.completed_at.desc().nullslast(), Job.started_at.desc().nullslast(), Job.id.desc())
            .limit(per_type_limit)
            .all()
        ]
        if not keep_ids:
            continue

        deleted_total += db.query(Job).filter(
            Job.job_type == job_type,
            Job.status.in_(JOB_STATUSES_RECENT_TERMINAL),
            ~Job.id.in_(keep_ids),
        ).delete(synchronize_session=False)

    if deleted_total > 0:
        db.commit()

    return int(deleted_total)


def update_job_state(
    db: Session,
    job: Job,
    *,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    completed_at: Optional[datetime] = None,
) -> None:
    """Update job fields and persist changes."""
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    if message is not None:
        job.message = message
    if error is not None:
        job.error = error
    if completed_at is not None:
        job.completed_at = completed_at
    db.commit()

    should_prune = False
    if status is not None and status in JOB_STATUSES_RECENT_TERMINAL:
        should_prune = True
    elif completed_at is not None:
        should_prune = True

    if should_prune:
        prune_job_history(db)


def mark_job_failed(db: Session, job_id: int, error: Exception | str) -> None:
    """Mark a job as failed and set completion timestamp."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job:
        update_job_state(
            db,
            job,
            status=JOB_STATUS_FAILED,
            error=str(error),
            completed_at=datetime.now(timezone.utc),
        )


def finalize_job_cancelled(db: Session, job_id: int, message: str = "Stopped by user") -> None:
    """Mark a job as cancelled (user-requested stop) and set completion timestamp."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job:
        update_job_state(
            db,
            job,
            status=JOB_STATUS_CANCELLED,
            message=message,
            completed_at=datetime.now(timezone.utc),
        )


def create_job(
    db: Session,
    job_type: str,
    message: str,
    status: str = JOB_STATUS_PENDING,
    progress: int = 0,
) -> Job:
    """Create and persist a job record."""
    job = Job(
        job_type=job_type,
        status=status,
        progress=progress,
        message=message,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job