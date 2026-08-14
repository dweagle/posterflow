from fastapi import APIRouter, Query, HTTPException, WebSocket, WebSocketDisconnect, Depends
from typing import List, Optional
from pydantic import BaseModel
from pathlib import Path
from sqlalchemy.orm import Session
from core.config import settings
from core.logging import log_warning, log_error, log_user_action, LogTags
from core.log_stream import hub
from core.websocket import WebSocketConnectionManager, shutdown_event, watch_disconnect
from database import get_db
from models.setting import upsert_setting
import re
import asyncio
from collections import deque

router = APIRouter(prefix="/api/logs", tags=["logs"])

_ws = WebSocketConnectionManager()
LOG_WS_HEARTBEAT_INTERVAL_SECONDS = 30.0

class LogEntry(BaseModel):
    timestamp: str
    level: str
    message: str


LOG_LINE_PATTERN = r'(\d{2}[/:]\d{2}[/:]\d{2} \d{2}:\d{2}:\d{2}) \| (\w+)\s+\| (.+)'
LOG_WINDOW_BYTES = 1_000_000
# Keep the initial window modest: Safari's Reader-availability scan repeatedly forces
# full layout of every mounted row, and ~5k rows wedges its main thread (Jul 2026).
LOG_WINDOW_MAX_LINES = 1000


def read_log_window(end_offset: Optional[int] = None, max_lines: int = LOG_WINDOW_MAX_LINES) -> tuple[list[dict], int]:
    """The newest `max_lines` parsed entries ending at byte `end_offset` (file end when None),
    plus the byte cursor of the first returned line — pass it back as `end_offset` to page
    further into history. Cursor 0 means the start of the file was reached.

    Binary reads keep the cursor byte-accurate (log lines contain multi-byte glyphs)."""
    log_file = Path(settings.log_file)
    if not log_file.exists():
        return [], 0
    with open(log_file, 'rb') as f:
        f.seek(0, 2)
        file_end = f.tell()
        end = file_end if end_offset is None else min(end_offset, file_end)
        if end <= 0:
            return [], 0
        start = max(0, end - LOG_WINDOW_BYTES)
        f.seek(start)
        if start > 0:
            f.readline()  # skip the partial line the window cut through
        kept_start = f.tell()
        raw = f.read(max(0, end - kept_start))

    lines = raw.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()  # the window ends on a line boundary; don't let '' consume max_lines
    if len(lines) > max_lines:
        dropped, lines = lines[:-max_lines], lines[-max_lines:]
        kept_start += sum(len(line) + 1 for line in dropped)

    entries = []
    for line_bytes in lines:
        match = re.match(LOG_LINE_PATTERN, line_bytes.decode('utf-8', errors='replace').strip())
        if match:
            timestamp, log_level, message = match.groups()
            entries.append({'timestamp': timestamp, 'level': log_level, 'message': message})
    return entries, kept_start

class LogSettings(BaseModel):
    debug_enabled: bool

@router.get("/", response_model=List[LogEntry])
def get_logs(
    lines: int = Query(default=100, le=1000),
    level: Optional[str] = Query(default=None)
) -> List[LogEntry]:
    """
    Get recent log entries from the log file.
    Filter by level if provided (INFO, WARNING, ERROR, DEBUG)
    """
    log_file = Path(settings.log_file)
    
    if not log_file.exists():
        log_warning(LogTags.LOGGING, "Log file does not exist yet")
        return []
    
    try:
        # Read last N lines from log file
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
        # Parse log entries
        log_entries = []
        # Pattern matches: YY/MM/DD HH:MM:SS | LEVEL | message
        # Also supports historical YY:MM:DD separator found in older logs/tests.
        log_pattern = r'(\d{2}[/:]\d{2}[/:]\d{2} \d{2}:\d{2}:\d{2}) \| (\w+)\s+\| (.+)'
        
        for line in recent_lines:
            match = re.match(log_pattern, line.strip())
            if match:
                timestamp, log_level, message = match.groups()
                
                # Filter by level if specified
                if level and log_level != level:
                    continue
                
                log_entries.append(LogEntry(
                    timestamp=timestamp,
                    level=log_level,
                    message=message
                ))
        
        return log_entries
    except Exception as e:
        log_error(LogTags.LOGGING, f"Error reading logs: {str(e)}")
        return []

