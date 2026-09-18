"""Title search over the library the poster renamer places for — the same media list it
fetches from the configured sources (Plex/Jellyfin or Radarr/Sonarr, plus manual entries).
The fetch takes seconds on a big library, so it is cached briefly; searches after the first
are instant."""
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from unidecode import unidecode

from core.logging import LogTags
from services.poster_renamer import MediaDict, PosterRenameService

CACHE_TTL_SECONDS = 300
TYPE_MAP = {"movies": "movie", "series": "show", "collections": "collection"}

# Bits of a drive file name that never appear in a library title: id tags, the artwork
# subtype suffix, a season suffix, and the year (kept aside to rank by, never required).
_ID_TAG_RE = re.compile(r"\{[^}]*\}")
_SUBTYPE_SUFFIX_RE = re.compile(r"\s*-\s*(?:logo|background|squareart)s?\s*$", re.IGNORECASE)
_SEASON_SUFFIX_RE = re.compile(r"(?:\s*-\s*Season\s*\d+|_Season\d{1,4}|\s*-\s*Specials|_Specials)\s*$", re.IGNORECASE)
_TRAILING_YEAR_RE = re.compile(r"\(?\b(\d{4})\)?\s*$")

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "media": None}


def clear_cache() -> None:
    with _lock:
        _cache.update(at=0.0, media=None)


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unidecode(str(text or "")).lower()).strip()


def cached_media(db, refresh: bool = False) -> MediaDict:
    with _lock:
        fresh = _cache["media"] is not None and time.monotonic() - _cache["at"] < CACHE_TTL_SECONDS
        if fresh and not refresh:
            return _cache["media"]
        media = PosterRenameService(db).get_media_from_instances(
            log_tag=LogTags.API, setting_key="poster_renamer_libraries"
        )
        _cache.update(at=time.monotonic(), media=media)
        return media


def _item(media: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    seasons: List[int] = []
    if media_type == "show":
        seasons = sorted({
            int(s["season_number"]) for s in (media.get("seasons") or [])
            if isinstance(s, dict) and s.get("season_number") is not None
        })
    return {
        "media_type": media_type,
        "title": media.get("title") or "",
        "year": media.get("year"),
        # Series/collections keep their TMDB id off the matcher as tmdb_id_ref; overrides key on it.
        "tmdb_id": media.get("tmdb_id") or media.get("tmdb_id_ref"),
        "tvdb_id": media.get("tvdb_id"),
        "imdb_id": media.get("imdb_id"),
        "seasons": seasons,
        "source": media.get("instance") or media.get("source") or "",
        "poster_url": media.get("poster_url"),
        "thumb_url": media.get("thumb_url"),
    }


def parse_query(query: str) -> Tuple[List[str], Optional[int]]:
    """(title words, year) from a free-form query or a pasted drive file name such as
    "Show (2008) {tmdb-1396} - background"."""
    text = _ID_TAG_RE.sub("", query or "")
    text = _SUBTYPE_SUFFIX_RE.sub("", text)
    text = _SEASON_SUFFIX_RE.sub("", text)
    year = None
    m = _TRAILING_YEAR_RE.search(text)
    if m and 1880 <= int(m.group(1)) <= 2100:
        year = int(m.group(1))
        text = text[: m.start()]
    return _norm(text).split(), year


def search_library(db, query: str, limit: int = 50, refresh: bool = False) -> Dict[str, Any]:
    """Items whose title (or an alternate title) contains every word of the query, exact
    title-starts first, then the query's year (if any) first. Id tags, artwork/season suffixes
    and the year are peeled off the query so a pasted file name still finds its show. An empty
    query just warms the cache and reports the library size."""
    media = cached_media(db, refresh)
    total = sum(len(media.get(t) or []) for t in TYPE_MAP)
    words, year = parse_query(query)
    if not words and year is None:
        return {"query": query, "count": 0, "total": total, "items": []}

    def _hits(words: List[str], year: Optional[int]) -> List[tuple]:
        hits: List[tuple] = []
        for asset_type, media_type in TYPE_MAP.items():
            for m in media.get(asset_type) or []:
                title = _norm(m.get("title"))
                names = [title] + [_norm(t) for t in (m.get("alternate_titles") or [])]
                if words and not any(all(w in name for w in words) for name in names):
                    continue
                if not words and (m.get("year") or None) != year:
                    continue
                rank = 0 if title.startswith(" ".join(words)) else 1
                year_rank = 0 if year is None or (m.get("year") or None) == year else 1
                hits.append((year_rank, rank, title, m.get("year") or 0, _item(m, media_type)))
        hits.sort(key=lambda h: (h[0], h[1], h[2], h[3]))
        return hits

    hits = _hits(words, year)
    if not hits and not words and year is not None:
        hits = _hits([str(year)], None)  # a bare "1917" is more likely a title than a year
    items = [h[4] for h in hits[:limit]]
    return {"query": query, "count": len(items), "total": total, "items": items}
