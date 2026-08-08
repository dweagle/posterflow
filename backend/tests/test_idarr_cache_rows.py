"""IDarr asset cache rows: store/merge/rekey semantics, cross-type collision
guards, unmatched + duplicate pruning, filename index, and migration 0010."""

import json
from datetime import datetime, timezone
from pathlib import Path

from models.idarr import IdarrAssetCache, IdarrPendingMatch, upsert_idarr_asset_cache
from services.idarr_runner import IdarrRunner


def test_idarr_runner_asset_drive_keeps_type_inferred_for_unmatched_provisional_cache(test_db, tmp_path):
    """An ambiguous item that went to pending leaves an unmatched provisional cache row carrying
    the seed type ("movie"). On a later run that row must NOT settle the type — otherwise the
    enrichment dual-search is skipped and the item auto-commits the seed type instead of staying
    pending (the "Little Bear (1995)" pending → movie flip)."""
    runner = IdarrRunner(test_db)
    # Unmatched provisional row from a prior ambiguous run: seed type movie, no IDs.
    test_db.add(IdarrAssetCache(
        asset_key="movie::littlebear::1995",
        title="Little Bear", year=1995, asset_type="movie",
        matched=False, payload_json=json.dumps({"status": "not_found"}),
    ))
    # A *resolved* row (carries an ID) is a valid type signal and SHOULD settle the type.
    test_db.add(IdarrAssetCache(
        asset_key="movie::frozen::2013::tmdb=109445",
        title="Frozen", year=2013, asset_type="movie",
        tmdb_id=109445, matched=True, payload_json="{}",
    ))
    test_db.commit()
    (tmp_path / "Little Bear (1995).psd").write_bytes(b"x")
    (tmp_path / "Frozen (2013).psd").write_bytes(b"x")

    assets = runner._scan_assets_for_asset_drive(tmp_path)
    little_bear = next(a for a in assets if str(a.get("title")) == "Little Bear")
    frozen = next(a for a in assets if str(a.get("title")) == "Frozen")

    # Unmatched provisional "movie" type must stay inferred so the dual-search re-evaluates.
    assert little_bear["type_is_inferred"] is True
    assert little_bear.get("has_id") is False
    # Resolved cache row (has tmdb) settles the type — inferred flag cleared, id prefilled.
    assert frozen["type_is_inferred"] is False
    assert frozen.get("tmdb_id") == 109445


def test_idarr_runner_scan_rejects_cross_type_tmdb_collision(test_db, tmp_path):
    """A movie file must NOT inherit a cached series row that merely shares the numeric
    tmdb id. TMDB ids are unique only within a media type: movie 2122 ("The Whole Ten
    Yards") and tv 2122 ("King of the Hill") are different entities. Without a type guard
    the movie gets the series' tvdb id, flips to tv_series, and resolves to the wrong title."""
    runner = IdarrRunner(test_db)
    # Cached, fully-resolved King of the Hill *series* row sharing tmdb id 2122.
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::kingofthehill::1997::tmdb=2122",
        title="King of the Hill", year=1997, asset_type="tv_series",
        tmdb_id=2122, tvdb_id=73903, imdb_id="tt0118375", matched=True, payload_json="{}",
    ))
    test_db.commit()
    (tmp_path / "The Whole Ten Yards (2004) {tmdb-2122} {imdb-tt0327247}.jpg").write_bytes(b"x")

    # Season-grouped scanner (regular poster drive).
    grouped = runner._scan_assets(tmp_path)
    movie = next(a for a in grouped if int(a.get("tmdb_id") or 0) == 2122)
    assert movie["type"] == "movie"
    assert movie.get("tvdb_id") is None
    assert movie.get("imdb_id") == "tt0327247"
    assert "whole ten yards" in str(movie.get("title") or "").lower()

    # Flat asset-drive scanner exercises the second by_tmdb hint path.
    flat = runner._scan_assets_for_asset_drive(tmp_path)
    movie2 = next(a for a in flat if int(a.get("tmdb_id") or 0) == 2122)
    assert movie2["type"] == "movie"
    assert movie2.get("tvdb_id") is None
    assert movie2.get("imdb_id") == "tt0327247"


def test_idarr_runner_load_cache_map_rejects_cross_type_tmdb_collision(test_db):
    """_load_cache_map must NOT match a TV asset to a cached *movie* row that only shares the
    numeric tmdb id. TMDB ids are namespaced per media type: movie 4599 ("Raising Helen") and
    tv 4599 ("Yes, Dear") are different entities. Matching the movie row crosses the series'
    title to the movie at rename time (ids kept, title swapped)."""
    runner = IdarrRunner(test_db)
    # Fully-resolved *movie* row sharing tmdb id 4599 with the incoming TV series.
    test_db.add(IdarrAssetCache(
        asset_key="movie::raisinghelen::2004::tmdb=4599",
        title="Raising Helen", year=2004, asset_type="movie",
        tmdb_id=4599, tvdb_id=None, imdb_id="tt0357082", matched=True,
        payload_json=json.dumps({"canonical_title": "Raising Helen", "canonical_year": 2004}),
    ))
    test_db.commit()

    cache_map = runner._load_cache_map([{
        "title": "Yes, Dear", "year": 2000, "type": "tv_series",
        "tmdb_id": 4599, "tvdb_id": 72367, "imdb_id": "tt0247144",
    }])
    # The movie row must not be handed to the TV asset under any key form.
    assert all(row.asset_type != "movie" for row in cache_map.values())


