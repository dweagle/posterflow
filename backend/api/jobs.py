from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from typing import Any, Callable, Dict, List
from pydantic import BaseModel, ConfigDict
from datetime import datetime, timedelta, timezone
import asyncio
import json
import traceback
import re
from pathlib import Path
from websockets.exceptions import ConnectionClosed
from uvicorn.protocols.utils import ClientDisconnected
from core.config import settings
from core.websocket import WebSocketConnectionManager, shutdown_event
from database import get_db, SessionLocal
from models.job import (
    Job,
    JOB_TYPE_IDARR,
    job_type_sync_one,
    job_type_sync_all,
    JOB_TYPE_SYNC_ALL_PREFIX,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_FAILED,
    JOB_STATUSES_ACTIVE,
    JOB_STATUSES_RECENT_TERMINAL,
    format_start_message,
    update_job_state,
)
from models.drive import Drive
from models.setting import get_setting
from modules.sync import run_sync_one_job, run_sync_all_job
from modules.idarr import run_idarr_background_job, run_idarr_sync_background_job
from models.idarr import resolve_idarr_scope_token
from core.logging import LogTags, log_debug, log_warning, log_error, log_user_action
from core.job_queue import job_queue

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_ws = WebSocketConnectionManager()
JOB_WS_HEARTBEAT_INTERVAL_SECONDS = 30.0


