from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import Any, Dict, List
from core.config import settings
from core.logging import log_warning, log_error, LogTags
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/job-logs", tags=["job-logs"])

JobLogFile = Dict[str, Any]
JobLogsByType = Dict[str, List[JobLogFile]]

PRIMARY_JOB_TYPES = ["sync_one", "sync_all", "plex_upload", "workflow", "poster_renamer", "border_replacer", "unmatched_assets", "idarr"]


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
            log_files.append({
                "name": log_file.name,
                "path": str(log_file.relative_to(logs_dir)),
                "size": stat.st_size,
                "modified": stat.st_mtime
            })
        except Exception as e:
            log_warning(LogTags.API, f"Failed to stat log file {log_file}: {e}")

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
