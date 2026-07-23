from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from uuid import uuid4
import traceback
import shutil
import json
from pydantic import BaseModel, ConfigDict, field_serializer
from datetime import datetime, timezone

from database import get_db
from models.artwork_drive import ArtworkDrive
from models.artwork import Artwork
from models.job import Job, JOB_STATUS_PENDING, JOB_STATUSES_ACTIVE
from models.setting import get_setting, upsert_setting
from core.logging import LogTags, log_info, log_warning, log_error, log_user_action
from core.job_queue import job_queue
from services.artwork_drive_loader import get_artwork_drive_descriptions, load_artwork_drives_data
from services.artwork_sync import ArtworkSyncService
from modules.artwork_sync import run_artwork_sync_job
from modules.sync import run_sync_all_job

router = APIRouter(prefix="/api/artwork-drives", tags=["artwork-drives"])

SETTING_ARTWORK_DRIVE_PRIORITY = "artwork_drive_priority"


class ArtworkDriveSchema(BaseModel):
    id: int
    name: str
    display_name: str | None = None
    drive_id: str
    description: str | None = None
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
    artwork_count: int = 0
    logo_count: int = 0
    background_count: int = 0
    squareart_count: int = 0

    @field_serializer('last_synced', 'last_rename_processed')
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    model_config = ConfigDict(from_attributes=True)


class ArtworkDriveUpdateRequest(BaseModel):
    priority: int | None = None
    custom_path: str | None = None
    subscribed: bool | None = None
    sync_enabled: bool | None = None
    drive_id: str | None = None


class CustomArtworkDriveRequest(BaseModel):
    name: str
    drive_id: str | None = None
    custom_path: str | None = None
    priority: int = 0
    subscribed: bool = True
    sync_enabled: bool = True


class ArtworkDrivePriorityRequest(BaseModel):
    drive_ids: List[int]


@router.get("/", response_model=List[ArtworkDriveSchema])
def list_artwork_drives(db: Session = Depends(get_db)) -> List[ArtworkDriveSchema]:
    """List all artwork drives, highest priority first. Loads presets if the table is empty."""
    drives = db.query(ArtworkDrive).order_by(ArtworkDrive.priority.desc(), ArtworkDrive.name).all()

    if not drives:
        log_info(LogTags.STARTUP, "No artwork drives in database, loading from artwork_drives.json")
        load_artwork_drives_from_json(db, load_artwork_drives_data())
        drives = db.query(ArtworkDrive).order_by(ArtworkDrive.priority.desc(), ArtworkDrive.name).all()

    descriptions = get_artwork_drive_descriptions()

    # Indexed asset counts per drive, grouped by artwork_type, in one aggregate query.
    type_counts: Dict[str, Dict[str, int]] = {}
    for drive_id, artwork_type, count in (
        db.query(Artwork.artwork_drive_id, Artwork.artwork_type, func.count(Artwork.id))
        .group_by(Artwork.artwork_drive_id, Artwork.artwork_type)
        .all()
    ):
        type_counts.setdefault(drive_id, {})[artwork_type] = count

    result = []
    for drive in drives:
        counts = type_counts.get(drive.drive_id, {})
        logo = counts.get('logo', 0)
        background = counts.get('background', 0)
        squareart = counts.get('squareart', 0)
        drive_dict = {
            'id': drive.id,
            'name': drive.name,
            'display_name': drive.display_name,
            'drive_id': drive.drive_id,
            'description': descriptions.get(drive.drive_id),
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
            'artwork_count': logo + background + squareart,
            'logo_count': logo,
            'background_count': background,
            'squareart_count': squareart,
        }
        result.append(ArtworkDriveSchema(**drive_dict))

    return result


def _append_artwork_drive_to_priority(db: Session, drive_id: int) -> bool:
    """Append an artwork drive to the bottom of artwork priority if not already present."""
    setting = get_setting(db, SETTING_ARTWORK_DRIVE_PRIORITY)
    data: Dict[str, Any] = {}
    if setting and setting.value:
        try:
            data = json.loads(setting.value)
        except json.JSONDecodeError:
            data = {}
    drive_ids = data.get("drive_ids", [])
    if not isinstance(drive_ids, list):
        drive_ids = []
    if any(str(existing_id) == str(drive_id) for existing_id in drive_ids):
        return False
    drive_ids.append(drive_id)
    data["drive_ids"] = drive_ids
    upsert_setting(db, SETTING_ARTWORK_DRIVE_PRIORITY, json.dumps(data))
    return True


