import subprocess  # nosec B404
import json
import re
import shutil
from collections import deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.logging import LogTags, log_success, log_error, log_warning, log_info, log_debug
from core.config import settings
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

SyncProgressCallback = Callable[[str, int, int, str, int], None]
BatchProgressCallback = Callable[[int, str, str, int, int, str, int], None]
UploadProgressCallback = Callable[[int, int, str, str], None]

SYNC_ALLOWED_EXTENSIONS = ("jpg", "jpeg", "png", "webp", "psd")

SYNC_INCLUDE_REGEX = "{{(?i).*\\.(" + "|".join(SYNC_ALLOWED_EXTENSIONS) + ")$}}"

SYNC_MAX_FILE_SIZE = "250M"

class RcloneService:
    """Service for managing rclone operations with Google Drive"""

    DRIVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
    
    def __init__(self) -> None:
        self.config_path = settings.config_dir / "rclone.conf"
        self.rclone_binary = self._resolve_rclone_binary()
        self._ensure_config()

    def _resolve_rclone_binary(self) -> Optional[str]:
        """Resolve absolute path to the rclone binary to avoid PATH ambiguity."""
        binary = shutil.which("rclone")
        if binary:
            return binary
        log_error(LogTags.RCLONE, "rclone executable not found in PATH")
        return None

    def _build_remote_root_path(self, drive_id: str) -> str:
        """Build and validate a root-folder scoped rclone remote string."""
        normalized_drive_id = str(drive_id or "").strip()
        if not normalized_drive_id or not self.DRIVE_ID_PATTERN.fullmatch(normalized_drive_id):
            raise ValueError("Invalid drive_id format")
        return f"gdrive,root_folder_id={normalized_drive_id}:"

    def _sanitize_remote_relative_path(self, remote_path: str) -> str:
        """Normalize remote relative path and reject control characters/traversal segments."""
        normalized_path = str(remote_path or "").strip()
        if any(char in normalized_path for char in ("\x00", "\n", "\r")):
            raise ValueError("Invalid remote path characters")

        normalized_path = normalized_path.lstrip("/")
        path_parts = [part for part in normalized_path.split("/") if part not in ("", ".")]
        if any(part == ".." for part in path_parts):
            raise ValueError("Path traversal segments are not allowed in remote path")

        return "/".join(path_parts)
    
    def _ensure_config(self) -> None:
        """Create minimal rclone config file if it does not already exist."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            config_content = """[gdrive]
