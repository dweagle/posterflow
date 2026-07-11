from pathlib import Path
import os
import time
from typing import Any, Callable, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from services.rclone import RcloneService
from util.data.extract import extract_tmdb_id
from models.drive import Drive
from models.poster import Poster
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    update_job_state,
)
from core.logging import LogTags, log_success, log_error, log_warning, log_info, log_debug, log_section_start, log_section_end

ProgressCallback = Callable[[str, int, int, str], None]

class PosterSyncService:
    """Service for syncing posters from Google Drives"""
    
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rclone = RcloneService()
    
    def sync_drive(
        self,
        drive_id: int,
        job_id: int,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> dict[str, Any]:
        """
        Sync all posters from a subscribed drive.
        
        Args:
            drive_id: Database ID of the drive to sync
            job_id: Job ID for tracking
            progress_callback: Optional callback function(phase, current, total, message) for progress updates
            
        Returns dict with results.
        """
        try:
            # Get drive from database
            drive = self.db.query(Drive).filter(Drive.id == drive_id).first()
            if not drive:
                return {"success": False, "error": "Drive not found"}
            
            if not drive.subscribed:
                return {"success": False, "error": "Drive not subscribed"}
            
            # Log section start
            log_section_start(LogTags.SYNC, f"Sync Starting: {drive.name}")
            
            # Get job
            job = self.db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return {"success": False, "error": "Job not found"}
            
            # Update job status
            job.status = JOB_STATUS_RUNNING
            job.message = f"Starting sync of {drive.name}"
            self.db.commit()
            
            # Get target folder from drive model (centralizes path logic)
            local_folder = drive.get_local_path()
            
            # Pre-sync cleanup: Remove DB records for files missing from disk
            # This ensures that when rclone re-downloads them, they're counted as "added"
            image_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
            existing_files_on_disk = set()
            
            if local_folder.exists() and local_folder.is_dir():
                try:
                    for file_path in local_folder.iterdir():
                        if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                            existing_files_on_disk.add(file_path.name)
                except Exception as e:
                    log_warning(LogTags.SYNC, f"Could not scan folder before sync: {e}", drive=drive.name)
            
            # Get lightweight (id, file_name) pairs — avoid loading full ORM objects
            # into the session identity map for potentially 50k+ records.
            db_name_ids = (
                self.db.query(Poster.id, Poster.file_name)
                .filter(Poster.drive_id == drive.drive_id)
                .all()
            )

            # Find IDs for records whose files don't exist on disk
            ids_to_delete = [
                r.id for r in db_name_ids if r.file_name not in existing_files_on_disk
            ]

            # Delete those records so rclone re-downloads count as "added"
            if ids_to_delete:
                log_info(
                    LogTags.SYNC,
                    f"Pre-sync cleanup: Removing {len(ids_to_delete)} DB records for missing files",
                    drive=drive.name,
                    missing_count=len(ids_to_delete)
                )
                self.db.query(Poster).filter(Poster.id.in_(ids_to_delete)).delete(synchronize_session=False)
                self.db.commit()
                log_debug(LogTags.SYNC, "Cleared records for files to be re-downloaded", drive=drive.name, deleted=len(ids_to_delete))
            
            local_folder.mkdir(parents=True, exist_ok=True)
            
            # Phase 1: Sync folder using rclone (0-70%)
            if progress_callback:
                progress_callback("syncing", 0, 100, f"Starting sync of {drive.name}...")
            
            # Track rclone progress with monotonic guarantee (never goes backwards)
            last_update = [{
                "progress": 0,
                "message": "",
                "max_checked": 0,
                "max_transferred": 0,
                "max_transfer_total": 0,  # real total files to transfer from rclone Transferred: X/Y stats
                "last_file": "",
                "last_emit_at": 0.0,
            }]
            
            # Define progress callback for file-level updates from rclone
            def file_progress_callback(
                filename: str,
                files_checked: int,
                files_transferred: int,
                rclone_phase: str,
                transfer_total_hint: int = 0,
            ) -> None:
                """Called when rclone reports progress
                Args:
                    filename: Current file being processed
                    files_checked: Number of files checked so far
                    files_transferred: Number of files transferred so far
                    rclone_phase: 'checking' or 'transferring'
                """
                try:
                    if not progress_callback:
                        return
                    
                    import math
                    
                    # Track maximum values seen (counters can sometimes fluctuate)
                    last_update[0]["max_checked"] = max(last_update[0]["max_checked"], files_checked)
                    last_update[0]["max_transferred"] = max(last_update[0]["max_transferred"], files_transferred)
                    # Track real transfer total from rclone's "Transferred: X / Y" stats line
                    if transfer_total_hint > 0:
                        last_update[0]["max_transfer_total"] = max(last_update[0]["max_transfer_total"], transfer_total_hint)
                    
                    # Calculate progress based on current state
                    # Split the 0-70% range: 
                    # - Checking phase: 0-20% (based on file count growth)
                    # - Transferring phase: 20-70% (based on transfer progress)
                    
                    new_progress = 0
                    message = filename
                    
                    if rclone_phase == 'checking' and last_update[0]["max_checked"] > 0:
                        # During checking, show gradual progress based on files discovered
                        # Use logarithmic scale for smoother progress
                        log_progress = math.log(last_update[0]["max_checked"] + 1) / math.log(10000)  # Assume ~10k files max
                        new_progress = int(min(log_progress, 1.0) * 20)  # 0-20%
                        if filename and 'checking files:' in filename.lower() and '/' in filename:
                            message = filename
                        else:
                            message = "Listing remote and local files..."
                    
                    elif rclone_phase == 'transferring' and last_update[0]["max_checked"] > 0:
                        # During transfer, show progress from 20-70%
                        # Use the actual transfer total from rclone stats when available.
                        # max_checked reflects total files *listed* in the drive (could be 20k+),
                        # while max_transfer_total reflects how many actually need downloading.
                        known_transfer_total = last_update[0]["max_transfer_total"]
                        if known_transfer_total > 0:
                            transfer_total = max(known_transfer_total, last_update[0]["max_transferred"], 1)
                        else:
                            transfer_total = max(last_update[0]["max_checked"], last_update[0]["max_transferred"], 1)
                        transfer_percent = last_update[0]["max_transferred"] / transfer_total
                        new_progress = 20 + int(transfer_percent * 50)
                        if filename and len(filename) < 140 and not filename.lower().startswith("checking files") and "transferred" not in filename.lower():
                            last_update[0]["last_file"] = filename

                        stats_message = (
                            f"Syncing files: {last_update[0]['max_transferred']:,}/{transfer_total:,} transferred"
                        )
                        if last_update[0]["last_file"]:
                            message = f"{stats_message} | {last_update[0]['last_file']}"
                        else:
                            message = stats_message
                    
                    # CRITICAL: Only update if progress increased (monotonic progress)
                    # This prevents backwards jumps when rclone switches between checking/transferring
                    now = time.monotonic()
                    transfer_file_changed = (
                        rclone_phase == 'transferring'
                        and message != last_update[0]["message"]
                    )

                    should_emit = (
                        transfer_file_changed
                        or new_progress > last_update[0]["progress"]
                        or (
                            message != last_update[0]["message"]
                            and (now - float(last_update[0]["last_emit_at"])) >= 1.0
                        )
                    )

                    if should_emit:
                        last_update[0]["progress"] = new_progress
                        last_update[0]["message"] = message
                        last_update[0]["last_emit_at"] = now
                        progress_callback("syncing", new_progress, 100, message)
                    
                except Exception as e:
                    log_warning(LogTags.SYNC, f"Error updating progress: {str(e)}")
            
            if not drive.sync_enabled:
                log_info(
                    LogTags.SYNC,
                    f"Drive '{drive.name}' has GDrive sync disabled; skipping rclone and scanning local files only",
                    drive=drive.name,
                )
                result = {"success": True, "files_transferred": 0}
            else:
                result = self.rclone.sync_folder(drive.drive_id, local_folder, drive_name=drive.name, progress_callback=file_progress_callback)
            
            # Phase 2: Validate result (70%)
            if progress_callback:
                progress_callback("validating", 70, 100, "Rclone sync completed, validating...")
            
            if not result["success"]:
                update_job_state(
                    self.db,
                    job,
                    status=JOB_STATUS_FAILED,
                    error="Rclone sync failed",
                    progress=70,
                    completed_at=datetime.now(timezone.utc),
                )
                return {"success": False, "error": "Rclone sync failed"}
            
            files_transferred = result.get("files_transferred", 0)
            
            # Self-healing sync: Always validate DB against filesystem
            # Get existing posters from database
            existing_poster_count = self.db.query(Poster).filter(Poster.drive_id == drive.drive_id).count()
            
            # Smart optimization: Skip full DB update only if:
            # 0. Drive syncs via rclone (local-only folders always scan, since
            #    files_transferred==0 there says nothing about new local files) AND
            # 1. No files transferred by rclone AND
            # 2. Database already has records AND
            # 3. No mtime changes detected AND
            # 4. All DB records point to files that actually exist on disk
            if drive.sync_enabled and files_transferred == 0 and existing_poster_count > 0:
                # Quick validation: Check a sample of files
                if progress_callback:
                    progress_callback("checking", 75, 100, "Checking for changes...")
                
                log_debug(LogTags.SYNC, f"No rclone transfers for '{drive.name}', validating consistency...", drive=drive.name)
                
                # Sample up to 10 random files to check for issues
                sample_posters = self.db.query(Poster).filter(
                    Poster.drive_id == drive.drive_id
                ).limit(10).all()
                
                needs_full_update = False
                for poster in sample_posters:
                    poster_path = Path(poster.file_path)
                    
                    # Check if file is missing from disk (orphaned DB record)
                    if not poster_path.exists():
                        needs_full_update = True
                        log_debug(
                            LogTags.SYNC,
                            f"Missing file detected: {poster.file_name} (orphaned DB record)",
                            file=poster.file_name, drive=drive.name
                        )
                        break
                    
                    # Check if mtime changed
                    current_mtime = poster_path.stat().st_mtime
                    if poster.file_mtime is None or abs(poster.file_mtime - current_mtime) > 1.0:
                        needs_full_update = True
                        log_debug(
                            LogTags.SYNC,
                            f"mtime change detected on sample file: {poster.file_name}",
                            file=poster.file_name, drive=drive.name
                        )
                        break
                
                if not needs_full_update:
                    log_info(LogTags.SYNC, f"No changes detected for '{drive.name}', skipping DB update", drive=drive.name)
                    
                    # Update drive stats. Refresh sync_file_count from the live poster
                    # count (== disk here, since no changes were detected) so it stays
                    # consistent instead of keeping a stale value.
                    drive.last_synced = datetime.now(timezone.utc)
                    drive.last_files_transferred = 0
                    drive.sync_file_count = existing_poster_count
                    
                    # Update job
                    update_job_state(
                        self.db,
                        job,
                        status=JOB_STATUS_COMPLETED,
                        progress=100,
                        message="No changes detected",
                        completed_at=datetime.now(timezone.utc),
                    )
                    
                    if progress_callback:
                        progress_callback("completed", 100, 100, "No changes detected")
                    
                    return {
                        "success": True,
                        "added": 0,
                        "updated": 0,
                        "deleted": 0
                    }
                else:
                    log_info(
                        LogTags.SYNC,
                        f"Inconsistencies detected for '{drive.name}', running full database update",
                        drive=drive.name
                    )
            
            if files_transferred == 0 and existing_poster_count == 0:
                log_info(
                    LogTags.SYNC,
                    f"No files transferred but database is empty for '{drive.name}', forcing database update",
                    drive=drive.name
                )
            
            # Phase 3: Scan filesystem (70-80%)
            if progress_callback:
                progress_callback("scanning", 70, 100, "Scanning local folder...")
            
            job.progress = 70
            job.message = f"Rclone sync completed for {drive.name}, scanning filesystem..."
            self.db.commit()
            
            # Phase 1: Scan filesystem (source of truth)
            log_debug(LogTags.SYNC, f"Scanning local folder: {local_folder}", drive=drive.name, path=str(local_folder))
            image_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
            local_files = []
            
            try:
                with os.scandir(local_folder) as it:
                    for entry in it:
                        if entry.is_file() and Path(entry.name).suffix.lower() in image_extensions:
                            stat = entry.stat()
                            local_files.append({
                                'name': entry.name,
                                'path': Path(entry.path),
                                'size': stat.st_size,
                                'mtime': stat.st_mtime
                            })
            except Exception as e:
                log_error(LogTags.SYNC, f"Error scanning local folder: {e}", drive=drive.name, error=str(e), path=str(local_folder))
                update_job_state(
                    self.db,
                    job,
                    status=JOB_STATUS_FAILED,
                    error=f"Failed to scan local folder: {e}",
                    completed_at=datetime.now(timezone.utc),
                )
                return {"success": False, "error": f"Failed to scan local folder: {e}"}
            
            log_debug(LogTags.SYNC, f"Found {len(local_files)} image files on disk", drive=drive.name, count=len(local_files))
            
            # Phase 2: Get existing database records (lightweight columns only)
            existing_rows = (
                self.db.query(Poster.id, Poster.file_name, Poster.file_path, Poster.file_size, Poster.file_mtime)
                .filter(Poster.drive_id == drive.drive_id)
                .all()
            )
            
            # Build dicts for quick lookup
            local_filenames = {f['name'] for f in local_files}
            existing_by_name = {r.file_name: r for r in existing_rows}
            db_filenames = set(existing_by_name.keys())
            
            log_debug(
                LogTags.SYNC,
                f"Database has {len(existing_rows)} records",
                drive=drive.name, db_count=len(existing_rows)
            )
            
            # Phase 3: Self-healing - Find and fix discrepancies
            missing_from_disk = db_filenames - local_filenames  # In DB but not on disk
            
            deleted = 0
            if missing_from_disk:
                log_warning(
                    LogTags.SYNC,
                    f"Self-heal: Removing {len(missing_from_disk)} orphaned DB records",
                    drive=drive.name, count=len(missing_from_disk)
                )
                ids_to_delete = []
                for filename in missing_from_disk:
                    ids_to_delete.append(existing_by_name[filename].id)
                    deleted += 1
                    # Remove from lookup dict so it doesn't interfere with add/update logic below
                    del existing_by_name[filename]
                
                self.db.query(Poster).filter(Poster.id.in_(ids_to_delete)).delete(synchronize_session=False)
                self.db.commit()
                log_success(LogTags.SYNC, f"Cleaned up {deleted} orphaned records", drive=drive.name, deleted=deleted)
            
            # Phase 4: Dual-check change detection and updates (80-95%)
            if progress_callback:
                progress_callback("updating", 80, 100, "Updating database...")
            
            added = 0
            updated = 0
            unchanged = 0
            MTIME_TOLERANCE = 1.0  # 1 second tolerance for filesystem quirks
            
            pending_updates: list[dict] = []
            pending_inserts: list[Poster] = []
            
            for idx, file_info in enumerate(local_files):
                try:
                    file_name = file_info['name']
                    file_path = file_info['path']
                    file_size = file_info['size']
                    file_mtime = file_info['mtime']
                    
                    # Update progress (80-95%)
                    if progress_callback and len(local_files) > 0:
                        file_progress = 80 + int((idx / len(local_files)) * 15)
                        progress_callback("updating", file_progress, 100, f"Updating database ({idx + 1}/{len(local_files)})")
                    
                    progress = 80 + int((idx / len(local_files)) * 15)
                    if progress != job.progress:
                        job.progress = progress
                        job.message = f"Updating database ({idx + 1}/{len(local_files)})"
                    
                    # Check if record exists
                    existing = existing_by_name.get(file_name)
                    
                    if existing:
                        # DUAL-CHECK: Has mtime OR size changed?
                        mtime_changed = (
                            existing.file_mtime is None or 
                            abs(existing.file_mtime - file_mtime) > MTIME_TOLERANCE
                        )
                        size_changed = existing.file_size != file_size
                        path_changed = existing.file_path != str(file_path)
                        
                        if mtime_changed or size_changed or path_changed:
                            # File changed - queue update and mark for reprocessing
                            pending_updates.append({
                                'id': existing.id,
                                'file_path': str(file_path),
                                'file_size': file_size,
                                'file_mtime': file_mtime,
                                'downloaded_at': datetime.now(timezone.utc),
                                'last_processed': None,  # Mark stale for rename
                            })
                            updated += 1
                            
                            reasons = []
                            if mtime_changed:
                                reasons.append("mtime")
                            if size_changed:
                                reasons.append(f"size ({existing.file_size} → {file_size})")
                            if path_changed:
                                reasons.append("path")
                            
                            log_debug(
                                LogTags.SYNC,
                                f"Updated: {file_name} - {', '.join(reasons)}",
                                drive=drive.name, file=file_name, reasons=reasons
                            )
                        else:
                            # Truly unchanged
                            unchanged += 1
                    else:
                        # New file - queue insert
                        pending_inserts.append(Poster(
                            drive_id=drive.drive_id,
                            file_name=file_name,
                            tmdb_id=extract_tmdb_id(file_name),
                            file_path=str(file_path),
                            file_size=file_size,
                            file_mtime=file_mtime,
                            gdrive_file_id=file_name  # Use filename as ID since we're scanning locally
                        ))
                        added += 1
                        
                        log_debug(LogTags.SYNC, f"Added: {file_name}", drive=drive.name, file=file_name)
                    
                    # Flush every 500 files to bound memory
                    if idx % 500 == 0 and idx > 0:
                        if pending_updates:
                            self.db.bulk_update_mappings(Poster, pending_updates)
                            pending_updates = []
                        if pending_inserts:
                            self.db.bulk_save_objects(pending_inserts)
                            pending_inserts = []
                        self.db.commit()
                        
                except Exception as e:
                    log_error(
                        LogTags.SYNC,
                        f"Error updating database for file: {e}",
                        drive=drive.name, file=file_info.get('name', 'unknown'), error=str(e)
                    )
            
            # Final flush and commit
            if pending_updates:
                self.db.bulk_update_mappings(Poster, pending_updates)
            if pending_inserts:
                self.db.bulk_save_objects(pending_inserts)
            self.db.commit()
            
            # Phase 5: Update drive stats (95-100%)
            if progress_callback:
                progress_callback("finalizing", 95, 100, "Finalizing sync...")
            
            # Update drive stats
            drive.last_synced = datetime.now(timezone.utc)
            drive.sync_file_count = len(local_files)
            drive.last_files_transferred = files_transferred
            
            # Update job with detailed results
            job.progress = 100
            
            if added == 0 and updated == 0 and deleted == 0:
                job.message = "No posters synced"
            else:
                changes = []
                if added > 0:
                    changes.append(f"{added} added")
                if updated > 0:
                    changes.append(f"{updated} updated")
                if deleted > 0:
                    changes.append(f"{deleted} deleted")
                if unchanged > 0:
                    changes.append(f"{unchanged} unchanged")
                job.message = f"Sync complete: {', '.join(changes)}"
            
            update_job_state(
                self.db,
                job,
                status=JOB_STATUS_COMPLETED,
                progress=100,
                message=job.message,
                completed_at=datetime.now(timezone.utc),
            )
            
            # Final progress update
            if progress_callback:
                progress_callback("completed", 100, 100, job.message)
            
            # Log summary with all stats
            log_success(
                LogTags.SYNC,
                f"Completed: '{drive.name}' - "
                f"Added: {added}, Updated: {updated}, Deleted: {deleted}, Unchanged: {unchanged}",
                drive=drive.name, added=added, updated=updated, deleted=deleted, 
                unchanged=unchanged, total=len(local_files)
            )
            
            log_section_end(LogTags.SYNC, f"Sync Complete: {drive.name}")
            
            return {
                "success": True,
                "added": added,
                "updated": updated,
                "deleted": deleted
            }
            
        except Exception as e:
            import traceback
            log_error(
                LogTags.SYNC,
                f"Failed '{drive.name}': {str(e)}\n{traceback.format_exc()}",
                drive=drive.name, error=str(e)
            )
            
            log_section_end(LogTags.SYNC, f"Sync Failed: {drive.name}")
            
            # Update job
            if job:
                update_job_state(
                    self.db,
                    job,
                    status=JOB_STATUS_FAILED,
                    error=str(e),
                    completed_at=datetime.now(timezone.utc),
                )
            
            return {"success": False, "error": str(e)}
    
    def sync_multiple_drives(
        self,
        drive_ids: list[int],
        job_id: Optional[int] = None,
        max_workers: int = 1,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> dict[str, Any]:
        """
        Sync multiple drives sequentially or in parallel.
        Uses ThreadPoolExecutor to manage rclone syncs.
        
        Args:
            drive_ids: List of drive IDs to sync
            job_id: Optional parent job ID for tracking (if None, no job updates)
            max_workers: Maximum number of parallel rclone processes (default 1 for sequential)
            progress_callback: Optional callback function(phase, current, total, message) for progress updates
        
        Returns:
            Dictionary with sync results
        """
        log_info(LogTags.SYNC, f"Starting batch sync of {len(drive_ids)} drives {'sequentially' if max_workers == 1 else f'with {max_workers} workers'}")
        
        job = None
        if job_id:
            job = self.db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return {"success": False, "error": "Job not found"}
            
            job.status = JOB_STATUS_RUNNING
            job.message = f"Preparing to sync {len(drive_ids)} drives in parallel"
            self.db.commit()
        
        # Phase 1: Collect drive sync tasks (0-5%)
        if progress_callback:
            progress_callback("preparing", 0, 100, "Preparing drives for sync...")
        
        # Collect drive sync tasks
        sync_tasks = []
        for drive_id in drive_ids:
            drive = self.db.query(Drive).filter(Drive.id == drive_id).first()
            if not drive:
                log_warning(LogTags.SYNC, f"Drive {drive_id} not found, skipping")
                continue

            # Skip rclone if GDrive sync is disabled, or it's a manual custom drive with no real Drive ID
            is_local_only = (not drive.sync_enabled) or (
                drive.is_custom and (
                    drive.drive_id.startswith('manual-') or not (drive.drive_id or '').strip()
                )
            )
            result_key = drive.drive_id if (drive.drive_id and drive.drive_id.strip()) else f"local-only-{drive.id}"
            local_folder = drive.get_local_path()

            sync_tasks.append({
                'drive_id': drive.drive_id,
                'drive_name': drive.name,
                'db_id': drive.id,
                'local_folder': local_folder,
                'is_local_only': is_local_only,
                'result_key': result_key,
                'job_id': job_id,  # lets rclone abort before starting a drive on a stopped batch
            })
        
        if not sync_tasks:
            if job:
                update_job_state(
                    self.db,
                    job,
                    status=JOB_STATUS_FAILED,
                    error="No valid drives to sync",
                    completed_at=datetime.now(timezone.utc),
                )
            return {"success": False, "error": "No valid drives to sync"}
        
        # Add task index to each sync task
        for idx, task in enumerate(sync_tasks):
            task['task_index'] = idx
        
        # Pre-sync cleanup: Remove DB records for missing files BEFORE syncing
        # This ensures rclone re-downloads are counted as "added" not "unchanged"
        image_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
        for task in sync_tasks:
            local_folder = task['local_folder']
            drive_id = task['drive_id']
            drive_name = task['drive_name']
            
            # Scan existing files on disk
            existing_files_on_disk = set()
            if local_folder.exists() and local_folder.is_dir():
                try:
                    for file_path in local_folder.iterdir():
                        if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                            existing_files_on_disk.add(file_path.name)
                except Exception as e:
                    log_warning(LogTags.SYNC, f"Could not scan folder before sync: {e}", drive=drive_name)
            
            # Get lightweight (id, file_name) pairs — avoid loading full ORM objects
            # into the session identity map for potentially 50k+ records.
            db_name_ids = (
                self.db.query(Poster.id, Poster.file_name)
                .filter(Poster.drive_id == drive_id)
                .all()
            )

            # Find IDs for records whose files don't exist on disk
            ids_to_delete = [
                r.id for r in db_name_ids if r.file_name not in existing_files_on_disk
            ]

            # Delete those records so rclone re-downloads count as "added"
            if ids_to_delete:
                log_info(
                    LogTags.SYNC,
                    f"Pre-sync cleanup: Removing {len(ids_to_delete)} DB records for missing files",
                    drive=drive_name,
                    missing_count=len(ids_to_delete)
                )
                self.db.query(Poster).filter(Poster.id.in_(ids_to_delete)).delete(synchronize_session=False)
                self.db.commit()
                log_debug(LogTags.SYNC, "Cleared records for files to be re-downloaded", drive=drive_name, deleted=len(ids_to_delete))
        
        # Phase 2: Sync drives (5-65%)
        if progress_callback:
            progress_callback("syncing", 5, 100, f"Syncing {len(sync_tasks)} drives...")
        
        # Define progress callback for file-level updates with monotonic progress guarantee
        last_updates = {}  # Track last progress per drive
        
        def file_progress_callback(
            task_idx: int,
            drive_name: str,
            filename: str,
            files_checked: int,
            files_transferred: int,
            rclone_phase: str,
            transfer_total_hint: int = 0,
        ) -> None:
            """Called when a file is being transferred
            Args:
                task_idx: Index of the current task
                drive_name: Name of the drive being synced
                filename: Current file being processed
                files_checked: Number of files checked so far
                files_transferred: Number of files transferred so far
                rclone_phase: Current phase ('checking' or 'transferring')
            """
            try:
                import math
                import time
                
                # Get or initialize tracking for this drive
                if task_idx not in last_updates:
                    last_updates[task_idx] = {
                        'progress': 0,
                        'message': '',
                        'max_checked': 0,
                        'max_transferred': 0,
                        'max_transfer_total': 0,
                        'last_file': '',
                        'last_emit_at': 0.0,
                    }
                last_update = last_updates[task_idx]
                
                # Track maximum values seen (counters can sometimes fluctuate)
                last_update['max_checked'] = max(last_update['max_checked'], files_checked)
                last_update['max_transferred'] = max(last_update['max_transferred'], files_transferred)
                if transfer_total_hint > 0:
                    last_update['max_transfer_total'] = max(last_update['max_transfer_total'], transfer_total_hint)
                
                # Calculate progress within the sync phase (5-65%)
                # Each drive gets an equal portion of the 60% range
                progress_per_drive = 60 / len(sync_tasks)
                drive_base_progress = 5 + (task_idx * progress_per_drive)
                
                # Split each drive's progress:
                # - 50% for checking phase
                # - 50% for transferring phase
                half_drive_progress = progress_per_drive / 2
                
                new_progress = 0
                message = filename
                
                if rclone_phase == 'checking' and last_update['max_checked'] > 0:
                    # During checking, show gradual progress based on files discovered
                    log_progress = math.log(last_update['max_checked'] + 1) / math.log(5000)  # Assume ~5k files per drive
                    check_progress_within_drive = min(log_progress, 1.0) * half_drive_progress
                    new_progress = int(drive_base_progress + check_progress_within_drive)
                    if filename and 'checking files:' in filename.lower() and '/' in filename and len(filename) < 140:
                        message = filename
                    else:
                        message = "Listing remote and local files..."
                
                elif rclone_phase == 'transferring' and last_update['max_checked'] > 0:
                    # During transfer, show progress in the second half of this drive's range
                    known_transfer_total = last_update['max_transfer_total']
                    if known_transfer_total > 0:
                        transfer_total = max(known_transfer_total, last_update['max_transferred'], 1)
                    else:
                        transfer_total = max(last_update['max_checked'], last_update['max_transferred'], 1)
                    transfer_percent = last_update['max_transferred'] / transfer_total
                    transfer_progress_within_drive = half_drive_progress + (transfer_percent * half_drive_progress)
                    new_progress = int(drive_base_progress + transfer_progress_within_drive)

                    if filename and len(filename) < 140 and not filename.lower().startswith('checking') and 'transferred' not in filename.lower():
                        last_update['last_file'] = filename

                    stats_message = f"Syncing files: {last_update['max_transferred']:,}/{transfer_total:,} transferred"
                    if last_update['last_file']:
                        message = f"{stats_message} | {last_update['last_file']}"
                    else:
                        message = stats_message
                
                # CRITICAL: Only update if progress increased (monotonic progress)
                # This prevents backwards jumps when rclone switches between checking/transferring
                now = time.monotonic()
                transfer_file_changed = (
                    rclone_phase == 'transferring'
                    and message != last_update['message']
                )
                should_emit = (
                    transfer_file_changed
                    or new_progress > last_update['progress']
                    or (
                        message != last_update['message']
                        and (now - float(last_update['last_emit_at'])) >= 1.0
                    )
                )

                if should_emit:
                    last_update['progress'] = new_progress
                    last_update['message'] = message
                    last_update['last_emit_at'] = now
                    
                    if progress_callback:
                        progress_callback(
                            "syncing", 
                            min(new_progress, 65), 
                            100, 
                            f"{drive_name} ({task_idx + 1}/{len(sync_tasks)}): {message}"
                        )
                    
                    if job_id:
                        job_obj = self.db.query(Job).filter(Job.id == job_id).first()
                        if job_obj:
                            job_obj.progress = min(new_progress, 65)
                            job_obj.message = f"{drive_name} ({task_idx + 1}/{len(sync_tasks)}): {message}"
                            self.db.commit()
                    
            except Exception as e:
                log_warning(LogTags.SYNC_ALL, f"Error updating progress: {e}")
        
        # Execute all syncs
        remote_sync_tasks = [task for task in sync_tasks if not task.get('is_local_only', False)]
        local_only_tasks = [task for task in sync_tasks if task.get('is_local_only', False)]

        results: dict[str, dict[str, Any]] = {}

        # Execute remote sync tasks via rclone
        if remote_sync_tasks:
            results.update(
                self.rclone.sync_multiple_folders(
                    remote_sync_tasks,
                    max_workers=max_workers,
                    progress_callback=file_progress_callback,
                )
            )

        # Mark local-only tasks as successful sync placeholders (scan/update phase will process files)
        for task in local_only_tasks:
            local_key = task.get('result_key')
            if not local_key:
                continue
            results[local_key] = {
                'success': True,
                'files_transferred': 0,
                'is_local_only': True,
            }
            log_info(
                LogTags.SYNC,
                f"Drive '{task.get('drive_name', 'Unknown')}' is local-folder-only; skipping Google Drive sync and scanning local files",
                drive=task.get('drive_name', 'Unknown'),
            )
        
        # Phase 3: Process results and update database (65-100%)
        if progress_callback:
            progress_callback("updating", 65, 100, "Updating database for synced drives...")
        
        total_added = 0
        total_updated = 0
        total_deleted = 0
        total_errors = 0
        
        log_debug(LogTags.SYNC, f"Starting database update phase for {len(sync_tasks)} drives")
        
        for idx, task in enumerate(sync_tasks):
            try:
                drive_name = task.get('drive_name', 'Unknown')
                
                # Update progress (65-100%)
                db_progress = 65 + int(((idx + 1) / len(sync_tasks)) * 35)
                
                if progress_callback:
                    progress_callback(
                        "updating", 
                        db_progress, 
                        100, 
                        f"Updating database for {drive_name} ({idx + 1}/{len(sync_tasks)})"
                    )
                
                if job:
                    job.progress = db_progress
                    job.message = f"Updating database for {drive_name} ({idx + 1}/{len(sync_tasks)})"
                    self.db.commit()
                
                drive_id = task.get('drive_id')
                result_key = task.get('result_key')
                local_folder = task.get('local_folder')
                if not result_key or not local_folder:
                    log_error(LogTags.SYNC, f"Invalid task data for {drive_name}", drive=drive_name)
                    total_errors += 1
                    continue
                
                log_debug(LogTags.SYNC, f"Processing drive {idx + 1}/{len(sync_tasks)}: {drive_name}")
                
                # Check if sync was successful for this drive
                if not results.get(result_key, {}).get('success', False):
                    log_error(LogTags.SYNC, f"Sync failed for {task.get('drive_name', 'Unknown')}")
                    total_errors += 1
                    continue
                
                # List files and update database using self-healing filesystem scan
                log_debug(LogTags.SYNC, f"Scanning local folder: {local_folder}", drive=drive_name)
                image_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
                local_files = []
                
                try:
                    with os.scandir(local_folder) as it:
                        for entry in it:
                            if entry.is_file() and Path(entry.name).suffix.lower() in image_extensions:
                                stat = entry.stat()
                                local_files.append({
                                    'name': entry.name,
                                    'path': Path(entry.path),
                                    'size': stat.st_size,
                                    'mtime': stat.st_mtime
                                })
                except OSError as e:
                    import traceback
                    log_error(
                        LogTags.SYNC,
                        f"Error scanning {local_folder}: {str(e)}\n{traceback.format_exc()}",
                        drive=drive_name, error=str(e)
                    )
                    total_errors += 1
                    continue
                
                log_debug(LogTags.SYNC, f"Found {len(local_files)} image files", drive=drive_name, count=len(local_files))
                
                # Get existing posters from database (lightweight columns only)
                existing_rows = (
                    self.db.query(Poster.id, Poster.file_name, Poster.file_path, Poster.file_size, Poster.file_mtime)
                    .filter(Poster.drive_id == drive_id)
                    .all()
                )
                
                existing_by_name = {r.file_name: r for r in existing_rows}
                
                # Build set of local filenames safely
                local_filenames = set()
                for f in local_files:
                    name = f.get('name')
                    if name:
                        local_filenames.add(name)
                    else:
                        log_warning(LogTags.SYNC, f"File dict missing 'name' key: {f}", drive=drive_name)
                
                db_filenames = set(existing_by_name.keys())
                
                # Self-healing: Remove orphaned records
                missing_from_disk = db_filenames - local_filenames
                deleted = 0
                if missing_from_disk:
                    log_debug(
                        LogTags.SYNC,
                        f"Removing {len(missing_from_disk)} orphaned records",
                        drive=drive_name, count=len(missing_from_disk)
                    )
                    ids_to_delete = []
                    for filename in missing_from_disk:
                        row = existing_by_name.get(filename)
                        if row:
                            ids_to_delete.append(row.id)
                            deleted += 1
                            # Remove from lookup dict so it doesn't interfere with add/update logic below
                            del existing_by_name[filename]
                        else:
                            log_warning(LogTags.SYNC, f"Could not find poster to delete: {filename}", drive=drive_name)
                    if ids_to_delete:
                        self.db.query(Poster).filter(Poster.id.in_(ids_to_delete)).delete(synchronize_session=False)
                
                # Dual-check updates
                added = 0
                updated = 0
                unchanged = 0
                metadata_populated = 0  # Track initial metadata population
                MTIME_TOLERANCE = 1.0
                
                pending_updates: list[dict] = []
                pending_inserts: list[Poster] = []
                
                log_debug(LogTags.SYNC, f"Processing {len(local_files)} files for {drive_name}...", drive=drive_name)
                
                for file_info in local_files:
                    file_name = file_info.get('name')
                    file_path = file_info.get('path')
                    file_size = file_info.get('size')
                    file_mtime = file_info.get('mtime')
                    
                    if not file_name or not file_path:
                        log_warning(LogTags.SYNC, f"Skipping invalid file_info: {file_info}", drive=drive_name)
                        continue
                    
                    existing = existing_by_name.get(file_name)
                    
                    if existing:
                        # Check if this is initial metadata population or an actual change
                        is_initial_metadata = existing.file_mtime is None
                        
                        # Dual-check: mtime OR size changed (only if we have prior values)
                        mtime_changed = (
                            existing.file_mtime is not None and
                            abs(existing.file_mtime - file_mtime) > MTIME_TOLERANCE
                        )
                        size_changed = (
                            existing.file_size is not None and
                            existing.file_size != file_size
                        )
                        path_changed = existing.file_path != str(file_path)
                        
                        # Update metadata if missing OR if actual change detected
                        if is_initial_metadata or mtime_changed or size_changed or path_changed:
                            update_dict: dict = {
                                'id': existing.id,
                                'file_path': str(file_path),
                                'file_size': file_size,
                                'file_mtime': file_mtime,
                            }
                            
                            # Only reset last_processed and count as update if this is an ACTUAL change
                            if not is_initial_metadata and (mtime_changed or size_changed or path_changed):
                                update_dict['downloaded_at'] = datetime.now(timezone.utc)
                                update_dict['last_processed'] = None
                                updated += 1
                            else:
                                # Initial metadata population - not a real update
                                metadata_populated += 1
                                unchanged += 1
                            pending_updates.append(update_dict)
                        else:
                            unchanged += 1
                    else:
                        # New file
                        pending_inserts.append(Poster(
                            drive_id=drive_id,
                            file_name=file_name,
                            tmdb_id=extract_tmdb_id(file_name),
                            file_path=str(file_path),
                            file_size=file_size,
                            file_mtime=file_mtime,
                            gdrive_file_id=file_name
                        ))
                        added += 1
                
                if pending_updates:
                    self.db.bulk_update_mappings(Poster, pending_updates)
                if pending_inserts:
                    self.db.bulk_save_objects(pending_inserts)
                
                log_debug(
                    LogTags.SYNC,
                    f"Completed file processing for {drive_name}",
                    drive=drive_name, added=added, updated=updated, deleted=deleted, unchanged=unchanged
                )
                
                # Update drive stats
                try:
                    db_drive = self.db.query(Drive).filter(Drive.id == task.get('db_id')).first()
                    if db_drive:
                        db_drive.last_synced = datetime.now(timezone.utc)
                        db_drive.sync_file_count = len(local_files)
                        db_drive.last_files_transferred = results.get(result_key, {}).get('files_transferred', 0)
                except Exception as e:
                    import traceback
                    log_error(
                        LogTags.SYNC,
                        f"Error updating drive stats: {str(e)}\n{traceback.format_exc()}",
                        drive=drive_name, error=str(e)
                    )
                    raise
                
                log_debug(LogTags.SYNC, f"Committing database changes for {drive_name}...", drive=drive_name)
                self.db.commit()
                
                total_added += added
                total_updated += updated
                total_deleted += deleted
                
                # Log result
                log_parts = [f"Added: {added}", f"Updated: {updated}", f"Deleted: {deleted}", f"Unchanged: {unchanged}"]
                if metadata_populated > 0:
                    log_parts.append(f"Metadata populated: {metadata_populated}")
                
                log_success(
                    LogTags.SYNC,
                    f"'{drive_name}' - {', '.join(log_parts)}",
                    drive=drive_name, added=added, updated=updated, deleted=deleted, 
                    unchanged=unchanged, metadata_populated=metadata_populated
                )
                
            except Exception as e:
                # Rollback the failed transaction to allow subsequent drives to process
                self.db.rollback()
                import traceback
                log_error(
                    LogTags.SYNC,
                    f"Error processing {drive_name}: {str(e)}\n{traceback.format_exc()}", 
                    drive=drive_name, error=str(e)
                )
                total_errors += 1
        
        # Update job completion
        if job:
            job.progress = 100
            
            if total_added == 0 and total_updated == 0 and total_deleted == 0:
                job.message = f"All {len(sync_tasks)} drives up to date - no posters synced"
            else:
                job.message = f"Synced {len(sync_tasks)} drives: {total_added} added, {total_updated} updated, {total_deleted} deleted"
            
            update_job_state(
                self.db,
                job,
                status=JOB_STATUS_COMPLETED,
                progress=100,
                message=job.message,
                completed_at=datetime.now(timezone.utc),
            )
        
        # Final progress callback
        if progress_callback:
            message = f"Completed: {total_added} added, {total_updated} updated, {total_deleted} deleted"
            progress_callback("completed", 100, 100, message)
        
        return {
            "success": True,
            "drives_synced": len(sync_tasks),
            "added": total_added,
            "updated": total_updated,
            "deleted": total_deleted,
            "errors": total_errors
        }