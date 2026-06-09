from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import time
import unicodedata
from functools import wraps
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.config import settings as app_settings
from core.logging import LogTags, log_debug, log_info, log_warning
from models.idarr import (
    IdarrAssetCache,
    IdarrPendingMatch,
    build_idarr_asset_key,
    build_idarr_scope_token,
    make_pending_entry_payload,
    normalize_idarr_asset_type,
    upsert_idarr_asset_cache,
    upsert_idarr_pending_match,
)
from models.setting import get_setting
from util.data.normalization import normalize_titles

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".psd"}
UNSUPPORTED_IMAGE_EXTENSIONS = {".svg", ".gif", ".bmp", ".tiff", ".tif", ".ico", ".heic", ".heif", ".avif", ".eps"}
TMDB_ID_REGEX = re.compile(r"tmdb[-_\s]?(\d+)", re.IGNORECASE)
TVDB_ID_REGEX = re.compile(r"tvdb[-_\s]?(\d+)", re.IGNORECASE)
IMDB_ID_REGEX = re.compile(r"imdb[-_\s]?(tt\d+)", re.IGNORECASE)
YEAR_REGEX = re.compile(r"\((\d{4})\)")
MALFORMED_YEAR_OPEN_PAREN_REGEX = re.compile(r"\((\d{4})(?!\d)(?!\s*\))")
MALFORMED_YEAR_CLOSE_PAREN_REGEX = re.compile(r"(?<!\()(?<!\d)(\d{4})\)")
YEAR_IN_COLLECTION_TITLE_REGEX = re.compile(r"\s*\((\d{4})\)(?=\s*Collection\b)", re.IGNORECASE)


def repair_year_parens(stem: str) -> str:
    """Balance a single dangling year parenthesis from an interrupted/partial rename, e.g.
    ``"Title (2025"`` or ``"Title 2025)"`` → ``"Title (2025)"``.

    Only acts when the stem's parentheses are unbalanced, so a legitimately-parenthesised title
    (e.g. ``"Movie (Anthology 2025)"``) is never altered. A bare year with no parens
    (``"Title 2025"``) is intentionally left alone — a 4-digit number isn't necessarily a year
    (e.g. ``"Blade Runner 2049"``), and the ``(YYYY)`` convention is what disambiguates it.
    """
    opens = stem.count("(")
    closes = stem.count(")")
    if opens > closes:
        return MALFORMED_YEAR_OPEN_PAREN_REGEX.sub(r"(\1)", stem, count=1)
    if closes > opens:
        return MALFORMED_YEAR_CLOSE_PAREN_REGEX.sub(r"(\1)", stem, count=1)
    return stem
COLLECTION_REGEX = re.compile(r"collection", re.IGNORECASE)
SEASON_REGEX = re.compile(r"(?:\s*-\s*Season\s*\d+|_Season\d{1,2}|\s*-\s*Specials|_Specials)", re.IGNORECASE)
SEASON_SUFFIX_REGEX = re.compile(r"(?:\s*-\s*Season\s*\d+|_Season\d{1,2}|\s*-\s*Specials|_Specials)", re.IGNORECASE)
ASSET_SUBTYPE_SUFFIX_REGEX = re.compile(r"\s*-\s*(?:logo|backdrop)s?\s*$", re.IGNORECASE)
ID_TAG_BLOCK_REGEX = re.compile(r"\{(?:tmdb|tvdb|imdb)-[^}]+\}", re.IGNORECASE)
SETTING_MAKER_IDARR_IGNORED_TITLES = "maker_tools_idarr_ignored_titles"


class RateLimitException(Exception):
    def __init__(self, period_remaining: float):
        self.period_remaining = max(0.0, float(period_remaining))
        super().__init__(f"Rate limit exceeded. Retry after {self.period_remaining:.2f}s")


