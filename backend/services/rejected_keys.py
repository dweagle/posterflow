"""One warning per rejected API key, however many calls fail on it.

Every source funnels its auth failures here so a bad key shows up in the log as one line that
names the key and where to fix it, instead of a per-card burst or nothing at all."""
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.logging import LogTags, log_warning

# A page of maker cards or a monitor run fails the same key dozens of times; say it once an hour.
INTERVAL = 60 * 60

_LABELS = {"tmdb": "TMDB", "tvdb": "TheTVDB", "fanart": "fanart.tv", "gemini": "Gemini"}
_HINTS = {
    "tmdb": "Check the key in Settings > General > API Keys. It is the v3 'API Key' on TMDB's API "
            "settings page, not the v4 'API Read Access Token'.",
    "tvdb": "Legacy v3 keys do not work with the v4 API. Create a v4 project key at thetvdb.com and "
            "enter it in Settings > General > API Keys. A user-supported key also needs its PIN.",
    "fanart": "Check the personal API key in Settings > General > API Keys. It is the key on your "
              "fanart.tv profile page.",
    "gemini": "Check the key in Settings > General > API Keys. Create one in Google AI Studio.",
}

_lock = threading.Lock()
_logged_at: dict[tuple[str, str], float] = {}


def warn_rejected_key(source: str, key: str, *, what: str = "API key") -> None:
    now = time.monotonic()
    with _lock:
        last = _logged_at.get((source, key))
        if last is not None and now - last < INTERVAL:
            return
        _logged_at[(source, key)] = now
    log_warning(LogTags.API, f"{_LABELS[source]} rejected the {what}. {_HINTS[source]}")


def _key_from_url(url: Any) -> str:
    try:
        return (parse_qs(urlparse(str(url or "")).query).get("api_key") or [""])[0]
    except ValueError:
        return ""


def note_tmdb_response(resp: Any, api_key: str | None = None) -> None:
    """Call on a TMDB response before raising on it: a 401 means the key was rejected."""
    if getattr(resp, "status_code", None) != 401:
        return
    if api_key is None:
        api_key = _key_from_url(getattr(getattr(resp, "request", None), "url", ""))
    warn_rejected_key("tmdb", api_key)
