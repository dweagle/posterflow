"""Tests for AssetCleanupService — the reverse-renamer assets-folder cleanup."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from models.plex_upload import PlexUploadRecord
from models.poster import Poster
from services.asset_cleanup import AssetCleanupService
from util.data.normalization import normalize_titles


# ── fixtures / helpers ─────────────────────────────────────────────────────

def _make_folder(root: Path, name: str, files: List[str]) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for file_name in files:
        (folder / file_name).write_bytes(b"poster-bytes")
    return folder


def _movie(title: str, year: int, folder: str, tmdb_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "type": "movies",
        "title": title,
        "year": year,
        "tmdb_id": tmdb_id,
        "imdb_id": None,
        "normalized_title": normalize_titles(title),
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "folder": folder,
    }


def _series(title: str, year: int, folder: str, tvdb_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "type": "series",
        "title": title,
        "year": year,
        "tvdb_id": tvdb_id,
        "imdb_id": None,
        "normalized_title": normalize_titles(title),
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "folder": folder,
        "seasons": [{"season_number": 1, "season_has_episodes": True}],
    }


def _collection(title: str) -> Dict[str, Any]:
    return {
        "type": "collections",
        "title": title,
        "year": None,
        "tmdb_id": None,
        "normalized_title": normalize_titles(title),
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "folder": title,
    }


def _run(test_db, dest: Path, media_dict, **kwargs):
    return AssetCleanupService(test_db).cleanup(str(dest), media_dict=media_dict, **kwargs)


# ── tests ──────────────────────────────────────────────────────────────────

def test_keeps_live_removes_orphans(test_db, tmp_path):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])          # live movie
    _make_folder(dest, "Old Movie (2001)", ["poster.jpg"])           # orphan movie
    _make_folder(dest, "Dead Show (2010)", ["poster.jpg", "Season01.jpg"])  # orphan series

    media = {
        "movies": [_movie("The Matrix", 1999, "/movies/The Matrix (1999)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],  # keeps collections type non-empty
    }

    result = _run(test_db, dest, media, dry_run=False, delete_unknown=False)

    assert (dest / "The Matrix (1999)").exists()
    assert not (dest / "Old Movie (2001)").exists()
    assert not (dest / "Dead Show (2010)").exists()
    assert result["counts"]["removed_orphans"] == 2


def test_stale_duplicate_after_rename(test_db, tmp_path):
    """Workin' Moms case: keep the current-named folder, remove the stale one."""
    dest = tmp_path / "assets"
    _make_folder(dest, "Workin Moms (2018)", ["poster.jpg", "Season01.jpg"])    # current
    _make_folder(dest, "Workin' Moms (2018)", ["poster.jpg", "Season01.jpg"])   # stale

    media = {
        "movies": [_movie("Filler", 2000, "/movies/Filler (2000)")],
        "series": [_series("Workin Moms", 2018, "/tv/Workin Moms (2018)")],
        "collections": [_collection("Marvel")],
    }

    result = _run(test_db, dest, media, dry_run=False, delete_unknown=False)

    assert (dest / "Workin Moms (2018)").exists()
    assert not (dest / "Workin' Moms (2018)").exists()
    assert result["counts"]["removed_stale"] == 1
    assert result["counts"]["removed_orphans"] == 0


def test_stale_duplicate_resolved_in_tmp_too(test_db, tmp_path):
    dest = tmp_path / "assets"
    _make_folder(dest, "Workin Moms (2018)", ["poster.jpg", "Season01.jpg"])
    _make_folder(dest, "Workin' Moms (2018)", ["poster.jpg", "Season01.jpg"])
    tmp = dest / "tmp"
    _make_folder(tmp, "Workin Moms (2018)", ["poster.jpg", "Season01.jpg"])
    _make_folder(tmp, "Workin' Moms (2018)", ["poster.jpg", "Season01.jpg"])

    media = {
        "movies": [],
        "series": [_series("Workin Moms", 2018, "/tv/Workin Moms (2018)")],
        "collections": [_collection("Marvel")],
    }

    _run(test_db, dest, media, dry_run=False, delete_unknown=False)

    assert (dest / "Workin Moms (2018)").exists()
    assert not (dest / "Workin' Moms (2018)").exists()
    assert (tmp / "Workin Moms (2018)").exists()
    assert not (tmp / "Workin' Moms (2018)").exists()


