import base64
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from util.media_server.client import MediaServerClient
from util.media_server.types import (
    IMAGE_KIND_BACKGROUND,
    IMAGE_KIND_LOGO,
    IMAGE_KIND_POSTER,
    MediaServerInfo,
    MediaServerItem,
    MediaServerLibrary,
    provider_ids_match,
)

logging.getLogger("requests").setLevel(logging.WARNING)

# Jellyfin ImageType per image kind; squareart has no Jellyfin equivalent
IMAGE_TYPE_MAP = {
    IMAGE_KIND_POSTER: "Primary",
    IMAGE_KIND_LOGO: "Logo",
    IMAGE_KIND_BACKGROUND: "Backdrop",
}

ITEM_KIND_MAP = {
    "Movie": "movie",
    "Series": "show",
    "Season": "season",
    "BoxSet": "collection",
}

LIBRARY_TYPE_MAP = {"movies": "movie", "tvshows": "show"}

MEDIA_TYPE_TO_ITEM_KINDS = {
    "movie": ["Movie"],
    "movies": ["Movie"],
    "show": ["Series"],
    "shows": ["Series"],
    "series": ["Series"],
    "collection": ["BoxSet"],
    "collections": ["BoxSet"],
}

CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

ITEM_FIELDS = "ProviderIds,Path,Tags,DateCreated"
PAGE_SIZE = 500


def build_auth_header(token: str, app_version: str = "") -> str:
    """Jellyfin auth header. X-Emby-Token/api_key are legacy-gated in modern servers; this form always works."""
    version = app_version or "unknown"
    return (
        f'MediaBrowser Client="Posterflow", Device="Posterflow", '
        f'DeviceId="posterflow", Version="{version}", Token="{token}"'
    )


