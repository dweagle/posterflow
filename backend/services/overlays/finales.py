"""Finale markers for the status overlay: which aired episode was a season, mid-season or
series finale. Sonarr v4 carries them on every episode (its Skyhook feed proxies TheTVDB,
where editors set them), TheTVDB serves the same field directly, and when neither is
available the TMDB episode counts give a season-finale guess. Tried in that order."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging import LogTags, log_debug, log_warning

Episode = Dict[str, Any]  # {"season": int, "episode": int, "air_date": "YYYY-MM-DD" | None, "finale": "season"|"midseason"|"series"|None}
_TVDB_PAGE = 500


def _norm_finale(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in ("season", "midseason", "series") else None


def summarize(episodes: List[Episode], today: date, source: str) -> Dict[str, Any]:
    """The compact record the status overlay reads: the last aired episode with its finale
    flag, and each season's finale air date. Specials (season 0) are ignored."""
    aired = [e for e in episodes if e.get("season", 0) > 0 and e.get("air_date") and str(e["air_date"])[:10] <= today.isoformat()]
    last = max(aired, key=lambda e: (str(e["air_date"])[:10], e["season"], e["episode"])) if aired else None
    seasons: Dict[str, Dict[str, Any]] = {}
    for e in episodes:
        if e.get("season", 0) > 0 and e.get("finale") in ("season", "series"):
            seasons[str(e["season"])] = {"finale_air_date": (str(e["air_date"])[:10] if e.get("air_date") else None)}
    return {
        "source": source,
        "last": {"season": last["season"], "episode": last["episode"], "air_date": str(last["air_date"])[:10], "finale": last.get("finale")} if last else None,
        "seasons": seasons,
    }


