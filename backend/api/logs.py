from fastapi import APIRouter, Query, HTTPException, WebSocket, WebSocketDisconnect, Depends
from typing import List, Optional
from pydantic import BaseModel
from pathlib import Path
from sqlalchemy.orm import Session
from core.config import settings
from core.logging import log_warning, log_error, log_user_action, LogTags
from core.websocket import WebSocketConnectionManager, shutdown_event
from database import get_db
from models.setting import upsert_setting
import re
import asyncio

router = APIRouter(prefix="/api/logs", tags=["logs"])

_ws = WebSocketConnectionManager()
LOG_WS_HEARTBEAT_INTERVAL_SECONDS = 30.0

class LogEntry(BaseModel):
    timestamp: str
    level: str
    message: str

class LogSettings(BaseModel):
    debug_enabled: bool

@router.get("/", response_model=List[LogEntry])
async def get_logs(
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

@router.get("/debug-status")
async def get_debug_status() -> dict[str, bool]:
    """Get current debug mode status"""
    return {"debug_enabled": settings.debug}

@router.post("/debug-toggle")
async def toggle_debug(enable: bool, db: Session = Depends(get_db)) -> dict[str, str | bool]:
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
async def clear_logs(confirm: bool = False) -> dict[str, str]:
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
    """WebSocket endpoint for real-time log streaming"""
    conn_id = _ws.next_conn_id()
    
    await websocket.accept()
    _ws.active_connections.append(websocket)
    _ws.check_warning()
    
    log_file = Path(settings.log_file)
    log_pattern = r'(\d{2}[/:]\d{2}[/:]\d{2} \d{2}:\d{2}:\d{2}) \| (\w+)\s+\| (.+)'
    
    try:
        last_heartbeat_time = asyncio.get_running_loop().time()

        # Send initial logs (last 1000 lines)
        if log_file.exists():
            with open(log_file, 'r') as f:
                f.seek(0, 2)
                end_pos = f.tell()
                f.seek(max(0, end_pos - 200_000))
                if end_pos > 200_000:
                    f.readline()
                recent_lines = f.readlines()[-1000:]
                
                for line in recent_lines:
                    match = re.match(log_pattern, line.strip())
                    if match:
                        timestamp, log_level, message = match.groups()
                        try:
                            await websocket.send_json({
                                'timestamp': timestamp,
                                'level': log_level,
                                'message': message
                            })
                            last_heartbeat_time = asyncio.get_running_loop().time()
                        except Exception:
                            # Client disconnected during initial send
                            return
        
        # Tail log file for new entries
        last_position = log_file.stat().st_size if log_file.exists() else 0
        
        while True:
            # Sleep, but wake immediately if the server is shutting down
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=0.5)
                return  # Shutdown signaled — exit cleanly
            except asyncio.TimeoutError:
                pass  # Normal poll interval elapsed, continue
            now = asyncio.get_running_loop().time()
            
            if not log_file.exists():
                if (now - last_heartbeat_time) >= LOG_WS_HEARTBEAT_INTERVAL_SECONDS:
                    await websocket.send_json({'type': 'heartbeat', 'heartbeat': int(now)})
                    last_heartbeat_time = now
                continue
                
            current_size = log_file.stat().st_size
            sent_log_entry = False
            
            # If file was truncated (cleared), reset position
            if current_size < last_position:
                last_position = 0
            
            # If file grew, read new lines
            if current_size > last_position:
                with open(log_file, 'r') as f:
                    f.seek(last_position)
                    new_lines = f.readlines()
                    last_position = f.tell()
                    
                    for line in new_lines:
                        match = re.match(log_pattern, line.strip())
                        if match:
                            timestamp, log_level, message = match.groups()
                            try:
                                await websocket.send_json({
                                    'timestamp': timestamp,
                                    'level': log_level,
                                    'message': message
                                })
                                sent_log_entry = True
                            except Exception:
                                # Client disconnected during streaming
                                return

            if sent_log_entry:
                last_heartbeat_time = now
            elif (now - last_heartbeat_time) >= LOG_WS_HEARTBEAT_INTERVAL_SECONDS:
                await websocket.send_json({'type': 'heartbeat', 'heartbeat': int(now)})
                last_heartbeat_time = now
    
    except (WebSocketDisconnect, asyncio.CancelledError):
        # Client disconnected or server shutting down - this is normal, no logging needed
        return
    except Exception:
        # Silently handle errors during streaming - don't log to avoid recursion
        return
    finally:
        # ALWAYS remove connection from list, regardless of how we exit
        if websocket in _ws.active_connections:
            _ws.active_connections.remove(websocket)
            _ws.check_warning()
        else:
            log_warning(
                LogTags.WEBSOCKET,
                f"Log WS #{conn_id} not in active list during cleanup",
                connection_id=conn_id,
            )