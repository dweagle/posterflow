import re
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from urllib.parse import quote, urlencode

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

# selected_image_key's answer when Plex lists the item's images and shows none of them (a blank poster)
NO_IMAGE_SELECTED = "none://"

MEDIA_TYPE_TO_SECTION_TYPE = {
    "movie": "movie",
    "movies": "movie",
    "show": "show",
    "shows": "show",
    "series": "show",
}

# The Metadata subfolder holding an item type's bundles; a season's images live in its show's
PLEX_TYPE_DIRS = {"movie": "Movies", "show": "TV Shows", "season": "TV Shows", "collection": "Collections"}


def bundle_identity(item: Any, parent: Any = None) -> Optional[Tuple[str, str]]:
    """What names an item's bundle folder: (its type folder, its guid), a season's being its
    show's; None when the guid is unknown. Two items with one identity share the folder."""
    native = getattr(item, "native", None)
    if item.item_type == "season":
        guid = (getattr(parent, "guid", None) or getattr(item, "parent_guid", None)
                or getattr(getattr(parent, "native", None), "guid", None) or getattr(native, "parentGuid", None))
        return (PLEX_TYPE_DIRS["season"], str(guid)) if guid else None
    guid = getattr(item, "guid", None) or getattr(native, "guid", None)
    if not guid:
        return None
    guid = str(guid)
    # A movie edition is its own metadata item: Plex hashes the edition-qualified guid
    # ("plex://movie/<id>/edition/Anniversary%20Edition") for the bundle name.
    edition = getattr(item, "edition_title", None)
    if edition and "/edition/" not in guid:
        guid = f"{guid}/edition/{quote(str(edition), safe='')}"
    return PLEX_TYPE_DIRS.get(item.item_type, "TV Shows"), guid


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