class FinaleLookup:
    """Episode lists with finale flags, from the first source that has the show."""

    def __init__(
        self,
        sonarr_instances: Optional[List[Dict[str, str]]] = None,
        tvdb_credentials: Tuple[str, str] = ("", ""),
        *,
        arr_factory: Optional[Callable[[Dict[str, str]], Any]] = None,
        tvdb_get: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._sonarr = list(sonarr_instances or [])
        self._tvdb_key, self._tvdb_pin = tvdb_credentials
        self._arr_factory = arr_factory or self._connect_sonarr
        self._tvdb_get = tvdb_get or self._tvdb_request
        self._clients: Dict[str, Any] = {}  # instance name → client or None (unreachable)
        self._series_maps: Dict[str, Dict[Tuple[str, str], int]] = {}  # instance → {("tvdb"|"tmdb", id): series id}
        self._warned: set = set()

    @classmethod
    def from_db(cls, db: Any) -> "FinaleLookup":
        from models.setting import get_setting_value
        from services.tvdb import get_tvdb_credentials

        raw = get_setting_value(db, "sonarr_instances")
        try:
            parsed = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            parsed = []
        instances = [
            {"name": str(i.get("name") or "").strip(), "url": str(i.get("url") or "").strip(), "api_key": str(i.get("api_key") or "").strip()}
            for i in (parsed if isinstance(parsed, list) else []) if isinstance(i, dict)
        ]
        return cls([i for i in instances if i["name"] and i["url"] and i["api_key"]], get_tvdb_credentials(db))

    # ── Sonarr ─────────────────────────────────────────────────────────────
    @staticmethod
    def _connect_sonarr(instance: Dict[str, str]) -> Any:
        from util.arr.client import create_arr_client

        return create_arr_client(instance["url"], instance["api_key"], "sonarr", None)

    def _client(self, instance: Dict[str, str]) -> Any:
        name = instance["name"]
        if name not in self._clients:
            try:
                self._clients[name] = self._arr_factory(instance)
            except Exception as exc:
                self._clients[name] = None
                log_warning(LogTags.OVERLAYS, f"Sonarr '{name}' unreachable for finale markers: {exc}")
            if self._clients[name] is None and name not in self._warned:
                self._warned.add(name)
                log_warning(LogTags.OVERLAYS, f"Sonarr '{name}' unreachable — finale markers fall back to TheTVDB/TMDB")
        return self._clients[name]

    def _series_map(self, instance: Dict[str, str]) -> Dict[Tuple[str, str], int]:
        name = instance["name"]
        if name not in self._series_maps:
            client = self._client(instance)
            series = client.make_get_request(f"{client.url}/api/v3/series") if client is not None else None
            table: Dict[Tuple[str, str], int] = {}
            for show in series or []:
                if not isinstance(show, dict) or not show.get("id"):
                    continue
                if show.get("tvdbId"):
                    table[("tvdb", str(show["tvdbId"]))] = int(show["id"])
                if show.get("tmdbId"):
                    table[("tmdb", str(show["tmdbId"]))] = int(show["id"])
            self._series_maps[name] = table
        return self._series_maps[name]

    def _from_sonarr(self, tvdb_id: Optional[int], tmdb_id: Optional[int]) -> Tuple[Optional[List[Episode]], str]:
        for instance in self._sonarr:
            table = self._series_map(instance)
            series_id = (table.get(("tvdb", str(tvdb_id))) if tvdb_id else None) or (table.get(("tmdb", str(tmdb_id))) if tmdb_id else None)
            if not series_id:
                continue
            client = self._client(instance)
            rows = client.make_get_request(f"{client.url}/api/v3/episode?seriesId={series_id}") if client is not None else None
            if not isinstance(rows, list):
                continue
            if not any("finaleType" in r for r in rows if isinstance(r, dict)):
                log_debug(LogTags.OVERLAYS, f"Sonarr '{instance['name']}' carries no finale markers (needs Sonarr v4)")
                continue
            episodes = [
                {"season": int(r.get("seasonNumber") or 0), "episode": int(r.get("episodeNumber") or 0),
                 "air_date": (str(r.get("airDate") or r.get("airDateUtc") or "")[:10] or None), "finale": _norm_finale(r.get("finaleType"))}
                for r in rows if isinstance(r, dict)
            ]
            return episodes, f"sonarr:{instance['name']}"
        return None, ""

    # ── TheTVDB ────────────────────────────────────────────────────────────
    def _tvdb_request(self, path: str, params: Dict[str, Any]) -> Any:
        from services.tvdb import _get

        return _get(path, self._tvdb_key, self._tvdb_pin, params, what="episode finales")

    def _from_tvdb(self, tvdb_id: Optional[int]) -> Tuple[Optional[List[Episode]], str]:
        if not self._tvdb_key or not tvdb_id:
            return None, ""
        episodes: List[Episode] = []
        try:
            for page in range(20):
                data = self._tvdb_get(f"/series/{int(tvdb_id)}/episodes/default", {"page": page})
                rows = (data or {}).get("episodes") if isinstance(data, dict) else None
                if not rows:
                    break
                episodes.extend(
                    {"season": int(r.get("seasonNumber") or 0), "episode": int(r.get("number") or 0),
                     "air_date": (str(r.get("aired") or "")[:10] or None), "finale": _norm_finale(r.get("finaleType"))}
                    for r in rows if isinstance(r, dict)
                )
                if len(rows) < _TVDB_PAGE:
                    break
        except Exception as exc:
            if "tvdb" not in self._warned:
                self._warned.add("tvdb")
                log_warning(LogTags.OVERLAYS, f"TheTVDB episode lookup failed ({exc}) — finale markers fall back to TMDB")
            return None, ""
        return (episodes, "tvdb") if episodes else (None, "")

    # ── entry point ────────────────────────────────────────────────────────
    def finales(self, *, tvdb_id: Optional[int], tmdb_id: Optional[int], today: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """The finale record for a show, or None when no source has it (TMDB inference then
        happens in the status type from the show record itself)."""
        today = today or date.today()
        episodes, source = self._from_sonarr(tvdb_id, tmdb_id)
        if episodes is None:
            episodes, source = self._from_tvdb(tvdb_id)
        if episodes is None:
            return None
        return summarize(episodes, today, source)