def _create_job(db: Session, job_type: str, message: str) -> Job:
    job = Job(
        job_type=job_type,
        status=JOB_STATUS_PENDING,
        progress=0,
        message=message,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _log_job_queued(job_id: int, label: str, **context: Any) -> None:
    log_debug(LogTags.JOB, f"Job {job_id} queued: {label}", job_id=job_id, **context)


def _warn_no_ws(job_id: int) -> None:
    """Log a warning if no WebSocket clients are connected to receive job progress."""
    if _ws.count() == 0:
        log_warning(LogTags.WEBSOCKET, f"Job {job_id} started with no active WebSocket connections - user won't see progress", job_id=job_id)


def _run_job_task(job_id: int, start_msg: str, run_fn: Callable[[], None]) -> None:
    """Thread-pool task wrapper: mark job RUNNING, call run_fn, mark FAILED on error."""
    task_db = SessionLocal()
    try:
        job_obj = task_db.query(Job).filter(Job.id == job_id).first()
        if job_obj:
            update_job_state(task_db, job_obj, status=JOB_STATUS_RUNNING, message=start_msg)
        task_db.close()
        run_fn()
    except Exception as e:
        log_error(LogTags.JOB, f"Error in job {job_id}: {e}\n{traceback.format_exc()}")
        task_db = SessionLocal()
        try:
            job_obj = task_db.query(Job).filter(Job.id == job_id).first()
            if job_obj:
                update_job_state(task_db, job_obj, status=JOB_STATUS_FAILED, error=str(e), completed_at=datetime.now(timezone.utc))
        finally:
            task_db.close()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time job progress updates"""
    conn_id = _ws.next_conn_id()
    
    await websocket.accept()
    _ws.active_connections.append(websocket)

    _ws.check_warning()
    
    try:
        previous_snapshot: str | None = None
        last_heartbeat_time = asyncio.get_running_loop().time()

        # Send updates with adaptive intervals while connection is active
        while True:
            # Create new DB session for this update
            db = SessionLocal()
            try:
                # Get active AND recently completed jobs (last 2 minutes)
                two_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=2)
                
                jobs = db.query(Job).filter(
                    (Job.status.in_(JOB_STATUSES_ACTIVE)) |
                    ((Job.status.in_(JOB_STATUSES_RECENT_TERMINAL)) & (Job.completed_at >= two_minutes_ago))
                ).order_by(Job.started_at.desc(), Job.id.desc()).all()
                
                # Send job updates
                jobs_data = [
                    {
                        'id': job.id,
                        'job_type': job.job_type,
                        'status': job.status,
                        'progress': job.progress,
                        'message': job.message or '',
                        'error': job.error
                    }
                    for job in jobs
                ]

                payload = {'jobs': jobs_data}
                snapshot = json.dumps(payload, sort_keys=True)
                now = asyncio.get_running_loop().time()

                # Send only when data has changed to reduce websocket and DB pressure
                if snapshot != previous_snapshot:
                    await websocket.send_json(payload)
                    previous_snapshot = snapshot
                    last_heartbeat_time = now
                elif (now - last_heartbeat_time) >= JOB_WS_HEARTBEAT_INTERVAL_SECONDS:
                    # Heartbeat ensures stale/disconnected clients are detected
                    await websocket.send_json({'heartbeat': int(now)})
                    last_heartbeat_time = now

                # Poll more frequently while jobs are active, less frequently when idle
                has_active_jobs = any(job['status'] in JOB_STATUSES_ACTIVE for job in jobs_data)
                sleep_seconds = (
                    settings.job_ws_poll_interval_active
                    if has_active_jobs
                    else settings.job_ws_poll_interval_idle
                )
            except SQLAlchemyOperationalError as e:
                if "database is locked" in str(e):
                    # SQLite is busy with a heavy write (e.g. bulk border processing).
                    # Skip this poll cycle and retry after a short back-off rather
                    # than crashing the WebSocket connection.
                    log_debug(LogTags.WEBSOCKET, f"Job WS #{conn_id} DB busy, skipping poll cycle", conn_id=conn_id)
                    sleep_seconds = 1.0
                else:
                    raise
            finally:
                db.close()

            # Sleep, but wake immediately if the server is shutting down
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=max(0.1, sleep_seconds))
                return  # Shutdown signaled — exit cleanly
            except asyncio.TimeoutError:
                pass  # Normal poll interval elapsed, continue
            
    except (WebSocketDisconnect, ConnectionClosed, ClientDisconnected, asyncio.CancelledError):
        # Client disconnected or server shutting down - this is normal, no logging needed
        pass
    except Exception as e:
        # Log unexpected errors with full traceback
        log_error(LogTags.WEBSOCKET, f"Job WS #{conn_id} unexpected error: {e}\n{traceback.format_exc()}", conn_id=conn_id)
    finally:
        # ALWAYS remove connection from list, regardless of how we exit
        if websocket in _ws.active_connections:
            _ws.active_connections.remove(websocket)
            _ws.check_warning()
        else:
            log_warning(LogTags.WEBSOCKET, f"Job WS #{conn_id} not in active list during cleanup!", conn_id=conn_id)

class JobSchema(BaseModel):
    id: int
    job_type: str
    status: str
    progress: int
    message: str | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None
    
    model_config = ConfigDict(from_attributes=True)

class StartSyncRequest(BaseModel):
    drive_id: int


class StartIdarrRequest(BaseModel):
    dry_run: bool = False
    sync_target_index: int | None = None
    source_filenames: list[str] | None = None
    sync_after_run: bool = False


def _sanitize_source_filenames(raw_values: list[str] | None) -> list[str]:
    if not raw_values:
        return []

    sanitized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        filename = Path(str(raw_value or "")).name.strip()
        if not filename:
            continue
        filename_key = filename.lower()
        if filename_key in seen:
            continue
        seen.add(filename_key)
        sanitized.append(filename)
    return sanitized


class StartIdarrSyncRequest(BaseModel):
    personal_drive_id: str | None = None
    source_dir: str | None = None
    sync_target_index: int | None = None
    mode: str | None = None


WORKFLOW_LOG_PATH = Path("/config/logs/workflow/workflow.log")
WORKFLOW_START_MARKER = "JOB STARTED: Poster Workflow"
WORKFLOW_END_MARKERS = [
    "Workflow completed",
    "JOB COMPLETED SUCCESSFULLY",
    "JOB FAILED",
]
WORKFLOW_TIME_RE = re.compile(r"^(\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})")


def _parse_workflow_log_timestamp(line: str) -> datetime | None:
    match = WORKFLOW_TIME_RE.match(line)
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group(1), "%y/%m/%d %H:%M:%S")
        return naive.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _extract_last_workflow_timeline(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []

    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    if not lines:
        return []

    start_idx = -1
    for idx, line in enumerate(lines):
        if WORKFLOW_START_MARKER in line:
            start_idx = idx

    if start_idx == -1:
        return []

    end_idx = len(lines) - 1
    for idx in range(start_idx + 1, len(lines)):
        line = lines[idx]
        if any(marker in line for marker in WORKFLOW_END_MARKERS):
            end_idx = idx

    segment = lines[start_idx : end_idx + 1]
    start_time = _parse_workflow_log_timestamp(segment[0])
    if not start_time:
        return []

    timeline: list[dict[str, Any]] = []
    last_message: str = ""

    for line in segment:
        ts = _parse_workflow_log_timestamp(line)
        if not ts:
            continue

        if "| [" not in line:
            continue

        # Keep only operationally-useful transitions to avoid noise
        relevant = (
            "Step " in line
            or "Workflow completed" in line
            or "JOB COMPLETED SUCCESSFULLY" in line
            or "JOB FAILED" in line
            or "Syncing '" in line
            or "✓ Completed:" in line
        )
        if not relevant:
            continue

        message = line.split("]", 1)[1].strip() if "]" in line else line.strip()
        if message == last_message:
            continue

        elapsed = int((ts - start_time).total_seconds())
        timeline.append(
            {
                "timestamp": ts.isoformat(),
                "elapsed_seconds": max(0, elapsed),
                "message": message,
            }
        )
        last_message = message

    return timeline

@router.get("/", response_model=List[JobSchema])
def list_jobs(db: Session = Depends(get_db)) -> List[JobSchema]:
    """List all jobs, most recent first"""
    jobs = db.query(Job).order_by(Job.started_at.desc()).limit(50).all()
    return jobs

@router.get("/{job_id}", response_model=JobSchema)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobSchema:
    """Get a specific job"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/workflow/last-run-timeline")
