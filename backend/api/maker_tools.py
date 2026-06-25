import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.auth import mint_psd_access_token, verify_psd_access_token
from core.config import settings as app_settings
from core.job_queue import job_queue
from core.logging import LogIcons, LogTags, log_debug, log_error, log_info, log_success, log_user_action, log_warning
from database import SessionLocal, get_db
from models.drive import Drive
from models.job import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JOB_TYPE_MAKER_MONITOR,
    Job,
    create_job,
    format_complete_message,
    format_start_message,
    update_job_state,
)
from models.setting import get_setting, get_setting_value, upsert_setting
from services.discord_notifications import send_discord_notification, send_major_error_notification

router = APIRouter(prefix="/api/maker-tools", tags=["maker-tools"])

SETTING_MAKER_MONITOR_CONFIG = "maker_tools_monitor_config"
SETTING_MAKER_MONITOR_LAST_RESULT = "maker_tools_monitor_last_result"
MAKER_MONITOR_DEFAULT_MISSING_RETENTION_DAYS = 2
MAKER_MONITOR_TODAY_GRACE_DAYS = 1
TMDB_REGEX = re.compile(r"\{tmdb-(\d+)\}", re.IGNORECASE)
TVDB_REGEX = re.compile(r"\{tvdb-(\d+)\}", re.IGNORECASE)
PSD_ID_TAG_REGEX = re.compile(r"\s*\{(?:tmdb|tvdb|imdb)-[^}]+\}", re.IGNORECASE)
SEASON_NUMBER_REGEX = re.compile(r"(?i)\s-\sseason\s*(\d+)")
SPECIALS_REGEX = re.compile(r"(?i)\s-\sspecials")
_LIKE_UNSAFE_RE = re.compile(r'[<>:"/\\|?*%\x00-\x1f]')


class MakerMonitorConfig(BaseModel):
    tmdb_api_key: str = ""
    lookahead_days: int = 21
    drive_ids: list[int] = Field(default_factory=list)
    enable_discovery: bool = True
    discovery_popularity: float = 1.0
    discovery_vote_count: int = 0
    discovery_max_results: int = 25
    discovery_languages: list[str] = Field(default_factory=lambda: ["en", "ko", "ja", "zh", "es"])
    missing_retention_days: int = MAKER_MONITOR_DEFAULT_MISSING_RETENTION_DAYS


class MakerMonitorShowResult(BaseModel):
    tmdb_id: str
    name: str
    homepage: str
    season_number: int
    date: str
    first_air_year: str = ""
    poster_exists: bool
    poster_url: str = ""
    imdb_id: str = ""
    tvdb_id: int | None = None
    external_sources: list[str] = Field(default_factory=list)


class MakerMonitorLibraryResult(BaseModel):
    library_name: str
    library_type: str
    total_scanned: int
    premieres_found: int
    posters_needed: int
    shows: list[MakerMonitorShowResult] = Field(default_factory=list)


class MakerDiscoveryTypeStatus(BaseModel):
    type: str
    have: bool
    have_sources: list[str] = Field(default_factory=list)
    synced: bool = False
    synced_sources: list[str] = Field(default_factory=list)


class MakerDiscoveryItem(BaseModel):
    name: str
    date: str
    popularity: float
    overview: str
    type: str
    homepage: str
    language: str
    poster_url: str = ""
    imdb_id: str = ""
    tvdb_id: int | None = None
    statuses: list[MakerDiscoveryTypeStatus] = Field(default_factory=list)


class MakerMonitorDiscoveryResult(BaseModel):
    shows: list[MakerDiscoveryItem] = Field(default_factory=list)
    movies: list[MakerDiscoveryItem] = Field(default_factory=list)


class MakerMonitorRunRequest(BaseModel):
    config: MakerMonitorConfig | None = None
    save_config: bool = False


class MakerMonitorRunResponse(BaseModel):
    lookahead_days: int
    range_start: str
    range_end: str
    total_scanned: int
    total_premieres: int
    total_needed: int
    libraries: list[MakerMonitorLibraryResult]
    discovery: MakerMonitorDiscoveryResult | None = None


class MakerMonitorRunQueuedResponse(BaseModel):
    job_id: int
    message: str


def _monitor_today_local() -> date:
    return datetime.now().astimezone().date()


def _parse_iso_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_first_air_year(tmdb_id: str, tmdb_api_key: str) -> str:
    """Fetch the first air year for a TV show from TMDB (lightweight, best-effort fallback)."""
    try:
        payload = _tmdb_fetch_json(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}",
            {"api_key": tmdb_api_key, "language": "en-US"},
            "first air year",
        )
    except TmdbUpstreamError:
        return ""
    first_air_date = str(payload.get("first_air_date") or "")
    return first_air_date[:4] if len(first_air_date) >= 4 else ""


def _merge_recent_missing_items(
    current_results: list[MakerMonitorLibraryResult],
    previous_payload: dict[str, Any],
    reference_today: date,
    retention_days: int,
    fresh_dates: dict[tuple[str, int], str] | None = None,
    scanned_tmdb_ids: dict[tuple[str, str], set[str]] | None = None,
    scanned_seasons: dict[tuple[str, str], dict[str, set[int]]] | None = None,
    tmdb_api_key: str = "",
) -> tuple[int, int]:
    # fresh_dates: mapping of (tmdb_id, season_number) -> air_date string from current scan results
    # scanned_tmdb_ids: mapping of (library_name, library_type) -> set of tmdb_ids found on disk this run
    if not isinstance(previous_payload, dict):
        return 0, 0

    previous_run_date = _parse_iso_date(str(previous_payload.get("range_start") or ""))
    if not previous_run_date:
        return 0, 0

    retention_cutoff = reference_today - timedelta(days=max(0, retention_days))
    if previous_run_date < retention_cutoff:
        return 0, 0

    previous_libraries = previous_payload.get("libraries")
    if not isinstance(previous_libraries, list):
        return 0, 0

    previous_by_library: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in previous_libraries:
        if not isinstance(entry, dict):
            continue
        library_name = str(entry.get("library_name") or "").strip()
        if not library_name:
            continue
        library_type = str(entry.get("library_type") or "").strip().upper()
        shows = entry.get("shows")
        if not isinstance(shows, list):
            continue
        previous_by_library[(library_name, library_type)] = [show for show in shows if isinstance(show, dict)]

    libraries_updated = 0
    items_added = 0

    for library in current_results:
        previous_shows = previous_by_library.get((library.library_name, library.library_type.upper()), [])
        if not previous_shows:
            continue

        current_keys = {(show.tmdb_id, int(show.season_number)) for show in library.shows}
        library_key = (library.library_name, library.library_type.upper())
        drive_scanned = scanned_tmdb_ids.get(library_key) if scanned_tmdb_ids is not None else None
        drive_inventory = scanned_seasons.get(library_key) if scanned_seasons is not None else None
        added_this_library = 0

        for previous_show in previous_shows:
            if bool(previous_show.get("poster_exists")):
                continue

            tmdb_id = str(previous_show.get("tmdb_id") or "").strip()
            # Skip if the show's file is no longer in this drive's folder
            if drive_scanned is not None and tmdb_id not in drive_scanned:
                continue
            season_raw = previous_show.get("season_number")
            season_number: int | None = None
            try:
                season_number = int(season_raw)
            except Exception:
                season_number = None
            if season_number is None:
                continue

            show_key = (tmdb_id, season_number)
            if not tmdb_id or show_key in current_keys:
                continue

            air_date = _parse_iso_date(str(previous_show.get("date") or ""))
            if air_date and air_date < retention_cutoff:
                continue

            external_sources_raw = previous_show.get("external_sources")
            external_sources = []
            if isinstance(external_sources_raw, list):
                external_sources = [str(item).strip() for item in external_sources_raw if str(item).strip()]

            resolved_date = str(previous_show.get("date") or "")
            if fresh_dates is not None:
                fresh_date = fresh_dates.get((tmdb_id, season_number))
                if fresh_date:
                    resolved_date = fresh_date

            poster_exists_now = False
            if drive_inventory is not None:
                poster_exists_now = season_number in drive_inventory.get(tmdb_id, set())

            first_air_year = str(previous_show.get("first_air_year") or "")
            if not first_air_year and tmdb_api_key:
                first_air_year = _fetch_first_air_year(tmdb_id, tmdb_api_key)

            library.shows.append(
                MakerMonitorShowResult(
                    tmdb_id=tmdb_id,
                    name=str(previous_show.get("name") or "Unknown"),
                    homepage=str(previous_show.get("homepage") or f"https://www.themoviedb.org/tv/{tmdb_id}"),
                    season_number=season_number,
                    date=resolved_date,
                    first_air_year=first_air_year,
                    poster_exists=poster_exists_now,
                    poster_url=str(previous_show.get("poster_url") or ""),
                    imdb_id=str(previous_show.get("imdb_id") or ""),
                    tvdb_id=previous_show.get("tvdb_id") if isinstance(previous_show.get("tvdb_id"), int) else None,
                    external_sources=external_sources,
                )
            )
            current_keys.add(show_key)
            added_this_library += 1

        if added_this_library > 0:
            library.shows.sort(key=lambda item: item.date)
            library.premieres_found = len(library.shows)
            library.posters_needed = sum(1 for show in library.shows if not show.poster_exists)
            libraries_updated += 1
            items_added += added_this_library

    return libraries_updated, items_added


def _parse_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _parse_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except Exception:
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _sanitize_drive_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []

    cleaned: list[int] = []
    seen: set[int] = set()
    for item in value:
        drive_id: int | None = None
        try:
            drive_id = int(item)
        except Exception:
            drive_id = None
        if drive_id is None:
            continue
        if drive_id <= 0 or drive_id in seen:
            continue
        seen.add(drive_id)
        cleaned.append(drive_id)
    return cleaned


def _sanitize_monitor_config(payload: Any) -> MakerMonitorConfig:
    defaults = MakerMonitorConfig()
    if not isinstance(payload, dict):
        return defaults

    data: dict[str, Any] = {
        "drive_ids": _sanitize_drive_ids(payload.get("drive_ids")),
    }

    if payload.get("tmdb_api_key") is not None:
        data["tmdb_api_key"] = str(payload.get("tmdb_api_key")).strip()

    data["lookahead_days"] = _parse_positive_int(payload.get("lookahead_days"), defaults.lookahead_days)
    data["missing_retention_days"] = _parse_non_negative_int(
        payload.get("missing_retention_days"),
        defaults.missing_retention_days,
    )
    data["enable_discovery"] = _parse_bool(payload.get("enable_discovery"), defaults.enable_discovery)
    data["discovery_vote_count"] = _parse_non_negative_int(payload.get("discovery_vote_count"), defaults.discovery_vote_count)
    data["discovery_max_results"] = _parse_positive_int(payload.get("discovery_max_results"), defaults.discovery_max_results)

    try:
        popularity = float(payload.get("discovery_popularity", defaults.discovery_popularity))
        data["discovery_popularity"] = popularity if popularity >= 0 else defaults.discovery_popularity
    except Exception:
        data["discovery_popularity"] = defaults.discovery_popularity

    raw_languages = payload.get("discovery_languages")
    if isinstance(raw_languages, list):
        seen: set[str] = set()
        cleaned: list[str] = []
        for language in raw_languages:
            lang = str(language or "").strip().lower()
            if not lang or lang in seen:
                continue
            seen.add(lang)
            cleaned.append(lang)
        data["discovery_languages"] = cleaned

    return MakerMonitorConfig(**{**defaults.model_dump(), **data})


def _get_monitor_tmdb_key(db: Session) -> str:
    """Return the TMDB API key from the global setting (Settings → General → API Keys)."""
    setting = get_setting(db, "tmdb_api_key")
    return str(setting.value or "").strip() if setting else ""


def _get_monitor_config(db: Session) -> MakerMonitorConfig:
    setting = get_setting(db, SETTING_MAKER_MONITOR_CONFIG)
    if not setting or not setting.value:
        return MakerMonitorConfig()

    try:
        payload = json.loads(setting.value)
    except Exception:
        log_warning(LogTags.MONITOR, "Invalid maker monitor config payload; using defaults")
        return MakerMonitorConfig()

    return _sanitize_monitor_config(payload)


def _get_monitor_last_result(db: Session) -> dict[str, Any]:
    setting = get_setting(db, SETTING_MAKER_MONITOR_LAST_RESULT)
    if not setting or not setting.value:
        return {}

    try:
        payload = json.loads(setting.value)
    except Exception:
        log_warning(LogTags.MONITOR, "Invalid maker monitor last-result payload; ignoring")
        return {}

    return payload if isinstance(payload, dict) else {}


def _extract_name(filename: str) -> str:
    match = re.match(r"^(.*?)\s*[\(\{]", filename)
    if match:
        return match.group(1).strip()
    return filename


