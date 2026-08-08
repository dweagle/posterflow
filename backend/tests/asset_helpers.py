"""Shared media-dict builders and folder seeding for the asset cleanup/scan/rename tests."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from services.asset_cleanup import AssetCleanupService
from util.data.normalization import normalize_titles


def _make_folder(root: Path, name: str, files: List[str]) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for file_name in files:
        (folder / file_name).write_bytes(b"img")
    return folder


def _movie(title: str, year: int, folder: str, tmdb_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "type": "movies", "title": title, "year": year, "tmdb_id": tmdb_id, "imdb_id": None,
        "normalized_title": normalize_titles(title), "alternate_titles": [],
        "normalized_alternate_titles": [], "folder": folder,
    }


def _series(title: str, year: int, folder: str, tvdb_id: Optional[int] = None,
            seasons: Optional[List[int]] = None) -> Dict[str, Any]:
    return {
        "type": "series", "title": title, "year": year, "tvdb_id": tvdb_id, "imdb_id": None,
        "normalized_title": normalize_titles(title), "alternate_titles": [],
        "normalized_alternate_titles": [], "folder": folder,
        "seasons": [{"season_number": n, "season_has_episodes": True} for n in (seasons or [1])],
    }


def _collection(title: str) -> Dict[str, Any]:
    return {
        "type": "collections", "title": title, "year": None, "tmdb_id": None,
        "normalized_title": normalize_titles(title), "alternate_titles": [],
        "normalized_alternate_titles": [], "folder": title,
    }


def _seed_artwork(root, *, logo=None, background=None, squareart=None):
    for sub, fname in (("logos", logo), ("backgrounds", background), ("squareart", squareart)):
        if not fname:
            continue
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_bytes(b"img")


def _run(test_db, dest: Path, media_dict, **kwargs):
    return AssetCleanupService(test_db).cleanup(str(dest), media_dict=media_dict, **kwargs)
