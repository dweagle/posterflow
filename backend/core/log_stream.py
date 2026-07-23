"""Live log streaming: a loguru sink that pushes records straight to WebSocket clients.

The browser stream used to tail the log file (0.5s polling + regex re-parsing, one poller
per open page). Instead, setup_logging registers `broadcast_sink` as a loguru sink, so every
record is pushed the moment it is logged: structured (no parsing), instant, and one pipeline
no matter how many pages are open. The file stays the durable log — connections replay their
initial backlog from it, then follow the hub.

Thread model: jobs log from worker threads, so the sink hops onto the server's event loop
with call_soon_threadsafe and fans out to per-client asyncio queues. A stalled client drops
its oldest entries rather than blocking logging or growing unbounded.
"""

import asyncio
from typing import Any, Dict, Optional

# Keep in sync with core.logging's timestamp formats (importing it here would be circular).
_TIMESTAMP_FORMAT = "%y/%m/%d %H:%M:%S"

# Per-client buffer: above the frontend's own cap, far below memory-relevant sizes.
CLIENT_QUEUE_MAX = 8192


class LogBroadcastHub:
    """Fan-out of log entries to subscribed WebSocket connections."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def subscribe(self) -> asyncio.Queue:
        """Register the calling connection (must run on the event loop)."""
        self._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def publish(self, entry: Dict[str, Any]) -> None:
        """Hand an entry to every client. Safe from any thread; no-op with no subscribers."""
        loop = self._loop
        if loop is None or loop.is_closed() or not self._clients:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, entry)
        except RuntimeError:
            pass  # loop shut down between the check and the call

    def _fanout(self, entry: Dict[str, Any]) -> None:
        for q in list(self._clients):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                # Stalled client: stay live by dropping its oldest entry, never by blocking.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(entry)
                except asyncio.QueueFull:
                    pass


hub = LogBroadcastHub()


def broadcast_sink(message: Any) -> None:
    """Loguru sink: forward each record to the hub as the frame shape the page speaks."""
    if not hub._clients:
        return  # nobody watching — skip even building the entry
    record = message.record
    raw = record["message"]
    if not raw.strip():
        return  # structural blank spacers exist for the files, not the stream
    hub.publish({
        "timestamp": record["time"].strftime(_TIMESTAMP_FORMAT),
        "level": record["level"].name,
        "message": raw,
    })