def sleep_and_notify(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except RateLimitException as exc:
                log_warning(LogTags.IDARR, f"Rate limit hit, sleeping for {exc.period_remaining:.2f} seconds")
                time.sleep(exc.period_remaining)

    return wrapper


def is_recent(last_checked_at: datetime | None, days: int) -> bool:
    if not isinstance(last_checked_at, datetime):
        return False
    if days <= 0:
        return False
    reference = last_checked_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference >= datetime.now(timezone.utc) - timedelta(days=days)


def ensure_title_year(
    title: str | None,
    year: int | None,
    asset_type: str | None = None,
) -> tuple[str, int | None, str]:
    normalized_title = str(title or "").strip()
    normalized_year = int(year) if isinstance(year, int) else None

    normalized_type = str(asset_type or "").strip().lower()
    if normalized_type not in {"movie", "tv_series", "collection"}:
        normalized_type = "collection" if normalized_year is None else "movie"

    return normalized_title, normalized_year, normalized_type


def normalize_cache_key(title: str | None) -> str:
    return normalize_titles(str(title or "").strip())


@dataclass
class IdarrRunnerResult:
    success: bool
    message: str
    stats: dict[str, Any] | None = None
    warnings: list[str] | None = None
    unmatched_assets: list[dict[str, Any]] | None = None
    details: dict[str, Any] | None = None


class IdarrRunner:
    """Native in-app Idarr workflow implementation.

    This runner is intentionally independent from Poster Renamer and keeps
    all Idarr processing in this dedicated service.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._scope_token: str | None = None
        self._pending_resolved_mid_run: int = 0

    TMDB_MIN_INTERVAL_SECONDS = 0.10
    TMDB_MAX_RETRIES = 3
    _tmdb_last_request_at = 0.0
    CACHE_RENAME_HISTORY_MAX_ENTRIES = 20

    @classmethod
    def _compact_rename_history(cls, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []

        normalized: list[dict[str, str]] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            from_name = str(entry.get("from") or "").strip()
            to_name = str(entry.get("to") or "").strip()
            timestamp = str(entry.get("timestamp") or "").strip()
            if not from_name or not to_name:
                continue
            normalized.append(
                {
                    "from": from_name,
                    "to": to_name,
                    "timestamp": timestamp,
                }
            )

        if len(normalized) <= cls.CACHE_RENAME_HISTORY_MAX_ENTRIES:
            return normalized
        return normalized[-cls.CACHE_RENAME_HISTORY_MAX_ENTRIES :]

    @staticmethod
    def _extract_season_label(path_text: str) -> str | None:
        raw = str(path_text or "")
        normalized = raw.replace("\\", "/")
        season_match = re.search(
            r"(?:^|[^a-z0-9])season(?:\s|_|-)*(\d{1,4})(?:[^a-z0-9]|$)",
            normalized,
            re.IGNORECASE,
        )
        if season_match:
            season_number = season_match.group(1)
            try:
                return f"Season {int(str(season_number))}"
            except Exception:
                return "Season"
        if re.search(r"(?:^|[^a-z0-9])specials(?:[^a-z0-9]|$)", normalized, re.IGNORECASE):
            return "Specials"
        return None

    @staticmethod
    def _format_season_labels(labels: set[str]) -> str:
        if not labels:
            return "single poster"

        def _sort_key(label: str) -> tuple[int, int, str]:
            season_number_match = re.match(r"Season\s+(\d+)", str(label), re.IGNORECASE)
            if season_number_match:
                return (0, int(season_number_match.group(1)), str(label))
            if str(label).lower() == "specials":
                return (1, 0, str(label))
            return (2, 0, str(label))

        return ", ".join(sorted(labels, key=_sort_key))

    @staticmethod
    def _normalize_with_aliases(value: str) -> str:
        canonical_aliases = {
            "&": "and",
            "and": "and",
            "vs.": "versus",
            "vs": "versus",
            "ep.": "episode",
            "ep": "episode",
            "vol.": "volume",
            "vol": "volume",
            "pt.": "part",
            "pt": "part",
            "dr.": "doctor",
            "dr": "doctor",
            "doctor": "doctor",
        }

        normalized = re.sub(r"[’'`ʹʼ]", "", str(value or ""))
        normalized = normalized.replace(":", "").replace("/", "")
        normalized = unicodedata.normalize("NFKD", normalized).encode("ASCII", "ignore").decode()

        words = re.split(r"(\W+)", normalized)
        mapped = [canonical_aliases.get(token.lower(), token.lower()) if token.strip() else token for token in words]
        return re.sub(r"\s+", " ", "".join(mapped)).strip().lower()

    @staticmethod
    def _format_elapsed(elapsed_seconds: int) -> str:
        if elapsed_seconds < 60:
            return f"{elapsed_seconds}s"
        if elapsed_seconds < 3600:
            mins, secs = divmod(elapsed_seconds, 60)
            return f"{mins}m {secs}s"
        hours, rem = divmod(elapsed_seconds, 3600)
        mins, secs = divmod(rem, 60)
        return f"{hours}h {mins}m {secs}s"

    @staticmethod
    def _asset_type_aliases(asset_type: str) -> set[str]:
        normalized = str(asset_type or "").strip().lower()
        if normalized in {"movie", "movies"}:
            return {"movie", "movies"}
        if normalized in {"series", "show", "shows", "tv", "tv_series", "season", "seasons"}:
            return {"series", "show", "shows", "tv", "tv_series", "season", "seasons"}
        if normalized in {"collection", "collections"}:
            return {"collection", "collections"}
        return {normalized} if normalized else set()

    @staticmethod
    def _expand_asset_key_aliases(asset_key: str) -> set[str]:
        raw_key = str(asset_key or "").strip()
        if not raw_key:
            return set()

        parts = raw_key.split("::", 2)
        if len(parts) != 3:
            return {raw_key}

        type_part, title_part, year_part = parts
        aliases = IdarrRunner._asset_type_aliases(type_part)

        movie_aliases = IdarrRunner._asset_type_aliases("movie")
        show_aliases = IdarrRunner._asset_type_aliases("tv_series")
        collection_aliases = IdarrRunner._asset_type_aliases("collection")
        if aliases & (movie_aliases | show_aliases):
            aliases = aliases | movie_aliases | show_aliases

        if "pending" in aliases:
            aliases = aliases | movie_aliases | show_aliases | collection_aliases | {"pending"}

        expanded = {f"{alias}::{title_part}::{year_part}" for alias in aliases if alias}
        expanded.add(raw_key)
        return expanded

    @staticmethod
    def _normalize_asset_type(asset_type: str | None) -> str | None:
        """Delegate to the canonical model-level normalizer."""
        return normalize_idarr_asset_type(asset_type)

    @staticmethod
    def _normalize_filter_type(filter_type: str | None) -> str | None:
        """Delegate to the canonical model-level normalizer (same value set)."""
        return normalize_idarr_asset_type(filter_type)

    def _apply_asset_filters(self, assets: list[dict[str, Any]], config_data: dict[str, Any]) -> list[dict[str, Any]]:
        if not bool(config_data.get("filter", False)):
            return assets

        filtered = list(assets)

        requested_type = self._normalize_filter_type(config_data.get("type"))
        if requested_type:
            filtered = [asset for asset in filtered if str(asset.get("type") or "").strip().lower() == requested_type]

        requested_year = config_data.get("year")
        if isinstance(requested_year, int):
            filtered = [asset for asset in filtered if isinstance(asset.get("year"), int) and int(asset.get("year")) == requested_year]

        requested_contains = str(config_data.get("contains") or "").strip().lower()
        if requested_contains:
            filtered = [
                asset
                for asset in filtered
                if requested_contains in str(asset.get("title") or "").strip().lower()
            ]

        requested_id = str(config_data.get("id") or "").strip().lower()
        if requested_id:
            prefix, _, raw_value = requested_id.partition("-")
            if prefix == "tmdb" and raw_value.isdigit():
                requested_tmdb = int(raw_value)
                filtered = [asset for asset in filtered if isinstance(asset.get("tmdb_id"), int) and int(asset.get("tmdb_id")) == requested_tmdb]
            elif prefix == "tvdb" and raw_value.isdigit():
                requested_tvdb = int(raw_value)
                filtered = [asset for asset in filtered if isinstance(asset.get("tvdb_id"), int) and int(asset.get("tvdb_id")) == requested_tvdb]
            elif prefix == "imdb" and raw_value:
                filtered = [
                    asset
                    for asset in filtered
                    if str(asset.get("imdb_id") or "").strip().lower() == raw_value
                ]

        return filtered

    def filter_items(self, assets: list[dict[str, Any]], config_data: dict[str, Any]) -> list[dict[str, Any]]:
        return self._apply_asset_filters(assets, config_data)

    def _validate_source_dir(self, config_data: dict[str, Any]) -> Path:
        source = Path(str(config_data.get("source_dir") or "").strip())
        if not source.exists() or not source.is_dir():
            raise ValueError(f"Processing source folder not found: {source}")
        return source

    @staticmethod
    def _extract_selected_source_filenames(config_data: dict[str, Any]) -> set[str]:
        raw_values = config_data.get("source_filenames")
        if not isinstance(raw_values, list):
            return set()

        selected: set[str] = set()
        for raw_value in raw_values:
            filename = Path(str(raw_value or "")).name.strip()
            if not filename:
                continue
            selected.add(filename.lower())
        return selected

    @staticmethod
    def _asset_matches_selected_file(asset: dict[str, Any], selected_filenames: set[str]) -> bool:
        file_path = asset.get("file_path")
        if not isinstance(file_path, Path):
            return False
        return file_path.name.strip().lower() in selected_filenames

    def _asset_key_in_scope(self, asset_key: str | None) -> bool:
        key = str(asset_key or "").strip()
        if not key:
            return False
        if self._scope_token:
            return key.endswith(f"::scope={self._scope_token}")
        return "::scope=" not in key

    def _cache_query(self):
        query = self.db.query(IdarrAssetCache)
        if self._scope_token:
            return query.filter(IdarrAssetCache.asset_key.like(f"%::scope={self._scope_token}"))
        return query.filter(~IdarrAssetCache.asset_key.like("%::scope=%"))

    def _pending_query(self):
        query = self.db.query(IdarrPendingMatch)
        if self._scope_token:
            return query.filter(IdarrPendingMatch.asset_key.like(f"%::scope={self._scope_token}"))
        return query.filter(~IdarrPendingMatch.asset_key.like("%::scope=%"))

    def _ensure_idarr_tables_ready(self) -> None:
        try:
            bind = self.db.get_bind()
            if bind is None:
                return
            IdarrAssetCache.__table__.create(bind=bind, checkfirst=True)
            IdarrPendingMatch.__table__.create(bind=bind, checkfirst=True)
        except Exception as exc:
            log_warning(LogTags.IDARR, f"Failed to ensure IDarr cache tables exist: {exc}")
            raise

    @staticmethod
    def _parse_asset(file_path: Path) -> dict[str, Any]:
        stem = repair_year_parens(file_path.stem)
        lower_stem = stem.lower()

        tmdb_match = TMDB_ID_REGEX.search(lower_stem)
        tvdb_match = TVDB_ID_REGEX.search(lower_stem)
        imdb_match = IMDB_ID_REGEX.search(lower_stem)

        year_match = YEAR_REGEX.search(stem)
        year = int(year_match.group(1)) if year_match else None

        clean_title = re.sub(r"\{[^{}]*\}", "", stem)
        if COLLECTION_REGEX.search(lower_stem):
            if not YEAR_IN_COLLECTION_TITLE_REGEX.search(clean_title):
                clean_title = YEAR_REGEX.sub("", clean_title)
        else:
            clean_title = YEAR_REGEX.sub("", clean_title)
        clean_title = clean_title.strip()

        is_series = bool(SEASON_REGEX.search(stem) or tvdb_match)
        is_collection = year is None and not is_series

        if COLLECTION_REGEX.search(lower_stem) or is_collection:
            asset_type = "collection"
            type_is_inferred = False
        elif is_series:
            asset_type = "tv_series"
            type_is_inferred = False  # Explicit hint (season suffix or TVDB tag)
        else:
            asset_type = "movie"
            # No explicit type signal — type is a guess; enrichment will search both endpoints
            type_is_inferred = not bool(tmdb_match or imdb_match)

        has_id = bool(tmdb_match or tvdb_match or imdb_match)

        normalized_title, normalized_year, normalized_type = ensure_title_year(clean_title or stem, year, asset_type)

        return {
            "file_path": file_path,
            "title": normalized_title,
            "year": normalized_year,
            "type": normalized_type,
            "type_is_inferred": type_is_inferred,
            "tmdb_id": int(tmdb_match.group(1)) if tmdb_match else None,
            "tvdb_id": int(tvdb_match.group(1)) if tvdb_match else None,
            "imdb_id": imdb_match.group(1) if imdb_match else None,
            "has_id": has_id,
        }

    @staticmethod
    def parse_file_group(group_assets: list[dict[str, Any]]) -> dict[str, Any]:
        if not group_assets:
            return {
                "title": "",
                "year": None,
                "base_lower": "",
                "tmdb_id": None,
                "tvdb_id": None,
                "imdb_id": None,
            }

        base_stem = str(group_assets[0]["file_path"].stem)
        base_group_stem = SEASON_REGEX.split(base_stem)[0].strip() or base_stem
        base_group_stem_lower = base_group_stem.lower()

        title = re.sub(r"{(tmdb|tvdb|imdb)-[^}]+}", "", base_group_stem)
        if COLLECTION_REGEX.search(base_group_stem_lower):
            if not YEAR_IN_COLLECTION_TITLE_REGEX.search(title):
                title = YEAR_REGEX.sub("", title)
            title = title.strip()
        else:
            title = YEAR_REGEX.sub("", title).strip()

        year_match = YEAR_REGEX.search(base_group_stem)
        group_year = int(year_match.group(1)) if year_match else None

        base_lower = base_group_stem.lower()
        tmdb_match = TMDB_ID_REGEX.search(base_lower)
        tvdb_match = TVDB_ID_REGEX.search(base_lower)
        imdb_match = IMDB_ID_REGEX.search(base_lower)

        return {
            "title": title,
            "year": group_year,
            "base_lower": base_lower,
            "tmdb_id": int(tmdb_match.group(1)) if tmdb_match else None,
            "tvdb_id": int(tvdb_match.group(1)) if tvdb_match else None,
            "imdb_id": imdb_match.group(1) if imdb_match else None,
        }

    def _scan_assets(self, source_dir: Path) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        grouped_indexes: dict[str, list[int]] = {}
        cache_rows = self._load_all_cache_rows("index build")
        filename_cache_index = self._load_cache_filename_index(rows=cache_rows)
        group_cache_hints = self._load_group_cache_hints(rows=cache_rows)

        for entry in sorted(source_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in IMAGE_EXTENSIONS:
                continue

            asset = self._parse_asset(entry)
            assets.append(asset)

            group_title = SEASON_REGEX.split(entry.stem)[0].strip()
            if not group_title:
                group_title = entry.stem
            group_key = self._normalize_with_aliases(group_title)
            grouped_indexes.setdefault(group_key, []).append(len(assets) - 1)

        for indexes in grouped_indexes.values():
            if not indexes:
                continue

            group_assets = [assets[index] for index in indexes]
            parsed_group = self.parse_file_group(group_assets)
            title = str(parsed_group.get("title") or "")
            group_year = parsed_group.get("year") if isinstance(parsed_group.get("year"), int) else None
            base_lower = str(parsed_group.get("base_lower") or "")
            group_tmdb_id = parsed_group.get("tmdb_id") if isinstance(parsed_group.get("tmdb_id"), int) else None
            group_tvdb_id = parsed_group.get("tvdb_id") if isinstance(parsed_group.get("tvdb_id"), int) else None
            group_imdb_id = parsed_group.get("imdb_id") if isinstance(parsed_group.get("imdb_id"), str) else None
            cache_type: str | None = None

            group_cache_hint: dict[str, Any] | None = None
            normalized_group_title = self._normalize_with_aliases(title)
            if isinstance(group_tmdb_id, int):
                tmdb_candidates = group_cache_hints.get("by_tmdb", {}).get(group_tmdb_id, [])
                if isinstance(tmdb_candidates, list):
                    best_hint: dict[str, Any] | None = None
                    best_rank = -1
                    for candidate in tmdb_candidates:
                        if not isinstance(candidate, dict):
                            continue
                        candidate_title = str(candidate.get("normalized_title") or "")
                        candidate_year = candidate.get("year") if isinstance(candidate.get("year"), int) else None
                        exact_title = bool(candidate_title and candidate_title == normalized_group_title)
                        exact_year = bool(isinstance(group_year, int) and isinstance(candidate_year, int) and group_year == candidate_year)

                        rank = int(candidate.get("_rank") or 0)
                        if exact_title and exact_year:
                            rank += 100
                        elif exact_title:
                            rank += 50

                        if rank > best_rank:
                            best_rank = rank
                            best_hint = candidate
                    group_cache_hint = best_hint
            if not group_cache_hint:
                hint_key = f"{normalized_group_title}::{group_year if isinstance(group_year, int) else ''}"
                candidate_hint = group_cache_hints.get("by_title_year", {}).get(hint_key)
                if candidate_hint is not None:
                    # Guard: if the group already has a known TMDB ID and the title/year
                    # hint has a *different* TMDB ID, this is a distinct title that merely
                    # shares the same title/year (e.g. two movies titled "Spiral (2019)").
                    # Skip the hint so we don't inherit the wrong imdb_id from it.
                    hint_tmdb = int(candidate_hint["tmdb_id"]) if isinstance(candidate_hint.get("tmdb_id"), int) else None
                    if group_tmdb_id is None or hint_tmdb is None or group_tmdb_id == hint_tmdb:
                        group_cache_hint = candidate_hint

            if group_cache_hint:
                cache_type = self._normalize_asset_type(str(group_cache_hint.get("asset_type") or ""))
                if group_tmdb_id is None and isinstance(group_cache_hint.get("tmdb_id"), int):
                    group_tmdb_id = int(group_cache_hint["tmdb_id"])
                if group_tvdb_id is None and isinstance(group_cache_hint.get("tvdb_id"), int):
                    group_tvdb_id = int(group_cache_hint["tvdb_id"])
                if group_imdb_id is None and isinstance(group_cache_hint.get("imdb_id"), str):
                    candidate_imdb = str(group_cache_hint["imdb_id"])
                    if candidate_imdb.startswith("tt"):
                        group_imdb_id = candidate_imdb

            if group_tmdb_id is None:
                group_tmdb_id = next(
                    (int(asset["tmdb_id"]) for asset in group_assets if isinstance(asset.get("tmdb_id"), int)),
                    None,
                )
            if group_tvdb_id is None:
                group_tvdb_id = next(
                    (int(asset["tvdb_id"]) for asset in group_assets if isinstance(asset.get("tvdb_id"), int)),
                    None,
                )
            if group_imdb_id is None:
                group_imdb_id = next(
                    (
                        str(asset["imdb_id"])
                        for asset in group_assets
                        if isinstance(asset.get("imdb_id"), str) and str(asset.get("imdb_id")).startswith("tt")
                    ),
                    None,
                )

            if group_tmdb_id is None or group_tvdb_id is None or group_imdb_id is None:
                cache_hit: dict[str, Any] | None = None
                for asset in group_assets:
                    file_name_key = str(asset["file_path"].name).strip().lower()
                    if not file_name_key:
                        continue
                    indexed = filename_cache_index.get(file_name_key)
                    if indexed:
                        cache_hit = indexed
                        break

                if cache_hit:
                    cache_type = cache_type or self._normalize_asset_type(str(cache_hit.get("asset_type") or ""))
                    if group_tmdb_id is None and isinstance(cache_hit.get("tmdb_id"), int):
                        group_tmdb_id = int(cache_hit["tmdb_id"])
                    if group_tvdb_id is None and isinstance(cache_hit.get("tvdb_id"), int):
                        group_tvdb_id = int(cache_hit["tvdb_id"])
                    if group_imdb_id is None and isinstance(cache_hit.get("imdb_id"), str):
                        candidate_imdb = str(cache_hit["imdb_id"])
                        if candidate_imdb.startswith("tt"):
                            group_imdb_id = candidate_imdb

            is_series = any(SEASON_REGEX.search(str(asset["file_path"].stem)) for asset in group_assets) or bool(group_tvdb_id)
            is_collection = group_year is None and not is_series

            if is_series and cache_type == "movie":
                group_tmdb_id = None
                group_imdb_id = None

            if is_series:
                group_type = "tv_series"
            elif cache_type == "collection":
                group_type = "collection"
            elif cache_type == "tv_series":
                group_type = "tv_series"
            elif cache_type == "movie":
                group_type = "movie"
            elif COLLECTION_REGEX.search(base_lower) or is_collection:
                group_type = "collection"
            else:
                group_type = "movie"

            normalized_group_title, normalized_group_year, normalized_group_type = ensure_title_year(
                title,
                group_year,
                group_type,
            )

            for asset in group_assets:
                if normalized_group_title:
                    asset["title"] = normalized_group_title
                if normalized_group_year is not None:
                    asset["year"] = normalized_group_year

                # If this asset carries a different TMDB ID than the group's consensus,
                # it's a distinct entity sharing the same title/year (e.g. a movie poster
                # alongside TV show files). Don't override its type or cross-contaminate
                # with the group's TVDB ID.
                _asset_tmdb = asset.get("tmdb_id") if isinstance(asset.get("tmdb_id"), int) else None
                _conflicts_with_group = (
                    _asset_tmdb is not None
                    and isinstance(group_tmdb_id, int)
                    and _asset_tmdb != group_tmdb_id
                )

                if not _conflicts_with_group:
                    asset["type"] = normalized_group_type
                    _group_has_resolving_id = (
                        isinstance(group_tmdb_id, int)
                        or isinstance(group_tvdb_id, int)
                        or (isinstance(group_imdb_id, str) and group_imdb_id.startswith("tt"))
                    )
                    if is_series or _group_has_resolving_id:
                        asset["type_is_inferred"] = False

                # Only propagate group IDs (tmdb/imdb) to assets that have no IDs of their
                # own when those assets are explicitly season/specials sub-items of a series.
                # A plain base-poster file (no season pattern) could be a different movie
                # with the same title/year — propagating would silently mis-identify it.
                # tvdb_id is series-specific and only present when the group is confirmed TV,
                # so propagating it is always safe unless this asset conflicts with the group.
                _asset_is_season = bool(SEASON_REGEX.search(str(asset["file_path"].stem)))
                if not isinstance(asset.get("tmdb_id"), int) and isinstance(group_tmdb_id, int):
                    if _asset_is_season:
                        asset["tmdb_id"] = group_tmdb_id
                if not isinstance(asset.get("tvdb_id"), int) and isinstance(group_tvdb_id, int):
                    if not _conflicts_with_group:
                        asset["tvdb_id"] = group_tvdb_id
                if not isinstance(asset.get("imdb_id"), str) and isinstance(group_imdb_id, str):
                    if _asset_is_season:
                        asset["imdb_id"] = group_imdb_id

                asset["has_id"] = bool(asset.get("tmdb_id") or asset.get("tvdb_id") or asset.get("imdb_id"))

        return assets

    def scan_files_in_flat_folder(self, source_dir: Path) -> list[dict[str, Any]]:
        return self._scan_assets(source_dir)

    @staticmethod
    def _parse_asset_no_season_hint(file_path: Path) -> dict[str, Any]:
        """Like _parse_asset but omits the season-suffix regex as a type hint.

        Used for asset drive files (logos, backdrops) where filenames never carry
        season suffixes, so SEASON_REGEX would only produce false positives.
        Type detection relies solely on ID tags (tvdb → tv_series) and TMDB lookup.
        """
        stem = repair_year_parens(file_path.stem)
        lower_stem = stem.lower()

        tmdb_match = TMDB_ID_REGEX.search(lower_stem)
        tvdb_match = TVDB_ID_REGEX.search(lower_stem)
        imdb_match = IMDB_ID_REGEX.search(lower_stem)

        year_match = YEAR_REGEX.search(stem)
        year = int(year_match.group(1)) if year_match else None

        clean_title = re.sub(r"\{[^{}]*\}", "", stem)
        if COLLECTION_REGEX.search(lower_stem):
            if not YEAR_IN_COLLECTION_TITLE_REGEX.search(clean_title):
                clean_title = YEAR_REGEX.sub("", clean_title)
        else:
            clean_title = YEAR_REGEX.sub("", clean_title)
        # Strip " - logo" / " - backdrop" subtype labels from the title
        clean_title = ASSET_SUBTYPE_SUFFIX_REGEX.sub("", clean_title).strip()

        # No SEASON_REGEX hint — only a TVDB tag marks the file as a series
        is_series = bool(tvdb_match)
        is_collection = year is None and not is_series

        if COLLECTION_REGEX.search(lower_stem) or is_collection:
            asset_type = "collection"
            type_is_inferred = False
        elif is_series:
            asset_type = "tv_series"
            type_is_inferred = False
        else:
            asset_type = "movie"
            type_is_inferred = not bool(tmdb_match or imdb_match)

        has_id = bool(tmdb_match or tvdb_match or imdb_match)
        normalized_title, normalized_year, normalized_type = ensure_title_year(clean_title or stem, year, asset_type)

        return {
            "file_path": file_path,
            "title": normalized_title,
            "year": normalized_year,
            "type": normalized_type,
            "type_is_inferred": type_is_inferred,
            "tmdb_id": int(tmdb_match.group(1)) if tmdb_match else None,
            "tvdb_id": int(tvdb_match.group(1)) if tvdb_match else None,
            "imdb_id": imdb_match.group(1) if imdb_match else None,
            "has_id": has_id,
        }

    def _scan_assets_for_asset_drive(self, source_dir: Path) -> list[dict[str, Any]]:
        """Scan a flat directory with asset-drive matching rules (no season-suffix hints).

        Applies cache hints per file for ID enrichment but skips season grouping
        entirely — each file is an independent logo or backdrop for a single title.
        """
        assets: list[dict[str, Any]] = []
        cache_rows = self._load_all_cache_rows("index build")
        filename_cache_index = self._load_cache_filename_index(rows=cache_rows)
        group_cache_hints = self._load_group_cache_hints(rows=cache_rows)

        for entry in sorted(source_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            assets.append(self._parse_asset_no_season_hint(entry))

        for asset in assets:
            title = str(asset.get("title") or "")
            year = asset.get("year")
            normalized_title = self._normalize_with_aliases(title)
            asset_tmdb_id = asset.get("tmdb_id") if isinstance(asset.get("tmdb_id"), int) else None
            asset_tvdb_id = asset.get("tvdb_id") if isinstance(asset.get("tvdb_id"), int) else None
            asset_imdb_id = asset.get("imdb_id") if isinstance(asset.get("imdb_id"), str) else None
            cache_type: str | None = None

            cache_hint: dict[str, Any] | None = None
            if isinstance(asset_tmdb_id, int):
                tmdb_candidates = group_cache_hints.get("by_tmdb", {}).get(asset_tmdb_id, [])
                if isinstance(tmdb_candidates, list) and tmdb_candidates:
                    best_hint: dict[str, Any] | None = None
                    best_rank = -1
                    for candidate in tmdb_candidates:
                        if not isinstance(candidate, dict):
                            continue
                        candidate_title = str(candidate.get("normalized_title") or "")
                        candidate_year = candidate.get("year") if isinstance(candidate.get("year"), int) else None
                        exact_title = bool(candidate_title and candidate_title == normalized_title)
                        exact_year = bool(isinstance(year, int) and isinstance(candidate_year, int) and year == candidate_year)
                        rank = int(candidate.get("_rank") or 0)
                        if exact_title and exact_year:
                            rank += 100
                        elif exact_title:
                            rank += 50
                        if rank > best_rank:
                            best_rank = rank
                            best_hint = candidate
                    cache_hint = best_hint

            if not cache_hint:
                hint_key = f"{normalized_title}::{year if isinstance(year, int) else ''}"
                candidate_hint = group_cache_hints.get("by_title_year", {}).get(hint_key)
                if candidate_hint is not None:
                    hint_tmdb = int(candidate_hint["tmdb_id"]) if isinstance(candidate_hint.get("tmdb_id"), int) else None
                    if asset_tmdb_id is None or hint_tmdb is None or asset_tmdb_id == hint_tmdb:
                        cache_hint = candidate_hint

            if cache_hint:
                cache_type = self._normalize_asset_type(str(cache_hint.get("asset_type") or ""))
                if asset_tmdb_id is None and isinstance(cache_hint.get("tmdb_id"), int):
                    asset_tmdb_id = int(cache_hint["tmdb_id"])
                if asset_tvdb_id is None and isinstance(cache_hint.get("tvdb_id"), int):
                    asset_tvdb_id = int(cache_hint["tvdb_id"])
                if asset_imdb_id is None and isinstance(cache_hint.get("imdb_id"), str):
                    candidate_imdb = str(cache_hint["imdb_id"])
                    if candidate_imdb.startswith("tt"):
                        asset_imdb_id = candidate_imdb

            if asset_tmdb_id is None or asset_tvdb_id is None or asset_imdb_id is None:
                file_name_key = str(asset["file_path"].name).strip().lower()
                indexed = filename_cache_index.get(file_name_key)
                if indexed:
                    cache_type = cache_type or self._normalize_asset_type(str(indexed.get("asset_type") or ""))
                    if asset_tmdb_id is None and isinstance(indexed.get("tmdb_id"), int):
                        asset_tmdb_id = int(indexed["tmdb_id"])
                    if asset_tvdb_id is None and isinstance(indexed.get("tvdb_id"), int):
                        asset_tvdb_id = int(indexed["tvdb_id"])
                    if asset_imdb_id is None and isinstance(indexed.get("imdb_id"), str):
                        candidate_imdb = str(indexed["imdb_id"])
                        if candidate_imdb.startswith("tt"):
                            asset_imdb_id = candidate_imdb

            # Re-determine type using enriched IDs (no season regex)
            is_series = bool(asset_tvdb_id)
            is_collection = year is None and not is_series

            if is_series:
                resolved_type = "tv_series"
            elif cache_type == "collection":
                resolved_type = "collection"
            elif cache_type == "tv_series":
                resolved_type = "tv_series"
            elif cache_type == "movie":
                resolved_type = "movie"
            elif COLLECTION_REGEX.search(str(asset.get("title") or "").lower()) or is_collection:
                resolved_type = "collection"
            else:
                resolved_type = "movie"

            asset["type"] = resolved_type
            _has_resolving_id = (
                isinstance(asset_tmdb_id, int)
                or isinstance(asset_tvdb_id, int)
                or (isinstance(asset_imdb_id, str) and asset_imdb_id.startswith("tt"))
            )
            if is_series or _has_resolving_id:
                asset["type_is_inferred"] = False
            if isinstance(asset_tmdb_id, int):
                asset["tmdb_id"] = asset_tmdb_id
            if isinstance(asset_tvdb_id, int):
                asset["tvdb_id"] = asset_tvdb_id
            if isinstance(asset_imdb_id, str):
                asset["imdb_id"] = asset_imdb_id
            asset["has_id"] = bool(asset.get("tmdb_id") or asset.get("tvdb_id") or asset.get("imdb_id"))

        return assets

    def scan_asset_drive_subfolders(self, source_dir: Path) -> list[dict[str, Any]]:
        """Scan logos/ and backdrops/ subfolders of an asset drive.

        Each returned asset dict includes an ``asset_subtype`` key set to either
        ``"logo"`` or ``"backdrop"`` based on which subfolder the file came from.
        """
        assets: list[dict[str, Any]] = []
        for subtype, subfolder_name in [("logo", "logos"), ("backdrop", "backdrops")]:
            subfolder = source_dir / subfolder_name
            if not subfolder.exists() or not subfolder.is_dir():
                log_info(
                    LogTags.IDARR,
                    f"Asset drive subfolder '{subfolder_name}' not found, skipping",
                    path=str(subfolder),
                )
                continue
            subfolder_assets = self._scan_assets_for_asset_drive(subfolder)
            for asset in subfolder_assets:
                asset["asset_subtype"] = subtype
            assets.extend(subfolder_assets)
            log_info(
                LogTags.IDARR,
                f"Asset drive: scanned {len(subfolder_assets)} {subtype}(s)",
                path=str(subfolder),
            )
        return assets

    def _load_cache_filename_index(self, rows: list | None = None) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        if rows is None:
            rows = self._load_all_cache_rows("filename index")
        if not rows:
            return index

        for row in rows:
            payload: dict[str, Any] = {}
            if isinstance(row.payload_json, str) and row.payload_json.strip():
                try:
                    parsed = json.loads(row.payload_json)
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}

            file_names: list[str] = []
            current_filenames = payload.get("current_filenames")
            original_filenames = payload.get("original_filenames")
            payload_files = payload.get("files")

            if isinstance(current_filenames, list):
                file_names.extend([str(value) for value in current_filenames if isinstance(value, str)])
            if isinstance(original_filenames, list):
                file_names.extend([str(value) for value in original_filenames if isinstance(value, str)])
            if isinstance(payload_files, list):
                file_names.extend([str(value) for value in payload_files if isinstance(value, str)])
            elif isinstance(payload_files, str):
                file_names.extend([part.strip() for part in payload_files.split(";") if part.strip()])

            candidate = {
                "asset_type": str(row.asset_type or "").strip().lower(),
                "tmdb_id": row.tmdb_id if isinstance(row.tmdb_id, int) else None,
                "tvdb_id": row.tvdb_id if isinstance(row.tvdb_id, int) else None,
                "imdb_id": str(row.imdb_id) if isinstance(row.imdb_id, str) else None,
            }
            candidate_rank = sum(1 for key in ("tmdb_id", "tvdb_id", "imdb_id") if candidate.get(key))

            for file_name in file_names:
                file_key = Path(file_name).name.strip().lower()
                if not file_key:
                    continue

                existing = index.get(file_key)
                if not existing:
                    index[file_key] = candidate
                    continue

                existing_rank = sum(1 for key in ("tmdb_id", "tvdb_id", "imdb_id") if existing.get(key))
                if candidate_rank > existing_rank:
                    index[file_key] = candidate

        return index

    def _load_group_cache_hints(self, rows: list | None = None) -> dict[str, dict[Any, Any]]:
        by_tmdb: dict[int, list[dict[str, Any]]] = {}
        by_title_year: dict[str, dict[str, Any]] = {}
        if rows is None:
            rows = self._load_all_cache_rows("group hints")
        if not rows:
            return {"by_tmdb": by_tmdb, "by_title_year": by_title_year}

        for row in rows:
            normalized_type = self._normalize_asset_type(str(row.asset_type or ""))
            candidate = {
                "asset_type": normalized_type,
                "tmdb_id": row.tmdb_id if isinstance(row.tmdb_id, int) else None,
                "tvdb_id": row.tvdb_id if isinstance(row.tvdb_id, int) else None,
                "imdb_id": str(row.imdb_id) if isinstance(row.imdb_id, str) else None,
                "normalized_title": self._normalize_with_aliases(str(row.title or "").strip()),
                "year": row.year if isinstance(row.year, int) else None,
            }
            rank = sum(1 for key in ("tmdb_id", "tvdb_id", "imdb_id") if candidate.get(key))
            candidate["_rank"] = rank

            if isinstance(candidate.get("tmdb_id"), int):
                tmdb_id = int(candidate["tmdb_id"])
                by_tmdb.setdefault(tmdb_id, []).append(candidate)

            normalized_title = str(candidate.get("normalized_title") or "")
            if normalized_title:
                title_year_key = f"{normalized_title}::{row.year if isinstance(row.year, int) else ''}"
                existing_title_year = by_title_year.get(title_year_key)
                existing_rank = sum(
                    1 for key in ("tmdb_id", "tvdb_id", "imdb_id") if existing_title_year and existing_title_year.get(key)
                )
                if not existing_title_year or rank > existing_rank:
                    by_title_year[title_year_key] = candidate

        return {"by_tmdb": by_tmdb, "by_title_year": by_title_year}

    @staticmethod
    def _classify_search_match(
        *,
        candidate: dict[str, Any],
        title: str,
        year: int | None,
    ) -> dict[str, Any]:
        embedded = candidate.get("_idarr_match")
        if isinstance(embedded, dict):
            return {
                "confidence": str(embedded.get("confidence") or "unknown"),
                "reason": str(embedded.get("reason") or "rank_fallback"),
                "score": int(embedded.get("score") or 0),
                "candidate_title": str(embedded.get("candidate_title") or candidate.get("title") or candidate.get("name") or "").strip(),
                "candidate_year": embedded.get("candidate_year") if isinstance(embedded.get("candidate_year"), int) else None,
            }

        candidate_title = str(candidate.get("title") or candidate.get("name") or "").strip()
        candidate_year_value = candidate.get("release_date") or candidate.get("first_air_date")
        candidate_year = int(candidate_year_value[:4]) if isinstance(candidate_year_value, str) and candidate_year_value[:4].isdigit() else None

        requested_normalized = IdarrRunner._normalize_with_aliases(title)
        candidate_normalized = IdarrRunner._normalize_with_aliases(candidate_title)
        exact_title = bool(requested_normalized and candidate_normalized and requested_normalized == candidate_normalized)
        title_close = bool(
            requested_normalized
            and candidate_normalized
            and (requested_normalized in candidate_normalized or candidate_normalized in requested_normalized)
        )
        year_match = bool(isinstance(year, int) and isinstance(candidate_year, int) and year == candidate_year)
        year_close = bool(
            isinstance(year, int)
            and isinstance(candidate_year, int)
            and abs(year - candidate_year) <= 1
        )

        score = 0
        reason = "rank_fallback"
        confidence = "low"

        if exact_title and year_match:
            score = 95
            reason = "exact"
            confidence = "high"
        elif exact_title and (not isinstance(year, int) or year_close):
            score = 82
            reason = "exact"
            confidence = "high"
        elif exact_title:
            score = 68
            reason = "exact_year_mismatch"
            confidence = "medium"
        elif title_close and year_match:
            score = 74
            reason = "close_title_year"
            confidence = "medium"
        elif title_close:
            score = 62
            reason = "close_title"
            confidence = "medium"
        elif year_match:
            score = 50
            reason = "year_match_only"
            confidence = "low"

        return {
            "confidence": confidence,
            "reason": reason,
            "score": score,
            "candidate_title": candidate_title,
            "candidate_year": candidate_year,
        }

    @staticmethod
    def _is_low_confidence_alternate_match(
        *,
        match_reason: str,
        match_confidence: str,
        match_score: int,
    ) -> bool:
        alternate_reasons = {
            "alternate_title",
            "alternate_transform",
            "fuzzy_alternate",
            "fuzzy_alternate_transform",
            "exact_year_mismatch",
            "exact_year_mismatch_transform",
            "original_title_year_mismatch",
            "original_title_year_mismatch_transform",
        }
        normalized_reason = str(match_reason or "").strip().lower()
        if normalized_reason not in alternate_reasons:
            return False

        normalized_confidence = str(match_confidence or "").strip().lower()
        if normalized_confidence in {"low", "unknown"}:
            return True

        return int(match_score or 0) < 88

    @staticmethod
    def _candidate_year(candidate: dict[str, Any]) -> int | None:
        value = candidate.get("release_date") or candidate.get("first_air_date")
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
        return None

    @classmethod
    def _match_tmdb_candidate_by_id(
        cls,
        *,
        candidates: list[dict[str, Any]],
        tmdb_id: int | None,
        tvdb_id: int | None,
        imdb_id: str | None,
    ) -> dict[str, Any] | None:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if isinstance(tmdb_id, int) and isinstance(candidate.get("id"), int) and int(candidate.get("id")) == tmdb_id:
                return candidate
            if isinstance(tvdb_id, int) and isinstance(candidate.get("tvdb_id"), int) and int(candidate.get("tvdb_id")) == tvdb_id:
                return candidate
            if isinstance(imdb_id, str) and imdb_id.strip() and str(candidate.get("imdb_id") or "").strip() == imdb_id.strip():
                return candidate
        return None

    @classmethod
    def _exact_tmdb_title_year_match(
        cls,
        *,
        candidates: list[dict[str, Any]],
        title: str,
        year: int | None,
    ) -> dict[str, Any] | None:
        requested = cls._normalize_with_aliases(title)
        if not requested:
            return None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_title = str(candidate.get("title") or candidate.get("name") or "")
            if cls._normalize_with_aliases(candidate_title) != requested:
                continue
            candidate_year = cls._candidate_year(candidate)
            if isinstance(year, int) and isinstance(candidate_year, int) and candidate_year == year:
                return candidate
        return None

    @classmethod
    def _tmdb_match_by_original_title(
        cls,
        *,
        candidates: list[dict[str, Any]],
        title: str,
        year: int | None = None,
    ) -> dict[str, Any] | None:
        requested = cls._normalize_with_aliases(title)
        if not requested:
            return None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            original_title = str(candidate.get("original_title") or candidate.get("original_name") or "")
            if not original_title or cls._normalize_with_aliases(original_title) != requested:
                continue
            if isinstance(year, int):
                candidate_year = cls._candidate_year(candidate)
                if isinstance(candidate_year, int) and abs(candidate_year - year) > 1:
                    continue
            return candidate
        return None

    @classmethod
    def _tmdb_match_by_alternate_title(
        cls,
        *,
        candidates: list[dict[str, Any]],
        title: str,
    ) -> dict[str, Any] | None:
        requested = cls._normalize_with_aliases(title)
        if not requested:
            return None

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            alternate_titles = candidate.get("alternative_titles")
            if not isinstance(alternate_titles, list):
                continue

            for alt in alternate_titles:
                alt_title = alt.get("title") if isinstance(alt, dict) else str(alt or "")
                if alt_title and cls._normalize_with_aliases(str(alt_title)) == requested:
                    return candidate

        return None

    @classmethod
    def _tmdb_fuzzy_year_diff_single_match(
        cls,
        *,
        candidates: list[dict[str, Any]],
        title: str,
        year: int | None,
    ) -> dict[str, Any] | None:
        if len(candidates) != 1 or not isinstance(year, int):
            return None

        candidate = candidates[0]
        if not isinstance(candidate, dict):
            return None

        candidate_title = str(candidate.get("title") or candidate.get("name") or "")
        requested_norm = cls._normalize_with_aliases(title)
        candidate_norm = cls._normalize_with_aliases(candidate_title)
        if not requested_norm or not candidate_norm:
            return None

        ratio = SequenceMatcher(None, requested_norm, candidate_norm).ratio()
        jaccard = cls._word_jaccard(requested_norm, candidate_norm)
        candidate_year = cls._candidate_year(candidate)

        if not isinstance(candidate_year, int):
            return None
        if abs(candidate_year - year) > 2:
            return None
        if ratio < 0.90 or jaccard < 0.85:
            return None

        return candidate

    @classmethod
    def _tmdb_fuzzy_alternate_title_match(
        cls,
        *,
        candidates: list[dict[str, Any]],
        title: str,
        year: int | None = None,
    ) -> dict[str, Any] | None:
        requested_norm = cls._normalize_with_aliases(title)
        if not requested_norm:
            return None

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            candidate_year = cls._candidate_year(candidate)
            year_ok = (year is None and candidate_year is None) or (
                isinstance(year, int) and isinstance(candidate_year, int)
            )
            if not year_ok:
                continue

            alternate_titles = candidate.get("alternative_titles")
            if not isinstance(alternate_titles, list):
                continue

            for alt in alternate_titles:
                alt_title = alt.get("title") if isinstance(alt, dict) else str(alt or "")
                alt_norm = cls._normalize_with_aliases(str(alt_title or ""))
                if not alt_norm:
                    continue

                ratio = SequenceMatcher(None, requested_norm, alt_norm).ratio()
                jaccard = cls._word_jaccard(requested_norm, alt_norm)
                if ratio >= 0.90 and jaccard >= 0.85:
                    return candidate

        return None

    @staticmethod
    def _word_jaccard(left: str, right: str) -> float:
        left_words = set(re.findall(r"\w+", left.lower()))
        right_words = set(re.findall(r"\w+", right.lower()))
        if not left_words or not right_words:
            return 0.0
        intersection = left_words & right_words
        union = left_words | right_words
        return len(intersection) / len(union)

    @classmethod
    def _rank_tmdb_candidates(
        cls,
        *,
        title: str,
        year: int | None,
        candidates: list[dict[str, Any]],
        query_title: str,
    ) -> list[dict[str, Any]]:
        requested_norm = cls._normalize_with_aliases(title)
        query_norm = cls._normalize_with_aliases(query_title)
        scored: list[dict[str, Any]] = []

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            candidate_title = str(candidate.get("title") or candidate.get("name") or "").strip()
            candidate_norms = [
                cls._normalize_with_aliases(candidate_title),
                cls._normalize_with_aliases(str(candidate.get("original_title") or "")),
                cls._normalize_with_aliases(str(candidate.get("original_name") or "")),
            ]
            candidate_norms = [value for value in candidate_norms if value]
            if not candidate_norms:
                continue

            candidate_year = cls._candidate_year(candidate)

            year_ok = (year is None and candidate_year is None) or (
                isinstance(year, int) and isinstance(candidate_year, int)
            )
            if not year_ok:
                continue

            ratio = max(
                (SequenceMatcher(None, requested_norm, candidate_norm).ratio() for candidate_norm in candidate_norms),
                default=0.0,
            ) if requested_norm else 0.0
            jaccard = max(
                (cls._word_jaccard(requested_norm, candidate_norm) for candidate_norm in candidate_norms),
                default=0.0,
            ) if requested_norm else 0.0
            query_ratio = max(
                (SequenceMatcher(None, query_norm, candidate_norm).ratio() for candidate_norm in candidate_norms),
                default=0.0,
            ) if query_norm else 0.0

            primary_norm = candidate_norms[0]
            exact_title = bool(requested_norm and any(requested_norm == candidate_norm for candidate_norm in candidate_norms))
            exact_primary = bool(requested_norm and primary_norm and requested_norm == primary_norm)
            year_match = bool(isinstance(year, int) and isinstance(candidate_year, int) and year == candidate_year)
            year_close = bool(
                isinstance(year, int)
                and isinstance(candidate_year, int)
                and abs(year - candidate_year) <= 1
            )

            score = 0
            score += int(max(ratio, query_ratio) * 60)
            score += int(jaccard * 20)
            if exact_title:
                score += 15
            if year_match:
                score += 20
            elif year_close:
                score += 10
            elif isinstance(year, int) and isinstance(candidate_year, int):
                # Penalise large year gaps so wrong-year candidates rank lower
                year_gap = abs(year - candidate_year)
                score -= min(year_gap * 4, 20)

            if exact_title and year_match:
                reason = "exact" if exact_primary else "original_title"
                confidence = "high"
            elif exact_title and not isinstance(year, int):
                # No year in filename — title match alone is high confidence
                reason = "exact" if exact_primary else "original_title"
                confidence = "high"
            elif exact_title and year_close:
                # Year off by ±1 — medium so an exact-year match of the other
                # type wins in the dual-search rather than triggering ambiguity
                reason = "exact" if exact_primary else "original_title"
                confidence = "medium"
            elif exact_title:
                # Year is known and differs by > 1 — downgrade so it goes to pending review
                reason = "exact_year_mismatch" if exact_primary else "original_title_year_mismatch"
                confidence = "medium"
            elif ratio >= 0.90 and (year_match or year_close):
                reason = "fuzzy_title_year"
                confidence = "medium"
            elif ratio >= 0.90:
                reason = "fuzzy_title"
                confidence = "medium"
            elif jaccard >= 0.75 and (year_match or year_close):
                reason = "token_overlap_year"
                confidence = "medium"
            elif year_match:
                reason = "year_match_only"
                confidence = "low"
            else:
                reason = "rank_fallback"
                confidence = "low"

            scored.append(
                {
                    "candidate": candidate,
                    "score": score,
                    "reason": reason,
                    "confidence": confidence,
                    "candidate_title": candidate_title,
                    "candidate_year": candidate_year,
                    "ratio": ratio,
                }
            )

        scored.sort(key=lambda item: (int(item.get("score") or 0), float(item.get("ratio") or 0.0)), reverse=True)
        return scored

    @staticmethod
    def _query_transformations(title: str) -> list[str]:
        variants = [str(title or "").strip()]
        raw = variants[0]
        if not raw:
            return []

        variants.append(re.sub(r"\((?:NL|FR|US|UK|CA|DE|ES|IT|JP|RU|KR|BR|PL|SE|NO|DK|FI|CN|IN|AU|NZ|IE|PT|MX|TR)\)", "", raw, flags=re.IGNORECASE).strip())
        variants.append(raw.replace("_", " "))
        variants.append(raw.replace("-", " "))
        variants.append(raw.replace("_", ":"))
        variants.append(raw.replace("-", ":"))
        variants.append(raw.replace("-", "\\"))
        variants.append(raw.replace("+", "\\"))
        variants.append(raw.replace("+", "/"))
        variants.append(unicodedata.normalize("NFKD", raw).encode("ASCII", "ignore").decode())

        deduped: list[str] = []
        seen: set[str] = set()
        for item in variants:
            normalized = " ".join(str(item or "").split()).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped[:10]

    @classmethod
    @sleep_and_notify
    def _tmdb_get_json(
        cls,
        *,
        path: str,
        api_key: str,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        query_params: dict[str, Any] = dict(params or {})
        query_params["api_key"] = api_key

        for attempt in range(cls.TMDB_MAX_RETRIES):
            now = time.monotonic()
            elapsed = now - cls._tmdb_last_request_at
            if elapsed < cls.TMDB_MIN_INTERVAL_SECONDS:
                time.sleep(cls.TMDB_MIN_INTERVAL_SECONDS - elapsed)

            try:
                response = requests.get(f"https://api.themoviedb.org/3{path}", params=query_params, timeout=20)
                cls._tmdb_last_request_at = time.monotonic()

                status_code = getattr(response, "status_code", None)
                if allow_404 and status_code == 404:
                    return None

                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                retryable = status in {429, 500, 502, 503, 504}

                if status == 429 and attempt >= cls.TMDB_MAX_RETRIES - 1:
                    retry_after_raw = exc.response.headers.get("Retry-After") if exc.response is not None else None
                    try:
                        retry_after = float(str(retry_after_raw)) if retry_after_raw is not None else 1.0
                    except Exception:
                        retry_after = 1.0
                    raise RateLimitException(retry_after)

                if retryable and attempt < cls.TMDB_MAX_RETRIES - 1:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                if allow_404 and status == 404:
                    return None
                raise

        return None

    @staticmethod
    def _tmdb_search(
        *,
        api_key: str,
        title: str,
        asset_type: str,
        year: int | None,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        imdb_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        endpoint_map = {
            "movie": "movie",
            "tv_series": "tv",
            "collection": "collection",
        }
        endpoint = endpoint_map.get(asset_type, "movie")

        best_match: dict[str, Any] | None = None
        best_meta: dict[str, Any] | None = None

        for query_title in IdarrRunner._query_transformations(title):
            is_transformed_query = query_title != title
            if bool(app_settings.debug) and query_title != title:
                log_info(LogTags.IDARR, f"🔁 Retrying TMDB search with transformed title: '{query_title}'")

            params: dict[str, Any] = {
                "api_key": api_key,
                "query": query_title,
                "include_adult": False,
            }
            if isinstance(year, int):
                if endpoint == "movie":
                    params["year"] = year
                elif endpoint == "tv":
                    params["first_air_date_year"] = year

            payload = IdarrRunner._tmdb_get_json(path=f"/search/{endpoint}", api_key=api_key, params=params) or {}
            results = payload.get("results", []) if isinstance(payload, dict) else []
            if not isinstance(results, list) or not results:
                continue

            dict_results = [candidate for candidate in results if isinstance(candidate, dict)]
            if not dict_results:
                continue

            requested_normalized_title = IdarrRunner._normalize_with_aliases(title)
            # Only run the ambiguity guard when we have no ID to disambiguate with.
            # If tmdb_id / tvdb_id / imdb_id is already known the id_match path below
            # will pick the right candidate, so returning "ambiguous" here would
            # incorrectly block files that already have a TMDB ID embedded in their name.
            has_known_id = bool(tmdb_id or tvdb_id or imdb_id)
            exact_title_year_matches = 0
            for candidate in dict_results:
                candidate_title = str(candidate.get("title") or candidate.get("name") or "")
                if IdarrRunner._normalize_with_aliases(candidate_title) != requested_normalized_title:
                    continue
                candidate_year = IdarrRunner._candidate_year(candidate)
                if isinstance(year, int) and isinstance(candidate_year, int) and candidate_year == year:
                    exact_title_year_matches += 1
                    if exact_title_year_matches > 1 and not has_known_id:
                        return None, "ambiguous"

            id_match = IdarrRunner._match_tmdb_candidate_by_id(
                candidates=dict_results,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,
                imdb_id=imdb_id,
            )
            if id_match:
                selected = dict(id_match)
                reason_label = "id_transform" if is_transformed_query else "id"
                selected["_idarr_match"] = {
                    "confidence": "high",
                    "reason": reason_label,
                    "score": 98,
                    "candidate_title": str(selected.get("title") or selected.get("name") or ""),
                    "candidate_year": IdarrRunner._candidate_year(selected),
                }
                return selected, reason_label

            exact_match = IdarrRunner._exact_tmdb_title_year_match(candidates=dict_results, title=title, year=year)
            if exact_match:
                selected = dict(exact_match)
                reason_label = "exact_transform" if is_transformed_query else "exact"
                selected["_idarr_match"] = {
                    "confidence": "high",
                    "reason": reason_label,
                    "score": 95,
                    "candidate_title": str(selected.get("title") or selected.get("name") or ""),
                    "candidate_year": IdarrRunner._candidate_year(selected),
                }
                return selected, reason_label

            original_match = IdarrRunner._tmdb_match_by_original_title(candidates=dict_results, title=title, year=year)
            if original_match:
                selected = dict(original_match)
                reason_label = "original_transform" if is_transformed_query else "original_title"
                _orig_candidate_year = IdarrRunner._candidate_year(selected)
                # Exact year → high; year_close (±1 but not exact) → medium.
                # A year-close original-title match is a weaker signal than a
                # same-year primary-title match, so it should lose to one in the
                # dual-search comparison (e.g. 2022 movie vs 2023 TV show).
                _orig_year_exact = isinstance(year, int) and _orig_candidate_year == year
                selected["_idarr_match"] = {
                    "confidence": "high" if _orig_year_exact else "medium",
                    "reason": reason_label,
                    "score": 90 if _orig_year_exact else 80,
                    "candidate_title": str(selected.get("title") or selected.get("name") or ""),
                    "candidate_year": _orig_candidate_year,
                }
                return selected, reason_label

            IdarrRunner._hydrate_missing_alternate_titles(
                api_key=api_key,
                asset_type=asset_type,
                candidates=dict_results,
            )

            alternate_match = IdarrRunner._tmdb_match_by_alternate_title(candidates=dict_results, title=title)
            if alternate_match:
                selected = dict(alternate_match)
                reason_label = "alternate_transform" if is_transformed_query else "alternate_title"
                selected["_idarr_match"] = {
                    "confidence": "medium",
                    "reason": reason_label,
                    "score": 86,
                    "candidate_title": str(selected.get("title") or selected.get("name") or ""),
                    "candidate_year": IdarrRunner._candidate_year(selected),
                }
                return selected, reason_label

            fuzzy_year_diff_match = IdarrRunner._tmdb_fuzzy_year_diff_single_match(
                candidates=dict_results,
                title=title,
                year=year,
            )
            if fuzzy_year_diff_match:
                selected = dict(fuzzy_year_diff_match)
                reason_label = "fuzzy_transform" if is_transformed_query else "fuzzy_year_diff"
                selected["_idarr_match"] = {
                    "confidence": "medium",
                    "reason": reason_label,
                    "score": 80,
                    "candidate_title": str(selected.get("title") or selected.get("name") or ""),
                    "candidate_year": IdarrRunner._candidate_year(selected),
                }
                return selected, reason_label

            fuzzy_alternate_match = IdarrRunner._tmdb_fuzzy_alternate_title_match(
                candidates=dict_results,
                title=title,
                year=year,
            )
            if fuzzy_alternate_match:
                selected = dict(fuzzy_alternate_match)
                reason_label = "fuzzy_alternate_transform" if is_transformed_query else "fuzzy_alternate"
                selected["_idarr_match"] = {
                    "confidence": "medium",
                    "reason": reason_label,
                    "score": 76,
                    "candidate_title": str(selected.get("title") or selected.get("name") or ""),
                    "candidate_year": IdarrRunner._candidate_year(selected),
                }
                return selected, reason_label

            ranked = IdarrRunner._rank_tmdb_candidates(
                title=title,
                year=year,
                candidates=dict_results,
                query_title=query_title,
            )
            if not ranked:
                continue

            top = ranked[0]

            if int(top.get("score") or 0) < 45:
                continue

            if not best_meta or int(top.get("score") or 0) > int(best_meta.get("score") or 0):
                best_meta = top
                best_match = dict(top.get("candidate") or {})

            if query_title == title and str(top.get("reason") or "") in {"exact", "original_title"} and int(top.get("score") or 0) >= 85:
                best_meta = top
                best_match = dict(top.get("candidate") or {})
                break

        if not best_match or not best_meta:
            return None, None

        best_match["_idarr_match"] = {
            "confidence": str(best_meta.get("confidence") or "unknown"),
            "reason": str(best_meta.get("reason") or "rank_fallback"),
            "score": int(best_meta.get("score") or 0),
            "candidate_title": str(best_meta.get("candidate_title") or ""),
            "candidate_year": best_meta.get("candidate_year") if isinstance(best_meta.get("candidate_year"), int) else None,
        }
        return best_match, str(best_meta.get("reason") or "rank_fallback")



    @staticmethod
    def _tmdb_external_ids(*, api_key: str, tmdb_id: int, asset_type: str) -> dict[str, Any]:
        endpoint_map = {
            "movie": "movie",
            "tv_series": "tv",
        }
        endpoint = endpoint_map.get(asset_type)
        if not endpoint:
            return {}

        payload = IdarrRunner._tmdb_get_json(
            path=f"/{endpoint}/{tmdb_id}/external_ids",
            api_key=api_key,
            params={},
        ) or {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _tmdb_alternative_titles(*, api_key: str, tmdb_id: int, asset_type: str) -> list[dict[str, Any]]:
        endpoint_map = {
            "movie": "movie",
            "tv_series": "tv",
        }
        endpoint = endpoint_map.get(asset_type)
        if not endpoint:
            return []

        payload = IdarrRunner._tmdb_get_json(
            path=f"/{endpoint}/{tmdb_id}/alternative_titles",
            api_key=api_key,
            params={},
            allow_404=True,
        ) or {}
        if not isinstance(payload, dict):
            return []

        entries = payload.get("titles") if endpoint == "movie" else payload.get("results")
        if not isinstance(entries, list):
            return []

        normalized: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            alt_title = str(entry.get("title") or "").strip()
            if not alt_title:
                continue
            normalized.append({"title": alt_title})
        return normalized

    @staticmethod
    def _hydrate_missing_alternate_titles(
        *,
        api_key: str,
        asset_type: str,
        candidates: list[dict[str, Any]],
        max_candidates: int = 5,
    ) -> None:
        if asset_type not in {"movie", "tv_series"}:
            return

        hydrated = 0
        for candidate in candidates:
            if hydrated >= max_candidates:
                break
            if not isinstance(candidate, dict):
                continue

            existing = candidate.get("alternative_titles")
            if isinstance(existing, list) and existing:
                continue

            tmdb_id = candidate.get("id")
            if not isinstance(tmdb_id, int):
                continue

            alternates = IdarrRunner._tmdb_alternative_titles(
                api_key=api_key,
                tmdb_id=tmdb_id,
                asset_type=asset_type,
            )
            if alternates:
                candidate["alternative_titles"] = alternates
            hydrated += 1

    @staticmethod
    def _tmdb_verify_id(
        *,
        api_key: str,
        tmdb_id: int,
        asset_type: str,
        title: str,
        year: int | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        preferred = {
            "movie": "movie",
            "tv_series": "tv",
            "collection": "collection",
        }.get(asset_type)

        endpoint_order = [endpoint for endpoint in [preferred, "movie", "tv", "collection"] if endpoint]
        deduped_order: list[str] = []
        seen: set[str] = set()
        for endpoint in endpoint_order:
            if endpoint not in seen:
                deduped_order.append(endpoint)
                seen.add(endpoint)

        requested_norm = IdarrRunner._normalize_with_aliases(title)

        found_but_mismatch = False
        for endpoint in deduped_order:
            payload = IdarrRunner._tmdb_get_json(
                path=f"/{endpoint}/{tmdb_id}",
                api_key=api_key,
                params={},
                allow_404=True,
            )
            if not isinstance(payload, dict):
                continue

            # The ID exists on TMDB — any failure from here is a mismatch, not a 404.
            found_but_mismatch = True

            candidate_title = str(payload.get("title") or payload.get("name") or "")
            if requested_norm and candidate_title:
                candidate_norm = IdarrRunner._normalize_with_aliases(candidate_title)
                # For collection verification, strip the trailing "collection" word from
                # both sides before comparing.  TMDB collection names inconsistently
                # include/omit the "Collection" suffix, but user filenames always need
                # it for idarr to detect them as collections.  Stripping both sides
                # gives a fair comparison regardless of which convention TMDB uses.
                if endpoint == "collection":
                    _strip_coll = re.compile(r"\bcollection\s*$", re.IGNORECASE)
                    compare_requested = _strip_coll.sub("", requested_norm).strip()
                    compare_candidate = _strip_coll.sub("", candidate_norm).strip()
                else:
                    compare_requested = requested_norm
                    compare_candidate = candidate_norm
                ratio = SequenceMatcher(None, compare_requested, compare_candidate).ratio() if compare_candidate else 0.0
                # Always enforce the threshold — when compare_candidate is empty the TMDB
                # title is fully non-ASCII (e.g. Chinese/Japanese).  ratio is 0.0 in that
                # case, which correctly triggers a mismatch rather than silently accepting
                # a completely wrong entry.
                if ratio < 0.80:
                    continue

                if isinstance(year, int):
                    candidate_year = IdarrRunner._candidate_year(payload)
                    if isinstance(candidate_year, int) and abs(candidate_year - year) > 1:
                        continue

            resolved_type = {
                "movie": "movie",
                "tv": "tv_series",
                "collection": "collection",
            }.get(endpoint)
            return payload, resolved_type, None

        reject_reason = "title_mismatch" if found_but_mismatch else "not_found"
        return None, None, reject_reason

    def _load_cache_map(self, assets: list[dict[str, Any]]) -> dict[str, IdarrAssetCache]:
        keys: set[str] = set()
        for asset in assets:
            title = str(asset.get("title") or "").strip()
            asset_type = str(asset.get("type") or "").strip()
            if not title or not asset_type:
                continue
            year = asset.get("year") if isinstance(asset.get("year"), int) else None
            tmdb_id = int(asset.get("tmdb_id")) if isinstance(asset.get("tmdb_id"), int) else None
            tvdb_id = int(asset.get("tvdb_id")) if isinstance(asset.get("tvdb_id"), int) else None
            imdb_id = str(asset.get("imdb_id") or "").strip() if isinstance(asset.get("imdb_id"), str) else None
            # Include both the provisional title/year key and the resolved ID-keyed form
            # so we catch rows regardless of which format they were stored under.
            keys.add(self._asset_key(asset_type=asset_type, title=title, year=year))
            keys.add(self._asset_key(asset_type=asset_type, title=title, year=year,
                                     tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id))
        tmdb_ids = {
            int(asset.get("tmdb_id"))
            for asset in assets
            if isinstance(asset.get("tmdb_id"), int)
        }
        tvdb_ids = {
            int(asset.get("tvdb_id"))
            for asset in assets
            if isinstance(asset.get("tvdb_id"), int)
        }
        imdb_ids = {
            str(asset.get("imdb_id") or "").strip()
            for asset in assets
            if isinstance(asset.get("imdb_id"), str) and str(asset.get("imdb_id") or "").strip()
        }

        if not keys and not tmdb_ids and not tvdb_ids and not imdb_ids:
            return {}

        conditions = []
        if keys:
            conditions.append(IdarrAssetCache.asset_key.in_(keys))
        if tmdb_ids:
            conditions.append(IdarrAssetCache.tmdb_id.in_(tmdb_ids))
        if tvdb_ids:
            conditions.append(IdarrAssetCache.tvdb_id.in_(tvdb_ids))
        if imdb_ids:
            conditions.append(IdarrAssetCache.imdb_id.in_(imdb_ids))

        if conditions:
            query = self.db.query(IdarrAssetCache).filter(or_(*conditions))
            if self._scope_token:
                query = query.filter(IdarrAssetCache.asset_key.like(f"%::scope={self._scope_token}"))
            else:
                query = query.filter(~IdarrAssetCache.asset_key.like("%::scope=%"))
            rows = query.all()
        else:
            rows = []

        by_key = {
            str(row.asset_key): row
            for row in rows
            if isinstance(row.asset_key, str) and str(row.asset_key).strip()
        }
        by_tmdb: dict[int, list[IdarrAssetCache]] = {}
        by_tvdb: dict[int, list[IdarrAssetCache]] = {}
        by_imdb: dict[str, list[IdarrAssetCache]] = {}

        for row in rows:
            if isinstance(row.tmdb_id, int):
                by_tmdb.setdefault(int(row.tmdb_id), []).append(row)
            if isinstance(row.tvdb_id, int):
                by_tvdb.setdefault(int(row.tvdb_id), []).append(row)
            if isinstance(row.imdb_id, str) and row.imdb_id.strip():
                by_imdb.setdefault(str(row.imdb_id).strip(), []).append(row)

        def _is_compatible_type(asset_type: str, row_type: str | None) -> bool:
            normalized_asset = self._normalize_asset_type(asset_type)
            normalized_row = self._normalize_asset_type(row_type)
            return bool(normalized_asset and normalized_row and normalized_asset == normalized_row)

        resolved_map: dict[str, IdarrAssetCache] = {}
        for asset in assets:
            asset_type = str(asset.get("type") or "")
            asset_key = self._asset_key(
                asset_type=asset_type,
                title=str(asset.get("title") or ""),
                year=asset.get("year") if isinstance(asset.get("year"), int) else None,
            )
            if not asset_key:
                continue

            matched_row: IdarrAssetCache | None = None

            if isinstance(asset.get("tmdb_id"), int):
                candidates = by_tmdb.get(int(asset["tmdb_id"]), [])
                matched_row = next((row for row in candidates if _is_compatible_type(asset_type, row.asset_type)), None)
                if not matched_row and candidates:
                    matched_row = candidates[0]

            if not matched_row and isinstance(asset.get("tvdb_id"), int):
                candidates = by_tvdb.get(int(asset["tvdb_id"]), [])
                matched_row = next((row for row in candidates if _is_compatible_type(asset_type, row.asset_type)), None)
                if not matched_row and candidates:
                    matched_row = candidates[0]

            if not matched_row and isinstance(asset.get("imdb_id"), str) and str(asset.get("imdb_id") or "").strip():
                imdb_value = str(asset.get("imdb_id") or "").strip()
                candidates = by_imdb.get(imdb_value, [])
                matched_row = next((row for row in candidates if _is_compatible_type(asset_type, row.asset_type)), None)
                if not matched_row and candidates:
                    matched_row = candidates[0]

            if not matched_row:
                key_row = by_key.get(asset_key)
                if key_row is not None:
                    # Guard: if the asset has a specific TMDB ID and the title/year cache
                    # row has a *different* known TMDB ID, this is a distinct title sharing
                    # the same title/year (e.g. two movies titled "Spiral (2019)").  Skip
                    # this row so we don't inherit its imdb_id or stale freshness timestamp.
                    asset_tmdb = asset.get("tmdb_id") if isinstance(asset.get("tmdb_id"), int) else None
                    row_tmdb = int(key_row.tmdb_id) if isinstance(key_row.tmdb_id, int) else None
                    if asset_tmdb is None or row_tmdb is None or asset_tmdb == row_tmdb:
                        matched_row = key_row

            if matched_row:
                # Store under the ID-keyed form so two items with identical title/year
                # (but different TMDB IDs) each get their own slot in the map.
                id_keyed = self._asset_key(
                    asset_type=asset_type,
                    title=str(asset.get("title") or ""),
                    year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                    tmdb_id=int(asset["tmdb_id"]) if isinstance(asset.get("tmdb_id"), int) else None,
                    tvdb_id=int(asset["tvdb_id"]) if isinstance(asset.get("tvdb_id"), int) else None,
                    imdb_id=str(asset.get("imdb_id") or "").strip() or None,
                )
                resolved_map[id_keyed] = matched_row

        return resolved_map

    @staticmethod
    def _search_result_rank(result: dict[str, Any] | None) -> int:
        """Return a comparable rank for a TMDB search result.

        -1 = no result; 0 = weak (low/unknown confidence or rank_fallback/year_match_only);
        1 = low; 2 = medium; 3 = high.
        """
        if result is None:
            return -1
        info = result.get("_idarr_match") or {}
        confidence = str(info.get("confidence") or "unknown").lower()
        reason = str(info.get("reason") or "").lower()
        rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}.get(confidence, 0)
        if reason in {"rank_fallback", "year_match_only"}:
            rank = min(rank, 0)
        return rank

    def _enrich_assets_with_tmdb(
        self,
        assets: list[dict[str, Any]],
        tmdb_api_key: str,
        *,
        frequency_days: int,
        tvdb_frequency: int,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        enrichment_stats = {
            "items_considered": len(assets),
            "tmdb_assigned": 0,
            "tvdb_assigned": 0,
            "imdb_assigned": 0,
            "reclassified_tv": 0,
            "reclassified_movie": 0,
            "tmdb_search_api_calls": 0,
            "tmdb_external_id_api_calls": 0,
            "tmdb_api_calls": 0,
            "cache_prefilled": 0,
            "freshness_skipped": 0,
            "tvdb_rehydrate_calls": 0,
            "errors": 0,
            "confidence_high": 0,
            "confidence_medium": 0,
            "confidence_low": 0,
            "confidence_unknown": 0,
            "cache_checkpoint_commits": 0,
            "match_reason_counts": {},
        }
        detail_rows: list[dict[str, Any]] = []
        cache_map = self._load_cache_map(assets)
        grouped_resolution_map: dict[str, dict[str, Any]] = {}
        grouped_season_labels_by_key: dict[str, set[str]] = {}
        grouped_unresolved_map: dict[str, str] = {}

        for asset in assets:
            grouped_cache_key = self._asset_key(
                asset_type=str(asset.get("type") or ""),
                title=str(asset.get("title") or ""),
                year=asset.get("year") if isinstance(asset.get("year"), int) else None,
            )
            season_label = self._extract_season_label(str(asset.get("file_path") or ""))
            if season_label:
                grouped_season_labels_by_key.setdefault(grouped_cache_key, set()).add(season_label)

        total_items = len(assets)
        progress_interval = 25
        cache_checkpoint_interval = 25 if total_items <= 500 else 100
        cache_checkpoint_assets: list[dict[str, Any]] = []
        grouped_reuse_logged_keys: set[str] = set()
        grouped_unresolved_logged_keys: set[str] = set()
        grouped_unresolved_skip_counts: dict[str, int] = {}
        grouped_unresolved_display_labels: dict[str, str] = {}
        grouped_cached_logged_keys: set[str] = set()
        verify_logged_keys: set[str] = set()
        verify_success_logged_keys: set[str] = set()
        verify_result_cache: dict[tuple, tuple] = {}
        matched_with_any_id = 0

        # Sort so base/hub posters (no season pattern) come before their season
        # sub-files within each group.  This ensures the base poster always
        # populates grouped_resolution_map first, so every season file can
        # reuse the result rather than triggering its own TMDB search.
        assets = sorted(
            assets,
            key=lambda a: (
                self._asset_key(
                    asset_type=str(a.get("type") or ""),
                    title=str(a.get("title") or ""),
                    year=a.get("year") if isinstance(a.get("year"), int) else None,
                ),
                bool(SEASON_REGEX.search(str(a.get("file_path") or ""))),
                str(a.get("file_path") or ""),
            ),
        )

        for index, asset in enumerate(assets, start=1):
            before_tmdb = asset.get("tmdb_id")
            before_tvdb = asset.get("tvdb_id")
            before_imdb = asset.get("imdb_id")
            cache_key = self._asset_key(
                asset_type=str(asset.get("type") or ""),
                title=str(asset.get("title") or ""),
                year=asset.get("year") if isinstance(asset.get("year"), int) else None,
            )
            # Prefer the ID-keyed form when IDs are already known (e.g. from filename
            # tags) so two items with identical title/year but distinct TMDB IDs each
            # hit their own cache row rather than sharing one colliding entry.
            _lk_tmdb = int(asset["tmdb_id"]) if isinstance(asset.get("tmdb_id"), int) else None
            _lk_tvdb = int(asset["tvdb_id"]) if isinstance(asset.get("tvdb_id"), int) else None
            _lk_imdb = str(asset.get("imdb_id") or "").strip() or None
            if _lk_tmdb or _lk_tvdb or _lk_imdb:
                _id_key = self._asset_key(
                    asset_type=str(asset.get("type") or ""),
                    title=str(asset.get("title") or ""),
                    year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                    tmdb_id=_lk_tmdb,
                    tvdb_id=_lk_tvdb,
                    imdb_id=_lk_imdb,
                )
                cache_row = cache_map.get(_id_key) or cache_map.get(cache_key)
            else:
                cache_row = cache_map.get(cache_key)
            network_lookup_performed = False

            try:
                match_confidence = "unknown"
                match_reason = "unchanged"
                match_score = 0

                grouped_resolution = grouped_resolution_map.get(cache_key)
                _asset_has_season = bool(SEASON_REGEX.search(str(asset.get("file_path") or "")))
                _group_is_confirmed_tv = grouped_resolution is not None and str(grouped_resolution.get("asset_type") or "") == "tv_series"
                _skip_verify_due_to_group_prefill = False
                # If this asset has a different TMDB ID than the group resolved to, it's a
                # separate entity sharing a title/year — skip group prefill to avoid
                # cross-contaminating type and TVDB (e.g. movie poster alongside TV show files).
                _group_tmdb_conflicts = (
                    isinstance(asset.get("tmdb_id"), int)
                    and isinstance((grouped_resolution or {}).get("tmdb_id"), int)
                    and asset["tmdb_id"] != grouped_resolution["tmdb_id"]
                ) if grouped_resolution else False
                if grouped_resolution and (_asset_has_season or _group_is_confirmed_tv) and not _group_tmdb_conflicts:
                    grouped_prefilled = False
                    grouped_tmdb = grouped_resolution.get("tmdb_id")
                    grouped_tvdb = grouped_resolution.get("tvdb_id")
                    grouped_imdb = grouped_resolution.get("imdb_id")
                    grouped_canonical_title = str(grouped_resolution.get("canonical_title") or "").strip()
                    grouped_canonical_year = grouped_resolution.get("canonical_year") if isinstance(grouped_resolution.get("canonical_year"), int) else None

                    if not asset.get("tmdb_id") and isinstance(grouped_tmdb, int):
                        asset["tmdb_id"] = grouped_tmdb
                        grouped_prefilled = True
                    if not asset.get("tvdb_id") and isinstance(grouped_tvdb, int):
                        asset["tvdb_id"] = grouped_tvdb
                        grouped_prefilled = True
                    if not asset.get("imdb_id") and isinstance(grouped_imdb, str):
                        asset["imdb_id"] = grouped_imdb
                        grouped_prefilled = True

                    if grouped_prefilled:
                        _skip_verify_due_to_group_prefill = True
                        if isinstance(grouped_resolution.get("asset_type"), str):
                            asset["type"] = grouped_resolution["asset_type"]
                        current_title = str(asset.get("title") or "").strip()
                        if (
                            grouped_canonical_title
                            and grouped_canonical_title != current_title
                        ):
                            asset["new_title"] = grouped_canonical_title
                        if isinstance(grouped_canonical_year, int) and grouped_canonical_year != asset.get("year"):
                            asset["new_year"] = grouped_canonical_year
                        match_reason = str(grouped_resolution.get("match_reason") or "group_prefill")
                        match_confidence = str(grouped_resolution.get("match_confidence") or "high")
                        match_score = int(grouped_resolution.get("match_score") or 90)
                        if not str(asset.get("match_reason") or "").strip():
                            asset["match_reason"] = match_reason

                        if bool(app_settings.debug):
                            if cache_key not in grouped_reuse_logged_keys:
                                grouped_reuse_logged_keys.add(cache_key)
                                season_labels = grouped_season_labels_by_key.get(cache_key, set())
                                log_info(
                                    LogTags.IDARR,
                                    (
                                        f"↪ Reusing grouped match for “{str(asset.get('title') or '')}” ({asset.get('year')}) "
                                        f"— grouped posters: {self._format_season_labels(season_labels)}"
                                    ),
                                )

                _needs_title_from_tmdb = False
                if cache_row:
                    cache_payload: dict[str, Any] = {}
                    if isinstance(cache_row.payload_json, str) and cache_row.payload_json.strip():
                        try:
                            parsed_payload = json.loads(cache_row.payload_json)
                            if isinstance(parsed_payload, dict):
                                cache_payload = parsed_payload
                        except Exception:
                            cache_payload = {}

                    cache_applied = False
                    if not asset.get("tmdb_id") and isinstance(cache_row.tmdb_id, int):
                        asset["tmdb_id"] = cache_row.tmdb_id
                        cache_applied = True
                    # Only restore tvdb_id for TV series — movies and collections never
                    # have TVDB IDs, so skip restoration to prevent stale tv_series cache
                    # rows from contaminating movie/collection assets with a stale tvdb_id.
                    _is_tv_series_for_prefill = str(asset.get("type") or "").strip().lower() == "tv_series"
                    if _is_tv_series_for_prefill and not asset.get("tvdb_id") and isinstance(cache_row.tvdb_id, int):
                        asset["tvdb_id"] = cache_row.tvdb_id
                        cache_applied = True
                    if not asset.get("imdb_id") and isinstance(cache_row.imdb_id, str):
                        asset["imdb_id"] = cache_row.imdb_id
                        cache_applied = True
                    if cache_applied:
                        enrichment_stats["cache_prefilled"] += 1
                        match_confidence = "medium"
                        match_reason = "cache_prefill"
                        match_score = 60

                    canonical_title = str(cache_payload.get("canonical_title") or "").strip()
                    canonical_year = cache_payload.get("canonical_year") if isinstance(cache_payload.get("canonical_year"), int) else None
                    current_title = str(asset.get("title") or "").strip()
                    if (
                        canonical_title
                        and canonical_title != current_title
                        and not str(asset.get("new_title") or "").strip()
                    ):
                        asset["new_title"] = canonical_title
                    if isinstance(canonical_year, int) and canonical_year != asset.get("year") and not isinstance(asset.get("new_year"), int):
                        asset["new_year"] = canonical_year

                    # If the item was manually resolved but the canonical title was never
                    # stored (e.g. resolved before the canonical_title fix was added),
                    # mark it for a forced TMDB verify on this run.
                    # NOTE: Check cache_payload["canonical_title"] not asset["new_title"].
                    # new_title is only set when the canonical title differs from the current
                    # title, so checking it causes an infinite verify loop when they match.
                    if (
                        isinstance(asset.get("tmdb_id"), int)
                        and bool(cache_payload.get("resolved_manually"))
                        and not str(cache_payload.get("canonical_title") or "").strip()
                        and bool(tmdb_api_key)
                    ):
                        _needs_title_from_tmdb = True

                general_recent = is_recent(cache_row.last_checked_at if cache_row else None, frequency_days)
                tvdb_recent = is_recent(cache_row.last_checked_at if cache_row else None, tvdb_frequency)

                unresolved_reason = grouped_unresolved_map.get(cache_key)
                skip_grouped_unresolved_search = False
                if (
                    unresolved_reason
                    and not asset.get("tmdb_id")
                ):
                    skip_grouped_unresolved_search = True
                    grouped_unresolved_skip_counts[cache_key] = int(grouped_unresolved_skip_counts.get(cache_key, 0)) + 1
                    if cache_key not in grouped_unresolved_display_labels:
                        grouped_unresolved_display_labels[cache_key] = (
                            f"“{str(asset.get('title') or '')}” ({asset.get('year')})"
                        )
                    match_reason = unresolved_reason
                    if not str(asset.get("match_reason") or "").strip():
                        asset["match_reason"] = unresolved_reason
                    if bool(app_settings.debug):
                        if cache_key not in grouped_unresolved_logged_keys:
                            grouped_unresolved_logged_keys.add(cache_key)
                            season_labels = grouped_season_labels_by_key.get(cache_key, set())
                            log_info(
                                LogTags.IDARR,
                                (
                                    f"↪ Grouped unresolved for “{str(asset.get('title') or '')}” ({asset.get('year')}) "
                                    f"— grouped posters: {self._format_season_labels(season_labels)} [{unresolved_reason}] "
                                    f"(still unmatched; skipping repeated TMDB searches for grouped duplicates)"
                                ),
                            )

                if isinstance(asset.get("tmdb_id"), int) and not _skip_verify_due_to_group_prefill and (not general_recent or _needs_title_from_tmdb):
                    if bool(app_settings.debug):
                        _verify_log_key = f"{cache_key}::tmdb={int(asset['tmdb_id'])}"
                        if _verify_log_key not in verify_logged_keys:
                            verify_logged_keys.add(_verify_log_key)
                            reason_label = "title missing after resolve" if _needs_title_from_tmdb and general_recent else "stale cache"
                            log_info(
                                LogTags.IDARR,
                                (
                                    f"🔎 Verifying existing TMDB ID {int(asset['tmdb_id'])} for "
                                    f"“{str(asset.get('title') or '')}” ({asset.get('year')}) "
                                    f"[{str(asset.get('type') or 'movie')}] ({reason_label})"
                                ),
                            )
                    _verify_tmdb_id = int(asset["tmdb_id"])
                    _verify_asset_type = str(asset.get("type") or "movie")
                    _verify_cache_key = (_verify_tmdb_id, _verify_asset_type)
                    if _verify_cache_key in verify_result_cache:
                        verified_payload, verified_type, verify_reject_reason = verify_result_cache[_verify_cache_key]
                    else:
                        verified_payload, verified_type, verify_reject_reason = self._tmdb_verify_id(
                            api_key=tmdb_api_key,
                            tmdb_id=_verify_tmdb_id,
                            asset_type=_verify_asset_type,
                            title=str(asset.get("title") or ""),
                            year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                        )
                        verify_result_cache[_verify_cache_key] = (verified_payload, verified_type, verify_reject_reason)
                        network_lookup_performed = True

                    if verified_payload:
                        _verified_title = str(verified_payload.get("title") or verified_payload.get("name") or "").strip()
                        _verified_year = IdarrRunner._candidate_year(verified_payload)
                        if bool(app_settings.debug):
                            _verify_success_log_key = f"{cache_key}::tmdb={int(asset['tmdb_id'])}"
                            if _verify_success_log_key not in verify_success_logged_keys:
                                verify_success_logged_keys.add(_verify_success_log_key)
                                log_info(
                                    LogTags.IDARR,
                                    (
                                        f"✓ Verified existing TMDB ID {int(asset['tmdb_id'])}: "
                                        f"{_verified_title} ({_verified_year or asset.get('year')})"
                                    ),
                                )
                        if verified_type in {"movie", "tv_series", "collection"} and verified_type != str(asset.get("type") or ""):
                            asset["type"] = verified_type
                            enrichment_stats["reclassified_tv"] += 1 if verified_type == "tv_series" else 0

                        # Propagate verified TMDB title/year — TMDB is canonical, always wins
                        _current_title = str(asset.get("title") or "").strip()
                        if _verified_title and _verified_title != _current_title:
                            asset["new_title"] = _verified_title
                        if isinstance(_verified_year, int) and _verified_year != asset.get("year"):
                            asset["new_year"] = _verified_year

                        # TMDB verification is authoritative — always apply its imdb_id.
                        # This also corrects an imdb_id that was incorrectly inherited from
                        # a different movie sharing the same title/year in the cache.
                        if isinstance(verified_payload.get("imdb_id"), str):
                            asset["imdb_id"] = str(verified_payload.get("imdb_id"))
                    else:
                        removed_tmdb = asset.get("tmdb_id")
                        asset["tmdb_id"] = None
                        asset["match_reason"] = "tmdb_removed"
                        match_reason = "tmdb_removed"
                        if bool(app_settings.debug):
                            _asset_title = str(asset.get("title") or "")
                            if verify_reject_reason == "title_mismatch":
                                log_warning(
                                    LogTags.IDARR,
                                    f"❌ TMDB title mismatch for \"{_asset_title}\": ID {removed_tmdb} exists but title similarity too low — clearing for re-search",
                                )
                            else:
                                log_warning(
                                    LogTags.IDARR,
                                    f"❌ TMDB ID {removed_tmdb} no longer exists for \"{_asset_title}\" — clearing for re-search",
                                )

                if not asset.get("tmdb_id") and not general_recent and not skip_grouped_unresolved_search:
                    if bool(app_settings.debug):
                        media_label = "tv_series" if str(asset.get("type") or "") == "tv_series" else "movie"
                        log_info(
                            LogTags.IDARR,
                            f"🔍 Searching TMDB for “{str(asset.get('title') or '')}” ({asset.get('year')}) [{media_label}]...",
                        )

                    enrichment_stats["tmdb_search_api_calls"] += 1
                    search_result, search_reason = self._tmdb_search(
                        api_key=tmdb_api_key,
                        title=str(asset.get("title") or ""),
                        asset_type=str(asset.get("type") or "movie"),
                        year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                        tmdb_id=asset.get("tmdb_id") if isinstance(asset.get("tmdb_id"), int) else None,
                        tvdb_id=asset.get("tvdb_id") if isinstance(asset.get("tvdb_id"), int) else None,
                        imdb_id=str(asset.get("imdb_id")) if isinstance(asset.get("imdb_id"), str) else None,
                    )
                    if search_reason == "ambiguous":
                        log_warning(
                            LogTags.IDARR,
                            (
                                f"🤷 Ambiguous result for “{str(asset.get('title') or '')}” "
                                f"({asset.get('year')}) [{str(asset.get('type') or 'movie')}] — Multiple possible matches found."
                            ),
                        )
                        asset["match_reason"] = "ambiguous"
                        grouped_unresolved_map.setdefault(cache_key, "ambiguous")
                        continue

                    _exact_reasons = {"exact", "original_title", "exact_transform", "original_transform", "id", "id_transform", "alternate_title", "alternate_transform"}
                    if (
                        search_result
                        and str(asset.get("type")) == "collection"
                        and not isinstance(asset.get("year"), int)
                        and search_reason not in _exact_reasons
                    ):
                        if bool(app_settings.debug):
                            _rejected_title = str(search_result.get("title") or search_result.get("name") or "")
                            log_debug(
                                LogTags.IDARR,
                                f"⊘ Rejected '{search_reason}' match for yearless collection \"{str(asset.get('title') or '')}\": {_rejected_title}",
                            )
                        search_result = None
                        search_reason = None

                    # When type was inferred (no TVDB tag / season suffix), search the
                    # alternate endpoint too and keep whichever result is more confident.
                    # This avoids committing to “movie” just because no season hint exists.
                    if bool(asset.get("type_is_inferred")) and str(asset.get("type") or "") in {"movie", "tv_series"}:
                        _primary_rank = self._search_result_rank(search_result)
                        _current_type = str(asset.get("type") or "")
                        _alt_type = "tv_series" if _current_type == "movie" else "movie"
                        if bool(app_settings.debug):
                            _title_val = str(asset.get("title") or "")
                            log_info(LogTags.IDARR, f"Type inferred - also searching {_alt_type}: {_title_val!r}")
                        enrichment_stats["tmdb_search_api_calls"] += 1
                        alt_result, alt_reason = self._tmdb_search(
                            api_key=tmdb_api_key,
                            title=str(asset.get("title") or ""),
                            asset_type=_alt_type,
                            year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                            tmdb_id=asset.get("tmdb_id") if isinstance(asset.get("tmdb_id"), int) else None,
                            tvdb_id=asset.get("tvdb_id") if isinstance(asset.get("tvdb_id"), int) else None,
                            imdb_id=str(asset.get("imdb_id")) if isinstance(asset.get("imdb_id"), str) else None,
                        )
                        if alt_reason == "ambiguous":
                            asset["match_reason"] = "ambiguous"
                            grouped_unresolved_map.setdefault(cache_key, "ambiguous")
                            continue
                        _alt_rank = self._search_result_rank(alt_result)
                        _both_confident_and_close = (
                            alt_result is not None
                            and _alt_rank >= 1 and _primary_rank >= 1
                            and (
                                _alt_rank == _primary_rank
                                or (_alt_rank >= 2 and _primary_rank >= 2 and abs(_alt_rank - _primary_rank) <= 1)
                            )
                        )
                        if _both_confident_and_close:
                            asset["match_reason"] = "ambiguous"
                            grouped_unresolved_map.setdefault(cache_key, "ambiguous")
                            continue
                        if alt_result and _alt_rank > _primary_rank:
                            search_result = alt_result
                            asset['type'] = _alt_type
                            if _alt_type == "tv_series":
                                enrichment_stats["reclassified_tv"] += 1
                            else:
                                enrichment_stats["reclassified_movie"] += 1
                        if bool(app_settings.debug) and not search_result:
                            _title_warn = str(asset.get("title") or "")
                            _year_warn = asset.get("year")
                            log_warning(
                                LogTags.IDARR,
                                f"No confident match found for {_title_warn!r} ({_year_warn})",
                            )
                    if (
                        not search_result
                        and not asset.get("tmdb_id")
                    ):
                        grouped_unresolved_map.setdefault(cache_key, str(asset.get("match_reason") or "no_match"))

                    network_lookup_performed = True
                    if search_result and isinstance(search_result.get("id"), int):
                        asset["tmdb_id"] = int(search_result["id"])

                        # Check if this TMDB ID is already resolved in the cache
                        # (e.g. the same title was previously resolved with a TMDB tag).
                        # If so, pull the data from that row and skip re-enrichment.
                        _post_search_id_key = self._asset_key(
                            asset_type=str(asset.get("type") or ""),
                            title=str(asset.get("title") or ""),
                            year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                            tmdb_id=int(search_result["id"]),
                        )
                        _post_search_cache_row = cache_map.get(_post_search_id_key)
                        if _post_search_cache_row is not None and isinstance(_post_search_cache_row.payload_json, str):
                            try:
                                _cached_payload = json.loads(_post_search_cache_row.payload_json)
                            except Exception:
                                _cached_payload = {}
                            if _cached_payload:
                                for _f in ("new_title", "new_year", "tvdb_id", "imdb_id"):
                                    _src_key = {"new_title": "canonical_title", "new_year": "canonical_year"}.get(_f, _f)
                                    _val = _cached_payload.get(_src_key)
                                    if _val is not None and not asset.get(_f):
                                        asset[_f] = _val
                                if isinstance(_post_search_cache_row.tvdb_id, int) and not asset.get("tvdb_id"):
                                    asset["tvdb_id"] = int(_post_search_cache_row.tvdb_id)
                                if isinstance(_post_search_cache_row.imdb_id, str) and not asset.get("imdb_id"):
                                    asset["imdb_id"] = str(_post_search_cache_row.imdb_id)
                                asset["match_reason"] = "cache_hit_post_search"
                                cache_row = _post_search_cache_row

                        # Propagate TMDB canonical title/year to asset for filename generation
                        _tmdb_title = str(search_result.get("title") or search_result.get("name") or "").strip()
                        _tmdb_year = IdarrRunner._candidate_year(search_result)
                        current_title = str(asset.get("title") or "").strip()
                        if (
                            _tmdb_title
                            and _tmdb_title != current_title
                        ):
                            asset["new_title"] = _tmdb_title
                        if isinstance(_tmdb_year, int) and _tmdb_year != asset.get("year"):
                            asset["new_year"] = _tmdb_year
                        classification = self._classify_search_match(
                            candidate=search_result,
                            title=str(asset.get("title") or ""),
                            year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                        )
                        match_confidence = str(classification.get("confidence") or "unknown")
                        match_reason = str(classification.get("reason") or "rank_fallback")
                        match_score = int(classification.get("score") or 0)
                        asset["match_reason"] = match_reason

                        had_existing_ids = bool(before_tmdb or before_tvdb or before_imdb)
                        if self._is_low_confidence_alternate_match(
                            match_reason=match_reason,
                            match_confidence=match_confidence,
                            match_score=match_score,
                        ) and not had_existing_ids:
                            asset["review_required"] = True
                            asset["pending_reason"] = "low_confidence_alternate"
                            asset["match_reason"] = "low_confidence_alternate"
                            match_reason = "low_confidence_alternate"
                            match_confidence = "low"
                            if match_score > 55:
                                match_score = 55
                            grouped_unresolved_map.setdefault(cache_key, "low_confidence_alternate")

                            if bool(app_settings.debug):
                                log_warning(
                                    LogTags.IDARR,
                                    (
                                        f"↺ Review required for alternate-title match: "
                                        f"\"{str(asset.get('title') or '')}\" ({asset.get('year')})"
                                    ),
                                )
                        elif self._is_low_confidence_alternate_match(
                            match_reason=match_reason,
                            match_confidence=match_confidence,
                            match_score=match_score,
                        ) and bool(app_settings.debug):
                            log_info(
                                LogTags.IDARR,
                                (
                                    f"Retaining existing IDs for \"{str(asset.get('title') or '')}\" "
                                    f"({asset.get('year')}); skipping low-confidence pending escalation"
                                ),
                            )

                        if bool(app_settings.debug):
                            matched_title = str(search_result.get("title") or search_result.get("name") or str(asset.get("title") or "")).strip()
                            matched_year = IdarrRunner._candidate_year(search_result) or asset.get("year")
                            media_label = "tv_series" if str(asset.get("type") or "") == "tv_series" else "movie"
                            log_info(
                                LogTags.IDARR,
                                (
                                    f"🎯 {match_reason} match: "
                                    f"→ {matched_title} ({matched_year}) [{asset.get('tmdb_id')}] [{media_label}]"
                                ),
                            )

                should_fetch_external_ids = False
                if isinstance(asset.get("tmdb_id"), int):
                    _asset_type = str(asset.get("type") or "").strip().lower()
                    _is_movie = _asset_type == "movie"
                    _is_tv_series = _asset_type == "tv_series"
                    missing_imdb = not bool(asset.get("imdb_id"))
                    missing_tvdb = not bool(asset.get("tvdb_id"))
                    if _is_movie and missing_imdb and not general_recent:
                        should_fetch_external_ids = True
                    if _is_tv_series and missing_imdb and not general_recent:
                        should_fetch_external_ids = True
                    if _is_tv_series and missing_tvdb and not tvdb_recent:
                        should_fetch_external_ids = True

                if should_fetch_external_ids and isinstance(asset.get("tmdb_id"), int):
                    is_tv_series_asset = str(asset.get("type") or "").strip().lower() == "tv_series"
                    if bool(app_settings.debug):
                        _rehydrate_title = str(asset.get("title") or "unknown")
                        _rehydrate_tmdb_id = str(asset.get("tmdb_id") or "?")
                        _rehydrate_type = str(asset.get("type") or "movie")
                        _missing = []
                        if not bool(asset.get("imdb_id")):
                            _missing.append("imdb_id")
                        if is_tv_series_asset and not bool(asset.get("tvdb_id")):
                            _missing.append("tvdb_id")
                        log_debug(
                            LogTags.IDARR,
                            f"\U0001f504 Fetching external IDs for {_rehydrate_title} [{_rehydrate_tmdb_id}] ({_rehydrate_type}) — missing: {', '.join(_missing)}",
                        )
                    enrichment_stats["tmdb_external_id_api_calls"] += 1
                    external_ids = self._tmdb_external_ids(
                        api_key=tmdb_api_key,
                        tmdb_id=int(asset["tmdb_id"]),
                        asset_type=str(asset.get("type") or "movie"),
                    )
                    network_lookup_performed = True
                    if not asset.get("imdb_id") and isinstance(external_ids.get("imdb_id"), str):
                        asset["imdb_id"] = external_ids["imdb_id"]
                    if is_tv_series_asset and not asset.get("tvdb_id") and isinstance(external_ids.get("tvdb_id"), int):
                        asset["tvdb_id"] = external_ids["tvdb_id"]
                    if is_tv_series_asset and not before_tvdb and isinstance(asset.get("tvdb_id"), int):
                        enrichment_stats["tvdb_rehydrate_calls"] += 1
                    if bool(app_settings.debug):
                        _resolved = []
                        if not before_imdb and isinstance(asset.get("imdb_id"), str):
                            _resolved.append(f"imdb_id={asset['imdb_id']}")
                        if not before_tvdb and isinstance(asset.get("tvdb_id"), int):
                            _resolved.append(f"tvdb_id={asset['tvdb_id']}")
                        if _resolved:
                            log_debug(
                                LogTags.IDARR,
                                f"✓ Rehydrated external IDs for {str(asset.get('title') or 'unknown')} [{asset.get('tmdb_id')}]: {', '.join(_resolved)}",
                            )
                        else:
                            log_debug(
                                LogTags.IDARR,
                                f"○ Rehydrate returned nothing new for {str(asset.get('title') or 'unknown')} [{asset.get('tmdb_id')}]",
                            )
                    if match_reason == "unchanged":
                        match_reason = "external_ids_rehydrate"
                        if match_confidence == "unknown":
                            match_confidence = "medium"
                        if match_score == 0:
                            match_score = 55

                if not network_lookup_performed and (general_recent or tvdb_recent):
                    enrichment_stats["freshness_skipped"] += 1
                    if bool(app_settings.debug):
                        _cached_tmdb_id = asset.get("tmdb_id") if isinstance(asset.get("tmdb_id"), int) else None
                        _cached_tvdb_id = asset.get("tvdb_id") if isinstance(asset.get("tvdb_id"), int) else None
                        _cached_imdb_id = str(asset.get("imdb_id") or "").strip() or None
                        _cached_log_key = (
                            f"{cache_key}::tmdb={_cached_tmdb_id}" if _cached_tmdb_id
                            else f"{cache_key}::tvdb={_cached_tvdb_id}" if _cached_tvdb_id
                            else f"{cache_key}::imdb={_cached_imdb_id}" if _cached_imdb_id
                            else cache_key
                        )
                        if _cached_log_key not in grouped_cached_logged_keys:
                            grouped_cached_logged_keys.add(_cached_log_key)
                            display_title = str(asset.get("title") or "unknown").strip() or "unknown"
                            display_year = asset.get("year") if isinstance(asset.get("year"), int) else None
                            year_label = str(display_year) if isinstance(display_year, int) else "None"
                            season_labels = grouped_season_labels_by_key.get(cache_key, set())
                            grouped_suffix = ""
                            if season_labels:
                                grouped_suffix = f" — grouped posters: {self._format_season_labels(season_labels)}"

                            has_tmdb_id = isinstance(_cached_tmdb_id, int)
                            has_tvdb_id = isinstance(_cached_tvdb_id, int)
                            has_imdb_id = bool(_cached_imdb_id)
                            has_any_id = bool(has_tmdb_id or has_tvdb_id or has_imdb_id)

                            cache_state_label = "cached and unchanged"
                            if not has_any_id:
                                cache_state_label = "cached unresolved (no IDs, still unmatched)"
                            elif not has_tmdb_id:
                                cache_state_label = "cached unchanged (no TMDB ID; using TVDB/IMDB only)"

                            id_suffix = ""
                            if has_tmdb_id:
                                id_suffix = f" {{tmdb-{_cached_tmdb_id}}}"
                            elif has_tvdb_id:
                                id_suffix = f" {{tvdb-{_cached_tvdb_id}}}"
                            elif has_imdb_id:
                                id_suffix = f" {{imdb-{_cached_imdb_id}}}"

                            log_debug(
                                LogTags.IDARR,
                                f"📦 Used cached metadata for {display_title} ({year_label}){id_suffix}{grouped_suffix} [{cache_state_label}]",
                            )
                            log_debug(
                                LogTags.IDARR,
                                f"NO UPSERT NEEDED: {display_title} ({year_label}){id_suffix} - {cache_state_label}.",
                            )

                after_tmdb = asset.get("tmdb_id")
                after_tvdb = asset.get("tvdb_id")
                after_imdb = asset.get("imdb_id")

                if not before_tmdb and after_tmdb:
                    enrichment_stats["tmdb_assigned"] += 1
                if not before_tvdb and after_tvdb:
                    enrichment_stats["tvdb_assigned"] += 1
                if not before_imdb and after_imdb:
                    enrichment_stats["imdb_assigned"] += 1

                review_required = bool(asset.get("review_required", False))
                asset["has_id"] = bool(after_tmdb or after_tvdb or after_imdb) and not review_required
                if not asset["has_id"] and not str(asset.get("match_reason") or "").strip():
                    asset["match_reason"] = "no_match"

                if (before_tmdb, before_tvdb, before_imdb) != (after_tmdb, after_tvdb, after_imdb):
                    if match_confidence == "high":
                        enrichment_stats["confidence_high"] += 1
                    elif match_confidence == "medium":
                        enrichment_stats["confidence_medium"] += 1
                    elif match_confidence == "low":
                        enrichment_stats["confidence_low"] += 1
                    else:
                        enrichment_stats["confidence_unknown"] += 1

                    reason_counts = enrichment_stats.get("match_reason_counts")
                    if isinstance(reason_counts, dict):
                        reason_counts[match_reason] = int(reason_counts.get(match_reason, 0)) + 1

                    detail_rows.append(
                        {
                            "title": asset.get("title"),
                            "type": asset.get("type"),
                            "year": asset.get("year"),
                            "before": {"tmdb": before_tmdb, "tvdb": before_tvdb, "imdb": before_imdb},
                            "after": {"tmdb": after_tmdb, "tvdb": after_tvdb, "imdb": after_imdb},
                            "match_confidence": match_confidence,
                            "match_reason": match_reason,
                            "match_score": match_score,
                        }
                    )

                if bool(asset.get("has_id")):
                    canonical_title = str(asset.get("new_title") or asset.get("title") or "").strip()
                    canonical_year = asset.get("new_year") if isinstance(asset.get("new_year"), int) else (asset.get("year") if isinstance(asset.get("year"), int) else None)
                    grouped_resolution_map[cache_key] = {
                        "tmdb_id": asset.get("tmdb_id") if isinstance(asset.get("tmdb_id"), int) else None,
                        "tvdb_id": asset.get("tvdb_id") if isinstance(asset.get("tvdb_id"), int) else None,
                        "imdb_id": asset.get("imdb_id") if isinstance(asset.get("imdb_id"), str) else None,
                        "asset_type": str(asset.get("type") or ""),
                        "canonical_title": canonical_title,
                        "canonical_year": canonical_year,
                        "match_reason": str(asset.get("match_reason") or match_reason),
                        "match_confidence": match_confidence,
                        "match_score": match_score,
                    }
            except Exception as exc:
                enrichment_stats["errors"] += 1
                _safe_msg = re.sub(r"api_key=[^&\s]+", "api_key=***", str(exc))
                log_warning(LogTags.IDARR, f"TMDB enrichment failed for '{asset.get('title', 'unknown')}': {_safe_msg}")

            asset["_cache_touch"] = network_lookup_performed
            if bool(asset.get("has_id")):
                matched_with_any_id += 1

            cache_checkpoint_assets.append(asset)
            if cache_checkpoint_interval > 0 and len(cache_checkpoint_assets) >= cache_checkpoint_interval:
                try:
                    self._store_asset_cache_rows(cache_checkpoint_assets)
                    self.db.commit()
                    enrichment_stats["cache_checkpoint_commits"] = int(enrichment_stats.get("cache_checkpoint_commits", 0)) + 1
                    if bool(app_settings.debug):
                        log_debug(
                            LogTags.IDARR,
                            f"Checkpoint cache commit complete: rows={len(cache_checkpoint_assets)}",
                        )
                except Exception as exc:
                    self.db.rollback()
                    log_warning(LogTags.IDARR, f"Incremental cache checkpoint commit failed: {exc}")
                finally:
                    cache_checkpoint_assets.clear()

            if total_items > 0 and (index == 1 or index % progress_interval == 0 or index == total_items):
                pending_unmatched = max(0, index - matched_with_any_id)
                live_api_calls = int(enrichment_stats.get("tmdb_search_api_calls", 0)) + int(
                    enrichment_stats.get("tmdb_external_id_api_calls", 0)
                )
                log_info(
                    LogTags.IDARR,
                    (
                        f"Processing {index}/{total_items} items... "
                        f"matched={matched_with_any_id}, unmatched={pending_unmatched}, "
                        f"api_calls={live_api_calls}"
                    ),
                    processed=index,
                    total=total_items,
                    matched=matched_with_any_id,
                    unmatched=pending_unmatched,
                )
                if progress_callback:
                    try:
                        progress_callback(
                            index,
                            total_items,
                            (
                                f"Checking {index}/{total_items} assets "
                                f"(matched={matched_with_any_id}, unmatched={pending_unmatched})"
                            ),
                        )
                    except Exception:  # nosec B110
                        pass

        if cache_checkpoint_assets:
            try:
                self._store_asset_cache_rows(cache_checkpoint_assets)
                self.db.commit()
                enrichment_stats["cache_checkpoint_commits"] = int(enrichment_stats.get("cache_checkpoint_commits", 0)) + 1
                if bool(app_settings.debug):
                    log_debug(
                        LogTags.IDARR,
                        f"Final checkpoint cache commit complete: rows={len(cache_checkpoint_assets)}",
                    )
            except Exception as exc:
                self.db.rollback()
                log_warning(LogTags.IDARR, f"Final incremental cache checkpoint commit failed: {exc}")

        if grouped_unresolved_skip_counts:
            skipped_total = sum(int(value or 0) for value in grouped_unresolved_skip_counts.values())
            unresolved_titles = len(grouped_unresolved_skip_counts)
            log_info(
                LogTags.IDARR,
                (
                    f"Grouped unresolved dedupe summary: skipped {skipped_total} repeated TMDB search(es) "
                    f"across {unresolved_titles} title(s). Assets remain unmatched until resolved."
                ),
            )
            if bool(app_settings.debug):
                for unresolved_key, skipped_count in sorted(
                    grouped_unresolved_skip_counts.items(),
                    key=lambda item: int(item[1]),
                    reverse=True,
                ):
                    display_label = grouped_unresolved_display_labels.get(unresolved_key, unresolved_key)
                    unresolved_reason = grouped_unresolved_map.get(unresolved_key, "no_match")
                    log_debug(
                        LogTags.IDARR,
                        (
                            f"Grouped unresolved detail: {display_label} "
                            f"— skipped_repeated_searches={int(skipped_count)} reason={unresolved_reason}"
                        ),
                    )

        enrichment_stats["tmdb_api_calls"] = int(enrichment_stats.get("tmdb_search_api_calls", 0)) + int(
            enrichment_stats.get("tmdb_external_id_api_calls", 0)
        )

        return enrichment_stats, detail_rows

    def handle_data(
        self,
        assets: list[dict[str, Any]],
        tmdb_api_key: str,
        *,
        frequency_days: int,
        tvdb_frequency: int,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self._enrich_assets_with_tmdb(
            assets,
            tmdb_api_key,
            frequency_days=frequency_days,
            tvdb_frequency=tvdb_frequency,
            progress_callback=progress_callback,
        )

    def _find_unsupported_image_files(self, source_dir: Path, *, is_asset_drive: bool = False) -> list[Path]:
        """Return files with recognised-but-unsupported image extensions (e.g. .svg, .gif)."""
        result: list[Path] = []
        dirs_to_scan: list[Path]
        if is_asset_drive:
            dirs_to_scan = [
                source_dir / subtype
                for subtype in ("logos", "backdrops")
                if (source_dir / subtype).is_dir()
            ]
        else:
            dirs_to_scan = [source_dir]
        for scan_dir in dirs_to_scan:
            try:
                for entry in scan_dir.iterdir():
                    if entry.is_file() and not entry.name.startswith(".") and entry.suffix.lower() in UNSUPPORTED_IMAGE_EXTENSIONS:
                        result.append(entry)
            except Exception:
                pass
        return result

    def _remove_non_images(self, source_dir: Path, dry_run: bool) -> tuple[int, int]:
        removed_count = 0
        skipped_count = 0

        for entry in source_dir.iterdir():
            if not entry.is_file():
                continue

            if entry.suffix.lower() in IMAGE_EXTENSIONS:
                continue

            if dry_run:
                skipped_count += 1
                continue

            entry.unlink(missing_ok=True)
            removed_count += 1

        return removed_count, skipped_count

    def _asset_key(self, *, asset_type: str, title: str, year: int | None,
                   tmdb_id: int | None = None, tvdb_id: int | None = None,
                   imdb_id: str | None = None) -> str:
        """Delegate to the canonical model-level key builder, injecting the current scope token.

        Pass tmdb_id/tvdb_id/imdb_id to produce a resolved (ID-keyed) form.
        Omit all ID args to produce the provisional title/year-only form used for
        grouping and pending/ignore lookups.
        """
        return build_idarr_asset_key(asset_type, title, year, self._scope_token,
                                     tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id)

    @staticmethod
    def _pending_conflict_token(source_path: str | None) -> str | None:
        raw_source_path = str(source_path or "").strip()
        if not raw_source_path:
            return None

        normalized = raw_source_path
        try:
            normalized = str(Path(raw_source_path).resolve())
        except Exception:
            normalized = raw_source_path

        return hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]

    def _pending_asset_key(
        self,
        *,
        asset_type: str,
        title: str,
        year: int | None,
        pending_reason: str | None = None,
        source_path: str | None = None,
    ) -> str:
        base_key = self._asset_key(asset_type=asset_type, title=title, year=year)
        reason = str(pending_reason or "").strip().lower()
        if reason != "rename_conflict":
            return base_key

        conflict_token = self._pending_conflict_token(source_path)
        if not conflict_token:
            return base_key

        if self._scope_token:
            scope_suffix = f"::scope={self._scope_token}"
            if base_key.endswith(scope_suffix):
                key_without_scope = base_key[: -len(scope_suffix)]
                return f"{key_without_scope}::conflict={conflict_token}{scope_suffix}"

        return f"{base_key}::conflict={conflict_token}"

    def _load_pending_asset_keys(self) -> set[str]:
        try:
            rows = self._pending_query().all()
            return {
                str(row.asset_key)
                for row in rows
                if isinstance(row.asset_key, str) and row.asset_key.strip()
            }
        except Exception as exc:
            log_warning(LogTags.IDARR, f"Failed reading IDarr pending table: {exc}")
            return set()

    def _load_all_cache_rows(self, context: str) -> list[IdarrAssetCache]:
        try:
            return self._cache_query().all()
        except Exception as exc:
            log_warning(LogTags.IDARR, f"Failed reading IDarr cache rows for {context}: {exc}")
            return []

    def _delete_cache_by_key(self, asset_key: str) -> bool:
        key = str(asset_key or "").strip()
        if not key:
            return False

        row = self._cache_query().filter(IdarrAssetCache.asset_key == key).first()
        if not row:
            return False

        self.db.delete(row)
        self._pending_query().filter(IdarrPendingMatch.asset_key == key).delete(synchronize_session=False)
        self.db.commit()
        return True

    def _delete_cache_entries(self, query: str) -> int:
        raw = str(query or "").strip()
        if not raw:
            return 0

        deleted = 0
        direct_key_deleted = self._delete_cache_by_key(raw)
        if direct_key_deleted:
            return 1

        if raw.isdigit():
            tmdb_value = int(raw)
            rows = self._cache_query().filter(IdarrAssetCache.tmdb_id == tmdb_value).all()
            for row in rows:
                if isinstance(row.asset_key, str):
                    self._pending_query().filter(IdarrPendingMatch.asset_key == row.asset_key).delete(synchronize_session=False)
                self.db.delete(row)
                deleted += 1
            if deleted:
                self.db.commit()
            return deleted

        title = raw
        year: int | None = None
        year_match = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", raw)
        if year_match:
            title = str(year_match.group(1) or "").strip()
            year = int(year_match.group(2))

        if not title:
            return 0

        normalized_title = normalize_cache_key(title)
        rows = self._load_all_cache_rows("delete by title")
        target_rows: list[IdarrAssetCache] = []
        for row in rows:
            row_title = str(row.title or "").strip()
            if not row_title:
                continue
            if normalize_cache_key(row_title) != normalized_title:
                continue
            if isinstance(year, int):
                row_year = row.year if isinstance(row.year, int) else None
                if row_year != year:
                    continue
            target_rows.append(row)

        for row in target_rows:
            if isinstance(row.asset_key, str):
                self._pending_query().filter(IdarrPendingMatch.asset_key == row.asset_key).delete(synchronize_session=False)
            self.db.delete(row)
            deleted += 1

        if deleted:
            self.db.commit()
        return deleted

    def _load_ignored_asset_keys(self) -> set[str]:
        setting = get_setting(self.db, SETTING_MAKER_IDARR_IGNORED_TITLES)
        if not setting or not setting.value:
            return set()
        try:
            payload = json.loads(setting.value)
        except Exception:
            return set()
        if not isinstance(payload, list):
            return set()

        keys: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = item.get("asset_key")
            if isinstance(key, str) and key.strip():
                if self._asset_key_in_scope(key):
                    keys.update(self._expand_asset_key_aliases(key))
        return keys

    def _store_asset_cache_rows(self, assets: list[dict[str, Any]]) -> None:
        aggregated: dict[str, dict[str, Any]] = {}

        for asset in assets:
            title = str(asset.get("title") or "").strip()
            asset_type = str(asset.get("type") or "").strip().lower()
            if not title or not asset_type:
                continue

            year = asset.get("year") if isinstance(asset.get("year"), int) else None
            tmdb_id = int(asset.get("tmdb_id")) if isinstance(asset.get("tmdb_id"), int) else None
            tvdb_id = int(asset.get("tvdb_id")) if isinstance(asset.get("tvdb_id"), int) else None
            imdb_id = str(asset.get("imdb_id") or "").strip() if isinstance(asset.get("imdb_id"), str) else None

            # Use the resolved (ID-keyed) form when we know the ID so that two
            # distinct items with the same title/year get separate rows.
            key = self._asset_key(asset_type=asset_type, title=title, year=year,
                                  tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id)

            entry = aggregated.get(key)
            if not entry:
                entry = {
                    "asset_key": key,
                    "title": title,
                    "year": year,
                    # Use empty string sentinel so the second loop can fall back to
                    # existing_canonical_title from the DB when new_title was not set.
                    "canonical_title": str(asset.get("new_title") or "").strip(),
                    "canonical_year": asset.get("new_year") if isinstance(asset.get("new_year"), int) else year,
                    "asset_type": asset_type,
                    "tmdb_id": None,
                    "tvdb_id": None,
                    "imdb_id": None,
                    "matched": False,
                    "touch_checked_at": False,
                    "filenames": set(),
                }
                aggregated[key] = entry

            incoming_canonical_title = str(asset.get("new_title") or "").strip()
            if incoming_canonical_title:
                entry["canonical_title"] = incoming_canonical_title
            if isinstance(asset.get("new_year"), int):
                entry["canonical_year"] = int(asset.get("new_year"))

            if isinstance(asset.get("tmdb_id"), int):
                entry["tmdb_id"] = int(asset["tmdb_id"])
            # Only persist tvdb_id for TV series — movies and collections do not have
            # TVDB IDs, so never write one into their cache row to prevent a stale
            # tvdb_id from being pre-filled on the next run and causing oscillation.
            if asset_type == "tv_series" and isinstance(asset.get("tvdb_id"), int):
                entry["tvdb_id"] = int(asset["tvdb_id"])
            if isinstance(asset.get("imdb_id"), str) and str(asset.get("imdb_id") or "").startswith("tt"):
                entry["imdb_id"] = str(asset["imdb_id"])

            entry["matched"] = bool(entry["matched"] or asset.get("has_id", False))
            entry["touch_checked_at"] = bool(entry["touch_checked_at"] or asset.get("_cache_touch", False))

            file_path = asset.get("file_path")
            if file_path:
                entry["filenames"].add(str(Path(file_path).name))

        for key, entry in aggregated.items():
            row = self._cache_query().filter(IdarrAssetCache.asset_key == key).first()

            touch_checked_at = bool(entry.get("touch_checked_at", False))

            # When the ID-keyed row doesn't exist yet, look for a matching provisional
            # (title/year-only) row to absorb its payload and timestamps into the new
            # ID-keyed row and then delete it so no dangling provisional rows remain.
            provisional_row: IdarrAssetCache | None = None
            provisional_key = self._asset_key(
                asset_type=str(entry["asset_type"]),
                title=str(entry["title"]),
                year=entry["year"] if isinstance(entry.get("year"), int) else None,
            )
            if row is None and key != provisional_key:
                provisional_row = self._cache_query().filter(IdarrAssetCache.asset_key == provisional_key).first()

            incoming_title = str(entry["title"])
            incoming_year = entry["year"] if isinstance(entry.get("year"), int) else None
            incoming_asset_type = str(entry["asset_type"])
            incoming_tmdb_id = entry["tmdb_id"] if isinstance(entry.get("tmdb_id"), int) else None
            incoming_tvdb_id = entry["tvdb_id"] if isinstance(entry.get("tvdb_id"), int) else None
            incoming_imdb_id = entry["imdb_id"] if isinstance(entry.get("imdb_id"), str) else None

            predecessor_payload: dict[str, Any] = {}
            if row is None and provisional_row is None:
                _id_filter = None
                if incoming_tmdb_id is not None:
                    _id_filter = IdarrAssetCache.tmdb_id == incoming_tmdb_id
                elif incoming_tvdb_id is not None:
                    _id_filter = IdarrAssetCache.tvdb_id == incoming_tvdb_id
                elif incoming_imdb_id:
                    _id_filter = IdarrAssetCache.imdb_id == incoming_imdb_id
                if _id_filter is not None:
                    _pred = (
                        self._cache_query()
                        .filter(
                            IdarrAssetCache.asset_key != key,
                            IdarrAssetCache.asset_type == incoming_asset_type,
                            _id_filter,
                        )
                        .first()
                    )
                    if _pred and isinstance(_pred.payload_json, str) and _pred.payload_json.strip():
                        try:
                            _parsed_pred = json.loads(_pred.payload_json)
                            if isinstance(_parsed_pred, dict):
                                predecessor_payload = _parsed_pred
                        except Exception:
                            predecessor_payload = {}

            resolved_title = incoming_title
            resolved_year = incoming_year
            # Defer canonical_title resolution until after existing_payload is loaded
            # so we can fall back to the preserved existing value (e.g. from a prior
            # manual resolve) when the current run's TMDB enrichment didn't set new_title.
            resolved_canonical_title = str(entry.get("canonical_title") or incoming_title).strip() or incoming_title
            resolved_canonical_year = entry.get("canonical_year") if isinstance(entry.get("canonical_year"), int) else incoming_year
            resolved_asset_type = incoming_asset_type
            resolved_tmdb_id = incoming_tmdb_id
            resolved_tvdb_id = incoming_tvdb_id
            resolved_imdb_id = incoming_imdb_id

            existing_payload: dict[str, Any] = {}
            _payload_source = row if row is not None else provisional_row
            if _payload_source and isinstance(_payload_source.payload_json, str) and _payload_source.payload_json.strip():
                try:
                    parsed = json.loads(_payload_source.payload_json)
                    if isinstance(parsed, dict):
                        existing_payload = parsed
                except Exception:
                    existing_payload = {}

            existing_canonical_title = str(existing_payload.get("canonical_title") or "").strip()
            existing_canonical_year = existing_payload.get("canonical_year") if isinstance(existing_payload.get("canonical_year"), int) else None

            # If the current run did not determine a new_title (entry["canonical_title"] is
            # empty) but the DB already has a good canonical_title (e.g. stored during a
            # manual resolve), preserve it instead of overwriting with the raw dirty title. Fall
            # back to a renamed-from predecessor row's canonical_title when this row is brand new.
            if not str(entry.get("canonical_title") or "").strip():
                _fallback_canonical = existing_canonical_title or str(predecessor_payload.get("canonical_title") or "").strip()
                if _fallback_canonical:
                    resolved_canonical_title = _fallback_canonical

            if row:
                identity_diffs = {
                    "title": (normalize_cache_key(str(row.title or "")), normalize_cache_key(incoming_title)),
                    "year": (row.year if isinstance(row.year, int) else None, incoming_year),
                    "asset_type": (str(row.asset_type or ""), incoming_asset_type),
                    "tmdb_id": (row.tmdb_id if isinstance(row.tmdb_id, int) else None, incoming_tmdb_id),
                    "tvdb_id": (row.tvdb_id if isinstance(row.tvdb_id, int) else None, incoming_tvdb_id),
                    "imdb_id": (row.imdb_id if isinstance(row.imdb_id, str) else None, incoming_imdb_id),
                }
                changed_identity = any(before != after for before, after in identity_diffs.values())

                existing_checked_at = row.last_checked_at or row.updated_at
                incoming_is_newer = bool(entry.get("touch_checked_at", False)) or existing_checked_at is None

                # Detect a year-only drift where all IDs are stable — this means TMDB
                # updated their release year for the same item (e.g. after a cache clear
                # and re-run). Trust the incoming year and let it self-heal rather than
                # freezing the stale stored value forever.
                year_only_tmdb_drift = (
                    changed_identity
                    and identity_diffs["year"][0] != identity_diffs["year"][1]
                    and identity_diffs["title"][0] == identity_diffs["title"][1]
                    and identity_diffs["asset_type"][0] == identity_diffs["asset_type"][1]
                    and identity_diffs["tmdb_id"][0] is not None
                    and identity_diffs["tmdb_id"][0] == identity_diffs["tmdb_id"][1]
                    and identity_diffs["tvdb_id"][0] == identity_diffs["tvdb_id"][1]
                    and identity_diffs["imdb_id"][0] == identity_diffs["imdb_id"][1]
                )

                if changed_identity and not incoming_is_newer and not year_only_tmdb_drift:
                    resolved_title = str(row.title or incoming_title)
                    resolved_year = row.year if isinstance(row.year, int) else incoming_year
                    resolved_canonical_title = existing_canonical_title or resolved_title
                    resolved_canonical_year = existing_canonical_year if isinstance(existing_canonical_year, int) else resolved_year
                    resolved_asset_type = str(row.asset_type or incoming_asset_type)
                    resolved_tmdb_id = row.tmdb_id if isinstance(row.tmdb_id, int) else incoming_tmdb_id
                    resolved_tvdb_id = row.tvdb_id if isinstance(row.tvdb_id, int) else incoming_tvdb_id
                    resolved_imdb_id = row.imdb_id if isinstance(row.imdb_id, str) else incoming_imdb_id

                    if bool(app_settings.debug):
                        log_info(
                            LogTags.IDARR,
                            f"Cache collision resolved by keeping existing identity for key '{key}'",
                        )

            existing_current = existing_payload.get("current_filenames")
            existing_original = existing_payload.get("original_filenames")
            existing_history = existing_payload.get("rename_history")
            existing_status = str(existing_payload.get("status") or "").strip().lower()

            current_filenames = {
                str(name)
                for name in entry.get("filenames", set())
                if isinstance(name, str) and name.strip()
            }
            if isinstance(existing_current, list):
                current_filenames.update(str(name) for name in existing_current if isinstance(name, str) and name.strip())

            original_filenames = set(current_filenames)
            if isinstance(existing_original, list):
                original_filenames.update(str(name) for name in existing_original if isinstance(name, str) and name.strip())

            payload_status = existing_status if existing_status == "ignored" else ("found" if bool(entry["matched"]) else "not_found")

            if resolved_asset_type == "collection":
                if resolved_canonical_title:
                    resolved_title = resolved_canonical_title
                if isinstance(resolved_canonical_year, int):
                    resolved_year = resolved_canonical_year

            cache_payload = {
                "title": resolved_title,
                "year": resolved_year,
                "canonical_title": resolved_canonical_title,
                "canonical_year": resolved_canonical_year,
                "type": resolved_asset_type,
                "tmdb_id": resolved_tmdb_id,
                "tvdb_id": resolved_tvdb_id,
                "imdb_id": resolved_imdb_id,
                "current_filenames": sorted(current_filenames),
                "original_filenames": sorted(original_filenames),
                "rename_history": self._compact_rename_history(existing_history),
                "status": payload_status,
                # Preserve resolve-workflow metadata so the enrichment step can detect
                # manually-resolved items that are still missing a canonical title. Fall back to a
                # renamed-from predecessor row so the audit trail survives a wholesale title re-key.
                "resolved_manually": existing_payload.get("resolved_manually") or predecessor_payload.get("resolved_manually") or None,
                "last_resolved_at": existing_payload.get("last_resolved_at") or predecessor_payload.get("last_resolved_at") or None,
                "resolution_history": existing_payload.get("resolution_history") or predecessor_payload.get("resolution_history") or None,
                "pending_entry": existing_payload.get("pending_entry") or None,
                "candidate_reviews": existing_payload.get("candidate_reviews") or predecessor_payload.get("candidate_reviews") or None,
                "pending_reason": existing_payload.get("pending_reason") or None,
            }

            upserted = upsert_idarr_asset_cache(
                self.db,
                asset_key=key,
                title=resolved_title,
                year=resolved_year,
                asset_type=resolved_asset_type,
                tmdb_id=resolved_tmdb_id,
                tvdb_id=resolved_tvdb_id,
                imdb_id=resolved_imdb_id,
                matched=bool(entry.get("matched", False)),
                payload_json=json.dumps(cache_payload),
                touch_checked_at=touch_checked_at,
            )

            # If the ID-keyed row was just created from a provisional row, delete the
            # provisional row and inherit its last_checked_at when touch was suppressed.
            if provisional_row is not None:
                self._pending_query().filter(IdarrPendingMatch.asset_key == provisional_key).delete(synchronize_session=False)
                self.db.delete(provisional_row)
                self._pending_resolved_mid_run += 1
                if not touch_checked_at and provisional_row.last_checked_at is not None:
                    upserted.last_checked_at = provisional_row.last_checked_at



    def _prune_inactive_cache_keys_for_source(self, source_dir: Path, active_keys: set[str]) -> dict[str, int]:
        source_files = {
            entry.name
            for entry in source_dir.iterdir()
            if entry.is_file() and not entry.name.startswith(".")
        }
        if not source_files:
            return {"removed_cache": 0, "removed_pending": 0}

        rows = self._load_all_cache_rows("inactive prune")
        if not rows:
            return {"removed_cache": 0, "removed_pending": 0}
        keys_to_remove: set[str] = set()
        removed_labels: list[str] = []

        for row in rows:
            if not isinstance(row.asset_key, str) or not row.asset_key.strip():
                continue
            asset_key = str(row.asset_key)
            if asset_key in active_keys:
                continue

            payload: dict[str, Any] = {}
            if isinstance(row.payload_json, str) and row.payload_json.strip():
                try:
                    parsed = json.loads(row.payload_json)
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}

            filenames = set()
            for field_name in ("current_filenames", "original_filenames"):
                field_value = payload.get(field_name)
                if isinstance(field_value, list):
                    for value in field_value:
                        if isinstance(value, str) and value.strip():
                            filenames.add(Path(value).name)

            if filenames and filenames & source_files:
                keys_to_remove.add(asset_key)
                removed_labels.append(self._format_pending_row_label(row))

        if not keys_to_remove:
            return {"removed_cache": 0, "removed_pending": 0}

        removed_cache = (
            self._cache_query()
            .filter(IdarrAssetCache.asset_key.in_(list(keys_to_remove)))
            .delete(synchronize_session=False)
        )
        removed_pending = (
            self._pending_query()
            .filter(IdarrPendingMatch.asset_key.in_(list(keys_to_remove)))
            .delete(synchronize_session=False)
        )

        log_info(
            LogTags.IDARR,
            f"Inactive cache prune complete for source: removed_cache={int(removed_cache or 0)}, removed_pending={int(removed_pending or 0)}",
            removed_cache=int(removed_cache or 0),
            removed_pending=int(removed_pending or 0),
        )
        for label in removed_labels[:25]:
            log_info(LogTags.IDARR, f"• Inactive cache entry removed: {label}")
        if len(removed_labels) > 25:
            log_info(LogTags.IDARR, f"Additional inactive cache entries removed: {len(removed_labels) - 25}")

        return {
            "removed_cache": int(removed_cache or 0),
            "removed_pending": int(removed_pending or 0),
        }

    @staticmethod
    def _format_pending_row_label(row: Any) -> str:
        """Human-readable ``Title (Year) [type]`` label for a pending/cache row, used
        when logging which items were pruned or cleared."""
        title = str(getattr(row, "title", "") or "").strip() or "unknown"
        year = getattr(row, "year", None)
        asset_type = str(getattr(row, "asset_type", "") or "").strip()
        year_label = str(year) if isinstance(year, int) else "None"
        type_suffix = f" [{asset_type}]" if asset_type else ""
        return f"{title} ({year_label}){type_suffix}"

    def _prune_orphaned_pending_cache_rows(self) -> int:
        """Delete ``pending::``-typed placeholder cache rows that have no backing
        pending-match row.

        Unmatched items are stored as a ``pending::``-keyed cache row alongside their
        ``idarr_pending_matches`` entry. When the item is resolved, resolution writes the
        data under the real-type/ID key and removes the pending-match row; when it is
        cleared or goes stale the pending-match row is dropped too. In both cases the
        ``pending::`` cache row can be left dangling. Reconcile against the live
        pending-match table (scope-aware via ``_cache_query``/``_pending_query``).
        """
        cache_rows = self._cache_query().filter(IdarrAssetCache.asset_key.like("pending::%")).all()
        if not cache_rows:
            return 0

        live_pending_keys = {
            str(row.asset_key)
            for row in self._pending_query().all()
            if isinstance(row.asset_key, str) and row.asset_key.strip()
        }

        removed = 0
        removed_labels: list[str] = []
        for row in cache_rows:
            if not isinstance(row.asset_key, str) or row.asset_key in live_pending_keys:
                continue
            removed_labels.append(self._format_pending_row_label(row))
            self.db.delete(row)
            removed += 1

        if removed:
            self.db.commit()
            log_info(
                LogTags.IDARR,
                f"Pruned {removed} orphaned pending cache row(s) with no active pending match",
                removed_orphaned_pending=removed,
            )
            for label in removed_labels:
                log_info(LogTags.IDARR, f"• Orphaned pending cache row removed: {label}")
        return removed

    def _store_pending_assets(
        self,
        unmatched_assets: list[dict[str, Any]],
        scoped_asset_keys: set[str] | None = None,
    ) -> None:
        payload: list[dict[str, Any]] = []
        for item in unmatched_assets:
            title = str(item.get("title") or "").strip()
            asset_type = str(item.get("type") or "").strip().lower()
            if not title or not asset_type:
                continue

            pending_reason_raw = str(item.get("pending_reason") or item.get("match_reason") or "").strip().lower()
            pending_reason = ""
            if pending_reason_raw in {
                "rename_conflict",
                "in_place_conflict_kept_existing",
                # archived_older_existing_renamed_newer_incoming is intentionally excluded:
                # it represents a successful replacement (newer poster won) and needs no review.
                "archived_older_incoming_kept_newer_existing",
                "in_place_conflict_archive_failed",
                "in_place_conflict_rename_failed_after_archive",
            }:
                pending_reason = "rename_conflict"
            elif pending_reason_raw in {"low_confidence_alternate", "review_required_low_confidence_alternate"}:
                pending_reason = "low_confidence_alternate"

            raw_conflict_files = item.get("conflict_files")
            raw_conflict_file_paths = item.get("conflict_file_paths")
            payload.append(
                {
                    "title": title,
                    "year": item.get("year") if isinstance(item.get("year"), int) else None,
                    "type": asset_type,
                    "pending_reason": pending_reason,
                    "source_path": str(item.get("source_path") or "").strip() or None,
                    "conflict_files": raw_conflict_files if isinstance(raw_conflict_files, list) else None,
                    "conflict_file_paths": raw_conflict_file_paths if isinstance(raw_conflict_file_paths, list) else None,
                }
            )

        desired_keys: set[str] = set()
        for item in payload:
            key = self._pending_asset_key(
                asset_type=str(item.get("type") or ""),
                title=str(item.get("title") or ""),
                year=item.get("year") if isinstance(item.get("year"), int) else None,
                pending_reason=str(item.get("pending_reason") or ""),
                source_path=str(item.get("source_path") or ""),
            )
            desired_keys.add(key)
            upsert_idarr_pending_match(
                self.db,
                asset_key=key,
                title=str(item.get("title") or ""),
                year=item.get("year") if isinstance(item.get("year"), int) else None,
                asset_type=str(item.get("type") or "").lower(),
            )

            cache_row = self._cache_query().filter(IdarrAssetCache.asset_key == key).first()
            cache_payload: dict[str, Any] = {}
            if cache_row and isinstance(cache_row.payload_json, str) and cache_row.payload_json.strip():
                try:
                    parsed = json.loads(cache_row.payload_json)
                    if isinstance(parsed, dict):
                        cache_payload = parsed
                except Exception:
                    cache_payload = {}

            source_path_str = str(item.get("source_path") or "").strip()
            if source_path_str and not cache_payload.get("current_filenames"):
                filename = Path(source_path_str).name
                if filename:
                    cache_payload["current_filenames"] = [filename]

            pending_entry = make_pending_entry_payload(
                title=str(item.get("title") or ""),
                year=item.get("year") if isinstance(item.get("year"), int) else None,
                files=(
                    source_path_str
                    or cache_payload.get("current_filenames")
                    or cache_payload.get("original_filenames")
                    or cache_payload.get("files")
                ),
            )
            pending_reason = str(item.get("pending_reason") or "").strip().lower()
            if pending_reason:
                cache_payload["pending_reason"] = pending_reason
                pending_entry["reason"] = pending_reason
            else:
                cache_payload.pop("pending_reason", None)
                pending_entry.pop("reason", None)
            conflict_files = item.get("conflict_files")
            if isinstance(conflict_files, list) and conflict_files:
                cache_payload["conflict_files"] = [str(f) for f in conflict_files if f]
            else:
                cache_payload.pop("conflict_files", None)
            conflict_file_paths = item.get("conflict_file_paths")
            if isinstance(conflict_file_paths, list) and conflict_file_paths:
                cache_payload["conflict_file_paths"] = [str(p) for p in conflict_file_paths if p]
            else:
                cache_payload.pop("conflict_file_paths", None)
            cache_payload["pending_entry"] = pending_entry
            if not str(cache_payload.get("status") or "").strip():
                cache_payload["status"] = "not_found"

            upsert_idarr_asset_cache(
                self.db,
                asset_key=key,
                title=str(item.get("title") or ""),
                year=item.get("year") if isinstance(item.get("year"), int) else None,
                asset_type=str(item.get("type") or "").lower(),
                tmdb_id=cache_row.tmdb_id if cache_row and isinstance(cache_row.tmdb_id, int) else None,
                tvdb_id=cache_row.tvdb_id if cache_row and isinstance(cache_row.tvdb_id, int) else None,
                imdb_id=cache_row.imdb_id if cache_row and isinstance(cache_row.imdb_id, str) else None,
                matched=bool(cache_row.matched) if cache_row else False,
                payload_json=json.dumps(cache_payload),
                touch_checked_at=False,
            )

        existing_rows = self._pending_query().all()
        if scoped_asset_keys is not None:
            existing_rows = [
                row
                for row in existing_rows
                if isinstance(row.asset_key, str) and row.asset_key in scoped_asset_keys
            ]
        existing_keys = [str(row.asset_key) for row in existing_rows if isinstance(row.asset_key, str) and row.asset_key not in desired_keys]
        resolved_cache_keys: set[str] = set()
        unresolved_cache_keys: set[str] = set()
        manually_reviewed_keys: set[str] = set()
        if existing_keys:
            cache_rows = self._cache_query().filter(IdarrAssetCache.asset_key.in_(existing_keys)).all()
            for row in cache_rows:
                if not isinstance(row.asset_key, str):
                    continue
                key = str(row.asset_key)

                is_resolved = bool(
                    row.matched
                    or isinstance(row.tmdb_id, int)
                    or isinstance(row.tvdb_id, int)
                    or (isinstance(row.imdb_id, str) and row.imdb_id.strip())
                )

                payload: dict[str, Any] = {}
                if isinstance(row.payload_json, str) and row.payload_json.strip():
                    try:
                        parsed = json.loads(row.payload_json)
                        if isinstance(parsed, dict):
                            payload = parsed
                    except Exception:
                        payload = {}

                status = str(payload.get("status") or "").strip().lower()
                is_unresolved = not is_resolved and status in {"", "not_found", "tmdb_removed"}

                if payload.get("pending_status"):
                    manually_reviewed_keys.add(key)

                if is_resolved:
                    resolved_cache_keys.add(key)
                elif is_unresolved:
                    unresolved_cache_keys.add(key)

        is_targeted_run = scoped_asset_keys is not None
        removed_resolved = 0
        removed_stale = 0
        removed_resolved_items: list[str] = []
        removed_stale_items: list[str] = []
        def _drop_pending_placeholder(asset_key: Any) -> None:
            if isinstance(asset_key, str) and asset_key.startswith("pending::"):
                self._cache_query().filter(IdarrAssetCache.asset_key == asset_key).delete(synchronize_session=False)

        for row in existing_rows:
            if row.asset_key in desired_keys:
                continue
            if isinstance(row.asset_key, str) and row.asset_key in resolved_cache_keys:
                # Rows the user manually resolved stay in the reviewed list until the
                # next full run — targeted single-file renames must not clear them.
                if is_targeted_run and row.asset_key in manually_reviewed_keys:
                    continue
                self.db.delete(row)
                _drop_pending_placeholder(row.asset_key)
                removed_resolved += 1
                removed_resolved_items.append(self._format_pending_row_label(row))
                continue
            if isinstance(row.asset_key, str) and row.asset_key not in unresolved_cache_keys:
                self.db.delete(row)
                _drop_pending_placeholder(row.asset_key)
                removed_stale += 1
                removed_stale_items.append(self._format_pending_row_label(row))

        self.db.commit()
        removed_resolved_total = removed_resolved + self._pending_resolved_mid_run
        log_info(
            LogTags.IDARR,
            f"Pending matches updated: active={self._pending_query().count()}, unresolved_this_run={len(payload)}, removed_resolved={removed_resolved_total}, removed_stale={removed_stale}",
            pending_count=len(payload),
            removed_resolved=removed_resolved_total,
            removed_resolved_mid_run=self._pending_resolved_mid_run,
            removed_stale=removed_stale,
        )
        for label in removed_resolved_items:
            log_info(LogTags.IDARR, f"• Pending match removed (resolved): {label}")
        for label in removed_stale_items:
            log_info(LogTags.IDARR, f"• Pending match removed (stale, no longer on disk): {label}")

    def _collect_conflict_pending_assets(
        self,
        *,
        assets: list[dict[str, Any]],
        conflict_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not conflict_rows:
            return []

        asset_by_source_path: dict[str, dict[str, Any]] = {}
        for asset in assets:
            file_path = asset.get("file_path")
            if not isinstance(file_path, Path):
                continue
            path_text = str(file_path)
            asset_by_source_path[path_text] = asset
            resolved_path_text: str | None = None
            try:
                resolved_path_text = str(file_path.resolve())
            except Exception:
                resolved_path_text = None
            if resolved_path_text:
                asset_by_source_path[resolved_path_text] = asset

        conflict_assets: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for row in conflict_rows:
            if not isinstance(row, dict):
                continue

            resolution = str(row.get("resolution") or "")

            # A successfully resolved replacement is NOT a conflict that needs review.
            if resolution == "archived_older_existing_renamed_newer_incoming":
                continue

            # Intra-batch conflict: multiple source files → same target filename.
            # Build one pending entry covering all the conflicting files.
            if resolution == "intra_batch_filename_conflict":
                raw_source_paths = row.get("source_paths")
                source_paths_list = raw_source_paths if isinstance(raw_source_paths, list) else []
                if not source_paths_list:
                    sp = str(row.get("source_path") or "").strip()
                    if sp:
                        source_paths_list = [sp]
                if not source_paths_list:
                    continue

                matched_asset = None
                for sp in source_paths_list:
                    matched_asset = asset_by_source_path.get(sp)
                    if matched_asset is None:
                        try:
                            matched_asset = asset_by_source_path.get(str(Path(sp).resolve()))
                        except Exception:
                            pass
                    if matched_asset is not None:
                        break
                if matched_asset is None:
                    continue

                title = str(matched_asset.get("title") or "").strip()
                asset_type = str(matched_asset.get("type") or "").strip().lower()
                year = matched_asset.get("year") if isinstance(matched_asset.get("year"), int) else None
                if not title or not asset_type:
                    continue

                dedupe_key = self._pending_asset_key(
                    asset_type=asset_type,
                    title=title,
                    year=year,
                    pending_reason="rename_conflict",
                    source_path=source_paths_list[0],
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)

                conflict_file_names = [Path(sp).name for sp in source_paths_list if sp]
                conflict_assets.append(
                    {
                        "title": title,
                        "year": year,
                        "type": asset_type,
                        "match_reason": "rename_conflict",
                        "pending_reason": "rename_conflict",
                        "source_path": source_paths_list[0],
                        "conflict_files": conflict_file_names,
                        "conflict_file_paths": [str(sp) for sp in source_paths_list if sp],
                    }
                )
                continue

            source_path = str(row.get("source_path") or "").strip()
            if not source_path:
                continue

            matched_asset = asset_by_source_path.get(source_path)
            if matched_asset is None:
                try:
                    matched_asset = asset_by_source_path.get(str(Path(source_path).resolve()))
                except Exception:
                    matched_asset = None
            if matched_asset is None:
                continue

            title = str(matched_asset.get("title") or "").strip()
            asset_type = str(matched_asset.get("type") or "").strip().lower()
            year = matched_asset.get("year") if isinstance(matched_asset.get("year"), int) else None
            if not title or not asset_type:
                continue

            dedupe_key = self._pending_asset_key(
                asset_type=asset_type,
                title=title,
                year=year,
                pending_reason="rename_conflict",
                source_path=source_path,
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            conflict_assets.append(
                {
                    "title": title,
                    "year": year,
                    "type": asset_type,
                    "match_reason": "rename_conflict",
                    "pending_reason": "rename_conflict",
                    "source_path": source_path,
                }
            )

        return conflict_assets

    def _sync_ignore_and_pending(self, ignored_asset_keys: set[str]) -> dict[str, int]:
        removed_pending = 0
        marked_ignored = 0
        restored_pending = 0
        unignored_reactivated = 0

        pending_rows = self._pending_query().all()
        current_pending_keys = {
            str(row.asset_key)
            for row in pending_rows
            if isinstance(row.asset_key, str) and row.asset_key.strip()
        }

        for row in pending_rows:
            asset_key = str(row.asset_key or "").strip()
            if not asset_key:
                continue
            if asset_key in ignored_asset_keys:
                self.db.delete(row)
                removed_pending += 1
                current_pending_keys.discard(asset_key)

        cache_rows = self._cache_query().all()
        for row in cache_rows:
            asset_key = str(row.asset_key or "").strip()
            if not asset_key:
                continue

            payload: dict[str, Any] = {}
            if isinstance(row.payload_json, str) and row.payload_json.strip():
                try:
                    parsed = json.loads(row.payload_json)
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}

            status = str(payload.get("status") or "").strip().lower()
            payload_changed = False

            if asset_key in ignored_asset_keys:
                if status != "ignored":
                    payload["status"] = "ignored"
                    payload_changed = True
                    marked_ignored += 1
            elif status == "ignored":
                payload["status"] = "not_found"
                payload_changed = True
                unignored_reactivated += 1

                resolved = bool(
                    row.matched
                    or isinstance(row.tmdb_id, int)
                    or isinstance(row.tvdb_id, int)
                    or (isinstance(row.imdb_id, str) and row.imdb_id.strip())
                )
                if not resolved and asset_key not in current_pending_keys:
                    upsert_idarr_pending_match(
                        self.db,
                        asset_key=asset_key,
                        title=str(row.title or "").strip(),
                        year=row.year if isinstance(row.year, int) else None,
                        asset_type=str(row.asset_type or "").strip().lower(),
                    )
                    current_pending_keys.add(asset_key)
                    restored_pending += 1

            if payload_changed:
                row.payload_json = json.dumps(payload)

        self.db.commit()
        return {
            "removed_pending": removed_pending,
            "marked_ignored": marked_ignored,
            "restored_pending": restored_pending,
            "unignored_reactivated": unignored_reactivated,
        }

    def load_ignore_and_sync_pending(self) -> tuple[set[str], dict[str, int]]:
        ignored_asset_keys = self._load_ignored_asset_keys()
        sync_stats = self._sync_ignore_and_pending(ignored_asset_keys)
        return ignored_asset_keys, sync_stats

    def _prune_orphaned_cache_entries(self, source_dir: Path) -> dict[str, int]:
        log_info(LogTags.IDARR, "\U0001f9f9 Starting prune operation for orphaned cache entries...")
        try:
            current_files = {
                entry.name
                for entry in source_dir.iterdir()
                if entry.is_file() and not entry.name.startswith(".")
            }
        except Exception as exc:
            log_warning(LogTags.IDARR, f"Failed to list source directory for orphan prune: {exc}")
            return {"removed_cache": 0, "removed_pending": 0}

        removed_keys: list[str] = []
        removed_titles: list[str] = []

        cache_rows = self._load_all_cache_rows("orphan prune")
        if not cache_rows:
            return {"removed_cache": 0, "removed_pending": 0}

        for row in cache_rows:
            payload: dict[str, Any] = {}
            if isinstance(row.payload_json, str) and row.payload_json.strip():
                try:
                    parsed = json.loads(row.payload_json)
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}

            original_filenames = payload.get("original_filenames") if isinstance(payload.get("original_filenames"), list) else []
            current_filenames = payload.get("current_filenames") if isinstance(payload.get("current_filenames"), list) else []

            relevant_filenames = {
                Path(str(name)).name
                for name in [*original_filenames, *current_filenames]
                if isinstance(name, str) and str(name).strip()
            }

            if relevant_filenames and relevant_filenames & current_files:
                continue

            if isinstance(row.asset_key, str) and row.asset_key.strip():
                removed_keys.append(str(row.asset_key))
            title = str(row.title or "").strip()
            if title:
                if isinstance(row.year, int):
                    removed_titles.append(f"{title} ({row.year})")
                else:
                    removed_titles.append(title)
            self.db.delete(row)

        removed_pending = 0
        if removed_keys:
            pending_rows = self._pending_query().filter(IdarrPendingMatch.asset_key.in_(removed_keys)).all()
            for pending_row in pending_rows:
                self.db.delete(pending_row)
                removed_pending += 1

        try:
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            log_warning(LogTags.IDARR, f"Failed to commit orphan prune changes: {exc}")
            return {"removed_cache": 0, "removed_pending": 0}

        for title in removed_titles[:25]:
            log_info(LogTags.IDARR, f"\U0001f5d1\ufe0f Removed orphaned cache entry: {title}")
        if len(removed_titles) > 25:
            log_info(LogTags.IDARR, f"Additional orphaned cache entries removed: {len(removed_titles) - 25}")

        log_info(
            LogTags.IDARR,
            f"Orphan prune complete: removed_cache={len(removed_keys)}, removed_pending={removed_pending}",
            removed_cache=len(removed_keys),
            removed_pending=removed_pending,
        )

        return {"removed_cache": len(removed_keys), "removed_pending": removed_pending}

    @staticmethod
    def _ensure_duplicates_dir() -> Path:
        duplicates_dir = app_settings.config_dir / "idarr" / "duplicates"
        duplicates_dir.mkdir(parents=True, exist_ok=True)
        return duplicates_dir

    @staticmethod
    def _strip_id_tags_from_filename(filename: str) -> str:
        path_obj = Path(filename)
        cleaned_stem = ID_TAG_BLOCK_REGEX.sub("", path_obj.stem)
        cleaned_stem = " ".join(cleaned_stem.split()).strip()
        if not cleaned_stem:
            return filename
        return f"{cleaned_stem}{path_obj.suffix}"

    @staticmethod
    def _generate_new_filename(asset: dict[str, Any], old_filename: str) -> str:
        stripped_old_filename = IdarrRunner._strip_id_tags_from_filename(old_filename)
        old_stem = Path(stripped_old_filename).stem
        ext = Path(stripped_old_filename).suffix

        # Asset drive files always use a fixed extension and carry a subtype label
        asset_subtype = str(asset.get("asset_subtype") or "").strip().lower()
        if asset_subtype == "logo":
            ext = ".png"
            asset_subtype_label = " - logo"
        elif asset_subtype == "backdrop":
            ext = ".jpg"
            asset_subtype_label = " - backdrop"
        else:
            asset_subtype_label = ""

        base_title = str(asset.get("new_title") or asset.get("title") or old_stem).strip()
        base_year: int | None = None
        if isinstance(asset.get("new_year"), int):
            base_year = int(asset.get("new_year"))
        elif isinstance(asset.get("year"), int):
            base_year = int(asset.get("year"))

        id_parts: list[str] = []

        tmdb_id: int | None = None
        if isinstance(asset.get("new_tmdb_id"), int):
            tmdb_id = int(asset.get("new_tmdb_id"))
        elif isinstance(asset.get("tmdb_id"), int):
            tmdb_id = int(asset.get("tmdb_id"))

        tvdb_id: int | None = None
        if isinstance(asset.get("new_tvdb_id"), int):
            tvdb_id = int(asset.get("new_tvdb_id"))
        elif isinstance(asset.get("tvdb_id"), int):
            tvdb_id = int(asset.get("tvdb_id"))

        imdb_id: str | None = None
        if isinstance(asset.get("new_imdb_id"), str):
            imdb_id = str(asset.get("new_imdb_id"))
        elif isinstance(asset.get("imdb_id"), str):
            imdb_id = str(asset.get("imdb_id"))

        if tmdb_id:
            id_parts.append(f"tmdb-{tmdb_id}")
        if tvdb_id and str(asset.get("type") or "").strip().lower() == "tv_series":
            id_parts.append(f"tvdb-{tvdb_id}")
        if imdb_id and imdb_id.startswith("tt"):
            id_parts.append(f"imdb-{imdb_id}")
        suffix = "".join(f" {{{part}}}" for part in id_parts)

        season_suffix = ""
        season_match = SEASON_SUFFIX_REGEX.search(stripped_old_filename)
        if season_match:
            season_suffix = season_match.group(0)

        normalized_base_title = " ".join(base_title.split()).strip()
        asset_type = str(asset.get("type") or "").strip().lower()
        if asset_type == "collection" and base_year:
            collection_match = re.match(r"^(.*?)\s+Collection$", normalized_base_title, re.IGNORECASE)
            if collection_match:
                collection_name = str(collection_match.group(1) or "").strip()
                existing_year_match = re.search(r"\((\d{4})\)\s*$", collection_name)
                if existing_year_match and int(existing_year_match.group(1)) == int(base_year):
                    base = f"{collection_name} Collection"
                else:
                    base = f"{collection_name} ({base_year}) Collection"
            else:
                base = f"{normalized_base_title} ({base_year})"
        else:
            base = f"{normalized_base_title}{f' ({base_year})' if base_year else ''}"

        new_name = f"{base}{suffix}{season_suffix}{asset_subtype_label}{ext}"

        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", new_name)
        cleaned = " ".join(cleaned.split()).strip()
        return cleaned or old_filename

    def generate_new_filename(self, asset: dict[str, Any], old_filename: str) -> str:
        return self._generate_new_filename(asset, old_filename)

    def is_ignored(self, asset: dict[str, Any], ignored_asset_keys: set[str]) -> bool:
        asset_key = self._asset_key(
            asset_type=str(asset.get("type") or ""),
            title=str(asset.get("title") or ""),
            year=asset.get("year") if isinstance(asset.get("year"), int) else None,
        )
        return asset_key in ignored_asset_keys

    @staticmethod
    def _archive_duplicate(file_path: Path, duplicates_dir: Path, dry_run: bool) -> tuple[bool, str | None]:
        archived_name = file_path.name
        archived_path = duplicates_dir / archived_name
        if archived_path.exists():
            archived_name = f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
            archived_path = duplicates_dir / archived_name
        if dry_run:
            return True, str(archived_path)
        try:
            shutil.move(str(file_path), str(archived_path))
            return True, str(archived_path)
        except Exception:
            return False, None

    def _update_cache_rename_history(self, *, asset: dict[str, Any], old_filename: str, new_filename: str) -> bool:
        if old_filename == new_filename:
            return False

        asset_type = str(asset.get("type") or "").strip().lower()
        title = str(asset.get("title") or "").strip()
        year = asset.get("year") if isinstance(asset.get("year"), int) else None
        if not asset_type or not title:
            return False

        asset_key = self._asset_key(asset_type=asset_type, title=title, year=year)
        # Rows for resolved items are stored under the ID-keyed form; try that first.
        _rh_tmdb = int(asset["tmdb_id"]) if isinstance(asset.get("tmdb_id"), int) else None
        _rh_tvdb = int(asset["tvdb_id"]) if isinstance(asset.get("tvdb_id"), int) else None
        _rh_imdb = str(asset.get("imdb_id") or "").strip() or None
        if _rh_tmdb or _rh_tvdb or _rh_imdb:
            id_keyed = self._asset_key(asset_type=asset_type, title=title, year=year,
                                       tmdb_id=_rh_tmdb, tvdb_id=_rh_tvdb, imdb_id=_rh_imdb)
            row = self._cache_query().filter(IdarrAssetCache.asset_key == id_keyed).first()
            if not row and id_keyed != asset_key:
                row = self._cache_query().filter(IdarrAssetCache.asset_key == asset_key).first()
        else:
            row = self._cache_query().filter(IdarrAssetCache.asset_key == asset_key).first()
        if not row:
            return False

        payload: dict[str, Any] = {}
        if isinstance(row.payload_json, str) and row.payload_json.strip():
            try:
                parsed = json.loads(row.payload_json)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}

        rename_history = self._compact_rename_history(payload.get("rename_history"))
        rename_history.append(
            {
                "from": old_filename,
                "to": new_filename,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        payload["rename_history"] = self._compact_rename_history(rename_history)

        original_filenames = payload.get("original_filenames")
        if not isinstance(original_filenames, list):
            original_filenames = []
        original_set = {str(value) for value in original_filenames if isinstance(value, str)}
        original_set.add(old_filename)
        payload["original_filenames"] = sorted(original_set)

        current_filenames = payload.get("current_filenames")
        if not isinstance(current_filenames, list):
            current_filenames = []
        current_set = {str(value) for value in current_filenames if isinstance(value, str)}
        if old_filename in current_set:
            current_set.remove(old_filename)
        current_set.add(new_filename)
        payload["current_filenames"] = sorted(current_set)

        row.payload_json = json.dumps(payload)
        return True

    @staticmethod
    def _write_duplicate_log_csv(
        rows: list[dict[str, Any]],
        duplicates_dir: Path,
        dry_run: bool,
    ) -> str | None:
        if not rows:
            return None
        csv_path = duplicates_dir / "duplicate_conflicts.csv"
        if dry_run:
            return str(csv_path)

        fieldnames = [
            "timestamp",
            "source_path",
            "target_path",
            "resolution",
            "archived_path",
            "action_mode",
            "dry_run",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return str(csv_path)

    @staticmethod
    def _rename_in_place(source: Path, target_name: str, dry_run: bool) -> bool:
        destination = source.with_name(target_name)
        if destination == source:
            return True
        if destination.exists():
            return False
        if dry_run:
            return True
        source.rename(destination)
        return True

    @staticmethod
    def _convert_asset_drive_file(file_path: Path, asset_subtype: str) -> None:
        """Re-encode an asset drive file to its required format.

        Logos are always PNG with alpha preserved; backdrops are always JPEG flattened to RGB.
        Called after rename when the original extension differed from the target.
        """
        try:
            img = Image.open(file_path)
            if asset_subtype == "logo":
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                img.save(file_path, "PNG")
            elif asset_subtype == "backdrop":
                if img.mode in ("RGBA", "LA", "P"):
                    background = Image.new("RGB", img.size, (0, 0, 0))
                    converted = img.convert("RGBA") if img.mode == "P" else img
                    background.paste(converted, mask=converted.split()[-1])
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(file_path, "JPEG", quality=95, subsampling=0)
        except (UnidentifiedImageError, Exception) as exc:
            log_warning(LogTags.IDARR, f"Asset format conversion failed for '{file_path.name}': {exc}")

    def _transfer_file(
        self,
        *,
        source: Path,
        destination: Path,
        action_type: str,
        dry_run: bool,
        duplicates_dir: Path,
    ) -> tuple[bool, bool, bool, str | None, str]:
        normalized_action = str(action_type or "move").strip().lower() or "move"
        if normalized_action not in {"move", "copy"}:
            return False, False, False, None, "unsupported_action"

        if not source.exists() or not source.is_file():
            return False, False, False, None, "source_missing"

        conflicted = destination.exists()
        archived_duplicate = False
        archived_path: str | None = None

        if conflicted:
            source_mtime = source.stat().st_mtime
            destination_mtime = destination.stat().st_mtime
            if source_mtime <= destination_mtime:
                return False, False, True, None, "kept_newer_destination"

            archived_duplicate, archived_path = self._archive_duplicate(destination, duplicates_dir, dry_run)
            if not archived_duplicate:
                return False, False, True, None, "archive_failed"

        if dry_run:
            reason = "replaced_older_destination" if conflicted else "transferred"
            return True, archived_duplicate, conflicted, archived_path, reason

        destination.parent.mkdir(parents=True, exist_ok=True)
        if normalized_action == "move":
            shutil.move(str(source), str(destination))
        else:
            shutil.copy2(str(source), str(destination))

        reason = "replaced_older_destination" if conflicted else "transferred"
        return True, archived_duplicate, conflicted, archived_path, reason

    @staticmethod
    def _is_filename_too_long(filename: str, limit: int = 255) -> bool:
        return len(filename) > limit

    def _log_operation_rows(self, operation_rows: list[dict[str, Any]], *, debug_enabled: bool) -> None:
        total_operations = len(operation_rows)
        if total_operations == 0:
            log_info(LogTags.IDARR, "No file updates were needed")
            return

        success_count = sum(1 for row in operation_rows if str(row.get("status") or "").lower() == "success")
        skipped_count = sum(1 for row in operation_rows if str(row.get("status") or "").lower() == "skipped")
        failed_count = total_operations - success_count - skipped_count

        log_info(
            LogTags.IDARR,
            f"File rename summary: total={total_operations}, success={success_count}, skipped={skipped_count}, failed={failed_count}",
            total_operations=total_operations,
            success_count=success_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )

        rows_to_log = operation_rows if debug_enabled else operation_rows[:25]
        for index, row in enumerate(rows_to_log, start=1):
            operation = str(row.get("operation") or "unknown").lower()
            status = str(row.get("status") or "unknown").lower()
            reason = str(row.get("reason") or "")
            from_path = str(row.get("from_path") or "")
            to_path = str(row.get("to_path") or "")
            from_name = Path(from_path).name if from_path else ""
            to_name = Path(to_path).name if to_path else ""

            log_info(
                LogTags.IDARR,
                f"{index}/{len(rows_to_log)} {operation.upper()}: {from_name} -> {to_name} [{status}] {reason}",
                operation=operation,
                status=status,
                reason=reason,
                from_path=from_path,
                to_path=to_path,
            )

        if not debug_enabled and total_operations > len(rows_to_log):
            omitted = total_operations - len(rows_to_log)
            log_info(
                LogTags.IDARR,
                f"Additional file actions omitted: {omitted} (enable PosterFlow debug for full per-file output)",
                omitted_operations=omitted,
            )

    def rename_files(
        self,
        *,
        assets: list[dict[str, Any]],
        dry_run: bool,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        renamed_count = 0
        skipped_count = 0
        duplicate_conflicts = 0
        cache_history_updated = False
        conflict_rows: list[dict[str, Any]] = []
        operation_rows: list[dict[str, Any]] = []
        duplicates_dir_for_log: Path | None = None
        total_assets = len(assets)
        progress_interval = 25

        _target_to_indices: dict[str, list[int]] = {}
        _precomputed_filenames: list[str] = []
        for _pi, _pa in enumerate(assets):
            _sf = _pa["file_path"]
            _nf = self.generate_new_filename(_pa, _sf.name)
            _precomputed_filenames.append(_nf)
            _key = str(_sf.parent / _nf)
            _target_to_indices.setdefault(_key, []).append(_pi)

        _intra_conflict_indices: set[int] = set()
        for _norm_target, _idxs in _target_to_indices.items():
            if len(_idxs) < 2:
                continue
            _src_paths = [str(assets[_i]["file_path"]) for _i in _idxs]
            conflict_rows.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source_path": _src_paths[0],
                    "source_paths": _src_paths,
                    "target_path": _norm_target,
                    "resolution": "intra_batch_filename_conflict",
                    "archived_path": "",
                    "action_mode": "rename",
                    "dry_run": str(dry_run).lower(),
                }
            )
            for _i in _idxs:
                _intra_conflict_indices.add(_i)
                duplicate_conflicts += 1
                skipped_count += 1
                operation_rows.append(
                    {
                        "operation": "rename",
                        "from_path": str(assets[_i]["file_path"]),
                        "to_path": _norm_target,
                        "status": "skipped",
                        "revert_supported": False,
                        "reason": "intra_batch_filename_conflict",
                    }
                )

        for index, asset in enumerate(assets, start=1):
            if (index - 1) in _intra_conflict_indices:
                continue
            source_file = asset["file_path"]
            new_filename = self.generate_new_filename(asset, source_file.name)
            original_path = str(source_file)
            new_path = str(source_file.with_name(new_filename))
            if bool(asset.get("review_required", False)):
                skipped_count += 1
                operation_rows.append(
                    {
                        "operation": "rename",
                        "from_path": original_path,
                        "to_path": new_path,
                        "status": "skipped",
                        "revert_supported": False,
                        "reason": "review_required_low_confidence_alternate",
                    }
                )
                continue
            if self._is_filename_too_long(new_filename):
                skipped_count += 1
                operation_rows.append(
                    {
                        "operation": "rename",
                        "from_path": original_path,
                        "to_path": new_path,
                        "status": "skipped",
                        "revert_supported": False,
                        "reason": "filename_too_long",
                    }
                )
                continue
            renamed = self._rename_in_place(source_file, new_filename, dry_run)
            if renamed:
                if source_file.name != new_filename:
                    renamed_count += 1
                    if not dry_run:
                        cache_history_updated = self._update_cache_rename_history(
                            asset=asset,
                            old_filename=source_file.name,
                            new_filename=new_filename,
                        ) or cache_history_updated
                        # Convert asset drive files when extension changed (e.g. webp → png/jpg)
                        _asset_subtype = str(asset.get("asset_subtype") or "").strip().lower()
                        if _asset_subtype and source_file.suffix.lower() != Path(new_filename).suffix.lower():
                            self._convert_asset_drive_file(source_file.with_name(new_filename), _asset_subtype)

                        asset["file_path"] = source_file.with_name(new_filename)
                    operation_rows.append(
                        {
                            "operation": "rename",
                            "from_path": original_path,
                            "to_path": new_path,
                            "status": "success",
                            "revert_supported": True,
                            "reason": "in_place_rename",
                        }
                    )
            else:
                # Destination already exists — compare mtimes to decide which is newer
                destination = source_file.with_name(new_filename)
                dupe_dir = self._ensure_duplicates_dir()
                if duplicates_dir_for_log is None:
                    duplicates_dir_for_log = dupe_dir

                try:
                    source_mtime = source_file.stat().st_mtime
                    destination_mtime = destination.stat().st_mtime
                except OSError:
                    source_mtime = 0.0
                    destination_mtime = 0.0

                duplicate_conflicts += 1

                if source_mtime > destination_mtime:
                    # Incoming file is newer → archive existing, rename incoming
                    archived, archived_path = self._archive_duplicate(destination, dupe_dir, dry_run)
                    if archived:
                        ok = self._rename_in_place(source_file, new_filename, dry_run)
                        if ok:
                            if source_file.name != new_filename:
                                renamed_count += 1
                                if not dry_run:
                                    cache_history_updated = self._update_cache_rename_history(
                                        asset=asset,
                                        old_filename=source_file.name,
                                        new_filename=new_filename,
                                    ) or cache_history_updated
                                    asset["file_path"] = source_file.with_name(new_filename)
                            conflict_rows.append(
                                {
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "source_path": str(source_file),
                                    "target_path": new_path,
                                    "resolution": "archived_older_existing_renamed_newer_incoming",
                                    "archived_path": archived_path or "",
                                    "action_mode": "rename",
                                    "dry_run": str(dry_run).lower(),
                                }
                            )
                            operation_rows.append(
                                {
                                    "operation": "rename",
                                    "from_path": original_path,
                                    "to_path": new_path,
                                    "status": "success",
                                    "revert_supported": True,
                                    "reason": "archived_older_existing_renamed_newer_incoming",
                                }
                            )
                        else:
                            skipped_count += 1
                            operation_rows.append(
                                {
                                    "operation": "rename",
                                    "from_path": original_path,
                                    "to_path": new_path,
                                    "status": "skipped",
                                    "revert_supported": False,
                                    "reason": "in_place_conflict_rename_failed_after_archive",
                                }
                            )
                    else:
                        skipped_count += 1
                        conflict_rows.append(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "source_path": str(source_file),
                                "target_path": new_path,
                                "resolution": "in_place_conflict_archive_failed",
                                "archived_path": "",
                                "action_mode": "rename",
                                "dry_run": str(dry_run).lower(),
                            }
                        )
                        operation_rows.append(
                            {
                                "operation": "rename",
                                "from_path": original_path,
                                "to_path": new_path,
                                "status": "skipped",
                                "revert_supported": False,
                                "reason": "in_place_conflict_archive_failed",
                            }
                        )
                else:
                    # Existing destination is newer or same age → archive the incoming source as the older duplicate
                    archived, archived_path = self._archive_duplicate(source_file, dupe_dir, dry_run)
                    resolution = "archived_older_incoming_kept_newer_existing" if archived else "in_place_conflict_kept_existing"
                    skipped_count += 1
                    conflict_rows.append(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "source_path": str(source_file),
                            "target_path": new_path,
                            "resolution": resolution,
                            "archived_path": archived_path or "",
                            "action_mode": "rename",
                            "dry_run": str(dry_run).lower(),
                        }
                    )
                    operation_rows.append(
                        {
                            "operation": "rename",
                            "from_path": original_path,
                            "to_path": new_path,
                            "status": "skipped",
                            "revert_supported": False,
                            "reason": resolution,
                        }
                    )

            if progress_callback and total_assets > 0 and (index == 1 or index % progress_interval == 0 or index == total_assets):
                try:
                    progress_callback(
                        index,
                        total_assets,
                        f"Renaming {index}/{total_assets} assets",
                    )
                except Exception:  # nosec B110
                    pass

        return {
            "renamed_count": renamed_count,
            "skipped_count": skipped_count,
            "duplicate_conflicts": duplicate_conflicts,
            "cache_history_updated": cache_history_updated,
            "conflict_rows": conflict_rows,
            "operation_rows": operation_rows,
            "duplicates_dir_for_log": duplicates_dir_for_log,
        }

    def summarize_run(
        self,
        *,
        run_started_at: datetime,
        assets: list[dict[str, Any]],
        total_assets: int,
        unmatched_assets: list[dict[str, Any]],
        enrichment_stats: dict[str, Any],
        enrichment_details: list[dict[str, Any]],
        renamed_count: int,
        skipped_count: int,
        duplicate_conflicts: int,
        conflict_rows: list[dict[str, Any]],
        operation_rows: list[dict[str, Any]],
        duplicate_log_csv: str | None,
        ignored_count: int,
        ignore_pending_sync_stats: dict[str, int],
        pending_only: bool,
        orphan_prune_stats: dict[str, int],
        inactive_cache_prune_stats: dict[str, int],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        run_finished_at = datetime.now(timezone.utc)
        elapsed_seconds = max(0, int((run_finished_at - run_started_at).total_seconds()))
        elapsed = self._format_elapsed(elapsed_seconds)

        ambiguous_matches = sum(1 for item in unmatched_assets if str(item.get("match_reason") or "") == "ambiguous")
        tvdb_missing_tv = sum(
            1
            for asset in assets
            if str(asset.get("type") or "") == "tv_series"
            and isinstance(asset.get("tmdb_id"), int)
            and not isinstance(asset.get("tvdb_id"), int)
        )
        reclassified_tv = int(enrichment_stats.get("reclassified_tv", 0))
        files_renamed = renamed_count
        summary_report = [
            {"label": "⏱️ Elapsed Time", "value": elapsed},
            {"label": "📦 Items Processed", "value": len(assets)},
            {"label": "✏️ Files Renamed", "value": files_renamed},
            {"label": "❌ Unmatched Items", "value": len(unmatched_assets)},
            {"label": "🤷 Ambiguous Matches", "value": ambiguous_matches},
            {"label": "📺 TVDB Missing (TV)", "value": tvdb_missing_tv},
            {"label": "🔁 Reclassified (TV)", "value": reclassified_tv},
            {"label": "💾 Cache Skipped", "value": int(enrichment_stats.get("freshness_skipped", 0))},
            {"label": "📡 TMDB API Calls", "value": int(enrichment_stats.get("tmdb_api_calls", 0))},
        ]

        stats = {
            "total_assets": total_assets,
            "processed_assets": len(assets),
            "elapsed_seconds": elapsed_seconds,
            "elapsed": elapsed,
            "renamed": renamed_count,
            "files_renamed": files_renamed,
            "skipped": skipped_count,
            "duplicate_conflicts": duplicate_conflicts,
            "duplicate_conflict_items": len(conflict_rows),
            "file_operations": len(operation_rows),
            "unmatched_assets": len(unmatched_assets),
            "ambiguous_matches": ambiguous_matches,
            "tvdb_missing_tv": tvdb_missing_tv,
            "reclassified_tv": reclassified_tv,
            "ignored_assets": ignored_count,
            "ignore_pending_sync": ignore_pending_sync_stats,
            "pending_only": pending_only,
            "tmdb_enrichment": enrichment_stats,
            "orphan_prune": orphan_prune_stats,
            "inactive_cache_prune": inactive_cache_prune_stats,
        }

        details = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "elapsed": elapsed,
            "enrichment": enrichment_stats,
            "enriched_items": enrichment_details[:50],
            "unmatched_items": unmatched_assets[:50],
            "duplicate_conflicts": conflict_rows[:100],
            "duplicate_log_csv": duplicate_log_csv,
            "file_operations": operation_rows,
            "ignore_pending_sync": ignore_pending_sync_stats,
            "orphan_prune": orphan_prune_stats,
            "inactive_cache_prune": inactive_cache_prune_stats,
            "summary_report": summary_report,
        }

        return summary_report, stats, details

    def run(
        self,
        config_data: dict[str, Any],
        progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> IdarrRunnerResult:
        def _notify_progress(phase: str, progress: int, message: str) -> None:
            if not progress_callback:
                return
            try:
                normalized_progress = max(0, min(int(progress), 99))
                progress_callback(phase, normalized_progress, 100, message)
            except Exception as e:
                log_debug(LogTags.IDARR, f"IDarr progress callback failed: {e}")

        run_started_at = datetime.now(timezone.utc)
        self._ensure_idarr_tables_ready()
        source_dir = self._validate_source_dir(config_data)
        requested_scope_token = str(config_data.get("scope_token") or "").strip()
        if requested_scope_token:
            self._scope_token = requested_scope_token
        else:
            requested_index = config_data.get("sync_target_index")
            if isinstance(requested_index, int):
                self._scope_token = build_idarr_scope_token(requested_index, str(source_dir))
            else:
                self._scope_token = None
        dry_run = bool(config_data.get("dry_run", False))

        tmdb_api_key = str(config_data.get("tmdb_api_key", "")).strip()
        if not tmdb_api_key:
            raise ValueError("TMDB API key is required for IDarr runs")

        warnings: list[str] = []

        compatibility_payload = {
            "limit": config_data.get("limit"),
            "frequency_days": config_data.get("frequency_days"),
            "tvdb_frequency": config_data.get("tvdb_frequency"),
            "debug": bool(app_settings.debug),
        }
        if compatibility_payload:
            log_info(
                LogTags.IDARR,
                (
                    "IDarr Settings: "
                    f"limit={compatibility_payload.get('limit')}, "
                    f"frequency_days={compatibility_payload.get('frequency_days')}, "
                    f"tvdb_frequency={compatibility_payload.get('tvdb_frequency')}, "
                    f"debug={compatibility_payload.get('debug')}"
                ),
            )
            log_debug(
                LogTags.IDARR,
                f"IDarr Settings payload: {json.dumps(compatibility_payload, default=str, sort_keys=True)}",
            )
            _notify_progress("initializing", 3, "Loaded IDarr runtime settings")

        pending_only = bool(config_data.get("pending_matches", False))
        allowed_asset_keys: set[str] | None = None
        ignored_asset_keys = self._load_ignored_asset_keys()
        ignore_pending_sync_stats = {
            "removed_pending": 0,
            "marked_ignored": 0,
            "restored_pending": 0,
            "unignored_reactivated": 0,
        }

        try:
            ignored_asset_keys, ignore_pending_sync_stats = self.load_ignore_and_sync_pending()
        except Exception as exc:
            self.db.rollback()
            warnings.append(f"Failed to synchronize ignored/pending state: {exc}")
            log_warning(LogTags.IDARR, f"Failed to synchronize ignored/pending state: {exc}")

        ignored_count = 0
        if pending_only:
            pending_keys = self._load_pending_asset_keys()
            if not pending_keys:
                return IdarrRunnerResult(
                    success=True,
                    message="Pending-matches mode enabled but no pending items were found",
                    stats={"total_assets": 0, "total_matched": 0, "unmatched_assets": 0},
                    warnings=warnings,
                    unmatched_assets=[],
                )
            allowed_asset_keys = pending_keys
            log_info(LogTags.IDARR, f"Pending-matches mode enabled: {len(pending_keys)} item(s) queued")

        if bool(config_data.get("remove_non_image_files", False)):
            removed_count, dry_run_count = self._remove_non_images(source_dir, dry_run=dry_run)
            log_info(
                LogTags.IDARR,
                f"Non-image cleanup complete: removed={removed_count}, dry_run_only={dry_run_count}",
                removed=removed_count,
                dry_run_only=dry_run_count,
            )
            _notify_progress("cleanup", 6, f"Cleanup complete: removed {removed_count} non-image file(s)")

        selected_source_filenames = self._extract_selected_source_filenames(config_data)
        single_item_mode = bool(selected_source_filenames)

        is_asset_drive = bool(config_data.get("is_asset_drive", False))
        is_psd_drive = bool(config_data.get("is_psd_drive", False))
        if is_asset_drive:
            log_info(
                LogTags.IDARR,
                f"Asset drive mode: scanning logos/ and backdrops/ subfolders of: {source_dir}",
                source_dir=str(source_dir),
            )
            _notify_progress("scanning", 9, f"Scanning asset drive subfolders: {source_dir}")
            assets = self.scan_asset_drive_subfolders(source_dir)
        elif is_psd_drive:
            # PSD drive: a flat folder using asset-style matching (no season-suffix hints, since
            # PSD source files don't carry season info) but without the logos/backdrops subfolders.
            log_info(
                LogTags.IDARR,
                f"PSD drive mode: scanning flat folder with asset-style matching: {source_dir}",
                source_dir=str(source_dir),
            )
            _notify_progress("scanning", 9, f"Scanning PSD drive folder: {source_dir}")
            assets = self._scan_assets_for_asset_drive(source_dir)
        else:
            log_info(LogTags.IDARR, f"Scanning directory for image assets: {source_dir}", source_dir=str(source_dir))
            _notify_progress("scanning", 9, f"Scanning source folder: {source_dir}")
            assets = self.scan_files_in_flat_folder(source_dir)

        unsupported_files = self._find_unsupported_image_files(source_dir, is_asset_drive=is_asset_drive)
        if unsupported_files:
            ext_groups: dict[str, list[str]] = {}
            for _uf in unsupported_files:
                ext_groups.setdefault(_uf.suffix.lower(), []).append(_uf.name)
            for _ext, _names in sorted(ext_groups.items()):
                _examples = ", ".join(_names[:3])
                if len(_names) > 3:
                    _examples += f" (and {len(_names) - 3} more)"
                _msg = f"Unsupported file format {_ext}: {_examples} — these files cannot be renamed or processed"
                warnings.append(_msg)
                log_warning(LogTags.IDARR, _msg)

        if selected_source_filenames:
            assets = [
                asset
                for asset in assets
                if self._asset_matches_selected_file(asset, selected_source_filenames)
            ]

        total_assets = len(assets)
        if total_assets == 0:
            if selected_source_filenames:
                log_info(
                    LogTags.IDARR,
                    f"No uploaded source files matched for scoped IDarr run in: {source_dir}",
                    source_dir=str(source_dir),
                    selected_count=len(selected_source_filenames),
                )
                _notify_progress("scanning", 12, "No selected upload files were found in source folder")
            else:
                log_info(LogTags.IDARR, f"No image assets found in source folder: {source_dir}", source_dir=str(source_dir))
                _notify_progress("scanning", 12, "No image assets found in source folder")
        else:
            log_info(LogTags.IDARR, f"Completed scanning: discovered {total_assets} image asset(s)", total_assets=total_assets)
            _notify_progress("scanning", 12, f"Discovered {total_assets} image asset(s)")

        if ignored_asset_keys:
            before_ignored = len(assets)
            assets = [
                asset
                for asset in assets
                if not self.is_ignored(asset, ignored_asset_keys)
            ]
            ignored_count = before_ignored - len(assets)
            if ignored_count > 0:
                log_info(LogTags.IDARR, f"Ignored assets excluded: {ignored_count}", ignored_count=ignored_count)

        if bool(config_data.get("skip_collections", False)):
            before_count = len(assets)
            assets = [asset for asset in assets if asset.get("type") != "collection"]
            log_info(
                LogTags.IDARR,
                f"Collections skipped: {before_count - len(assets)}",
                skipped_collections=before_count - len(assets),
            )

        if pending_only and allowed_asset_keys is not None:
            assets = [
                asset
                for asset in assets
                if self._asset_key(
                    asset_type=str(asset.get("type") or ""),
                    title=str(asset.get("title") or ""),
                    year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                ) in allowed_asset_keys
            ]

        assets = self.filter_items(assets, config_data)

        limit = config_data.get("limit")
        if isinstance(limit, int) and limit > 0:
            assets = assets[:limit]

        frequency_days = int(config_data.get("frequency_days", 30) or 30)
        tvdb_frequency = int(config_data.get("tvdb_frequency", 7) or 7)
        if frequency_days < 1:
            frequency_days = 1
        if tvdb_frequency < 1:
            tvdb_frequency = 1

        log_info(LogTags.IDARR, "Starting metadata enrichment via TMDB")
        _notify_progress("enrichment", 12, "Enriching metadata via TMDB")

        def _map_progress(current: int, total: int, start: int, end: int) -> int:
            if total <= 0:
                return start
            bounded_current = max(0, min(current, total))
            span = max(1, end - start)
            return start + int((bounded_current / total) * span)

        def _enrichment_progress(current: int, total: int, message: str) -> None:
            mapped_progress = _map_progress(current, total, 12, 78)
            _notify_progress("enrichment", mapped_progress, message)

        enrichment_stats, enrichment_details = self.handle_data(
            assets,
            tmdb_api_key,
            frequency_days=frequency_days,
            tvdb_frequency=tvdb_frequency,
            progress_callback=_enrichment_progress,
        )
        log_info(
            LogTags.IDARR,
            (
                "Completed metadata enrichment: "
                f"items={int(enrichment_stats.get('items_considered', 0))}, "
                f"tmdb={int(enrichment_stats.get('tmdb_assigned', 0))}, "
                f"tvdb={int(enrichment_stats.get('tvdb_assigned', 0))}, "
                f"imdb={int(enrichment_stats.get('imdb_assigned', 0))}, "
                f"api_calls={int(enrichment_stats.get('tmdb_api_calls', 0))}, "
                f"errors={int(enrichment_stats.get('errors', 0))}"
            ),
        )
        _notify_progress("enrichment", 78, "Metadata enrichment complete")
        tvdb_rehydrated = int(enrichment_stats.get("tvdb_rehydrate_calls", 0))
        if tvdb_rehydrated > 0:
            log_info(LogTags.IDARR, f"\u2705 Rehydrated {tvdb_rehydrated} TV series {'entry' if tvdb_rehydrated == 1 else 'entries'}.")
        log_info(LogTags.IDARR, f"\u2705 Completed metadata enrichment ({ignored_count} item(s) ignored by exclusion list)")
        inactive_cache_prune_stats = {"removed_cache": 0, "removed_pending": 0}
        try:
            self._store_asset_cache_rows(assets)
            active_keys: set[str] = set()
            for _aa in assets:
                if not (str(_aa.get("title") or "").strip() and str(_aa.get("type") or "").strip()):
                    continue
                _ak_type = str(_aa.get("type") or "")
                _ak_title = str(_aa.get("title") or "")
                _ak_year = _aa.get("year") if isinstance(_aa.get("year"), int) else None
                # Always include the provisional key (for unmatched/unresolved rows).
                active_keys.add(self._asset_key(asset_type=_ak_type, title=_ak_title, year=_ak_year))
                # Also include the ID-keyed form so resolved rows aren't pruned.
                _ak_tmdb = int(_aa["tmdb_id"]) if isinstance(_aa.get("tmdb_id"), int) else None
                _ak_tvdb = int(_aa["tvdb_id"]) if isinstance(_aa.get("tvdb_id"), int) else None
                _ak_imdb = str(_aa.get("imdb_id") or "").strip() or None
                if _ak_tmdb or _ak_tvdb or _ak_imdb:
                    active_keys.add(self._asset_key(
                        asset_type=_ak_type, title=_ak_title, year=_ak_year,
                        tmdb_id=_ak_tmdb, tvdb_id=_ak_tvdb, imdb_id=_ak_imdb,
                    ))
            if not single_item_mode:
                inactive_cache_prune_stats = self._prune_inactive_cache_keys_for_source(source_dir, active_keys)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            warnings.append(f"Failed to update IDarr cache table: {exc}")
            log_warning(LogTags.IDARR, f"Failed to update IDarr cache table: {exc}")

        renamed_count = 0
        skipped_count = 0
        duplicate_conflicts = 0
        cache_history_updated = False
        conflict_rows: list[dict[str, Any]] = []
        operation_rows: list[dict[str, Any]] = []
        duplicates_dir_for_log: Path | None = None
        rename_mode = "DRY RUN" if dry_run else "LIVE"
        log_info(LogTags.IDARR, f"Starting file rename process ({rename_mode} mode)")
        _notify_progress("renaming", 80, f"Applying rename operations ({rename_mode.lower()} mode)")

        def _rename_progress(current: int, total: int, message: str) -> None:
            mapped_progress = _map_progress(current, total, 80, 95)
            _notify_progress("renaming", mapped_progress, message)

        rename_results = self.rename_files(
            assets=assets,
            dry_run=dry_run,
            progress_callback=_rename_progress,
        )
        renamed_count = int(rename_results.get("renamed_count") or 0)
        skipped_count = int(rename_results.get("skipped_count") or 0)
        duplicate_conflicts = int(rename_results.get("duplicate_conflicts") or 0)
        cache_history_updated = bool(rename_results.get("cache_history_updated"))
        conflict_rows = list(rename_results.get("conflict_rows") or [])
        operation_rows = list(rename_results.get("operation_rows") or [])
        duplicates_dir_for_log = rename_results.get("duplicates_dir_for_log") if isinstance(rename_results.get("duplicates_dir_for_log"), Path) else None
        _notify_progress(
            "renaming",
            95,
            f"Rename phase complete: renamed {renamed_count}, skipped {skipped_count}, conflicts {duplicate_conflicts}",
        )

        self._log_operation_rows(operation_rows, debug_enabled=bool(app_settings.debug))

        if cache_history_updated:
            try:
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                warnings.append(f"Failed to persist IDarr rename history: {exc}")
                log_warning(LogTags.IDARR, f"Failed to persist IDarr rename history: {exc}")

        unmatched_assets = [
            {
                "title": str(asset.get("title") or "").strip(),
                "year": asset.get("year") if isinstance(asset.get("year"), int) else None,
                "type": "pending",
                "match_reason": str(asset.get("match_reason") or "no_match").strip() or "no_match",
                "pending_reason": str(asset.get("pending_reason") or "").strip().lower() or None,
                "source_path": str(asset.get("file_path") or "").strip() or None,
            }
            for asset in assets
            if not bool(asset.get("has_id", False))
        ]
        if bool(app_settings.debug) and unmatched_assets:
            for _unmatched in unmatched_assets:
                _unmatched_title = str(_unmatched.get("title") or "unknown")
                log_debug(LogTags.IDARR, f"❌ Unmatched: {_unmatched_title}")

        duplicate_log_csv: str | None = None
        if conflict_rows:
            if duplicates_dir_for_log is None:
                duplicates_dir_for_log = self._ensure_duplicates_dir()
            try:
                duplicate_log_csv = self._write_duplicate_log_csv(conflict_rows, duplicates_dir_for_log, dry_run)
            except Exception as exc:
                warnings.append(f"Failed to write duplicate conflict CSV: {exc}")
                log_warning(LogTags.IDARR, f"Failed to write duplicate conflict CSV: {exc}")

        log_info(LogTags.IDARR, f"Unmatched assets found: {len(unmatched_assets)}", unmatched_count=len(unmatched_assets))
        conflict_pending_assets = self._collect_conflict_pending_assets(assets=assets, conflict_rows=conflict_rows)
        pending_assets_by_key: dict[str, dict[str, Any]] = {}
        for pending_asset in unmatched_assets:
            pending_key = self._pending_asset_key(
                asset_type=str(pending_asset.get("type") or ""),
                title=str(pending_asset.get("title") or ""),
                year=pending_asset.get("year") if isinstance(pending_asset.get("year"), int) else None,
                pending_reason=str(pending_asset.get("pending_reason") or pending_asset.get("match_reason") or ""),
                source_path=str(pending_asset.get("source_path") or ""),
            )
            pending_assets_by_key[pending_key] = pending_asset

        for pending_asset in conflict_pending_assets:
            pending_key = self._pending_asset_key(
                asset_type=str(pending_asset.get("type") or ""),
                title=str(pending_asset.get("title") or ""),
                year=pending_asset.get("year") if isinstance(pending_asset.get("year"), int) else None,
                pending_reason=str(pending_asset.get("pending_reason") or pending_asset.get("match_reason") or ""),
                source_path=str(pending_asset.get("source_path") or ""),
            )
            pending_assets_by_key[pending_key] = pending_asset

        scoped_asset_keys = {
            self._asset_key(
                asset_type=str(asset.get("type") or ""),
                title=str(asset.get("title") or ""),
                year=asset.get("year") if isinstance(asset.get("year"), int) else None,
            )
            for asset in assets
            if str(asset.get("title") or "").strip() and str(asset.get("type") or "").strip()
        }

        self._store_pending_assets(
            list(pending_assets_by_key.values()),
            scoped_asset_keys=scoped_asset_keys if single_item_mode else None,
        )
        try:
            ignored_asset_keys, ignore_pending_sync_stats = self.load_ignore_and_sync_pending()
        except Exception as exc:
            self.db.rollback()
            warnings.append(f"Failed to finalize ignored/pending sync: {exc}")
            log_warning(LogTags.IDARR, f"Failed to finalize ignored/pending sync: {exc}")

        if not single_item_mode:
            try:
                self._prune_orphaned_pending_cache_rows()
            except Exception as exc:
                self.db.rollback()
                warnings.append(f"Failed to prune orphaned pending cache rows: {exc}")
                log_warning(LogTags.IDARR, f"Failed to prune orphaned pending cache rows: {exc}")

        orphan_prune_stats = {"removed_cache": 0, "removed_pending": 0}
        if bool(config_data.get("prune", False)) and not single_item_mode:
            orphan_prune_stats = self._prune_orphaned_cache_entries(source_dir)

        _notify_progress("finalizing", 96, "Finalizing IDarr summary report")
        summary_report, stats, details = self.summarize_run(
            run_started_at=run_started_at,
            assets=assets,
            total_assets=total_assets,
            unmatched_assets=unmatched_assets,
            enrichment_stats=enrichment_stats,
            enrichment_details=enrichment_details,
            renamed_count=renamed_count,
            skipped_count=skipped_count,
            duplicate_conflicts=duplicate_conflicts,
            conflict_rows=conflict_rows,
            operation_rows=operation_rows,
            duplicate_log_csv=duplicate_log_csv,
            ignored_count=ignored_count,
            ignore_pending_sync_stats=ignore_pending_sync_stats,
            pending_only=pending_only,
            orphan_prune_stats=orphan_prune_stats,
            inactive_cache_prune_stats=inactive_cache_prune_stats,
        )
        log_info(LogTags.IDARR, "Summary Report:")
        for _row in summary_report:
            log_info(LogTags.IDARR, f"{_row.get('label', '')}: {_row.get('value', '')}")

        tvdb_missing_assets = [
            a for a in assets
            if str(a.get("type") or "") == "tv_series"
            and isinstance(a.get("tmdb_id"), int)
            and not isinstance(a.get("tvdb_id"), int)
        ]
        if tvdb_missing_assets:
            # Deduplicate by TMDB ID so base poster + season posters count as one show
            seen_tmdb: set[int] = set()
            tvdb_missing_unique: list[dict[str, Any]] = []
            for _a in tvdb_missing_assets:
                _tid = int(_a["tmdb_id"])
                if _tid not in seen_tmdb:
                    seen_tmdb.add(_tid)
                    tvdb_missing_unique.append(_a)
            log_info(LogTags.IDARR, f"📺 {len(tvdb_missing_unique)} TV show(s) still missing TVDB ID after enrichment:")
            for _a in sorted(tvdb_missing_unique, key=lambda x: str(x.get("title") or "").lower()):
                _title = str(_a.get("title") or "unknown")
                _year = f" ({_a['year']})" if isinstance(_a.get("year"), int) else ""
                _tmdb = f" [tmdb-{_a['tmdb_id']}]"
                log_info(LogTags.IDARR, f"  ○ {_title}{_year}{_tmdb}")

        _notify_progress("finalizing", 99, "IDarr run complete, saving final status")

        message = "IDarr tool run complete"
        return IdarrRunnerResult(
            success=True,
            message=message,
            stats=stats,
            warnings=warnings,
            unmatched_assets=unmatched_assets if bool(config_data.get("show_unmatched", False)) or pending_only else None,
            details=details,
        )