def test_sole_copy_cosmetic_drift_is_kept(test_db, tmp_path):
    dest = tmp_path / "assets"
    _make_folder(dest, "WALL-E (2008)", ["poster.jpg"])  # only copy, name differs from arr

    media = {
        "movies": [_movie("WALLE", 2008, "/movies/WALLE (2008)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    result = _run(test_db, dest, media, dry_run=False, delete_unknown=False)

    assert (dest / "WALL-E (2008)").exists()
    assert result["counts"]["removed_orphans"] == 0
    assert result["counts"]["removed_stale"] == 0


def test_stray_and_collection_kept_by_default_removed_with_delete_unknown(test_db, tmp_path):
    dest = tmp_path / "assets"
    _make_folder(dest, "Some Random Folder", ["poster.jpg"])  # year-less stray (collection type)
    _make_folder(dest, "Gone Collection", ["poster.jpg"])     # collection not in Plex

    media = {
        "movies": [_movie("The Matrix", 1999, "/movies/The Matrix (1999)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],  # present so collections type is healthy
    }

    keep_result = _run(test_db, dest, media, dry_run=False, delete_unknown=False)
    assert (dest / "Some Random Folder").exists()
    assert (dest / "Gone Collection").exists()
    assert keep_result["counts"]["unknown_kept"] == 2
    assert keep_result["counts"]["removed_orphans"] == 0

    del_result = _run(test_db, dest, media, dry_run=False, delete_unknown=True)
    assert not (dest / "Some Random Folder").exists()
    assert not (dest / "Gone Collection").exists()
    assert del_result["counts"]["removed_orphans"] == 2


def test_dry_run_deletes_nothing(test_db, tmp_path):
    dest = tmp_path / "assets"
    _make_folder(dest, "Old Movie (2001)", ["poster.jpg"])

    media = {
        "movies": [_movie("The Matrix", 1999, "/movies/The Matrix (1999)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    result = _run(test_db, dest, media, dry_run=True, delete_unknown=True)

    assert (dest / "Old Movie (2001)").exists()
    assert result["dry_run"] is True
    # Reported as would-remove even though nothing was deleted.
    assert len(result["removed"]) == 1


def test_aborts_when_all_media_empty(test_db, tmp_path):
    dest = tmp_path / "assets"
    _make_folder(dest, "Old Movie (2001)", ["poster.jpg"])

    media = {"movies": [], "series": [], "collections": []}
    result = _run(test_db, dest, media, dry_run=False, delete_unknown=True)

    assert result["skipped_for_safety"] is True
    assert (dest / "Old Movie (2001)").exists()


def test_per_type_safety_gate_skips_empty_source(test_db, tmp_path):
    """Movies source empty (e.g. Radarr down) → movie folders are never removed."""
    dest = tmp_path / "assets"
    _make_folder(dest, "Old Movie (2001)", ["poster.jpg"])           # would-be movie orphan
    _make_folder(dest, "Dead Show (2010)", ["poster.jpg", "Season01.jpg"])  # series orphan

    media = {
        "movies": [],  # unhealthy / down
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    result = _run(test_db, dest, media, dry_run=False, delete_unknown=True)

    assert (dest / "Old Movie (2001)").exists()         # protected by per-type gate
    assert not (dest / "Dead Show (2010)").exists()     # series source healthy → removed
    assert "movies" in result["skipped_types"]


def test_db_rows_purged_for_removed_folder(test_db, tmp_path):
    dest = tmp_path / "assets"
    orphan = _make_folder(dest, "Old Movie (2001)", ["poster.jpg"])
    poster_path = str(orphan / "poster.jpg")

    test_db.add(Poster(drive_id="d1", file_name="poster.jpg", file_path=poster_path))
    test_db.add(PlexUploadRecord(file_path=poster_path))
    test_db.commit()

    media = {
        "movies": [_movie("The Matrix", 1999, "/movies/The Matrix (1999)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    _run(test_db, dest, media, dry_run=False, delete_unknown=False)

    assert test_db.query(Poster).filter(Poster.file_path == poster_path).first() is None
    assert test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == poster_path).first() is None


def test_missing_destination_is_noop(test_db, tmp_path):
    result = _run(test_db, tmp_path / "does-not-exist", {"movies": [_movie("X", 2000, "/m/X (2000)")]}, dry_run=False)
    assert result["error"] is not None
    assert result["counts"]["removed_orphans"] == 0
