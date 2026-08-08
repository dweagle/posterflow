import re
from urllib.parse import urlsplit

from core.config import settings
from core.logging import LogTags, log_warning

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
_IPV6_RE = re.compile(r"^[0-9A-Fa-f:]+$")


def normalize_frame_origin(raw: str) -> str | None:
    """Return the canonical scheme://host[:port] form, or None if not a clean origin.

    Values end up in the Content-Security-Policy response header, so anything
    that isn't a plain http(s) origin (wildcards, paths, userinfo, control
    characters) is rejected outright.
    """
    candidate = raw.strip()
    if not candidate or any(ch.isspace() or ord(ch) < 0x20 for ch in candidate):
        return None
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    if ":" in host:
        if not _IPV6_RE.match(host):
            return None
        host = f"[{host}]"
    elif not _HOSTNAME_RE.match(host):
        return None
    return f"{parts.scheme}://{host}:{port}" if port else f"{parts.scheme}://{host}"


# Parsed once — the ALLOWED_FRAME_ORIGINS env var can't change while the app runs
_parsed: list[str] | None = None


def get_allowed_frame_origins() -> list[str]:
    """Origins from the comma-separated ALLOWED_FRAME_ORIGINS env var."""
    global _parsed
    if _parsed is None:
        origins: list[str] = []
        for raw in settings.allowed_frame_origins.split(","):
            if not raw.strip():
                continue
            origin = normalize_frame_origin(raw)
            if origin is None:
                log_warning(
                    LogTags.STARTUP,
                    f"ALLOWED_FRAME_ORIGINS: ignoring invalid origin '{raw.strip()}' — "
                    "use http(s)://host[:port] with no path, and no wildcards",
                )
            elif origin not in origins:
                origins.append(origin)
        _parsed = origins
    return _parsed
