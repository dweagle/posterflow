"""
Backup and restore endpoints for PosterFlow configuration
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pathlib import Path
from typing import Any, Dict
import zipfile
import json
import shutil
import tempfile
import traceback
from datetime import datetime
from core.config import settings as app_settings
from core.logging import LogTags, log_info, log_success, log_error, log_warning, log_user_action

router = APIRouter(prefix="/api/backup", tags=["backup"])

CONFIG_DIR = app_settings.config_dir
DB_FILE = CONFIG_DIR / "posterflow.db"
RCLONE_CONF = CONFIG_DIR / "rclone.conf"
DRIVES_CACHE = CONFIG_DIR / "drives_cache.json"


@router.get("/")
async def create_backup() -> FileResponse:
    """
    Create a backup zip file containing database and configuration files
    """
    try:
        backup_name = f"posterflow_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        backup_path = CONFIG_DIR / backup_name
        
        # Create metadata
        metadata = {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "app": "PosterFlow"
        }
        
        # Create zip file directly in config directory
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add database
            if DB_FILE.exists():
                zipf.write(DB_FILE, "posterflow.db")
                log_info(LogTags.BACKUP, "Added database to backup")
            
            # Add rclone config
            if RCLONE_CONF.exists():
                zipf.write(RCLONE_CONF, "rclone.conf")
                log_info(LogTags.BACKUP, "Added rclone config to backup")
            
            # Add drives cache (optional)
            if DRIVES_CACHE.exists():
                zipf.write(DRIVES_CACHE, "drives_cache.json")
                log_info(LogTags.BACKUP, "Added drives cache to backup")
            
            # Add metadata
            zipf.writestr("metadata.json", json.dumps(metadata, indent=2))
        
        log_success(LogTags.BACKUP, f"Created backup: {backup_name}")
        
        # Return the file and clean up after sending
        return FileResponse(
            path=str(backup_path),
            filename=backup_name,
            media_type="application/zip",
            background=BackgroundTask(backup_path.unlink, missing_ok=True)
        )
        
    except Exception as e:
        log_error(LogTags.BACKUP, f"Failed to create backup: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to create backup")


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
            
            # Restore database
            db_backup = temp_path / "posterflow.db"
            if db_backup.exists():
                if DB_FILE.exists():
                    safety_path = safety_backup_dir / f"posterflow.db{backup_suffix}"
                    shutil.copy(DB_FILE, safety_path)
                shutil.copy(db_backup, DB_FILE)
                log_success(LogTags.BACKUP, "Database restored successfully")
            
            # Restore rclone config
            rclone_backup = temp_path / "rclone.conf"
            if rclone_backup.exists():
                if RCLONE_CONF.exists():
                    safety_path = safety_backup_dir / f"rclone.conf{backup_suffix}"
                    shutil.copy(RCLONE_CONF, safety_path)
                shutil.copy(rclone_backup, RCLONE_CONF)
                log_success(LogTags.BACKUP, "Rclone config restored successfully")
            
            # Restore drives cache
            cache_backup = temp_path / "drives_cache.json"
            if cache_backup.exists():
                if DRIVES_CACHE.exists():
                    safety_path = safety_backup_dir / f"drives_cache.json{backup_suffix}"
                    shutil.copy(DRIVES_CACHE, safety_path)
                shutil.copy(cache_backup, DRIVES_CACHE)
                log_success(LogTags.BACKUP, "Drives cache restored successfully")

            log_user_action(
                "Backup restore completed",
                database_restored=db_backup.exists(),
                rclone_restored=rclone_backup.exists(),
                cache_restored=cache_backup.exists(),
            )
            
            return {
                "message": "Backup restored successfully. Please restart the application for changes to take effect.",
                "restored_files": {
                    "database": db_backup.exists(),
                    "rclone_config": rclone_backup.exists(),
                    "drives_cache": cache_backup.exists()
                }
            }
            
    except zipfile.BadZipFile:
        log_error(LogTags.BACKUP, f"Invalid zip file\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail="Invalid backup file")
    except HTTPException:
        raise
    except Exception as e:
        log_error(LogTags.BACKUP, f"Failed to restore backup: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to restore backup")
