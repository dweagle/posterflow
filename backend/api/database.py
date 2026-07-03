"""
Database Maintenance API

Endpoints for database cleanup and maintenance operations.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Any, Dict
import os

from database import get_db
from models.poster import Poster
from models.drive import Drive
from models.schedule import Schedule
from core.logging import LogTags, log_info, log_success, log_warning, log_user_action

router = APIRouter(prefix="/api/database", tags=["database"])


@router.get("/cleanup/preview")
def preview_cleanup(full: bool = False, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Preview orphaned poster records that would be deleted.
    Shows records where the file_path doesn't exist on disk.
    
    Args:
        full: If True, returns all orphaned records. If False, returns only first 10 as samples.
    
    Returns summary statistics and sample/full records.
    """
    try:
        log_info(LogTags.DATABASE, "Starting cleanup preview...")
        
        # Build drive id -> Drive object map for state checks
        drives = db.query(Drive).all()
        drive_id_to_drive: Dict[str, Drive] = {d.drive_id: d for d in drives}

        # Exclude internal tracking records (e.g. border replacer cache entries).
        # These are not real poster inventory and are managed separately.
        all_records = db.query(Poster).filter(Poster.drive_id != "border_processed").all()
        
        orphaned = []
        by_drive_name: Dict[str, int] = {}
        
        # Check each record
        for poster in all_records:
            if not os.path.exists(poster.file_path):
                drive = drive_id_to_drive.get(poster.drive_id)

                if drive is None:
                    drive_name = poster.drive_id or "Unknown"
                    reason = "Drive record missing"
                elif not drive.subscribed:
                    drive_name = drive.name
                    reason = "Drive unsubscribed"
                elif drive.is_deprecated:
                    drive_name = drive.name
                    reason = "Drive deprecated"
                else:
                    drive_name = drive.name
                    reason = "File deleted from disk"
                orphaned.append({
                    'id': poster.id,
                    'file_name': os.path.basename(poster.file_path),
                    'file_path': poster.file_path,
                    'drive_id': poster.drive_id,
                    'drive_name': drive_name,
                    'reason': reason,
                    'downloaded_at': poster.downloaded_at.isoformat() if poster.downloaded_at else None,
                    'last_processed': poster.last_processed.isoformat() if poster.last_processed else None
                })
                by_drive_name[drive_name] = by_drive_name.get(drive_name, 0) + 1
        
        log_info(
            LogTags.DATABASE,
            f"Found {len(orphaned)} orphaned records",
            total=len(all_records),
            orphaned=len(orphaned)
        )
        
        result = {
            'total_records': len(all_records),
            'orphaned_count': len(orphaned),
            'by_location': by_drive_name,
            'by_drive': by_drive_name,
            'would_delete': len(orphaned)
        }
        
        # Include all records if full=True, otherwise just first 10
        if full:
            result['orphaned_records'] = orphaned
        else:
            result['sample_records'] = orphaned[:10]
        
        return result
        
    except Exception as e:
        log_warning(LogTags.DATABASE, f"Error previewing cleanup: {e}")
        raise HTTPException(status_code=500, detail="Error previewing cleanup")


@router.post("/cleanup/execute")
def execute_cleanup(
    confirm: bool = False,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Execute cleanup of orphaned poster records.
    Deletes records where the file_path doesn't exist on disk.
    
    Args:
        confirm: Must be True to actually execute (safety check)
        
    Returns summary of deleted records.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Must pass confirm=true to execute cleanup"
        )
    
    try:
        log_user_action("Database cleanup requested", confirm=confirm)
        log_info(LogTags.DATABASE, "Starting database cleanup...")
        
        # Exclude internal tracking records — they are not user poster inventory
        all_records = db.query(Poster).filter(Poster.drive_id != "border_processed").all()
        
        deleted_ids = []
        deleted_paths = []
        by_location = {}
        
        # Check each record and delete if orphaned
        for poster in all_records:
            if not os.path.exists(poster.file_path):
                deleted_ids.append(poster.id)
                deleted_paths.append(poster.file_path)
                
                # Categorize
                if poster.file_path.startswith('/posters/gdrive/'):
                    location = '/posters/gdrive/'
                elif poster.file_path.startswith('/posters/assets/tmp/'):
                    location = '/posters/assets/tmp/'
                elif poster.file_path.startswith('/posters/assets/'):
                    location = '/posters/assets/'
                else:
                    location = 'other'
                
                by_location[location] = by_location.get(location, 0) + 1
                
                # Delete the record
                db.delete(poster)
        
        # Commit all deletions
        db.commit()
        
        log_success(
            LogTags.DATABASE,
            f"Cleanup complete: deleted {len(deleted_ids)} orphaned records",
            deleted=len(deleted_ids),
            by_location=by_location
        )
        log_user_action("Database cleanup completed", deleted_count=len(deleted_ids))
        
        return {
            'success': True,
            'deleted_count': len(deleted_ids),
            'deleted_ids': deleted_ids[:100],  # First 100 IDs
            'by_location': by_location,
            'message': f'Successfully deleted {len(deleted_ids)} orphaned poster records'
        }
        
    except Exception as e:
        db.rollback()
        log_warning(LogTags.DATABASE, f"Error executing cleanup: {e}")
        raise HTTPException(status_code=500, detail="Error executing cleanup")


@router.get("/stats")
def database_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get statistics about the database.
    
    Returns counts and breakdowns of poster records.
    """
    try:
        total = db.query(Poster).count()
        
        # By location
        gdrive_count = db.query(Poster).filter(
            Poster.file_path.like('/posters/gdrive/%')
        ).count()
        
        assets_dir = db.query(Poster).filter(
            Poster.file_path.like('/posters/assets/%')
        ).count()
        
        assets_tmp = db.query(Poster).filter(
            Poster.file_path.like('/posters/assets/tmp/%')
        ).count()
        
        # By processing state
        never_processed = db.query(Poster).filter(
            Poster.last_processed == None
        ).count()
        
        processed = db.query(Poster).filter(
            Poster.last_processed != None
        ).count()
        
        # Check for orphaned records
        all_records = db.query(Poster).all()
        orphaned = sum(1 for p in all_records if not os.path.exists(p.file_path))

        # Check for schedules pointing to deleted drives
        all_drive_ids = {d.id for d in db.query(Drive.id).all()}
        dangling_schedules = (
            db.query(Schedule)
            .filter(Schedule.drive_id != None)
            .all()
        )
        dangling_schedule_count = sum(1 for s in dangling_schedules if s.drive_id not in all_drive_ids)
        
        return {
            'total_records': total,
            'by_location': {
                'synced_drives': gdrive_count,
                'assets_total': assets_dir,
                'assets_tmp': assets_tmp,
                'assets_processed': assets_dir - assets_tmp
            },
            'by_processing_state': {
                'never_processed': never_processed,
                'processed': processed
            },
            'health': {
                'orphaned_records': orphaned,
                'healthy_records': total - orphaned,
                'orphan_percentage': round((orphaned / total * 100), 2) if total > 0 else 0,
                'dangling_schedules': dangling_schedule_count,
            }
        }
        
    except Exception as e:
        log_warning(LogTags.DATABASE, f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail="Error getting database stats")
