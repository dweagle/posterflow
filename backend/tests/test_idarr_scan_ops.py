"""IDarr disk-facing scan ops: id realignment, stale reverify, orphan pruning,
and targeted scans."""

import json
from datetime import datetime, timezone

from models.idarr import IdarrAssetCache
from services.idarr_runner import IdarrRunner


def test_idarr_runner_sync_asset_ids_from_cache_realigns_stale_files(test_db, tmp_path):
    """A file scanned while the cache still agreed with its tags keeps stale ids when a later
    file's fetch corrects the item mid-run; the pre-rename realign pass brings every file of
    the item onto the final cache ids so collisions surface instead of silently no-oping."""
    runner = IdarrRunner(test_db)
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::littlehouseontheprairie::2026::tmdb=283304",
        title="Little House on the Prairie", year=2026, asset_type="tv_series",
        tmdb_id=283304, tvdb_id=459561, imdb_id="tt2431250", matched=True, payload_json="{}",
    ))
    test_db.commit()

    stale_file = tmp_path / "Little House on the Prairie (2026) {tmdb-283304} {tvdb-459561} {imdb-tt13829154} - background.jpg"
    stale_file.write_bytes(b"x")
    assets = [
        {"file_path": stale_file, "title": "Little House on the Prairie", "year": 2026,
         "type": "tv_series", "tmdb_id": 283304, "tvdb_id": 459561, "imdb_id": "tt13829154"},
        {"file_path": tmp_path / "unmatched.jpg", "title": "Unmatched", "year": None,
         "type": "tv_series", "tmdb_id": None, "tvdb_id": None, "imdb_id": None},
    ]
    assert runner._sync_asset_ids_from_cache(assets) == 1
    assert assets[0]["imdb_id"] == "tt2431250"
    assert assets[1]["imdb_id"] is None

    # Cross-type guard: a movie asset sharing the numeric tmdb id must not inherit series ids.
    movie_asset = {"file_path": stale_file, "title": "Some Movie", "year": 2004,
                   "type": "movie", "tmdb_id": 283304, "tvdb_id": None, "imdb_id": "tt0000001"}
    assert runner._sync_asset_ids_from_cache([movie_asset]) == 0
    assert movie_asset["imdb_id"] == "tt0000001"


def test_idarr_runner_stale_reverify_picks_up_upstream_relink_with_one_call(test_db, tmp_path, monkeypatch):
    """Present-and-consistent ids past the freshness window get one authoritative re-verify:
    an upstream TMDB imdb relink is applied to every file of the item, via a single
    external-ids call (memoized across the item's files)."""
    from datetime import timedelta

    runner = IdarrRunner(test_db)
    stale_checked = datetime.now(timezone.utc) - timedelta(days=60)
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::littlehouseontheprairie::2026::tmdb=283304",
        title="Little House on the Prairie", year=2026, asset_type="tv_series",
        tmdb_id=283304, tvdb_id=459561, imdb_id="tt13829154", matched=True,
        payload_json="{}", last_checked_at=stale_checked,
    ))
    test_db.commit()

    calls: list[int] = []

    def _fake_external_ids(*, api_key, tmdb_id, asset_type):
        calls.append(tmdb_id)
        return {"imdb_id": "tt2431250", "tvdb_id": 459561}

    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(_fake_external_ids))
    monkeypatch.setattr(
        IdarrRunner,
        "_tmdb_verify_id",
        staticmethod(lambda *, api_key, tmdb_id, asset_type, title, year=None: (
            {"id": tmdb_id, "name": title, "first_air_date": f"{year}-01-01"}, asset_type, None,
        )),
    )

    assets = []
    for subtype in ("background", "logo", "squareart"):
        source_file = tmp_path / f"Little House on the Prairie (2026) {{tmdb-283304}} {{tvdb-459561}} {{imdb-tt13829154}} - {subtype}.jpg"
        source_file.write_bytes(b"x")
        assets.append({
            "file_path": source_file, "title": "Little House on the Prairie", "year": 2026,
            "type": "tv_series", "tmdb_id": 283304, "tvdb_id": 459561,
            "imdb_id": "tt13829154", "has_id": True,
        })

    runner._enrich_assets_with_tmdb(
        assets, "fake-api-key", frequency_days=14, tvdb_frequency=14,
    )

    assert len(calls) == 1
    assert all(asset["imdb_id"] == "tt2431250" for asset in assets)


