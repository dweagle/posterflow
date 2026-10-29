import re
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from urllib.parse import quote

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

# Image kind → the /library/metadata/<ratingKey>/<endpoint> plexapi's upload methods POST to
UPLOAD_ENDPOINTS = {
    IMAGE_KIND_POSTER: "posters",
    IMAGE_KIND_LOGO: "clearLogos",
    IMAGE_KIND_BACKGROUND: "arts",
    IMAGE_KIND_SQUAREART: "squareArts",
}

MEDIA_TYPE_TO_SECTION_TYPE = {
    "movie": "movie",
    "movies": "movie",
    "show": "show",
    "shows": "show",
    "series": "show",
}


# Legacy-agent guid strings, the only ids a library on TheTVDB/TheMovieDB/IMDb (retired) or
# HAMA carries: "com.plexapp.agents.hama://tvdb-267440?lang=en". The new agents' Guid children
# never appear on those items, so without this they look id-less.
_LEGACY_GUID_RE = re.compile(r"^com\.plexapp\.agents\.(?P<agent>[a-z0-9]+)://(?P<value>[^?]+)")
# HAMA prefixes: tvdb (tvdb2..tvdb5 are season-mapping modes of the same TVDB id), tmdb/tsdb
# (TMDB movie/show), imdb. anidb-N has no TVDB equivalent here and is left unmapped.
_HAMA_VALUE_RE = re.compile(r"^(?P<source>tvdb|tmdb|tsdb|imdb)\d?-(?P<id>[a-z0-9]+)$")


def legacy_guid_ids(guid: Any) -> Dict[str, str]:
    """tmdb/tvdb/imdb ids from a legacy agent guid string, or {} for anything else."""
    match = _LEGACY_GUID_RE.match(str(guid or "").strip().lower())
    if not match:
        return {}
    agent, value = match.group("agent"), match.group("value")
    if agent == "hama":
        hama = _HAMA_VALUE_RE.match(value)
        if not hama:
            return {}
        source = {"tsdb": "tmdb"}.get(hama.group("source"), hama.group("source"))
        value = hama.group("id")
    else:
        source = {"thetvdb": "tvdb", "themoviedb": "tmdb", "imdb": "imdb"}.get(agent, "")
    if source == "imdb":
        return {"imdb": value} if value.startswith("tt") else {}
    if source in ("tmdb", "tvdb") and value.isdigit():
        return {source: value}
    return {}


