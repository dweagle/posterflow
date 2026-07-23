from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.config import settings
from core.logging import log_warning, log_error, LogTags
from fastapi.responses import FileResponse
import asyncio
import re

router = APIRouter(prefix="/api/job-logs", tags=["job-logs"])

JobLogFile = Dict[str, Any]
JobLogsByType = Dict[str, List[JobLogFile]]

PRIMARY_JOB_TYPES = ["sync_one", "sync_all", "plex_upload", "workflow", "poster_renamer", "border_replacer", "unmatched_assets", "idarr", "artwork_pull"]


def _extract_log_metadata(log_file: Path) -> tuple[Optional[int], Optional[str]]:
    """Read the first 10 lines of a log file to extract job ID and started timestamp.

    Returns:
        (job_id, started_at) where started_at is in 'MM/DD HH:MM' short form.
    """
    job_id: Optional[int] = None
    started_at: Optional[str] = None
    try:
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                if i >= 10:
                    break
                if job_id is None:
                    m = re.search(r'Job ID: (\d+)', line)
                    if m:
                        job_id = int(m.group(1))
                if started_at is None:
                    m = re.search(r'Started: (\d{2})/(\d{2})/(\d{2}) (\d{2}:\d{2}):\d{2}', line)
                    if m:
                        # Format: YY/MM/DD HH:MM:SS → MM/DD HH:MM
                        started_at = f"{m.group(2)}/{m.group(3)} {m.group(4)}"
                if job_id is not None and started_at is not None:
                    break
    except Exception:
        pass
    return job_id, started_at


def _collect_log_files(logs_dir: Path, job_type: str) -> List[JobLogFile]:
    log_files: List[JobLogFile] = []
    job_logs_dir = logs_dir / job_type

    if not job_logs_dir.exists():
        return log_files

    all_logs = sorted(job_logs_dir.glob("*.log"))

    def sort_key(path: Path) -> int:
        name = path.stem
        if '.' in name:
            try:
                return int(name.split('.')[-1])
            except ValueError:
                return -1
        return -1

    all_logs = sorted(all_logs, key=sort_key)

    for log_file in all_logs:
        try:
            stat = log_file.stat()
        except Exception as e:
            log_warning(LogTags.API, f"Failed to stat log file {log_file}: {e}")
            continue

        job_id, started_at = _extract_log_metadata(log_file)
        log_files.append({
            "name": log_file.name,
            "path": str(log_file.relative_to(logs_dir)),
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "job_id": job_id,
            "started_at": started_at,
        })

    return log_files


def _resolve_job_type_paths(logs_dir: Path, requested_job_type: str) -> List[Path]:
    return [logs_dir / requested_job_type]

@router.get("/")
def list_job_logs() -> JobLogsByType:
    """
    List all job log files organized by job type.
    Returns a dictionary with all job type log files.
    """
    logs_dir = Path(settings.log_file).parent
    result = {}
    
    for job_type in PRIMARY_JOB_TYPES:
        log_files = _collect_log_files(logs_dir, job_type)

        result[job_type] = log_files
    
    return result


@router.get("/{job_type}/{filename}")
def get_job_log(job_type: str, filename: str) -> Dict[str, str]:
    """
    Get the contents of a specific job log file.
    """
    valid_job_types = set(PRIMARY_JOB_TYPES)
    if job_type not in valid_job_types:
        raise HTTPException(status_code=400, detail="Invalid job type")
    
    logs_dir = Path(settings.log_file).parent
    candidate_paths = [path / filename for path in _resolve_job_type_paths(logs_dir, job_type)]
    log_file = next((path for path in candidate_paths if path.exists()), None)

    if not log_file:
        raise HTTPException(status_code=404, detail="Log file not found")
    
    # Security check: ensure file is within the job logs directory
    try:
        log_file.resolve().relative_to(logs_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        with open(log_file, 'r') as f:
            content = f.read()
        return {"content": content, "filename": filename}
    except Exception as e:
        log_error(LogTags.API, f"Failed to read log file {log_file}: {e}")
        raise HTTPException(status_code=500, detail="Failed to read log file")


@router.get("/{job_type}/{filename}/download")
def download_job_log(job_type: str, filename: str) -> FileResponse:
    """
    Download a specific job log file.
    """
    valid_job_types = set(PRIMARY_JOB_TYPES)
    if job_type not in valid_job_types:
        raise HTTPException(status_code=400, detail="Invalid job type")
    
    logs_dir = Path(settings.log_file).parent
    candidate_paths = [path / filename for path in _resolve_job_type_paths(logs_dir, job_type)]
    log_file = next((path for path in candidate_paths if path.exists()), None)

    if not log_file:
        raise HTTPException(status_code=404, detail="Log file not found")
    
    # Security check
    try:
        log_file.resolve().relative_to(logs_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return FileResponse(
        path=str(log_file),
        filename=filename,
        media_type="text/plain"
    )


JOB_LOG_WS_HEARTBEAT_INTERVAL_SECONDS = 30.0


@router.websocket("/{job_type}/live")
async def websocket_job_log_live(websocket: WebSocket, job_type: str) -> None:
    """WebSocket endpoint for live-tailing the current job log file."""
    valid_job_types = set(PRIMARY_JOB_TYPES)
    if job_type not in valid_job_types:
        await websocket.close(code=4004)
        return

    await websocket.accept()

    logs_dir = Path(settings.log_file).parent
    log_file = logs_dir / job_type / f"{job_type}.log"

    try:
        last_heartbeat_time = asyncio.get_running_loop().time()

        # Send existing content of the current log file
        if log_file.exists():
            with open(log_file, 'r') as f:
                content = f.read()
            if content:
                try:
                    await websocket.send_json({'type': 'content', 'content': content})
                except Exception:
                    return

        # Tail for new content
        last_position = log_file.stat().st_size if log_file.exists() else 0

        while True:
            await asyncio.sleep(0.5)
            now = asyncio.get_running_loop().time()

            if not log_file.exists():
                if (now - last_heartbeat_time) >= JOB_LOG_WS_HEARTBEAT_INTERVAL_SECONDS:
                    await websocket.send_json({'type': 'heartbeat', 'heartbeat': int(now)})
                    last_heartbeat_time = now
                continue

            current_size = log_file.stat().st_size

            # File was rotated (new job started) — reload from scratch
            if current_size < last_position:
                last_position = 0
                with open(log_file, 'r') as f:
                    content = f.read()
                if content:
                    try:
                        await websocket.send_json({'type': 'reset', 'content': content})
                        last_position = log_file.stat().st_size
                        last_heartbeat_time = now
                    except Exception:
                        return
                continue

            # New content appended
            if current_size > last_position:
                with open(log_file, 'r') as f:
                    f.seek(last_position)
                    new_content = f.read()
                    last_position = f.tell()

                if new_content:
                    try:
                        await websocket.send_json({'type': 'append', 'content': new_content})
                        last_heartbeat_time = now
                    except Exception:
                        return
            elif (now - last_heartbeat_time) >= JOB_LOG_WS_HEARTBEAT_INTERVAL_SECONDS:
                await websocket.send_json({'type': 'heartbeat', 'heartbeat': int(now)})
                last_heartbeat_time = now

    except WebSocketDisconnect:
        return
    except Exception:
        return