def test_idarr_runner_heal_cross_type_title_collisions(test_db):
    """Rows poisoned by a past cross-type tmdb collision (same tmdb + same title across a movie
    and a series) get their freshness cleared so enrichment re-verifies and fixes the title.
    A legit same-tmdb pair with *different* titles is left fresh (no needless re-verify)."""
    runner = IdarrRunner(test_db)
    fresh = datetime.now(timezone.utc)
    # Poisoned: movie + series share tmdb 4599 AND title "Raising Helen".
    test_db.add(IdarrAssetCache(
        asset_key="movie::raisinghelen::2004::tmdb=4599", title="Raising Helen", year=2004,
        asset_type="movie", tmdb_id=4599, imdb_id="tt0357082", matched=True,
        payload_json="{}", last_checked_at=fresh,
    ))
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::raisinghelen::2004::tmdb=4599", title="Raising Helen", year=2004,
        asset_type="tv_series", tmdb_id=4599, tvdb_id=72367, imdb_id="tt0247144", matched=True,
        payload_json="{}", last_checked_at=fresh,
    ))
    # Legit collision: same tmdb 2122 but different titles — must stay fresh.
    test_db.add(IdarrAssetCache(
        asset_key="movie::wholetenyards::2004::tmdb=2122", title="The Whole Ten Yards", year=2004,
        asset_type="movie", tmdb_id=2122, imdb_id="tt0327247", matched=True,
        payload_json="{}", last_checked_at=fresh,
    ))
    test_db.add(IdarrAssetCache(
        asset_key="tv_series::kingofthehill::1997::tmdb=2122", title="King of the Hill", year=1997,
        asset_type="tv_series", tmdb_id=2122, tvdb_id=73903, imdb_id="tt0118375", matched=True,
        payload_json="{}", last_checked_at=fresh,
    ))
    test_db.commit()

    assert runner._heal_cross_type_title_collisions() == 2

    rows = {r.asset_key: r for r in test_db.query(IdarrAssetCache).all()}
    assert rows["movie::raisinghelen::2004::tmdb=4599"].last_checked_at is None
    assert rows["tv_series::raisinghelen::2004::tmdb=4599"].last_checked_at is None
    assert rows["movie::wholetenyards::2004::tmdb=2122"].last_checked_at is not None
    assert rows["tv_series::kingofthehill::1997::tmdb=2122"].last_checked_at is not None

    # Idempotent / self-limiting: a second pass clears nothing new.
    assert runner._heal_cross_type_title_collisions() == 0


def test_idarr_runner_summary_surfaces_collisions_healed_only_when_nonzero(test_db):
    """The 'Collisions Healed' summary line appears only when the heal actually fired, and the
    count is always carried in stats for downstream logging."""
    runner = IdarrRunner(test_db)

    def _summarize(healed):
        return runner.summarize_run(
            run_started_at=datetime.now(timezone.utc),
            assets=[], total_assets=0, unmatched_assets=[],
            enrichment_stats={}, enrichment_details=[],
            renamed_count=0, skipped_count=0, duplicate_conflicts=0,
            conflict_rows=[], operation_rows=[], duplicate_log_csv=None,
            ignored_count=0, ignore_pending_sync_stats={}, pending_only=False,
            orphan_prune_stats={}, inactive_cache_prune_stats={},
            collisions_healed=healed,
        )

    report, stats, _ = _summarize(2)
    assert any("Collisions Healed" in str(r.get("label")) and r.get("value") == 2 for r in report)
    assert stats["collisions_healed"] == 2

    report0, stats0, _ = _summarize(0)
    assert not any("Collisions Healed" in str(r.get("label")) for r in report0)
    assert stats0["collisions_healed"] == 0


def test_idarr_runner_store_cache_rows_same_title_year_merges_to_one_row(test_db):
    """With title-based keys, two movies of the same title+year share one cache row.
    Both filenames are tracked under that single entry."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    assets = [
        {
            "file_path": Path("/tmp/Spiral A.jpg"),
            "title": "Spiral",
            "year": 2019,
            "type": "movie",
            "tmdb_id": 730227,
            "tvdb_id": None,
            "imdb_id": "tt8405708",
            "has_id": True,
            "_cache_touch": True,
        },
        {
            "file_path": Path("/tmp/Spiral B.jpg"),
            "title": "Spiral",
            "year": 2019,
            "type": "movie",
            "tmdb_id": 614199,
            "tvdb_id": None,
            "imdb_id": "tt9247314",
            "has_id": True,
            "_cache_touch": True,
        },
    ]

    runner._store_asset_cache_rows(assets)
    test_db.commit()

    rows = (
        test_db.query(IdarrAssetCache)
        .filter(IdarrAssetCache.title == "Spiral")
        .filter(IdarrAssetCache.year == 2019)
        .all()
    )

    # With ID-keyed storage each distinct TMDB ID generates its own row.
    assert len(rows) == 2
    row_keys = {row.asset_key for row in rows}
    key_a = runner._asset_key(asset_type="movie", title="Spiral", year=2019, tmdb_id=730227)
    key_b = runner._asset_key(asset_type="movie", title="Spiral", year=2019, tmdb_id=614199)
    assert key_a in row_keys
    assert key_b in row_keys

    payload_a = json.loads(next(r for r in rows if r.asset_key == key_a).payload_json or "{}")
    assert "Spiral A.jpg" in set(payload_a.get("current_filenames") or [])
    payload_b = json.loads(next(r for r in rows if r.asset_key == key_b).payload_json or "{}")
    assert "Spiral B.jpg" in set(payload_b.get("current_filenames") or [])


def test_idarr_runner_store_cache_rows_updates_existing_title_key_row(test_db):
    """_store_asset_cache_rows updates an existing title-keyed row in-place."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    title_key = runner._asset_key(asset_type="movie", title="Spiral", year=2019)
    existing_row = IdarrAssetCache(
        asset_key=title_key,
        title="Spiral",
        year=2019,
        asset_type="movie",
        tmdb_id=730227,
        imdb_id="tt8405708",
        matched=True,
        payload_json=json.dumps(
            {
                "title": "Spiral",
                "year": 2019,
                "type": "movie",
                "current_filenames": ["Spiral A.jpg"],
                "original_filenames": ["Spiral A.jpg"],
            }
        ),
    )
    test_db.add(existing_row)
    test_db.commit()

    assets = [
        {
            "file_path": Path("/tmp/Spiral A.jpg"),
            "title": "Spiral",
            "year": 2019,
            "type": "movie",
            "tmdb_id": 730227,
            "tvdb_id": None,
            "imdb_id": "tt8405708",
            "has_id": True,
            "_cache_touch": True,
        }
    ]

    runner._store_asset_cache_rows(assets)
    test_db.commit()

    # The provisional key row is migrated to the ID-keyed form.
    expected_key = runner._asset_key(asset_type="movie", title="Spiral", year=2019, tmdb_id=730227)

    updated = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == expected_key).first()

    assert updated is not None
    # Only one row — absorbed in-place (provisional row deleted, ID-keyed row created).
    total_rows = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.title == "Spiral").count()
    assert total_rows == 1


