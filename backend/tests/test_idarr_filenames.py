"""IDarr filename generation and file ops: tag stripping, collection/season
suffixes, parse_asset, rename-in-place, transfers, and rename history caps."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from models.idarr import IdarrAssetCache
from services.idarr_runner import IdarrRunner


def test_rename_in_place_same_source_destination_is_noop(tmp_path):
    source = tmp_path / "same.jpg"
    source.write_bytes(b"image-bytes")

    renamed = IdarrRunner._rename_in_place(source=source, target_name=source.name, dry_run=False)

    assert renamed is True
    assert source.exists()


def test_rename_in_place_conflict_is_skipped(tmp_path):
    work_dir = tmp_path / "idarr-drive"
    work_dir.mkdir(parents=True, exist_ok=True)

    source = work_dir / "28 Years Later (2025).jpg"
    destination = work_dir / "28 Years Later (2025) {tmdb-1100988} {imdb-tt10548174}.jpg"

    source.write_bytes(b"new-poster")
    destination.write_bytes(b"existing-poster")

    renamed = IdarrRunner._rename_in_place(source=source, target_name=destination.name, dry_run=False)

    assert renamed is False
    assert source.exists()
    assert source.read_bytes() == b"new-poster"
    assert destination.exists()
    assert destination.read_bytes() == b"existing-poster"


def test_idarr_runner_parse_repairs_malformed_year_paren():
    """A dangling year parenthesis (from an interrupted/partial rename) must be repaired so the
    year is parsed and stripped, instead of becoming a junk title that never matches and churns
    as a recurring pending item. Balanced/bare-number titles must be left alone."""
    # Dangling opening paren.
    p = IdarrRunner._parse_asset_no_season_hint(Path("/x/Bert Kreischer Lucky (2025.psd"))
    assert p["title"] == "Bert Kreischer Lucky" and p["year"] == 2025
    # Dangling closing paren.
    p = IdarrRunner._parse_asset_no_season_hint(Path("/x/Bert Kreischer Lucky 2025).psd"))
    assert p["title"] == "Bert Kreischer Lucky" and p["year"] == 2025
    # Well-formed name is unchanged.
    p = IdarrRunner._parse_asset_no_season_hint(Path("/x/Bert Kreischer Lucky (2025).psd"))
    assert p["title"] == "Bert Kreischer Lucky" and p["year"] == 2025
    # Bare number with no parens is left as part of the title (ambiguous — could be like
    # "Blade Runner 2049"), so no year is fabricated.
    p = IdarrRunner._parse_asset_no_season_hint(Path("/x/Blade Runner 2049.psd"))
    assert p["year"] is None
    assert "2049" in p["title"]


def test_idarr_runner_parse_year_less_imdb_tagged_file_is_a_movie():
    """Without a year, the only remaining collection hint is 'no year at all' — but TMDB
    collections have no IMDb id, so an {imdb-tt…} tag means a movie whose filename lost its year."""
    p = IdarrRunner._parse_asset_no_season_hint(Path("/x/Leo 2 {tmdb-1235976} {imdb-tt31066554} - logo.png"))
    assert p["type"] == "movie"
    assert p["title"] == "Leo 2" and p["year"] is None
    assert p["type_is_inferred"] is False
    # An explicit "Collection" in the name still wins, and a year-less file with no IMDb tag
    # stays a collection (Kometa's custom-collection assets look exactly like this).
    assert IdarrRunner._parse_asset_no_season_hint(Path("/x/a24 - logo.png"))["type"] == "collection"
    assert IdarrRunner._parse_asset_no_season_hint(
        Path("/x/Alien Collection {tmdb-8091} - background.jpg"))["type"] == "collection"


def test_idarr_runner_generate_filename_strips_existing_tags_and_prefers_new_ids(test_db):
    runner = IdarrRunner(test_db)
    asset = {
        "title": "The Matrix",
        "year": 1999,
        "new_title": "The Matrix Reloaded",
        "new_year": 2003,
        "tmdb_id": 603,
        "new_tmdb_id": 604,
        "imdb_id": "tt0133093",
        "new_imdb_id": "tt0234215",
    }

    old_name = "The Matrix (1999) {tmdb-603} {imdb-tt0133093} Season 1.jpg"
    new_name = runner._generate_new_filename(asset, old_name)

    assert "{tmdb-604}" in new_name
    assert "{imdb-tt0234215}" in new_name
    assert "{tmdb-603}" not in new_name
    assert "{imdb-tt0133093}" not in new_name
    assert "Season 1" not in new_name
    assert new_name.endswith(".jpg")


def test_idarr_runner_update_cache_rename_history_caps_entries(test_db):
    runner = IdarrRunner(test_db)
    asset = {
        "title": "History Test",
        "year": 2024,
        "type": "movie",
    }

    seed_history = [
        {
            "from": f"old_{index}.jpg",
            "to": f"new_{index}.jpg",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        for index in range(25)
    ]

    cache_row = IdarrAssetCache(
        asset_key="movie::historytest::2024",
        title="History Test",
        year=2024,
        asset_type="movie",
        matched=True,
        payload_json=json.dumps(
            {
                "title": "History Test",
                "year": 2024,
                "type": "movie",
                "rename_history": seed_history,
                "current_filenames": ["current.jpg"],
                "original_filenames": ["original.jpg"],
            }
        ),
    )
    test_db.add(cache_row)
    test_db.commit()

    updated = runner._update_cache_rename_history(
        asset=asset,
        old_filename="latest_old.jpg",
        new_filename="latest_new.jpg",
    )
    assert updated is True
    test_db.commit()

    refreshed = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == cache_row.asset_key).first()
    payload = json.loads(refreshed.payload_json or "{}")
    history = payload.get("rename_history")
    assert isinstance(history, list)
    assert len(history) == 20
    assert history[-1]["from"] == "latest_old.jpg"
    assert history[-1]["to"] == "latest_new.jpg"


def test_idarr_runner_transfer_file_replaces_older_destination_and_archives_duplicate(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    source = tmp_path / "newer.jpg"
    destination = tmp_path / "target.jpg"
    duplicates_dir = tmp_path / "idarr_duplicates"
    duplicates_dir.mkdir(parents=True, exist_ok=True)

    destination.write_text("old")
    time.sleep(0.01)
    source.write_text("new")

    transferred, archived_duplicate, conflicted, archived_path, reason = runner._transfer_file(
        source=source,
        destination=destination,
        action_type="move",
        dry_run=False,
        duplicates_dir=duplicates_dir,
    )

    assert transferred is True
    assert archived_duplicate is True
    assert conflicted is True
    assert reason == "replaced_older_destination"
    assert archived_path is not None
    assert destination.exists()
    assert destination.read_text() == "new"
    assert source.exists() is False


def test_idarr_runner_generate_filename_preserves_hyphenated_season_suffix(test_db):
    runner = IdarrRunner(test_db)
    asset = {
        "title": "The Show",
        "year": 2024,
        "tmdb_id": 555,
    }

    old_name = "The Show - Season 01 {tmdb-111}.jpg"
    new_name = runner._generate_new_filename(asset, old_name)

    assert " - Season 01" in new_name
    assert "{tmdb-555}" in new_name
    assert "{tmdb-111}" not in new_name


def test_idarr_runner_generate_filename_places_collection_year_before_collection_suffix(test_db):
    runner = IdarrRunner(test_db)
    asset = {
        "title": "Troll Collection",
        "year": 2022,
        "type": "collection",
        "tmdb_id": 1180834,
    }

    old_name = "Troll Collection {tmdb-1180834}.jpg"
    new_name = runner._generate_new_filename(asset, old_name)

    assert new_name == "Troll (2022) Collection {tmdb-1180834}.jpg"


def test_idarr_runner_generate_filename_collection_title_with_existing_year_does_not_duplicate_year(test_db):
    runner = IdarrRunner(test_db)
    asset = {
        "title": "Troll (2022) Collection",
        "year": 2022,
        "type": "collection",
        "tmdb_id": 1180834,
    }

    old_name = "Troll Collection {tmdb-1180834}.jpg"
    new_name = runner._generate_new_filename(asset, old_name)

    assert new_name == "Troll (2022) Collection {tmdb-1180834}.jpg"


def test_idarr_runner_parse_asset_preserves_year_in_collection_title(test_db, tmp_path):
    file_path = tmp_path / "Troll (2022) Collection {tmdb-1180834}.jpg"
    file_path.write_bytes(b"image")

    parsed = IdarrRunner._parse_asset(file_path)

    assert parsed["type"] == "collection"
    assert parsed["title"] == "Troll (2022) Collection"
    assert parsed["year"] == 2022


def test_idarr_runner_parse_asset_movie_still_strips_year_from_title(test_db, tmp_path):
    file_path = tmp_path / "Troll (2022) {tmdb-736526}.jpg"
    file_path.write_bytes(b"image")

    parsed = IdarrRunner._parse_asset(file_path)

    assert parsed["type"] == "movie"
    assert parsed["title"] == "Troll"
    assert parsed["year"] == 2022


def test_derive_asset_subtype_maps_each_subfolder():
    """Asset-drive subtype is derived from the subfolder in the source path."""
    from api.idarr import _derive_asset_subtype

    assert _derive_asset_subtype({"files": "/drive/logos/Air (2023) - logo.png"}) == "logo"
    assert _derive_asset_subtype({"files": "/drive/backgrounds/Air (2023) - background.jpg"}) == "background"
    assert _derive_asset_subtype({"files": "/drive/squareart/Air (2023) - squareart.jpg"}) == "squareart"
    # Windows-style separators and the flat root resolve to no subtype.
    assert _derive_asset_subtype({"files": r"C:\drive\squareart\Air (2023) - squareart.jpg"}) == "squareart"
    assert _derive_asset_subtype({"files": "/drive/Air (2023).jpg"}) is None
