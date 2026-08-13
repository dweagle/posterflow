"""Shared WebSocket connection management for API routers."""

import asyncio

from starlette.websockets import WebSocketState
from fastapi import WebSocket

from core.logging import LogTags, log_info, log_warning

# Set during application shutdown so WebSocket polling loops can exit cleanly
# before uvicorn's graceful-shutdown timeout fires.
shutdown_event: asyncio.Event = asyncio.Event()

_DEFAULT_ENTER_THRESHOLD = 12
_DEFAULT_EXIT_THRESHOLD = 8


def watch_disconnect(websocket: WebSocket) -> tuple[asyncio.Event, asyncio.Task]:
    """Background reader that flags client disconnect for send-only WS loops."""
    disconnected = asyncio.Event()

    async def _reader() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except Exception:
            pass
        finally:
            disconnected.set()

    return disconnected, asyncio.create_task(_reader())


class WebSocketConnectionManager:
    """
    Manages a pool of active WebSocket connections with high-count warnings.

    Each router that serves a WebSocket endpoint creates its own instance so
    connection state stays isolated per endpoint.
    """

    def __init__(
        self,
        enter_threshold: int = _DEFAULT_ENTER_THRESHOLD,
        exit_threshold: int = _DEFAULT_EXIT_THRESHOLD,
    ) -> None:
        self.active_connections: list[WebSocket] = []
        self._counter: int = 0
        self._high_warning_active: bool = False
        self.enter_threshold = enter_threshold
        self.exit_threshold = exit_threshold

    def next_conn_id(self) -> int:
        """Increment and return the connection counter."""
        self._counter += 1
        return self._counter

    def prune_stale(self) -> int:
        """Remove disconnected WebSocket objects. Returns count removed."""
        stale = [
            ws
            for ws in self.active_connections
            if (
                ws.client_state == WebSocketState.DISCONNECTED
                or ws.application_state == WebSocketState.DISCONNECTED
            )
        ]
        for ws in stale:
            if ws in self.active_connections:
                self.active_connections.remove(ws)
        return len(stale)

    def count(self) -> int:
        """Prune stale connections and return the current active count."""
        self.prune_stale()
        return len(self.active_connections)

    def check_warning(self) -> int:
        """Log a warning when count crosses the enter/exit thresholds. Returns current count."""
        current = self.count()

        if current >= self.enter_threshold and not self._high_warning_active:
            log_warning(
                LogTags.WEBSOCKET,
                f"High connection count detected: {current} active connections",
                connections=current,
                enter_threshold=self.enter_threshold,
                exit_threshold=self.exit_threshold,
            )
            self._high_warning_active = True
        elif current <= self.exit_threshold and self._high_warning_active:
            log_info(
                LogTags.WEBSOCKET,
                f"Connection count returned to normal: {current} active connections",
                connections=current,
                enter_threshold=self.enter_threshold,
                exit_threshold=self.exit_threshold,
            )
            self._high_warning_active = False

        return current
