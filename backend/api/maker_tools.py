import json
import os
import re
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.job_queue import job_queue
from core.logging import LogIcons, LogTags, log_error, log_info, log_success, log_user_action, log_warning
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
from models.setting import get_setting, upsert_setting
from services.discord_notifications import send_discord_notification, send_major_error_notification

router = APIRouter(prefix="/api/maker-tools", tags=["maker-tools"])

SETTING_MAKER_MONITOR_CONFIG = "maker_tools_monitor_config"
SETTING_MAKER_MONITOR_LAST_RESULT = "maker_tools_monitor_last_result"
MAKER_MONITOR_DEFAULT_MISSING_RETENTION_DAYS = 2
MAKER_MONITOR_TODAY_GRACE_DAYS = 1
TMDB_REGEX = re.compile(r"\{tmdb-(\d+)\}", re.IGNORECASE)
TVDB_REGEX = re.compile(r"\{tvdb-(\d+)\}", re.IGNORECASE)
SEASON_NUMBER_REGEX = re.compile(r"(?i)\s-\sseason\s*(\d+)")
SPECIALS_REGEX = re.compile(r"(?i)\s-\sspecials")


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
    poster_exists: bool
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


def _merge_recent_missing_items(
    current_results: list[MakerMonitorLibraryResult],
    previous_payload: dict[str, Any],
    reference_today: date,
    retention_days: int,
    fresh_dates: dict[tuple[str, int], str] | None = None,
    scanned_tmdb_ids: dict[tuple[str, str], set[str]] | None = None,
    scanned_seasons: dict[tuple[str, str], dict[str, set[int]]] | None = None,
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

            library.shows.append(
                MakerMonitorShowResult(
                    tmdb_id=tmdb_id,
                    name=str(previous_show.get("name") or "Unknown"),
                    homepage=str(previous_show.get("homepage") or f"https://www.themoviedb.org/tv/{tmdb_id}"),
                    season_number=season_number,
                    date=resolved_date,
                    poster_exists=poster_exists_now,
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
    params = {"api_key": tmdb_api_key, "language": "en-US"}

    try:
        response = requests.get(url, params=params, timeout=10)
    except Exception as exc:
        log_error(LogTags.MONITOR, f"TMDB request failed: {exc}", tmdb_id=tmdb_id)
        return None

    if response.status_code != 200:
        log_warning(LogTags.MONITOR, "TMDB request returned non-200", tmdb_id=tmdb_id, status=response.status_code)
        return None

    try:
        payload = response.json()
    except Exception:
        return None

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
        poster_exists=poster_exists,
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
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                log_warning(
                    LogTags.MONITOR,
                    f"Discovery request non-200: category={category} language={lang_key} status={response.status_code}",
                    category=category,
                    language=lang_key,
                    status=response.status_code,
                )
                continue

            payload = response.json()
            results = payload.get("results") if isinstance(payload, dict) else []
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
        except Exception as exc:
            log_warning(LogTags.MONITOR, f"Discovery scan failed for language '{lang}': {exc}")

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
                statuses=statuses,
            )
        )

    return discovery_results


@router.get("/monitor/config", response_model=MakerMonitorConfig)
def get_maker_monitor_config(db: Session = Depends(get_db)) -> MakerMonitorConfig:
    """Return saved Maker Tools monitor configuration."""
    return _get_monitor_config(db)


@router.get("/monitor/last-result")
def get_maker_monitor_last_result(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the last successful monitor run result for UI reload persistence."""
    return _get_monitor_last_result(db)


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


def _send_monitor_start_notification(db: Session, config: MakerMonitorConfig, selected_drives: list[Drive]) -> None:
    folders = "\n".join(
        f"• {drive.name} ({str(drive.style_type or '').strip().upper() or 'CUSTOM'})" for drive in selected_drives
    ) or "• None"

    fields: list[dict[str, Any]] = [
        {"name": "Scanning My Folders", "value": folders, "inline": True},
        {"name": "Timeframe", "value": f"Next {config.lookahead_days} Days", "inline": True},
        {"name": "Display", "value": "All Items", "inline": True},
    ]

    send_discord_notification(
        db,
        feature_key="maker_monitor",
        event_type="start",
        title="Season Monitor Started",
        fields=fields,
        color=5093631,
        footer_text="Season Monitor Script",
    )


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
        log_warning(LogTags.MODULE, "Maker Tools monitor blocked: TMDB API key is not configured")
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

            for index, (tmdb_id, seasons) in enumerate(tv_inventory.items(), start=1):
                show_result = _check_show_status(
                    tmdb_id=tmdb_id,
                    existing_seasons=seasons,
                    tmdb_api_key=resolved_config.tmdb_api_key,
                    lookahead_days=resolved_config.lookahead_days,
                )
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