def _scan_library(path: str, library_name: str) -> tuple[dict[str, set[int]], set[str]]:
    tv_inventory: dict[str, set[int]] = {}
    movie_ids: set[str] = set()
    files_seen = 0
    tmdb_tagged_files = 0

    log_info(
        LogTags.MONITOR,
        f"Library scan started: '{library_name}' at {path}",
        library=library_name,
        path=path,
    )

    folder_path = Path(path)
    if not folder_path.exists() or not folder_path.is_dir():
        log_warning(LogTags.MONITOR, f"Maker monitor path not found: {path}", library=library_name)
        return tv_inventory, movie_ids

    def _on_walk_error(exc: OSError) -> None:
        log_warning(LogTags.MONITOR, f"Monitor scan walk error: {exc}", library=library_name, path=path)

    try:
        for root, _dirs, files in os.walk(folder_path, topdown=True, onerror=_on_walk_error, followlinks=False):
            for filename in files:
                files_seen += 1
                try:
                    tmdb_match = TMDB_REGEX.search(filename)
                    if not tmdb_match:
                        continue

                    tmdb_tagged_files += 1

                    tmdb_id = tmdb_match.group(1)
                    has_tvdb = TVDB_REGEX.search(filename)
                    season_match = SEASON_NUMBER_REGEX.search(filename)
                    specials_match = SPECIALS_REGEX.search(filename)

                    if has_tvdb or season_match or specials_match:
                        tv_inventory.setdefault(tmdb_id, set())
                        if season_match:
                            tv_inventory[tmdb_id].add(int(season_match.group(1)))
                        elif specials_match:
                            tv_inventory[tmdb_id].add(0)
                    else:
                        movie_ids.add(tmdb_id)
                except Exception as exc:
                    log_warning(LogTags.MONITOR, f"Monitor scan skipped file due to parse error: {exc}", library=library_name, file=filename, root=root)
    except Exception as exc:
        log_error(LogTags.MONITOR, f"Monitor scan failed traversing library: {exc}\n{traceback.format_exc()}", library=library_name, path=path)
        return {}, set()

    log_info(
        LogTags.MONITOR,
        (
            f"Library scan complete: '{library_name}' | files={files_seen} | tmdb_tagged={tmdb_tagged_files} "
            f"| unique_shows={len(tv_inventory)} | unique_movies={len(movie_ids)}"
        ),
        library=library_name,
        files_seen=files_seen,
        tmdb_tagged_files=tmdb_tagged_files,
        unique_shows=len(tv_inventory),
        unique_movies=len(movie_ids),
    )

    return tv_inventory, movie_ids


def _check_show_status(
    tmdb_id: str,
    existing_seasons: set[int],
    tmdb_api_key: str,
    lookahead_days: int,
) -> MakerMonitorShowResult | None:
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
    # append_to_response=external_ids folds the IMDb/TVDB ids into this same call
    # (free — no extra request) so the card can show them like the request cards.
    # Raises TmdbUpstreamError on failure so the run's circuit breaker can count it (vs. a legit no-premiere None).
    payload = _tmdb_fetch_json(
        url,
        {"api_key": tmdb_api_key, "language": "en-US", "append_to_response": "external_ids"},
        "show status",
    )

    next_episode = payload.get("next_episode_to_air")
    if not isinstance(next_episode, dict):
        return None

    air_date = str(next_episode.get("air_date") or "").strip()
    season_number = next_episode.get("season_number")
    episode_number = next_episode.get("episode_number")
    if not air_date or not isinstance(season_number, int) or not isinstance(episode_number, int):
        return None

    try:
        premiere_date = datetime.strptime(air_date, "%Y-%m-%d").date()
    except ValueError:
        return None

    today = _monitor_today_local()
    end_date = today + timedelta(days=lookahead_days)
    start_date = today - timedelta(days=MAKER_MONITOR_TODAY_GRACE_DAYS)
    if not (start_date <= premiere_date <= end_date):
        return None
    if episode_number != 1:
        return None

    poster_exists = season_number in existing_seasons

    name = str(payload.get("name") or "Unknown")
    first_air_date = str(payload.get("first_air_date") or "")
    first_air_year = first_air_date[:4] if len(first_air_date) >= 4 else ""
    poster_path = str(payload.get("poster_path") or "")
    poster_url = f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else ""
    ext_ids = payload.get("external_ids") if isinstance(payload.get("external_ids"), dict) else {}
    imdb_id = str(ext_ids.get("imdb_id") or "").strip()
    tvdb_raw = ext_ids.get("tvdb_id")
    tvdb_id = int(tvdb_raw) if isinstance(tvdb_raw, int) and tvdb_raw > 0 else None
    log_info(
        LogTags.MONITOR,
        (
            f"Premiere match: '{name}' (tmdb:{tmdb_id}) | season={season_number} | air_date={air_date} "
            f"| poster_exists={poster_exists}"
        ),
        tmdb_id=tmdb_id,
        show=name,
        season=season_number,
        air_date=air_date,
        poster_exists=poster_exists,
    )
    return MakerMonitorShowResult(
        tmdb_id=tmdb_id,
        name=name,
        homepage=f"https://www.themoviedb.org/tv/{tmdb_id}",
        season_number=season_number,
        date=air_date,
        first_air_year=first_air_year,
        poster_exists=poster_exists,
        poster_url=poster_url,
        imdb_id=imdb_id,
        tvdb_id=tvdb_id,
        external_sources=[],
    )


def _resolve_monitor_drives(db: Session, drive_ids: list[int]) -> list[Drive]:
    if not drive_ids:
        return []

    requested = set(drive_ids)
    drives = db.query(Drive).filter(Drive.id.in_(requested)).all()
    if not drives:
        return []

    order_map = {drive_id: index for index, drive_id in enumerate(drive_ids)}
    return sorted(drives, key=lambda drive: order_map.get(int(drive.id), 10_000))


def _resolve_discovery_drives(db: Session) -> list[Drive]:
    return (
        db.query(Drive)
        .filter(Drive.subscribed.is_(True), Drive.is_deprecated.is_(False))
        .order_by(Drive.name)
        .all()
    )


def _build_inventory_by_type(selected_drives: list[Drive]) -> tuple[
    dict[str, dict[str, dict[str, set[Any]]]],
    dict[str, dict[str, set[str]]],
]:
    tv_inventory_by_type: dict[str, dict[str, dict[str, set[Any]]]] = {}
    movie_inventory_by_type: dict[str, dict[str, set[str]]] = {}

    for drive in selected_drives:
        drive_type = str(drive.style_type or "").strip().upper() or "CUSTOM"
        drive_path = drive.get_local_path()
        tv_inventory, movie_inventory = _scan_library(str(drive_path), drive.name)

        tv_inventory_by_type.setdefault(drive_type, {})
        movie_inventory_by_type.setdefault(drive_type, {})

        for tmdb_id, seasons in tv_inventory.items():
            tv_inventory_by_type[drive_type].setdefault(tmdb_id, {"seasons": set(), "sources": set()})
            tv_inventory_by_type[drive_type][tmdb_id]["seasons"].update(seasons)
            tv_inventory_by_type[drive_type][tmdb_id]["sources"].add(drive.name)

        for tmdb_id in movie_inventory:
            movie_inventory_by_type[drive_type].setdefault(tmdb_id, set())
            movie_inventory_by_type[drive_type][tmdb_id].add(drive.name)

    return tv_inventory_by_type, movie_inventory_by_type


def _fetch_discovery_items(
    category: str,
    start_date: str,
    end_date: str,
    config: MakerMonitorConfig,
    my_tv_inventory_by_type: dict[str, dict[str, dict[str, set[Any]]]],
    my_movie_inventory_by_type: dict[str, dict[str, set[str]]],
    ext_tv_inventory_by_type: dict[str, dict[str, dict[str, set[Any]]]],
    ext_movie_inventory_by_type: dict[str, dict[str, set[str]]],
    breaker: "TmdbCircuitBreaker | None" = None,
) -> list[MakerDiscoveryItem]:
    if category == "tv":
        url = "https://api.themoviedb.org/3/discover/tv"
        base_params = {
            "api_key": config.tmdb_api_key,
            "sort_by": "popularity.desc",
            "first_air_date.gte": start_date,
            "first_air_date.lte": end_date,
            "include_null_first_air_dates": "false",
            "vote_count.gte": config.discovery_vote_count,
        }
    else:
        url = "https://api.themoviedb.org/3/discover/movie"
        base_params = {
            "api_key": config.tmdb_api_key,
            "sort_by": "popularity.desc",
            "primary_release_date.gte": start_date,
            "primary_release_date.lte": end_date,
            "vote_count.gte": config.discovery_vote_count,
        }

    all_results_map: dict[int, dict[str, Any]] = {}
    langs_to_search = config.discovery_languages if config.discovery_languages else [None]

    for lang in langs_to_search:
        params = base_params.copy()
        if lang:
            params["with_original_language"] = lang
        params["page"] = 1
        lang_key = str(lang or "ALL").upper()
        count_raw = 0
        count_kept = 0
        max_pop_seen = 0.0

        try:
            payload = _tmdb_fetch_json(url, params, f"discovery ({category}/{lang_key})")
        except TmdbUpstreamError as err:
            if breaker is not None:
                breaker.record_failure(err.reason)
                if breaker.tripped:
                    raise MonitorAborted(breaker.abort_message())
            continue
        if breaker is not None:
            breaker.record_success()

        results = payload.get("results")
        if not isinstance(results, list):
            continue

        count_raw = len(results)

        for item in results[: config.discovery_max_results]:
            if not isinstance(item, dict):
                continue
            popularity = float(item.get("popularity") or 0)
            if popularity > max_pop_seen:
                max_pop_seen = popularity
            if popularity < config.discovery_popularity:
                continue
            item_id = item.get("id")
            if isinstance(item_id, int):
                all_results_map[item_id] = item
                count_kept += 1

        log_info(
            LogTags.MONITOR,
            (
                f"Discovery language complete: category={category} language={lang_key} "
                f"| returned={count_raw} | kept={count_kept} | max_popularity={max_pop_seen:.2f}"
            ),
            category=category,
            language=lang_key,
            api_returned=count_raw,
            kept=count_kept,
            max_popularity=max_pop_seen,
        )

    final_items = list(all_results_map.values())
    final_items.sort(key=lambda row: float(row.get("popularity") or 0), reverse=True)

    active_types = sorted(
        ({key for key in my_tv_inventory_by_type.keys()} | {key for key in my_movie_inventory_by_type.keys()})
        | ({key for key in ext_tv_inventory_by_type.keys()} | {key for key in ext_movie_inventory_by_type.keys()})
    )
    discovery_results: list[MakerDiscoveryItem] = []

    is_movie = category == "movie"
    for item in final_items:
        tmdb_id = str(item.get("id") or "").strip()
        if not tmdb_id:
            continue

        title = str(item.get("title") if is_movie else item.get("name") or "Unknown")
        release_date = str(item.get("release_date") if is_movie else item.get("first_air_date") or "")

        statuses: list[MakerDiscoveryTypeStatus] = []
        for drive_type in active_types:
            have_sources: list[str] = []
            synced_sources: list[str] = []

            if is_movie:
                my_sources = my_movie_inventory_by_type.get(drive_type, {}).get(tmdb_id, set())
                ext_sources = ext_movie_inventory_by_type.get(drive_type, {}).get(tmdb_id, set())
                if my_sources:
                    have_sources = sorted(str(source) for source in my_sources)
                if ext_sources:
                    synced_sources = sorted(str(source) for source in ext_sources)
            else:
                my_entry = my_tv_inventory_by_type.get(drive_type, {}).get(tmdb_id)
                ext_entry = ext_tv_inventory_by_type.get(drive_type, {}).get(tmdb_id)
                if my_entry and 1 in my_entry.get("seasons", set()):
                    have_sources = sorted(str(source) for source in my_entry.get("sources", set()))
                if ext_entry and 1 in ext_entry.get("seasons", set()):
                    synced_sources = sorted(str(source) for source in ext_entry.get("sources", set()))

            statuses.append(
                MakerDiscoveryTypeStatus(
                    type=drive_type,
                    have=bool(have_sources),
                    have_sources=have_sources,
                    synced=bool(synced_sources),
                    synced_sources=synced_sources,
                )
            )

        discovery_results.append(
            MakerDiscoveryItem(
                name=title,
                date=release_date,
                popularity=float(item.get("popularity") or 0),
                overview=str(item.get("overview") or ""),
                type="Movie" if is_movie else "Series",
                homepage=f"https://www.themoviedb.org/{'movie' if is_movie else 'tv'}/{tmdb_id}",
                language=str(item.get("original_language") or "en").upper(),
                poster_url=(
                    f"https://image.tmdb.org/t/p/w185{item.get('poster_path')}"
                    if item.get("poster_path") else ""
                ),
                statuses=statuses,
            )
        )

    # discover/* doesn't return external ids, so fetch IMDb/TVDB ids in parallel
    # (bounded by discovery_max_results) so discovery cards can show the external
    # links like the request cards. Best-effort — _fetch_external_ids never raises.
    if discovery_results:
        media_type = "movie" if is_movie else "tv"
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs: dict[Any, int] = {}
            for idx, dres in enumerate(discovery_results):
                try:
                    tid = int(dres.homepage.rsplit("/", 1)[-1])
                except ValueError:
                    continue
                futs[pool.submit(_fetch_external_ids, tid, media_type, config.tmdb_api_key)] = idx
            for fut in as_completed(futs):
                imdb_id, tvdb_id = fut.result()
                if imdb_id:
                    discovery_results[futs[fut]].imdb_id = imdb_id
                if tvdb_id:
                    discovery_results[futs[fut]].tvdb_id = tvdb_id

    return discovery_results