@router.post("/{drive_id}/subscribe")
def subscribe_artwork_drive(drive_id: int, add_to_priority: bool = False, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Subscribe to an artwork drive for syncing."""
    from api.jobs import _run_job_task

    drive = db.query(ArtworkDrive).filter(ArtworkDrive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Artwork drive not found")

    drive.subscribed = True
    added_to_priority = _append_artwork_drive_to_priority(db, drive.id) if add_to_priority else False
    db.commit()
    db.refresh(drive)

    log_user_action(f"Subscribed to artwork drive: {drive.name}" + (" (added to artwork priority)" if added_to_priority else ""))

    # Auto-scan the local folder for drives with GDrive sync disabled, so files already on
    # disk are indexed immediately after subscribe (mirrors poster subscribe behaviour).
    scan_job_id: Optional[int] = None
    if not drive.sync_enabled:
        captured_drive_id = drive.id
        scan_job = Job(
            job_type=f"Artwork Sync: {drive.name}",
            status=JOB_STATUS_PENDING,
            progress=0,
            message=f"Queued initial scan for {drive.name}",
        )
        db.add(scan_job)
        db.commit()
        db.refresh(scan_job)
        scan_job_id = scan_job.id
        log_info(LogTags.JOB, f"Auto-scan queued for local-only artwork drive '{drive.name}'", drive=drive.name, job_id=scan_job_id)
        job_queue.submit(
            _run_job_task, scan_job_id, scan_job_id, f"Starting initial scan of {drive.name}",
            lambda did=captured_drive_id, jid=scan_job_id: run_artwork_sync_job(did, jid),
        )

    return {
        "message": f"Subscribed to {drive.name}",
        "drive": ArtworkDriveSchema.model_validate(drive).model_dump(mode="json"),
        "added_to_priority": added_to_priority,
        "scan_job_id": scan_job_id,
    }


@router.post("/{drive_id}/unsubscribe")
def unsubscribe_artwork_drive(drive_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Unsubscribe from an artwork drive."""
    drive = db.query(ArtworkDrive).filter(ArtworkDrive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Artwork drive not found")

    drive.subscribed = False
    db.commit()
    db.refresh(drive)

    log_user_action(f"Unsubscribed from artwork drive: {drive.name}")
    return {
        "message": f"Unsubscribed from {drive.name}",
        "drive": ArtworkDriveSchema.model_validate(drive).model_dump(mode="json"),
    }


@router.patch("/{drive_id}", response_model=ArtworkDriveSchema)
def update_artwork_drive(drive_id: int, request: ArtworkDriveUpdateRequest, db: Session = Depends(get_db)) -> ArtworkDriveSchema:
    """Update artwork drive settings (priority, custom path, sync inclusion)."""
    drive = db.query(ArtworkDrive).filter(ArtworkDrive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Artwork drive not found")

    changes = []
    if request.priority is not None:
        drive.priority = request.priority
        changes.append(f"priority={request.priority}")

    if 'custom_path' in request.model_fields_set:
        drive.custom_path = request.custom_path or None
        changes.append(f"custom_path={request.custom_path}")

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
        requested_drive_id = request.drive_id.strip() or f"manual-{uuid4()}"
        if requested_drive_id != drive.drive_id:
            existing = db.query(ArtworkDrive).filter(ArtworkDrive.drive_id == requested_drive_id).first()
            if existing:
                raise HTTPException(status_code=400, detail="Drive ID already exists")
            drive.drive_id = requested_drive_id
            changes.append(f"drive_id={requested_drive_id}")

    db.commit()
    db.refresh(drive)

    if changes:
        log_user_action(f"Updated artwork drive '{drive.name}': {', '.join(changes)}")

    return drive


@router.post("/custom", response_model=ArtworkDriveSchema)
def create_custom_artwork_drive(request: CustomArtworkDriveRequest, db: Session = Depends(get_db)) -> ArtworkDriveSchema:
    """Create a custom user artwork drive."""
    drive_id = (request.drive_id or "").strip() or f"manual-{uuid4()}"

    existing = db.query(ArtworkDrive).filter(ArtworkDrive.drive_id == drive_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Drive ID already exists")

    drive = ArtworkDrive(
        name=request.name,
        drive_id=drive_id,
        custom_path=request.custom_path,
        priority=request.priority,
        is_custom=True,
        subscribed=request.subscribed,
        sync_enabled=request.sync_enabled,
    )
    db.add(drive)
    db.commit()
    db.refresh(drive)

    log_user_action(f"Created custom artwork drive: {drive.name} - Drive ID: {drive.drive_id}")
    return drive


@router.delete("/{drive_id}")
def delete_artwork_drive(
    drive_id: int,
    delete_files: bool = False,
    confirm: bool = False,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Delete a custom or deprecated artwork drive, optionally removing its files."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Must pass confirm=true to delete artwork drive")

    drive = db.query(ArtworkDrive).filter(ArtworkDrive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Artwork drive not found")

    if not drive.is_custom and not drive.is_deprecated:
        raise HTTPException(status_code=400, detail="Cannot delete active preset artwork drives. Only custom or deprecated drives can be deleted.")

    drive_name = drive.name
    folder_path = drive.get_local_path(validate=False)

    files_deleted = False
    if delete_files and folder_path.exists():
        try:
            shutil.rmtree(folder_path)
            files_deleted = True
            log_info(LogTags.DRIVES, f"Deleted artwork folder: {folder_path}")
        except Exception as e:
            log_error(LogTags.DRIVES, f"Failed to delete artwork folder {folder_path}: {e}\n{traceback.format_exc()}")

    # Remove indexed artwork rows for this drive.
    artwork_records = db.query(Artwork).filter(Artwork.artwork_drive_id == drive.drive_id).count()
    if artwork_records:
        db.query(Artwork).filter(Artwork.artwork_drive_id == drive.drive_id).delete(synchronize_session=False)

    db.delete(drive)
    db.commit()

    log_user_action(f"Deleted artwork drive: {drive_name} ({artwork_records} artwork records)" + (" and files" if files_deleted else ""))
    return {"message": f"Deleted {drive_name}", "artwork_records_deleted": artwork_records, "files_deleted": files_deleted}


@router.post("/reload")
def reload_artwork_drives(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Reload artwork drives from remote/cache/bundled source, adding new and refreshing counts."""
    log_user_action("Reloading artwork drives from source...")
    try:
        result = load_artwork_drives_from_json(db, load_artwork_drives_data())
        count_result = refresh_artwork_counts(db)
        result["file_counts_updated"] = count_result.get("drives_checked", 0)
        return result
    except Exception as e:
        log_error(LogTags.DRIVES, f"Failed to reload artwork drives: {e}\n{traceback.format_exc()}")
        return {"success": False, "error": str(e)}


@router.post("/refresh-counts")
def refresh_artwork_counts(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Re-index all artwork drives from disk (rescan subfolders, upsert the assets table)."""
    drives = db.query(ArtworkDrive).all()
    service = ArtworkSyncService(db)
    drives_checked = 0
    total_files = 0
    for drive in drives:
        try:
            index_result = service._index_artwork(drive)
            drive.sync_file_count = index_result["total"]
            total_files += index_result["total"]
            drives_checked += 1
        except Exception as e:
            log_error(LogTags.DRIVES, f"Error indexing artwork files for {drive.name}: {e}")
    db.commit()
    log_info(LogTags.DRIVES, f"Artwork drives re-indexed: {drives_checked} drives, {total_files} files")
    return {"success": True, "drives_checked": drives_checked, "total_files": total_files}


@router.post("/{drive_id}/sync")
def sync_artwork_drive(drive_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Start a sync job for a single artwork drive."""
    from api.jobs import _run_job_task

    drive = db.query(ArtworkDrive).filter(ArtworkDrive.id == drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Artwork drive not found")
    if not drive.subscribed:
        raise HTTPException(status_code=400, detail="Artwork drive not subscribed")

    job_type = f"Artwork Sync: {drive.name}"
    existing = db.query(Job).filter(Job.job_type == job_type, Job.status.in_(JOB_STATUSES_ACTIVE)).first()
    if existing:
        log_warning(LogTags.JOB, f"Duplicate artwork sync for '{drive.name}' (job {existing.id})")

    job = Job(job_type=job_type, status=JOB_STATUS_PENDING, progress=0, message=f"Queued artwork sync for {drive.name}")
    db.add(job)
    db.commit()
    db.refresh(job)

    captured_drive_id = drive.id
    job_id = job.id
    log_user_action(f"Artwork sync requested for '{drive.name}'")
    job_queue.submit(
        _run_job_task, job_id, job_id, f"Starting artwork sync of {drive.name}",
        lambda: run_artwork_sync_job(captured_drive_id, job_id),
    )
    return {"job_id": job_id, "message": f"Sync started for {drive.name}"}


@router.post("/sync-all")
def sync_all_artwork_drives(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Start a sync job for all subscribed artwork drives."""
    from api.jobs import _run_job_task

    drives = db.query(ArtworkDrive).filter(ArtworkDrive.subscribed == True).all()
    if not drives:
        raise HTTPException(status_code=400, detail="No subscribed artwork drives found")

    job = Job(
        job_type=f"Artwork Sync All ({len(drives)} drives)",
        status=JOB_STATUS_PENDING,
        progress=0,
        message=f"Queued artwork sync for {len(drives)} drives",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id = job.id
    log_user_action(f"Artwork Sync All requested for {len(drives)} drives")
    job_queue.submit(
        _run_job_task, job_id, job_id, f"Starting artwork sync of {len(drives)} drives",
        lambda: run_sync_all_job(job_id, sync_posters=False, sync_artwork=True),
    )
    return {"job_id": job_id, "message": f"Sync started for {len(drives)} artwork drives"}


@router.get("/priority")
def get_artwork_drive_priority(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get the artwork drive priority order (list of artwork drive ids, highest priority first)."""
    setting = get_setting(db, SETTING_ARTWORK_DRIVE_PRIORITY)
    if setting and setting.value:
        try:
            data = json.loads(setting.value)
            return {"drive_ids": data.get("drive_ids", [])}
        except json.JSONDecodeError:
            log_warning(LogTags.DRIVES, "Invalid artwork_drive_priority JSON; returning empty order")
    return {"drive_ids": []}


@router.post("/priority")
def save_artwork_drive_priority(priority: ArtworkDrivePriorityRequest, db: Session = Depends(get_db)) -> Dict[str, bool]:
    """Save the artwork drive priority order."""
    try:
        upsert_setting(db, SETTING_ARTWORK_DRIVE_PRIORITY, json.dumps({"drive_ids": priority.drive_ids}))
        db.commit()
        log_user_action(f"Updated artwork drive priority: {len(priority.drive_ids)} drives")
        return {"success": True}
    except Exception as e:
        db.rollback()
        log_error(LogTags.DRIVES, f"Error saving artwork drive priority: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error saving artwork drive priority")


def load_artwork_drives_from_json(db: Session, drives_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Sync preset artwork drives into the database: add new, update changed, mark deprecated.
    Custom drives (is_custom=True) are never marked as deprecated.
    """
    if drives_data is None:
        return {"success": False, "error": "No artwork drives data provided."}

    preset_drive_ids = set()
    added_count = 0
    updated_count = 0
    reactivated_count = 0

    for drive_data in drives_data.get("drives", []):
        drive_id = drive_data["drive_id"]
        preset_drive_ids.add(drive_id)

        existing = db.query(ArtworkDrive).filter(ArtworkDrive.drive_id == drive_id).first()
        if not existing:
            db.add(ArtworkDrive(
                name=drive_data["name"],
                display_name=drive_data.get("display_name"),
                drive_id=drive_id,
                subscribed=False,
                is_custom=False,
                is_deprecated=False,
            ))
            added_count += 1
        else:
            changed = False
            new_display_name = drive_data.get("display_name")
            if existing.name != drive_data["name"] or existing.display_name != new_display_name:
                existing.name = drive_data["name"]
                existing.display_name = new_display_name
                changed = True
            if existing.is_deprecated:
                existing.is_deprecated = False
                reactivated_count += 1
                changed = True
            if changed:
                updated_count += 1

    deprecated_count = 0
    all_preset_drives = db.query(ArtworkDrive).filter(
        ArtworkDrive.is_custom == False,
        ArtworkDrive.is_deprecated == False,
    ).all()
    for drive in all_preset_drives:
        if drive.drive_id not in preset_drive_ids:
            log_warning(LogTags.DRIVES, f"Marking preset artwork drive as deprecated: {drive.name}")
            drive.is_deprecated = True
            deprecated_count += 1

    db.commit()

    log_info(LogTags.DRIVES, f"Artwork drive sync complete: {added_count} added, {updated_count} updated, {deprecated_count} deprecated, {reactivated_count} reactivated")
    return {
        "success": True,
        "added": added_count,
        "updated": updated_count,
        "deprecated": deprecated_count,
        "reactivated": reactivated_count,
    }