def normalize_provider_ids(provider_ids: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Lowercase Jellyfin ProviderIds keys (Tmdb/Tvdb/Imdb) to tmdb/tvdb/imdb."""
    out: Dict[str, str] = {}
    for key, value in (provider_ids or {}).items():
        if value is None or str(value).strip() == "":
            continue
        out[str(key).strip().lower()] = str(value).strip()
    return out


class JellyfinClient(MediaServerClient):
    """Jellyfin REST client (10.9+; verified against the v12 OpenAPI spec)."""

    server_type = "jellyfin"
    capabilities = frozenset()  # no squareart/editions/labels/smart or per-library collections

    def __init__(self, url: str, api_key: str, logger: Any = None, app_version: str = "") -> None:
        super().__init__()
        self.logger = logger
        self.max_retries = 3
        self.timeout = 30
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": build_auth_header(api_key, app_version),
            }
        )

        self._libraries_cache: Optional[List[MediaServerLibrary]] = None
        self._user_id_cache: Optional[str] = None

        info = self.get_server_info()
        if not info:
            return
        self.server_id = info.server_id
        self.server_name = info.name
        self.server_version = info.version
        self.connect_status = True

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Any]:
        endpoint = f"{self.url}{path}"
        for i in range(self.max_retries):
            try:
                response = self.session.request(
                    method, endpoint, params=params, data=data, headers=headers, timeout=self.timeout
                )
                response.raise_for_status()
                if response.status_code == 204 or not response.content:
                    return True
                return response.json()
            except requests.exceptions.RequestException as ex:
                if i < self.max_retries - 1:
                    if self.logger:
                        self.logger.warning(
                            f"Jellyfin {method} {path} failed ({ex}), retrying ({i + 1}/{self.max_retries})..."
                        )
                    time.sleep(1)
                else:
                    if self.logger:
                        self.logger.error(
                            f"Jellyfin {method} {path} failed after {self.max_retries} retries: {ex}"
                        )
        return None

    def get_server_info(self) -> Optional[MediaServerInfo]:
        data = self._request("GET", "/System/Info")
        if not isinstance(data, dict):
            return None
        return MediaServerInfo(
            server_id=str(data.get("Id") or ""),
            name=str(data.get("ServerName") or ""),
            version=str(data.get("Version") or ""),
        )

    def get_libraries(self) -> List[MediaServerLibrary]:
        if self._libraries_cache is not None:
            return self._libraries_cache
        data = self._request("GET", "/Library/VirtualFolders")
        libraries: List[MediaServerLibrary] = []
        for folder in data or []:
            collection_type = str(folder.get("CollectionType") or "").lower()
            libraries.append(
                MediaServerLibrary(
                    key=str(folder.get("ItemId") or ""),
                    title=str(folder.get("Name") or ""),
                    type=LIBRARY_TYPE_MAP.get(collection_type, collection_type),
                    locations=[str(loc) for loc in (folder.get("Locations") or [])],
                )
            )
        if libraries:
            self._libraries_cache = libraries
        return libraries

    def _library_title(self, local_id: str) -> Optional[str]:
        for library in self.get_libraries():
            if library.key == local_id:
                return library.title
        return None

    def _library_selected(self, library: MediaServerLibrary, library_keys: List[str]) -> bool:
        """Whether a caller's library_keys selection names this library (global or local key)."""
        return self.global_library_key(library.key) in library_keys or library.key in library_keys

    def _items_page(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        start = 0
        while True:
            page = self._request(
                "GET",
                "/Items",
                # collapseBoxSetItems must be explicit: v12 folds box-set member movies
                # out of recursive listings by default, dropping them from the index
                params={"collapseBoxSetItems": "false", **params, "startIndex": start, "limit": PAGE_SIZE},
            )
            if not isinstance(page, dict):
                break
            batch = page.get("Items") or []
            items.extend(batch)
            start += len(batch)
            total = page.get("TotalRecordCount")
            if not batch or (isinstance(total, int) and start >= total):
                break
        return items

    def _parse_item(
        self,
        data: Dict[str, Any],
        library_key: Optional[str] = None,
        library_name: Optional[str] = None,
    ) -> MediaServerItem:
        item_id = str(data.get("Id") or "")
        thumb_path = None
        if (data.get("ImageTags") or {}).get("Primary"):
            thumb_path = f"/Items/{item_id}/Images/Primary"
        path = data.get("Path")
        return MediaServerItem(
            item_id=item_id,
            item_type=ITEM_KIND_MAP.get(str(data.get("Type") or ""), str(data.get("Type") or "").lower()),
            title=str(data.get("Name") or ""),
            year=data.get("ProductionYear"),
            provider_ids=normalize_provider_ids(data.get("ProviderIds")),
            library_key=library_key,
            library_name=library_name,
            index=data.get("IndexNumber"),
            parent_title=data.get("SeriesName"),
            parent_id=str(data.get("SeriesId") or data.get("ParentId") or "") or None,
            paths=[path] if path else [],
            tags=list(data.get("Tags") or []),
            added_at=data.get("DateCreated"),
            release_date=data.get("PremiereDate"),
            child_count=int(data["ChildCount"]) if data.get("ChildCount") is not None else None,
            thumb_path=thumb_path,
            native=data,
            client=self,
        )

    def get_library_items(
        self, library_key: str, item_types: Optional[List[str]] = None
    ) -> List[MediaServerItem]:
        local_id = self._local_library_id(library_key)
        include = item_types or ["Movie", "Series"]
        rows = self._items_page(
            {
                "parentId": local_id,
                "recursive": "true",
                "includeItemTypes": ",".join(include),
                "fields": ITEM_FIELDS,
            }
        )
        global_key = self.global_library_key(local_id)
        library_name = self._library_title(local_id)
        return [self._parse_item(row, library_key=global_key, library_name=library_name) for row in rows]

    def get_collections(self, library_key: Optional[str] = None) -> List[MediaServerItem]:
        # Jellyfin box sets are server-global; library_key is accepted for interface parity.
        # Attribute them to the Collections virtual folder so upload caching can key them.
        box_folder = next((l for l in self.get_libraries() if l.type == "boxsets"), None)
        global_key = self.global_library_key(box_folder.key if box_folder else "boxsets")
        library_name = box_folder.title if box_folder else "Collections"
        rows = self._items_page(
            {"recursive": "true", "includeItemTypes": "BoxSet", "fields": ITEM_FIELDS}
        )
        return [self._parse_item(row, library_key=global_key, library_name=library_name) for row in rows]

    def _any_user_id(self) -> Optional[str]:
        """First server user's id; box-set membership queries resolve through a user view."""
        if self._user_id_cache is None:
            users = self._request("GET", "/Users")
            if isinstance(users, list) and users:
                self._user_id_cache = str(users[0].get("Id") or "") or None
        return self._user_id_cache

    def get_collection_items(self, collection: MediaServerItem) -> List[MediaServerItem]:
        params: Dict[str, Any] = {"parentId": collection.item_id, "fields": ITEM_FIELDS}
        # Without a userId, box-set children come back empty (verified on 10.11)
        user_id = self._any_user_id()
        if user_id:
            params["userId"] = user_id
        rows = self._items_page(params)
        return [self._parse_item(row) for row in rows]

    FIELD_PARAMS = {"label": "tags", "genre": "genres"}

    def find_by_field(
        self, field: str, value: str, library_key: str, include_collections: bool = True
    ) -> List[MediaServerItem]:
        param = self.FIELD_PARAMS.get(field)
        if not param:
            return []
        local_id = self._local_library_id(library_key)
        global_key = self.global_library_key(local_id)
        rows = self._items_page(
            {
                "parentId": local_id,
                "recursive": "true",
                "includeItemTypes": "Movie,Series",
                param: value,
                "fields": ITEM_FIELDS,
            }
        )
        items = [self._parse_item(row, library_key=global_key) for row in rows]
        if include_collections:
            # Box sets are server-global, not per-library
            box_rows = self._items_page(
                {"recursive": "true", "includeItemTypes": "BoxSet", param: value, "fields": ITEM_FIELDS}
            )
            items.extend(self._parse_item(row) for row in box_rows)
        return items

    def get_seasons(self, show: MediaServerItem) -> List[MediaServerItem]:
        data = self._request("GET", f"/Shows/{show.item_id}/Seasons")
        if not isinstance(data, dict):
            return []
        seasons = []
        for row in data.get("Items") or []:
            season = self._parse_item(row, library_key=show.library_key, library_name=show.library_name)
            season.parent_title = season.parent_title or show.title
            seasons.append(season)
        return seasons

    def get_library_seasons(self, library_key: str) -> List[MediaServerItem]:
        local_id = self._local_library_id(library_key)
        rows = self._items_page(
            {
                "parentId": local_id,
                "recursive": "true",
                "includeItemTypes": "Season",
                "fields": f"{ITEM_FIELDS},ChildCount",
            }
        )
        global_key = self.global_library_key(local_id)
        library_name = self._library_title(local_id)
        return [self._parse_item(row, library_key=global_key, library_name=library_name) for row in rows]

    def get_item(self, item_id: str) -> Optional[MediaServerItem]:
        rows = self._items_page({"ids": item_id, "fields": ITEM_FIELDS})
        return self._parse_item(rows[0]) if rows else None

    def find_by_provider_ids(
        self,
        provider_ids: Dict[str, str],
        media_type: str,
        library_keys: Optional[List[str]] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[MediaServerItem]:
        wanted = {k.lower(): str(v) for k, v in (provider_ids or {}).items() if v}
        kinds = MEDIA_TYPE_TO_ITEM_KINDS.get((media_type or "").lower(), ["Movie", "Series"])

        if kinds == ["BoxSet"]:
            if not title:
                return []
            rows = self._items_page(
                {
                    "recursive": "true",
                    "includeItemTypes": "BoxSet",
                    "searchTerm": title,
                    "fields": ITEM_FIELDS,
                }
            )
            lowered = title.strip().lower()
            return [self._parse_item(r) for r in rows if str(r.get("Name") or "").strip().lower() == lowered]

        # Pass 1: title search, then match provider ids client-side
        # (Jellyfin removed anyProviderIdEquals in 10.9 — no server-side id lookup exists)
        if title and wanted:
            title_params = {
                "recursive": "true",
                "includeItemTypes": ",".join(kinds),
                "searchTerm": title,
                "fields": ITEM_FIELDS,
            }
            if library_keys:
                # Scope per library so results carry attribution (Jellyfin items lack library fields)
                results: List[MediaServerItem] = []
                for library in self.get_libraries():
                    if not self._library_selected(library, library_keys):
                        continue
                    global_key = self.global_library_key(library.key)
                    rows = self._items_page({**title_params, "parentId": library.key})
                    results.extend(
                        self._parse_item(r, library_key=global_key, library_name=library.title)
                        for r in rows
                        if provider_ids_match(wanted, normalize_provider_ids(r.get("ProviderIds")))
                    )
                if results:
                    return results
            else:
                rows = self._items_page(title_params)
                matches = [
                    r for r in rows if provider_ids_match(wanted, normalize_provider_ids(r.get("ProviderIds")))
                ]
                if matches:
                    return [self._parse_item(r) for r in matches]

        # Pass 2: walk candidate libraries filtered to items that have the id type at all
        if not wanted:
            return []
        id_filter: Dict[str, Any] = {}
        if "tmdb" in wanted:
            id_filter["hasTmdbId"] = "true"
        elif "tvdb" in wanted:
            id_filter["hasTvdbId"] = "true"
        elif "imdb" in wanted:
            id_filter["hasImdbId"] = "true"

        wanted_type = "movie" if kinds == ["Movie"] else "show"
        results: List[MediaServerItem] = []
        for library in self.get_libraries():
            if library.type != wanted_type:
                continue
            if library_keys and not self._library_selected(library, library_keys):
                continue
            global_key = self.global_library_key(library.key)
            rows = self._items_page(
                {
                    "parentId": library.key,
                    "recursive": "true",
                    "includeItemTypes": ",".join(kinds),
                    "fields": ITEM_FIELDS,
                    **id_filter,
                }
            )
            for row in rows:
                if provider_ids_match(wanted, normalize_provider_ids(row.get("ProviderIds"))):
                    results.append(self._parse_item(row, library_key=global_key, library_name=library.title))
        return results

    def find_by_title(
        self,
        title: str,
        media_type: Optional[str] = None,
        library_keys: Optional[List[str]] = None,
    ) -> List[MediaServerItem]:
        kinds = MEDIA_TYPE_TO_ITEM_KINDS.get((media_type or "").lower(), ["Movie", "Series"])
        params = {
            "recursive": "true",
            "includeItemTypes": ",".join(kinds),
            "searchTerm": title,
            "fields": ITEM_FIELDS,
        }
        if not library_keys:
            return [self._parse_item(row) for row in self._items_page(params)]
        results: List[MediaServerItem] = []
        for library in self.get_libraries():
            if not self._library_selected(library, library_keys):
                continue
            global_key = self.global_library_key(library.key)
            rows = self._items_page({**params, "parentId": library.key})
            results.extend(
                self._parse_item(row, library_key=global_key, library_name=library.title) for row in rows
            )
        return results

    def search(
        self,
        query: str,
        media_types: Optional[List[str]] = None,
        limit: int = 30,
    ) -> List[MediaServerItem]:
        kinds: List[str] = []
        for media_type in media_types or ["movie", "show", "collection"]:
            for kind in MEDIA_TYPE_TO_ITEM_KINDS.get(media_type.lower(), []):
                if kind not in kinds:
                    kinds.append(kind)
        rows = self._items_page(
            {
                "recursive": "true",
                "includeItemTypes": ",".join(kinds),
                "searchTerm": query,
                "fields": ITEM_FIELDS,
                "limit": max(1, limit),
            }
        )
        return [self._parse_item(row) for row in rows[:limit]]

    def upload_image(self, item: MediaServerItem, kind: str, filepath: str) -> bool:
        image_type = IMAGE_TYPE_MAP.get(kind)
        if not image_type:
            if self.logger:
                self.logger.warning(f"Jellyfin does not support '{kind}' images, skipping {item.title}")
            return False
        try:
            raw = Path(filepath).read_bytes()
        except OSError as ex:
            if self.logger:
                self.logger.error(f"Cannot read {filepath}: {ex}")
            return False
        content_type = CONTENT_TYPES.get(Path(filepath).suffix.lower(), "image/jpeg")
        # Body must be base64 text: the server wraps Request.Body in FromBase64Transform
        result = self._request(
            "POST",
            f"/Items/{item.item_id}/Images/{image_type}",
            data=base64.b64encode(raw),
            headers={"Content-Type": content_type},
        )
        return result is not None

    def image_url(self, item: MediaServerItem, max_width: Optional[int] = None) -> Optional[str]:
        if not item.thumb_path:
            return None
        if max_width:
            return f"{item.thumb_path}?maxWidth={max_width}"
        return item.thumb_path
