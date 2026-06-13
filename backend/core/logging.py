from loguru import logger
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
from core.config import settings


LOGURU_TIMESTAMP_FORMAT = "YY/MM/DD HH:mm:ss"
PYTHON_TIMESTAMP_FORMAT = "%y/%m/%d %H:%M:%S"

# ============================================================================
# LOGGING ICONS - Plain white characters for consistency
# ============================================================================
class LogIcons:
    """Standard icons for logging output"""
    START = "▶"      # Start of operation
    STOP = "■"       # End of operation
    SUCCESS = "✓"    # Successful completion
    ERROR = "✗"      # Error/failure
    WARNING = "⚠"    # Warning
    INFO = "•"       # Information
    PROGRESS = "→"   # Progress indicator
    SKIP = "○"       # Skipped item
    DEBUG = "◆"      # Debug information
    USER = "◉"       # User action

# ============================================================================
# LOGGING TAGS - Standard tags for all operations
# ============================================================================
class LogTags:
    """Standard tags for consistent logging across the application"""
    STARTUP = "STARTUP"
    SHUTDOWN = "SHUTDOWN"
    SCHEDULER = "SCHEDULER"
    QUEUE = "QUEUE"
    JOB = "JOB"
    SYNC = "SYNC"
    SYNC_ALL = "SYNC-ALL"
    RCLONE = "RCLONE"
    DRIVES = "DRIVES"
    SCANNER = "SCANNER"
    MATCH = "MATCH"
    UNMATCHED = "UNMATCHED"
    POSTER_RENAMER = "POSTER_RENAMER"
    BORDER_REPLACER = "BORDER_REPLACER"
    POSTER_STATS = "POSTER_STATS"
    BACKUP = "BACKUP"
    USER_ACTION = "USER ACTION"
    WEBSOCKET = "WEBSOCKET"
    LOGGING = "LOGGING"
    DATABASE = "DATABASE"
    API = "API"
    UPLOADER = "UPLOADER"
    WORKFLOW = "WORKFLOW"
    ARR = "ARR"
    IDARR = "IDARR"
    MONITOR = "MONITOR"
    CLEANUP = "CLEANUP"

# ============================================================================
# LOGGING HELPERS
# ============================================================================
def log_separator(char: str = "=", length: int = 80) -> str:
    """Print a separator line"""
    return char * length

def log_section_start(tag: str, title: str, icon: str = LogIcons.START) -> None:
    """Log the start of a major section"""
    logger.info(log_separator())
    logger.info(f"[{tag:^15}] {icon} {title}")
    logger.info(log_separator())

def log_section_end(tag: str, title: str, icon: str = LogIcons.STOP) -> None:
    """Log the end of a major section"""
    logger.info(log_separator())
    logger.info(f"[{tag:^15}] {icon} {title}")
    logger.info(log_separator())

def log_step(tag: str, step_num: int, total_steps: int, message: str, icon: str = LogIcons.PROGRESS) -> None:
    """Log a step in a multi-step process"""
    logger.info(f"[{tag:^15}] {icon} Step {step_num}/{total_steps}: {message}")

def log_success(tag: str, message: str, **context: Any) -> None:
    """Log a success message with context (uses INFO level)"""
    if context:
        logger.bind(**context).info(f"[{tag:^15}] {LogIcons.SUCCESS} {message}")
    else:
        logger.info(f"[{tag:^15}] {LogIcons.SUCCESS} {message}")

def log_error(tag: str, message: str, **context: Any) -> None:
    """Log an error message with context"""
    if context:
        logger.bind(**context).error(f"[{tag:^15}] {LogIcons.ERROR} {message}")
    else:
        logger.error(f"[{tag:^15}] {LogIcons.ERROR} {message}")

def log_warning(tag: str, message: str, **context: Any) -> None:
    """Log a warning message with context"""
    if context:
        logger.bind(**context).warning(f"[{tag:^15}] {LogIcons.WARNING} {message}")
    else:
        logger.warning(f"[{tag:^15}] {LogIcons.WARNING} {message}")

def log_info(tag: str, message: str, **context: Any) -> None:
    """Log an info message with context"""
    if context:
        logger.bind(**context).info(f"[{tag:^15}] {LogIcons.INFO} {message}")
    else:
        logger.info(f"[{tag:^15}] {LogIcons.INFO} {message}")

def log_debug(tag: str, message: str, **context: Any) -> None:
    """Log a debug message with context"""
    if context:
        logger.bind(**context).debug(f"[{tag:^15}] {LogIcons.DEBUG} {message}")
    else:
        logger.debug(f"[{tag:^15}] {LogIcons.DEBUG} {message}")

def log_user_action(action: str, **context: Any) -> None:
    """Log a user action"""
    if context:
        logger.bind(**context).info(f"[{LogTags.USER_ACTION:^15}] {LogIcons.USER} {action}")
    else:
        logger.info(f"[{LogTags.USER_ACTION:^15}] {LogIcons.USER} {action}")

