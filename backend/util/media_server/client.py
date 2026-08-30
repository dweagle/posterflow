import traceback
from abc import ABC, abstractmethod
from typing import Any, Dict, FrozenSet, List, Optional

from util.media_server.types import (
    MediaServerInfo,
    MediaServerItem,
    MediaServerLibrary,
)


class MediaServerClient(ABC):
    """Provider-agnostic client for a media server (Plex, Jellyfin)."""

    server_type: str = ""
    capabilities: FrozenSet[str] = frozenset()

    def __init__(self) -> None:
        self.connect_status = False
        self.server_id: Optional[str] = None
        self.server_name: Optional[str] = None
        self.server_version: Optional[str] = None
        self.instance_name: Optional[str] = None  # user-configured name, set by callers for log labels

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def global_library_key(self, library_id: Any) -> str:
        """Server-scoped stable library key, matching the existing '<machineId>:<sectionKey>' convention."""
        return f"{self.server_id}:{library_id}"

    def _local_library_id(self, library_key: str) -> str:
        """Strip this server's prefix from a global library key; local keys pass through."""
        prefix = f"{self.server_id}:"
        if library_key.startswith(prefix):
            return library_key[len(prefix):]
        return library_key

    @abstractmethod
    def get_server_info(self) -> Optional[MediaServerInfo]:
        ...

    @abstractmethod
    def get_libraries(self) -> List[MediaServerLibrary]:
        ...

    @abstractmethod
    def get_library_items(
        self, library_key: str, item_types: Optional[List[str]] = None
    ) -> List[MediaServerItem]:
        """All movies/shows in a library, with provider ids and file paths."""
        ...

    @abstractmethod
    def get_collections(self, library_key: Optional[str] = None) -> List[MediaServerItem]:
        """Collections, per-library where the server supports it (Jellyfin box sets are server-global)."""
        ...

    @abstractmethod
    def get_seasons(self, show: MediaServerItem) -> List[MediaServerItem]:
        ...

    @abstractmethod
    def get_library_seasons(self, library_key: str) -> List[MediaServerItem]:
        """Every season in a show library in one call (group by parent_id); avoids per-show fetches."""
        ...

    def get_item(self, item_id: str) -> Optional[MediaServerItem]:
        """One item by its server id (e.g. a webhook rating key); None when missing/unsupported."""
        return None

    @abstractmethod
    def get_collection_items(self, collection: MediaServerItem) -> List[MediaServerItem]:
        """Member items of a collection/box set."""
        ...

    @abstractmethod
    def find_by_field(
        self, field: str, value: str, library_key: str, include_collections: bool = True
    ) -> List[MediaServerItem]:
        """Items in a library carrying a label/tag ("label") or genre ("genre");
        optionally also collections carrying it (border rules)."""
        ...

    @abstractmethod
    def find_by_provider_ids(
        self,
        provider_ids: Dict[str, str],
        media_type: str,
        library_keys: Optional[List[str]] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[MediaServerItem]:
        """Targeted lookup by tmdb/tvdb/imdb id (webhook path); title/year help servers without id search."""
        ...

    @abstractmethod
    def find_by_title(
        self,
        title: str,
        media_type: Optional[str] = None,
        library_keys: Optional[List[str]] = None,
    ) -> List[MediaServerItem]:
        """Title search WITHOUT id confirmation (legacy-agent fallback); caller vets the results."""
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        media_types: Optional[List[str]] = None,
        limit: int = 30,
    ) -> List[MediaServerItem]:
        ...

    @abstractmethod
    def upload_image(self, item: MediaServerItem, kind: str, filepath: str) -> bool:
        ...

    @abstractmethod
    def image_url(self, item: MediaServerItem, max_width: Optional[int] = None) -> Optional[str]:
        """Server-relative image path for backend proxying, or None."""
        ...

    def remove_label(self, item: MediaServerItem, label: str) -> bool:
        """Remove a label/tag from an item; True only if it was present and removed. No-op by default."""
        return False


_app_version_cache: Optional[str] = None


def _default_app_version() -> str:
    """App version for client identification headers; lazy to avoid a main import cycle,
    cached because get_app_version shells out to git."""
    global _app_version_cache
    if _app_version_cache is None:
        try:
            from main import get_app_version

            _app_version_cache = get_app_version()
        except Exception:
            _app_version_cache = ""
    return _app_version_cache


def create_media_server_client(
    instance: Dict[str, Any],
    logger: Any = None,
    app_version: str = "",
    timeout: Optional[int] = None,
    raise_on_error: bool = False,
) -> Optional[MediaServerClient]:
    """Create a connected media server client from an instance config dict.

    Returns None on any failure by default; with raise_on_error=True the underlying
    exception propagates instead (callers that report per-instance error text)."""
    from util.media_server.instances import instance_type

    server_type = instance_type(instance)
    url = (instance.get("url") or "").strip()
    api_key = (instance.get("api_key") or "").strip()
    if not url or not api_key:
        if raise_on_error:
            raise ValueError("missing url or api key")
        return None
    try:
        if server_type == "jellyfin":
            from util.media_server.jellyfin import JellyfinClient

            client = JellyfinClient(
                url, api_key, logger=logger, app_version=app_version or _default_app_version()
            )
        elif server_type == "plex":
            from util.media_server.plex import PlexClient

            client = PlexClient(url, api_key, logger=logger, timeout=timeout)
        else:
            if logger:
                logger.error(f"Unknown media server type: {server_type}")
            if raise_on_error:
                raise ValueError(f"unknown media server type: {server_type}")
            return None
        if not client.connect_status:
            if raise_on_error:
                raise RuntimeError("connection failed")
            return None
        client.instance_name = str(instance.get("name") or "").strip() or None
        return client
    except Exception as e:
        if raise_on_error:
            raise
        if logger:
            logger.error(f"Failed to create {server_type} client: {e}\n{traceback.format_exc()}")
        return None