@router.get("/search")
def search_logs(q: str = Query(min_length=2), limit: int = Query(default=10000, le=10000)) -> dict:
    """Case-insensitive substring search over the WHOLE log file (the page's live search
    only covers loaded lines). Streams the file once; keeps the newest `limit` matches."""
    log_file = Path(settings.log_file)
    needle = q.strip().lower()
    if not log_file.exists() or not needle:
        return {"entries": [], "total": 0, "truncated": False}

    matches: deque = deque(maxlen=limit)
    total = 0
    with open(log_file, 'rb') as f:
        for line_bytes in f:
            match = re.match(LOG_LINE_PATTERN, line_bytes.decode('utf-8', errors='replace').strip())
            if not match:
                continue
            timestamp, log_level, message = match.groups()
            if needle in message.lower():
                total += 1
                matches.append({'timestamp': timestamp, 'level': log_level, 'message': message})
    return {"entries": list(matches), "total": total, "truncated": total > len(matches)}


@router.get("/history")
def get_log_history(end: int = Query(ge=0)) -> dict:
    """Page backwards through the log file: `end` is the cursor a previous window returned
    (the WS backlog frame carries the first one). Returns older entries plus the next cursor;
    cursor 0 means the file start was reached."""
    entries, cursor = read_log_window(end_offset=end)
    return {"entries": entries, "cursor": cursor}


@router.get("/debug-status")
def get_debug_status() -> dict[str, bool]:
    """Get current debug mode status"""
    return {"debug_enabled": settings.debug}

@router.post("/debug-toggle")
def toggle_debug(enable: bool, db: Session = Depends(get_db)) -> dict[str, str | bool]:
    """
    Toggle debug mode and update logging level.
    """
    settings.debug = enable

    # Persist toggle so it survives restarts
    upsert_setting(db, "debug_enabled", "true" if enable else "false")
    db.commit()
    
    # Reconfigure logging with new debug level
    from core.logging import setup_logging
    setup_logging(debug_enabled=enable)
    
    log_user_action(f"Debug logging {'enabled' if enable else 'disabled'}")
    
    return {"debug_enabled": enable, "message": f"Debug mode {'enabled' if enable else 'disabled'}"}

@router.post("/clear")
def clear_logs(confirm: bool = False) -> dict[str, str]:
    """Clear all log entries from the log file"""
    if not confirm:
        raise HTTPException(status_code=400, detail="Must pass confirm=true to clear logs")

    log_file = Path(settings.log_file)
    
    try:
        # Truncate the log file
        if log_file.exists():
            with open(log_file, 'w') as f:
                f.write('')
            log_user_action("Log file cleared")
            return {"message": "Logs cleared successfully"}
        else:
            return {"message": "Log file does not exist"}
    except Exception as e:
        log_error(LogTags.LOGGING, f"Error clearing logs: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to clear logs")
@router.websocket("/ws")
async def websocket_logs(websocket: WebSocket) -> None:
    """Real-time log streaming: file replay for the backlog, then push from the loguru sink.

    Live entries arrive from core.log_stream's broadcast hub — pushed the moment they are
    logged, no file polling, no re-parsing. The log file is only read once per connection,
    for the initial backlog.
    """
    await websocket.accept()
    _ws.active_connections.append(websocket)
    _ws.check_warning()

    disconnected, watcher = watch_disconnect(websocket)

    # Subscribe BEFORE reading the backlog: lines logged during the file read may then
    # appear twice at the seam, but none can be lost (the reverse order drops them).
    queue = hub.subscribe()
    try:
        last_heartbeat_time = asyncio.get_running_loop().time()

        # Initial backlog as ONE batch frame, with the history cursor so the page can
        # lazy-load older chunks via GET /api/logs/history when scrolled to the top.
        entries, cursor = read_log_window()
        if entries:
            try:
                await websocket.send_json({'type': 'batch', 'entries': entries, 'history_cursor': cursor})
                last_heartbeat_time = asyncio.get_running_loop().time()
            except Exception:
                # Client disconnected during initial send
                return
        
        while True:
            if shutdown_event.is_set() or disconnected.is_set():
                return
            try:
                first = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                now = asyncio.get_running_loop().time()
                if (now - last_heartbeat_time) >= LOG_WS_HEARTBEAT_INTERVAL_SECONDS:
                    await websocket.send_json({'type': 'heartbeat', 'heartbeat': int(now)})
                    last_heartbeat_time = now
                continue

            # Coalesce a burst into one frame: brief window, then drain whatever queued.
            await asyncio.sleep(0.25)
            entries = [first]
            while True:
                try:
                    entries.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await websocket.send_json({'type': 'batch', 'entries': entries})
                last_heartbeat_time = asyncio.get_running_loop().time()
            except Exception:
                # Client disconnected during streaming
                return

    except (WebSocketDisconnect, asyncio.CancelledError):
        # Client disconnected or server shutting down - this is normal, no logging needed
        return
    except Exception:
        # Silently handle errors during streaming - don't log to avoid recursion
        return
    finally:
        watcher.cancel()
        hub.unsubscribe(queue)
        # Remove from the active list unless prune_stale() already did (routine race)
        if websocket in _ws.active_connections:
            _ws.active_connections.remove(websocket)
            _ws.check_warning()