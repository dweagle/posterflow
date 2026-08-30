from typing import Any, Dict, Optional
from urllib.parse import quote


def instance_type(instance: Dict[str, Any]) -> str:
    """Media server type of an instance config entry; absent field = plex (legacy configs)."""
    try:
        return str(instance.get("type") or "plex").strip().lower() or "plex"
    except AttributeError:
        return "plex"


def is_plex_instance(instance: Dict[str, Any]) -> bool:
    return instance_type(instance) == "plex"


def is_jellyfin_instance(instance: Dict[str, Any]) -> bool:
    return instance_type(instance) == "jellyfin"


def server_type_label(server_type: str) -> str:
    """Display name for a server type: "Plex" / "Jellyfin" (unknown types capitalized)."""
    normalized = str(server_type or "").strip().lower()
    return {"plex": "Plex", "jellyfin": "Jellyfin"}.get(normalized, normalized.capitalize() or "Server")


def server_label(server_type: str, name: Optional[str]) -> str:
    """Log/display label: "Plex 'name'" / "Jellyfin 'name'" (type alone when the name adds nothing)."""
    label = server_type_label(server_type)
    cleaned = str(name or "").strip()
    if cleaned and cleaned.lower() != label.lower():
        return f"{label} '{cleaned}'"
    return label


def instance_label(instance: Dict[str, Any]) -> str:
    """server_label() for an instance config dict."""
    try:
        name = instance.get("name")
    except AttributeError:
        name = None
    return server_label(instance_type(instance), name)


def thumb_proxy_url(instance_name: str, thumb_path: Optional[str]) -> Optional[str]:
    """Backend proxy URL for a server-relative image path (display-only, never published)."""
    if not thumb_path:
        return None
    return (
        f"/api/posterflow/plex-upload/plex-thumb"
        f"?instance={quote(str(instance_name), safe='')}&key={quote(str(thumb_path), safe='')}"
    )
