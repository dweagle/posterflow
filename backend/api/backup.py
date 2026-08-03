"""
Backup and restore endpoints for PosterFlow configuration
"""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from pathlib import Path
from typing import Any, Dict
import zipfile
import json
import shutil
import tempfile
import traceback
from datetime import datetime
from database import get_db
from core.config import settings as app_settings
from core.logging import LogTags, log_info, log_success, log_error, log_warning, log_user_action
from services.backup import build_backup_zip, run_backup_to_location

router = APIRouter(prefix="/api/backup", tags=["backup"])

CONFIG_DIR = app_settings.config_dir
DB_FILE = CONFIG_DIR / "posterflow.db"
RCLONE_CONF = CONFIG_DIR / "rclone.conf"
DRIVES_CACHE = CONFIG_DIR / "drives_cache.json"
ARTWORK_DRIVES_CACHE = CONFIG_DIR / "artwork_drives_cache.json"


@router.get("/")
def create_backup() -> FileResponse:
    """
    Create a backup zip file containing database and configuration files
    """
    try:
        backup_path = build_backup_zip(CONFIG_DIR)

        # Return the file and clean up after sending
        return FileResponse(
            path=str(backup_path),
            filename=backup_path.name,
            media_type="application/zip",
            background=BackgroundTask(backup_path.unlink, missing_ok=True)
        )

    except Exception as e:
        log_error(LogTags.BACKUP, f"Failed to create backup: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to create backup")


@router.post("/save")
def save_backup_to_location(db: Session = Depends(get_db)) -> Dict[str, str]:
    """
    Create a backup zip in the configured backup location (same as scheduled backups)
    """
    try:
        backup_path = run_backup_to_location(db)
        log_user_action("Manual backup saved to backup location", path=str(backup_path))
        return {
            "message": f"Backup saved to {backup_path}",
            "path": str(backup_path),
        }
    except Exception as e:
        log_error(LogTags.BACKUP, f"Failed to save backup: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to save backup")


@router.post("/")
async def restore_backup(confirm: bool = False, file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Restore database and configuration from a backup zip file
    """
    if not confirm:
        log_warning(LogTags.BACKUP, "Restore blocked without explicit confirmation")
        raise HTTPException(status_code=400, detail="Must pass confirm=true to restore backup")

    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Backup file must be a .zip file")

    if file.size is not None and file.size > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Backup file must not exceed 50 MB")
    
    try:
        # Create temporary directory for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / "backup.zip"
            
            # Save uploaded file
            with open(zip_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
            
            # Extract zip file - validate each member to prevent Zip Slip path traversal
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                temp_path_resolved = temp_path.resolve()
                for member in zipf.namelist():
                    member_path = (temp_path / member).resolve()
                    if not member_path.is_relative_to(temp_path_resolved):
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid backup file: contains path traversal entries",
                        )
                zipf.extractall(temp_path)
            
            # Verify metadata
            metadata_file = temp_path / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                    log_info(LogTags.BACKUP, f"Restoring backup from {metadata.get('created_at')}")
            log_user_action(f"Backup restore requested: {file.filename}")
            
            # Create safety backups folder
            safety_backup_dir = CONFIG_DIR / "safety_backups"
            safety_backup_dir.mkdir(exist_ok=True)
            backup_suffix = f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # (zip member, live target, response key, log label)
            restore_targets = [
                ("posterflow.db", DB_FILE, "database", "Database"),
                ("rclone.conf", RCLONE_CONF, "rclone_config", "Rclone config"),
                ("drives_cache.json", DRIVES_CACHE, "drives_cache", "Drives cache"),
                ("artwork_drives_cache.json", ARTWORK_DRIVES_CACHE, "artwork_drives_cache", "Artwork drives cache"),
            ]

            restored_files: Dict[str, bool] = {}
            for member_name, target, response_key, label in restore_targets:
                extracted = temp_path / member_name
                restored_files[response_key] = extracted.exists()
                if not extracted.exists():
                    continue
                if target.exists():
                    safety_path = safety_backup_dir / f"{member_name}{backup_suffix}"
                    shutil.copy(target, safety_path)
                shutil.copy(extracted, target)
                log_success(LogTags.BACKUP, f"{label} restored successfully")

            log_user_action(
                "Backup restore completed",
                database_restored=restored_files["database"],
                rclone_restored=restored_files["rclone_config"],
                cache_restored=restored_files["drives_cache"],
                artwork_cache_restored=restored_files["artwork_drives_cache"],
            )

            return {
                "message": "Backup restored successfully. Please restart the application for changes to take effect.",
                "restored_files": restored_files
            }
            
    except zipfile.BadZipFile:
        log_error(LogTags.BACKUP, f"Invalid zip file\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail="Invalid backup file")
    except HTTPException:
        raise
    except Exception as e:
        log_error(LogTags.BACKUP, f"Failed to restore backup: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to restore backup")