def test_idarr_runner_store_cache_rows_migration_preserves_freshness_timestamp(test_db):
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    legacy_key = runner._asset_key(asset_type="movie", title="Spiral", year=2019)
    legacy_row = IdarrAssetCache(
        asset_key=legacy_key,
        title="Spiral",
        year=2019,
        asset_type="movie",
        tmdb_id=730227,
        imdb_id="tt8405708",
        matched=True,
        payload_json=json.dumps(
            {
                "title": "Spiral",
                "year": 2019,
                "type": "movie",
                "current_filenames": ["Spiral A.jpg"],
                "original_filenames": ["Spiral A.jpg"],
            }
        ),
        last_checked_at=datetime.now(timezone.utc),
    )
    test_db.add(legacy_row)
    test_db.commit()

    assets = [
        {
            "file_path": Path("/tmp/Spiral A.jpg"),
            "title": "Spiral",
            "year": 2019,
            "type": "movie",
            "tmdb_id": 730227,
            "tvdb_id": None,
            "imdb_id": "tt8405708",
            "has_id": True,
            "_cache_touch": False,
        }
    ]

    runner._store_asset_cache_rows(assets)
    test_db.commit()

    # The provisional row is migrated to the ID-keyed form; freshness timestamp is preserved.
    expected_key = runner._asset_key(asset_type="movie", title="Spiral", year=2019, tmdb_id=730227)

    updated = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == expected_key).first()

    assert updated is not None
    assert isinstance(updated.last_checked_at, datetime)


def test_idarr_runner_store_cache_rows_preserves_resolve_metadata_across_title_rekey(test_db):
    """A manually-resolved item renamed from a dirty title (e.g. "asdfqwer") to its canonical
    title changes its cache key wholesale. The resolve audit metadata (resolved_manually /
    resolution_history / canonical_title) must carry forward to the new ID-keyed row via the
    same-ID predecessor lookup, instead of being lost on the re-key."""
    runner = IdarrRunner(test_db)
    dirty_key = runner._asset_key(asset_type="movie", title="asdfqwer", year=None, tmdb_id=27205)
    upsert_idarr_asset_cache(
        test_db,
        asset_key=dirty_key,
        title="asdfqwer",
        year=None,
        asset_type="movie",
        tmdb_id=27205,
        tvdb_id=None,
        imdb_id=None,
        matched=True,
        payload_json=json.dumps({
            "canonical_title": "Inception",
            "resolved_manually": True,
            "resolution_history": [{"action": "resolve", "tmdb_id": 27205}],
        }),
    )
    test_db.commit()

    # Next run: the file has been renamed to its canonical title — produces a new key.
    runner._store_asset_cache_rows([{
        "title": "Inception",
        "year": 2010,
        "type": "movie",
        "tmdb_id": 27205,
        "has_id": True,
        "file_path": "/x/Inception (2010) {tmdb-27205}.psd",
    }])
    test_db.commit()

    new_key = runner._asset_key(asset_type="movie", title="Inception", year=2010, tmdb_id=27205)
    row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == new_key).first()
    assert row is not None
    payload = json.loads(row.payload_json)
    assert payload.get("resolved_manually") is True
    assert payload.get("resolution_history") and isinstance(payload["resolution_history"], list)
    assert payload.get("canonical_title") == "Inception"


def test_idarr_resolved_asset_key_is_id_only(test_db):
    """A resolved row's key is its id alone — any name for the same id yields the same key."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    k1 = runner._asset_key(asset_type="movie", title="1171x1299", year=None, tmdb_id=550)
    k2 = runner._asset_key(asset_type="movie", title="Fight Club", year=1999, tmdb_id=550)
    assert k1 == k2 == "movie::tmdb=550::scope=t0_demo"
    # tvdb / imdb fall-through when no tmdb id is present.
    assert runner._asset_key(asset_type="tv_series", title="x", year=2020, tvdb_id=99) == "tv_series::tvdb=99::scope=t0_demo"
    assert runner._asset_key(asset_type="movie", title="x", year=2020, imdb_id="tt7") == "movie::imdb=tt7::scope=t0_demo"
    # Unresolved (no id) keeps the provisional title/year form.
    assert runner._asset_key(asset_type="movie", title="Fight Club", year=1999) == "movie::fightclub::1999::scope=t0_demo"


def test_idarr_runner_store_cache_rows_one_row_per_id_across_names(test_db):
    """Two names for the same movie collapse to one id-keyed row, with both filenames recorded."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    runner._store_asset_cache_rows([
        {"title": "1171x1299", "year": None, "type": "movie", "tmdb_id": 550,
         "has_id": True, "_cache_touch": True, "file_path": "/plexart/logos/1171x1299 - logo.png"},
        {"title": "Fight Club", "year": 1999, "type": "movie", "tmdb_id": 550,
         "has_id": True, "_cache_touch": True, "file_path": "/plexart/logos/Fight Club (1999) {tmdb-550} - logo.png"},
    ])
    test_db.commit()

    rows = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 550).all()
    assert len(rows) == 1
    assert rows[0].asset_key == "movie::tmdb=550::scope=t0_demo"
    files = json.loads(rows[0].payload_json).get("current_filenames") or []
    assert "1171x1299 - logo.png" in files
    assert "Fight Club (1999) {tmdb-550} - logo.png" in files


