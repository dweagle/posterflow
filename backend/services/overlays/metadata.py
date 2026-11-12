"""Item metadata for overlays (airing status, dates), fetched from TMDB and cached on disk.

Freshness rules — a run should reflect TMDB as of that run, so:
- "Live" records (a show still returning/upcoming, a recent or future movie) are re-fetched
  once per run, at most every LIVE_REFRESH_FLOOR_SECONDS. One request per live title per
  run, never one per poster: the cache dedupes within the run.
- Settled records (ended/canceled shows, older movies) are reused for SETTLED_TTL_SECONDS.
- Id mappings (tvdb/imdb → tmdb id, via /find) never change; kept FIND_TTL_SECONDS.
- A rebuild (begin_run(refresh=True)) ignores everything cached before the run: every title
  is asked again once, and the run then reuses its own answers as usual.
The cache lives in the overlay_metadata_cache table.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple, Set

import requests
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models.overlay import OverlayMetadataCache

from core.logging import LogTags, log_info, log_warning
from util.data.normalization import normalize_titles

from .base import ItemContext, MEDIA_MOVIE, MEDIA_SHOW

TMDB_BASE = "https://api.themoviedb.org/3"
SETTLED_TTL_SECONDS = 7 * 86400
LIVE_REFRESH_FLOOR_SECONDS = 5 * 60
FIND_TTL_SECONDS = 30 * 86400
_MAX_FAILURES_BEFORE_PAUSE = 5
_MIN_REQUEST_INTERVAL = 0.04  # ~25 req/s, under TMDB's limit
_LIVE_STATUSES = ("returning series", "in production", "planned", "pilot")
_MOVIE_LIVE_WINDOW_DAYS = 60


def describe_record(media: str, data: Optional[Dict[str, Any]]) -> str:
    """Terse summary of a trimmed TMDB record for debug lines: status plus the dates the
    rules look at, e.g. 'Returning · first 2020-01-01 · last 2026-09-04 · next 2026-09-11'."""
    if not data:
        return "no record"
    status = str(data.get("status") or "unknown").replace(" Series", "")
    if media == MEDIA_SHOW:
        dates = [
            ("first", data.get("first_air_date")),
            ("last", (data.get("last_episode_to_air") or {}).get("air_date") or data.get("last_air_date")),
            ("next", (data.get("next_episode_to_air") or {}).get("air_date")),
        ]
        return " · ".join([status] + [f"{k} {v}" for k, v in dates if v])
    return f"{status} {data.get('release_date')}" if data.get("release_date") else status


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"

_SHOW_FIELDS = ("status", "first_air_date", "last_air_date", "in_production", "name", "vote_average", "vote_count")
_MOVIE_FIELDS = ("status", "release_date", "title", "vote_average", "vote_count")
_PROVIDER_BUCKETS = ("flatrate", "free", "ads", "rent", "buy")  # TMDB's watch-provider offer kinds


def _trim_providers(raw: Any) -> Dict[str, Dict[str, list]]:
    """TMDB's appended watch/providers → {region: {offer kind: [provider ids]}}, nothing else."""
    results = raw.get("results") if isinstance(raw, dict) else None
    out: Dict[str, Dict[str, list]] = {}
    for region, offers in (results or {}).items():
        if not isinstance(offers, dict):
            continue
        kinds = {
            bucket: sorted({int(p["provider_id"]) for p in offers.get(bucket) or [] if isinstance(p, dict) and p.get("provider_id") is not None})
            for bucket in _PROVIDER_BUCKETS
        }
        kinds = {k: v for k, v in kinds.items() if v}
        if kinds:
            out[str(region).upper()] = kinds
    return out


_KEY_IN_URL = re.compile(r"api_key=[^&\s]+")


def scrubbed(exc: Any) -> str:
    """An error's text with the TMDB key blanked: requests puts the whole request URL in it."""
    return _KEY_IN_URL.sub("api_key=***", str(exc))


