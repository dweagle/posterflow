"""fanart.tv v3 — a third image source for the artwork finder and the maker card, beside TMDB
and TheTVDB.

fanart.tv has no search: artwork is looked up by id (TMDB or IMDb for movies, TheTVDB for
series), which the finder's items already carry. Only the types that map onto the app's roles
are kept — clear logos (HD first), backgrounds (4K first), posters, season posters and square
art; thumbs, clearart, discs, banners and character art are dropped, the way TVDB's extra
types are.
"""
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from core.rate_limiter import TokenBucket
from models.setting import get_setting

FANART_API = "https://webservice.fanart.tv/v3"
FANART_ASSET_HOST = "assets.fanart.tv"

# Posterflow's own fanart.tv project key. fanart.tv expects one on every call, with the user's
# personal key as an add-on (client_key). Until one is registered the personal key fills both
# slots, which the API accepts.
PROJECT_KEY = ""

# fanart.tv publishes no rate limit; this only runs when a user opens a card's fanart.tv tab.
fanart_bucket = TokenBucket(5.0, 5)
_TIMEOUT = 15

# One record per title covers every role and every season, so keep it for as long as fanart.tv
# itself says its answer is fresh (cache-control: max-age=600) — a season picker then costs one
# lookup, not one per click.
_RECORD_TTL = 10 * 60
_record_cache: dict[str, tuple[Optional[dict], float]] = {}
_lock = threading.Lock()

# fanart.tv enforces one exact size per type and its records carry no dimensions.
TYPE_DIMS: dict[str, tuple[int, int]] = {
    "hdmovielogo": (800, 310), "movielogo": (400, 155),
    "movie4kbackground": (3840, 2160), "moviebackground": (1920, 1080),
    "movieposter": (1000, 1426), "moviesquare": (1000, 1000),
    "hdtvlogo": (800, 310), "clearlogo": (400, 155),
    "show4kbackground": (3840, 2160), "showbackground": (1920, 1080),
    "tvposter": (1000, 1426), "seasonposter": (1000, 1426), "tvsquare": (1000, 1000),
}
# role -> fanart.tv types, in listing order (HD logos and 4K backgrounds ahead of standard ones)
MOVIE_TYPES = {"logos": ("hdmovielogo", "movielogo"),
               "backgrounds": ("movie4kbackground", "moviebackground"),
               "posters": ("movieposter",), "squareart": ("moviesquare",)}
TV_TYPES = {"logos": ("hdtvlogo", "clearlogo"),
            "backgrounds": ("show4kbackground", "showbackground"),
            "posters": ("tvposter",), "squareart": ("tvsquare",)}


class FanartError(Exception):
    """Any fanart.tv failure worth surfacing to the user. The API layer maps this to an HTTP error."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def get_fanart_key(db) -> str:
    """The user's fanart.tv API key from Settings → General → API Keys ('' when unset)."""
    setting = get_setting(db, "fanart_api_key")
    return str(setting.value or "").strip() if setting else ""


# ------------------------------------------------------------------ transport

def _params(api_key: str) -> dict[str, str]:
    return {"api_key": PROJECT_KEY or api_key, "client_key": api_key}