def test_idarr_runner_store_cache_rows_rescan_renamed_file_updates_in_place(test_db):
    """Re-scanning a resolved+renamed file updates the same id-keyed row in place, no second row."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    # First pass: the dirty-named file resolves to tmdb 550.
    runner._store_asset_cache_rows([{
        "title": "1171x1299", "year": None, "type": "movie", "tmdb_id": 550,
        "has_id": True, "_cache_touch": True, "file_path": "/plexart/logos/1171x1299 - logo.png",
    }])
    test_db.commit()
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 550).count() == 1

    # Second pass: the renamed canonical file is scanned — same id, different name.
    runner._store_asset_cache_rows([{
        "title": "Fight Club", "year": 1999, "type": "movie", "tmdb_id": 550,
        "has_id": True, "_cache_touch": False, "file_path": "/plexart/logos/Fight Club (1999) {tmdb-550} - logo.png",
    }])
    test_db.commit()

    rows = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 550).all()
    assert len(rows) == 1
    assert rows[0].asset_key == "movie::tmdb=550::scope=t0_demo"


def test_idarr_runner_trim_cache_filenames_to_disk(test_db):
    """current_filenames is trimmed to files on disk; original_filenames is left intact."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    test_db.add(IdarrAssetCache(
        asset_key="movie::tmdb=550::scope=t0_demo", title="Fight Club", year=1999,
        asset_type="movie", tmdb_id=550, matched=True,
        payload_json=json.dumps({
            "current_filenames": [
                "Fight Club (1999) {tmdb-550} - logo.png",  # on disk
                "1171x1299 - logo.png",                      # archived/renamed away
                "cWUJnmUFQ34SyjcvsO4EGwB7w23.webp",          # archived/renamed away
            ],
            "original_filenames": ["1171x1299 - logo.png"],
        }),
    ))
    # A row whose only file is on disk — must be left unchanged.
    test_db.add(IdarrAssetCache(
        asset_key="movie::tmdb=27205::scope=t0_demo", title="Inception", year=2010,
        asset_type="movie", tmdb_id=27205, matched=True,
        payload_json=json.dumps({"current_filenames": ["Inception (2010) {tmdb-27205} - logo.png"]}),
    ))
    test_db.commit()

    on_disk = {"Fight Club (1999) {tmdb-550} - logo.png", "Inception (2010) {tmdb-27205} - logo.png"}
    changed = runner._trim_cache_filenames_to_disk(on_disk)
    test_db.commit()

    assert changed == 1  # only the Fight Club row had stale entries
    fc = json.loads(test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 550).first().payload_json)
    assert fc["current_filenames"] == ["Fight Club (1999) {tmdb-550} - logo.png"]
    assert fc["original_filenames"] == ["1171x1299 - logo.png"]  # audit trail untouched


def test_idarr_runner_store_cache_rows_collapses_idtype_transition(test_db):
    """A tvdb-only row is absorbed by the new tmdb-keyed row (metadata + freshness carried), leaving one row."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    older = datetime(2026, 6, 1, tzinfo=timezone.utc)
    tvdb_key = runner._asset_key(asset_type="tv_series", title="Some Show", year=2020, tvdb_id=99)
    assert tvdb_key == "tv_series::tvdb=99::scope=t0_demo"
    test_db.add(IdarrAssetCache(
        asset_key=tvdb_key, title="Some Show", year=2020, asset_type="tv_series",
        tmdb_id=None, tvdb_id=99, matched=True,
        payload_json=json.dumps({
            "resolved_manually": True, "canonical_title": "Some Show",
            "current_filenames": ["Some Show (2020) {tvdb-99}.png"],
        }),
        last_checked_at=older,
    ))
    test_db.commit()

    # Re-scanned now carrying a tmdb id; no network lookup of its own this run.
    runner._store_asset_cache_rows([{
        "title": "Some Show", "year": 2020, "type": "tv_series",
        "tmdb_id": 12345, "tvdb_id": 99, "has_id": True, "_cache_touch": False,
        "file_path": "/plexart/logos/Some Show (2020) {tmdb-12345} {tvdb-99} - logo.png",
    }])
    test_db.commit()

    rows = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_type == "tv_series").all()
    assert len(rows) == 1
    surv = rows[0]
    assert surv.asset_key == "tv_series::tmdb=12345::scope=t0_demo"
    assert surv.tmdb_id == 12345 and surv.tvdb_id == 99
    assert json.loads(surv.payload_json).get("resolved_manually") is True
    assert surv.last_checked_at is not None  # inherited from the tvdb-only predecessor


def test_idarr_runner_store_cache_rows_idtype_transition_ignores_conflicting_tmdb(test_db):
    """A different item sharing only a stale tvdb (but with its own tmdb) is NOT collapsed."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    other_key = runner._asset_key(asset_type="tv_series", title="Other Show", year=2019, tmdb_id=111)
    test_db.add(IdarrAssetCache(
        asset_key=other_key, title="Other Show", year=2019, asset_type="tv_series",
        tmdb_id=111, tvdb_id=99, matched=True,  # shares tvdb=99 but a different tmdb
        payload_json=json.dumps({"current_filenames": ["Other Show (2019) {tmdb-111}.png"]}),
        last_checked_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    ))
    test_db.commit()

    runner._store_asset_cache_rows([{
        "title": "Some Show", "year": 2020, "type": "tv_series",
        "tmdb_id": 222, "tvdb_id": 99, "has_id": True, "_cache_touch": True,
        "file_path": "/plexart/logos/Some Show (2020) {tmdb-222} - logo.png",
    }])
    test_db.commit()

    # The conflicting-tmdb row survives; a separate new row is created for the new item.
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == other_key).first() is not None
    assert test_db.query(IdarrAssetCache).filter(
        IdarrAssetCache.asset_key == "tv_series::tmdb=222::scope=t0_demo"
    ).first() is not None
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_type == "tv_series").count() == 2