def get_last_workflow_timeline() -> Dict[str, Any]:
    """Return timeline points from the most recent real workflow run log."""
    timeline = _extract_last_workflow_timeline(WORKFLOW_LOG_PATH)
    if not timeline:
        return {
            "available": False,
            "source": str(WORKFLOW_LOG_PATH),
            "timeline": [],
            "message": "No workflow timeline found in log file.",
        }

    duration_seconds = timeline[-1]["elapsed_seconds"] if timeline else 0
    return {
        "available": True,
        "source": str(WORKFLOW_LOG_PATH),
        "points": len(timeline),
        "duration_seconds": duration_seconds,
        "timeline": timeline,
    }

@router.post("/sync", response_model=JobSchema)
def start_sync_one_drive(request: StartSyncRequest, db: Session = Depends(get_db)) -> JobSchema:
    """Start a sync job for a single drive"""
    # Check if drive exists and is subscribed
    drive = db.query(Drive).filter(Drive.id == request.drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")
    
    if not drive.subscribed:
        raise HTTPException(status_code=400, detail="Drive not subscribed")

    # Warn if drive is deprecated
    if drive.is_deprecated:
        log_warning(LogTags.JOB, f"Sync requested for deprecated drive: '{drive.name}'", drive_id=drive.id, drive=drive.name)
    
    # Check for duplicate running/pending jobs for this drive
    existing_job = db.query(Job).filter(
        Job.job_type == job_type_sync_one(drive.name),
        Job.status.in_(JOB_STATUSES_ACTIVE)
    ).first()
    if existing_job:
        log_warning(LogTags.JOB, f"Duplicate sync detected: '{drive.name}' already has job {existing_job.id} ({existing_job.status})", 
                   drive=drive.name, existing_job_id=existing_job.id, new_request=True)
    
    log_user_action(f"Sync requested for '{drive.name}'")
    
    # Capture drive info before session closes
    drive_id = drive.id
    drive_name = drive.name
    
    # Create job record
    job = _create_job(
        db,
        job_type=job_type_sync_one(drive_name),
        message=f"Queued sync for {drive_name}",
    )
    job_id = job.id
    
    # Submit to managed thread pool queue
    job_queue.submit(_run_job_task, job_id, job_id, f"Starting sync of {drive_name}", lambda: run_sync_one_job(drive_id, job_id))
    _log_job_queued(job_id, f"Sync '{drive_name}'", drive=drive_name)
    _warn_no_ws(job_id)
    
    return job


@router.post("/sync-all", response_model=JobSchema)
def start_sync_all_drives(db: Session = Depends(get_db)) -> JobSchema:
    """Start a sync job for all subscribed drives"""
    # Get all subscribed drives
    drives = db.query(Drive).filter(Drive.subscribed == True).all()
    
    if not drives:
        raise HTTPException(status_code=400, detail="No subscribed drives found")
    
    # Warn if only one drive (unusual use case)
    if len(drives) == 1:
        log_warning(LogTags.JOB, "Sync All requested with only 1 subscribed drive", drive_count=1, drive=drives[0].name)
    
    # Count deprecated drives
    deprecated_count = sum(1 for d in drives if d.is_deprecated)
    if deprecated_count > 0:
        log_warning(LogTags.JOB, f"Sync All includes {deprecated_count} deprecated drive(s)", deprecated_count=deprecated_count, total_drives=len(drives))
    
    # Check for existing sync-all job
    existing_job = db.query(Job).filter(
        Job.job_type.like(f"{JOB_TYPE_SYNC_ALL_PREFIX}%"),
        Job.status.in_(JOB_STATUSES_ACTIVE)
    ).first()
    if existing_job:
        log_warning(LogTags.JOB, f"Duplicate Sync All detected: job {existing_job.id} already {existing_job.status}", 
                   existing_job_id=existing_job.id, status=existing_job.status)
    
    log_user_action(f"Sync All requested for {len(drives)} drives")
    
    # Create job record
    job = _create_job(
        db,
        job_type=job_type_sync_all(len(drives)),
        message=f"Queued sync for {len(drives)} drives",
    )
    job_id = job.id
    
    # Submit to managed thread pool queue
    job_queue.submit(_run_job_task, job_id, job_id, f"Starting sync of {len(drives)} drives", lambda: run_sync_all_job(job_id))
    _log_job_queued(job_id, f"Sync All ({len(drives)} drives)", drive_count=len(drives))
    _warn_no_ws(job_id)
    
    return job


@router.post("/idarr", response_model=JobSchema)
def start_idarr_job(request: StartIdarrRequest, db: Session = Depends(get_db)) -> JobSchema:
    """Start a native in-app idarr job from Maker Tools."""
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
            LogTags.JOB,
            f"Duplicate IDarr start blocked: job {existing_idarr_job.id} already {existing_idarr_job.status}",
            existing_job_id=existing_idarr_job.id,
            status=existing_idarr_job.status,
        )
        raise HTTPException(
            status_code=409,
            detail=f"IDarr job {existing_idarr_job.id} is already {existing_idarr_job.status}. Wait for it to finish before starting a new run.",
        )

    config_setting = get_setting(db, "maker_tools_idarr_config")
    if not config_setting or not config_setting.value:
        raise HTTPException(status_code=400, detail="idarr is not configured. Configure Maker Tools → idarr first.")

    try:
        config_data = json.loads(config_setting.value)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="idarr configuration is invalid JSON. Save configuration again.")

    if not isinstance(config_data, dict):
        raise HTTPException(status_code=400, detail="idarr configuration payload is invalid.")

    _tmdb_setting = get_setting(db, "tmdb_api_key")
    global_tmdb_key = str(_tmdb_setting.value or "").strip() if _tmdb_setting else ""
    if not global_tmdb_key:
        log_warning(LogTags.JOB, "idarr job blocked: TMDB API key is not configured")
        raise HTTPException(
            status_code=400,
            detail="TMDB API key is not configured. Add it in Settings → General → API Keys.",
        )
    config_data["tmdb_api_key"] = global_tmdb_key

    raw_targets = config_data.get("sync_targets")
    sync_targets = [item for item in raw_targets if isinstance(item, dict)] if isinstance(raw_targets, list) else []
    if not sync_targets:
        raise HTTPException(
            status_code=400,
            detail="idarr requires at least one sync target with a processing folder.",
        )

    requested_index = request.sync_target_index if request.sync_target_index is not None else 0
    if requested_index < 0 or requested_index >= len(sync_targets):
        raise HTTPException(
            status_code=400,
            detail=f"sync_target_index must be between 0 and {max(len(sync_targets) - 1, 0)}",
        )

    selected_target = sync_targets[requested_index]
    source_dir = str(selected_target.get("source_dir") or "").strip()
    if not source_dir:
        raise HTTPException(
            status_code=400,
            detail="Selected sync target is missing a processing folder (source_dir).",
        )

    source_filenames = _sanitize_source_filenames(request.source_filenames)

    config_data["dry_run"] = bool(request.dry_run)
    config_data["source_dir"] = source_dir
    config_data["sync_target_index"] = requested_index
    config_data["scope_token"] = resolve_idarr_scope_token(selected_target, requested_index)
    config_data["sync_after_run"] = bool(request.sync_after_run)
    config_data["is_asset_drive"] = bool(selected_target.get("is_asset_drive", False))
    config_data["is_psd_drive"] = bool(selected_target.get("is_psd_drive", False))
    if source_filenames:
        config_data["source_filenames"] = source_filenames

    log_user_action(
        "idarr run requested",
        dry_run=request.dry_run,
        sync_target_index=requested_index,
        source_dir=source_dir,
        source_filenames_count=len(source_filenames),
        sync_after_run=request.sync_after_run,
        force_sync_after_run=bool(config_data.get("force_sync_after_run", False)),
    )

    job = _create_job(
        db,
        job_type=JOB_TYPE_IDARR,
        message=format_start_message("idarr native rename", dry_run=request.dry_run),
    )

    job_queue.submit(run_idarr_background_job, job.id, job.id, config_data)
    _log_job_queued(job.id, "idarr", dry_run=request.dry_run)
    _warn_no_ws(job.id)

    return job