def _get(path: str, api_key: str, *, what: str) -> Optional[dict]:
    """One fanart.tv lookup, cached per path. None when fanart.tv has nothing for the id; raises
    on anything that isn't a clean answer."""
    with _lock:
        hit = _record_cache.get(path)
    if hit and hit[1] > time.monotonic():
        return hit[0]

    fanart_bucket.acquire()
    try:
        resp = requests.get(f"{FANART_API}{path}", params=_params(api_key), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise FanartError(f"Could not reach fanart.tv: {exc}")
    if resp.status_code == 404:
        data = None
    elif resp.status_code in (401, 403):
        raise FanartError("fanart.tv rejected the API key.", status=401)
    elif resp.status_code != 200:
        raise FanartError(f"fanart.tv {what} lookup failed (HTTP {resp.status_code}).")
    else:
        try:
            data = resp.json()
        except ValueError:
            raise FanartError(f"fanart.tv returned an unreadable {what} response.")
        # fanart.tv also reports "nothing here" as a 200 carrying an error status.
        if not isinstance(data, dict) or data.get("status") == "error":
            data = None
    with _lock:
        _record_cache[path] = (data, time.monotonic() + _RECORD_TTL)
    return data


def fetch_artwork(*, media_type: str, tmdb_id: Optional[int], imdb_id: Optional[str],
                  tvdb_id: Optional[int], api_key: str) -> dict:
    """The raw fanart.tv record for a movie (TMDB id, else IMDb id) or a series (TheTVDB id).
    {} when the item has no usable id or fanart.tv has nothing for it; collections have no
    fanart.tv entity at all."""
    if media_type == "tv":
        if not tvdb_id:
            return {}
        return _get(f"/tv/{int(tvdb_id)}", api_key, what="series") or {}
    if media_type == "movie":
        ref = tmdb_id or (str(imdb_id).strip() if imdb_id else "")
        if not ref:
            return {}
        return _get(f"/movies/{ref}", api_key, what="movie") or {}
    return {}


# ------------------------------------------------------------------ shaping

def normalize_language(raw: Any) -> Optional[str]:
    """fanart.tv tags language-neutral images '00' (backgrounds arrive blank); the finder calls
    those textless (None)."""
    lang = str(raw or "").strip().lower()
    return None if lang in ("", "00") else lang


def wanted_languages(language: Optional[str]) -> Optional[set]:
    """The languages to keep, None inside the set standing for textless, or None to keep every
    language. Accepts the gallery's choice ('all', 'en+textless', a bare code) and TMDB's
    include_image_language form ('en,null', 'de')."""
    lang = str(language or "").strip().lower()
    if lang in ("", "all"):
        return None
    if lang == "en+textless":
        return {"en", None}
    return {None if code == "null" else code for code in lang.split(",") if code}


def preview_url(url: str) -> str:
    """fanart.tv serves a small preview of every asset at the same path under /preview/."""
    return url.replace("/fanart/", "/preview/", 1)


def is_fanart_image_url(url: str) -> bool:
    """Guard for the image proxy — only fanart.tv's own asset host may be fetched."""
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == FANART_ASSET_HOST


def _shape(entry: dict, type_name: str, wanted: Optional[set]) -> Optional[dict]:
    url = str(entry.get("url") or "").strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if not is_fanart_image_url(url):
        return None
    lang = normalize_language(entry.get("lang"))
    if wanted is not None and lang not in wanted:
        return None
    try:
        likes = int(entry.get("likes") or 0)
    except (TypeError, ValueError):
        likes = 0
    width, height = TYPE_DIMS[type_name]
    # Same fields the TMDB/TVDB galleries render, so one code path shows all three sources.
    return {"file_path": url, "width": width, "height": height, "language": lang, "likes": likes,
            "vote_average": float(likes), "url_thumb": preview_url(url), "url_full": url}


def _ordered(entries: list[dict]) -> list[dict]:
    """The finder's usual order: textless first, then most liked."""
    return sorted(entries, key=lambda e: (0 if e["language"] is None else 1, -e["likes"]))


def group_artwork(record: dict, media_type: str, wanted: Optional[set] = None) -> dict[str, list[dict]]:
    """Bucket a fanart.tv record into {'logos', 'backgrounds', 'posters', 'squareart'}. Types
    are listed in role order (HD logos and 4K backgrounds first), each in the finder's usual
    order within itself."""
    types = TV_TYPES if media_type == "tv" else MOVIE_TYPES
    buckets: dict[str, list[dict]] = {"logos": [], "backgrounds": [], "posters": [], "squareart": []}
    for role, type_names in types.items():
        for type_name in type_names:
            buckets[role].extend(_ordered([
                shaped for e in (record.get(type_name) or [])
                if isinstance(e, dict) and (shaped := _shape(e, type_name, wanted))]))
    return buckets


def season_posters(record: dict, season_number: int, wanted: Optional[set] = None) -> list[dict]:
    """A series' fanart.tv posters for one season (specials are season 0)."""
    return _ordered([
        shaped for e in (record.get("seasonposter") or [])
        if isinstance(e, dict) and str(e.get("season") or "").strip() == str(season_number)
        and (shaped := _shape(e, "seasonposter", wanted))])