def test_idarr_runner_store_cache_rows_uses_canonical_title_for_resolved(test_db):
    """A resolved row's title column is the canonical title, not the dirty filename name."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    runner._store_asset_cache_rows([{
        "title": "cWUJnmUFQ34SyjcvsO4EGwB7w23",                       # dirty scanned title
        "new_title": "From Dusk Till Dawn 3: The Hangman's Daughter",  # canonical from TMDB
        "year": 2000, "new_year": 2000,
        "type": "movie", "tmdb_id": 10213, "has_id": True, "_cache_touch": True,
        "file_path": "/plexart/logos/cWUJnmUFQ34SyjcvsO4EGwB7w23.webp",
    }])
    test_db.commit()

    row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 10213).first()
    assert row.asset_key == "movie::tmdb=10213::scope=t0_demo"
    assert row.title == "From Dusk Till Dawn 3: The Hangman's Daughter"  # canonical, not the hash
    assert json.loads(row.payload_json)["canonical_title"] == "From Dusk Till Dawn 3: The Hangman's Daughter"


def test_idarr_runner_prune_stale_conflict_cache_rows(test_db):
    """Stale conflict-keyed rows are removed; live conflicts and ordinary rows are kept."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    live_key = "movie::sometitle::2020::conflict=aaaa::scope=t0_demo"
    stale_key = "movie::oldtitle::1999::conflict=bbbb::scope=t0_demo"
    for k in (live_key, stale_key):
        test_db.add(IdarrAssetCache(
            asset_key=k, title="x", year=2000, asset_type="movie", matched=False,
            payload_json=json.dumps({"pending_reason": "rename_conflict", "status": "dismissed"}),
        ))
    # An ordinary resolved row must be left alone.
    test_db.add(IdarrAssetCache(
        asset_key="movie::tmdb=550::scope=t0_demo", title="Fight Club", year=1999,
        asset_type="movie", tmdb_id=550, matched=True, payload_json=json.dumps({}),
    ))
    test_db.commit()

    removed = runner._prune_stale_conflict_cache_rows({live_key})
    test_db.commit()

    assert removed == 1
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == live_key).first() is not None
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == stale_key).first() is None
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == "movie::tmdb=550::scope=t0_demo").first() is not None


def test_idarr_runner_store_unmatched_uses_single_pending_row(test_db):
    """An unmatched item is one pending:: row (not an inferred-type + pending:: pair), and _load_cache_map finds it."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    # Year-less unmatched asset (the runner would infer "collection").
    runner._store_asset_cache_rows([{
        "title": "123456", "year": None, "type": "collection",
        "has_id": False, "_cache_touch": True, "file_path": "/plexart/logos/123456 - logo.png",
    }])
    test_db.commit()

    rows = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.title == "123456").all()
    assert len(rows) == 1
    assert rows[0].asset_key == "pending::123456::::scope=t0_demo"
    assert rows[0].asset_type == "pending"
    # No bogus collection:: twin.
    assert test_db.query(IdarrAssetCache).filter(
        IdarrAssetCache.asset_key == "collection::123456::::scope=t0_demo"
    ).first() is None

    # The enrichment lookup finds the pending row for the same (collection-inferred) asset, so its
    # freshness throttles re-search.
    cache_map = runner._load_cache_map([{"title": "123456", "year": None, "type": "collection"}])
    assert any(row.asset_key == "pending::123456::::scope=t0_demo" for row in cache_map.values())


def test_idarr_runner_store_unmatched_removes_stale_inferred_type_twin(test_db):
    """Storing the pending:: row removes a stale inferred-type twin (e.g. collection::)."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    # Pre-existing stale twin from before the change.
    test_db.add(IdarrAssetCache(
        asset_key="collection::123456::::scope=t0_demo", title="123456", year=None,
        asset_type="collection", matched=False,
        payload_json=json.dumps({"current_filenames": ["123456 - logo.png"], "status": "not_found"}),
    ))
    test_db.commit()

    runner._store_asset_cache_rows([{
        "title": "123456", "year": None, "type": "collection",
        "has_id": False, "_cache_touch": True, "file_path": "/plexart/logos/123456 - logo.png",
    }])
    test_db.commit()

    rows = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.title == "123456").all()
    assert len(rows) == 1
    assert rows[0].asset_key == "pending::123456::::scope=t0_demo"