def extract_provider_ids(item: Any) -> Dict[str, str]:
    """tmdb/tvdb/imdb ids from plexapi guids + *id attrs (same parse as _extract_plex_id_keys),
    with the legacy guid string filling any gap."""
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
    try:
        legacy = legacy_guid_ids(getattr(item, "guid", None))
    except Exception:
        legacy = {}
    for source, value in legacy.items():
        ids.setdefault(source, value)
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
        self._labeled_ids_cache: Dict[Tuple[str, str], Set[str]] = {}
        self._probe_item_cache: Dict[str, Any] = {}

    # Rows per listing request; one page of plexapi objects is alive at a time
    LISTING_PAGE = 500
    # Rating keys per multi-key metadata request
    METADATA_BATCH = 100

    def get_server_info(self) -> Optional[MediaServerInfo]:
        return MediaServerInfo(server_id=self.server_id, name=self.server_name, version=self.server_version)

    def _sections(self) -> List[Any]:
        if self._sections_cache is None:
            self._sections_cache = list(self.server.library.sections())
        return self._sections_cache

    def _find_section(self, library_key: str) -> Optional[Any]:
        local_id = self._local_library_id(str(library_key))
        if local_id.startswith("/library/sections/"):
            local_id = local_id.rsplit("/", 1)[-1]
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

    def _parse_item(self, item: Any, section: Any = None, *, bulk: bool = False) -> MediaServerItem:
        g = self._safe_getattr
        if bulk:
            # Read the row as delivered: plexapi otherwise refetches full metadata on the first empty attribute
            try:
                item._autoReload = False
            except Exception:
                pass
        # Prefer the item-level _server machineIdentifier (test fakes carry it there)
        item_server = g(item, "_server")
        server_id = str(g(item_server, "machineIdentifier", "") or "").strip() or self.server_id
        section_id = str(g(item, "librarySectionID", "") or "").strip()
        section_key = str(g(item, "librarySectionKey", "") or "").strip()
        # Listing rows carry only the id; upload records key libraries by the full-object form
        identity = section_key or (f"/library/sections/{section_id}" if section_id else "")
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
        # Only full metadata carries the locked fields (a locked poster field means someone
        # chose or uploaded the poster; unlocked means the agent's own). Listing rows never do,
        # so a bulk row stays unknown until fill_thumb_locks() asks in batches.
        thumb_locked: Optional[bool] = None
        if not bulk:
            try:
                fields = g(item, "fields")
                if fields is not None:
                    thumb_locked = any(str(g(f, "name", "")) == "thumb" and bool(g(f, "locked", False)) for f in fields)
            except Exception:
                thumb_locked = None

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
            thumb_locked=thumb_locked,
            original_title=str(g(item, "originalTitle", "") or "").strip() or None,
            native=None if bulk else item,
            client=self,
        )

    def _iter_listing(self, section: Any, **filters: Any) -> Iterator[Any]:
        start = 0
        while True:
            page = section.search(
                includeGuids=1,
                container_start=start,
                container_size=self.LISTING_PAGE,
                maxresults=self.LISTING_PAGE,
                **filters,
            )
            yield from page
            if len(page) < self.LISTING_PAGE:
                return
            start += len(page)

    def _fill_show_paths(self, shows: List[MediaServerItem]) -> None:
        # Section listings carry no Location for shows; the multi-key metadata endpoint does
        by_id = {s.item_id: s for s in shows if s.item_id and not s.paths}
        ids = list(by_id)
        for i in range(0, len(ids), self.METADATA_BATCH):
            chunk = ids[i : i + self.METADATA_BATCH]
            try:
                data = self.server.query(f"/library/metadata/{','.join(chunk)}")
            except Exception as ex:
                if self.logger:
                    self.logger.warning(f"Plex show locations fetch failed ({len(chunk)} shows): {ex}")
                continue
            for element in data if data is not None else []:
                show = by_id.get(str(element.get("ratingKey") or ""))
                if show is not None:
                    show.paths = [
                        str(loc.get("path")) for loc in element if loc.tag == "Location" and loc.get("path")
                    ]

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
        items = [self._parse_item(row, section, bulk=True) for row in self._iter_listing(section, libtype=str(section.type))]
        if str(section.type) == "show":
            self._fill_show_paths(items)
        return items

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
        if not show.item_id:
            return []
        from plexapi.video import Season

        # The request Show.seasons() makes, without needing the Show object
        rows = self.server.fetchItems(f"/library/metadata/{show.item_id}/children?excludeAllLeaves=1&includeGuids=1", Season)
        seasons = []
        for row in rows:
            season = self._parse_item(row, bulk=True)
            season.parent_title = season.parent_title or show.title
            season.library_key = season.library_key or show.library_key
            season.library_name = season.library_name or show.library_name
            seasons.append(season)
        return seasons

    def get_library_seasons(self, library_key: str) -> List[MediaServerItem]:
        section = self._find_section(library_key)
        if section is None or str(section.type) != "show":
            return []
        return [self._parse_item(row, section, bulk=True) for row in self._iter_listing(section, libtype="season")]

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
                matches = self._search_external_guid(section, f"{source}://{value}")
                if matches:
                    results.extend(self._parse_item(item, section) for item in matches)
                    break
        return results

    def _search_external_guid(self, section: Any, external_guid: str) -> List[Any]:
        # Plex's guid filter only matches the primary plex:// guid, so the external id is
        # translated through the agent's match endpoint first (what plexapi's getGuid does)
        probe = self._section_probe_item(section)
        if probe is None:
            return []
        try:
            candidates = probe.matches(agent=getattr(section, "agent", None), title=external_guid.replace("://", "-"))
        except Exception:
            return []
        wanted = external_guid.lower()
        for candidate in candidates or []:
            plex_guid = str(getattr(candidate, "guid", "") or "")
            if not plex_guid.startswith("plex://"):
                continue
            try:
                items = section.search(guid=plex_guid)
            except Exception:
                continue
            hits = []
            for item in items:
                guids = {str(getattr(g, "id", "") or "").lower() for g in (self._safe_getattr(item, "guids") or [])}
                if not guids or wanted in guids:
                    hits.append(item)
            if hits:
                return hits
        return []

    def _section_probe_item(self, section: Any) -> Optional[Any]:
        # matches() hangs off an item, not the section; any item in the section will do
        key = str(getattr(section, "key", ""))
        if key not in self._probe_item_cache:
            try:
                found = section.search(maxresults=1)
            except Exception:
                found = []
            self._probe_item_cache[key] = found[0] if found else None
        return self._probe_item_cache[key]

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
        endpoint = UPLOAD_ENDPOINTS.get(kind)
        if not endpoint or not item.item_id:
            return False
        with open(filepath, "rb") as fh:
            data = fh.read()
        # Transport errors propagate so callers keep their per-asset error accounting
        self.server.query(f"/library/metadata/{item.item_id}/{endpoint}", method=self.server._session.post, data=data)
        return True

    def labeled_item_ids(self, library_key: str, label: str) -> Set[str]:
        cache_key = (str(library_key), label)
        cached = self._labeled_ids_cache.get(cache_key)
        if cached is not None:
            return cached
        ids: Set[str] = set()
        section = self._find_section(str(library_key))
        if section is not None:
            # Listings carry no Label children; the server-side label filter names the carriers
            libtypes = [str(section.type), "collection"]
            if str(section.type) == "show":
                libtypes.insert(1, "season")
            for libtype in libtypes:
                try:
                    for row in self._iter_listing(section, libtype=libtype, label=label):
                        rating_key = str(self._safe_getattr(row, "ratingKey", "") or "")
                        if rating_key:
                            ids.add(rating_key)
                except Exception as ex:
                    if self.logger:
                        self.logger.warning(f"Plex label lookup '{label}' ({libtype}) failed in '{section.title}': {ex}")
        self._labeled_ids_cache[cache_key] = ids
        return ids

    # Full metadata (not the listing) carries the locked fields; batches of ids, trimmed of
    # what we do not need, cost ~0.3 s per 100 movies.
    _LOCK_QUERY = "?excludeAllLeaves=1&includeExtras=0&includeRelated=0&includeChapters=0&includeMarkers=0&includeReviews=0&includeOnDeck=0"

    def fill_thumb_locks(self, items: List[MediaServerItem]) -> None:
        by_id = {item.item_id: item for item in items if item.item_id and item.thumb_locked is None}
        ids = list(by_id)
        for i in range(0, len(ids), self.METADATA_BATCH):
            chunk = ids[i : i + self.METADATA_BATCH]
            try:
                data = self.server.query(f"/library/metadata/{','.join(chunk)}{self._LOCK_QUERY}")
            except Exception as ex:
                if self.logger:
                    self.logger.warning(f"Plex locked-field fetch failed ({len(chunk)} items): {ex}")
                continue
            for element in data if data is not None else []:
                item = by_id.get(str(element.get("ratingKey") or ""))
                if item is not None:
                    item.thumb_locked = any(
                        child.tag == "Field" and child.get("name") == "thumb" and str(child.get("locked") or "") == "1"
                        for child in element
                    )

    def selected_poster_key(self, item: MediaServerItem) -> str:
        """The key of the poster Plex shows: 'upload://posters/<sha1>' for an upload, the
        provider's key otherwise, '' when Plex can't say. Bulk rows have no native object."""
        from plexapi.media import Poster
        try:
            for image in self.server.fetchItems(f"/library/metadata/{item.item_id}/posters", cls=Poster):
                if getattr(image, "selected", False):
                    return str(getattr(image, "ratingKey", None) or getattr(image, "key", "") or "")
        except Exception as ex:
            if self.logger:
                self.logger.debug(f"Plex posters lookup failed for ratingKey {item.item_id}: {ex}")
        return ""

    def remove_label(self, item: MediaServerItem, label: str) -> bool:
        if not item.item_id:
            return False
        labeled = self.labeled_item_ids(str(item.library_key or ""), label)
        if item.item_id not in labeled:
            return False
        section = self._find_section(str(item.library_key or ""))
        if section is None:
            return False
        try:
            from plexapi.utils import joinArgs, searchType

            # The request plexapi's removeLabel() sends, without needing the item object
            params = {
                "id": item.item_id,
                "type": searchType(item.item_type),
                "label[].tag.tag-": quote(label),
                "label.locked": 1,
            }
            self.server.query(f"/library/sections/{section.key}/all{joinArgs(params)}", method=self.server._session.put)
            labeled.discard(item.item_id)
            return True
        except Exception as ex:
            if self.logger:
                self.logger.error(f"Plex removeLabel('{label}') failed for {item.title}: {ex}")
            return False

    def image_url(self, item: MediaServerItem, max_width: Optional[int] = None) -> Optional[str]:
        return item.thumb_path or None