class MetadataProvider:
    """Resolves the TMDB record behind an ItemContext (by tmdb id, else tvdb/imdb via /find)."""

    def __init__(self, api_key: str, *, now: Optional[float] = None, db: Optional[Session] = None,
                 finales: Optional[Any] = None, providers: bool = False) -> None:
        self.api_key = (api_key or "").strip()
        # Streaming badges need each record's watch providers: fetched with the details (one
        # request) and kept trimmed; a cached record without them is refetched once.
        self.want_providers = bool(providers)
        self._lock = threading.Lock()
        self._db = db  # None = this run's answers are kept in memory only (previews, tests)
        self._finales = finales  # FinaleLookup (Sonarr → TheTVDB); None = TMDB inference only
        self._cache: Dict[str, Any] = {}
        self._dirty: Set[str] = set()
        self._failures = 0
        self._paused = False
        self._warned_no_key = False
        self._now = now
        self._session = requests.Session()
        self._last_request = 0.0
        self.run_started = self._clock()
        self.refresh_all = False  # a rebuild: nothing cached before this run is trusted
        self.stats = {"requests": 0, "refreshed": 0, "cached": 0}
        self._load()

    def begin_run(self, *, refresh: bool = False) -> None:
        """Mark the start of a run. With `refresh`, every record cached before it is fetched again."""
        self.run_started = self._clock()
        self.refresh_all = refresh

    @property
    def available(self) -> bool:
        return bool(self.api_key) and not self._paused

    def _clock(self) -> float:
        return self._now if self._now is not None else time.time()

    def _load(self) -> None:
        if self._db is None:
            return
        try:
            for row in self._db.query(OverlayMetadataCache):
                try:
                    self._cache[row.key] = json.loads(row.value)
                except (json.JSONDecodeError, TypeError):
                    continue
        except SQLAlchemyError as exc:
            self._db.rollback()
            log_warning(LogTags.OVERLAYS, f"Ignoring unreadable overlay metadata cache: {exc}")

    def flush(self) -> None:
        if self.stats["requests"] or self.stats["cached"]:
            log_info(
                LogTags.OVERLAYS,
                f"TMDB: {self.stats['requests']} request(s) this run "
                f"({self.stats['refreshed']} refreshed, {self.stats['cached']} served from cache)",
            )
        with self._lock:
            if not self._dirty or self._db is None:
                self._dirty = set()
                return
            try:
                for key in self._dirty:
                    value = self._cache.get(key)
                    if not isinstance(value, dict):
                        continue
                    row = self._db.get(OverlayMetadataCache, key)
                    if row is None:
                        row = OverlayMetadataCache(key=key)
                        self._db.add(row)
                    row.value = json.dumps(value)
                    row.fetched_at = float(value.get("fetched_at") or 0) or None
                self._db.commit()
                self._dirty = set()
            except SQLAlchemyError as exc:
                self._db.rollback()
                log_warning(LogTags.OVERLAYS, f"Could not write the overlay metadata cache: {exc}")

    def _fresh(self, key: str, ttl: float) -> Optional[Dict[str, Any]]:
        entry = self._cache.get(key)
        if not isinstance(entry, dict):
            return None
        fetched = float(entry.get("fetched_at") or 0)
        if self.refresh_all and fetched < self.run_started:
            return None
        if self._clock() - fetched > ttl:
            return None
        return entry

    def _store(self, key: str, value: Dict[str, Any]) -> None:
        value["fetched_at"] = self._clock()
        self._cache[key] = value
        self._dirty.add(key)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """GET a TMDB path. None on 404; raises nothing (failures are logged and counted)."""
        if not self.api_key:
            if not self._warned_no_key:
                log_warning(LogTags.OVERLAYS, "No TMDB API key configured — status overlays need one (Settings → TMDB)")
                self._warned_no_key = True
            return None
        if self._paused:
            return None
        query = dict(params or {})
        query["api_key"] = self.api_key
        # Gentle pacing so a run over hundreds of live shows stays under TMDB's rate limit.
        wait = _MIN_REQUEST_INTERVAL - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()
        self.stats["requests"] += 1
        try:
            resp = self._session.get(f"{TMDB_BASE}{path}", params=query, timeout=15)
            if resp.status_code == 404:
                self._failures = 0
                return {"_missing": True}
            resp.raise_for_status()
            self._failures = 0
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            self._failures += 1
            log_warning(LogTags.OVERLAYS, f"TMDB request failed ({path}): {scrubbed(exc)}")
            if self._failures >= _MAX_FAILURES_BEFORE_PAUSE:
                self._paused = True
                log_warning(LogTags.OVERLAYS, "TMDB unreachable — skipping metadata lookups for the rest of this run")
            return None

    def _find(self, source: str, value: str) -> Optional[Dict[str, Any]]:
        """/find results for an external id, as {"show": tmdb_id|None, "movie": tmdb_id|None}."""
        key = f"find:{source}:{value}"
        entry = self._fresh(key, FIND_TTL_SECONDS)
        if entry is not None and "show" in entry and "movie" in entry:
            return entry
        data = self._get(f"/find/{value}", {"external_source": f"{source}_id"})
        if data is None:
            return None

        def first_id(bucket: str) -> Optional[int]:
            results = data.get(bucket) or []
            return results[0].get("id") if results and isinstance(results[0], dict) else None

        entry = {MEDIA_SHOW: first_id("tv_results"), MEDIA_MOVIE: first_id("movie_results")}
        self._store(key, dict(entry))
        return entry

    def _find_tmdb_id(self, source: str, value: str, media: str) -> Optional[int]:
        entry = self._find(source, value)
        return entry.get(media) if entry else None

    def _matches(self, ctx: ItemContext, media: str, details: Optional[Dict[str, Any]]) -> bool:
        """Does a TMDB record look like this item (title, and year when both are known)?"""
        if not details:
            return False
        name = details.get("name") if media == MEDIA_SHOW else details.get("title")
        if normalize_titles(str(name or "")) != normalize_titles(ctx.title or ""):
            return False
        raw_year = details.get("first_air_date") if media == MEDIA_SHOW else details.get("release_date")
        year = int(raw_year[:4]) if isinstance(raw_year, str) and raw_year[:4].isdigit() else None
        return ctx.year is None or year is None or abs(ctx.year - year) <= 1

    def _search(self, media: str, ctx: ItemContext) -> Optional[int]:
        """TMDB id for an id-less item by exact (normalized) title + year, or None."""
        if not ctx.title:
            return None
        key = f"search:{media}:{normalize_titles(ctx.title)}:{ctx.year or ''}"
        entry = self._fresh(key, FIND_TTL_SECONDS)
        if entry is not None:
            return entry.get("tmdb_id")
        params: Dict[str, Any] = {"query": ctx.title}
        if ctx.year:
            params["first_air_date_year" if media == MEDIA_SHOW else "year"] = ctx.year
        data = self._get(f"/search/{'tv' if media == MEDIA_SHOW else 'movie'}", params)
        if data is None:
            return None
        tmdb_id = None
        for hit in data.get("results") or []:
            if isinstance(hit, dict) and self._matches(ctx, media, hit):
                tmdb_id = hit.get("id")
                break
        self._store(key, {"tmdb_id": tmdb_id})
        return tmdb_id

    @staticmethod
    def _is_live(media: str, data: Optional[Dict[str, Any]]) -> bool:
        """Can this record's badge still change? Ended/canceled shows and old movies can't."""
        if not data:
            return False
        if media == MEDIA_SHOW:
            status = str(data.get("status") or "").strip().lower()
            return status in _LIVE_STATUSES or bool(data.get("next_episode_to_air")) or status == ""
        raw = data.get("release_date")
        if not isinstance(raw, str) or len(raw) < 10:
            return True
        try:
            released = time.mktime(time.strptime(raw[:10], "%Y-%m-%d"))
        except (ValueError, OverflowError):
            return True
        return time.time() - released < _MOVIE_LIVE_WINDOW_DAYS * 86400

    def _usable(self, media: str, entry: Optional[Dict[str, Any]]) -> bool:
        """A cached details entry serves this run when it is settled and within its TTL, or
        live but already fetched during this run (or within the refresh floor)."""
        if not isinstance(entry, dict):
            return False
        fetched = float(entry.get("fetched_at") or 0)
        age = self._clock() - fetched
        if self.refresh_all and fetched < self.run_started:
            return False
        if entry.get("missing"):
            return age <= SETTLED_TTL_SECONDS
        if self.want_providers and isinstance(entry.get("data"), dict) and "providers" not in entry["data"]:
            return False
        if not self._is_live(media, entry.get("data")):
            return age <= SETTLED_TTL_SECONDS
        return fetched >= self.run_started or age <= LIVE_REFRESH_FLOOR_SECONDS

    def _details(self, media: str, tmdb_id: int) -> Tuple[Optional[Dict[str, Any]], str]:
        """(trimmed record or None, how it was obtained — for the caller's debug line)."""
        key = f"{'tv' if media == MEDIA_SHOW else 'movie'}:{tmdb_id}"
        entry = self._cache.get(key)
        if self._usable(media, entry):
            self.stats["cached"] += 1
            age = _age(self._clock() - float(entry.get("fetched_at") or 0))
            if entry.get("missing"):
                return None, f"not found ({age} ago)"
            data = entry.get("data")
            return data, f"cached {age} ago" if self._is_live(media, data) else f"cached {age} ago, settled"
        refreshing = isinstance(entry, dict) and entry.get("data")
        if refreshing:
            self.stats["refreshed"] += 1
        data = self._get(f"/{'tv' if media == MEDIA_SHOW else 'movie'}/{tmdb_id}",
                         {"append_to_response": "watch/providers"} if self.want_providers else None)
        if data is None:
            stale = self._cache.get(key)  # better a stale answer than none during an outage
            if isinstance(stale, dict) and stale.get("data"):
                return stale.get("data"), "unreachable, using the stale cached record"
            return None, "unreachable"
        if data.get("_missing"):
            self._store(key, {"missing": True})
            return None, "not on TMDB (404)"
        trimmed = self._trim(media, data, providers=self.want_providers)
        self._store(key, {"data": trimmed})
        return trimmed, "refreshed" if refreshing else "fetched"

    @staticmethod
    def _trim(media: str, data: Dict[str, Any], providers: bool = False) -> Dict[str, Any]:
        fields = _SHOW_FIELDS if media == MEDIA_SHOW else _MOVIE_FIELDS
        out = {k: data.get(k) for k in fields}
        if providers:
            out["providers"] = _trim_providers(data.get("watch/providers"))
        if media == MEDIA_SHOW:
            for ep_key in ("last_episode_to_air", "next_episode_to_air"):
                ep = data.get(ep_key)
                out[ep_key] = (
                    {"air_date": ep.get("air_date"), "season_number": ep.get("season_number"),
                     "episode_number": ep.get("episode_number")}
                    if isinstance(ep, dict) else None
                )
            # Episode counts per season: the finale guess when no finale markers are available.
            out["season_episode_counts"] = {
                str(s["season_number"]): int(s.get("episode_count") or 0)
                for s in (data.get("seasons") or []) if isinstance(s, dict) and (s.get("season_number") or 0) > 0
            }
        return out

    def _finale_record(self, ctx: ItemContext) -> Optional[Dict[str, Any]]:
        """Finale markers for a live show, from the lookup's first source that has it; cached
        with the live refresh cadence (once per run, at most every few minutes)."""
        if self._finales is None or (ctx.tvdb_id is None and ctx.tmdb_id is None):
            return None
        key = f"finales:{ctx.tvdb_id or ''}:{ctx.tmdb_id or ''}"
        entry = self._cache.get(key)
        if isinstance(entry, dict):
            fetched = float(entry.get("fetched_at") or 0)
            if fetched >= self.run_started or (not self.refresh_all and self._clock() - fetched <= LIVE_REFRESH_FLOOR_SECONDS):
                return entry.get("data")
        try:
            record = self._finales.finales(tvdb_id=ctx.tvdb_id, tmdb_id=ctx.tmdb_id)
        except Exception as exc:
            log_warning(LogTags.OVERLAYS, f"Finale lookup failed for {ctx.title}: {exc}")
            record = None
        self._store(key, {"data": record})
        return record

    def for_item(self, ctx: ItemContext) -> Dict[str, Any]:
        """Metadata for one item: {"tmdb": {...trimmed details...}, "lookup": "<how>"}, or
        {} when unknown. The TMDB id comes from the item's ids (tvdb/imdb are translated via
        /find); an item with no ids at all is searched by exact title + year. `lookup` says
        where the record came from, for the engine's per-item debug line."""
        if ctx.media_type not in (MEDIA_MOVIE, MEDIA_SHOW):
            return {}
        with self._lock:
            media = ctx.media_type
            tmdb_id = ctx.tmdb_id
            via = ""
            if tmdb_id is None and ctx.tvdb_id is not None and media == MEDIA_SHOW:
                tmdb_id = self._find_tmdb_id("tvdb", str(ctx.tvdb_id), MEDIA_SHOW)
                via = f" (tvdb {ctx.tvdb_id} → tmdb {tmdb_id})" if tmdb_id else ""
            if tmdb_id is None and ctx.imdb_id:
                tmdb_id = self._find_tmdb_id("imdb", ctx.imdb_id, media)
                via = f" (imdb {ctx.imdb_id} → tmdb {tmdb_id})" if tmdb_id else ""
            if tmdb_id is None and ctx.tvdb_id is None and not ctx.imdb_id and self.available:
                tmdb_id = self._search(media, ctx)
                via = f" (title search → tmdb {tmdb_id})" if tmdb_id else ""
            if tmdb_id is None:
                return {}
            details, how = self._details(media, int(tmdb_id))
            if not details:
                return {"lookup": how + via}
            meta: Dict[str, Any] = {"tmdb": details, "lookup": how + via}
            if media == MEDIA_SHOW and self._is_live(media, details):
                finales = self._finale_record(ctx)
                if finales:
                    meta["finales"] = finales
                    meta["lookup"] += f"; finales via {finales.get('source')}"
            return meta