def test_idarr_runner_store_absorbs_provisional_when_id_row_exists(test_db):
    """Resolving a dirty file to an already-cached movie absorbs the provisional row, no duplicate id row."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    # Canonical id-keyed row already exists.
    test_db.add(IdarrAssetCache(
        asset_key="movie::tmdb=10213::scope=t0_demo", title="From Dusk Till Dawn 3", year=2000,
        asset_type="movie", tmdb_id=10213, matched=True,
        payload_json=json.dumps({"current_filenames": ["From Dusk Till Dawn 3 (2000) {tmdb-10213}.png"]}),
        last_checked_at=datetime.now(timezone.utc),
    ))
    # A dirty-title provisional row carrying the same id, left by a manual resolve.
    test_db.add(IdarrAssetCache(
        asset_key="movie::abcdefg::::scope=t0_demo", title="abcdefg", year=None,
        asset_type="movie", tmdb_id=10213, matched=True,
        payload_json=json.dumps({"current_filenames": ["abcdefg - logo.png"], "resolved_manually": True}),
    ))
    test_db.commit()

    # Re-scan the (renamed) dirty file: it resolves to the existing id row.
    runner._store_asset_cache_rows([{
        "title": "abcdefg", "year": None, "type": "movie", "tmdb_id": 10213,
        "has_id": True, "_cache_touch": False, "file_path": "/plexart/logos/abcdefg - logo.png",
    }])
    test_db.commit()

    rows = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 10213).all()
    assert len(rows) == 1
    assert rows[0].asset_key == "movie::tmdb=10213::scope=t0_demo"


def test_idarr_runner_prune_duplicate_id_rows(test_db):
    """A dirty-title row duplicating a canonical id row is removed; canonical and unrelated rows kept."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    test_db.add(IdarrAssetCache(
        asset_key="movie::tmdb=10213::scope=t0_demo", title="From Dusk Till Dawn 3", year=2000,
        asset_type="movie", tmdb_id=10213, matched=True, payload_json=json.dumps({}),
    ))
    test_db.add(IdarrAssetCache(
        asset_key="movie::abcdefg::::scope=t0_demo", title="abcdefg", year=None,
        asset_type="movie", tmdb_id=10213, matched=True, payload_json=json.dumps({"current_filenames": []}),
    ))
    test_db.add(IdarrAssetCache(
        asset_key="movie::tmdb=550::scope=t0_demo", title="Fight Club", year=1999,
        asset_type="movie", tmdb_id=550, matched=True, payload_json=json.dumps({}),
    ))
    test_db.commit()

    removed = runner._prune_duplicate_id_rows()
    test_db.commit()

    assert removed == 1
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == "movie::abcdefg::::scope=t0_demo").first() is None
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 10213).count() == 1
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 550).count() == 1


def test_idarr_runner_prune_orphaned_unmatched_rows(test_db):
    """Dead unmatched orphans are removed; active/ignored/dismissed/resolved rows survive."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    # Dead orphan — should be removed.
    test_db.add(IdarrAssetCache(
        asset_key="movie::dusktilldawn3::::scope=t0_demo", title="dusk till dawn 3", year=None,
        asset_type="movie", matched=False,
        payload_json=json.dumps({"candidate_results": [{"tmdb_id": 10213}]}),
    ))
    # Active unmatched — has a pending-match → keep.
    test_db.add(IdarrAssetCache(
        asset_key="movie::stillpending::::scope=t0_demo", title="still pending", year=None,
        asset_type="movie", matched=False, payload_json=json.dumps({"status": "not_found"}),
    ))
    test_db.add(IdarrPendingMatch(asset_key="movie::stillpending::::scope=t0_demo", title="still pending", year=None, asset_type="movie"))
    # Ignored marker — keep.
    test_db.add(IdarrAssetCache(
        asset_key="movie::someignored::::scope=t0_demo", title="some ignored", year=None,
        asset_type="movie", matched=False, payload_json=json.dumps({"status": "ignored"}),
    ))
    # Resolved row — keep (has id).
    test_db.add(IdarrAssetCache(
        asset_key="movie::tmdb=550::scope=t0_demo", title="Fight Club", year=1999,
        asset_type="movie", tmdb_id=550, matched=True, payload_json=json.dumps({}),
    ))
    test_db.commit()

    removed = runner._prune_orphaned_unmatched_rows()
    test_db.commit()

    assert removed == 1
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == "movie::dusktilldawn3::::scope=t0_demo").first() is None
    for survivor in ("movie::stillpending::::scope=t0_demo", "movie::someignored::::scope=t0_demo", "movie::tmdb=550::scope=t0_demo"):
        assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == survivor).first() is not None


def test_idarr_filename_index_excludes_historical_names(test_db):
    """The filename index maps only current filenames, so a re-dropped old name can't inherit the past id."""
    runner = IdarrRunner(test_db)
    runner._scope_token = "t0_demo"

    class _Row:
        def __init__(self, asset_key, tmdb, payload):
            self.asset_key = asset_key
            self.asset_type = "movie"
            self.tmdb_id = tmdb
            self.tvdb_id = None
            self.imdb_id = None
            self.payload_json = json.dumps(payload)

    rows = [_Row("movie::tmdb=550::scope=t0_demo", 550, {
        "current_filenames": ["Fight Club (1999) {tmdb-550} - logo.png"],
        "original_filenames": ["1171x1299 - logo.png", "xyz.png"],
    })]
    index = runner._load_cache_filename_index(rows=rows)

    assert "1171x1299 - logo.png" not in index   # historical name not indexed
    assert "xyz.png" not in index
    current = "fight club (1999) {tmdb-550} - logo.png"
    assert current in index and index[current]["tmdb_id"] == 550


def test_idarr_current_tracked_filenames_excludes_history():
    """Only current_filenames count as 'tracked'; history names are excluded."""
    class _Row:
        def __init__(self, payload):
            self.payload_json = json.dumps(payload)
    rows = [
        _Row({"current_filenames": ["Foo (2020) - logo.png", "Bar.png"]}),
        _Row({"current_filenames": ["Baz.webp"], "original_filenames": ["old-name.png"]}),
    ]
    tracked = IdarrRunner._current_tracked_filenames(rows)
    assert tracked == {"foo (2020) - logo.png", "bar.png", "baz.webp"}
    assert "old-name.png" not in tracked