@router.get("/monitor/config", response_model=MakerMonitorConfig)
def get_maker_monitor_config(db: Session = Depends(get_db)) -> MakerMonitorConfig:
    """Return saved Maker Tools monitor configuration."""
    return _get_monitor_config(db)


@router.get("/monitor/last-result")
def get_maker_monitor_last_result(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the last successful monitor run result for UI reload persistence."""
    return _get_monitor_last_result(db)


@router.get("/monitor/needed-count")
def get_maker_monitor_needed_count(db: Session = Depends(get_db)) -> dict[str, int]:
    """Return count of monitored items needing posters from the last scan (for sidebar badge)."""
    last_result = _get_monitor_last_result(db)
    count = last_result.get("total_needed")
    return {"count": count if isinstance(count, int) else 0}


class TmdbSearchResult(BaseModel):
    tmdb_id: int
    media_type: str  # "movie" | "tv" | "collection"
    title: str
    year: str
    overview: str
    poster_url: str
    homepage: str
    imdb_id: str | None = None
    tvdb_id: int | None = None


def _fetch_external_ids(tmdb_id: int, media_type: str, api_key: str) -> tuple[str | None, int | None]:
    """Fetch IMDB and TVDB IDs from TMDB external_ids endpoint (best-effort). Returns (imdb_id, tvdb_id)."""
    if media_type not in ("movie", "tv"):
        return None, None
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids"
    try:
        data = _tmdb_fetch_json(url, {"api_key": api_key}, "external IDs", timeout=8)
    except TmdbUpstreamError:
        return None, None
    imdb_id = str(data.get("imdb_id") or "").strip() or None
    tvdb_raw = data.get("tvdb_id")
    tvdb_id = int(tvdb_raw) if isinstance(tvdb_raw, int) and tvdb_raw > 0 else None
    return imdb_id, tvdb_id


_YEAR_SUFFIX_RE = re.compile(r"\s+\(?(\d{4})\)?$")


MAKER_MONITOR_TMDB_ABORT_THRESHOLD = 8  # consecutive TMDB failures before a run gives up


class TmdbUpstreamError(Exception):
    """A TMDB call failed (timeout / unreachable / non-200 / bad JSON). `.reason` is human-readable; `.status` is the HTTP code, if any."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class MonitorAborted(Exception):
    """Raised to stop a monitor run early — e.g. the TMDB circuit breaker tripped during an outage."""


class TmdbCircuitBreaker:
    """Trips a run after N consecutive TMDB failures so an outage aborts fast instead of grinding through every item."""

    def __init__(self, max_consecutive: int = MAKER_MONITOR_TMDB_ABORT_THRESHOLD) -> None:
        self.max_consecutive = max_consecutive
        self.consecutive = 0
        self.total_failures = 0
        self.last_reason = ""

    def record_success(self) -> None:
        self.consecutive = 0

    def record_failure(self, reason: str) -> None:
        self.consecutive += 1
        self.total_failures += 1
        self.last_reason = reason

    @property
    def tripped(self) -> bool:
        return self.consecutive >= self.max_consecutive

    def abort_message(self) -> str:
        return (
            f"Aborted Maker Monitor: {self.consecutive} TMDB requests failed in a row "
            f"({self.last_reason}) — the API appears to be down. Try again later."
        )


# Shared TMDB error layer: name the real cause (timeout/unreachable/5xx/429) in both the log and the toast.
# `context` is a short human label (e.g. "images", "TV details") spliced into the message.
def _tmdb_failure_reason(context: str, *, exc: Exception | None = None, status: int | None = None) -> str:
    """Short, specific cause for a failed TMDB call — shared by the toast endpoints and the background scans."""
    if exc is not None:
        if isinstance(exc, requests.exceptions.Timeout):
            return f"TMDB timed out loading {context}"
        if isinstance(exc, requests.exceptions.ConnectionError):
            return f"couldn't reach TMDB for {context} (network/DNS, or TMDB is down)"
        return f"TMDB request for {context} failed: {exc}"
    if status == 401:
        return "TMDB rejected the API key (HTTP 401)"
    if status == 429:
        return f"TMDB rate-limited loading {context} (HTTP 429)"
    if status in (500, 502, 503, 504):
        return f"TMDB temporarily unavailable loading {context} (HTTP {status})"
    return f"TMDB returned HTTP {status} loading {context}"


def _tmdb_is_severe(*, exc: Exception | None = None, status: int | None = None) -> bool:
    """Outage-class failures (transport error / 5xx) log as ERROR; client-ish ones (401/429/4xx) as WARNING."""
    return exc is not None or status in (500, 502, 503, 504)


def _log_tmdb_failure(reason: str, *, severe: bool, exc: Exception | None = None) -> None:
    detail = f"{reason}: {exc}" if exc is not None else reason
    (log_error if severe else log_warning)(LogTags.MONITOR, detail)


def _tmdb_http_error(err: TmdbUpstreamError) -> HTTPException:
    """Convert a TmdbUpstreamError into the user-facing HTTPException for an endpoint (401 → bad-key 400)."""
    if err.status == 401:
        return HTTPException(status_code=400, detail="Invalid TMDB API key. Check it in Settings → General → API Keys.")
    return HTTPException(status_code=502, detail=f"{err.reason[:1].upper()}{err.reason[1:]} — try again shortly.")


def _tmdb_fetch_json(url: str, params: dict[str, Any], context: str, timeout: int = 10, retries: int = 2) -> dict[str, Any]:
    """Low-level TMDB GET: logs + raises TmdbUpstreamError on any failure, else returns the parsed JSON dict.

    A 429 (rate limit) is retried up to ``retries`` times, honoring the Retry-After
    header, before giving up — so a brief throttle doesn't fail the request outright."""
    attempt = 0
    while True:
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except Exception as exc:
            reason = _tmdb_failure_reason(context, exc=exc)
            _log_tmdb_failure(reason, severe=True, exc=exc)
            raise TmdbUpstreamError(reason)
        if resp.status_code == 429 and attempt < retries:
            try:
                retry_after = float(resp.headers.get("Retry-After", ""))
            except ValueError:
                retry_after = 0.0
            time.sleep(min(max(retry_after, 0.5 * (attempt + 1)), 10.0))
            attempt += 1
            continue
        if resp.status_code != 200:
            reason = _tmdb_failure_reason(context, status=resp.status_code)
            _log_tmdb_failure(reason, severe=_tmdb_is_severe(status=resp.status_code))
            raise TmdbUpstreamError(reason, status=resp.status_code)
        try:
            data = resp.json()
        except Exception:
            reason = f"TMDB sent an unreadable (non-JSON) response loading {context}"
            _log_tmdb_failure(reason, severe=True)
            raise TmdbUpstreamError(reason)
        return data if isinstance(data, dict) else {}


def _tmdb_get_json(url: str, params: dict[str, Any], context: str, timeout: int = 10) -> dict[str, Any]:
    """Endpoint wrapper around _tmdb_fetch_json that surfaces failures as a user-facing HTTPException."""
    try:
        return _tmdb_fetch_json(url, params, context, timeout)
    except TmdbUpstreamError as err:
        raise _tmdb_http_error(err)


def _tmdb_raise_http(context: str, *, exc: Exception | None = None, status: int | None = None) -> HTTPException:
    """Log + build the user-facing HTTPException for a non-JSON TMDB call (image streams)."""
    reason = _tmdb_failure_reason(context, exc=exc, status=status)
    _log_tmdb_failure(reason, severe=_tmdb_is_severe(exc=exc, status=status), exc=exc)
    return _tmdb_http_error(TmdbUpstreamError(reason, status=status))


@router.get("/tmdb/search", response_model=list[TmdbSearchResult])
def tmdb_search(q: str, type: str = "all", db: Session = Depends(get_db)) -> list[TmdbSearchResult]:
    """Proxy a TMDB search and return normalized results with external IDs.

    type: 'all' | 'movie' | 'tv' | 'collection'

    A trailing 4-digit year in the query, with or without parentheses
    (e.g. "The Office 2005" or "The Office (2005)"), is automatically
    extracted and passed as the appropriate TMDB year filter parameter.
    """
    query = q.strip()
    if not query:
        return []

    api_key = _get_monitor_tmdb_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured. Add it in Settings → General → API Keys.")

    filter_type = str(type or "all").strip().lower()
    if filter_type not in ("all", "movie", "tv", "collection"):
        filter_type = "all"

    # Extract a trailing year from the query string and pass it as a structured param
    year_param: str | None = None
    year_match = _YEAR_SUFFIX_RE.search(query)
    if year_match:
        candidate = int(year_match.group(1))
        current_year = datetime.now().year
        if 1880 <= candidate <= current_year + 5:
            year_param = str(candidate)
            query = query[: year_match.start()]

    raw_items: list[dict[str, Any]] = []

    def _fetch_page(url: str, extra_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"api_key": api_key, "query": query, "language": "en-US", "page": 1}
        if extra_params:
            params.update(extra_params)
        results = _tmdb_get_json(url, params, "search results").get("results", [])
        return results if isinstance(results, list) else []

    if filter_type == "all":
        year_extra: dict[str, Any] = {}
        if year_param:
            year_extra["year"] = year_param  # TMDB multi-search accepts 'year' loosely
        multi_items = _fetch_page("https://api.themoviedb.org/3/search/multi", year_extra or None)
        for item in multi_items:
            mt = str(item.get("media_type") or "")
            if mt in ("movie", "tv"):
                raw_items.append(item)
        collection_items = _fetch_page("https://api.themoviedb.org/3/search/collection")
        for item in collection_items:
            item["media_type"] = "collection"
            raw_items.append(item)
    elif filter_type == "movie":
        movie_extra: dict[str, Any] = {"primary_release_year": year_param} if year_param else {}
        items = _fetch_page("https://api.themoviedb.org/3/search/movie", movie_extra or None)
        for item in items:
            item["media_type"] = "movie"
            raw_items.extend([item])
    elif filter_type == "tv":
        tv_extra: dict[str, Any] = {"first_air_date_year": year_param} if year_param else {}
        items = _fetch_page("https://api.themoviedb.org/3/search/tv", tv_extra or None)
        for item in items:
            item["media_type"] = "tv"
            raw_items.extend([item])
    elif filter_type == "collection":
        items = _fetch_page("https://api.themoviedb.org/3/search/collection")
        for item in items:
            item["media_type"] = "collection"
            raw_items.extend([item])

    # Build base results
    results: list[TmdbSearchResult] = []
    for item in raw_items:
        media_type = str(item.get("media_type") or "")
        tmdb_id = int(item.get("id") or 0)
        if not tmdb_id:
            continue

        if media_type == "movie":
            title = str(item.get("title") or item.get("original_title") or "Unknown")
            raw_date = str(item.get("release_date") or "")
            homepage = f"https://www.themoviedb.org/movie/{tmdb_id}"
        elif media_type == "tv":
            title = str(item.get("name") or item.get("original_name") or "Unknown")
            raw_date = str(item.get("first_air_date") or "")
            homepage = f"https://www.themoviedb.org/tv/{tmdb_id}"
        else:  # collection
            title = str(item.get("name") or item.get("original_name") or "Unknown")
            raw_date = ""
            homepage = f"https://www.themoviedb.org/collection/{tmdb_id}"

        year = raw_date[:4] if len(raw_date) >= 4 else ""
        poster_path = str(item.get("poster_path") or "")
        poster_url = f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else ""

        results.append(TmdbSearchResult(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=title,
            year=year,
            overview=str(item.get("overview") or ""),
            poster_url=poster_url,
            homepage=homepage,
        ))

    # Fetch external IDs in parallel for movie and tv items
    ext_id_targets = [(i, r) for i, r in enumerate(results) if r.media_type in ("movie", "tv")]
    if ext_id_targets:
        with ThreadPoolExecutor(max_workers=10) as pool:
            future_map = {
                pool.submit(_fetch_external_ids, r.tmdb_id, r.media_type, api_key): i
                for i, r in ext_id_targets
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    imdb_id, tvdb_id = future.result()
                    results[idx] = results[idx].model_copy(update={"imdb_id": imdb_id, "tvdb_id": tvdb_id})
                except Exception as e:
                    log_debug(LogTags.MODULE, f"Failed to enrich external IDs for result idx={idx}: {e}")

    return results


# ---------------------------------------------------------------------------
# TMDB poster availability check (local drive DB)
# ---------------------------------------------------------------------------

_SEASON_RE = re.compile(r"[-\u2013]\s*season\s+(\d+)", re.IGNORECASE)
_SPECIALS_RE = re.compile(r"[-\u2013]\s*specials\b", re.IGNORECASE)
_TVDB_RE = re.compile(r"\{tvdb-", re.IGNORECASE)


def _is_tv_filename(file_name: str) -> bool:
    """Return True if the filename looks like a TV show file (has tvdb tag or season marker)."""
    return bool(_TVDB_RE.search(file_name) or _SEASON_RE.search(file_name) or _SPECIALS_RE.search(file_name))


def _matches_media_type(file_name: str, media_type: str) -> bool:
    """Guard against TMDB ID collisions across media type namespaces.

    Movie and TV TMDB IDs are independent — the same number can exist in both.
    TV files always carry a {tvdb-} tag or a season marker; movie files don't.
    Collections have no reliable marker so we accept them unconditionally.
    """
    if not media_type or media_type == "collection":
        return True
    is_tv = _is_tv_filename(file_name)
    return is_tv if media_type == "tv" else not is_tv


class PosterCheckItem(BaseModel):
    tmdb_id: int
    title: str
    year: str = ""
    media_type: str = ""  # "movie" | "tv" | "collection"


class PosterCheckRequest(BaseModel):
    items: list[PosterCheckItem]


def _collect_style_seasons(
    rows: list[tuple],
    media_type: str,
    year: str,
    expected_tmdb_id: int | None = None,
) -> dict[str, set[int]]:
    """Build a mapping of style_label -> {season_numbers} from (Poster, Drive) rows.

    Every matched style is present as a key; the season set is empty for non-TV items.
    When expected_tmdb_id is provided, any file that carries a {tmdb-XXXX} tag must
    have XXXX == expected_tmdb_id — this double-checks title-fallback matches against
    the actual TMDB ID so a file for a different title can't trigger a false positive.
    Files with no embedded TMDB tag are always accepted (they are the intended target
    of the fallback for legacy untagged posters).
    """
    style_seasons: dict[str, set[int]] = {}
    for poster, drive in rows:
        if year and year not in poster.file_name:
            continue
        if not _matches_media_type(poster.file_name, media_type):
            continue
        if expected_tmdb_id is not None:
            m = TMDB_REGEX.search(poster.file_name)
            if m and int(m.group(1)) != expected_tmdb_id:
                continue
        style = "Custom" if drive.is_custom else drive.style_type
        if style not in style_seasons:
            style_seasons[style] = set()
        if media_type == "tv":
            if _SPECIALS_RE.search(poster.file_name):
                style_seasons[style].add(0)
            else:
                m = _SEASON_RE.search(poster.file_name)
                if m:
                    style_seasons[style].add(int(m.group(1)))
    return style_seasons


@router.post("/tmdb/poster-check")
def tmdb_poster_check(
    payload: PosterCheckRequest,
    db: Session = Depends(get_db),
) -> dict[int, list[dict[str, Any]]]:
    """Check the local poster database for matching files for a list of TMDB items.

    Primary match: filename contains {tmdb-<id>} (exact, collision-safe via media type guard).
    Fallback match: filename contains title + year (for files without embedded TMDB IDs).

    Returns a mapping of tmdb_id -> list of {style, seasons} objects, one per drive style found.
    """
    from models.poster import Poster

    result: dict[int, list[dict[str, Any]]] = {}

    for item in payload.items:
        title = item.title.strip()
        if not title:
            continue

        # ── Primary: match by embedded TMDB ID ──────────────────────────────
        tmdb_rows = (
            db.query(Poster, Drive)
            .join(Drive, Poster.drive_id == Drive.drive_id)
            .filter(
                Poster.file_name.ilike(f"%{{tmdb-{item.tmdb_id}}}%"),
                Drive.last_synced.isnot(None),
            )
            .order_by(Drive.name.asc(), Poster.file_name.asc())
            .limit(100)
            .all()
        )

        style_seasons = _collect_style_seasons(tmdb_rows, item.media_type, item.year)

        # ── Fallback: title + year for files without TMDB ID tags ───────────
        safe_title = " ".join(_LIKE_UNSAFE_RE.sub("", title).split()).strip()
        sql_title = safe_title.replace("_", r"\_")
        if not style_seasons and sql_title:
            fallback_rows = (
                db.query(Poster, Drive)
                .join(Drive, Poster.drive_id == Drive.drive_id)
                .filter(
                    Poster.file_name.ilike(f"{sql_title} (%", escape="\\"),
                    Drive.last_synced.isnot(None),
                )
                .order_by(Drive.name.asc(), Poster.file_name.asc())
                .limit(100)
                .all()
            )
            style_seasons = _collect_style_seasons(fallback_rows, item.media_type, item.year, expected_tmdb_id=item.tmdb_id)

        if style_seasons:
            result[item.tmdb_id] = [
                {"style": style, "seasons": sorted(style_seasons[style])}
                for style in sorted(style_seasons.keys())
            ]

    return result


# ---------------------------------------------------------------------------
# TMDB image browser
# ---------------------------------------------------------------------------

class TmdbImage(BaseModel):
    file_path: str          # e.g. "/abc123.jpg"
    width: int
    height: int
    language: str | None    # ISO 639-1 or None
    vote_average: float
    url_thumb: str          # w300 thumbnail
    url_full: str           # original


class TmdbImagesResponse(BaseModel):
    posters: list[TmdbImage]
    backdrops: list[TmdbImage]
    logos: list[TmdbImage]


class TmdbSeasonInfo(BaseModel):
    season_number: int
    name: str
    episode_count: int
    air_date: str | None = None
    poster_url: str | None = None


class TmdbTvDetails(BaseModel):
    season_count: int
    seasons: list[TmdbSeasonInfo]
    series_type: str | None = None  # TMDB "type": Scripted, Miniseries, Documentary, Reality, etc.


def _build_tmdb_images(items: list[dict[str, Any]], size_thumb: str = "w300") -> list[TmdbImage]:
    out: list[TmdbImage] = []
    for img in items:
        fp = str(img.get("file_path") or "")
        if not fp:
            continue
        out.append(TmdbImage(
            file_path=fp,
            width=int(img.get("width") or 0),
            height=int(img.get("height") or 0),
            language=str(img.get("iso_639_1") or "") or None,
            vote_average=float(img.get("vote_average") or 0),
            url_thumb=f"https://image.tmdb.org/t/p/{size_thumb}{fp}",
            url_full=f"https://image.tmdb.org/t/p/original{fp}",
        ))
    return out


@router.get("/tmdb/images", response_model=TmdbImagesResponse)
def tmdb_images(tmdb_id: int, media_type: str, language: str = "en", db: Session = Depends(get_db)) -> TmdbImagesResponse:
    """Fetch available posters, backdrops, and logos for a TMDB item."""
    import re

    mt = str(media_type or "").strip().lower()
    if mt not in ("movie", "tv", "collection"):
        raise HTTPException(status_code=400, detail="media_type must be movie, tv, or collection")

    # Validate language: ISO 639-1/2 code (2-3 lowercase alpha) or "all"
    lang = str(language or "en+textless").strip().lower()
    if lang not in ("all", "en+textless") and not re.fullmatch(r"[a-z]{2,3}", lang):
        lang = "en+textless"

    api_key = _get_monitor_tmdb_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured.")

    url = f"https://api.themoviedb.org/3/{mt}/{tmdb_id}/images"
    params: dict[str, str] = {"api_key": api_key}
    img_lang = _build_lang_params(lang)
    if img_lang:
        params["include_image_language"] = img_lang

    data = _tmdb_get_json(url, params, "images")

    posters = _build_tmdb_images(data.get("posters", []))
    backdrops = _build_tmdb_images(data.get("backdrops", []), size_thumb="w780")
    logos = _build_tmdb_images(data.get("logos", []))

    # Sort each group: textless (language=None) first, then by vote_average descending
    for group in (posters, backdrops, logos):
        group.sort(key=lambda x: (0 if x.language is None else 1, -x.vote_average))

    return TmdbImagesResponse(posters=posters, backdrops=backdrops, logos=logos)


@router.get("/tmdb/image-proxy")
def tmdb_image_proxy(path: str, db: Session = Depends(get_db)):
    """Proxy a TMDB image download so the browser gets a proper filename."""
    from fastapi.responses import StreamingResponse

    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid image path")

    api_key = _get_monitor_tmdb_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured.")

    url = f"https://image.tmdb.org/t/p/original{path}"
    try:
        resp = requests.get(url, stream=True, timeout=30)
    except Exception as exc:
        raise _tmdb_raise_http("image download", exc=exc)
    if resp.status_code != 200:
        raise _tmdb_raise_http("image download", status=resp.status_code)

    content_type = resp.headers.get("content-type", "image/jpeg")
    filename = path.lstrip("/")

    return StreamingResponse(
        resp.iter_content(chunk_size=8192),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_lang_params(lang: str) -> str | None:
    """Return the include_image_language value for TMDB, or None to omit it."""
    if lang == "all":
        return None
    if lang == "en+textless":
        return "en,null"
    return lang  # specific language, no textless


@router.get("/tmdb/tv-details", response_model=TmdbTvDetails)
def tmdb_tv_details(tmdb_id: int, db: Session = Depends(get_db)) -> TmdbTvDetails:
    """Fetch TV show details including the full seasons list."""
    api_key = _get_monitor_tmdb_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured.")

    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
    data = _tmdb_get_json(url, {"api_key": api_key, "language": "en-US"}, "TV details")

    seasons: list[TmdbSeasonInfo] = []
    for s in (data.get("seasons") or []):
        sn = int(s.get("season_number") or 0)
        poster_path = str(s.get("poster_path") or "")
        seasons.append(TmdbSeasonInfo(
            season_number=sn,
            name=str(s.get("name") or f"Season {sn}"),
            episode_count=int(s.get("episode_count") or 0),
            air_date=str(s.get("air_date") or "") or None,
            poster_url=f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None,
        ))

    return TmdbTvDetails(
        season_count=int(data.get("number_of_seasons") or 0),
        seasons=seasons,
        series_type=str(data.get("type") or "") or None,
    )


class TmdbOriginCountry(BaseModel):
    countries: list[str] = []  # ISO 3166-1 alpha-2, preference-ordered


@router.get("/tmdb/origin-country", response_model=TmdbOriginCountry)
def tmdb_origin_country(tmdb_id: int, media_type: str, db: Session = Depends(get_db)) -> TmdbOriginCountry:
    """Return a movie/TV item's country of origin as ISO 3166-1 alpha-2 codes.

    Used to pre-select the Apple TV artwork region. Prefers TMDB's ``origin_country``
    (always set for TV, sometimes for movies), then falls back to ``production_countries``.
    """
    mt = str(media_type or "").strip().lower()
    if mt not in ("movie", "tv"):
        return TmdbOriginCountry(countries=[])

    api_key = _get_monitor_tmdb_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured.")

    url = f"https://api.themoviedb.org/3/{mt}/{tmdb_id}"
    data = _tmdb_get_json(url, {"api_key": api_key, "language": "en-US"}, "country of origin")

    countries: list[str] = []
    for c in (data.get("origin_country") or []):
        code = str(c).strip().upper()
        if code and code not in countries:
            countries.append(code)
    for pc in (data.get("production_countries") or []):
        code = str(pc.get("iso_3166_1") or "").strip().upper()
        if code and code not in countries:
            countries.append(code)

    return TmdbOriginCountry(countries=countries)


@router.get("/tmdb/season-images", response_model=TmdbImagesResponse)
def tmdb_season_images(tmdb_id: int, season_number: int, language: str = "en+textless", db: Session = Depends(get_db)) -> TmdbImagesResponse:
    """Fetch poster images for a specific TV season."""
    api_key = _get_monitor_tmdb_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured.")

    lang = str(language or "en+textless").strip().lower()
    if lang not in ("all", "en+textless") and not re.fullmatch(r"[a-z]{2,3}", lang):
        lang = "en+textless"

    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_number}/images"
    params: dict[str, str] = {"api_key": api_key}
    img_lang = _build_lang_params(lang)
    if img_lang:
        params["include_image_language"] = img_lang

    data = _tmdb_get_json(url, params, "season images")

    posters = _build_tmdb_images(data.get("posters", []))
    posters.sort(key=lambda x: (0 if x.language is None else 1, -x.vote_average))

    return TmdbImagesResponse(posters=posters, backdrops=[], logos=[])


# ── PSD Export ───────────────────────────────────────────────────────────────

SETTING_PSD_EXPORT_FOLDER = "psd_export_folder"
SETTING_PSD_TEMPLATE_PATH = "psd_template_path"
SETTING_PSD_OPEN_PHOTOPEA = "psd_open_photopea"
SETTING_PSD_POSTER_FIT_BORDER = "psd_poster_fit_border"
SETTING_PSD_IMAGE_EXPORT_FOLDER = "psd_image_export_folder"

# Bundled default template — lives at backend/assets/default_template.psd
_DEFAULT_TEMPLATE_PATH = Path(__file__).parent.parent / "assets" / "default_template.psd"


def _validate_psd_filename(filename: str) -> None:
    """Reject path traversal and non-PSD names. Raises HTTP 400 on failure.

    Embedded dots are allowed (titles like "Spider-Man... Home" are valid); with
    separators already blocked, a ".." substring can't ascend a directory, so only
    path separators and the bare "."/".." names are rejected.
    """
    if ("/" in filename or "\\" in filename or filename in (".", "..")
            or not filename.lower().endswith(".psd")):
        raise HTTPException(status_code=400, detail="Invalid filename.")


def _psd_storage_dir(db: Session) -> Path:
    """Directory where saved PSDs live: the configured export folder, else the
    temp ``psd_cache`` under the config dir."""
    export_folder = get_setting_value(db, SETTING_PSD_EXPORT_FOLDER)
    return Path(export_folder) if export_folder else app_settings.config_dir / "psd_cache"


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")


def _validate_image_filename(filename: str) -> None:
    """Reject path traversal and non-image names. Raises HTTP 400 on failure.

    Embedded dots are allowed; with separators already blocked, a ".." substring
    can't ascend a directory, so only separators and bare "."/".." are rejected.
    """
    if ("/" in filename or "\\" in filename or filename in (".", "..")
            or not filename.lower().endswith(_IMAGE_EXTS)):
        raise HTTPException(status_code=400, detail="Invalid filename.")


def _find_psd_by_title(save_dir: Path, base_stem: str) -> Path | None:
    """Return an existing PSD in *save_dir* whose name matches "Title (Year)" once
    IDarr ID tags are stripped — regardless of which ``{tmdb-…}``/``{tvdb-…}``/
    ``{imdb-…}`` tag it carries. Prefers the exact untagged name, else the most
    recently modified match.

    This is the New Export overwrite guard: a wrong or reordered ID can't slip
    past it and overwrite an existing file silently.
    """
    target_stem = PSD_ID_TAG_REGEX.sub("", base_stem).strip().lower()
    matches: list[Path] = []
    try:
        for entry in save_dir.iterdir():
            if (entry.is_file() and entry.suffix.lower() == ".psd"
                    and PSD_ID_TAG_REGEX.sub("", entry.stem).strip().lower() == target_stem):
                matches.append(entry)
    except OSError:
        return None
    if not matches:
        return None
    exact = save_dir / f"{base_stem}.psd"
    return exact if exact in matches else max(matches, key=lambda p: p.stat().st_mtime)


class PsdExportRequest(BaseModel):
    title: str
    year: str = ""
    tmdb_id: str = ""                # TMDB id of the item — used to disambiguate existing PSDs that
                                     # share a title/year but carry different {tmdb-…} tags, and to
                                     # tag newly created PSDs the same way IDarr would
    tvdb_id: str = ""                # only tagged for shows (media_type == "tv"), matching IDarr
    imdb_id: str = ""                # only tagged when it starts with "tt", matching IDarr
    media_type: str = ""             # "movie" | "tv" | "collection"
    poster_paths: list[str] = []     # TMDB file_paths e.g. ["/abc.jpg"] — each becomes a separate pixel layer
    backdrop_paths: list[str] = []   # TMDB backdrop file_paths — fit-to-height, no crop, placed below posters
    logo_paths: list[str] = []       # TMDB file_paths — each becomes a separate logo layer
    use_existing: bool = False       # When True: open existing PSD in export folder and inject layers into it
    confirm_overwrite: bool = False  # When True: proceed with a New Export even if a PSD for this title exists


def _fetch_tmdb_image_bytes(path: str, api_key: str) -> bytes:
    """Download a full-resolution TMDB image and return raw bytes.

    If the original is an SVG (common for TMDB logos), converts it to a
    high-quality PNG in-process using cairosvg at 2000px wide.
    """
    url = f"https://image.tmdb.org/t/p/original{path}"
    # api_key is not needed for image CDN but included for consistency / future auth
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise _tmdb_raise_http("image download", status=resp.status_code)

    content = resp.content
    # Detect SVG: TMDB logo originals are often SVG files which Pillow cannot open.
    # Convert to high-quality PNG using cairosvg (Cairo vector renderer) so the
    # logo retains full vector fidelity at whatever size the PSD canvas requires.
    content_type = resp.headers.get("content-type", "").lower()
    is_svg = "svg" in content_type or content.lstrip()[:5].lower().startswith(b"<svg") or content.lstrip()[:38].lower().startswith(b"<?xml")
    if is_svg:
        log_info(LogTags.API, f"SVG logo detected — rendering to PNG via cairosvg at 2000px: {path}")
        try:
            import cairosvg
            # Render at 2000px wide; the logo sizing logic will scale it down appropriately.
            content = cairosvg.svg2png(bytestring=content, output_width=2000)
        except ImportError:
            log_warning(LogTags.API, "cairosvg is not installed — SVG logo cannot be converted; skipping logo")
            return b""
        except Exception as svg_exc:
            log_warning(LogTags.API, f"SVG conversion failed: {svg_exc}; skipping logo")
            return b""

    return content


def _measure_logo_density(logo_pil: Image.Image) -> float:
    """Ratio of non-transparent pixels to total pixels, via the alpha histogram
    (256 bins — fast, no numpy). Drives the sparse/dense sizing adjustments."""
    alpha_hist = logo_pil.split()[3].histogram()
    non_transparent = sum(alpha_hist[11:])  # alpha > 10 = effectively visible
    total_pixels = logo_pil.width * logo_pil.height
    return non_transparent / total_pixels if total_pixels > 0 else 1.0


def compute_logo_geometry(src_w: int, src_h: int, canvas_w: int, canvas_h: int, density: float) -> tuple[int, int, int, int]:
    """Size + place a logo with the Maker Tools formula. Returns (w, h, left, top) in
    canvas pixels: centered horizontally, bottom-anchored at 1352.13px @ 1500h.
    The Photopea plugin's Place Logo button mirrors this in JS — keep them in sync."""
    is_sparse_logo = density < 0.30   # thin/wispy logos → size up
    is_dense_logo  = density > 0.60   # solid/filled logos → tighter height, mild width reduction

    # Placement constants (reference canvas 1000×1500)
    logo_bottom = round(canvas_h * (1352.13 / 1500))   # bottom edge at 1352.13px @ 1500h
    max_logo_top = round(canvas_h * (1100.0 / 1500))   # top must not exceed 1100px @ 1500h
    max_logo_h = logo_bottom - max_logo_top             # = 252px @ 1500h
    max_logo_w = round(canvas_w * (800.0 / 1000))      # hard cap 800px @ 1000w

    # Smooth continuous formula: taller/squarer logos get narrower targets.
    # Ceiling scales with source pixel area (size bucket):
    #   small  (<200K px, e.g. 788×131 banner)  → ceiling 0.85: banner logos fill more canvas
    #   medium (200K–1.5M px, most logos)        → ceiling 0.84: standard
    #   large  (>1.5M px, e.g. 4080×921)         → ceiling 0.93: high-res logos downscale cleanly
    _logo_px = src_w * src_h
    if _logo_px < 200_000:
        _ceiling, _size_label = 0.85, "small"
    elif _logo_px > 1_500_000:
        _ceiling, _size_label = 0.93, "large"
    else:
        _ceiling, _size_label = 0.84, "medium"
    projected_h_at_max = src_h * (max_logo_w / src_w)
    _ref_h = canvas_w * (90.0 / 1000)
    _clamped_ph = max(projected_h_at_max, _ref_h)
    _target_ratio = _ceiling * (_ref_h / _clamped_ph) ** 0.40
    # Floor scales with density so denser logos get a slightly higher minimum width.
    _density_floor = 0.58 + max(0.0, density - 0.30) * 0.10
    target_logo_w = round(canvas_w * max(_density_floor, min(_ceiling, _target_ratio)))
    log_debug(LogTags.API, f"Logo sizing:  proj_h={projected_h_at_max:.0f}px  floor={_density_floor:.3f}  ceiling={_ceiling:.2f} ({_size_label})  base_target_w={target_logo_w}px ({target_logo_w/canvas_w*100:.1f}%)")

    # Scale: aim for target width, clamp by max height, cap by max width.
    # Logos wider than 600px (at 1000w reference) get a tighter 225px height cap.
    wide_threshold = round(canvas_w * (600.0 / 1000))
    effective_max_h = round(canvas_h * (225.0 / 1500)) if target_logo_w > wide_threshold else max_logo_h

    # Apply density adjustments
    if is_sparse_logo:
        # More transparent → more boost. Linear: 0% at threshold (0.30) → +15% at density 0.
        t = (0.30 - density) / 0.30
        density_mult = 1.0 + (t * 0.15)
        target_logo_w = min(round(target_logo_w * density_mult), max_logo_w)
        effective_max_h = min(round(effective_max_h * density_mult), max_logo_h)
        log_debug(LogTags.API, f"Logo density: sparse boost +{(density_mult - 1.0) * 100:.1f}% → target_w={target_logo_w}px  max_h={effective_max_h}px")
    elif is_dense_logo:
        # Dense logos: gentle width reduction (max 10%), aggressive height cap.
        t = (density - 0.60) / (1.0 - 0.60)
        w_mult = 1.0 - (t * 0.10)
        target_logo_w = round(target_logo_w * w_mult)
        effective_max_h = round(canvas_h * (225.0 / 1500) * (1.0 - t * 0.55))
        log_debug(LogTags.API, f"Logo density: dense shrink w×{w_mult:.3f} → target_w={target_logo_w}px  max_h={effective_max_h}px")
    scale = target_logo_w / src_w
    if src_h * scale > effective_max_h:
        scale = effective_max_h / src_h
    if src_w * scale > max_logo_w:
        scale = max_logo_w / src_w

    logo_w = round(src_w * scale)
    logo_h = round(src_h * scale)
    log_debug(LogTags.API, f"Logo result:  {logo_w}x{logo_h}px  scale={scale:.4f}  binding={'height' if src_h * (target_logo_w / src_w) > effective_max_h else 'width'}")

    logo_left = (canvas_w - logo_w) // 2
    logo_top = logo_bottom - logo_h   # bottom-anchored
    return logo_w, logo_h, logo_left, logo_top


def compute_poster_fit_geometry(src_w: int, src_h: int, canvas_w: int, canvas_h: int) -> tuple[int, int, int, int]:
    """Scale a poster to the bordered width (canvas − 25px each side), preserving ratio,
    then center horizontally and top-align at y=25. Returns (w, h, left, top) in canvas px.
    The plugin's Fit Poster button mirrors this in JS — keep them in sync."""
    border_px = 25
    target_w = max(1, canvas_w - (border_px * 2))
    scale = target_w / src_w
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    pos_left = (canvas_w - new_w) // 2
    pos_top = border_px
    return new_w, new_h, pos_left, pos_top


def _build_psd(
    poster_bytes_list: list[bytes],
    logo_bytes_list: list[bytes],
    backdrop_bytes_list: list[bytes] | None = None,
    canvas_w: int = 1000,
    canvas_h: int = 1500,
    fit_within_border: bool = False,
    template_path: Path | None = None,
    title: str = "",
    year: str = "",
) -> bytes:
    """
    Build a layered PSD in memory.

    Template mode (when *template_path* is set):
      - Opens the user's existing PSD template file.
      - Injects each poster image as a separate pixel layer inside the "POSTER" group.
      - Injects each backdrop image at the bottom of the "POSTER" group (fit-to-height, no crop).
      - Injects the logo image as a new PixelLayer inside the group named "LOGO".
      - All other groups/layers in the template (borders, gradients, effects) are preserved.

    Scratch mode (fallback when no template):
      - Creates a blank PSD with poster layers at the bottom and LOGO on top.

    Each poster is cover-filled to the canvas dimensions by default. When
    fit_within_border=True, posters are resized to the bordered width
    (canvas width minus 25px on each side), preserving ratio and top-aligning
    at y=25. The logo is bottom-anchored.
    Backdrop images are scaled to fit the canvas height (no crop) and centred horizontally.
    """
    try:
        from psd_tools import PSDImage
        from psd_tools.api.layers import PixelLayer
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="PSD export requires psd-tools, which is not installed.") from exc

    # ── Open template or create blank canvas ─────────────────────────────────
    if template_path is not None:
        psd = PSDImage.open(str(template_path))
        canvas_w, canvas_h = psd.width, psd.height
    else:
        psd = PSDImage.new("RGB", (canvas_w, canvas_h))

    # Build the base display name used for all layer names
    base_name = f"{title} ({year})" if title and year else title or "Poster"

    # ── POSTER(S) ─────────────────────────────────────────────────────────────
    # Insert in reverse order so first-selected ends up on top of the group stack
    for idx, poster_bytes in enumerate(reversed(poster_bytes_list)):
        layer_name = base_name if len(poster_bytes_list) == 1 else f"{base_name} {len(poster_bytes_list) - idx}"
        poster_pil = Image.open(BytesIO(poster_bytes)).convert("RGB")

        if fit_within_border:
            # Scale to the bordered width, top-aligned and centered.
            new_w, new_h, pos_left, pos_top = compute_poster_fit_geometry(
                poster_pil.width, poster_pil.height, canvas_w, canvas_h)
            poster_pil = poster_pil.resize((new_w, new_h), Image.LANCZOS)
        else:
            # Default behavior: cover-fill to full canvas, then center-crop.
            scale = max(canvas_w / poster_pil.width, canvas_h / poster_pil.height)
            new_w = round(poster_pil.width * scale)
            new_h = round(poster_pil.height * scale)
            poster_pil = poster_pil.resize((new_w, new_h), Image.LANCZOS)
            crop_left = (new_w - canvas_w) // 2
            crop_top = (new_h - canvas_h) // 2
            poster_pil = poster_pil.crop((crop_left, crop_top, crop_left + canvas_w, crop_top + canvas_h))

            pos_left = 0
            pos_top = 0

        poster_layer = PixelLayer.frompil(poster_pil, psd, layer_name=layer_name, top=pos_top, left=pos_left)

        if template_path is not None:
            poster_group = psd.find("POSTER")
            if poster_group is not None:
                poster_group.insert(0, poster_layer)
            else:
                log_warning(LogTags.API, "POSTER group not found in template; inserting at root")
                psd.insert(0, poster_layer)
        else:
            psd._layers.insert(0, poster_layer)

    if poster_bytes_list:
        log_info(LogTags.API, f"Injected {len(poster_bytes_list)} poster(s) into PSD")

    # ── BACKDROP(S) ───────────────────────────────────────────────────────────
    # Fit to canvas height (no crop), centred horizontally.
    # Placed below all poster layers inside the POSTER group (or at root bottom).
    for idx, backdrop_bytes in enumerate(reversed(backdrop_bytes_list or [])):
        bd_count = len(backdrop_bytes_list or [])
        layer_name = f"{base_name} - Backdrop" if bd_count == 1 else f"{base_name} - Backdrop {bd_count - idx}"
        bg_pil = Image.open(BytesIO(backdrop_bytes)).convert("RGB")
        # Scale so height == canvas_h, preserve aspect ratio — no crop
        scale = canvas_h / bg_pil.height
        new_w = round(bg_pil.width * scale)
        bg_pil = bg_pil.resize((new_w, canvas_h), Image.LANCZOS)
        # Centre horizontally (may extend outside canvas bounds on wide images)
        left = (canvas_w - new_w) // 2

        bg_layer = PixelLayer.frompil(bg_pil, psd, layer_name=layer_name, top=0, left=left)

        if template_path is not None:
            poster_group = psd.find("POSTER")
            if poster_group is not None:
                poster_group.append(bg_layer)   # append = bottom of group, below posters
            else:
                log_warning(LogTags.API, "POSTER group not found in template; appending backdrop at root")
                psd.append(bg_layer)
        else:
            psd._layers.append(bg_layer)

    if backdrop_bytes_list:
        log_info(LogTags.API, f"Injected {len(backdrop_bytes_list)} backdrop(s) into PSD (fit-to-height, no crop)")

    # ── LOGO(S) ───────────────────────────────────────────────────────────────
    # Insert in reverse order so first-selected ends up on top of the group stack.
    valid_logo_count = 0
    for logo_idx, logo_bytes in enumerate(reversed(logo_bytes_list)):
        if not logo_bytes:
            continue
        try:
            logo_pil = Image.open(BytesIO(logo_bytes)).convert("RGBA")
        except Exception as img_exc:
            log_warning(LogTags.API, f"Skipping unreadable logo image #{logo_idx + 1}: {img_exc}")
            continue

        # Measure density and size/place the logo with the shared formula. The Photopea
        # plugin's Place Logo button mirrors this formula client-side (it can't reach this
        # http API from inside HTTPS Photopea) — keep the two in sync.
        logo_density = _measure_logo_density(logo_pil)
        density_label = "sparse" if logo_density < 0.30 else ("dense" if logo_density > 0.60 else "normal")
        log_debug(LogTags.API, f"Logo source: {logo_pil.width}x{logo_pil.height}px  density={logo_density:.3f} ({density_label})")
        logo_w, logo_h, logo_left, logo_top = compute_logo_geometry(
            logo_pil.width, logo_pil.height, canvas_w, canvas_h, logo_density)
        logo_pil = logo_pil.resize((logo_w, logo_h), Image.LANCZOS)

        # Convert logo to pure white, preserving the original alpha channel
        _, _, _, alpha = logo_pil.split()
        white = Image.new("RGBA", logo_pil.size, (255, 255, 255, 255))
        white.putalpha(alpha)
        logo_pil = white

        logo_count = len(logo_bytes_list)
        layer_name = f"{base_name} - Logo" if logo_count == 1 else f"{base_name} - Logo {logo_count - logo_idx}"
        logo_layer = PixelLayer.frompil(logo_pil, None, layer_name=layer_name, top=logo_top, left=logo_left)

        if template_path is not None:
            logo_group = psd.find("LOGO")
            if logo_group is not None:
                logo_group.insert(0, logo_layer)
            else:
                log_warning(LogTags.API, "LOGO group not found in template; inserting at root")
                psd.append(logo_layer)
        else:
            psd._layers.append(logo_layer)

        valid_logo_count += 1

    if valid_logo_count:
        log_info(LogTags.API, f"Injected {valid_logo_count} logo(s) into PSD")

    buf = BytesIO()
    psd.save(buf)
    buf.seek(0)
    return buf.read()


def _build_idarr_id_suffix(payload: "PsdExportRequest") -> str:
    """Build the ID-tag suffix (e.g. ``" {tmdb-27205} {imdb-tt1375666}"``) exactly as IDarr
    would for this item: tmdb → tvdb (shows only) → imdb (must start with ``tt``)."""
    parts: list[str] = []
    tmdb_id = str(payload.tmdb_id or "").strip()
    if tmdb_id:
        parts.append(f"tmdb-{tmdb_id}")
    tvdb_id = str(payload.tvdb_id or "").strip()
    if tvdb_id and str(payload.media_type or "").strip().lower() == "tv":
        parts.append(f"tvdb-{tvdb_id}")
    imdb_id = str(payload.imdb_id or "").strip()
    if imdb_id.startswith("tt"):
        parts.append(f"imdb-{imdb_id}")
    return "".join(f" {{{part}}}" for part in parts)


class _PsdMatchAmbiguous(Exception):
    """Raised when several PSDs share the title/year stem but none can be tied to the
    requested TMDB id, so reusing one would risk injecting layers into the wrong file."""


def _find_existing_psd(save_dir: Path, base_stem: str, tmdb_id: str = "") -> Path | None:
    """Find an existing ``.psd`` in *save_dir* for the title/year *base_stem*
    (e.g. ``"Inception (2010)"``), tolerant of IDarr ID tags appended to the name.

    Resolution order (TMDB id is the collision-safe key, mirroring poster matching):
      1. A file carrying the matching ``{tmdb-<id>}`` tag — unambiguous.
      2. The plain untagged ``"<base_stem>.psd"`` — not yet renamed by IDarr.
      3. The single remaining stem-match (tags stripped) when there's exactly one.

    Returns ``None`` when nothing matches. Raises ``_PsdMatchAmbiguous`` when multiple
    stem-matches exist but none can be tied to *tmdb_id* (so we don't guess wrong).
    """
    target = base_stem.strip().lower()
    requested_id = str(tmdb_id or "").strip()

    stem_matches: list[Path] = []
    try:
        for entry in save_dir.iterdir():
            if not entry.is_file() or entry.suffix.lower() != ".psd":
                continue
            if PSD_ID_TAG_REGEX.sub("", entry.stem).strip().lower() == target:
                stem_matches.append(entry)
    except OSError:
        return None

    if not stem_matches:
        return None

    # 1. Prefer the file whose {tmdb-<id>} tag matches the requested item.
    if requested_id:
        id_matches = [
            p for p in stem_matches
            if (m := TMDB_REGEX.search(p.name)) and m.group(1) == requested_id
        ]
        if id_matches:
            return max(id_matches, key=lambda p: p.stat().st_mtime)

    # 2. Fall back to a plain, untagged title/year file (not yet ID-tagged by IDarr).
    untagged = [p for p in stem_matches if not TMDB_REGEX.search(p.name)]
    if untagged:
        exact = save_dir / f"{base_stem}.psd"
        if exact in untagged:
            return exact
        return max(untagged, key=lambda p: p.stat().st_mtime)

    # 3. Exactly one stem-match left → safe to use. More than one with differing ids and no
    #    way to disambiguate → ambiguous; don't guess.
    if len(stem_matches) == 1:
        return stem_matches[0]
    raise _PsdMatchAmbiguous()


@router.post("/tmdb/psd-export")
def tmdb_psd_export(payload: PsdExportRequest, db: Session = Depends(get_db)):
    """Download selected TMDB images and return a layered PSD file.

    Optionally also saves the PSD to the configured export folder.
    """
    try:
        return _tmdb_psd_export_impl(payload, db)
    except HTTPException:
        raise
    except Exception as exc:
        log_error(LogTags.API, f"Unhandled PSD export error: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"PSD export failed: {exc}")


def _fetch_export_images(payload: PsdExportRequest, api_key: str) -> tuple[list[bytes], list[bytes], list[bytes]]:
    """Download the selected poster/backdrop/logo images from TMDB.

    Returns (posters, backdrops, logos); logo entries that fail SVG conversion are dropped.
    """
    try:
        poster_bytes_list = [_fetch_tmdb_image_bytes(p, api_key) for p in payload.poster_paths]
        backdrop_bytes_list = [_fetch_tmdb_image_bytes(p, api_key) for p in payload.backdrop_paths]
        logo_bytes_list = [b for p in payload.logo_paths if (b := _fetch_tmdb_image_bytes(p, api_key))]
    except HTTPException:
        raise
    except Exception as exc:
        log_error(LogTags.API, f"PSD export image fetch failed: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=502, detail=f"Failed to download image from TMDB: {exc}")
    return poster_bytes_list, backdrop_bytes_list, logo_bytes_list


def _resolve_new_export_template(db: Session) -> Path | None:
    """Template for a New Export: user-configured PSD → bundled default → None (scratch)."""
    template_setting = get_setting_value(db, SETTING_PSD_TEMPLATE_PATH)
    if template_setting:
        candidate = Path(template_setting)
        if candidate.is_file():
            return candidate
        log_warning(LogTags.API, f"PSD template not found at configured path: {template_setting}; falling back to default")
    if _DEFAULT_TEMPLATE_PATH.is_file():
        log_info(LogTags.API, "Using bundled default PSD template")
        return _DEFAULT_TEMPLATE_PATH
    return None


def _tmdb_psd_export_impl(payload: PsdExportRequest, db: Session) -> Response:
    # ── Validate image paths (a blank selection is allowed — it yields the
    #    template/existing PSD as-is; paths must start with / and contain no traversal) ──
    all_paths = list(payload.poster_paths) + list(payload.backdrop_paths) + list(payload.logo_paths)
    for p in all_paths:
        if not p.startswith("/") or ".." in p:
            raise HTTPException(status_code=400, detail="Invalid image path.")

    # ── Resolve names + save destination ──
    #   - export_folder set     → save there
    #   - else open_photopea on → save to /config/psd_cache (temp, URL-accessible)
    #   - else                  → stream bytes as a browser download (no saving)
    safe_title = re.sub(r'[<>:"/\\|?*]', "", payload.title).strip()
    # Strip leading dots so the file isn't hidden — scanner/renamer/idarr skip dotfiles
    safe_title = safe_title.lstrip(".").strip()
    base_stem = f"{safe_title} ({payload.year})" if payload.year else safe_title
    filename = f"{base_stem}.psd"
    output_filename = f"{base_stem}{_build_idarr_id_suffix(payload)}.psd"

    export_folder = get_setting_value(db, SETTING_PSD_EXPORT_FOLDER)
    open_photopea = (get_setting_value(db, SETTING_PSD_OPEN_PHOTOPEA) or "").lower() == "true"
    save_dir: Path | None = _psd_storage_dir(db) if (export_folder or open_photopea) else None

    # ── Resolve template + handle conflicts BEFORE fetching any images ──
    #   use_existing=True  → reuse the existing PSD (404 not-found if none)
    #   use_existing=False → guard against silently overwriting an existing title (409 exists),
    #                        then configured template → bundled default → scratch
    template_path: Path | None = None
    if payload.use_existing:
        if save_dir is None:
            raise HTTPException(
                status_code=400,
                detail="No export folder is configured. Configure a PSD export folder in Settings to use this feature.",
            )
        try:
            existing_psd = _find_existing_psd(save_dir, base_stem, payload.tmdb_id)
        except _PsdMatchAmbiguous:
            # Several PSDs share this title/year but none match the TMDB id — don't guess and
            # risk layering into the wrong file. Surface as not-found so the UI offers "create new".
            log_info(LogTags.API, f"Existing PSD ambiguous for {filename} (tmdb-{payload.tmdb_id or '?'}); not reusing", folder=str(save_dir))
            existing_psd = None
        if existing_psd is None:
            return JSONResponse(status_code=404, content={"not_found": True, "expected_filename": filename})
        template_path = existing_psd
        output_filename = existing_psd.name
        log_info(LogTags.API, f"Existing PSD found — adding poster layers: {existing_psd.name}", folder=str(save_dir))
    else:
        if save_dir is not None and not payload.confirm_overwrite:
            existing = _find_psd_by_title(save_dir, base_stem)
            if existing is not None:
                return JSONResponse(status_code=409, content={"exists": True, "existing_filename": existing.name})
        template_path = _resolve_new_export_template(db)

    # ── Commit: require the TMDB key only when images need fetching, then fetch them ──
    api_key = _get_monitor_tmdb_key(db)
    if all_paths and not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured.")
    poster_bytes_list, backdrop_bytes_list, logo_bytes_list = _fetch_export_images(payload, api_key)

    # Optional override to fit posters inside a 25px border while preserving aspect
    # ratio. Canvas/template/backdrop/logo behavior is unchanged.
    fit_within_border = (get_setting_value(db, SETTING_PSD_POSTER_FIT_BORDER) or "").lower() == "true"
    if fit_within_border:
        log_info(LogTags.API, "Poster fit override enabled: fit within 25px border (top-aligned)")

    try:
        psd_bytes = _build_psd(
            poster_bytes_list,
            logo_bytes_list,
            backdrop_bytes_list=backdrop_bytes_list,
            fit_within_border=fit_within_border,
            template_path=template_path,
            title=payload.title,
            year=payload.year,
        )
    except Exception as exc:
        log_error(LogTags.API, f"PSD build failed: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to build PSD: {exc}")

    # ── Persist to the save folder, or stream as a browser download ──
    if save_dir is not None:
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            (save_dir / output_filename).write_bytes(psd_bytes)
            log_info(LogTags.API, f"PSD saved: {output_filename}", folder=str(save_dir))
        except Exception as exc:
            log_warning(LogTags.API, f"PSD save failed: {exc}")
            raise HTTPException(status_code=500, detail=f"Failed to save PSD: {exc}")
        log_user_action("Exported PSD from TMDB images", title=payload.title, year=payload.year)
        # When a password is set, Photopea fetches the PSD via files:[url] and can't send the
        # Bearer header — append a signed, file-scoped, expiring token it can use instead.
        psd_url = f"/api/maker-tools/psd-exports/{output_filename}"
        token_pair = mint_psd_access_token(db, output_filename)
        if token_pair is not None:
            sig, exp = token_pair
            psd_url = f"{psd_url}?token={sig}&exp={exp}"
        return JSONResponse({
            "filename": output_filename,
            "psd_url": psd_url,
            "open_photopea": open_photopea,
        })

    log_user_action("Exported PSD from TMDB images", title=payload.title, year=payload.year)
    return Response(
        content=psd_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{output_filename}"'},
    )


@router.get("/psd-exports/{filename}")
def serve_psd_export(filename: str, token: str = "", exp: str = "", db: Session = Depends(get_db)) -> Response:
    """Serve a previously-saved PSD file from the configured export folder.

    Used by the Photopea integration to load the file directly from the server. This route is
    exempt from the password middleware (Photopea can't send the Bearer header); instead, when
    a password is set it requires a signed, file-scoped ?token=&exp= minted at export time.
    Security: filename is validated (no slashes, no traversal, must end in .psd).
    """
    _validate_psd_filename(filename)
    if not verify_psd_access_token(db, filename, token, exp):
        raise HTTPException(status_code=401, detail="Unauthorized")
    file_path = _psd_storage_dir(db) / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    return Response(
        content=file_path.read_bytes(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.put("/psd-exports/{filename}")
async def upload_psd_to_export_folder(filename: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """Accept a PSD file upload and save it to the configured export folder.

    Used when 'Use Existing PSD' detects no file at the expected path — the user
    can select their local PSD and upload it here so the next export can reuse it.
    Security: filename is validated (no slashes, no traversal, must end in .psd).
    """
    _validate_psd_filename(filename)
    save_dir = _psd_storage_dir(db)

    try:
        save_dir.mkdir(parents=True, exist_ok=True)
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="Empty request body.")
        (save_dir / filename).write_bytes(body)
        log_user_action("Uploaded PSD to export folder", filename=filename, folder=str(save_dir))
    except HTTPException:
        raise
    except Exception as exc:
        log_error(LogTags.API, f"PSD upload failed: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded PSD: {exc}")

    return JSONResponse({"filename": filename, "saved": True})


@router.put("/image-exports/{filename}")
async def save_image_export(filename: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """Write an exported image (JPG/PNG/…) to the configured image export folder.

    Used by the Photopea wrapper's Export-As / JPG button when an image export
    folder is configured (otherwise the wrapper downloads the image in-browser).
    Security: filename is validated (no traversal, must be an image extension).
    """
    _validate_image_filename(filename)
    folder = (get_setting_value(db, SETTING_PSD_IMAGE_EXPORT_FOLDER) or "").strip()
    if not folder:
        raise HTTPException(status_code=400, detail="No image export folder configured.")
    save_dir = Path(folder)

    try:
        save_dir.mkdir(parents=True, exist_ok=True)
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="Empty request body.")
        (save_dir / filename).write_bytes(body)
        log_user_action("Saved exported image", filename=filename, folder=str(save_dir))
    except HTTPException:
        raise
    except Exception as exc:
        log_error(LogTags.API, f"Image export save failed: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to save image: {exc}")

    return JSONResponse({"filename": filename, "saved": True})


@router.post("/monitor/config", response_model=MakerMonitorConfig)
def save_maker_monitor_config(config: MakerMonitorConfig, db: Session = Depends(get_db)) -> MakerMonitorConfig:
    """Persist Maker Tools monitor configuration."""
    normalized = _sanitize_monitor_config(config.model_dump())

    try:
        # If a non-empty TMDB key was submitted, promote it to the global setting
        # and strip it from the per-config JSON so the canonical location is Settings → General.
        incoming_key = normalized.tmdb_api_key.strip()
        if incoming_key:
            upsert_setting(db, "tmdb_api_key", incoming_key)
        normalized = normalized.model_copy(update={"tmdb_api_key": ""})

        upsert_setting(db, SETTING_MAKER_MONITOR_CONFIG, normalized.model_dump_json())
        db.commit()
        log_user_action(
            "Saved Maker Tools monitor config",
            drive_count=len(normalized.drive_ids),
            lookahead_days=normalized.lookahead_days,
        )
        return normalized
    except Exception as exc:
        db.rollback()
        log_error(LogTags.MONITOR, f"Failed saving maker monitor config: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to save monitor configuration")


def _send_monitor_library_report_notification(db: Session, library_result: MakerMonitorLibraryResult) -> None:
    shows_to_report = sorted(library_result.shows, key=lambda item: item.date)
    needed_count = sum(1 for item in library_result.shows if not item.poster_exists)

    lines: list[str] = []
    max_items = 25
    for show in shows_to_report[:max_items]:
        icon = "✅" if show.poster_exists else "🎨"
        ext_text = " (👥 Synced)" if show.external_sources else ""
        season_text = "Specials" if show.season_number == 0 else f"S{show.season_number:02d}"
        lines.append(f"{icon} **[{show.name}]({show.homepage})** ({season_text}){ext_text}\n`{show.date}`")

    if len(shows_to_report) > max_items:
        lines.append(f"\n...and {len(shows_to_report) - max_items} more items.")
    if not lines:
        lines.append("_No upcoming premieres found._")

    send_discord_notification(
        db,
        feature_key="maker_monitor",
        event_type="info",
        title="Posters Needed",
        description=f"**Upcoming Seasons:**\n\n{'\n'.join(lines)}",
        fields=[
            {"name": "📂 Folder", "value": f"**{library_result.library_name}** ({library_result.library_type})", "inline": True},
            {"name": "🔎 Premieres", "value": f"{library_result.premieres_found} Found", "inline": True},
            {"name": "🎨 Action", "value": f"{needed_count} Needed", "inline": True},
        ],
        color=16750592 if needed_count > 0 else 5025616,
        footer_text="Season Monitor Script",
    )


def _send_monitor_summary_notification(
    db: Session,
    response: MakerMonitorRunResponse,
    resolved_config: MakerMonitorConfig,
    selected_drives: list[Drive],
) -> None:
    """Send a single combined Discord summary for a completed Maker Monitor scan.

    Replicates the original per-library report format (show lists with icons, links,
    season numbers, dates) for all libraries combined into one message.
    """
    folders = "\n".join(
        f"• {drive.name} ({str(drive.style_type or '').strip().upper() or 'CUSTOM'})" for drive in selected_drives
    ) or "• None"

    # Build per-library show lists in the same format as the original per-library notifications
    description_parts: list[str] = [
        f"**Upcoming Seasons:**\n**Scanning My Folders:** {folders}\n**Timeframe:** Next {resolved_config.lookahead_days} Days  ·  **Total Scanned:** {response.total_scanned}"
    ]
    max_items = 25
    for lib in response.libraries:
        shows_to_report = sorted(lib.shows, key=lambda item: item.date)

        description_parts.append(f"📂 **{lib.library_name}** ({lib.library_type})")

        lines: list[str] = []
        for show in shows_to_report[:max_items]:
            icon = "✅" if show.poster_exists else "🎨"
            ext_text = " (👥 Synced)" if show.external_sources else ""
            season_text = "Specials" if show.season_number == 0 else f"S{show.season_number:02d}"
            lines.append(
                f"{icon} **[{show.name}]({show.homepage})** ({season_text}){ext_text}\n`{show.date}`"
            )

        if len(shows_to_report) > max_items:
            lines.append(f"\n...and {len(shows_to_report) - max_items} more items.")
        if not lines:
            lines.append("_No upcoming premieres found._")

        description_parts.append("\n".join(lines))

    fields: list[dict[str, Any]] = [
        {"name": "🔎 Premieres", "value": f"{response.total_premieres} Found", "inline": False},
        {"name": "🎨 Action", "value": f"{response.total_needed} Needed", "inline": False},
    ]

    send_discord_notification(
        db,
        feature_key="maker_monitor",
        event_type="info",
        title="Posters Needed",
        description="\n\n".join(description_parts),
        fields=fields,
        color=16750592 if response.total_needed > 0 else 5025616,
        footer_text="Season Monitor Script",
    )


def _send_monitor_error_notification(db: Session, exc: Exception) -> None:
    send_discord_notification(
        db,
        feature_key="maker_monitor",
        event_type="error",
        title="Maker Monitor Failed",
        description=str(exc),
        fields=[],
        color=0xF44336,
    )
    send_major_error_notification(
        db,
        source="maker_monitor",
        message=str(exc),
    )


def run_maker_monitor_scan_internal(
    db: Session,
    *,
    resolved_config: MakerMonitorConfig,
    persist_config: bool,
    notify_discord: bool,
    progress_callback: Callable[[int, str], None] | None = None,
) -> MakerMonitorRunResponse:
    effective_tmdb_key = _get_monitor_tmdb_key(db)
    if not effective_tmdb_key:
        log_warning(LogTags.API, "Maker Tools monitor blocked: TMDB API key is not configured")
        raise HTTPException(status_code=400, detail="TMDB API key is not configured. Add it in Settings → General → API Keys.")
    # Propagate the resolved key so all downstream helpers use the correct value
    resolved_config = resolved_config.model_copy(update={"tmdb_api_key": effective_tmdb_key})
    if not resolved_config.drive_ids:
        raise HTTPException(status_code=400, detail="At least one monitor drive is required")

    selected_drives = _resolve_monitor_drives(db, resolved_config.drive_ids)
    if not selected_drives:
        raise HTTPException(status_code=400, detail="No valid selected drives found")

    if progress_callback:
        progress_callback(5, "Preparing monitor inventory...")

    my_tv_inventory_by_type, my_movie_inventory_by_type = _build_inventory_by_type(selected_drives)

    if progress_callback:
        progress_callback(12, "Monitor inventory ready")

    if persist_config:
        try:
            upsert_setting(db, SETTING_MAKER_MONITOR_CONFIG, resolved_config.model_dump_json())
            db.commit()
        except Exception as exc:
            db.rollback()
            log_error(LogTags.MONITOR, f"Failed persisting monitor config during run: {exc}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail="Failed to save monitor configuration")

    log_info(
        LogTags.MONITOR,
        (
            f"{LogIcons.START} Maker monitor scan started | drives={len(selected_drives)} "
            f"| lookahead_days={resolved_config.lookahead_days} | retention_days={resolved_config.missing_retention_days}"
        ),
        drives=len(selected_drives),
        lookahead_days=resolved_config.lookahead_days,
    )

    library_results: list[MakerMonitorLibraryResult] = []
    discovery_result: MakerMonitorDiscoveryResult | None = None
    total_scanned = 0
    total_premieres = 0
    total_needed = 0
    scanned_tmdb_ids: dict[tuple[str, str], set[str]] = {}
    scanned_seasons: dict[tuple[str, str], dict[str, set[int]]] = {}
    breaker = TmdbCircuitBreaker()  # abort the whole run if TMDB fails too many times in a row

    total_drives = max(1, len(selected_drives))
    drive_progress_start = 15
    drive_progress_end = 75

    for drive_index, drive in enumerate(selected_drives, start=1):
        try:
            if progress_callback:
                progress_callback(
                    drive_progress_start + int(((drive_index - 1) / total_drives) * (drive_progress_end - drive_progress_start)),
                    f"Scanning drive {drive_index}/{total_drives}: {drive.name}",
                )

            drive_path = drive.get_local_path()
            drive_type = str(drive.style_type or "").strip().upper() or "CUSTOM"
            log_info(
                LogTags.MONITOR,
                f"Drive scan started: '{drive.name}' [{drive_type}] at {drive_path}",
                drive_id=drive.id,
                drive_name=drive.name,
                style_type=drive_type,
                path=str(drive_path),
            )
            tv_inventory, _ = _scan_library(str(drive_path), drive.name)
            scanned_tmdb_ids[(drive.name, drive_type)] = set(tv_inventory.keys())
            scanned_seasons[(drive.name, drive_type)] = tv_inventory
            shows: list[MakerMonitorShowResult] = []
            tmdb_ids = list(tv_inventory.keys())
            total_tmdb_ids = len(tmdb_ids)
            progress_step = max(1, total_tmdb_ids // 10) if total_tmdb_ids > 0 else 1

            log_info(
                LogTags.MONITOR,
                (
                    f"TMDB checks started: '{drive.name}' | candidates={total_tmdb_ids} "
                    f"| lookahead_days={resolved_config.lookahead_days}"
                ),
                drive_id=drive.id,
                drive_name=drive.name,
                total_candidates=total_tmdb_ids,
                lookahead_days=resolved_config.lookahead_days,
            )

            # Each premiere check is one independent /tv/{id} lookup (we already
            # have the ids from the filenames), so run them in a small thread pool
            # instead of one-at-a-time — a big library otherwise takes minutes. The
            # HTTP calls run in parallel; results are consumed here on the main
            # thread so the circuit breaker and progress stay single-threaded.
            # 6 workers keeps us comfortably under TMDB's abuse threshold.
            with ThreadPoolExecutor(max_workers=6) as pool:
                future_to_id = {
                    pool.submit(
                        _check_show_status,
                        tmdb_id,
                        seasons,
                        resolved_config.tmdb_api_key,
                        resolved_config.lookahead_days,
                    ): tmdb_id
                    for tmdb_id, seasons in tv_inventory.items()
                }
                index = 0
                for future in as_completed(future_to_id):
                    index += 1
                    tmdb_id = future_to_id[future]
                    try:
                        show_result = future.result()
                        breaker.record_success()
                    except TmdbUpstreamError as err:
                        # A 429 is rate-limiting, not an outage — skip this show but
                        # don't let it trip the abort (the fetch already backed off).
                        if err.status == 429:
                            continue
                        breaker.record_failure(err.reason)
                        if breaker.tripped:
                            for pending in future_to_id:
                                pending.cancel()
                            msg = breaker.abort_message()
                            log_error(LogTags.MONITOR, msg)
                            raise MonitorAborted(msg)
                        continue
                    if show_result and not show_result.poster_exists:
                        ext_sources: list[str] = []
                        type_inventory = my_tv_inventory_by_type.get(drive_type, {})
                        synced_entry = type_inventory.get(tmdb_id)
                        if synced_entry and show_result.season_number in synced_entry.get("seasons", set()):
                            ext_sources = sorted(
                                source
                                for source in synced_entry.get("sources", set())
                                if str(source) != str(drive.name)
                            )
                        show_result.external_sources = ext_sources
                    if show_result:
                        shows.append(show_result)

                    if index % progress_step == 0 or index == total_tmdb_ids:
                        log_info(
                            LogTags.MONITOR,
                            (
                                f"TMDB progress: '{drive.name}' | checked={index}/{total_tmdb_ids} "
                                f"| matches={len(shows)}"
                            ),
                            drive_id=drive.id,
                            drive_name=drive.name,
                            checked=index,
                            total=total_tmdb_ids,
                            matches=len(shows),
                        )

                        if progress_callback and total_tmdb_ids > 0:
                            drive_base = drive_progress_start + int(((drive_index - 1) / total_drives) * (drive_progress_end - drive_progress_start))
                            drive_cap = drive_progress_start + int((drive_index / total_drives) * (drive_progress_end - drive_progress_start))
                            drive_percent = drive_base + int((index / total_tmdb_ids) * max(1, drive_cap - drive_base))
                            progress_callback(
                                drive_percent,
                                f"Checking TMDB ({index}/{total_tmdb_ids}) for {drive.name}",
                            )

            shows.sort(key=lambda item: item.date)
            scanned = len(tv_inventory)
            premieres = len(shows)
            needed = sum(1 for item in shows if not item.poster_exists)

            total_scanned += scanned
            total_premieres += premieres
            total_needed += needed

            library_results.append(
                MakerMonitorLibraryResult(
                    library_name=drive.name,
                    library_type=drive_type,
                    total_scanned=scanned,
                    premieres_found=premieres,
                    posters_needed=needed,
                    shows=shows,
                )
            )

            if progress_callback:
                progress_callback(
                    drive_progress_start + int((drive_index / total_drives) * (drive_progress_end - drive_progress_start)),
                    f"Completed drive {drive_index}/{total_drives}: {drive.name}",
                )

            log_info(
                LogTags.MONITOR,
                (
                    f"Drive scan complete: '{drive.name}' | scanned={scanned} | premieres={premieres} | needed={needed}"
                ),
                drive_id=drive.id,
                drive_name=drive.name,
                scanned=scanned,
                premieres=premieres,
                needed=needed,
            )
        except MonitorAborted:
            raise  # circuit breaker tripped — abort the whole run, don't fall through to the next drive
        except Exception as exc:
            log_error(
                LogTags.MONITOR,
                f"Monitor scan failed for drive '{drive.name}': {exc}\n{traceback.format_exc()}",
                drive_id=drive.id,
                drive_name=drive.name,
            )

    if resolved_config.enable_discovery:
        if progress_callback:
            progress_callback(80, "Running discovery scan...")

        start_date = _monitor_today_local()
        end_date = start_date + timedelta(days=resolved_config.lookahead_days)

        discovery_drives = _resolve_discovery_drives(db)
        if not discovery_drives:
            discovery_drives = selected_drives
            log_warning(LogTags.MONITOR, "No active subscribed drives found for discovery; falling back to monitor drives")

        log_info(
            LogTags.MONITOR,
            f"Discovery inventory build started | drives={len(discovery_drives)}",
            discovery_drives=len(discovery_drives),
        )

        selected_drive_ids = {int(drive.id) for drive in selected_drives}
        external_discovery_drives = [drive for drive in discovery_drives if int(drive.id) not in selected_drive_ids]
        ext_tv_inventory_by_type, ext_movie_inventory_by_type = _build_inventory_by_type(external_discovery_drives)

        discovery_shows = _fetch_discovery_items(
            category="tv",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            config=resolved_config,
            my_tv_inventory_by_type=my_tv_inventory_by_type,
            my_movie_inventory_by_type=my_movie_inventory_by_type,
            ext_tv_inventory_by_type=ext_tv_inventory_by_type,
            ext_movie_inventory_by_type=ext_movie_inventory_by_type,
            breaker=breaker,
        )
        discovery_movies = _fetch_discovery_items(
            category="movie",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            config=resolved_config,
            my_tv_inventory_by_type=my_tv_inventory_by_type,
            my_movie_inventory_by_type=my_movie_inventory_by_type,
            ext_tv_inventory_by_type=ext_tv_inventory_by_type,
            ext_movie_inventory_by_type=ext_movie_inventory_by_type,
            breaker=breaker,
        )
        discovery_result = MakerMonitorDiscoveryResult(shows=discovery_shows, movies=discovery_movies)

        if progress_callback:
            progress_callback(92, "Discovery scan complete")

        log_info(
            LogTags.MONITOR,
            f"Discovery scan complete | series={len(discovery_shows)} | movies={len(discovery_movies)}",
            discovery_series=len(discovery_shows),
            discovery_movies=len(discovery_movies),
        )


    previous_result_payload = _get_monitor_last_result(db)
    if progress_callback:
        progress_callback(95, "Merging retained missing items...")

    today = _monitor_today_local()

    # Build a map of (tmdb_id, season_number) -> air_date from all fresh scan results
    # so carryover items in libraries that missed a show can use the up-to-date air date
    fresh_dates: dict[tuple[str, int], str] = {}
    for lib in library_results:
        for show in lib.shows:
            key = (show.tmdb_id, int(show.season_number))
            if key not in fresh_dates and show.date:
                fresh_dates[key] = show.date

    merged_library_count, merged_item_count = _merge_recent_missing_items(
        current_results=library_results,
        previous_payload=previous_result_payload,
        reference_today=today,
        retention_days=resolved_config.missing_retention_days,
        fresh_dates=fresh_dates,
        scanned_tmdb_ids=scanned_tmdb_ids,
        scanned_seasons=scanned_seasons,
        tmdb_api_key=resolved_config.tmdb_api_key,
    )

    if merged_item_count > 0:
        log_info(
            LogTags.MONITOR,
            (
                f"Merged missing carryover items | libraries={merged_library_count} | items_added={merged_item_count} "
                f"| retention_days={resolved_config.missing_retention_days}"
            ),
            libraries_updated=merged_library_count,
            items_added=merged_item_count,
            retention_days=resolved_config.missing_retention_days,
        )

    total_premieres = sum(library.premieres_found for library in library_results)
    total_needed = sum(library.posters_needed for library in library_results)

    response = MakerMonitorRunResponse(
        lookahead_days=resolved_config.lookahead_days,
        range_start=today.isoformat(),
        range_end=(today + timedelta(days=resolved_config.lookahead_days)).isoformat(),
        total_scanned=total_scanned,
        total_premieres=total_premieres,
        total_needed=total_needed,
        libraries=library_results,
        discovery=discovery_result,
    )

    log_success(
        LogTags.MONITOR,
        (
            f"{LogIcons.SUCCESS} Maker monitor scan complete | scanned={total_scanned} "
            f"| premieres={total_premieres} | needed={total_needed}"
        ),
        total_scanned=total_scanned,
        total_premieres=total_premieres,
        total_needed=total_needed,
    )

    try:
        upsert_setting(db, SETTING_MAKER_MONITOR_LAST_RESULT, response.model_dump_json())
        db.commit()
        log_info(LogTags.MONITOR, "Saved monitor last-result snapshot")
        if progress_callback:
            progress_callback(99, "Finalizing monitor results...")
    except Exception as exc:
        db.rollback()
        log_error(LogTags.MONITOR, f"Failed to persist monitor last-result snapshot: {exc}\n{traceback.format_exc()}")

    if notify_discord:
        _send_monitor_summary_notification(db, response, resolved_config, selected_drives)

    return response


def run_maker_monitor_scan_for_schedule(db: Session) -> None:
    """Execute Maker Monitor scan from scheduler using saved configuration."""
    try:
        resolved_config = _get_monitor_config(db)
        queued_job = create_job(
            db,
            JOB_TYPE_MAKER_MONITOR,
            "Queued Maker Monitor scan (scheduled)",
        )
        job_queue.submit(
            run_maker_monitor_background_job,
            queued_job.id,
            queued_job.id,
            resolved_config.model_dump(),
            False,
            True,
        )
        log_info(LogTags.SCHEDULER, f"Queued scheduled Maker Monitor run as job {queued_job.id}", job_id=queued_job.id)
    except HTTPException as exc:
        log_warning(LogTags.SCHEDULER, f"Skipping scheduled Maker Monitor run: {exc.detail}")
    except Exception as exc:
        log_error(LogTags.SCHEDULER, f"Scheduled Maker Monitor run failed: {exc}\n{traceback.format_exc()}")
        _send_monitor_error_notification(db, exc)
        raise


def run_maker_monitor_background_job(
    job_id: int,
    resolved_config_payload: dict[str, Any],
    persist_config: bool,
    notify_discord: bool,
) -> MakerMonitorRunResponse:
    """Run Maker Monitor in the shared job queue while updating job lifecycle."""
    task_db = SessionLocal()
    job: Job | None = None

    def _report_job_progress(progress: int, message: str) -> None:
        if not job:
            return

        current_progress = int(job.progress or 0)
        next_progress = max(current_progress, min(99, max(0, int(progress))))
        current_message = str(job.message or "")
        next_message = str(message or "").strip() or current_message

        if next_progress == current_progress and next_message == current_message:
            return

        update_job_state(
            task_db,
            job,
            progress=next_progress,
            message=next_message,
        )

    try:
        job = task_db.query(Job).filter(Job.id == job_id).first()
        if job:
            update_job_state(
                task_db,
                job,
                status=JOB_STATUS_RUNNING,
                progress=0,
                message=format_start_message("Maker Monitor scan"),
            )

        resolved_config = _sanitize_monitor_config(resolved_config_payload)
        response = run_maker_monitor_scan_internal(
            task_db,
            resolved_config=resolved_config,
            persist_config=persist_config,
            notify_discord=notify_discord,
            progress_callback=_report_job_progress,
        )

        if job:
            update_job_state(
                task_db,
                job,
                status=JOB_STATUS_COMPLETED,
                progress=100,
                message=format_complete_message("Maker Monitor scan"),
                completed_at=datetime.now().astimezone(),
                error="",
            )

        return response
    except HTTPException as exc:
        if job:
            update_job_state(
                task_db,
                job,
                status=JOB_STATUS_FAILED,
                progress=100,
                error=str(exc.detail),
                completed_at=datetime.now().astimezone(),
            )
        raise
    except Exception as exc:
        if job:
            update_job_state(
                task_db,
                job,
                status=JOB_STATUS_FAILED,
                progress=100,
                error=str(exc),
                completed_at=datetime.now().astimezone(),
            )
        raise
    finally:
        task_db.close()


@router.post("/monitor/run", response_model=MakerMonitorRunQueuedResponse)
def run_maker_monitor_scan(request: MakerMonitorRunRequest, db: Session = Depends(get_db)) -> MakerMonitorRunQueuedResponse:
    """Queue Maker Monitor scan and return immediately with a background job id."""
    resolved_config = _sanitize_monitor_config(request.config.model_dump() if request.config else _get_monitor_config(db).model_dump())

    try:
        queued_job = create_job(
            db,
            JOB_TYPE_MAKER_MONITOR,
            "Queued Maker Monitor scan",
        )

        log_user_action(
            "Started Maker Tools monitor scan",
            job_id=queued_job.id,
            drive_count=len(resolved_config.drive_ids),
            lookahead_days=resolved_config.lookahead_days,
        )

        job_queue.submit(
            run_maker_monitor_background_job,
            queued_job.id,
            queued_job.id,
            resolved_config.model_dump(),
            bool(request.save_config),
            True,
        )

        return MakerMonitorRunQueuedResponse(
            job_id=queued_job.id,
            message="Maker Monitor queued in background. Monitor progress via Jobs.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log_error(LogTags.MONITOR, f"Maker monitor run failed: {exc}\n{traceback.format_exc()}")
        _send_monitor_error_notification(db, exc)
        raise HTTPException(status_code=500, detail="Maker monitor scan failed")