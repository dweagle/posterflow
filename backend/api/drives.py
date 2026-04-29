from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from typing import Literal
from uuid import uuid4
import traceback
import shutil
import json
from pydantic import BaseModel, ConfigDict, field_serializer
from datetime import datetime, timezone

from database import get_db, SessionLocal
from models.drive import Drive
from models.job import Job, JOB_STATUS_PENDING
from models.poster import Poster
from models.setting import get_setting, upsert_setting
from core.logging import LogTags, log_info, log_debug, log_warning, log_error, log_user_action
from core.job_queue import job_queue

router = APIRouter(prefix="/api/drives", tags=["drives"])

SETTING_POSTER_DRIVE_PRIORITY = "poster_drive_priority"
SETTING_POSTER_DRIVE_PRIORITY_REMOVED = "poster_drive_priority_removed"


def _load_removed_priority_map(db: Session) -> Dict[str, int]:
    """Load temporarily removed drive positions keyed by drive id."""
    removed_setting = get_setting(db, SETTING_POSTER_DRIVE_PRIORITY_REMOVED)
    if not removed_setting or not removed_setting.value:
        return {}

    try:
        removed_data = json.loads(removed_setting.value)
    except json.JSONDecodeError:
        log_warning(LogTags.DRIVES, "Skipping removed-priority restore due to invalid JSON")
        return {}

    if not isinstance(removed_data, dict):
        return {}

    normalized: Dict[str, int] = {}
    for key, value in removed_data.items():
        try:
            normalized[str(key)] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return normalized


def _save_removed_priority_map(db: Session, removed_map: Dict[str, int]) -> None:
    """Persist temporarily removed drive positions for future resubscribe restores."""
    upsert_setting(db, SETTING_POSTER_DRIVE_PRIORITY_REMOVED, json.dumps(removed_map))


def _prune_drive_from_priority(db: Session, drive_id: int) -> bool:
    """Remove a drive id from poster drive priority setting if present."""
    priority_setting = get_setting(db, SETTING_POSTER_DRIVE_PRIORITY)
    if not priority_setting or not priority_setting.value:
        return False

    try:
        priority_data = json.loads(priority_setting.value)
    except json.JSONDecodeError:
        log_warning(LogTags.DRIVES, "Skipping priority prune due to invalid poster_drive_priority JSON")
        return False

    drive_ids = priority_data.get("drive_ids", [])
    if not isinstance(drive_ids, list):
        return False

    removed_index = None
    filtered_drive_ids = []
    for index, existing_id in enumerate(drive_ids):
        if str(existing_id) == str(drive_id):
            if removed_index is None:
                removed_index = index
            continue
        filtered_drive_ids.append(existing_id)

    if removed_index is None:
        return False

    priority_data["drive_ids"] = filtered_drive_ids
    if "enabled_styles" not in priority_data or not isinstance(priority_data.get("enabled_styles"), list):
        priority_data["enabled_styles"] = ["MM2K", "CL2K", "Custom"]

    removed_map = _load_removed_priority_map(db)
    removed_map[str(drive_id)] = removed_index

    upsert_setting(db, SETTING_POSTER_DRIVE_PRIORITY, json.dumps(priority_data))
    _save_removed_priority_map(db, removed_map)
    return True


def _restore_drive_to_priority(db: Session, drive_id: int) -> bool:
    """Reinsert a previously-pruned drive into poster priority if a saved position exists."""
    removed_map = _load_removed_priority_map(db)
    removed_index = removed_map.pop(str(drive_id), None)

    if removed_index is None:
        if removed_map:
            _save_removed_priority_map(db, removed_map)
        return False

    priority_setting = get_setting(db, SETTING_POSTER_DRIVE_PRIORITY)
    if not priority_setting or not priority_setting.value:
        _save_removed_priority_map(db, removed_map)
        return False

    try:
        priority_data = json.loads(priority_setting.value)
    except json.JSONDecodeError:
        _save_removed_priority_map(db, removed_map)
        log_warning(LogTags.DRIVES, "Skipping priority restore due to invalid poster_drive_priority JSON")
        return False

    drive_ids = priority_data.get("drive_ids", [])
    if not isinstance(drive_ids, list):
        _save_removed_priority_map(db, removed_map)
        return False

    if any(str(existing_id) == str(drive_id) for existing_id in drive_ids):
        _save_removed_priority_map(db, removed_map)
        return False

    insert_index = max(0, min(int(removed_index), len(drive_ids)))
    drive_ids.insert(insert_index, drive_id)
    priority_data["drive_ids"] = drive_ids
    if "enabled_styles" not in priority_data or not isinstance(priority_data.get("enabled_styles"), list):
        priority_data["enabled_styles"] = ["MM2K", "CL2K", "Custom"]

    upsert_setting(db, SETTING_POSTER_DRIVE_PRIORITY, json.dumps(priority_data))
    _save_removed_priority_map(db, removed_map)
    return True

