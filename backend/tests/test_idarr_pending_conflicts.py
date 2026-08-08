"""IDarr pending assets and conflicts: store_pending_assets, conflict pickers,
and ignore/pending reconciliation."""

import json
from pathlib import Path

from models.idarr import IdarrAssetCache, IdarrPendingMatch
from services.idarr_runner import IdarrRunner


def test_idarr_collect_conflict_pending_marks_new_vs_old(test_db):
    """An intra-batch conflict tags each file old (tracked) or new, index-aligned with conflict_files."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"
    old_asset = {
        "file_path": Path("/plexart/logos/Show (2020) {tmdb-5} - logo.png"),
        "title": "Show", "year": 2020, "type": "tv_series", "_previously_tracked": True,
    }
    new_asset = {
        "file_path": Path("/plexart/logos/junk999.png"),
        "title": "Show", "year": 2020, "type": "tv_series", "_previously_tracked": False,
    }
    conflict_rows = [{
        "resolution": "intra_batch_filename_conflict",
        "source_paths": [str(old_asset["file_path"]), str(new_asset["file_path"])],
        "source_path": str(old_asset["file_path"]),
    }]

    result = runner._collect_conflict_pending_assets(assets=[old_asset, new_asset], conflict_rows=conflict_rows)

    assert len(result) == 1
    item = result[0]
    assert item["conflict_files"] == ["Show (2020) {tmdb-5} - logo.png", "junk999.png"]
    assert item["conflict_file_tracked"] == [True, False]


def test_idarr_runner_sync_ignore_and_pending_reconciles_rows(test_db):
    runner = IdarrRunner(test_db)

    ignored_key = "movies::ignoredtitle::2020"
    reactivated_key = "movies::reactivatetitle::2021"

    test_db.add(
        IdarrPendingMatch(
            asset_key=ignored_key,
            title="IgnoredTitle",
            year=2020,
            asset_type="movie",
        )
    )
    test_db.add(
        IdarrAssetCache(
            asset_key=ignored_key,
            title="IgnoredTitle",
            year=2020,
            asset_type="movie",
            matched=False,
            payload_json=json.dumps({"status": "not_found"}),
        )
    )
    test_db.add(
        IdarrAssetCache(
            asset_key=reactivated_key,
            title="ReactivateTitle",
            year=2021,
            asset_type="movie",
            matched=False,
            payload_json=json.dumps({"status": "ignored"}),
        )
    )
    test_db.commit()

    stats = runner._sync_ignore_and_pending({ignored_key})
    assert stats["removed_pending"] == 1
    assert stats["marked_ignored"] == 1
    assert stats["unignored_reactivated"] == 1
    assert stats["restored_pending"] == 1

    ignored_pending = test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == ignored_key).first()
    reactivated_pending = test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == reactivated_key).first()
    assert ignored_pending is None
    assert reactivated_pending is not None


def test_idarr_runner_store_pending_assets_sets_pending_entry_and_prunes_stale_extra(test_db):
    runner = IdarrRunner(test_db)

    extra = IdarrPendingMatch(
        asset_key="movie::oldextra::1990",
        title="OldExtra",
        year=1990,
        asset_type="movie",
    )
    test_db.add(extra)
    test_db.commit()

    unmatched_assets = [
        {"title": "Blade Runner", "year": 1982, "type": "movie"},
    ]
    runner._store_pending_assets(unmatched_assets)

    desired_key = runner._asset_key(asset_type="movie", title="Blade Runner", year=1982)
    desired_row = test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == desired_key).first()
    extra_row = test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == "movie::oldextra::1990").first()

    assert desired_row is not None
    assert extra_row is None

    cache_row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == desired_key).first()
    assert cache_row is not None
    payload = json.loads(cache_row.payload_json or "{}")
    assert payload.get("status") == "not_found"
    pending_entry = payload.get("pending_entry")
    assert isinstance(pending_entry, dict)
    assert pending_entry.get("add_tmdb_url_here") == "add_tmdb_url_here"
    assert "themoviedb.org" in str(pending_entry.get("google_search") or "")


def test_idarr_runner_store_pending_assets_removes_extra_resolved_rows_by_existing_ids(test_db):
    runner = IdarrRunner(test_db)

    resolved_key = "movies::resolvedtitle::2020"
    stale_pending = IdarrPendingMatch(
        asset_key=resolved_key,
        title="ResolvedTitle",
        year=2020,
        asset_type="movie",
    )
    cache_row = IdarrAssetCache(
        asset_key=resolved_key,
        title="ResolvedTitle",
        year=2020,
        asset_type="movie",
        tmdb_id=999,
        matched=False,
        payload_json=json.dumps({"status": "found"}),
    )
    test_db.add_all([stale_pending, cache_row])
    test_db.commit()

    runner._store_pending_assets([
        {"title": "Blade Runner", "year": 1982, "type": "movie"},
    ])

    removed_row = test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == resolved_key).first()
    assert removed_row is None


def test_idarr_runner_collect_conflict_pending_assets_maps_conflict_rows_to_asset_metadata(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Zack Snyder's Justice League Justice is Gray (2021).jpg"
    source_file.write_bytes(b"image")

    assets = [
        {
            "file_path": source_file,
            "title": "Zack Snyder's Justice League Justice is Gray",
            "year": 2021,
            "type": "movie",
        }
    ]

    conflict_rows = [
        {
            "source_path": str(source_file),
            "target_path": str(tmp_path / "Zack Snyder's Justice League (2021) {tmdb-791373} {imdb-tt12361974}.jpg"),
            "resolution": "in_place_conflict_kept_existing",
        }
    ]

    pending_assets = runner._collect_conflict_pending_assets(assets=assets, conflict_rows=conflict_rows)

    assert len(pending_assets) == 1
    assert pending_assets[0]["title"] == "Zack Snyder's Justice League Justice is Gray"
    assert pending_assets[0]["year"] == 2021
    assert pending_assets[0]["type"] == "movie"
    assert pending_assets[0]["pending_reason"] == "rename_conflict"
    assert pending_assets[0]["source_path"] == str(source_file)
    # Target doesn't exist on disk → no picker, just a plain conflict entry.
    assert "conflict_files" not in pending_assets[0]


def test_idarr_runner_collect_conflict_pending_assets_builds_picker_when_target_exists(test_db, tmp_path):
    """When the existing target is on disk, the conflict entry carries a two-file picker labelled by the canonical match."""
    runner = IdarrRunner(test_db)

    incoming = tmp_path / "abcdef.png"
    incoming.write_bytes(b"incoming")
    existing = tmp_path / "From Dusk Till Dawn 3 (2000).png"
    existing.write_bytes(b"existing")

    assets = [
        {
            "file_path": incoming,
            "title": "abcdef",
            "new_title": "From Dusk Till Dawn 3",
            "new_year": 2000,
            "type": "movie",
            "_previously_tracked": False,
        }
    ]
    conflict_rows = [
        {
            "source_path": str(incoming),
            "target_path": str(existing),
            "resolution": "rename_target_exists",
        }
    ]

    pending_assets = runner._collect_conflict_pending_assets(assets=assets, conflict_rows=conflict_rows)

    assert len(pending_assets) == 1
    entry = pending_assets[0]
    # Labelled by the canonical match, not the dirty parsed name.
    assert entry["title"] == "From Dusk Till Dawn 3"
    assert entry["year"] == 2000
    # Existing first (old/tracked), incoming second (new), index-aligned.
    assert entry["conflict_files"] == ["From Dusk Till Dawn 3 (2000).png", "abcdef.png"]
    assert entry["conflict_file_paths"] == [str(existing), str(incoming)]
    assert entry["conflict_file_tracked"] == [True, False]


def test_idarr_runner_store_pending_assets_prefers_conflict_source_path_for_pending_entry(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Dune (2021)_abc123.jpg"
    source_file.write_bytes(b"image")

    runner._store_pending_assets([
        {
            "title": "Dune",
            "year": 2021,
            "type": "movie",
            "pending_reason": "rename_conflict",
            "source_path": str(source_file),
        }
    ])

    cache_row = test_db.query(IdarrAssetCache).filter(
        IdarrAssetCache.title == "Dune",
        IdarrAssetCache.year == 2021,
    ).first()
    assert cache_row is not None
    payload = json.loads(cache_row.payload_json or "{}")
    pending_entry = payload.get("pending_entry")
    assert isinstance(pending_entry, dict)
    assert pending_entry.get("files") == str(source_file.resolve())


def test_idarr_runner_collect_conflict_pending_assets_keeps_duplicates_separate_by_source_path(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    source_file_a = tmp_path / "Dune (2021).jpg"
    source_file_b = tmp_path / "Dune (2021)_alt.jpg"
    source_file_a.write_bytes(b"image-a")
    source_file_b.write_bytes(b"image-b")

    assets = [
        {
            "file_path": source_file_a,
            "title": "Dune",
            "year": 2021,
            "type": "movie",
        },
        {
            "file_path": source_file_b,
            "title": "Dune",
            "year": 2021,
            "type": "movie",
        },
    ]

    conflict_rows = [
        {
            "source_path": str(source_file_a),
            "target_path": str(tmp_path / "Dune (2021) {tmdb-438631}.jpg"),
            "resolution": "in_place_conflict_kept_existing",
        },
        {
            "source_path": str(source_file_b),
            "target_path": str(tmp_path / "Dune (2021) {tmdb-438631}.jpg"),
            "resolution": "in_place_conflict_kept_existing",
        },
    ]

    pending_assets = runner._collect_conflict_pending_assets(assets=assets, conflict_rows=conflict_rows)

    assert len(pending_assets) == 2
    source_paths = {str(item.get("source_path") or "") for item in pending_assets}
    assert str(source_file_a) in source_paths
    assert str(source_file_b) in source_paths


def test_idarr_runner_store_pending_assets_creates_distinct_conflict_keys_for_same_title_year(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    source_file_a = tmp_path / "Dune (2021).jpg"
    source_file_b = tmp_path / "Dune (2021)_alt.jpg"
    source_file_a.write_bytes(b"image-a")
    source_file_b.write_bytes(b"image-b")

    runner._store_pending_assets([
        {
            "title": "Dune",
            "year": 2021,
            "type": "movie",
            "pending_reason": "rename_conflict",
            "source_path": str(source_file_a),
        },
        {
            "title": "Dune",
            "year": 2021,
            "type": "movie",
            "pending_reason": "rename_conflict",
            "source_path": str(source_file_b),
        },
    ])

    rows = test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.title == "Dune", IdarrPendingMatch.year == 2021).all()
    assert len(rows) == 2
    keys = [str(row.asset_key or "") for row in rows]
    assert all("::conflict=" in key for key in keys)