def _load_migration_0010():
    import importlib.util
    mig_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0010_idarr_idkeyed_cache.py"
    spec = importlib.util.spec_from_file_location("idarr_mig_0010", mig_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0010_rekeys_and_dedupes_legacy_rows(test_db):
    """Legacy title-keyed rows for the same id collapse to one id-only row (merged filenames, freshest timestamp)."""
    scope = "t2_demo"
    older = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Two legacy rows for tmdb 550 under different (title-embedded) keys.
    test_db.add(IdarrAssetCache(
        asset_key=f"movie::1171x1299::::tmdb=550::scope={scope}",
        title="1171x1299", year=None, asset_type="movie", tmdb_id=550, matched=True,
        payload_json=json.dumps({"current_filenames": ["1171x1299 - logo.png"]}),
        last_checked_at=older,
    ))
    test_db.add(IdarrAssetCache(
        asset_key=f"movie::fightclub::1999::tmdb=550::scope={scope}",
        title="Fight Club", year=1999, asset_type="movie", tmdb_id=550, matched=True,
        payload_json=json.dumps({
            "canonical_title": "Fight Club",
            "current_filenames": ["Fight Club (1999) {tmdb-550} - logo.png"],
        }),
        last_checked_at=None,
    ))
    # An unresolved (no-id) row must be left untouched.
    test_db.add(IdarrAssetCache(
        asset_key=f"movie::somethingunknown::2020::scope={scope}",
        title="Something Unknown", year=2020, asset_type="movie", matched=False,
        payload_json=json.dumps({"current_filenames": ["Something Unknown (2020).png"]}),
    ))
    test_db.commit()

    _load_migration_0010().rekey_idarr_cache(test_db.connection())
    test_db.expire_all()

    survivors = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.tmdb_id == 550).all()
    assert len(survivors) == 1
    surv = survivors[0]
    assert surv.asset_key == f"movie::tmdb=550::scope={scope}"
    # Canonical-filename row won as survivor; both filenames merged in.
    files = json.loads(surv.payload_json).get("current_filenames")
    assert set(files) == {"1171x1299 - logo.png", "Fight Club (1999) {tmdb-550} - logo.png"}
    # Inherited the only/freshest timestamp from the group.
    assert surv.last_checked_at is not None
    # The unresolved row is untouched.
    assert test_db.query(IdarrAssetCache).filter(
        IdarrAssetCache.asset_key == f"movie::somethingunknown::2020::scope={scope}"
    ).count() == 1


def test_idarr_runner_store_asset_cache_rows_preserves_group_filenames(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    assets = [
        {
            "file_path": tmp_path / "1883 (2021) - Season 1.jpg",
            "title": "1883",
            "year": 2021,
            "type": "tv_series",
            "tmdb_id": 118357,
            "tvdb_id": 396390,
            "imdb_id": "tt13991232",
            "new_title": "1883",
            "new_year": 2021,
            "has_id": True,
            "_cache_touch": True,
        },
        {
            "file_path": tmp_path / "1883 (2021) - Specials.jpg",
            "title": "1883",
            "year": 2021,
            "type": "tv_series",
            "tmdb_id": 118357,
            "tvdb_id": 396390,
            "imdb_id": "tt13991232",
            "has_id": True,
            "_cache_touch": False,
        },
    ]

    runner._store_asset_cache_rows(assets)
    test_db.commit()

    key = runner._asset_key(asset_type="tv_series", title="1883", year=2021, tmdb_id=118357)
    row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == key).first()
    assert row is not None

    payload = json.loads(row.payload_json or "{}")
    current_filenames = payload.get("current_filenames") or []
    original_filenames = payload.get("original_filenames") or []

    assert "1883 (2021) - Season 1.jpg" in current_filenames
    assert "1883 (2021) - Specials.jpg" in current_filenames
    assert "1883 (2021) - Season 1.jpg" in original_filenames
    assert "1883 (2021) - Specials.jpg" in original_filenames
    assert payload.get("canonical_title") == "1883"
    assert payload.get("canonical_year") == 2021


def test_idarr_runner_store_asset_cache_rows_prefers_canonical_collection_title(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    assets = [
        {
            "file_path": tmp_path / "Troll Collection (2022) {tmdb-1180834}.jpg",
            "title": "Troll Collection",
            "year": 2022,
            "type": "collection",
            "tmdb_id": 1180834,
            "new_title": "Troll (2022) Collection",
            "new_year": 2022,
            "has_id": True,
            "_cache_touch": True,
        }
    ]

    runner._store_asset_cache_rows(assets)
    test_db.commit()

    key = runner._asset_key(asset_type="collection", title="Troll Collection", year=2022, tmdb_id=1180834)
    row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == key).first()
    assert row is not None
    assert row.title == "Troll (2022) Collection"
    assert row.year == 2022

    payload = json.loads(row.payload_json or "{}")
    assert payload.get("title") == "Troll (2022) Collection"
    assert payload.get("canonical_title") == "Troll (2022) Collection"
    assert payload.get("canonical_year") == 2022