type = drive
scope = drive.readonly
"""
            self.config_path.write_text(config_content)

    def _write_oauth_to_config(
        self,
        client_id: str,
        client_secret: str,
        token_json: str,
    ) -> None:
        """Persist OAuth credentials into the [gdrive] rclone config section.

        Keeps secrets out of process argv (visible in ps aux / /proc).
        """
        import configparser
        config = configparser.ConfigParser()
        if self.config_path.exists():
            config.read(self.config_path)
        if "gdrive" not in config:
            config["gdrive"] = {}
        config["gdrive"]["type"] = "drive"
        config["gdrive"]["scope"] = "drive.readonly"
        config["gdrive"]["client_id"] = client_id
        config["gdrive"]["client_secret"] = client_secret
        config["gdrive"]["token"] = token_json
        with open(self.config_path, "w") as f:
            config.write(f)

    def _read_token_from_config(self) -> Optional[str]:
        """Read the current token from rclone.conf (may have been refreshed by rclone)."""
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read(self.config_path)
            return config.get("gdrive", "token", fallback=None)
        except Exception:
            return None

    def _sync_refreshed_token_to_db(self, original_token: str) -> None:
        """If rclone refreshed the access token and updated rclone.conf, persist it back to DB.

        This prevents the token written to rclone.conf on the next sync from overwriting
        a freshly-refreshed token with a stale one from the DB.
        """
        try:
            current_token = self._read_token_from_config()
            if not current_token or current_token == original_token:
                return

            from database import SessionLocal
            from models.setting import Setting
            db = SessionLocal()
            try:
                record = db.query(Setting).filter(Setting.key == "google_token").first()
                if record:
                    record.value = current_token
                    db.commit()
                    log_debug(LogTags.RCLONE, "Persisted refreshed OAuth token to DB")
            finally:
                db.close()
        except Exception as e:
            log_warning(LogTags.RCLONE, f"Could not sync refreshed token to DB: {e}")
    
    def _get_credentials(self) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Get Google Drive credentials from database."""
        from database import SessionLocal
        from models.setting import get_setting
        
        db = SessionLocal()
        try:
            client_id = get_setting(db, "google_client_id")
            client_secret = get_setting(db, "google_client_secret")
            token = get_setting(db, "google_token")
            service_account_file = get_setting(db, "google_service_account_file")
            
            return (
                client_id.value if client_id else None,
                client_secret.value if client_secret else None,
                token.value if token else None,
                service_account_file.value if service_account_file else None,
            )
        finally:
            db.close()
                
    def _build_drive_auth_args(
        self,
        client_id: Optional[str],
        client_secret: Optional[str],
        token_json: Optional[str],
        service_account_file: Optional[str],
    ) -> Optional[List[str]]:
        """Build rclone Drive auth args from either service-account or OAuth settings."""
        if service_account_file:
            sa_path = Path(service_account_file)
            if not sa_path.exists() or not sa_path.is_file():
                log_error(LogTags.RCLONE, f"Service account JSON file not found: {service_account_file}")
                return None
            return ["--drive-service-account-file", service_account_file]

        if all([client_id, client_secret, token_json]):
            # Write credentials into the config file so they never appear
            # in process argv (visible via ps aux / /proc/<pid>/cmdline)
            self._write_oauth_to_config(client_id, client_secret, token_json)  # type: ignore[arg-type]
            return []

        return None

    def _get_drive_auth_args(self) -> Optional[List[str]]:
        """Get validated rclone auth args from stored settings."""
        client_id, client_secret, token_json, service_account_file = self._get_credentials()

        auth_args = self._build_drive_auth_args(
            client_id=client_id,
            client_secret=client_secret,
            token_json=token_json,
            service_account_file=service_account_file,
        )
        if auth_args is None:
            log_error(
                LogTags.RCLONE,
                "Missing Google Drive credentials. Configure either OAuth (Client ID/Secret + Token) or Service Account JSON path."
            )

        return auth_args
    
    def test_connection(self) -> bool:
        """Test rclone connection"""
        auth_args = self._get_drive_auth_args()
        return auth_args is not None
    
    def sync_folder(
        self,
        drive_id: str,
        local_path: Path,
        drive_name: Optional[str] = None,
        progress_callback: Optional[SyncProgressCallback] = None,
    ) -> Dict[str, Any]:
        """
        Sync entire Google Drive folder to local path using rclone sync.
        
        Args:
            drive_id: Google Drive folder ID
            local_path: Local path to sync to
            drive_name: Optional drive name for logging
            progress_callback: Optional callback function(filename, files_checked, files_transferred, phase)
                             called during sync to report progress. Phase is 'checking' or 'transferring'.
            
        Returns:
            dict with 'success' (bool) and 'files_transferred' (int)
        """
        try:
            if not self.rclone_binary:
                return {"success": False, "files_transferred": 0, "error": "rclone executable not found"}

            # Capture token BEFORE _get_drive_auth_args() writes it to rclone.conf so we
            # can detect if rclone refreshed it during the sync and persist the new value.
            _, _, original_token, _ = self._get_credentials()

            auth_args = self._get_drive_auth_args()
            if auth_args is None:
                return {"success": False, "files_transferred": 0, "error": "Missing Google Drive credentials"}
            
            local_path.mkdir(parents=True, exist_ok=True)
            
            remote_path = self._build_remote_root_path(drive_id)
            
            display_name = drive_name or drive_id
            log_info(LogTags.RCLONE, f"Syncing '{display_name}' -> {local_path}")
            
            # Run rclone sync with streaming output
            process = subprocess.Popen(  # nosec B603
                [
                    self.rclone_binary, 'sync', remote_path, str(local_path),
                    '--config', str(self.config_path),
                    *auth_args,
                    '--include', SYNC_INCLUDE_REGEX,  # Only pull poster/PSD file types, never arbitrary files
                    '--max-size', SYNC_MAX_FILE_SIZE,  # Skip oversized files (disk-fill protection)
                    '--fast-list',  # Faster for large folders
                    '--tpslimit=8',  # Limit API calls to 8/sec to avoid rate limits
                    '--size-only',  # Compare by size only (fast)
                    '--no-update-modtime',  # Preserve original mtimes for filecmp change detection
                    '--check-first',  # Check all files before transferring
                    '--stats', '1s',  # Show stats every 1 second
                    '--stats-log-level', 'NOTICE',  # Log stats at NOTICE level
                    '-v'
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            recent_lines: Deque[str] = deque(maxlen=50)
            files_transferred = 0
            files_checked = 0
            transfer_total = 0
            _stat_transferred = 0
            _stat_transfer_size = ""
            _stat_elapsed = ""
            
            # Stream output and log rclone messages
            for line in process.stdout:
                recent_lines.append(line)
                line_stripped = line.strip()

                # Track summary stats inline (avoids buffering all output lines)
                if 'Transferred:' in line and '/' in line:
                    if any(u in line for u in ('MiB', 'KiB', 'GiB')):
                        try:
                            _stat_transfer_size = line.split('Transferred:')[1].strip().split('/')[0].strip()
                        except Exception:  # nosec B110
                            pass
                    else:
                        try:
                            parts = line.split('Transferred:')[1].strip().split('/')[0].strip().split()[0]
                            if parts.isdigit():
                                _stat_transferred = int(parts)
                        except Exception:  # nosec B110
                            pass
                elif 'Elapsed time:' in line:
                    try:
                        _stat_elapsed = line.split('Elapsed time:')[1].strip()
                    except Exception:  # nosec B110
                        pass
                
                # Log rclone's INFO messages (file operations)
                if 'INFO  :' in line:
                    # Extract message after "INFO  :"
                    try:
                        msg = line.split('INFO  :')[1].strip()
                        # Only log meaningful operations (Copied, Not copying, etc.)
                        if any(keyword in msg for keyword in ['Copied', 'Not copying', 'Deleted', 'Renamed', 'There was nothing to transfer']):
                            log_info(LogTags.RCLONE, msg)
                    except Exception as parse_error:
                        log_debug(LogTags.RCLONE, f"Failed to parse rclone INFO line: {parse_error}")
                
                # Log rclone warnings (skip stats-related NOTICE lines)
                elif 'NOTICE:' in line or 'WARNING:' in line:
                    try:
                        if 'NOTICE:' in line:
                            msg = line.split('NOTICE:')[1].strip()
                            # Skip stats output (Transferred, Checks, etc.)
                            if msg and not any(skip in msg for skip in ['Transferred:', 'Checks:', 'Elapsed time']):
                                log_warning(LogTags.RCLONE, f"Notice: {msg}")
                        else:
                            msg = line.split('WARNING:')[1].strip()
                            if msg:
                                log_warning(LogTags.RCLONE, f"Warning: {msg}")
                    except Exception as parse_error:
                        log_debug(LogTags.RCLONE, f"Failed to parse rclone warning line: {parse_error}")
                
                # Log rclone errors
                elif 'ERROR :' in line or 'ERROR:' in line:
                    try:
                        msg = line.split('ERROR')[1].strip().lstrip(':').strip()
                        log_error(LogTags.RCLONE, f"Error: {msg}")
                    except Exception as parse_error:
                        log_debug(LogTags.RCLONE, f"Failed to parse rclone error line: {parse_error}")
                
                # Update on any meaningful rclone output for smooth progress
                if progress_callback and line_stripped:
                    update_progress = False
                    filename = None
                    phase = 'checking'
                    
                    # Parse for listing/checking phase from stats
                    if 'Listed' in line:
                        try:
                            # During initial listing: "Checks: 0 / 0, -, Listed 15863"
                            # During checking: "Checks: 2142 / 9963, 21%, Listed 19926"
                            listed_match = line.split('Listed')[1].strip().split()[0]
                            if listed_match.isdigit():
                                listed_count = int(listed_match)
                                
                                # Check if we're in listing phase (Checks: 0/0) or checking phase
                                if 'Checks:' in line:
                                    checks_part = line.split('Checks:')[1].split(',')[0].strip()
                                    if '/' in checks_part:
                                        current = checks_part.split('/')[0].strip()
                                        total = checks_part.split('/')[1].strip()
                                        
                                        if current.isdigit() and total.isdigit() and int(total) > 0:
                                            # Actual checking phase
                                            files_checked = int(current)
                                            filename = f"Checking files: {current}/{total}"
                                            phase = 'checking'
                                            update_progress = True
                                        elif listed_count > 0:
                                            # Listing phase (Checks still 0/0). Keep message generic to avoid misleading counts.
                                            files_checked = listed_count
                                            filename = "Listing remote and local files..."
                                            phase = 'checking'
                                            update_progress = True
                        except Exception:
                            log_debug(LogTags.RCLONE, "Failed to parse rclone listing/checking stats line")
                    
                    # Parse for file operations from INFO messages
                    elif 'INFO  :' in line:
                        try:
                            parts = line.split('INFO  :')
                            if len(parts) > 1:
                                file_info = parts[1].strip()
                                # Skip progress summary lines
                                if not any(skip in file_info for skip in ['Transferred:', 'Checks:', 'Elapsed time', 'Transferring:', 'Checking:']):
                                    # Extract operation and filename
                                    if ':' in file_info:
                                        operation_and_file = file_info.split(':', 1)
                                        if len(operation_and_file) == 2:
                                            raw_filename = operation_and_file[0].strip()
                                            operation = operation_and_file[1].strip().lower()
                                            if raw_filename:
                                                file_display_name = Path(raw_filename).name or raw_filename
                                                filename = file_display_name if len(file_display_name) <= 200 else f"{file_display_name[:197]}..."
                                                if any(op in operation for op in ['copied', 'updated', 'renamed']):
                                                    files_transferred += 1
                                                    phase = 'transferring'
                                                    update_progress = True
                        except Exception as e:
                            log_debug(LogTags.RCLONE, f"Progress parse error: {e}")
                    
                    # Parse stats line for transfer count
                    elif 'Transferred:' in line and '/' in line:
                        try:
                            # Stats line format: "Transferred: 5 / 20, 25%"
                            if '/' in line:
                                parts = line.split('Transferred:')[1].strip()
                                # Extract current count before '/'
                                current = parts.split('/')[0].strip().split()[0]
                                if current.isdigit():
                                    new_count = int(current)
                                    if new_count > files_transferred:
                                        files_transferred = new_count
                                        filename = f"{files_transferred} files transferred"
                                        phase = 'transferring'
                                        update_progress = True
                                    # Also extract the total (Y from "X / Y, %")
                                    # This is the real number of files to transfer, not total in drive
                                    after_slash = parts.split('/')[1].strip()
                                    total_token = after_slash.split(',')[0].strip()
                                    if total_token.isdigit() and int(total_token) > 0:
                                        transfer_total = int(total_token)
                        except Exception as e:
                            log_debug(LogTags.RCLONE, f"Stats parse error: {e}")
                    
                    if update_progress:
                        progress_callback(filename or 'Processing...', files_checked, files_transferred, phase, transfer_total)
            
            process.wait()
            
            if process.returncode == 0:
                # Use stats tracked inline during streaming
                # Prefer the rclone stats summary count; fall back to per-file INFO count
                transferred = max(files_transferred, _stat_transferred)
                checked = files_checked
                transfer_size = _stat_transfer_size
                elapsed_time = _stat_elapsed

                # Format completion summary
                summary_parts = []
                if transferred > 0:
                    if transfer_size:
                        summary_parts.append(f"{transferred} files downloaded ({transfer_size})")
                    else:
                        summary_parts.append(f"{transferred} files downloaded")
                if checked > 0:
                    summary_parts.append(f"{checked} already up to date")
                
                summary = ", ".join(summary_parts) if summary_parts else "No files to sync"
                if elapsed_time:
                    summary += f" in {elapsed_time}"
                
                log_success(LogTags.RCLONE, f"Completed: {summary}")
                # Persist any token refresh rclone performed back to the DB so the
                # next sync doesn't overwrite a freshly-refreshed token with the old one.
                if original_token:
                    self._sync_refreshed_token_to_db(original_token)
                return {"success": True, "files_transferred": transferred}
            else:
                error_output = ''.join(recent_lines)  # Last 50 lines (capped by deque)
                log_error(LogTags.RCLONE, f"Failed: '{display_name}' - {error_output}")
                return {"success": False, "files_transferred": 0}
                
        except Exception as e:
            import traceback
            log_error(LogTags.RCLONE, f"Error in sync_folder: {str(e)}\n{traceback.format_exc()}")
            return {"success": False, "files_transferred": 0}
    
    def list_files(self, drive_id: str, path: str = "") -> Optional[List[Dict[str, Any]]]:
        """List files in a Google Drive folder by folder ID"""
        try:
            if not self.rclone_binary:
                return None

            auth_args = self._get_drive_auth_args()
            if auth_args is None:
                return None
            
            remote_root = self._build_remote_root_path(drive_id)
            relative_path = self._sanitize_remote_relative_path(path)
            remote_path = f"{remote_root}{relative_path}" if relative_path else remote_root
            
            result = subprocess.run(  # nosec B603
                [
                    self.rclone_binary, 'lsjson', remote_path,
                    '--config', str(self.config_path),
                    *auth_args,
                ],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                files = json.loads(result.stdout)
                return files
            else:
                log_error(LogTags.RCLONE, f"List failed: {result.stderr}")
                return None
                
        except Exception as e:
            import traceback
            log_error(LogTags.RCLONE, f"Error in list_files: {str(e)}\n{traceback.format_exc()}")
            return None

    def upload_folder(
        self,
        local_path: Path,
        drive_id: str,
        *,
        drive_name: Optional[str] = None,
        mode: str = "copy",
        progress_callback: Optional[UploadProgressCallback] = None,
    ) -> Dict[str, Any]:
        """Upload a local folder to a Google Drive folder using rclone copy/sync."""
        try:
            if not self.rclone_binary:
                return {"success": False, "error": "rclone executable not found"}

            auth_args = self._get_drive_auth_args()
            if auth_args is None:
                return {"success": False, "error": "Missing Google Drive credentials"}

            if not local_path.exists() or not local_path.is_dir():
                return {"success": False, "error": f"Local path not found: {local_path}"}

            sync_mode = mode.lower().strip()
            if sync_mode not in {"copy", "sync"}:
                sync_mode = "copy"

            remote_path = self._build_remote_root_path(drive_id)
            display_name = drive_name or drive_id

            log_info(LogTags.RCLONE, f"Uploading '{local_path}' -> '{display_name}' via rclone {sync_mode}")

            process = subprocess.Popen(  # nosec B603
                [
                    self.rclone_binary, sync_mode, str(local_path), remote_path,
                    '--config', str(self.config_path),
                    *auth_args,
                    '--fast-list',
                    '--tpslimit=8',
                    '--stats', '1s',
                    '--stats-log-level', 'NOTICE',
                    '-v',
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            recent_lines: Deque[str] = deque(maxlen=50)
            files_uploaded = 0
            total_files = sum(1 for path in local_path.rglob("*") if path.is_file())
            checks_re = re.compile(r"Checks:\s*([\d,]+)\s*/\s*([\d,]+)")
            transferred_re = re.compile(r"Transferred:\s*([\d,]+)\s*/\s*([\d,]+)")

            if progress_callback:
                try:
                    progress_callback(0, max(total_files, 1), "start", f"Preparing upload ({sync_mode})")
                except Exception as callback_error:
                    log_warning(LogTags.RCLONE, f"Upload progress callback error: {callback_error}")

            for line in process.stdout:
                recent_lines.append(line)

                checks_match = checks_re.search(line)
                if checks_match and progress_callback:
                    try:
                        checked = int(checks_match.group(1).replace(",", ""))
                        checked_total = int(checks_match.group(2).replace(",", ""))
                        effective_total = max(total_files, checked_total, 1)
                        progress_callback(
                            min(checked, effective_total),
                            effective_total,
                            "checking",
                            f"Checked {checked:,}/{effective_total:,} files",
                        )
                    except Exception as callback_error:
                        log_warning(LogTags.RCLONE, f"Upload progress callback error: {callback_error}")

                transferred_match = transferred_re.search(line)
                if transferred_match and progress_callback:
                    try:
                        transferred_count = int(transferred_match.group(1).replace(",", ""))
                        transferred_total = int(transferred_match.group(2).replace(",", ""))
                        if transferred_count <= 0 and transferred_total > 0:
                            continue
                        effective_total = max(transferred_total, transferred_count, 1)
                        progress_callback(
                            min(transferred_count, effective_total),
                            effective_total,
                            "uploading_stats",
                            f"Transferred {transferred_count:,}/{effective_total:,} files",
                        )
                    except Exception as callback_error:
                        log_warning(LogTags.RCLONE, f"Upload progress callback error: {callback_error}")

                if 'INFO  :' in line:
                    try:
                        msg = line.split('INFO  :', 1)[1].strip()
                        if any(keyword in msg for keyword in ['Copied', 'Updated', 'Deleted', 'Renamed', 'There was nothing to transfer']):
                            log_info(LogTags.RCLONE, msg)
                            if any(keyword in msg for keyword in ['Copied', 'Updated', 'Renamed']):
                                files_uploaded += 1
                                if progress_callback:
                                    try:
                                        progress_callback(
                                            files_uploaded,
                                            max(total_files, files_uploaded, 1),
                                            "uploading_file",
                                            msg,
                                        )
                                    except Exception as callback_error:
                                        log_warning(LogTags.RCLONE, f"Upload progress callback error: {callback_error}")
                    except Exception:
                        log_debug(LogTags.RCLONE, "Failed to parse upload INFO line")
                elif 'NOTICE:' in line or 'WARNING:' in line:
                    try:
                        if 'NOTICE:' in line:
                            msg = line.split('NOTICE:', 1)[1].strip()
                            if msg and not any(skip in msg for skip in ['Transferred:', 'Checks:', 'Elapsed time']):
                                log_warning(LogTags.RCLONE, f"Notice: {msg}")
                        else:
                            msg = line.split('WARNING:', 1)[1].strip()
                            if msg:
                                log_warning(LogTags.RCLONE, f"Warning: {msg}")
                    except Exception:
                        log_debug(LogTags.RCLONE, "Failed to parse upload NOTICE/WARNING line")
                elif 'ERROR :' in line or 'ERROR:' in line:
                    try:
                        msg = line.split('ERROR', 1)[1].strip().lstrip(':').strip()
                        log_error(LogTags.RCLONE, f"Error: {msg}")
                    except Exception:
                        log_debug(LogTags.RCLONE, "Failed to parse upload ERROR line")

            process.wait()

            if process.returncode == 0:
                if progress_callback:
                    try:
                        final_total = max(total_files, files_uploaded, 1)
                        progress_callback(
                            final_total,
                            final_total,
                            "complete",
                            f"Upload complete to {display_name} ({sync_mode})",
                        )
                    except Exception as callback_error:
                        log_warning(LogTags.RCLONE, f"Upload progress callback error: {callback_error}")
                log_success(LogTags.RCLONE, f"Upload complete to '{display_name}' ({sync_mode})")
                return {"success": True, "mode": sync_mode, "files_uploaded": files_uploaded}

            error_output = ''.join(recent_lines).strip()
            log_error(LogTags.RCLONE, f"Upload failed to '{display_name}': {error_output}")
            return {"success": False, "mode": sync_mode, "error": error_output}
        except Exception as e:
            import traceback
            log_error(LogTags.RCLONE, f"Error in upload_folder: {str(e)}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}
    
    def download_file(self, drive_id: str, remote_path: str, local_path: Path) -> bool:
        """Download a file from Google Drive"""
        try:
            if not self.rclone_binary:
                return False

            auth_args = self._get_drive_auth_args()
            if auth_args is None:
                return False
            
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            remote_root = self._build_remote_root_path(drive_id)
            remote_relative_path = self._sanitize_remote_relative_path(remote_path)
            remote_file = f"{remote_root}{remote_relative_path}"
            
            result = subprocess.run(  # nosec B603
                [
                    self.rclone_binary, 'copyto', remote_file, str(local_path),
                    '--config', str(self.config_path),
                    *auth_args,
                ],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                return True
            else:
                log_error(LogTags.RCLONE, f"Download failed: {result.stderr}")
                return False
                
        except Exception as e:
            import traceback
            log_error(LogTags.RCLONE, f"Error downloading: {str(e)}\n{traceback.format_exc()}")
            return False
    
    def sync_multiple_folders(
        self,
        sync_tasks: List[Dict[str, Any]],
        max_workers: int = 1,
        progress_callback: Optional[BatchProgressCallback] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Sync multiple Google Drive folders sequentially or in parallel.
        
        Args:
            sync_tasks: List of dicts with 'drive_id', 'drive_name', 'local_folder', and optional 'task_index'
            max_workers: Maximum number of parallel rclone processes (default 1 for sequential)
            progress_callback: Optional callback function(task_index, drive_name, filename, files_checked, 
                             files_transferred, phase) for file transfer progress during each drive sync.
        
        Returns:
            Dictionary mapping drive_id to sync result
        """
        results = {}
        
        def sync_single_drive(task: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
            """Helper function to sync a single drive"""
            drive_id = task['drive_id']
            drive_name = task['drive_name']
            local_folder = task['local_folder']
            task_index = task.get('task_index', 0)
            
            # Create a callback that includes the task index
            # sync_folder calls with: (filename, files_checked, files_transferred, phase)
            file_callback = None
            if progress_callback:
                file_callback = lambda filename, files_checked, files_transferred, phase, transfer_total=0: progress_callback(task_index, drive_name, filename, files_checked, files_transferred, phase, transfer_total)
            
            try:
                success = self.sync_folder(drive_id, local_folder, drive_name=drive_name, progress_callback=file_callback)
                return drive_id, {'success': success, 'drive_name': drive_name}
            except Exception as e:
                log_error(LogTags.RCLONE, f"Exception syncing {drive_name}: {str(e)}")
                return drive_id, {'success': False, 'drive_name': drive_name, 'error': str(e)}
        
        # Execute syncs using ThreadPoolExecutor (max_workers=1 means sequential)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_task = {executor.submit(sync_single_drive, task): task for task in sync_tasks}
            
            # Collect results as they complete
            for future in as_completed(future_to_task):
                try:
                    drive_id, result = future.result()
                    results[drive_id] = result
                except Exception as e:
                    task = future_to_task[future]
                    log_error(LogTags.RCLONE, f"Exception for {task['drive_name']}: {str(e)}")
                    results[task['drive_id']] = {'success': False, 'error': str(e)}
        
        successful = sum(1 for r in results.values() if r.get('success', False))
        log_success(LogTags.RCLONE, f"Batch sync completed: {successful}/{len(sync_tasks)} drives synced successfully")
        
        return results


