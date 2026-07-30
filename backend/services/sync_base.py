"""Shared drive-sync engine for poster and artwork drives.

rclone-syncs a drive's Google Drive folder to disk, then indexes the files into the content
table with self-healing (delete orphaned rows, update changed, insert new). Poster and artwork
drives differ ONLY at the seams declared below — which model/table, how to scan the folder, how
to build/compare a content row. The transfer, progress reporting, self-healing fast path,
batch orchestration and job lifecycle are identical for both, so this is where they live.

Generalized verbatim from the poster engine so poster behavior is unchanged; artwork inherits it.
"""

from pathlib import Path
import time
import math
from typing import Any, Callable, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from services.rclone import RcloneService
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    update_job_state,
)
from core.logging import LogTags, log_success, log_error, log_warning, log_info, log_debug, log_section_start, log_section_end

ProgressCallback = Callable[[str, int, int, str], None]

MTIME_TOLERANCE = 1.0  # seconds; filesystem mtime quirks

# Indent a drive's per-step logs so they read as sub-items under the "Processing drive N/M:
# <name>" header. Fixed spaces (not a tab) so the offset is consistent in the log viewer.
STEP_INDENT = "    "


class BaseSyncService:
    # ---- per-type config (subclasses set these) ----
    drive_model: Any = None            # Drive / ArtworkDrive
    content_model: Any = None          # Poster / Artwork
    content_drive_attr: str = ""       # FK column NAME: "drive_id" / "artwork_drive_id" (value == drive.drive_id)
    key_attr: str = ""                 # match-key column NAME: "file_name" / "file_path"
    log_tag: str = LogTags.SYNC
    log_tag_all: str = LogTags.SYNC_ALL
    asset_label: str = "posters"       # noun for messages
    third_field_label: str = "path"    # what _third_field_changed compares (for logs)

    def __init__(self, db: Session) -> None:
        self.db = db
        self.rclone = RcloneService()

    # Column objects resolved off the model CLASS (never off the service instance, which would
    # fire the InstrumentedAttribute descriptor against an unmapped object).
    @property
    def _drive_col(self):
        return getattr(self.content_model, self.content_drive_attr)

    @property
    def _key_col(self):
        return getattr(self.content_model, self.key_attr)

    # ---- seams (overridden per drive type) ------------------------------------------------
    def _get_local_path(self, drive) -> Path:
        return drive.get_local_path()

    def _scan_local_files(self, folder: Path, drive=None) -> list[dict[str, Any]]:
        """One dict per synced file: {'name', 'path', 'size', 'mtime', ...type-specific}.
        `drive` lets a subclass narrow the scan to what that drive subscribes to."""
        raise NotImplementedError

    def _disk_keys(self, folder: Path, drive=None) -> set:
        """Match keys currently on disk — a light scan for the pre-sync cleanup."""
        raise NotImplementedError

    def _remote_exclude_dirs(self, drive) -> list[str]:
        """Top-level remote folders rclone should skip for this drive (none by default)."""
        return []

    def _file_key(self, file_info: dict) -> Any:
        """Match key for a scanned file (file_name for posters, file_path for artwork)."""
        raise NotImplementedError

    def _row_key(self, row) -> Any:
        """Match key for an existing DB row."""
        raise NotImplementedError

    def _query_existing_rows(self, drive_id: str):
        """Lightweight existing content rows for this drive_id: id + the match key +
        change-detection columns (file_mtime, file_size, and the type-specific third field)."""
        raise NotImplementedError

    def _new_content_row(self, drive_id: str, file_info: dict):
        """A new content-model instance to insert (FK set to drive_id)."""
        raise NotImplementedError

    def _third_field_changed(self, existing, file_info: dict) -> bool:
        """The one non-mtime/size field that differs by type (path for posters, artwork_type
        for artwork). mtime + size are common columns and compared inline."""
        raise NotImplementedError

    def _metadata_fields(self, file_info: dict) -> dict:
        """Core fields written on an update (excludes the reprocess markers)."""
        raise NotImplementedError

    def _is_local_only(self, drive) -> bool:
        return not drive.sync_enabled

    def _reprocess_fields(self) -> dict:
        """Marks a row stale so the renamer reprocesses it — common to both types."""
        return {"downloaded_at": datetime.now(timezone.utc), "last_processed": None}

    # ---- single-drive sync ----------------------------------------------------------------
    def sync_drive(
        self,
        drive_id: int,
        job_id: int,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> dict[str, Any]:
        """Sync one subscribed drive: rclone → self-healing index → drive stats. Finalizes the job."""
        job = None
        drive = None
        try:
            drive = self.db.query(self.drive_model).filter(self.drive_model.id == drive_id).first()
            if not drive:
                return {"success": False, "error": "Drive not found"}
            if not drive.subscribed:
                return {"success": False, "error": "Drive not subscribed"}

            log_section_start(self.log_tag, f"Sync Starting: {drive.name}")

            job = self.db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return {"success": False, "error": "Job not found"}

            job.status = JOB_STATUS_RUNNING
            job.message = f"Starting sync of {drive.name}"
            self.db.commit()

            local_folder = self._get_local_path(drive)

            # Pre-sync cleanup: drop DB rows for files missing from disk so a re-download counts as "added".
            disk_keys = self._disk_keys(local_folder, drive)
            db_key_ids = (
                self.db.query(self.content_model.id, self._key_col)
                .filter(self._drive_col == drive.drive_id)
                .all()
            )
            ids_to_delete = [r[0] for r in db_key_ids if r[1] not in disk_keys]
            if ids_to_delete:
                log_info(self.log_tag, f"Pre-sync cleanup: Removing {len(ids_to_delete)} DB records for missing files",
                         drive=drive.name, missing_count=len(ids_to_delete))
                self.db.query(self.content_model).filter(self.content_model.id.in_(ids_to_delete)).delete(synchronize_session=False)
                self.db.commit()
                log_debug(self.log_tag, "Cleared records for files to be re-downloaded", drive=drive.name, deleted=len(ids_to_delete))

            local_folder.mkdir(parents=True, exist_ok=True)

            # Phase 1: rclone (0-70%)
            if progress_callback:
                progress_callback("syncing", 0, 100, f"Starting sync of {drive.name}...")

            last_update = [{"progress": 0, "message": "", "max_checked": 0, "max_transferred": 0,
                            "max_transfer_total": 0, "last_file": "", "last_emit_at": 0.0}]

            def file_progress_callback(filename, files_checked, files_transferred, rclone_phase, transfer_total_hint=0):
                try:
                    if not progress_callback:
                        return
                    last_update[0]["max_checked"] = max(last_update[0]["max_checked"], files_checked)
                    last_update[0]["max_transferred"] = max(last_update[0]["max_transferred"], files_transferred)
                    if transfer_total_hint > 0:
                        last_update[0]["max_transfer_total"] = max(last_update[0]["max_transfer_total"], transfer_total_hint)

                    new_progress = 0
                    message = filename

                    if rclone_phase == 'checking' and last_update[0]["max_checked"] > 0:
                        log_progress = math.log(last_update[0]["max_checked"] + 1) / math.log(10000)
                        new_progress = int(min(log_progress, 1.0) * 20)
                        if filename and 'checking files:' in filename.lower() and '/' in filename:
                            message = filename
                        else:
                            message = "Listing remote and local files..."
                    elif rclone_phase == 'transferring' and last_update[0]["max_checked"] > 0:
                        known_transfer_total = last_update[0]["max_transfer_total"]
                        if known_transfer_total > 0:
                            transfer_total = max(known_transfer_total, last_update[0]["max_transferred"], 1)
                        else:
                            transfer_total = max(last_update[0]["max_checked"], last_update[0]["max_transferred"], 1)
                        transfer_percent = last_update[0]["max_transferred"] / transfer_total
                        new_progress = 20 + int(transfer_percent * 50)
                        if filename and len(filename) < 140 and not filename.lower().startswith("checking files") and "transferred" not in filename.lower():
                            last_update[0]["last_file"] = filename
                        stats_message = f"Syncing files: {last_update[0]['max_transferred']:,}/{transfer_total:,} transferred"
                        message = f"{stats_message} | {last_update[0]['last_file']}" if last_update[0]["last_file"] else stats_message

                    now = time.monotonic()
                    transfer_file_changed = rclone_phase == 'transferring' and message != last_update[0]["message"]
                    should_emit = (
                        transfer_file_changed
                        or new_progress > last_update[0]["progress"]
                        or (message != last_update[0]["message"] and (now - float(last_update[0]["last_emit_at"])) >= 1.0)
                    )
                    if should_emit:
                        last_update[0]["progress"] = new_progress
                        last_update[0]["message"] = message
                        last_update[0]["last_emit_at"] = now
                        progress_callback("syncing", new_progress, 100, message)
                except Exception as e:
                    log_warning(self.log_tag, f"Error updating progress: {str(e)}")

            if self._is_local_only(drive):
                log_info(self.log_tag, f"Drive '{drive.name}' is local-folder-only (sync disabled or manual drive); skipping rclone and scanning local files only", drive=drive.name)
                result = {"success": True, "files_transferred": 0}
            else:
                result = self.rclone.sync_folder(
                    drive.drive_id, local_folder, drive_name=drive.name,
                    progress_callback=file_progress_callback,
                    exclude_dirs=self._remote_exclude_dirs(drive),
                )

            # Phase 2: validate (70%)
            if progress_callback:
                progress_callback("validating", 70, 100, "Rclone sync completed, validating...")

            if not result["success"]:
                update_job_state(self.db, job, status=JOB_STATUS_FAILED, error="Rclone sync failed", progress=70, completed_at=datetime.now(timezone.utc))
                return {"success": False, "error": "Rclone sync failed"}

            files_transferred = result.get("files_transferred", 0)

            existing_count = self.db.query(self.content_model).filter(self._drive_col == drive.drive_id).count()

            # Fast path: an rclone-synced drive transferred nothing and a sampled row still matches
            # disk → skip the full pass. Local-only drives always scan (files_transferred==0 there
            # says nothing about new local files).
            if (not self._is_local_only(drive)) and files_transferred == 0 and existing_count > 0:
                if progress_callback:
                    progress_callback("checking", 75, 100, "Checking for changes...")
                log_debug(self.log_tag, f"{STEP_INDENT}No rclone transfers for '{drive.name}', validating consistency...", drive=drive.name)

                sample_rows = self.db.query(self.content_model).filter(self._drive_col == drive.drive_id).limit(10).all()
                needs_full_update = False
                for row in sample_rows:
                    row_path = Path(row.file_path)
                    if not row_path.exists():
                        needs_full_update = True
                        log_debug(self.log_tag, f"{STEP_INDENT}Missing file detected: {row.file_name} (orphaned DB record)", file=row.file_name, drive=drive.name)
                        break
                    current_mtime = row_path.stat().st_mtime
                    if row.file_mtime is None or abs(row.file_mtime - current_mtime) > MTIME_TOLERANCE:
                        needs_full_update = True
                        log_debug(self.log_tag, f"{STEP_INDENT}mtime change detected on sample file: {row.file_name}", file=row.file_name, drive=drive.name)
                        break

                if not needs_full_update:
                    log_info(self.log_tag, f"{STEP_INDENT}No changes detected for '{drive.name}', skipping DB update", drive=drive.name)
                    drive.last_synced = datetime.now(timezone.utc)
                    drive.last_files_transferred = 0
                    drive.sync_file_count = existing_count
                    update_job_state(self.db, job, status=JOB_STATUS_COMPLETED, progress=100, message="No changes detected", completed_at=datetime.now(timezone.utc))
                    if progress_callback:
                        progress_callback("completed", 100, 100, "No changes detected")
                    return {"success": True, "added": 0, "updated": 0, "deleted": 0}
                else:
                    log_info(self.log_tag, f"{STEP_INDENT}Inconsistencies detected for '{drive.name}', running full database update", drive=drive.name)

            if files_transferred == 0 and existing_count == 0:
                log_info(self.log_tag, f"{STEP_INDENT}No files transferred but database is empty for '{drive.name}', forcing database update", drive=drive.name)

            # Phase 3: scan filesystem (70-80%)
            if progress_callback:
                progress_callback("scanning", 70, 100, "Scanning local folder...")
            job.progress = 70
            job.message = f"Rclone sync completed for {drive.name}, scanning filesystem..."
            self.db.commit()

            log_debug(self.log_tag, f"{STEP_INDENT}Scanning local folder: {local_folder}", drive=drive.name, path=str(local_folder))
            try:
                local_files = self._scan_local_files(local_folder, drive)
            except Exception as e:
                log_error(self.log_tag, f"Error scanning local folder: {e}", drive=drive.name, error=str(e), path=str(local_folder))
                update_job_state(self.db, job, status=JOB_STATUS_FAILED, error=f"Failed to scan local folder: {e}", completed_at=datetime.now(timezone.utc))
                return {"success": False, "error": f"Failed to scan local folder: {e}"}

            log_debug(self.log_tag, f"{STEP_INDENT}Found {len(local_files)} image files on disk", drive=drive.name, count=len(local_files))

            existing_rows = self._query_existing_rows(drive.drive_id)
            local_keys = {self._file_key(f) for f in local_files}
            existing_by_key = {self._row_key(r): r for r in existing_rows}
            db_keys = set(existing_by_key.keys())
            log_debug(self.log_tag, f"{STEP_INDENT}Database has {len(existing_rows)} records", drive=drive.name, db_count=len(existing_rows))

            # Self-heal: remove orphaned rows.
            missing_from_disk = db_keys - local_keys
            deleted = 0
            if missing_from_disk:
                log_warning(self.log_tag, f"{STEP_INDENT}Self-heal: Removing {len(missing_from_disk)} orphaned DB records", drive=drive.name, count=len(missing_from_disk))
                ids_to_delete = []
                for key in missing_from_disk:
                    ids_to_delete.append(existing_by_key[key].id)
                    deleted += 1
                    del existing_by_key[key]
                self.db.query(self.content_model).filter(self.content_model.id.in_(ids_to_delete)).delete(synchronize_session=False)
                self.db.commit()
                log_success(self.log_tag, f"{STEP_INDENT}Cleaned up {deleted} orphaned records", drive=drive.name, deleted=deleted)

            # Phase 4: dual-check change detection (80-95%)
            if progress_callback:
                progress_callback("updating", 80, 100, "Updating database...")
            added = updated = unchanged = metadata_populated = 0
            pending_updates: list[dict] = []
            pending_inserts: list = []

            for idx, file_info in enumerate(local_files):
                try:
                    file_name = file_info['name']
                    if progress_callback and len(local_files) > 0:
                        file_progress = 80 + int((idx / len(local_files)) * 15)
                        progress_callback("updating", file_progress, 100, f"Updating database ({idx + 1}/{len(local_files)})")
                    progress = 80 + int((idx / len(local_files)) * 15)
                    if progress != job.progress:
                        job.progress = progress
                        job.message = f"Updating database ({idx + 1}/{len(local_files)})"

                    existing = existing_by_key.get(self._file_key(file_info))
                    if existing:
                        # First-time metadata fill (mtime was NULL) is NOT a real change — write the
                        # metadata but don't reset last_processed. Guards mirror the batch path.
                        is_initial_metadata = existing.file_mtime is None
                        mtime_changed = existing.file_mtime is not None and abs(existing.file_mtime - file_info['mtime']) > MTIME_TOLERANCE
                        size_changed = existing.file_size is not None and existing.file_size != file_info['size']
                        third_changed = self._third_field_changed(existing, file_info)
                        real_change = mtime_changed or size_changed or third_changed
                        if is_initial_metadata or real_change:
                            update_dict = {'id': existing.id, **self._metadata_fields(file_info)}
                            if real_change and not is_initial_metadata:
                                update_dict.update(self._reprocess_fields())
                                updated += 1
                                reasons = []
                                if mtime_changed:
                                    reasons.append("mtime")
                                if size_changed:
                                    reasons.append(f"size ({existing.file_size} → {file_info['size']})")
                                if third_changed:
                                    reasons.append(self.third_field_label)
                                log_debug(self.log_tag, f"{STEP_INDENT}Updated: {file_name} - {', '.join(reasons)}", drive=drive.name, file=file_name, reasons=reasons)
                            else:
                                metadata_populated += 1
                                unchanged += 1
                            pending_updates.append(update_dict)
                        else:
                            unchanged += 1
                    else:
                        pending_inserts.append(self._new_content_row(drive.drive_id, file_info))
                        added += 1
                        log_debug(self.log_tag, f"{STEP_INDENT}Added: {file_name}", drive=drive.name, file=file_name)

                    if idx % 500 == 0 and idx > 0:
                        if pending_updates:
                            self.db.bulk_update_mappings(self.content_model, pending_updates)
                            pending_updates = []
                        if pending_inserts:
                            self.db.bulk_save_objects(pending_inserts)
                            pending_inserts = []
                        self.db.commit()
                except Exception as e:
                    log_error(self.log_tag, f"Error updating database for file: {e}", drive=drive.name, file=file_info.get('name', 'unknown'), error=str(e))

            if pending_updates:
                self.db.bulk_update_mappings(self.content_model, pending_updates)
            if pending_inserts:
                self.db.bulk_save_objects(pending_inserts)
            self.db.commit()

            # Phase 5: drive stats (95-100%)
            if progress_callback:
                progress_callback("finalizing", 95, 100, "Finalizing sync...")
            drive.last_synced = datetime.now(timezone.utc)
            drive.sync_file_count = len(local_files)
            drive.last_files_transferred = files_transferred

            job.progress = 100
            if added == 0 and updated == 0 and deleted == 0:
                job.message = f"No {self.asset_label} synced"
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

            update_job_state(self.db, job, status=JOB_STATUS_COMPLETED, progress=100, message=job.message, completed_at=datetime.now(timezone.utc))
            if progress_callback:
                progress_callback("completed", 100, 100, job.message)

            _mp = f", Metadata populated: {metadata_populated}" if metadata_populated else ""
            log_success(self.log_tag, f"{STEP_INDENT}Completed: '{drive.name}' - Added: {added}, Updated: {updated}, Deleted: {deleted}, Unchanged: {unchanged}{_mp}",
                        drive=drive.name, added=added, updated=updated, deleted=deleted, unchanged=unchanged, metadata_populated=metadata_populated, total=len(local_files))
            log_section_end(self.log_tag, f"Sync Complete: {drive.name}")
            return {"success": True, "added": added, "updated": updated, "deleted": deleted}

        except Exception as e:
            import traceback
            _name = drive.name if drive else drive_id
            log_error(self.log_tag, f"Failed '{_name}': {str(e)}\n{traceback.format_exc()}", drive=_name, error=str(e))
            log_section_end(self.log_tag, f"Sync Failed: {_name}")
            if job:
                update_job_state(self.db, job, status=JOB_STATUS_FAILED, error=str(e), completed_at=datetime.now(timezone.utc))
            return {"success": False, "error": str(e)}

    # ---- batch sync -----------------------------------------------------------------------
    def sync_multiple_drives(
        self,
        drive_ids: list[int],
        job_id: Optional[int] = None,
        max_workers: int = 1,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> dict[str, Any]:
        """Sync several drives under one job. max_workers must stay 1 (shared Session + per-drive
        progress slicing). Parallelise files via rclone_transfers, not workers."""
        log_info(self.log_tag, f"Starting batch sync of {len(drive_ids)} drives {'sequentially' if max_workers == 1 else f'with {max_workers} workers'}")

        job = None
        if job_id:
            job = self.db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return {"success": False, "error": "Job not found"}
            job.status = JOB_STATUS_RUNNING
            job.message = f"Preparing to sync {len(drive_ids)} drives in parallel"
            self.db.commit()

        if progress_callback:
            progress_callback("preparing", 0, 100, "Preparing drives for sync...")

        sync_tasks = []
        # Drives stay in this map rather than in the task dicts — the task dicts cross into
        # rclone's worker threads, and ORM objects must not follow them there.
        drives_by_db_id: dict[int, Any] = {}
        for drive_id in drive_ids:
            drive = self.db.query(self.drive_model).filter(self.drive_model.id == drive_id).first()
            if not drive:
                log_warning(self.log_tag, f"Drive {drive_id} not found, skipping")
                continue
            is_local_only = self._is_local_only(drive)
            result_key = drive.drive_id if (drive.drive_id and drive.drive_id.strip()) else f"local-only-{drive.id}"
            drives_by_db_id[drive.id] = drive
            sync_tasks.append({
                'drive_id': drive.drive_id,
                'drive_name': drive.name,
                'db_id': drive.id,
                'local_folder': self._get_local_path(drive),
                'is_local_only': is_local_only,
                'result_key': result_key,
                'exclude_dirs': self._remote_exclude_dirs(drive),
                'job_id': job_id,
            })

        if not sync_tasks:
            if job:
                update_job_state(self.db, job, status=JOB_STATUS_FAILED, error="No valid drives to sync", completed_at=datetime.now(timezone.utc))
            return {"success": False, "error": "No valid drives to sync"}

        for idx, task in enumerate(sync_tasks):
            task['task_index'] = idx

        # Pre-sync cleanup per drive.
        for task in sync_tasks:
            disk_keys = self._disk_keys(task['local_folder'], drives_by_db_id.get(task['db_id']))
            db_key_ids = (
                self.db.query(self.content_model.id, self._key_col)
                .filter(self._drive_col == task['drive_id'])
                .all()
            )
            ids_to_delete = [r[0] for r in db_key_ids if r[1] not in disk_keys]
            if ids_to_delete:
                log_info(self.log_tag, f"Pre-sync cleanup: Removing {len(ids_to_delete)} DB records for missing files", drive=task['drive_name'], missing_count=len(ids_to_delete))
                self.db.query(self.content_model).filter(self.content_model.id.in_(ids_to_delete)).delete(synchronize_session=False)
                self.db.commit()
                log_debug(self.log_tag, "Cleared records for files to be re-downloaded", drive=task['drive_name'], deleted=len(ids_to_delete))

        if progress_callback:
            progress_callback("syncing", 5, 100, f"Syncing {len(sync_tasks)} drives...")

        last_updates: dict = {}

        def file_progress_callback(task_idx, drive_name, filename, files_checked, files_transferred, rclone_phase, transfer_total_hint=0):
            try:
                if task_idx not in last_updates:
                    last_updates[task_idx] = {'progress': 0, 'message': '', 'max_checked': 0, 'max_transferred': 0,
                                              'max_transfer_total': 0, 'last_file': '', 'last_emit_at': 0.0}
                last_update = last_updates[task_idx]
                last_update['max_checked'] = max(last_update['max_checked'], files_checked)
                last_update['max_transferred'] = max(last_update['max_transferred'], files_transferred)
                if transfer_total_hint > 0:
                    last_update['max_transfer_total'] = max(last_update['max_transfer_total'], transfer_total_hint)

                progress_per_drive = 60 / len(sync_tasks)
                drive_base_progress = 5 + (task_idx * progress_per_drive)
                half_drive_progress = progress_per_drive / 2

                new_progress = 0
                message = filename

                if rclone_phase == 'checking' and last_update['max_checked'] > 0:
                    log_progress = math.log(last_update['max_checked'] + 1) / math.log(5000)
                    check_progress_within_drive = min(log_progress, 1.0) * half_drive_progress
                    new_progress = int(drive_base_progress + check_progress_within_drive)
                    if filename and 'checking files:' in filename.lower() and '/' in filename and len(filename) < 140:
                        message = filename
                    else:
                        message = "Listing remote and local files..."
                elif rclone_phase == 'transferring' and last_update['max_checked'] > 0:
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
                    message = f"{stats_message} | {last_update['last_file']}" if last_update['last_file'] else stats_message

                now = time.monotonic()
                transfer_file_changed = rclone_phase == 'transferring' and message != last_update['message']
                should_emit = (
                    transfer_file_changed
                    or new_progress > last_update['progress']
                    or (message != last_update['message'] and (now - float(last_update['last_emit_at'])) >= 1.0)
                )
                if should_emit:
                    last_update['progress'] = new_progress
                    last_update['message'] = message
                    last_update['last_emit_at'] = now
                    if progress_callback:
                        progress_callback("syncing", min(new_progress, 65), 100, f"{drive_name} ({task_idx + 1}/{len(sync_tasks)}): {message}")
                    if job_id:
                        job_obj = self.db.query(Job).filter(Job.id == job_id).first()
                        if job_obj:
                            job_obj.progress = min(new_progress, 65)
                            job_obj.message = f"{drive_name} ({task_idx + 1}/{len(sync_tasks)}): {message}"
                            self.db.commit()
            except Exception as e:
                log_warning(self.log_tag_all, f"Error updating progress: {e}")

        remote_sync_tasks = [t for t in sync_tasks if not t.get('is_local_only', False)]
        local_only_tasks = [t for t in sync_tasks if t.get('is_local_only', False)]

        results: dict[str, dict[str, Any]] = {}
        if remote_sync_tasks:
            results.update(self.rclone.sync_multiple_folders(remote_sync_tasks, max_workers=max_workers, progress_callback=file_progress_callback))

        for task in local_only_tasks:
            local_key = task.get('result_key')
            if not local_key:
                continue
            results[local_key] = {'success': True, 'files_transferred': 0, 'is_local_only': True}
            log_info(self.log_tag, f"Drive '{task.get('drive_name', 'Unknown')}' is local-folder-only; skipping Google Drive sync and scanning local files", drive=task.get('drive_name', 'Unknown'))

        if progress_callback:
            progress_callback("updating", 65, 100, "Updating database for synced drives...")

        total_added = total_updated = total_deleted = total_errors = 0
        log_debug(self.log_tag, f"Starting database update phase for {len(sync_tasks)} drives")

        for idx, task in enumerate(sync_tasks):
            try:
                drive_name = task.get('drive_name', 'Unknown')
                db_progress = 65 + int(((idx + 1) / len(sync_tasks)) * 35)
                if progress_callback:
                    progress_callback("updating", db_progress, 100, f"Updating database for {drive_name} ({idx + 1}/{len(sync_tasks)})")
                if job:
                    job.progress = db_progress
                    job.message = f"Updating database for {drive_name} ({idx + 1}/{len(sync_tasks)})"
                    self.db.commit()

                drive_id = task.get('drive_id')
                result_key = task.get('result_key')
                local_folder = task.get('local_folder')
                if not result_key or not local_folder:
                    log_error(self.log_tag, f"Invalid task data for {drive_name}", drive=drive_name)
                    total_errors += 1
                    continue

                log_debug(self.log_tag, f"Processing drive {idx + 1}/{len(sync_tasks)}: {drive_name}")
                if not results.get(result_key, {}).get('success', False):
                    log_error(self.log_tag, f"Sync failed for {drive_name}")
                    total_errors += 1
                    continue

                log_debug(self.log_tag, f"{STEP_INDENT}Scanning local folder: {local_folder}", drive=drive_name)
                try:
                    local_files = self._scan_local_files(local_folder, drives_by_db_id.get(task.get('db_id')))
                except OSError as e:
                    import traceback
                    log_error(self.log_tag, f"Error scanning {local_folder}: {str(e)}\n{traceback.format_exc()}", drive=drive_name, error=str(e))
                    total_errors += 1
                    continue

                log_debug(self.log_tag, f"{STEP_INDENT}Found {len(local_files)} image files", drive=drive_name, count=len(local_files))

                existing_rows = self._query_existing_rows(drive_id)
                existing_by_key = {self._row_key(r): r for r in existing_rows}
                local_keys = {self._file_key(f) for f in local_files}
                db_keys = set(existing_by_key.keys())

                missing_from_disk = db_keys - local_keys
                deleted = 0
                if missing_from_disk:
                    log_debug(self.log_tag, f"{STEP_INDENT}Removing {len(missing_from_disk)} orphaned records", drive=drive_name, count=len(missing_from_disk))
                    ids_to_delete = []
                    for key in missing_from_disk:
                        row = existing_by_key.get(key)
                        if row:
                            ids_to_delete.append(row.id)
                            deleted += 1
                            del existing_by_key[key]
                    if ids_to_delete:
                        self.db.query(self.content_model).filter(self.content_model.id.in_(ids_to_delete)).delete(synchronize_session=False)

                added = updated = unchanged = metadata_populated = 0
                pending_updates: list[dict] = []
                pending_inserts: list = []
                log_debug(self.log_tag, f"{STEP_INDENT}Processing {len(local_files)} files for {drive_name}...", drive=drive_name)

                for file_info in local_files:
                    existing = existing_by_key.get(self._file_key(file_info))
                    if existing:
                        is_initial_metadata = existing.file_mtime is None
                        mtime_changed = existing.file_mtime is not None and abs(existing.file_mtime - file_info['mtime']) > MTIME_TOLERANCE
                        size_changed = existing.file_size is not None and existing.file_size != file_info['size']
                        third_changed = self._third_field_changed(existing, file_info)
                        if is_initial_metadata or mtime_changed or size_changed or third_changed:
                            update_dict = {'id': existing.id, **self._metadata_fields(file_info)}
                            if not is_initial_metadata and (mtime_changed or size_changed or third_changed):
                                update_dict.update(self._reprocess_fields())
                                updated += 1
                            else:
                                metadata_populated += 1
                                unchanged += 1
                            pending_updates.append(update_dict)
                        else:
                            unchanged += 1
                    else:
                        pending_inserts.append(self._new_content_row(drive_id, file_info))
                        added += 1

                if pending_updates:
                    self.db.bulk_update_mappings(self.content_model, pending_updates)
                if pending_inserts:
                    self.db.bulk_save_objects(pending_inserts)

                log_debug(self.log_tag, f"{STEP_INDENT}Completed file processing for {drive_name}", drive=drive_name, added=added, updated=updated, deleted=deleted, unchanged=unchanged)

                try:
                    db_drive = self.db.query(self.drive_model).filter(self.drive_model.id == task.get('db_id')).first()
                    if db_drive:
                        db_drive.last_synced = datetime.now(timezone.utc)
                        db_drive.sync_file_count = len(local_files)
                        db_drive.last_files_transferred = results.get(result_key, {}).get('files_transferred', 0)
                except Exception as e:
                    import traceback
                    log_error(self.log_tag, f"Error updating drive stats: {str(e)}\n{traceback.format_exc()}", drive=drive_name, error=str(e))
                    raise

                log_debug(self.log_tag, f"{STEP_INDENT}Committing database changes for {drive_name}...", drive=drive_name)
                self.db.commit()

                total_added += added
                total_updated += updated
                total_deleted += deleted

                log_parts = [f"Added: {added}", f"Updated: {updated}", f"Deleted: {deleted}", f"Unchanged: {unchanged}"]
                if metadata_populated > 0:
                    log_parts.append(f"Metadata populated: {metadata_populated}")
                log_success(self.log_tag, f"{STEP_INDENT}'{drive_name}' - {', '.join(log_parts)}", drive=drive_name, added=added, updated=updated, deleted=deleted, unchanged=unchanged, metadata_populated=metadata_populated)

            except Exception as e:
                self.db.rollback()
                import traceback
                log_error(self.log_tag, f"Error processing {drive_name}: {str(e)}\n{traceback.format_exc()}", drive=drive_name, error=str(e))
                total_errors += 1

        if job:
            job.progress = 100
            if total_added == 0 and total_updated == 0 and total_deleted == 0:
                job.message = f"All {len(sync_tasks)} drives up to date - no {self.asset_label} synced"
            else:
                job.message = f"Synced {len(sync_tasks)} drives: {total_added} added, {total_updated} updated, {total_deleted} deleted"
            update_job_state(self.db, job, status=JOB_STATUS_COMPLETED, progress=100, message=job.message, completed_at=datetime.now(timezone.utc))

        if progress_callback:
            progress_callback("completed", 100, 100, f"Completed: {total_added} added, {total_updated} updated, {total_deleted} deleted")

        return {
            "success": True,
            "drives_synced": len(sync_tasks),
            "added": total_added,
            "updated": total_updated,
            "deleted": total_deleted,
            "errors": total_errors,
        }