@router.post("/idarr-sync", response_model=JobSchema)
def start_idarr_sync_job(request: StartIdarrSyncRequest, db: Session = Depends(get_db)) -> JobSchema:
    """Manually sync idarr output to a personal Google Drive folder ID."""

    config_setting = get_setting(db, "maker_tools_idarr_config")
    config_data: dict[str, Any] = {}
    if config_setting and config_setting.value:
        try:
            parsed = json.loads(config_setting.value)
            if isinstance(parsed, dict):
                config_data = parsed
        except json.JSONDecodeError:
            pass

    selected_target: dict[str, Any] = {}
    raw_targets = config_data.get("sync_targets")
    sync_targets = [item for item in raw_targets if isinstance(item, dict)] if isinstance(raw_targets, list) else []

    if request.sync_target_index is not None:
        if request.sync_target_index < 0 or request.sync_target_index >= len(sync_targets):
            raise HTTPException(
                status_code=400,
                detail=f"sync_target_index must be between 0 and {max(len(sync_targets) - 1, 0)}",
            )
        selected_target = sync_targets[request.sync_target_index]

    target_source_dir = str(selected_target.get("source_dir") or "").strip()
    source_dir = (request.source_dir or target_source_dir or "").strip()
    if not source_dir:
        raise HTTPException(
            status_code=400,
            detail="No source directory provided. Select a configured sync target or pass source_dir for personal sync.",
        )

    target_personal_drive_id = str(selected_target.get("personal_drive_id") or "").strip()
    personal_drive_id = (request.personal_drive_id or target_personal_drive_id or "").strip()
    if not personal_drive_id:
        raise HTTPException(
            status_code=400,
            detail="No personal drive ID provided. Select a configured sync target or pass personal_drive_id.",
        )

    sync_mode = "sync"

    log_user_action(
        "idarr personal sync requested",
        personal_drive_id=personal_drive_id,
        source_dir=source_dir,
        sync_target_index=request.sync_target_index,
        mode=sync_mode,
    )

    job = _create_job(
        db,
        job_type=JOB_TYPE_IDARR,
        message="Queued idarr personal sync",
    )

    job_queue.submit(
        run_idarr_sync_background_job,
        job.id,
        job.id,
        {
            "personal_drive_id": personal_drive_id,
            "source_dir": source_dir,
            "sync_mode": sync_mode,
        },
    )
    _log_job_queued(job.id, "idarr personal sync", personal_drive_id=personal_drive_id, mode=sync_mode)
    _warn_no_ws(job.id)

    return job


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)) -> Dict[str, str]:
    """Delete a job record"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    db.delete(job)
    db.commit()
    
    return {"message": "Job deleted"}