DriveStyle = Literal["CL2K", "MM2K", "Custom"]

class DriveSchema(BaseModel):
    id: int
    name: str
    drive_id: str
    style_type: str
    subscribed: bool
    sync_enabled: bool
    priority: int
    custom_path: str | None
    is_custom: bool
    is_deprecated: bool
    last_synced: datetime | None
    last_rename_processed: datetime | None = None
    sync_file_count: int = 0
    last_files_transferred: int = 0
    poster_count: int = 0
    unprocessed_count: int = 0
    
    @field_serializer('last_synced', 'last_rename_processed')
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        # If datetime is naive, assume it's UTC and make it aware
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    
    model_config = ConfigDict(from_attributes=True)

class DriveUpdateRequest(BaseModel):
    priority: int | None = None
    custom_path: str | None = None
    style_type: DriveStyle | None = None
    subscribed: bool | None = None
    sync_enabled: bool | None = None
    drive_id: str | None = None

class CustomDriveRequest(BaseModel):
    name: str
    drive_id: str | None = None
    style_type: DriveStyle = "Custom"
    custom_path: str | None = None
    priority: int = 0
    subscribed: bool = True
    sync_enabled: bool = True

@router.get("/", response_model=List[DriveSchema])
async def list_drives(db: Session = Depends(get_db)) -> List[DriveSchema]:
    """
    List all available drives from database.
    If database is empty, load from drives.json.
    Ordered by priority (highest first), then name.
    """
    # Check if drives exist in database
    drives = db.query(Drive).order_by(Drive.priority.desc(), Drive.name).all()
    
    # If no drives, load from drives.json
    if not drives:
        log_info(LogTags.STARTUP, "No drives in database, loading from drives.json")
        load_drives_from_json(db)
        drives = db.query(Drive).order_by(Drive.priority.desc(), Drive.name).all()
    
    # Calculate poster counts and stats for each drive
    drive_schemas = []
    for drive in drives:
        poster_count = db.query(Poster).filter(Poster.drive_id == drive.drive_id).count()
        
        # Count unprocessed posters (last_processed IS NULL)
        unprocessed_count = db.query(Poster).filter(
            Poster.drive_id == drive.drive_id,
            Poster.last_processed.is_(None)
        ).count()
        
        drive_dict = {
            'id': drive.id,
            'name': drive.name,
            'drive_id': drive.drive_id,
            'style_type': drive.style_type,
            'subscribed': drive.subscribed,
            'sync_enabled': drive.sync_enabled,
            'priority': drive.priority,
            'custom_path': drive.custom_path,
            'is_custom': drive.is_custom,
            'is_deprecated': drive.is_deprecated,
            'last_synced': drive.last_synced,
            'last_rename_processed': drive.last_rename_processed,
            'sync_file_count': drive.sync_file_count,
            'last_files_transferred': drive.last_files_transferred,
            'poster_count': poster_count,
            'unprocessed_count': unprocessed_count
        }
        drive_schemas.append(DriveSchema(**drive_dict))
    
    return drive_schemas

