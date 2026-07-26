"""TheTVDB v4 — a second image source for the maker card / artwork finder browsers.

Deliberately shaped to mirror the TMDB image path in api/maker_tools.py so both sources can
feed the same gallery, the same PSD roles, and the same {source, ref} candidates:
  - artwork is categorised into poster / background / logo only (TVDB's banner, icon and
    clearart types are fetched but dropped, since they don't map onto those roles)
  - "textless" is TVDB's ``includesText: false``, which stands in for TMDB's ``language: null``

Two things are cached process-wide because TVDB asks for it: the bearer token (valid one
month) and /artwork/types (type ids are opaque and only resolvable through that endpoint).
"""
import threading
import time
from typing import Any, Optional

import requests

from core.logging import LogTags, log_info
from core.rate_limiter import TokenBucket
from models.setting import get_setting

TVDB_API = "https://api4.thetvdb.com/v4"
TVDB_ARTWORK_HOST = "artworks.thetvdb.com"

# TVDB publishes no rate limit; this is only ever driven by a user clicking a gallery tab,
# so a polite ceiling costs nothing.
tvdb_bucket = TokenBucket(10.0, 5)

# Tokens are valid for a month. Refresh daily anyway — a login is one cheap call and it keeps
# us clear of any expiry/clock drift.
_TOKEN_TTL = 24 * 60 * 60
_TYPES_TTL = 24 * 60 * 60

_lock = threading.Lock()
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}   # (key, pin) -> (token, expires_at)
_types_cache: tuple[dict[int, str], float] | None = None      # (type id -> category, expires_at)


class TvdbError(Exception):
    """Any TVDB failure worth surfacing to the user. The API layer maps this to an HTTP error."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ credentials

def get_tvdb_credentials(db) -> tuple[str, str]:
    """(api_key, pin) from Settings → General → API Keys. Pin is blank for company keys."""
    key = get_setting(db, "tvdb_api_key")
    pin = get_setting(db, "tvdb_pin")
    return (
        str(key.value or "").strip() if key else "",
        str(pin.value or "").strip() if pin else "",
    )


# ------------------------------------------------------------------ auth + transport

def _login(api_key: str, pin: str) -> str:
    payload: dict[str, str] = {"apikey": api_key}
    if pin:
        payload["pin"] = pin
    tvdb_bucket.acquire()
    try:
        resp = requests.post(f"{TVDB_API}/login", json=payload, timeout=30)
    except Exception as exc:
        raise TvdbError(f"Could not reach TheTVDB: {exc}")
    if resp.status_code == 401:
        raise TvdbError("TheTVDB rejected the API key (and PIN, if this is a subscriber key).", status=401)
    if resp.status_code != 200:
        raise TvdbError(f"TheTVDB login failed (HTTP {resp.status_code}).")
    try:
        token = str((resp.json().get("data") or {}).get("token") or "")
    except Exception:
        token = ""
    if not token:
        raise TvdbError("TheTVDB login returned no token.")
    log_info(LogTags.API, "TheTVDB login succeeded — bearer token cached")
    return token


def _bearer(api_key: str, pin: str, *, force: bool = False) -> str:
    cache_key = (api_key, pin)
    now = time.monotonic()
    if not force:
        with _lock:
            hit = _token_cache.get(cache_key)
            if hit and hit[1] > now:
                return hit[0]
    token = _login(api_key, pin)
    with _lock:
        _token_cache[cache_key] = (token, time.monotonic() + _TOKEN_TTL)
    return token


def _get(path: str, api_key: str, pin: str, params: Optional[dict] = None, *, what: str = "data") -> Any:
    """GET a v4 endpoint and return its ``data`` payload. Retries once on a 401 (stale token)."""
    for attempt in (0, 1):
        token = _bearer(api_key, pin, force=attempt == 1)
        tvdb_bucket.acquire()
        try:
            resp = requests.get(
                f"{TVDB_API}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
                timeout=30,
            )
        except Exception as exc:
            raise TvdbError(f"Could not reach TheTVDB while fetching {what}: {exc}")
        if resp.status_code == 401 and attempt == 0:
            continue  # token went stale early — re-login and try once more
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise TvdbError(f"TheTVDB {what} lookup failed (HTTP {resp.status_code}).")
        try:
            return resp.json().get("data")
        except Exception:
            raise TvdbError(f"TheTVDB returned an unreadable {what} response.")
    return None


# ------------------------------------------------------------------ artwork types

# TVDB type ids are opaque, so we resolve them through /artwork/types and match on the
# type's own slug/name. Only the three that map onto our poster/background/logo roles are
# kept; banner, icon, clearart and the person/episode types fall through to "".
def _categorize(slug: str, name: str) -> str:
    token = (slug or name or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if not token:
        return ""
    if "clearlogo" in token or token in ("logo", "logos"):
        return "logo"
    if "background" in token or "fanart" in token:
        return "background"
    if "poster" in token:
        return "poster"
    return ""


def artwork_types(api_key: str, pin: str) -> dict[int, tuple[str, str]]:
    """type id -> (category, record_type), where category is 'poster' | 'background' | 'logo'.

    Ids that map to none of those three (banner, icon, clearart, …) are omitted. The record type
    rides along because ids are scoped per record — a season poster and a series poster are
    different ids, and only the caller knows which it asked for.
    """
    global _types_cache
    now = time.monotonic()
    with _lock:
        if _types_cache and _types_cache[1] > now:
            return _types_cache[0]

    rows = _get("/artwork/types", api_key, pin, what="artwork types") or []
    mapping: dict[int, tuple[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            type_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        category = _categorize(str(row.get("slug") or ""), str(row.get("name") or ""))
        if category:
            mapping[type_id] = (category, str(row.get("recordType") or "").strip().lower())

    if not mapping:
        raise TvdbError("TheTVDB returned no usable artwork types.")
    log_info(LogTags.API, f"TheTVDB artwork types cached — {len(mapping)} poster/background/logo ids")
    with _lock:
        _types_cache = (mapping, time.monotonic() + _TYPES_TTL)
    return mapping


# Record types that are never the title's own artwork. This filter earns its keep: TVDB returns
# season artwork inline from /series/{id}/artworks, so Breaking Bad's poster tab would show 199
# entries (154 of them per-season) instead of its own 45. The 'list' record also has a poster
# type. Matched as substrings and applied as an exclusion (not an allowlist) on purpose: an
# unrecognised record type still shows up, which beats silently emptying the gallery if TVDB
# renames one. Live record types as of Jul 2026: series, season, episode, actor, movie, company,
# award, list.
_NON_TITLE_RECORD_TYPES = ("season", "episode", "actor", "people", "person", "award", "list",
                           "company", "network")


# ------------------------------------------------------------------ id resolution

def resolve_tvdb_id(*, media_type: str, tvdb_id: Optional[int], imdb_id: Optional[str],
                    api_key: str, pin: str) -> int | None:
    """The item's TVDB id. TV carries one from TMDB's external_ids; movies never do, so those
    go through the remote-id search. Collections have no TVDB entity at all."""
    if tvdb_id and int(tvdb_id) > 0:
        return int(tvdb_id)
    if str(media_type or "").lower() == "movie" and imdb_id:
        return resolve_movie_tvdb_id(str(imdb_id), api_key, pin)
    return None


def resolve_movie_tvdb_id(imdb_id: str, api_key: str, pin: str) -> int | None:
    """TMDB carries no tvdb_id for movies, so go through TVDB's remote-id search on the IMDb id."""
    imdb = str(imdb_id or "").strip()
    if not imdb:
        return None
    rows = _get(f"/search/remoteid/{imdb}", api_key, pin, what="remote id search") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        movie = row.get("movie")
        if isinstance(movie, dict) and movie.get("id"):
            try:
                return int(movie["id"])
            except (TypeError, ValueError):
                continue
    return None