def test_idarr_runner_orphan_prune_finds_nested_files_and_removes_orphans(test_db, tmp_path):
    """Asset drives keep files in subfolders; the prune must list recursively or it would
    see zero files and gut the scope. Rows whose files are gone get removed."""
    runner = IdarrRunner(test_db)
    sub = tmp_path / "logos"
    sub.mkdir()
    (sub / "Kept Show (2020) {tmdb-1} - logo.png").write_bytes(b"x")
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::keptshow::2020::tmdb=1", title="Kept Show", year=2020,
        asset_type="tv_series", tmdb_id=1, matched=True,
        payload_json=json.dumps({"current_filenames": ["Kept Show (2020) {tmdb-1} - logo.png"]}),
    ))
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::goneshow::2020::tmdb=2", title="Gone Show", year=2020,
        asset_type="tv_series", tmdb_id=2, matched=True,
        payload_json=json.dumps({"current_filenames": ["Gone Show (2020) {tmdb-2} - logo.png"]}),
    ))
    test_db.commit()

    stats = runner._prune_orphaned_cache_entries(tmp_path)
    assert stats["removed_cache"] == 1
    keys = {r.asset_key for r in test_db.query(IdarrAssetCache).all()}
    assert "tv_series::keptshow::2020::tmdb=1" in keys
    assert "tv_series::goneshow::2020::tmdb=2" not in keys


def test_idarr_runner_orphan_prune_refuses_empty_or_gutted_source(test_db, tmp_path):
    """An unmounted (empty) or partially-mounted drive must not wipe the scope's cache."""
    runner = IdarrRunner(test_db)
    for i in range(20):
        test_db.add(IdarrAssetCache(
            asset_key=f"movie::title{i}::2020::tmdb={i + 10}", title=f"Title {i}", year=2020,
            asset_type="movie", tmdb_id=i + 10, matched=True,
            payload_json=json.dumps({"current_filenames": [f"Title {i} (2020) {{tmdb-{i + 10}}}.jpg"]}),
        ))
    test_db.commit()

    empty = tmp_path / "empty"
    empty.mkdir()
    assert runner._prune_orphaned_cache_entries(empty)["removed_cache"] == 0

    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "Title 0 (2020) {tmdb-10}.jpg").write_bytes(b"x")
    assert runner._prune_orphaned_cache_entries(partial)["removed_cache"] == 0

    assert test_db.query(IdarrAssetCache).count() == 20


def test_idarr_runner_targeted_scan_only_parses_selected_files(test_db, tmp_path):
    """A single-item run (resolve-and-rename / conflict Keep) must not parse the whole
    scope: only the selected file comes back, and it still inherits the resolved cache
    row's IDs via the title/year hint — the path the resolve endpoint depends on."""
    runner = IdarrRunner(test_db)
    # Row the resolve endpoint writes: keyed by the dirty title, carrying the chosen IDs.
    test_db.add(IdarrAssetCache(
        asset_key="movie::doctorstrange::2016",
        title="Doctor Strange", year=2016, asset_type="movie",
        tmdb_id=284052, matched=True, payload_json="{}",
    ))
    test_db.commit()
    (tmp_path / "Doctor Strange (2016).jpg").write_bytes(b"x")
    (tmp_path / "Frozen (2013).jpg").write_bytes(b"x")
    (tmp_path / "Moana (2016).jpg").write_bytes(b"x")

    assets = runner._scan_assets(tmp_path, only_filenames={"doctor strange (2016).jpg"})
    assert len(assets) == 1
    assert assets[0]["title"] == "Doctor Strange"

    # The flat asset-drive scan applies title/year cache hints directly — the targeted
    # fetch must surface the resolved row so the file inherits its IDs.
    flat = runner._scan_assets_for_asset_drive(tmp_path, only_filenames={"doctor strange (2016).jpg"})
    assert len(flat) == 1
    assert flat[0]["tmdb_id"] == 284052