def setup_logging(debug_enabled: Optional[bool] = None) -> None:
    """
    Configure Loguru with colored console and rotating file logs.
    Uses standardized format with aligned tags and icons.
    
    Date format: YY/MM/DD HH:MM:SS
    Tags: Centered in 15-character field
    Icons: Plain white characters for visual markers
    """
    # Use provided debug setting or fall back to config
    if debug_enabled is None:
        debug_enabled = settings.debug
    
    # Determine file log level
    if debug_enabled:
        file_level = "DEBUG"
    else:
        configured_level = settings.log_level.upper().strip() if settings.log_level else "INFO"
        allowed_levels = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
        file_level = configured_level if configured_level in allowed_levels else "INFO"
    
    # Remove default handler
    logger.remove()
    
    # Console handler - Colorized with aligned format
    logger.add(
        sys.stdout,
        level="DEBUG",
        format=f"<green>{{time:{LOGURU_TIMESTAMP_FORMAT}}}</green> │ <level>{{level: <8}}</level> │ <level>{{message}}</level>",
        colorize=True
    )
    
    # File handler - Same format without color codes
    logger.add(
        settings.log_file,
        level=file_level,
        format=f"{{time:{LOGURU_TIMESTAMP_FORMAT}}} | {{level: <8}} | {{message}}",
        rotation=settings.max_log_size,
        retention=settings.backup_count,
        compression="zip"
    )
    
    log_info(LogTags.LOGGING, f"Logging system initialized (file level: {file_level})")
    log_info(LogTags.LOGGING, f"Log file: {settings.log_file}")


def add_job_log_handler(job_type: str, job_id: int, job_name: Optional[str] = None) -> int:
    """
    Add a job-specific log handler that writes to /logs/{job_type}/{job_type}.log
    Rotates existing logs and adds section markers.
    
    Args:
        job_type: Type of job (e.g., "sync_one", "sync_all")
        job_id: Database job ID
        job_name: Optional descriptive name for the job
        
    Returns:
        Handler ID that can be used to remove the handler later
    """
    # Map job types to their corresponding LogTags
    job_type_to_tag = {
        "sync_one": LogTags.SYNC,
        "sync_all": LogTags.SYNC_ALL,
        "plex_upload": LogTags.UPLOADER,
        "workflow": LogTags.WORKFLOW,
        "poster_renamer": LogTags.POSTER_RENAMER,
        "border_replacer": LogTags.BORDER_REPLACER,
        "unmatched_assets": LogTags.UNMATCHED,
        "backup": LogTags.BACKUP,
        "idarr": LogTags.IDARR,
    }
    
    # Get the proper log tag for this job type
    log_tag = job_type_to_tag.get(job_type, job_type.upper())
    
    # Create job logs directory
    job_logs_dir = Path(settings.log_file).parent / job_type
    job_logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Rotate existing logs before creating new one
    rotate_job_logs(job_type, keep=10)
    
    # Current log filename (always the same name)
    log_filename = job_logs_dir / f"{job_type}.log"
    
    # Determine log level based on debug setting (same as main log)
    job_log_level = "DEBUG" if settings.debug else "INFO"
    
    # Add handler with standardized format
    handler_id = logger.add(
        str(log_filename),
        level=job_log_level,
        format=f"{{time:{LOGURU_TIMESTAMP_FORMAT}}} | {{level: <8}} | {{message}}",
        filter=lambda record: _should_log_to_job(record, job_type)
    )
    
    # Write section header to job log
    job_display_name = job_name or job_type.replace("_", " ").title()
    logger.debug(log_separator())
    logger.info(f"[{log_tag:^15}] {LogIcons.START} JOB STARTED: {job_display_name}")
    logger.debug(f"[{log_tag:^15}] {LogIcons.INFO} Job ID: {job_id}")
    logger.debug(f"[{log_tag:^15}] {LogIcons.INFO} Started: {datetime.now().strftime(PYTHON_TIMESTAMP_FORMAT)}")
    logger.debug(log_separator())
    
    log_debug(LogTags.LOGGING, f"Job log handler added: {log_filename.name}")
    
    return handler_id