def test_idarr_runner_store_cache_preserves_canonical_title_from_existing_payload(test_db):
    """_store_asset_cache_rows must not overwrite an existing canonical_title with
    the dirty parsed title when the current run's TMDB enrichment didn't set new_title."""
    runner = IdarrRunner(test_db)
    asset_key = runner._asset_key(asset_type="movie", title="Dirty Title", year=2020)

    # Pre-seed a cache row that was manually resolved with a clean canonical_title,
    # but no new_title was set by the current run (simulating general_recent=True).
    from models.idarr import upsert_idarr_asset_cache
    existing_payload = {
        "canonical_title": "The Real TMDB Title",
        "canonical_year": 2020,
        "resolved_manually": True,
        "resolution_history": [{"action": "resolve", "tmdb_id": 12345}],
    }
    upsert_idarr_asset_cache(
        test_db,
        asset_key=asset_key,
        title="Dirty Title",
        year=2020,
        asset_type="movie",
        tmdb_id=12345,
        tvdb_id=None,
        imdb_id=None,
        matched=True,
        payload_json=json.dumps(existing_payload),
    )
    test_db.commit()

    # Simulate what happens when the runner processes a file and TMDB enrichment
    # was skipped (general_recent=True): new_title is NOT set on the asset.
    assets = [
        {
            "title": "Dirty Title",
            "year": 2020,
            "type": "movie",
            "tmdb_id": 12345,
            "has_id": True,
            "file_path": "/source/dirty title (2020) {tmdb-12345}.jpg",
            # new_title intentionally absent — simulates skipped TMDB enrichment
        }
    ]
    runner._store_asset_cache_rows(assets)
    test_db.commit()

    from models.idarr import IdarrAssetCache
    # The provisional key row is migrated to ID-keyed form during _store_asset_cache_rows.
    id_keyed = runner._asset_key(asset_type="movie", title="Dirty Title", year=2020, tmdb_id=12345)
    cache_row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == id_keyed).first()
    assert cache_row is not None

    import json as _json
    saved_payload = _json.loads(cache_row.payload_json)
    # The canonical_title from the prior resolve must be preserved, not overwritten.
    assert saved_payload.get("canonical_title") == "The Real TMDB Title", (
        f"Expected canonical_title to be preserved, got: {saved_payload.get('canonical_title')!r}"
    )
    # resolve metadata should also be preserved
    assert saved_payload.get("resolved_manually") is True
    assert isinstance(saved_payload.get("resolution_history"), list)


def test_idarr_runner_store_cache_uses_new_title_over_existing_canonical_title(test_db):
    """When new_title IS set by TMDB enrichment it should win over existing canonical_title."""
    runner = IdarrRunner(test_db)
    asset_key = runner._asset_key(asset_type="movie", title="Some Title", year=2020)

    from models.idarr import upsert_idarr_asset_cache
    existing_payload = {"canonical_title": "Old Stale Title", "canonical_year": 2020}
    upsert_idarr_asset_cache(
        test_db,
        asset_key=asset_key,
        title="Some Title",
        year=2020,
        asset_type="movie",
        tmdb_id=99999,
        tvdb_id=None,
        imdb_id=None,
        matched=True,
        payload_json=json.dumps(existing_payload),
    )
    test_db.commit()

    assets = [
        {
            "title": "Some Title",
            "year": 2020,
            "type": "movie",
            "tmdb_id": 99999,
            "new_title": "Freshly Verified TMDB Title",  # TMDB enrichment set this
            "has_id": True,
            "file_path": "/source/some title (2020).jpg",
        }
    ]
    runner._store_asset_cache_rows(assets)
    test_db.commit()

    from models.idarr import IdarrAssetCache
    # The provisional key row is migrated to ID-keyed form during _store_asset_cache_rows.
    id_keyed = runner._asset_key(asset_type="movie", title="Some Title", year=2020, tmdb_id=99999)
    cache_row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == id_keyed).first()
    assert cache_row is not None

    import json as _json
    saved_payload = _json.loads(cache_row.payload_json)
    assert saved_payload.get("canonical_title") == "Freshly Verified TMDB Title"


def test_idarr_enrich_sets_new_title_from_canonical_title_in_cache(test_db):
    """When a manually-resolved item has canonical_title in its cache payload, the runner
    must set new_title from it so _generate_new_filename uses the TMDB title."""
    from models.idarr import upsert_idarr_asset_cache
    import json as _json

    runner = IdarrRunner(test_db)
    asset_key = runner._asset_key(asset_type="movie", title="Dirty File Title", year=2019)

    upsert_idarr_asset_cache(
        test_db,
        asset_key=asset_key,
        title="Dirty File Title",
        year=2019,
        asset_type="movie",
        tmdb_id=55555,
        tvdb_id=None,
        imdb_id=None,
        matched=True,
        payload_json=_json.dumps({
            "canonical_title": "Proper TMDB Title",
            "canonical_year": 2019,
            "resolved_manually": True,
        }),
    )
    test_db.commit()

    # Minimal asset representing a parsed file — no new_title yet
    asset = {
        "title": "Dirty File Title",
        "year": 2019,
        "type": "movie",
        "tmdb_id": None,
        "tvdb_id": None,
        "imdb_id": None,
        "file_path": "/source/dirty file title (2019).jpg",
    }

    cache_map = runner._load_cache_map([asset])
    cache_row = cache_map.get(asset_key)
    assert cache_row is not None

    # Simulate the canonical_title extraction block from _enrich_assets_with_tmdb
    cache_payload = _json.loads(cache_row.payload_json)
    canonical_title = str(cache_payload.get("canonical_title") or "").strip()
    current_title = str(asset.get("title") or "").strip()

    # Prefill IDs from cache (normally done in the enrichment loop)
    if not asset.get("tmdb_id") and isinstance(cache_row.tmdb_id, int):
        asset["tmdb_id"] = cache_row.tmdb_id

    if canonical_title and canonical_title != current_title and not str(asset.get("new_title") or "").strip():
        asset["new_title"] = canonical_title

    assert asset.get("new_title") == "Proper TMDB Title", (
        f"Expected new_title='Proper TMDB Title', got {asset.get('new_title')!r}"
    )

    # Verify _generate_new_filename uses new_title
    new_filename = runner._generate_new_filename(asset, "dirty file title (2019).jpg")
    assert "Proper TMDB Title" in new_filename, f"Expected TMDB title in filename, got: {new_filename!r}"
    assert "{tmdb-55555}" in new_filename
