from typing import Any, Dict, List, Optional

from util.media_server.client import MediaServerClient
from util.media_server.types import (
    CAP_EDITIONS,
    CAP_LABELS,
    CAP_PER_LIBRARY_COLLECTIONS,
    CAP_SMART_COLLECTIONS,
    CAP_SQUAREART,
    IMAGE_KIND_BACKGROUND,
    IMAGE_KIND_LOGO,
    IMAGE_KIND_POSTER,
    IMAGE_KIND_SQUAREART,
    MediaServerInfo,
    MediaServerItem,
    MediaServerLibrary,
)

# Image kind → the plexapi upload method for it (all take filepath=)
UPLOAD_METHODS = {
    IMAGE_KIND_POSTER: "uploadPoster",
    IMAGE_KIND_LOGO: "uploadLogo",
    IMAGE_KIND_BACKGROUND: "uploadArt",
    IMAGE_KIND_SQUAREART: "uploadSquareArt",
}

MEDIA_TYPE_TO_SECTION_TYPE = {
    "movie": "movie",
    "movies": "movie",
    "show": "show",
    "shows": "show",
    "series": "show",
}


def extract_provider_ids(item: Any) -> Dict[str, str]:
    """tmdb/tvdb/imdb ids from plexapi guids + *id attrs (same parse as _extract_plex_id_keys)."""
    ids: Dict[str, str] = {}
    try:
        raw_guids = getattr(item, "guids", None) or []
    except Exception:
        raw_guids = []
    for guid in raw_guids:
        guid_value = str(getattr(guid, "id", guid)).strip().lower()
        if not guid_value:
            continue
        if "://" in guid_value:
            source, value = guid_value.split("://", 1)
        elif ":" in guid_value:
            source, value = guid_value.split(":", 1)
        else:
            continue
        source = source.strip().lower()
        value = value.strip().lower()
        if source in {"imdb", "tmdb", "tvdb"} and value:
            ids[source] = value
    for source in ("imdb", "tmdb", "tvdb"):
        try:
            value = getattr(item, f"{source}id", None)
        except Exception:
            continue
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized and normalized != "0":
            ids.setdefault(source, normalized)
    return ids