def _should_log_to_job(record: dict, job_type: str) -> bool:
    """
    Filter function to determine if a log record should go to job log.
    Includes relevant tags based on job type.
    
    Note: Tags are centered in 15-char fields like [   WORKFLOW   ]
    so we check if the tag name appears anywhere in the message.
    """
    message = record.get("message", "")
    
    # Common tags that always go to job logs (check for tag name, not formatted version)
    common_tags = ["WORKER", "ERROR"]
    
    # Job-specific tags (using actual tag names, not formatted versions)
    job_specific_tags = {
        "sync_one": ["SYNC", "RCLONE"],
        "sync_all": ["SYNC-ALL", "SYNC", "RCLONE"],
        "plex_upload": ["UPLOADER", "DATABASE", "API", "POSTER_RENAMER", "BORDER_REPLACER", "SCANNER"],
        "poster_renamer": ["POSTER_RENAMER", "SCANNER", "ARR", "CLEANUP"],
        "border_replacer": ["BORDER_REPLACER"],
        "unmatched_assets": ["UNMATCHED", "SCANNER", "ARR", "POSTER_RENAMER"],
        "workflow": ["WORKFLOW", "SYNC", "SYNC-ALL", "POSTER_RENAMER", "BORDER_REPLACER", "UNMATCHED", "RCLONE", "SCANNER", "ARR", "UPLOADER", "CLEANUP"],
        "backup": ["BACKUP"],
        "idarr": ["IDARR", "RCLONE"],
    }
    
    # Get tags for this job type
    tags_to_check = common_tags + job_specific_tags.get(job_type, [])
    
    # Check if any tag name appears in the message (handles centered formatting)
    # Tags are formatted like [   WORKFLOW   ] so we check for the tag name itself
    for tag in tags_to_check:
        # Check if tag appears in the message (case sensitive)
        if tag in message:
            return True
    
    return False


def remove_job_log_handler(handler_id: int, job_type: str = None, success: bool = True) -> None:
    """
    Remove a job-specific log handler and write section footer.
    
    Args:
        handler_id: Handler ID returned by add_job_log_handler()
        job_type: Optional job type for footer formatting
        success: Whether the job completed successfully
    """
    try:
        # Write section footer before removing handler
        if job_type:
            # Map job types to their corresponding LogTags
            job_type_to_tag = {
                "sync_one": LogTags.SYNC,
                "sync_all": LogTags.SYNC_ALL,
                "plex_upload": LogTags.UPLOADER,
                "workflow": LogTags.WORKFLOW,
                "poster_renamer": LogTags.POSTER_RENAMER,
                "border_replacer": LogTags.BORDER_REPLACER,
                "unmatched_assets": LogTags.UNMATCHED,
                "backup": LogTags.BACKUP,
                "idarr": LogTags.IDARR,
            }
            
            # Get the proper log tag for this job type
            log_tag = job_type_to_tag.get(job_type, job_type.upper())
            
            status_icon = LogIcons.SUCCESS if success else LogIcons.ERROR
            status_text = "COMPLETED SUCCESSFULLY" if success else "COMPLETED WITH ERRORS"
            
            logger.debug(log_separator())
            logger.info(f"[{log_tag:^15}] {status_icon} JOB {status_text}")
            logger.debug(f"[{log_tag:^15}] {LogIcons.INFO} Ended: {datetime.now().strftime(PYTHON_TIMESTAMP_FORMAT)}")
            logger.debug(log_separator())
        
        logger.remove(handler_id)
        log_debug(LogTags.LOGGING, "Job log handler removed")
    except ValueError:
        # Handler already removed, ignore
        pass


def cleanup_old_job_logs(job_type: str, keep: int = 10) -> None:
    """
    Remove old job log files, keeping only the most recent N logs.
    This function is deprecated - use rotate_job_logs() instead.
    
    Args:
        job_type: Type of job (e.g., "sync_one", "sync_all")
        keep: Number of recent log files to keep (default: 10)
    """
    pass  # Now handled by rotate_job_logs


def rotate_job_logs(job_type: str, keep: int = 10) -> None:
    """
    Rotate job log files: sync_one.log → sync_one.1.log → sync_one.2.log → ... → sync_one.(keep-1).log
    Deletes the oldest log if it exceeds the keep limit.
    
    Args:
        job_type: Type of job (e.g., "sync_one", "sync_all")
        keep: Number of logs to keep including current (default: 10)
    """
    job_logs_dir = Path(settings.log_file).parent / job_type
    
    if not job_logs_dir.exists():
        return
    
    base_name = f"{job_type}.log"
    current_log = job_logs_dir / base_name
    
    # If current log doesn't exist, nothing to rotate
    if not current_log.exists():
        return
    
    # Delete the oldest log if we're at the limit (keep-1 because current will become .1)
    oldest_log = job_logs_dir / f"{job_type}.{keep-1}.log"
    if oldest_log.exists():
        try:
            oldest_log.unlink()
        except Exception as e:
            log_warning(LogTags.LOGGING, f"Failed to remove oldest log {oldest_log.name}: {e}")
    
    # Rotate existing numbered logs: .8 → .9, .7 → .8, etc.
    for i in range(keep - 2, 0, -1):
        old_file = job_logs_dir / f"{job_type}.{i}.log"
        new_file = job_logs_dir / f"{job_type}.{i+1}.log"
        
        if old_file.exists():
            try:
                old_file.rename(new_file)
            except Exception as e:
                log_warning(LogTags.LOGGING, f"Failed to rotate {old_file.name} → {new_file.name}: {e}")
    
    # Rotate current log to .1
    if current_log.exists():
        try:
            rotated_log = job_logs_dir / f"{job_type}.1.log"
            current_log.rename(rotated_log)
        except Exception as e:
            log_warning(LogTags.LOGGING, f"Failed to rotate current log: {e}")