def _ratings(item: Any) -> Dict[str, float]:
    """The ratings Plex holds for an item (0-10) — including whatever Kometa's
    mass_*_rating_update wrote there. audienceRating / rating (critic) / userRating."""
    out: Dict[str, float] = {}
    for key, attr in (("audience", "audienceRating"), ("critic", "rating"), ("user", "userRating")):
        # A partial plexapi object reloads itself on attribute access; a stale one raises.
        try:
            val = getattr(item, attr, None) if not isinstance(item, dict) else item.get(attr)
            if val is not None:
                out[key] = float(val)
        except Exception:
            pass
    return out


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
        self._copies_asked: Dict[Tuple[str, str], Optional[List[MediaServerItem]]] = {}  # bundle identity → copies, title by title
        self._shared_listed = False  # every library was listed once (enough titles were asked about)
        self._shared_listing: Optional[Dict[Tuple[str, str], List[MediaServerItem]]] = None  # None after a failed listing

    # Rows per listing request; one page of plexapi objects is alive at a time
    LISTING_PAGE = 500
    # Rating keys per multi-key metadata request
    METADATA_BATCH = 100
    # Titles asked about one at a time before every library is listed once instead
    SHARED_LOOKUPS = 25

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

    @staticmethod
    def _still_loading(item: Any) -> bool:
        """A new movie or show Plex has not finished loading the details of: its row carries no
        updatedAt yet. Fetching the item before that leaves it without ids and cast, so the row
        is then read as delivered, never refetched."""
        attrib = getattr(getattr(item, "_data", None), "attrib", None)
        if attrib is None or attrib.get("type") not in ("movie", "show") or attrib.get("updatedAt"):
            return False
        try:
            item._autoReload = False
        except Exception:
            pass
        return True

    def _parse_item(self, item: Any, section: Any = None, *, bulk: bool = False) -> MediaServerItem:
        g = self._safe_getattr
        loading = self._still_loading(item)
        bulk = bulk or loading
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
            ratings=_ratings(item),
            guid=str(g(item, "guid", "") or "") or None,
            parent_guid=str(g(item, "parentGuid", "") or "") or None,
            content_rating=str(g(item, "contentRating", "") or "").strip() or None,
            loading=loading,
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
        for element in self._metadata_rows(list(by_id), "show locations", "shows"):
            show = by_id.get(str(element.get("ratingKey") or ""))
            if show is not None:
                show.paths = [str(loc.get("path")) for loc in element if loc.tag == "Location" and loc.get("path")]

    def _metadata_rows(self, ids: List[str], what: str, noun: str, query: str = "") -> Iterator[Any]:
        """Full metadata of these ids, METADATA_BATCH per request; a failed batch is logged and skipped."""
        for i in range(0, len(ids), self.METADATA_BATCH):
            chunk = ids[i : i + self.METADATA_BATCH]
            try:
                data = self.server.query(f"/library/metadata/{','.join(chunk)}{query}")
            except Exception as ex:
                if self.logger:
                    self.logger.warning(f"Plex {what} fetch failed ({len(chunk)} {noun}): {ex}")
                continue
            yield from (data if data is not None else [])

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
        """One listing per library, then the poster lock and labels of its collections in
        batches: a listing row carries neither, and asking for each collection costs a request."""
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
            listed = [self._parse_item(row, section, bulk=True) for row in rows]
            self._fill_collection_details(listed)
            collections.extend(listed)
        return collections

    def _fill_collection_details(self, collections: List[MediaServerItem]) -> None:
        by_id = {c.item_id: c for c in collections if c.item_id}
        for element in self._metadata_rows(list(by_id), "collection details", "collections", self._LOCK_QUERY):
            item = by_id.get(str(element.get("ratingKey") or ""))
            if item is not None:
                item.thumb_locked = self._thumb_locked(element)
                item.tags = [str(child.get("tag")) for child in element if child.tag == "Label" and child.get("tag")]

    @staticmethod
    def _thumb_locked(element: Any) -> bool:
        return any(child.tag == "Field" and child.get("name") == "thumb" and str(child.get("locked") or "") == "1" for child in element)

    def get_collection_items(self, collection: MediaServerItem) -> List[MediaServerItem]:
        """The members as listing rows (no labels, no show paths), never one request per member.
        A failed request raises: no answer must not read as an empty collection."""
        if not collection.item_id:
            return []
        section = self._find_section(collection.library_key) if collection.library_key else None
        rows = self.server.fetchItems(
            f"/library/collections/{collection.item_id}/children?includeGuids=1", container_size=self.LISTING_PAGE
        )
        return [self._parse_item(row, section, bulk=True) for row in rows]

    def find_by_field(
        self, field: str, value: str, library_key: str, include_collections: bool = True
    ) -> List[MediaServerItem]:
        if field not in ("label", "genre"):
            return []
        section = self._find_section(library_key)
        if section is None:
            return []
        # Listing rows (no labels, no show paths): a full parse costs one request per item
        items = [self._parse_item(row, section, bulk=True) for row in self._iter_listing(section, **{field: value})]
        if include_collections:
            # Plex keeps collection labels/genres in their own namespace
            try:
                items.extend(
                    self._parse_item(row, section, bulk=True)
                    for row in self._iter_listing(section, libtype="collection", **{field: value})
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

    def item_is_loading(self, item_id: str) -> bool:
        try:
            rows = self.server.fetchItems(f"/library/all?id={quote(str(item_id), safe='')}&includeGuids=1")
        except Exception:
            return False
        return any(self._still_loading(row) for row in rows)

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
                self._still_loading(item)  # an empty guid list must not make plexapi refetch a loading item
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
                found = section.search(maxresults=5)
            except Exception:
                found = []
            probe = next((row for row in found if not self._still_loading(row)), None)
            if probe is None and found:
                return None  # only loading items so far: look again on the next search
            self._probe_item_cache[key] = probe
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

    def shared_copies(self, item: MediaServerItem, parent: Optional[MediaServerItem] = None) -> Optional[List[MediaServerItem]]:
        """The copies of the item's title when it is in more than one library (Movies and 4K
        Movies): two items with ONE bundle folder, uploads included. [] for a title in one
        library, None when Plex could not say; a season answers for its show. The first titles
        cost one small request each, after that every library is listed once: a webhook asks
        about its title, a full run lists the libraries."""
        identity = bundle_identity(item, parent)
        if identity is None or identity[0] not in (PLEX_TYPE_DIRS["movie"], PLEX_TYPE_DIRS["show"]):
            return []
        if not self._shared_listed and len(self._copies_asked) >= self.SHARED_LOOKUPS:
            self._shared_listed = True
            self._shared_listing = self._list_shared_titles()
        if self._shared_listed:
            return None if self._shared_listing is None else self._shared_listing.get(identity, [])
        if identity not in self._copies_asked:
            self._copies_asked[identity] = self._find_copies(identity)
        return self._copies_asked[identity]

    def _find_copies(self, identity: Tuple[str, str]) -> Optional[List[MediaServerItem]]:
        # One request: Plex's guid filter on /library/all covers every library
        guid = identity[1].split("/edition/")[0]
        try:
            rows = self.server.fetchItems(f"/library/all?guid={quote(guid, safe='')}")
        except Exception as ex:
            if self.logger:
                self.logger.debug(f"Plex copies lookup failed for {guid}: {ex}")
            return None
        copies = [c for c in (self._parse_item(row, bulk=True) for row in rows) if bundle_identity(c) == identity]
        return copies if len(copies) > 1 else []

    def _list_shared_titles(self) -> Optional[Dict[Tuple[str, str], List[MediaServerItem]]]:
        copies: Dict[Tuple[str, str], List[MediaServerItem]] = {}
        try:
            for section in self._sections():
                if str(section.type) not in ("movie", "show"):
                    continue
                for row in self._iter_listing(section, libtype=str(section.type)):
                    entry = self._parse_item(row, section, bulk=True)
                    identity = bundle_identity(entry)
                    if identity is not None:
                        copies.setdefault(identity, []).append(entry)
        except Exception as ex:
            if self.logger:
                self.logger.warning(f"Plex library listing failed while looking for titles in two libraries: {ex}")
            return None
        return {identity: items for identity, items in copies.items() if len(items) > 1}

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
        for element in self._metadata_rows(list(by_id), "locked-field", "items", self._LOCK_QUERY):
            item = by_id.get(str(element.get("ratingKey") or ""))
            if item is not None:
                item.thumb_locked = self._thumb_locked(element)

    def selected_image_key(self, item: MediaServerItem, kind: str) -> str:
        """The key of the image Plex shows for `kind`: 'upload://<folder>/<sha1>' for an upload,
        the provider's key otherwise, NO_IMAGE_SELECTED when it shows none, '' when Plex can't
        say. Bulk rows have no native object."""
        endpoint = UPLOAD_ENDPOINTS.get(kind)
        if not endpoint:
            return ""
        from plexapi.media import Poster
        try:
            for image in self.server.fetchItems(f"/library/metadata/{item.item_id}/{endpoint}", cls=Poster):
                if getattr(image, "selected", False):
                    return str(getattr(image, "ratingKey", None) or getattr(image, "key", "") or "")
        except Exception as ex:
            if self.logger:
                self.logger.debug(f"Plex {endpoint} lookup failed for ratingKey {item.item_id}: {ex}")
            return ""
        return NO_IMAGE_SELECTED

    def selected_poster_key(self, item: MediaServerItem) -> str:
        return self.selected_image_key(item, IMAGE_KIND_POSTER)

    # The library edit field that locks each image kind, which is also its delete endpoint
    _IMAGE_FIELDS = {IMAGE_KIND_POSTER: "thumb", IMAGE_KIND_BACKGROUND: "art", IMAGE_KIND_LOGO: "clearLogo", IMAGE_KIND_SQUAREART: "squareArt"}
    # Plex's number for an item type, as a library edit wants it
    _TYPE_NUMBERS = {"movie": 1, "show": 2, "season": 3, "collection": 18}
    # Ids per library edit request
    EDIT_BATCH = 200

    def unlock_images(self, items: List[MediaServerItem], kinds: List[str]) -> Set[str]:
        """Unlock these image kinds on many items: one library edit per library and item type,
        which Plex takes for a list of ids. Returns the ids it could not unlock."""
        fields = {f"{self._IMAGE_FIELDS[kind]}.locked": 0 for kind in kinds if kind in self._IMAGE_FIELDS}
        failed: Set[str] = set()
        groups: Dict[Tuple[str, int], List[str]] = {}
        for item in items:
            section = self._find_section(str(item.library_key or "")) if item.library_key else None
            number = self._TYPE_NUMBERS.get(item.item_type)
            if section is None or number is None or not item.item_id:
                failed.add(item.item_id)
            else:
                groups.setdefault((str(section.key), number), []).append(item.item_id)
        if not fields:
            return failed
        for (section_key, number), ids in groups.items():
            for start in range(0, len(ids), self.EDIT_BATCH):
                chunk = ids[start:start + self.EDIT_BATCH]
                query = urlencode({"type": number, "id": ",".join(chunk), **fields})
                try:
                    self.server.query(f"/library/sections/{section_key}/all?{query}", method=self.server._session.put)
                except Exception as ex:
                    failed.update(chunk)
                    if self.logger:
                        self.logger.error(f"Plex could not unlock {', '.join(kinds)} on {len(chunk)} item(s): {ex}")
        return failed

    def reset_images(self, item: MediaServerItem, kinds: List[str], *, unlocked: bool = False) -> Dict[str, bool]:
        """Put the item back on Plex's own image of each kind: unlock it, then select the first
        provider (agent) one. A kind with no provider image has its uploaded selection deleted,
        and the item's metadata is refreshed once for all of those. The item itself is never
        fetched. `unlocked` says unlock_images already did the first step."""
        done = {kind: False for kind in kinds}
        wanted = [kind for kind in kinds if kind in UPLOAD_ENDPOINTS]
        if not wanted or not item.item_id or (not unlocked and self.unlock_images([item], wanted)):
            return done
        from plexapi.media import Poster
        cleared: List[str] = []  # no provider image: the upload was deleted, a refresh follows
        for kind in wanted:
            try:
                images = self.server.fetchItems(f"/library/metadata/{item.item_id}/{UPLOAD_ENDPOINTS[kind]}", cls=Poster)
                agent = next((p for p in images if not str(getattr(p, "ratingKey", "") or "").startswith("upload://")), None)
                if agent is not None:
                    agent.select()
                    done[kind] = True
                else:
                    self.server.query(f"/library/metadata/{item.item_id}/{self._IMAGE_FIELDS[kind]}", method=self.server._session.delete)
                    cleared.append(kind)
            except Exception as ex:
                if self.logger:
                    self.logger.error(f"Plex {kind} reset failed for {item.title}: {ex}")
        if cleared:
            try:
                base = "collections" if item.item_type == "collection" else "metadata"
                self.server.query(f"/library/{base}/{item.item_id}/refresh", method=self.server._session.put)
                done.update({kind: True for kind in cleared})
            except Exception as ex:
                if self.logger:
                    self.logger.error(f"Plex refresh after the {', '.join(cleared)} reset failed for {item.title}: {ex}")
        return done

    def reset_image(self, item: MediaServerItem, kind: str) -> bool:
        return self.reset_images(item, [kind]).get(kind, False)

    def reset_poster(self, item: MediaServerItem) -> bool:
        return self.reset_image(item, IMAGE_KIND_POSTER)

    # ── media details (resolution, HDR, audio) for the overlays ──
    _HDR_TRANSFERS = ("smpte2084", "arib-std-b67")  # PQ (HDR10, HDR10+, Dolby Vision) and HLG

    def _raw_rows(self, path: str) -> Iterator[Any]:
        """Every row of a listing, as XML elements, page by page."""
        start = 0
        sep = "&" if "?" in path else "?"
        while True:
            root = self.server.query(f"{path}{sep}X-Plex-Container-Start={start}&X-Plex-Container-Size={self.LISTING_PAGE}")
            rows = list(root) if root is not None else []
            yield from rows
            if len(rows) < self.LISTING_PAGE:
                return
            start += len(rows)

    @classmethod
    def _media_files(cls, video: Any) -> List[Dict[str, Any]]:
        """One entry per file of a movie or episode row that carries its streams."""
        files: List[Dict[str, Any]] = []
        for media in video.iter("Media"):
            for part in media.iter("Part"):
                streams = list(part.iter("Stream"))
                videos = [st for st in streams if st.attrib.get("streamType") == "1"]
                dovi = any(str(st.attrib.get("DOVIPresent") or "").lower() in ("1", "true") for st in videos)
                titles = [st.attrib.get("extendedDisplayTitle") or st.attrib.get("displayTitle") for st in streams if st.attrib.get("streamType") == "2"]
                files.append({
                    "resolution": str(media.attrib.get("videoResolution") or ""),
                    "hdr": dovi or any(str(st.attrib.get("colorTrc") or "").lower() in cls._HDR_TRANSFERS for st in videos),
                    "dovi": dovi,
                    "path": str(part.attrib.get("file") or ""),
                    "audio_titles": [str(t) for t in titles if t],
                })
        return files

    def library_media(self, library_key: str) -> Dict[str, List[Dict[str, Any]]]:
        """One listing with streams per library: the movies, or every episode (grouped under its
        show and its season)."""
        section = self._find_section(library_key)
        section_type = str(getattr(section, "type", "") or "")
        if section is None or section_type not in ("movie", "show"):
            return {}
        out: Dict[str, List[Dict[str, Any]]] = {}
        libtype = 1 if section_type == "movie" else 4
        try:
            for row in self._raw_rows(f"/library/sections/{section.key}/all?type={libtype}&includeElements=Stream"):
                files = self._media_files(row)
                owners = [row.attrib.get("ratingKey")] if libtype == 1 else [row.attrib.get("grandparentRatingKey"), row.attrib.get("parentRatingKey")]
                for owner in owners:
                    if owner:
                        out.setdefault(str(owner), []).extend(files)
        except Exception as ex:
            if self.logger:
                self.logger.warning(f"Plex media listing failed for library {library_key}: {ex}")
            return {}
        return out

    def item_media(self, item: MediaServerItem) -> List[Dict[str, Any]]:
        paths = {"movie": f"/library/metadata/{item.item_id}", "show": f"/library/metadata/{item.item_id}/allLeaves?includeElements=Stream",
                 "season": f"/library/metadata/{item.item_id}/children?includeElements=Stream"}
        path = paths.get(str(item.item_type))
        if not path or not item.item_id:
            return []
        try:
            rows = list(self._raw_rows(path))
        except Exception as ex:
            if self.logger:
                self.logger.debug(f"Plex media lookup failed for ratingKey {item.item_id}: {ex}")
            return []
        return [f for row in rows for f in self._media_files(row)]

    @staticmethod
    def _poster_key(poster: Any) -> str:
        return str(getattr(poster, "ratingKey", None) or getattr(poster, "key", "") or "")

    def _posters(self, item: MediaServerItem) -> List[Any]:
        from plexapi.media import Poster
        return list(self.server.fetchItems(f"/library/metadata/{item.item_id}/posters", cls=Poster))

    def server_poster(self, item: MediaServerItem, ref: str = "") -> Optional[Tuple[str, bytes]]:
        """Plex keeps listing its provider posters after an upload is selected: the one `ref`
        names, else the selected one, else the first. An upload is never the source (it could
        be a badged poster of ours)."""
        try:
            own = [p for p in self._posters(item) if not self._poster_key(p).startswith("upload://")]
        except Exception as ex:
            if self.logger:
                self.logger.debug(f"Plex posters lookup failed for ratingKey {item.item_id}: {ex}")
            return None
        chosen = next((p for p in own if ref and self._poster_key(p) == ref), None) \
            or next((p for p in own if getattr(p, "selected", False)), None) or (own[0] if own else None)
        thumb = str(getattr(chosen, "thumb", "") or "") if chosen is not None else ""
        if not thumb:
            return None
        try:
            if thumb.startswith("/"):
                resp = self.server._session.get(self.server.url(thumb, includeToken=True), timeout=60)
            else:
                import requests
                resp = requests.get(thumb, timeout=60)  # a provider's own address: no Plex session, no token
            resp.raise_for_status()
        except Exception as ex:
            if self.logger:
                self.logger.error(f"Plex could not serve its poster for {item.title}: {type(ex).__name__}")  # the address carries the token
            return None
        return (self._poster_key(chosen), resp.content) if resp.content else None

    def restore_server_poster(self, item: MediaServerItem, ref: str = "") -> bool:
        """Unlock the poster and select the provider poster `ref` names; when Plex no longer
        lists it (or none was recorded) the usual reset picks the first provider poster."""
        if ref and not ref.startswith("upload://"):
            try:
                chosen = next((p for p in self._posters(item) if self._poster_key(p) == ref), None)
                if chosen is not None:
                    native = item.native or self._fetch_native(item)
                    unlock = getattr(native, "unlockPoster", None)
                    if callable(unlock):
                        unlock()
                    chosen.select()
                    return True
            except Exception as ex:
                if self.logger:
                    self.logger.debug(f"Plex could not select the recorded poster for {item.title}: {ex}")
        return self.reset_poster(item)

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