# ------------------------------------------------------------------ artwork fetch

def fetch_artwork(*, tvdb_id: int, media_type: str, api_key: str, pin: str) -> list[dict]:
    """Raw ArtworkBaseRecords for a series or movie.

    Series have a dedicated /artworks route; movies don't, so their artwork rides along on
    /movies/{id}/extended. Neither is filtered server-side — one unfiltered call is cheaper
    than one per type, and TVDB's own ``lang`` filter would drop the textless entries.
    """
    if media_type == "tv":
        data = _get(f"/series/{tvdb_id}/artworks", api_key, pin, what="series artwork")
        records = (data or {}).get("artworks") or []
    else:
        # No short=true here — that flag is exactly what strips artworks off the record.
        data = _get(f"/movies/{tvdb_id}/extended", api_key, pin, what="movie artwork")
        records = (data or {}).get("artworks") or []
    return [r for r in records if isinstance(r, dict)]


def fetch_series_seasons(*, tvdb_id: int, api_key: str, pin: str) -> list[dict]:
    """A series' seasons in official (aired) order — the same ordering Sonarr uses.

    TVDB publishes several parallel season orders (official/aired, DVD, absolute, alternate).
    Only the official one lines up with what Sonarr shows, so the alternates are dropped;
    otherwise a show would appear to have several times its real number of seasons.

    Returns [{id, number, name, image, year}] sorted by season number, specials (0) first.
    """
    data = _get(f"/series/{tvdb_id}/extended", api_key, pin, params={"short": "true"},
                what="series seasons")
    seasons: list[dict] = []
    for season in ((data or {}).get("seasons") or []):
        if not isinstance(season, dict):
            continue
        stype = season.get("type")
        type_slug = str((stype or {}).get("type") or "") if isinstance(stype, dict) else ""
        if type_slug and type_slug != "official":
            continue
        try:
            number = int(season.get("number"))
            season_id = int(season.get("id"))
        except (TypeError, ValueError):
            continue
        seasons.append({
            "id": season_id,
            "number": number,
            # TVDB usually leaves season names blank; the caller supplies a display fallback.
            "name": str(season.get("name") or "").strip(),
            "image": absolute_image_url(season.get("image")),
            "year": str(season.get("year") or "").strip() or None,
        })
    seasons.sort(key=lambda s: s["number"])
    return seasons