class PlexClient(MediaServerClient):
    """Plex client implementing MediaServerClient over plexapi."""

    server_type = "plex"
    capabilities = frozenset(
        {CAP_SQUAREART, CAP_EDITIONS, CAP_LABELS, CAP_SMART_COLLECTIONS, CAP_PER_LIBRARY_COLLECTIONS}
    )

    def __init__(
        self, url: str, api_key: str, logger: Any = None, server: Any = None, timeout: Optional[int] = None
    ) -> None:
        super().__init__()
        self.logger = logger
        self.url = url.rstrip("/")
        if server is None:
            from plexapi.server import PlexServer

            if timeout is not None:
                server = PlexServer(self.url, api_key, timeout=timeout)
            else:
                server = PlexServer(self.url, api_key)
        self.server = server
        self.server_id = str(getattr(server, "machineIdentifier", "") or "")
        self.server_name = str(getattr(server, "friendlyName", "") or "")
        self.server_version = str(getattr(server, "version", "") or "")
        self.connect_status = True
        self._sections_cache: Optional[List[Any]] = None

    def get_server_info(self) -> Optional[MediaServerInfo]:
        return MediaServerInfo(server_id=self.server_id, name=self.server_name, version=self.server_version)

    def _sections(self) -> List[Any]:
        if self._sections_cache is None:
            self._sections_cache = list(self.server.library.sections())
        return self._sections_cache

    def _find_section(self, library_key: str) -> Optional[Any]:
        local_id = self._local_library_id(str(library_key))
        for section in self._sections():
            if str(section.key) == local_id or str(section.title) == local_id:
                return section
        return None

    @staticmethod
    def _safe_getattr(item: Any, attr: str, default: Any = None) -> Any:
        # plexapi lazy reloads can 404 mid-read on stale items; treat as missing
        try:
            return getattr(item, attr, default)
        except Exception:
            return default

    def _parse_item(self, item: Any, section: Any = None) -> MediaServerItem:
        g = self._safe_getattr
        # Prefer the item-level _server machineIdentifier (test fakes carry it there)
        item_server = g(item, "_server")
        server_id = str(g(item_server, "machineIdentifier", "") or "").strip() or self.server_id
        section_id = str(g(item, "librarySectionID", "") or "").strip()
        section_key = str(g(item, "librarySectionKey", "") or "").strip()
        identity = section_key or section_id
        library_name = str(g(item, "librarySectionTitle", "") or "").strip()
        if not library_name and section is not None:
            library_name = str(section.title)

        added_at = g(item, "addedAt")
        released = g(item, "originallyAvailableAt")
        child_count = g(item, "leafCount")
        # Movie folder matching uses media[0].parts[0].file; shows use locations
        paths: List[str] = []
        try:
            for media in g(item, "media") or []:
                for part in g(media, "parts") or []:
                    part_file = g(part, "file")
                    if part_file:
                        paths.append(str(part_file))
        except Exception:
            paths = []
        if not paths:
            try:
                paths = [str(p) for p in (g(item, "locations") or [])]
            except Exception:
                paths = []

        try:
            tags = [str(g(label, "tag", label)) for label in (g(item, "labels") or [])]
        except Exception:
            tags = []

        return MediaServerItem(
            item_id=str(g(item, "ratingKey", "") or ""),
            item_type=str(g(item, "type", "") or ""),
            title=str(g(item, "title", "") or ""),
            year=g(item, "year"),
            provider_ids=extract_provider_ids(item),
            library_key=f"{server_id}:{identity}" if server_id and identity else (identity or None),
            library_name=library_name or None,
            index=g(item, "index"),
            parent_title=g(item, "parentTitle"),
            parent_id=str(g(item, "parentRatingKey", "") or "") or None,
            edition_title=g(item, "editionTitle") or None,
            paths=paths,
            tags=tags,
            added_at=added_at.isoformat() if hasattr(added_at, "isoformat") else added_at,
            release_date=released.isoformat() if hasattr(released, "isoformat") else released,
            child_count=int(child_count) if child_count is not None else None,
            smart=bool(g(item, "smart", False)),
            thumb_path=g(item, "thumb"),
            native=item,
            client=self,
        )

    def get_libraries(self) -> List[MediaServerLibrary]:
        return [
            MediaServerLibrary(
                key=str(s.key),
                title=str(s.title),
                type=str(s.type),
                locations=[str(loc) for loc in (self._safe_getattr(s, "locations") or [])],
            )
            for s in self._sections()
        ]

    def get_library_items(
        self, library_key: str, item_types: Optional[List[str]] = None
    ) -> List[MediaServerItem]:
        section = self._find_section(library_key)
        if section is None:
            return []
        return [self._parse_item(item, section) for item in section.all(includeGuids=1)]

    def get_collections(self, library_key: Optional[str] = None) -> List[MediaServerItem]:
        if library_key is not None:
            section = self._find_section(library_key)
            sections = [section] if section is not None else []
        else:
            sections = [s for s in self._sections() if str(s.type) in ("movie", "show")]
        collections: List[MediaServerItem] = []
        for section in sections:
            try:
                rows = section.collections()
            except AttributeError:
                rows = section.search(libtype="collection")
            for collection in rows:
                collections.append(self._parse_item(collection, section))
        return collections

    def get_collection_items(self, collection: MediaServerItem) -> List[MediaServerItem]:
        native = collection.native or self._fetch_native(collection)
        if native is None:
            return []
        return [self._parse_item(item) for item in native.items()]

    def find_by_field(
        self, field: str, value: str, library_key: str, include_collections: bool = True
    ) -> List[MediaServerItem]:
        if field not in ("label", "genre"):
            return []
        section = self._find_section(library_key)
        if section is None:
            return []
        items = [self._parse_item(i, section) for i in section.search(**{field: value})]
        if include_collections:
            # Plex keeps collection labels/genres in their own namespace
            try:
                items.extend(
                    self._parse_item(c, section)
                    for c in section.search(libtype="collection", **{field: value})
                )
            except Exception:
                pass  # some fields aren't filterable on collections; skip if unsupported
        return items

    def get_seasons(self, show: MediaServerItem) -> List[MediaServerItem]:
        native = show.native or self._fetch_native(show)
        if native is None:
            return []
        seasons = []
        for row in native.seasons():
            season = self._parse_item(row)
            season.parent_title = season.parent_title or show.title
            season.library_key = season.library_key or show.library_key
            season.library_name = season.library_name or show.library_name
            seasons.append(season)
        return seasons

    def get_library_seasons(self, library_key: str) -> List[MediaServerItem]:
        section = self._find_section(library_key)
        if section is None or str(section.type) != "show":
            return []
        return [self._parse_item(row, section) for row in section.search(libtype="season")]

    def get_item(self, item_id: str) -> Optional[MediaServerItem]:
        try:
            native = self.server.fetchItem(int(item_id))
        except Exception:
            return None
        return self._parse_item(native) if native is not None else None

    def find_by_provider_ids(
        self,
        provider_ids: Dict[str, str],
        media_type: str,
        library_keys: Optional[List[str]] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[MediaServerItem]:
        wanted = {k.lower(): str(v).strip().lower() for k, v in (provider_ids or {}).items() if v}
        media_type_lower = (media_type or "").lower()

        if media_type_lower in ("collection", "collections"):
            if not title:
                return []
            lowered = title.strip().lower()
            return [c for c in self.get_collections() if c.title.strip().lower() == lowered]

        section_type = MEDIA_TYPE_TO_SECTION_TYPE.get(media_type_lower)
        sections = []
        for section in self._sections():
            if section_type and str(section.type) != section_type:
                continue
            if not section_type and str(section.type) not in ("movie", "show"):
                continue
            if library_keys and not self._section_selected(section, library_keys):
                continue
            sections.append(section)

        # Per section: first guid source with results wins (tmdb → tvdb → imdb),
        # mirroring _build_plex_index_targeted's per-section break-on-hit
        results: List[MediaServerItem] = []
        for section in sections:
            for source in ("tmdb", "tvdb", "imdb"):
                value = wanted.get(source)
                if not value:
                    continue
                try:
                    matches = section.search(guid=f"{source}://{value}")
                except Exception:
                    continue
                if matches:
                    results.extend(self._parse_item(item, section) for item in matches)
                    break
        return results

    def find_by_title(
        self,
        title: str,
        media_type: Optional[str] = None,
        library_keys: Optional[List[str]] = None,
    ) -> List[MediaServerItem]:
        section_type = MEDIA_TYPE_TO_SECTION_TYPE.get((media_type or "").lower())
        results: List[MediaServerItem] = []
        for section in self._sections():
            if section_type and str(section.type) != section_type:
                continue
            if not section_type and str(section.type) not in ("movie", "show"):
                continue
            if library_keys and not self._section_selected(section, library_keys):
                continue
            try:
                matches = section.search(title=title)
            except Exception:
                continue
            results.extend(self._parse_item(item, section) for item in matches)
        return results

    def _section_selected(self, section: Any, library_keys: List[str]) -> bool:
        candidates = {
            str(section.key),
            str(section.title),
            self.global_library_key(section.key),
        }
        return any(str(key) in candidates for key in library_keys)

    def search(
        self,
        query: str,
        media_types: Optional[List[str]] = None,
        limit: int = 30,
    ) -> List[MediaServerItem]:
        plex_types = []
        for media_type in media_types or ["movie", "show", "collection"]:
            mapped = MEDIA_TYPE_TO_SECTION_TYPE.get(media_type.lower(), media_type.lower())
            if mapped not in plex_types:
                plex_types.append(mapped)
        results: List[MediaServerItem] = []
        for plex_type in plex_types:
            try:
                matches = self.server.search(query, mediatype=plex_type, limit=limit)
            except Exception:
                continue
            results.extend(self._parse_item(item) for item in matches)
        return results[:limit]

    def _fetch_native(self, item: MediaServerItem) -> Any:
        try:
            return self.server.fetchItem(int(item.item_id))
        except Exception:
            if self.logger:
                self.logger.error(f"Plex fetchItem failed for ratingKey {item.item_id}")
            return None

    def upload_image(self, item: MediaServerItem, kind: str, filepath: str) -> bool:
        method_name = UPLOAD_METHODS.get(kind)
        if not method_name:
            return False
        native = item.native or self._fetch_native(item)
        if native is None:
            return False
        method = getattr(native, method_name, None)
        if not callable(method):
            if self.logger:
                self.logger.warning(f"Plex item {item.title} has no {method_name} (plexapi too old?)")
            return False
        # Transport errors propagate so callers keep their per-asset error accounting
        method(filepath=filepath)
        return True

    def remove_label(self, item: MediaServerItem, label: str) -> bool:
        native = item.native or self._fetch_native(item)
        if native is None:
            return False
        try:
            labels = [str(getattr(entry, "tag", entry)) for entry in (getattr(native, "labels", None) or [])]
            if label not in labels:
                return False
            native.removeLabel(label)
            return True
        except Exception as ex:
            if self.logger:
                self.logger.error(f"Plex removeLabel('{label}') failed for {item.title}: {ex}")
            return False

    def image_url(self, item: MediaServerItem, max_width: Optional[int] = None) -> Optional[str]:
        return item.thumb_path or None