def test_idarr_runner_targeted_scan_season_group_gets_hints(test_db, tmp_path):
    """Season files look hints up under the group's base title — the targeted cache fetch
    must include rows keyed by that group title, not just the per-file titles."""
    runner = IdarrRunner(test_db)
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::breakingbad::",
        title="Breaking Bad", year=None, asset_type="tv_series",
        tmdb_id=1396, tvdb_id=81189, matched=True, payload_json="{}",
    ))
    test_db.commit()
    (tmp_path / "Breaking Bad - Season 1.jpg").write_bytes(b"x")
    (tmp_path / "Other Show - Season 2.jpg").write_bytes(b"x")

    assets = runner._scan_assets(tmp_path, only_filenames={"breaking bad - season 1.jpg"})
    assert len(assets) == 1
    assert assets[0]["type"] == "tv_series"
    assert assets[0]["tvdb_id"] == 81189


def test_idarr_runner_targeted_scan_filename_index_and_tracked(test_db, tmp_path):
    """A row whose title doesn't match still has to reach the targeted scan when it holds
    the selected filename — that's what feeds the filename index and the tracked flag."""
    runner = IdarrRunner(test_db)
    test_db.add(IdarrAssetCache(
        asset_key="movie::completelydifferent::1999::tmdb=42",
        title="Completely Different", year=1999, asset_type="movie",
        tmdb_id=42, matched=True,
        payload_json=json.dumps({"current_filenames": ["Some Movie (2020).jpg"]}),
    ))
    test_db.commit()
    (tmp_path / "Some Movie (2020).jpg").write_bytes(b"x")

    assets = runner._scan_assets(tmp_path, only_filenames={"some movie (2020).jpg"})
    assert len(assets) == 1
    assert assets[0]["_previously_tracked"] is True

    # The flat scan's filename index applies the row's IDs directly.
    flat = runner._scan_assets_for_asset_drive(tmp_path, only_filenames={"some movie (2020).jpg"})
    assert flat[0]["_previously_tracked"] is True
    assert flat[0]["tmdb_id"] == 42


def test_idarr_runner_load_cache_rows_for_selected_filters(test_db, tmp_path):
    """Targeted fetch keeps rows matching by alias-normalized title, by id, or by held
    filename — and drops unrelated rows."""
    runner = IdarrRunner(test_db)
    test_db.add(IdarrAssetCache(
        asset_key="movie::doctorstrange::2016",
        title="Doctor Strange", year=2016, asset_type="movie", matched=True, payload_json="{}",
    ))
    test_db.add(IdarrAssetCache(
        asset_key="movie::unrelatedtitle::2001::tmdb=999",
        title="Unrelated Title", year=2001, asset_type="movie", tmdb_id=999, matched=True, payload_json="{}",
    ))
    test_db.add(IdarrAssetCache(
        asset_key="movie::otherthing::2005",
        title="Other Thing", year=2005, asset_type="movie", matched=True,
        payload_json=json.dumps({"current_filenames": ["My Poster File.jpg"]}),
    ))
    test_db.add(IdarrAssetCache(
        asset_key="movie::nothing::1990",
        title="Nothing", year=1990, asset_type="movie", matched=True, payload_json="{}",
    ))
    test_db.commit()

    selected_asset = {
        "file_path": tmp_path / "Dr Strange (2016) {tmdb-999}.jpg",
        "title": "Dr Strange", "year": 2016, "type": "movie",
        "tmdb_id": 999, "tvdb_id": None, "imdb_id": None,
    }
    rows = runner._load_cache_rows_for_selected([selected_asset], {}, {"my poster file.jpg"})
    keys = {row.asset_key for row in rows}
    assert "movie::doctorstrange::2016" in keys       # alias-normalized title match (Dr -> Doctor)
    assert "movie::unrelatedtitle::2001::tmdb=999" in keys  # tmdb id match
    assert "movie::otherthing::2005" in keys          # filename held in payload
    assert "movie::nothing::1990" not in keys