@router.post("/{drive_id}/subscribe")
async def subscribe_drive(drive_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Subscribe to a drive for syncing"""
    drive = db.query(Drive).filter(Drive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")
    
    drive.subscribed = True
    restored_to_priority = _restore_drive_to_priority(db, drive.id)
    db.commit()
    db.refresh(drive)
    
    log_message = f"Subscribed to drive: {drive.name} ({drive.style_type})"
    if restored_to_priority:
        log_message += " (restored to poster priority)"
    log_user_action(log_message)

    # Auto-scan local folder for drives with GDrive sync disabled.
    # This ensures any files already on disk are indexed immediately after subscribe.
    scan_job_id: int | None = None
    if not drive.sync_enabled:
        from modules.sync import run_sync_one_job
        captured_drive_id = drive.id
        scan_job = Job(
            job_type=f"gdrive_sync:{drive.name}",
            status=JOB_STATUS_PENDING,
            progress=0,
            message=f"Queued initial scan for {drive.name}",
        )
        db.add(scan_job)
        db.commit()
        db.refresh(scan_job)
        scan_job_id = scan_job.id
        log_info(LogTags.DRIVES, f"Auto-scan queued for local-only drive '{drive.name}'", drive=drive.name, job_id=scan_job_id)
        job_queue.submit(
            lambda jid=scan_job_id, did=captured_drive_id: run_sync_one_job(did, jid, triggered_by="auto-scan"),
            scan_job_id,
        )

    drive_response = DriveSchema.model_validate(drive).model_dump(mode="json")
    return {
        "message": f"Subscribed to {drive.name}",
        "drive": drive_response,
        "restored_to_priority": restored_to_priority,
        "scan_job_id": scan_job_id,
    }

@router.post("/{drive_id}/unsubscribe")
async def unsubscribe_drive(drive_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Unsubscribe from a drive"""
    drive = db.query(Drive).filter(Drive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")
    
    drive.subscribed = False
    removed_from_priority = _prune_drive_from_priority(db, drive.id)
    db.commit()
    db.refresh(drive)
    
    log_message = f"Unsubscribed from drive: {drive.name}"
    if removed_from_priority:
        log_message += " (removed from poster priority)"
    log_user_action(log_message)
    drive_response = DriveSchema.model_validate(drive).model_dump(mode="json")
    return {
        "message": f"Unsubscribed from {drive.name}",
        "drive": drive_response,
        "removed_from_priority": removed_from_priority,
    }

@router.patch("/{drive_id}", response_model=DriveSchema)
async def update_drive(drive_id: int, request: DriveUpdateRequest, db: Session = Depends(get_db)) -> DriveSchema:
    """Update drive settings (priority, custom path, style, sync inclusion)"""
    drive = db.query(Drive).filter(Drive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")
    
    changes = []
    if request.priority is not None:
        drive.priority = request.priority
        changes.append(f"priority={request.priority}")
    
    if request.custom_path is not None:
        drive.custom_path = request.custom_path
        changes.append(f"custom_path={request.custom_path}")

    if request.style_type is not None:
        if not drive.is_custom:
            raise HTTPException(status_code=400, detail="Style type can only be changed for custom drives")
        drive.style_type = request.style_type
        changes.append(f"style_type={request.style_type}")

    if request.subscribed is not None:
        if not drive.is_custom:
            raise HTTPException(status_code=400, detail="Sync inclusion can only be changed here for custom drives")
        drive.subscribed = request.subscribed
        changes.append(f"subscribed={request.subscribed}")

    if request.sync_enabled is not None:
        drive.sync_enabled = request.sync_enabled
        changes.append(f"sync_enabled={request.sync_enabled}")

    if request.drive_id is not None:
        if not drive.is_custom:
            raise HTTPException(status_code=400, detail="Google Drive ID can only be changed for custom drives")

        requested_drive_id = request.drive_id.strip()
        if not requested_drive_id:
            requested_drive_id = f"manual-{uuid4()}"

        if requested_drive_id != drive.drive_id:
            existing = db.query(Drive).filter(Drive.drive_id == requested_drive_id).first()
            if existing:
                raise HTTPException(status_code=400, detail="Drive ID already exists")

            db.query(Poster).filter(Poster.drive_id == drive.drive_id).update({"drive_id": requested_drive_id})
            drive.drive_id = requested_drive_id
            changes.append(f"drive_id={requested_drive_id}")
    
    db.commit()
    db.refresh(drive)
    
    if changes:
        log_user_action(f"Updated drive '{drive.name}': {', '.join(changes)}")
    
    return drive

@router.post("/custom", response_model=DriveSchema)
async def create_custom_drive(request: CustomDriveRequest, db: Session = Depends(get_db)) -> DriveSchema:
    """Create a custom user drive"""
    if request.style_type != "Custom":
        raise HTTPException(status_code=400, detail="Custom drives must use Custom style")

    drive_id = (request.drive_id or "").strip()

    # Custom drives can omit Google Drive ID; generate internal unique identifier
    if not drive_id:
        drive_id = f"manual-{uuid4()}"

    # Check if drive_id already exists
    existing = db.query(Drive).filter(Drive.drive_id == drive_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Drive ID already exists")
    
    drive = Drive(
        name=request.name,
        drive_id=drive_id,
        style_type="Custom",
        custom_path=request.custom_path,
        priority=request.priority,
        is_custom=True,
        subscribed=request.subscribed,
        sync_enabled=request.sync_enabled,
    )
    
    db.add(drive)
    db.commit()
    db.refresh(drive)
    
    log_user_action(f"Created custom drive: {drive.name} (Custom) - Drive ID: {drive.drive_id}")
    return drive

@router.delete("/{drive_id}")
async def delete_drive(
    drive_id: int,
    delete_files: bool = False,
    confirm: bool = False,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Delete a drive from the database.
    Can delete custom drives or deprecated preset drives.
    Optionally delete associated poster files.
    
    Args:
        drive_id: ID of the drive to delete
        delete_files: If True, also delete poster files from disk
        confirm: Must be True to execute deletion
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="Must pass confirm=true to delete drive")

    drive = db.query(Drive).filter(Drive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")
    
    # Only allow deleting custom drives or deprecated preset drives
    if not drive.is_custom and not drive.is_deprecated:
        raise HTTPException(status_code=400, detail="Cannot delete active preset drives. Only custom or deprecated drives can be deleted.")
    
    drive_name = drive.name
    drive_type = "custom" if drive.is_custom else "deprecated preset"
    
    # Determine the folder path for this drive using canonical model logic
    folder_path = drive.get_local_path(validate=False)
    
    # Delete poster records from database
    poster_count = db.query(Poster).filter(Poster.drive_id == drive.drive_id).count()
    db.query(Poster).filter(Poster.drive_id == drive.drive_id).delete()
    
    # Delete files if requested
    files_deleted = False
    if delete_files and folder_path.exists():
        try:
            shutil.rmtree(folder_path)
            files_deleted = True
            log_info(LogTags.DRIVES, f"Deleted folder: {folder_path}")
        except Exception as e:
            log_error(LogTags.DRIVES, f"Failed to delete folder {folder_path}: {e}\n{traceback.format_exc()}")
            # Continue with database deletion even if file deletion fails
    
    # Delete drive record
    db.delete(drive)
    removed_from_priority = _prune_drive_from_priority(db, drive.id)
    db.commit()
    
    log_msg = f"Deleted {drive_type} drive: {drive_name} ({poster_count} poster records)"
    if files_deleted:
        log_msg += " and poster files"
    if removed_from_priority:
        log_msg += " (removed from poster priority)"
    log_user_action(log_msg)
    
    return {
        "message": f"Deleted {drive_name}",
        "poster_records_deleted": poster_count,
        "files_deleted": files_deleted
    }

@router.post("/reload")
async def reload_drives(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Manually reload drives from hybrid source (remote with local fallback).
    Adds new drives but doesn't remove existing ones.
    Also refreshes filesystem file counts for all subscribed drives.
    """
    log_user_action("Reloading drives from remote source...")
    from services.drive_loader import load_drives_data
    
    try:
        drives_data = load_drives_data()
        result = load_drives_from_json(db, drives_data)
        if result.get("success"):
            log_user_action(f"Drives reloaded: {result.get('added', 0)} added, {result.get('updated', 0)} updated, {result.get('deprecated', 0)} deprecated, {result.get('reactivated', 0)} reactivated")
        
        # Also refresh file counts from filesystem
        count_result = update_file_counts(db)
        result["file_counts_updated"] = count_result.get("drives_checked", 0)
        
        return result
    except Exception as e:
        log_error(LogTags.DRIVES, f"Failed to reload drives: {e}\n{traceback.format_exc()}")
        return {"success": False, "error": str(e)}

@router.post("/refresh-counts")
async def refresh_file_counts(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Refresh filesystem file counts for all drives.
    Counts actual files in each drive's directory and updates sync_file_count.
    """
    log_user_action("Refreshing file counts from filesystem...")
    result = update_file_counts(db)
    return result

def update_file_counts(db: Session) -> Dict[str, Any]:
    """
    Update sync_file_count for all drives by counting actual files in their directories.
    Returns dict with counts updated.
    """
    drives = db.query(Drive).all()
    drives_checked = 0
    total_files = 0
    
    for drive in drives:
        try:
            drive_path = drive.get_local_path(validate=False)
            
            if drive_path.exists():
                # Count only image files (jpg, jpeg, png), exclude hidden files
                file_count = sum(
                    1 for f in drive_path.rglob('*') 
                    if f.is_file() 
                    and not f.name.startswith('.')
                    and f.suffix.lower() in ['.jpg', '.jpeg', '.png']
                )
                drive.sync_file_count = file_count
                total_files += file_count
                drives_checked += 1
                log_debug(LogTags.DRIVES, f"{drive.name}: {file_count} files", drive=drive.name, file_count=file_count)
            else:
                # Directory doesn't exist, set count to 0
                drive.sync_file_count = 0
                drives_checked += 1
                log_debug(LogTags.DRIVES, f"{drive.name}: directory not found, set to 0", drive=drive.name)
        except Exception as e:
            log_error(LogTags.DRIVES, f"Error counting files for {drive.name}: {e}\n{traceback.format_exc()}", drive=drive.name, error=str(e))
    
    db.commit()
    
    log_info(LogTags.DRIVES, f"File counts refreshed: {drives_checked} drives checked, {total_files} total files", drives_checked=drives_checked, total_files=total_files)
    
    return {
        "success": True,
        "drives_checked": drives_checked,
        "total_files": total_files
    }

def load_drives_from_json(db: Session, drives_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Load drives from provided data or from legacy path into database.
    Syncs preset drives: adds new ones, updates existing ones, marks deprecated ones.
    Custom drives (is_custom=True) are never marked as deprecated.
    
    Args:
        db: Database session
        drives_data: Optional dictionary containing drives data from remote URL or cache.
                     Must be provided (no local fallback).
    
    Returns:
        Dictionary with success status and counts of added/updated/deprecated drives
    """
    if drives_data is None:
        return {"success": False, "error": "No drives data provided. Remote fetch or cache required."}
    
    # Track all preset drive_ids from the JSON file
    preset_drive_ids = set()
    
    # Load/update drives from data
    added_count = 0
    updated_count = 0
    reactivated_count = 0
    
    total_drives_in_source = len(drives_data.get("drives", []))
    log_info(LogTags.DRIVES, f"Processing {total_drives_in_source} drives from source")
    
    for drive_data in drives_data.get("drives", []):
        drive_id = drive_data["drive_id"]
        preset_drive_ids.add(drive_id)
        
        # Check if drive already exists
        existing = db.query(Drive).filter(Drive.drive_id == drive_id).first()
        if not existing:
            # Add new preset drive
            drive = Drive(
                name=drive_data["name"],
                drive_id=drive_id,
                style_type=drive_data["style_type"],
                subscribed=False,
                is_custom=False,
                is_deprecated=False
            )
            db.add(drive)
            added_count += 1
        else:
            # Update existing drive
            changed = False
            
            # Update name and style_type if they changed
            if existing.name != drive_data["name"] or existing.style_type != drive_data["style_type"]:
                existing.name = drive_data["name"]
                existing.style_type = drive_data["style_type"]
                changed = True
            
            # Reactivate if it was deprecated (drive came back to the list)
            if existing.is_deprecated:
                existing.is_deprecated = False
                log_info(LogTags.DRIVES, f"Reactivating previously deprecated drive: {existing.name}")
                reactivated_count += 1
                changed = True
            
            if changed:
                updated_count += 1
    
    # Mark preset drives as deprecated if they're no longer in the JSON file
    # (Custom drives are never marked as deprecated)
    deprecated_count = 0
    
    # Get all preset drives that are not already deprecated
    all_preset_drives = db.query(Drive).filter(
        Drive.is_custom == False,
        Drive.is_deprecated == False
    ).all()
    
    log_info(LogTags.DRIVES, f"Checking {len(all_preset_drives)} preset drives against {len(preset_drive_ids)} drives from source")
    
    obsolete_drives = [d for d in all_preset_drives if d.drive_id not in preset_drive_ids]
    
    for drive in obsolete_drives:
        log_warning(LogTags.DRIVES, f"Marking preset drive as deprecated: {drive.name} ({drive.drive_id})")
        drive.is_deprecated = True
        deprecated_count += 1
    
    db.commit()
    
    log_info(LogTags.DRIVES, f"Sync complete: {added_count} added, {updated_count} updated, {deprecated_count} deprecated, {reactivated_count} reactivated")
    log_info(LogTags.DRIVES, f"Total in source: {total_drives_in_source}, Total in DB: {db.query(Drive).count()} (including custom)")
    
    return {
        "success": True, 
        "added": added_count, 
        "updated": updated_count, 
        "deprecated": deprecated_count,
        "reactivated": reactivated_count
    }