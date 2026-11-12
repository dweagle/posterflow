from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Image kinds (provider-agnostic); map to native upload methods per server
IMAGE_KIND_POSTER = "poster"
IMAGE_KIND_LOGO = "logo"
IMAGE_KIND_BACKGROUND = "background"
IMAGE_KIND_SQUAREART = "squareart"

# Optional capabilities a server may support
CAP_SQUAREART = "squareart"
CAP_EDITIONS = "editions"
CAP_LABELS = "labels"
CAP_SMART_COLLECTIONS = "smart_collections"
CAP_PER_LIBRARY_COLLECTIONS = "per_library_collections"


@dataclass
class MediaServerInfo:
    server_id: str
    name: str
    version: str


@dataclass
class MediaServerLibrary:
    key: str
    title: str
    type: str  # "movie" | "show" | other native types
    locations: List[str] = field(default_factory=list)  # library root folders (flat-layout detection)


@dataclass
class MediaServerItem:
    item_id: str
    item_type: str  # "movie" | "show" | "season" | "collection"
    title: str
    year: Optional[int] = None
    provider_ids: Dict[str, str] = field(default_factory=dict)  # keys: tmdb, tvdb, imdb
    library_key: Optional[str] = None  # "<server_id>:<library_id>"
    library_name: Optional[str] = None
    index: Optional[int] = None  # season number
    parent_title: Optional[str] = None
    parent_id: Optional[str] = None
    edition_title: Optional[str] = None
    paths: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    added_at: Optional[str] = None
    release_date: Optional[str] = None
    child_count: Optional[int] = None  # episode count for seasons; None = unknown
    smart: bool = False  # Plex smart collections; always False elsewhere
    thumb_path: Optional[str] = None  # server-relative image path for proxying
    thumb_locked: Optional[bool] = None  # Plex: the poster field is locked (a chosen or uploaded poster); None = unknown
    original_title: Optional[str] = None  # the agent's original title (Plex 'Seven' vs original 'Se7en')
    virtual: bool = False  # Jellyfin placeholder for unaired/missing episodes; nothing on disk
    ratings: Dict[str, float] = field(default_factory=dict)  # audience / critic / user, 0-10 (whatever the server holds)
    guid: Optional[str] = None  # Plex guid as the server reports it (plex://… or a legacy agent string); bundle folders hash it
    parent_guid: Optional[str] = None  # seasons: the show's guid
    content_rating: Optional[str] = None  # the age rating as the server shows it (R, TV-14, gb/15); None = unrated
    native: Any = field(default=None, repr=False)
    client: Any = field(default=None, repr=False)  # owning MediaServerClient (multi-instance indexes)


def provider_ids_match(wanted: Dict[str, str], item_ids: Dict[str, str]) -> bool:
    """True when any shared provider id agrees (mirrors Plex one-guid-at-a-time search)."""
    for key, value in wanted.items():
        if not value:
            continue
        item_value = item_ids.get(key)
        if item_value and str(item_value) == str(value):
            return True
    return False


# Id-less items named per library warning at warning level; the rest go to debug.
IDLESS_DETAIL_LIMIT = 15


def idless_item_label(item: MediaServerItem) -> str:
    """'Title (Year) — why', read from the guid the server reports, for an item with no provider id."""
    name = f"{item.title} ({item.year})" if item.year else str(item.title)
    guid = str(item.guid or "").strip()
    if guid.startswith("local://"):
        return f"{name} — not matched in Plex ({guid})"
    if guid.startswith("plex://"):
        return f"{name} — matched in Plex without a TMDB/TVDB/IMDb id ({guid})"
    if guid:
        return f"{name} — legacy agent guid {guid}"
    return name