def fetch_season_artwork(*, tvdb_id: int, season_number: int, api_key: str, pin: str) -> list[dict]:
    """Artwork for one season — needs the series' season list first to turn a number into an id."""
    seasons = fetch_series_seasons(tvdb_id=tvdb_id, api_key=api_key, pin=pin)
    season_id = next((s["id"] for s in seasons if s["number"] == int(season_number)), None)
    if season_id is None:
        return []
    extended = _get(f"/seasons/{season_id}/extended", api_key, pin, what="season artwork")
    return [r for r in ((extended or {}).get("artwork") or []) if isinstance(r, dict)]


# ------------------------------------------------------------------ normalisation

# TVDB reports 3-letter codes; the gallery's language picker is 2-letter ISO 639-1. Both the
# terminological and bibliographic variants are listed so either spelling matches.
_LANG_3_TO_2: dict[str, str] = {
    "eng": "en", "ara": "ar", "zho": "zh", "chi": "zh", "ces": "cs", "cze": "cs",
    "dan": "da", "nld": "nl", "dut": "nl", "fin": "fi", "fra": "fr", "fre": "fr",
    "deu": "de", "ger": "de", "ell": "el", "gre": "el", "heb": "he", "hin": "hi",
    "hun": "hu", "ind": "id", "ita": "it", "jpn": "ja", "kor": "ko", "nor": "no",
    "pol": "pl", "por": "pt", "ron": "ro", "rum": "ro", "rus": "ru", "slk": "sk",
    "slo": "sk", "spa": "es", "swe": "sv", "tha": "th", "tur": "tr", "ukr": "uk",
    "vie": "vi",
}


def normalize_language(raw: Any) -> str | None:
    """TVDB language code -> the 2-letter code the gallery shows, or None for textless."""
    code = str(raw or "").strip().lower()
    if not code:
        return None
    return _LANG_3_TO_2.get(code, code)


def absolute_image_url(raw: Any) -> str:
    """TVDB image refs are normally absolute; fall back to the artwork host when they aren't."""
    url = str(raw or "").strip()
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://{TVDB_ARTWORK_HOST}/{url.lstrip('/')}"


def is_tvdb_image_url(url: str) -> bool:
    """Guard for the image proxy — only TVDB's own artwork host may be fetched."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == TVDB_ARTWORK_HOST or host.endswith(".thetvdb.com"))


def group_artwork(records: list[dict], types: dict[int, tuple[str, str]], language: str,
                  *, title_only: bool = True) -> dict[str, list[dict]]:
    """Bucket raw records into {'posters', 'backdrops', 'logos'}, honouring the language filter.

    Language values match the TMDB endpoint's: 'all', 'en+textless', or a bare code. An image is
    textless when TVDB says ``includesText: false`` or carries no language at all.

    ``title_only`` drops season/episode/people artwork, so a series' own poster tab doesn't fill
    up with per-season posters. Pass False when the records are already scoped (season artwork).
    """
    buckets: dict[str, list[dict]] = {"posters": [], "backdrops": [], "logos": []}
    bucket_of = {"poster": "posters", "background": "backdrops", "logo": "logos"}

    for record in records:
        try:
            entry = types.get(int(record.get("type")))
        except (TypeError, ValueError):
            continue
        if not entry:
            continue
        category, record_type = entry
        if title_only and any(t in record_type for t in _NON_TITLE_RECORD_TYPES):
            continue

        image_url = absolute_image_url(record.get("image"))
        if not image_url:
            continue

        lang = normalize_language(record.get("language"))
        textless = record.get("includesText") is False or lang is None
        if textless:
            lang = None

        if language == "all":
            pass
        elif language == "en+textless":
            if lang is not None and lang != "en":
                continue
        elif lang != language:
            continue

        thumb = absolute_image_url(record.get("thumbnail")) or image_url
        try:
            score = float(record.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0

        buckets[bucket_of[category]].append({
            "file_path": image_url,
            "width": int(record.get("width") or 0),
            "height": int(record.get("height") or 0),
            "language": lang,
            "vote_average": score,
            "url_thumb": thumb,
            "url_full": image_url,
        })

    # Same ordering as the TMDB gallery: textless first, then best-scored.
    for group in buckets.values():
        group.sort(key=lambda x: (0 if x["language"] is None else 1, -x["vote_average"]))
    return buckets
