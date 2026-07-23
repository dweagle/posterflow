import json
import time
from datetime import datetime, timezone
from pathlib import Path

from models.idarr import IdarrAssetCache, IdarrPendingMatch, IdarrRun, prune_idarr_run_history, compact_idarr_run_details_history, upsert_idarr_asset_cache
from models.setting import Setting
from services.idarr_runner import IdarrRunner


def test_idarr_config_get_returns_defaults_when_missing(client):
    response = client.get("/api/idarr/")
    assert response.status_code == 200
    data = response.json()
    assert "incoming_dir" not in data
    assert data["auto_rename_quick_add"] is True
    assert data["frequency_days"] == 30
    assert data["tvdb_frequency"] == 7


def test_idarr_config_save_and_get_round_trip(client, test_db):
    payload = {
        "sync_targets": [
            {
                "label": "Drive 1",
                "personal_drive_id": "folder-123",
                "source_dir": "/tmp/output",
            }
        ],
        "tmdb_api_key": "tmdb-key",
        "auto_rename_quick_add": False,
        "remove_non_image_files": True,
        "show_unmatched": True,
        "pending_matches": True,
        "skip_collections": False,
        "limit": 50,
        "frequency_days": 15,
        "tvdb_frequency": 5,
    }

    save_response = client.post("/api/idarr/", json=payload)
    assert save_response.status_code == 200
    assert save_response.json()["success"] is True

    persisted = test_db.query(Setting).filter(Setting.key == "maker_tools_idarr_config").first()
    assert persisted is not None

    get_response = client.get("/api/idarr/")
    assert get_response.status_code == 200
    data = get_response.json()
    assert "incoming_dir" not in data
    assert len(data["sync_targets"]) == 1
    assert data["sync_targets"][0]["personal_drive_id"] == "folder-123"
    assert data["sync_targets"][0]["source_dir"] == "/tmp/output"
    assert isinstance(data["sync_targets"][0].get("scope_token"), str)
    assert data["sync_targets"][0]["scope_token"].strip()
    # tmdb_api_key is promoted to the global setting on save and stripped from the config JSON
    assert data["tmdb_api_key"] == ""
    global_key = test_db.query(Setting).filter(Setting.key == "tmdb_api_key").first()
    assert global_key is not None
    assert global_key.value == "tmdb-key"
    assert data["auto_rename_quick_add"] is False
    assert data["frequency_days"] == 15
    assert data["tvdb_frequency"] == 5


def test_idarr_config_path_change_preserves_scope_token(client, test_db):
    initial_payload = {
        "sync_targets": [
            {
                "label": "Drive 1",
                "personal_drive_id": "folder-123",
                "source_dir": "/tmp/output-a",
            }
        ],
        "tmdb_api_key": "tmdb-key",
    }

    first_save = client.post("/api/idarr/", json=initial_payload)
    assert first_save.status_code == 200

    first_get = client.get("/api/idarr/")
    assert first_get.status_code == 200
    first_scope_token = str(first_get.json()["sync_targets"][0].get("scope_token") or "")
    assert first_scope_token

    updated_payload = {
        "sync_targets": [
            {
                "label": "Drive 1",
                "personal_drive_id": "folder-123",
                "source_dir": "/tmp/output-b",
            }
        ],
        "tmdb_api_key": "tmdb-key",
    }

    second_save = client.post("/api/idarr/", json=updated_payload)
    assert second_save.status_code == 200

    second_get = client.get("/api/idarr/")
    assert second_get.status_code == 200
    second_target = second_get.json()["sync_targets"][0]
    assert second_target["source_dir"] == "/tmp/output-b"
    assert str(second_target.get("scope_token") or "") == first_scope_token


def test_prune_idarr_run_history_keeps_latest_per_scope(test_db):
    scope_token = "scope-test-1"
    for index in range(6):
        test_db.add(
            IdarrRun(
                job_id=1000 + index,
                success=True,
                source_dir="/tmp/idarr-scope-a",
                scope_token=scope_token,
                unmatched_count=0,
            )
        )
    test_db.commit()

    deleted = prune_idarr_run_history(test_db, keep_latest=3, scope_token=scope_token)
    test_db.commit()

    assert deleted == 3
    remaining = (
        test_db.query(IdarrRun)
        .filter(IdarrRun.scope_token == scope_token)
        .order_by(IdarrRun.id.desc())
        .all()
    )
    assert len(remaining) == 3
    assert [row.job_id for row in remaining] == [1005, 1004, 1003]


def test_prune_idarr_run_history_handles_legacy_blank_scope_bucket(test_db):
    for index in range(5):
        test_db.add(
            IdarrRun(
                job_id=2000 + index,
                success=True,
                source_dir="/tmp/idarr-legacy",
                scope_token=None,
                unmatched_count=0,
            )
        )
    test_db.commit()

    deleted = prune_idarr_run_history(test_db, keep_latest=2, scope_token=None)
    test_db.commit()

    assert deleted == 3
    remaining = (
        test_db.query(IdarrRun)
        .filter(IdarrRun.scope_token.is_(None))
        .order_by(IdarrRun.id.desc())
        .all()
    )
    assert len(remaining) == 2
    assert [row.job_id for row in remaining] == [2004, 2003]


def test_compact_idarr_run_details_history_keeps_latest_full_payload(test_db):
    scope_token = "scope-compact-1"

    def _details(operation_count: int) -> str:
        rows = [
            {
                "operation": "rename",
                "status": "success",
                "reason": "in_place_rename",
                "from_path": f"/tmp/from_{index}.jpg",
                "to_path": f"/tmp/to_{index}.jpg",
            }
            for index in range(operation_count)
        ]
        return json.dumps({"file_operations": rows, "unmatched_items": []})

    for index in range(3):
        test_db.add(
            IdarrRun(
                job_id=3000 + index,
                success=True,
                source_dir="/tmp/idarr-scope-b",
                scope_token=scope_token,
                details_json=_details(10 + index),
                unmatched_count=0,
            )
        )
    test_db.commit()

    compacted = compact_idarr_run_details_history(test_db, keep_full_latest=1, scope_token=scope_token)
    test_db.commit()

    assert compacted == 2

    rows = (
        test_db.query(IdarrRun)
        .filter(IdarrRun.scope_token == scope_token)
        .order_by(IdarrRun.id.desc())
        .all()
    )
    assert len(rows) == 3

    latest_payload = json.loads(rows[0].details_json or "{}")
    assert isinstance(latest_payload.get("file_operations"), list)
    assert "file_operation_summary" not in latest_payload

    older_payload = json.loads(rows[1].details_json or "{}")
    assert "file_operations" not in older_payload
    assert older_payload.get("file_operations_compacted") is True
    summary = older_payload.get("file_operation_summary")
    assert isinstance(summary, dict)
    assert int(summary.get("total_operations") or 0) > 0
    assert isinstance(older_payload.get("file_operations_sample"), list)


def test_idarr_upload_files_to_selected_sync_target(client, test_db, tmp_path):
    source_dir = tmp_path / "idarr-source"
    payload = {
        "sync_targets": [
            {
                "label": "Drive 1",
                "personal_drive_id": "folder-123",
                "source_dir": str(source_dir),
            }
        ],
        "tmdb_api_key": "tmdb-key",
    }

    upsert_value = Setting(key="maker_tools_idarr_config", value=json.dumps(payload))
    test_db.add(upsert_value)
    test_db.commit()

    response = client.post(
        "/api/idarr/upload",
        data={"sync_target_index": "0"},
        files=[("files", ("test.jpg", b"sample-bytes", "image/jpeg"))],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["uploaded_count"] == 1
    assert (source_dir / data["uploaded"][0]).exists()


def test_idarr_upload_rejects_when_no_valid_images(client, test_db, tmp_path):
    source_dir = tmp_path / "idarr-source"
    payload = {
        "sync_targets": [
            {
                "label": "Drive 1",
                "personal_drive_id": "folder-123",
                "source_dir": str(source_dir),
            }
        ],
        "tmdb_api_key": "tmdb-key",
    }

    test_db.add(Setting(key="maker_tools_idarr_config", value=json.dumps(payload)))
    test_db.commit()

    response = client.post(
        "/api/idarr/upload",
        data={"sync_target_index": "0"},
        files=[("files", ("notes.txt", b"not-image", "text/plain"))],
    )

    assert response.status_code == 400
    assert "No valid image files" in response.json()["detail"]


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


def test_import_ignored_titles_bulk_resolves_exact_title_and_year(client, test_db):
    test_db.add_all([
        IdarrAssetCache(
            asset_key="collection::modernsitcomcollection::",
            title="Modern Sitcom Collection",
            year=None,
            asset_type="collection",
            matched=False,
        ),
        IdarrAssetCache(
            asset_key="movie::bloodandorchids::1986",
            title="Blood & Orchids",
            year=1986,
            asset_type="movie",
            matched=False,
        ),
    ])
    test_db.commit()

    response = client.post(
        "/api/idarr/ignored-titles/import",
        json={"titles": ["Modern Sitcom Collection", "Blood & Orchids (1986)"], "sync_target_index": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["added"] == 2
    assert data["total"] == 2

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    keys = {item["asset_key"] for item in ignored}
    assert "collection::modernsitcomcollection::" in keys
    assert "movie::bloodandorchids::1986" in keys


def test_replace_ignored_titles_bulk_overwrites_existing_entries(client, test_db):
    test_db.add(
        Setting(
            key="maker_tools_idarr_ignored_titles",
            value=json.dumps([
                {
                    "asset_key": "collection::legacy::",
                    "title": "Legacy Collection",
                    "year": None,
                    "type": "collection",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]),
        )
    )
    test_db.commit()

    response = client.post(
        "/api/idarr/ignored-titles/replace",
        json={"titles": ["Mini-Series Collection"], "sync_target_index": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["total"] == 1

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["title"] == "Mini-Series Collection"
    assert ignored[0]["type"] == "collection"


def test_import_ignored_titles_bulk_unresolved_year_creates_single_entry(client):
    response = client.post(
        "/api/idarr/ignored-titles/import",
        json={"titles": ["Tom and Jerry (1940)"], "sync_target_index": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["added"] == 1

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["title"] == "Tom and Jerry"
    assert ignored[0]["year"] == 1940
    assert ignored[0]["type"] == "movie"


def test_idarr_runner_expand_asset_key_aliases_covers_movie_and_show_variants():
    movie_key = "movie::tomandjerry::1940"
    expanded_movie = IdarrRunner._expand_asset_key_aliases(movie_key)
    assert "movie::tomandjerry::1940" in expanded_movie
    assert "tv_series::tomandjerry::1940" in expanded_movie

    show_key = "tv_series::tomandjerry::1940"
    expanded_show = IdarrRunner._expand_asset_key_aliases(show_key)
    assert "tv_series::tomandjerry::1940" in expanded_show
    assert "movie::tomandjerry::1940" in expanded_show


def test_idarr_runner_expand_asset_key_aliases_pending_covers_all_concrete_types():
    """An ignore entry saved from the pending list is keyed ``pending::`` (type unknown).
    It must expand to every concrete type so the ignore still matches once the same item
    is typed concretely on a later run — otherwise ignored items reappear as unmatched."""
    pending_key = "pending::astarwarsstorycollection::::scope=t2_abc"
    expanded = IdarrRunner._expand_asset_key_aliases(pending_key)
    assert "collection::astarwarsstorycollection::::scope=t2_abc" in expanded
    assert "movie::astarwarsstorycollection::::scope=t2_abc" in expanded
    assert "tv_series::astarwarsstorycollection::::scope=t2_abc" in expanded
    assert "pending::astarwarsstorycollection::::scope=t2_abc" in expanded


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


def test_enrich_grouped_assets_reuses_single_tmdb_search(test_db, monkeypatch):
    runner = IdarrRunner(test_db)

    search_calls = 0
    external_calls = 0

    def fake_tmdb_search(*, api_key, title, asset_type, year, tmdb_id=None, tvdb_id=None, imdb_id=None):
        nonlocal search_calls
        search_calls += 1
        return {
            "id": 118357,
            "name": "1883",
            "first_air_date": "2021-12-19",
        }, "exact"

    def fake_tmdb_external_ids(*, api_key, tmdb_id, asset_type):
        nonlocal external_calls
        external_calls += 1
        return {
            "tvdb_id": 396390,
            "imdb_id": "tt13991232",
        }

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(fake_tmdb_external_ids))
    monkeypatch.setattr(
        IdarrRunner,
        "_tmdb_verify_id",
        staticmethod(
            lambda *, api_key, tmdb_id, asset_type, title, year=None: (
                {"id": tmdb_id, "name": title, "first_air_date": "2021-12-19"},
                "tv_series",
                None,
            )
        ),
    )

    assets = [
        {
            "file_path": Path("/tmp/1883 (2021) - Season 1.jpg"),
            "title": "1883",
            "year": 2021,
            "type": "tv_series",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        },
        {
            "file_path": Path("/tmp/1883 (2021) - Specials.jpg"),
            "title": "1883",
            "year": 2021,
            "type": "tv_series",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        },
    ]

    stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert search_calls == 1
    assert external_calls == 1
    assert stats["tmdb_search_api_calls"] == 1
    assert stats["tmdb_external_id_api_calls"] == 1

    for asset in assets:
        assert asset["tmdb_id"] == 118357
        assert asset["tvdb_id"] == 396390
        assert asset["imdb_id"] == "tt13991232"
        assert asset["has_id"] is True


def test_idarr_pending_matches_returns_empty_list(client):
    client.post(
        "/api/idarr/",
        json={
            "sync_targets": [{"label": "Drive 1", "personal_drive_id": "folder-123", "source_dir": ""}],
            "tmdb_api_key": "",
        },
    )
    response = client.get("/api/idarr/pending-matches", params={"sync_target_index": 0})
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []


def test_idarr_pending_matches_includes_pending_reason_from_cache_payload(client, test_db):
    client.post(
        "/api/idarr/",
        json={
            "sync_targets": [{"label": "Drive 1", "personal_drive_id": "folder-123", "source_dir": ""}],
            "tmdb_api_key": "",
        },
    )

    pending_key = "movie::zacksnydersjusticeleague::2021"
    test_db.add(
        IdarrPendingMatch(
            asset_key=pending_key,
            title="Zack Snyder's Justice League",
            year=2021,
            asset_type="movie",
        )
    )
    test_db.add(
        IdarrAssetCache(
            asset_key=pending_key,
            title="Zack Snyder's Justice League",
            year=2021,
            asset_type="movie",
            tmdb_id=791373,
            imdb_id="tt12361974",
            matched=True,
            payload_json=json.dumps({"pending_reason": "rename_conflict"}),
        )
    )
    test_db.commit()

    response = client.get("/api/idarr/pending-matches", params={"sync_target_index": 0})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["asset_key"] == pending_key
    assert items[0].get("pending_reason") == "rename_conflict"


def test_idarr_pending_matches_builds_preview_url_from_source_dir_fallback(client, test_db, tmp_path):
    source_dir = tmp_path / "idarr-source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_file = source_dir / "The Outlaws (2017).jpg"
    source_file.write_bytes(b"image")

    client.post(
        "/api/idarr/",
        json={
            "sync_targets": [{"label": "Drive 1", "personal_drive_id": "folder-123", "source_dir": str(source_dir)}],
            "tmdb_api_key": "",
        },
    )

    config_response = client.get("/api/idarr/")
    assert config_response.status_code == 200
    scope_token = str(config_response.json()["sync_targets"][0].get("scope_token") or "").strip()
    assert scope_token

    pending_key = f"movie::theoutlaws::2017::scope={scope_token}"
    test_db.add(
        IdarrPendingMatch(
            asset_key=pending_key,
            title="The Outlaws",
            year=2017,
            asset_type="movie",
        )
    )
    test_db.add(
        IdarrAssetCache(
            asset_key=pending_key,
            title="The Outlaws",
            year=2017,
            asset_type="movie",
            payload_json=json.dumps({}),
        )
    )
    test_db.commit()

    response = client.get("/api/idarr/pending-matches", params={"sync_target_index": 0})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert isinstance(items[0].get("preview_url"), str)
    assert "/api/idarr/pending-matches/source-image?path=" in str(items[0].get("preview_url"))


def test_idarr_pending_matches_clear_all_endpoint(client, test_db):
    test_db.add_all([
        IdarrPendingMatch(asset_key="movie::a::2020", title="A", year=2020, asset_type="movie"),
        IdarrPendingMatch(asset_key="movie::b::2021", title="B", year=2021, asset_type="movie"),
    ])
    test_db.commit()

    response = client.post("/api/idarr/pending-matches/clear-all", params={"sync_target_index": 0})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["deleted"] == 2

    remaining = test_db.query(IdarrPendingMatch).count()
    assert remaining == 0


def test_idarr_resolve_pending_match_not_found_returns_404(client):
    response = client.post(
        "/api/idarr/pending-matches/resolve",
        json={
            "asset_key": "movie::missing::2024",
            "action": "resolve",
            "tmdb_id": 123,
            "sync_target_index": 0,
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Pending match not found"


def test_idarr_resolve_pending_match_requires_at_least_one_id(client, test_db):
    row = IdarrPendingMatch(
        asset_key="movie::sample::2020",
        title="Sample",
        year=2020,
        asset_type="movie",
    )
    test_db.add(row)
    test_db.commit()

    response = client.post(
        "/api/idarr/pending-matches/resolve",
        json={
            "asset_key": "movie::sample::2020",
            "action": "resolve",
            "sync_target_index": 0,
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Provide at least one ID to resolve"


def test_idarr_resolve_pending_match_success_updates_cache_and_removes_pending(client, test_db):
    pending = IdarrPendingMatch(
        asset_key="movie::inception::2010",
        title="Inception",
        year=2010,
        asset_type="movie",
    )
    test_db.add(pending)
    test_db.commit()

    response = client.post(
        "/api/idarr/pending-matches/resolve",
        json={
            "asset_key": "movie::inception::2010",
            "action": "resolve",
            "tmdb_id": 27205,
            "sync_target_index": 0,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["action"] == "resolve"

    pending_row = test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == "movie::inception::2010").first()
    assert pending_row is None

    cache_row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == "movie::inception::2010").first()
    assert cache_row is not None
    assert cache_row.tmdb_id == 27205
    assert cache_row.matched is True


def test_idarr_dismiss_conflict_deletes_leftover_cache_row(client, test_db):
    """Dismissing a conflict-keyed entry deletes its leftover cache row on the spot."""
    conflict_key = "movie::sometitle::2020::conflict=abcd1234"
    test_db.add(IdarrPendingMatch(asset_key=conflict_key, title="Some Title", year=2020, asset_type="movie"))
    test_db.add(IdarrAssetCache(
        asset_key=conflict_key, title="Some Title", year=2020, asset_type="movie", matched=False,
        payload_json=json.dumps({"pending_reason": "rename_conflict", "conflict_files": ["a.png", "b.png"]}),
    ))
    test_db.commit()

    response = client.post(
        "/api/idarr/pending-matches/resolve",
        json={"asset_key": conflict_key, "action": "dismiss", "sync_target_index": 0},
    )
    assert response.status_code == 200

    assert test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == conflict_key).first() is None
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == conflict_key).first() is None


def test_idarr_dismiss_non_conflict_keeps_row_marked_dismissed(client, test_db):
    """A normal dismiss keeps the cache row marked dismissed (suppressed, not deleted)."""
    key = "movie::sometitle::2020"
    test_db.add(IdarrPendingMatch(asset_key=key, title="Some Title", year=2020, asset_type="movie"))
    test_db.add(IdarrAssetCache(
        asset_key=key, title="Some Title", year=2020, asset_type="movie", matched=False,
        payload_json=json.dumps({"status": "not_found"}),
    ))
    test_db.commit()

    response = client.post(
        "/api/idarr/pending-matches/resolve",
        json={"asset_key": key, "action": "dismiss", "sync_target_index": 0},
    )
    assert response.status_code == 200

    assert test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == key).first() is None
    cache_row = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == key).first()
    assert cache_row is not None
    assert json.loads(cache_row.payload_json).get("status") == "dismissed"


def test_idarr_resolve_removes_other_type_provisional_unmatched_row(client, test_db):
    """Resolving a pending match to a different type than the runner inferred must clear the
    inferred-type provisional unmatched cache row, so it doesn't linger as unmatched/stale."""
    # The runner originally guessed "movie" and left a provisional unmatched cache row.
    test_db.add(IdarrAssetCache(
        asset_key="movie::someshow::2021",
        title="Some Show",
        year=2021,
        asset_type="movie",
        matched=False,
        payload_json=json.dumps({"status": "not_found"}),
    ))
    # A distinct, already-resolved item that merely shares the title/year must NOT be touched.
    test_db.add(IdarrAssetCache(
        asset_key="movie::someshow::2021::tmdb=999",
        title="Some Show",
        year=2021,
        asset_type="movie",
        tmdb_id=999,
        matched=True,
        payload_json="{}",
    ))
    test_db.add(IdarrPendingMatch(
        asset_key="pending::someshow::2021",
        title="Some Show",
        year=2021,
        asset_type="pending",
    ))
    test_db.commit()

    response = client.post(
        "/api/idarr/pending-matches/resolve",
        json={
            "asset_key": "pending::someshow::2021",
            "action": "resolve",
            "tmdb_id": 555,
            "tmdb_type": "tv_series",
            "sync_target_index": 0,
        },
    )
    assert response.status_code == 200

    # The inferred-type provisional unmatched row is gone.
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == "movie::someshow::2021").first() is None
    # The pending placeholder is gone, the resolved row exists under the tv_series key.
    assert test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == "pending::someshow::2021").first() is None
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == "tv_series::someshow::2021").first() is not None
    # The distinct already-resolved (matched) sibling sharing title/year is untouched.
    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == "movie::someshow::2021::tmdb=999").first() is not None


def test_idarr_cache_stats_and_maintenance_prune_unmatched(client, test_db):
    matched = IdarrAssetCache(
        asset_key="movie::match::2021",
        title="Match",
        year=2021,
        asset_type="movie",
        tmdb_id=111,
        matched=True,
        payload_json="{}",
        last_checked_at=datetime.now(timezone.utc),
    )
    unmatched = IdarrAssetCache(
        asset_key="movie::unmatch::2021",
        title="Unmatch",
        year=2021,
        asset_type="movie",
        matched=False,
        payload_json="{}",
        last_checked_at=None,
    )
    test_db.add_all([matched, unmatched])
    test_db.commit()

    stats_response = client.get("/api/idarr/cache/stats", params={"sync_target_index": 0})
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["total"] == 2
    assert stats["matched"] == 1
    assert stats["unmatched"] == 1
    assert stats["never_checked"] == 1

    prune_response = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "prune_unmatched", "sync_target_index": 0},
    )
    assert prune_response.status_code == 200
    payload = prune_response.json()
    assert payload["success"] is True
    assert payload["deleted"] == 1
    assert payload["remaining"] == 1


def test_idarr_cache_maintenance_removes_orphaned_pending_matches(client, test_db):
    """prune_unmatched (and the other cache deletes) must also drop the matching
    pending-match rows. Otherwise a ``pending::`` cache row is deleted while its pending
    match survives with no backing cache row, leaving the pending item with no
    ``source_filenames`` so "resolve and rename" silently no-ops."""
    config_response = client.post(
        "/api/idarr/",
        json={
            "sync_targets": [{"label": "Drive 1", "personal_drive_id": "folder-123", "source_dir": ""}],
            "tmdb_api_key": "",
        },
    )
    assert config_response.status_code == 200
    scope_token = str(client.get("/api/idarr/").json()["sync_targets"][0].get("scope_token") or "").strip()

    pending_key = f"pending::air::2023::scope={scope_token}" if scope_token else "pending::air::2023"
    test_db.add(
        IdarrPendingMatch(asset_key=pending_key, title="Air", year=2023, asset_type="pending")
    )
    test_db.add(
        IdarrAssetCache(
            asset_key=pending_key,
            title="Air",
            year=2023,
            asset_type="pending",
            matched=False,
            payload_json=json.dumps({"status": "not_found", "current_filenames": ["Air (2023) - logo.png"]}),
            last_checked_at=None,
        )
    )
    test_db.commit()

    prune_response = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "prune_unmatched", "sync_target_index": 0},
    )
    assert prune_response.status_code == 200
    assert prune_response.json()["deleted"] == 1

    assert test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == pending_key).count() == 0
    # The orphaned pending match must be gone too, not left dangling.
    assert test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == pending_key).count() == 0


def test_idarr_pending_matches_recovers_source_filenames_from_run_history(client, test_db):
    """When the backing ``pending::`` cache row is missing (e.g. pruned by cache
    maintenance), the pending list must still surface ``source_filenames`` by falling
    back to the most recent run's unmatched_items source_path, so "resolve and rename"
    works without first requiring a full run to recreate the cache row."""
    client.post(
        "/api/idarr/",
        json={
            "sync_targets": [{"label": "Drive 1", "personal_drive_id": "folder-123", "source_dir": ""}],
            "tmdb_api_key": "",
        },
    )
    scope_token = str(client.get("/api/idarr/").json()["sync_targets"][0].get("scope_token") or "").strip()

    pending_key = f"pending::air::2023::scope={scope_token}" if scope_token else "pending::air::2023"
    # Orphaned pending match: no cache row at all.
    test_db.add(
        IdarrPendingMatch(asset_key=pending_key, title="Air", year=2023, asset_type="pending")
    )
    test_db.add(
        IdarrRun(
            job_id=None,
            success=True,
            source_dir="/logos",
            destination_dir=None,
            scope_token=scope_token or None,
            stats_json="{}",
            details_json=json.dumps({
                "unmatched_items": [
                    {
                        "title": "Air",
                        "year": 2023,
                        "type": "pending",
                        "source_path": "/logos/Air (2023) - logo.png",
                        "match_reason": "ambiguous",
                    }
                ]
            }),
            warnings_json="[]",
            unmatched_count=1,
            completed_at=datetime.now(timezone.utc),
        )
    )
    test_db.commit()

    response = client.get("/api/idarr/pending-matches", params={"sync_target_index": 0})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["asset_key"] == pending_key
    assert items[0]["source_filenames"] == ["Air (2023) - logo.png"]


def test_idarr_cache_maintenance_purge_stale_rejects_invalid_days(client):
    response = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "purge_stale", "days": 0, "sync_target_index": 0},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "days must be >= 1"


def test_idarr_cache_maintenance_prune_targeted_by_title_and_asset_key(client, test_db):
    row_a = IdarrAssetCache(
        asset_key="collection::wizarding-world::n-a",
        title="Wizarding World Collection",
        year=None,
        asset_type="collection",
        tmdb_id=435,
        tvdb_id=None,
        imdb_id=None,
        matched=False,
        payload_json=json.dumps({"filename": "Wizarding World Collection.jpg"}),
        last_checked_at=datetime.now(timezone.utc),
    )
    row_b = IdarrAssetCache(
        asset_key="show::sample-show::2024",
        title="Sample Show",
        year=2024,
        asset_type="show",
        tmdb_id=9988,
        tvdb_id=5544,
        imdb_id="tt1234567",
        matched=True,
        payload_json="{}",
        last_checked_at=datetime.now(timezone.utc),
    )
    row_c = IdarrAssetCache(
        asset_key="movie::another::2020",
        title="Another Movie",
        year=2020,
        asset_type="movie",
        tmdb_id=777,
        tvdb_id=None,
        imdb_id="tt7654321",
        matched=False,
        payload_json="{}",
        last_checked_at=datetime.now(timezone.utc),
    )
    test_db.add_all([row_a, row_b, row_c])
    test_db.commit()

    response_search = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "prune_targeted", "title": "Wizarding World Collection", "sync_target_index": 0},
    )
    assert response_search.status_code == 200
    assert response_search.json()["deleted"] == 1

    response_partial_title = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "prune_targeted", "title": "Sample", "sync_target_index": 0},
    )
    assert response_partial_title.status_code == 200
    assert response_partial_title.json()["deleted"] == 0

    response_asset_key = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "prune_targeted", "asset_key": "show::sample-show::2024", "sync_target_index": 0},
    )
    assert response_asset_key.status_code == 200
    assert response_asset_key.json()["deleted"] == 1

    response_id = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "prune_targeted", "imdb_id": "tt7654321", "tmdb_id": 777, "sync_target_index": 0},
    )
    assert response_id.status_code == 200
    assert response_id.json()["deleted"] == 1

    remaining = test_db.query(IdarrAssetCache).count()
    assert remaining == 0


def test_idarr_cache_maintenance_prune_targeted_requires_criteria(client):
    response = client.post(
        "/api/idarr/cache/maintenance",
        json={"action": "prune_targeted", "sync_target_index": 0},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Provide at least one criterion (title, asset_key, tmdb_id, tvdb_id, imdb_id)"


def test_idarr_last_run_returns_payload_when_present(client, test_db):
    run = IdarrRun(
        success=True,
        stats_json=json.dumps({"processed": 10}),
        details_json=json.dumps({"enriched_items": []}),
        warnings_json=json.dumps(["example warning"]),
        unmatched_count=2,
        completed_at=datetime.now(timezone.utc),
    )
    test_db.add(run)
    test_db.commit()

    response = client.get("/api/idarr/last-run", params={"sync_target_index": 0})
    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["processed"] == 10
    assert data["unmatched_count"] == 2
    assert data["warnings"] == ["example warning"]


def test_idarr_export_and_revert_return_404_when_no_runs(client):
    export_response = client.post("/api/idarr/exports/csvs", json={"sync_target_index": 0})
    assert export_response.status_code == 404
    assert export_response.json()["detail"] == "No IDarr run data available for export"

    revert_response = client.post("/api/idarr/revert-latest", json={"dry_run": True, "sync_target_index": 0})
    assert revert_response.status_code == 404
    assert revert_response.json()["detail"] == "No IDarr run data available for revert"


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


def test_idarr_runner_enrich_uses_cached_canonical_title_for_filename_generation_without_network_lookup(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Zack Snyder's Justice League Justice is Gray (2021).jpg"
    source_file.write_bytes(b"image")

    asset_key = runner._asset_key(
        asset_type="movie",
        title="Zack Snyder's Justice League Justice is Gray",
        year=2021,
    )

    cached_payload = {
        "status": "found",
        "canonical_title": "Zack Snyder's Justice League",
        "canonical_year": 2021,
        "current_filenames": [source_file.name],
        "original_filenames": [source_file.name],
    }
    test_db.add(
        IdarrAssetCache(
            asset_key=asset_key,
            title="Zack Snyder's Justice League Justice is Gray",
            year=2021,
            asset_type="movie",
            tmdb_id=791373,
            imdb_id="tt12361974",
            matched=True,
            payload_json=json.dumps(cached_payload),
            last_checked_at=datetime.now(timezone.utc),
        )
    )
    test_db.commit()

    def _fail_tmdb_search(*args, **kwargs):
        raise AssertionError("TMDB search should not run when cache is fresh")

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fail_tmdb_search))

    assets = [
        {
            "file_path": source_file,
            "title": "Zack Snyder's Justice League Justice is Gray",
            "year": 2021,
            "type": "movie",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        }
    ]

    stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert stats["tmdb_search_api_calls"] == 0
    assert assets[0]["tmdb_id"] == 791373
    assert assets[0]["imdb_id"] == "tt12361974"
    assert assets[0].get("new_title") == "Zack Snyder's Justice League"

    renamed = runner.generate_new_filename(assets[0], source_file.name)
    assert renamed.startswith("Zack Snyder's Justice League (2021)")
    assert "Justice is Gray" not in renamed


def test_idarr_runner_enrich_applies_cached_canonical_title_for_case_difference(test_db, tmp_path, monkeypatch):
    """Cached canonical title from TMDB wins over filename casing, including accent/case-only differences."""
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "LEGO MARVEL Super Heroes Maximum Overload (2013).jpg"
    source_file.write_bytes(b"image")

    asset_key = runner._asset_key(
        asset_type="movie",
        title="LEGO MARVEL Super Heroes Maximum Overload",
        year=2013,
    )
    cached_payload = {
        "canonical_title": "LEGO Marvel Super Heroes Maximum Overload",
        "canonical_year": 2013,
        "current_filenames": [source_file.name],
        "original_filenames": [source_file.name],
    }
    test_db.add(
        IdarrAssetCache(
            asset_key=asset_key,
            title="LEGO MARVEL Super Heroes Maximum Overload",
            year=2013,
            asset_type="movie",
            tmdb_id=62576,
            imdb_id="tt3331092",
            matched=True,
            payload_json=json.dumps(cached_payload),
            last_checked_at=datetime.now(timezone.utc),
        )
    )
    test_db.commit()

    def _fail_tmdb_search(*args, **kwargs):
        raise AssertionError("TMDB search should not run when cache is fresh")

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fail_tmdb_search))

    assets = [
        {
            "file_path": source_file,
            "title": "LEGO MARVEL Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": 62576,
            "tvdb_id": None,
            "imdb_id": "tt3331092",
            "has_id": True,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0].get("new_title") == "LEGO Marvel Super Heroes Maximum Overload"
    renamed = runner.generate_new_filename(assets[0], source_file.name)
    assert renamed.startswith("LEGO Marvel Super Heroes Maximum Overload (2013)")


def test_idarr_runner_marks_low_confidence_alternate_as_review_required(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Zack Snyder's Justice League Justice is Gray (2021).jpg"
    source_file.write_bytes(b"image")

    def _fake_tmdb_search(*, api_key, title, asset_type, year, tmdb_id=None, tvdb_id=None, imdb_id=None):
        return {
            "id": 791373,
            "title": "Zack Snyder's Justice League",
            "release_date": "2021-03-18",
            "_idarr_match": {
                "confidence": "medium",
                "reason": "fuzzy_alternate",
                "score": 76,
                "candidate_title": "Zack Snyder's Justice League",
                "candidate_year": 2021,
            },
        }, "fuzzy_alternate"

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(lambda **kwargs: {"imdb_id": "tt12361974", "tvdb_id": None}))

    assets = [
        {
            "file_path": source_file,
            "title": "Zack Snyder's Justice League Justice is Gray",
            "year": 2021,
            "type": "movie",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0]["tmdb_id"] == 791373
    assert assets[0]["imdb_id"] == "tt12361974"
    assert assets[0]["has_id"] is False
    assert assets[0].get("review_required") is True
    assert assets[0].get("pending_reason") == "low_confidence_alternate"
    assert assets[0].get("match_reason") == "low_confidence_alternate"


def test_idarr_runner_live_tmdb_title_always_wins_regardless_of_case(test_db, tmp_path, monkeypatch):
    """Live TMDB result is the canonical source — its title always wins even if the
    difference from the current asset title is casing only."""
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "LEGO MARVEL Super Heroes Maximum Overload (2013).jpg"
    source_file.write_bytes(b"image")

    def _fake_tmdb_search(*, api_key, title, asset_type, year, tmdb_id=None, tvdb_id=None, imdb_id=None):
        return {
            "id": 249331,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "release_date": "2013-01-01",
            "imdb_id": "tt3466264",
            "_idarr_match": {
                "confidence": "high",
                "reason": "exact",
                "score": 95,
                "candidate_title": "LEGO Marvel Super Heroes Maximum Overload",
                "candidate_year": 2013,
            },
        }, "exact"

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(lambda **kwargs: {}))

    assets = [
        {
            "file_path": source_file,
            "title": "LEGO MARVEL Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": "tt3466264",
            "has_id": False,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0]["tmdb_id"] == 249331
    # TMDB is the canonical source; its title wins even for case-only differences.
    assert assets[0].get("new_title") == "LEGO Marvel Super Heroes Maximum Overload"


def test_idarr_runner_skips_external_ids_for_collections(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Troll Collection (2022) {tmdb-1180834}.jpg"
    source_file.write_bytes(b"image")

    external_calls = 0

    def _fake_tmdb_verify(*, api_key, tmdb_id, asset_type, title, year=None):
        return {
            "id": 1180834,
            "name": "Troll Collection",
        }, "collection", None

    def _fake_tmdb_external_ids(*, api_key, tmdb_id, asset_type):
        nonlocal external_calls
        external_calls += 1
        return {"imdb_id": "tt9999999", "tvdb_id": 123456}

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_fake_tmdb_verify))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(_fake_tmdb_external_ids))

    assets = [
        {
            "file_path": source_file,
            "title": "Troll Collection",
            "year": 2022,
            "type": "collection",
            "tmdb_id": 1180834,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": True,
        }
    ]

    stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert external_calls == 0
    assert stats["tmdb_external_id_api_calls"] == 0
    assert assets[0].get("imdb_id") is None
    assert assets[0].get("tvdb_id") is None


def test_idarr_runner_corrects_wrong_imdb_tag_against_fresh_cache(test_db, tmp_path, monkeypatch):
    """A tv_series artwork file imported with a WRONG {imdb-...} tag self-heals: when the
    on-disk imdb disagrees with the authoritative (fresh) cache row, IDarr re-fetches
    external IDs by the known-good tmdb and OVERWRITES the wrong tag — even though the cache
    is fresh and a (wrong) imdb is already present. Verify/search must not run."""
    runner = IdarrRunner(test_db)

    key = runner._asset_key(asset_type="tv_series", title="Doctor Who", year=1963, tmdb_id=121)
    test_db.add(IdarrAssetCache(
        asset_key=key,
        title="Doctor Who",
        year=1963,
        asset_type="tv_series",
        tmdb_id=121,
        tvdb_id=76107,
        imdb_id="tt0056751",  # authoritative / correct
        matched=True,
        last_checked_at=datetime.now(timezone.utc),  # fresh → normally skips TMDB
    ))
    test_db.commit()

    external_calls = 0

    def _fake_external_ids(*, api_key, tmdb_id, asset_type):
        nonlocal external_calls
        external_calls += 1
        assert tmdb_id == 121  # fetched by the trusted tmdb, not a title/year re-search
        return {"imdb_id": "tt0056751", "tvdb_id": 76107}

    def _must_not_run(*args, **kwargs):
        raise AssertionError("verify/search must not run — correction goes through external_ids by tmdb")

    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(_fake_external_ids))
    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_must_not_run))
    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_must_not_run))

    art = tmp_path / "Doctor Who (1963) {tmdb-121} {tvdb-76107} {imdb-tt0167261} - background.jpg"
    art.write_bytes(b"img")

    assets = [{
        "file_path": art,
        "title": "Doctor Who",
        "year": 1963,
        "type": "tv_series",
        "tmdb_id": 121,
        "tvdb_id": 76107,
        "imdb_id": "tt0167261",  # WRONG (LOTR) baked into the filename
        "has_id": True,
    }]

    runner._enrich_assets_with_tmdb(assets, "fake-api-key", frequency_days=30, tvdb_frequency=7)

    assert external_calls == 1
    assert assets[0]["imdb_id"] == "tt0056751"  # corrected from the authoritative source


def test_idarr_runner_enrichment_emits_progress_callback_updates(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    def _fake_tmdb_verify(*, api_key, tmdb_id, asset_type, title, year=None):
        payload = {
            "id": tmdb_id,
            "title": title,
            "name": title,
        }
        return payload, asset_type, None

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_fake_tmdb_verify))
    # Uncached-but-tagged items now get a one-time authoritative re-verify; keep it offline.
    monkeypatch.setattr(
        IdarrRunner,
        "_tmdb_external_ids",
        staticmethod(lambda *, api_key, tmdb_id, asset_type: {}),
    )

    assets = []
    for index in range(30):
        source_file = tmp_path / f"Progress Movie {index} (2020).jpg"
        source_file.write_bytes(b"image")
        assets.append(
            {
                "file_path": source_file,
                "title": f"Progress Movie {index}",
                "year": 2020,
                "type": "movie",
                "tmdb_id": 500000 + index,
                "tvdb_id": None,
                "imdb_id": f"tt{7000000 + index}",
                "has_id": True,
            }
        )

    callback_updates: list[tuple[int, int, str]] = []

    runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
        progress_callback=lambda current, total, message: callback_updates.append((current, total, message)),
    )

    assert callback_updates
    assert callback_updates[-1][0] == len(assets)
    assert callback_updates[-1][1] == len(assets)
    assert any("Checking" in message for _, _, message in callback_updates)


def test_idarr_runner_does_not_mark_low_confidence_when_existing_ids_present(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "MASH (1972) - Season 1.jpg"
    source_file.write_bytes(b"image")

    def _fake_tmdb_search(*, api_key, title, asset_type, year=None, tmdb_id=None, tvdb_id=None, imdb_id=None):
        return {
            "id": 918,
            "name": "M*A*S*H",
            "first_air_date": "1972-09-17",
        }, "alternate_title"

    def _fake_classify_search_match(*, candidate, title, year):
        return {
            "confidence": "low",
            "reason": "alternate_title",
            "score": 40,
        }

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_classify_search_match", staticmethod(_fake_classify_search_match))

    assets = [
        {
            "file_path": source_file,
            "title": "MASH",
            "year": 1972,
            "type": "tv_series",
            "tmdb_id": None,
            "tvdb_id": 70994,
            "imdb_id": "tt0068098",
            "has_id": True,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0].get("review_required") is not True
    assert assets[0].get("pending_reason") != "low_confidence_alternate"
    assert assets[0].get("has_id") is True


def test_idarr_runner_rename_files_skips_review_required_low_confidence_alternate(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Zack Snyder's Justice League Justice is Gray (2021).jpg"
    source_file.write_bytes(b"image")

    rename_results = runner.rename_files(
        assets=[
            {
                "file_path": source_file,
                "title": "Zack Snyder's Justice League Justice is Gray",
                "year": 2021,
                "type": "movie",
                "tmdb_id": 791373,
                "imdb_id": "tt12361974",
                "new_title": "Zack Snyder's Justice League",
                "new_year": 2021,
                "review_required": True,
            }
        ],
        dry_run=False,
    )

    assert rename_results["renamed_count"] == 0
    assert rename_results["skipped_count"] == 1
    assert rename_results["duplicate_conflicts"] == 0
    assert source_file.exists() is True
    assert any(
        str(row.get("reason") or "") == "review_required_low_confidence_alternate"
        for row in rename_results.get("operation_rows") or []
    )


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


def test_idarr_runner_tmdb_search_flags_ambiguous_when_multiple_exact_title_year_matches_exist(test_db, monkeypatch):
    runner = IdarrRunner(test_db)

    class _MockResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"id": 101, "title": "The Office", "first_air_date": "2005-01-01"},
                    {"id": 102, "title": "The Office", "first_air_date": "2005-01-01"},
                ]
            }

    monkeypatch.setattr("services.idarr_runner.requests.get", lambda *args, **kwargs: _MockResponse())

    def _ranked(*, title, year, candidates, query_title):
        return [
            {
                "candidate": {"id": 101, "title": "The Office", "first_air_date": "2005-01-01"},
                "score": 82,
                "reason": "exact_title_year",
                "confidence": "high",
                "candidate_title": "The Office",
                "candidate_year": 2005,
                "ratio": 1.0,
            },
            {
                    "candidate": {"id": 102, "title": "The Office", "first_air_date": "2005-01-01"},
                "score": 80,
                "reason": "fuzzy_title_year",
                "confidence": "medium",
                    "candidate_title": "The Office",
                "candidate_year": 2005,
                "ratio": 0.94,
            },
        ]

    monkeypatch.setattr(IdarrRunner, "_rank_tmdb_candidates", staticmethod(_ranked))

    candidate, reason = runner._tmdb_search(
        api_key="fake-key",
        title="The Office",
        asset_type="tv_series",
        year=2005,
    )

    assert candidate is None
    assert reason == "ambiguous"


def test_idarr_runner_tmdb_search_retries_with_transformed_title(test_db, monkeypatch):
    runner = IdarrRunner(test_db)
    seen_queries: list[str] = []

    class _MockResponse:
        def __init__(self, query: str):
            self.query = query

        def raise_for_status(self):
            return None

        def json(self):
            if self.query == "Spider_Man":
                return {"results": []}
            if self.query == "Spider Man":
                return {
                    "results": [
                        {"id": 321, "title": "Spider Man", "release_date": "2002-05-01"}
                    ]
                }
            return {"results": []}

    def _mock_get(url, params=None, timeout=20):
        query = str((params or {}).get("query") or "")
        seen_queries.append(query)
        return _MockResponse(query)

    monkeypatch.setattr("services.idarr_runner.requests.get", _mock_get)

    candidate, reason = runner._tmdb_search(
        api_key="fake-key",
        title="Spider_Man",
        asset_type="movie",
        year=2002,
    )

    assert candidate is not None
    assert candidate.get("id") == 321
    assert reason in {"exact", "fuzzy_title_year", "fuzzy_title"}
    assert "Spider_Man" in seen_queries
    assert "Spider Man" in seen_queries


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


def test_idarr_runner_query_transformations_include_original_script_variants():
    variants = IdarrRunner._query_transformations("Spider-Man+No_Way_Home")

    assert "Spider-Man+No_Way_Home" in variants
    assert "Spider Man+No_Way_Home" in variants
    assert "Spider-Man+No Way Home" in variants
    assert "Spider:Man+No_Way_Home" in variants
    assert "Spider-Man+No:Way:Home" in variants
    assert "Spider\\Man+No_Way_Home" in variants
    assert "Spider-Man\\No_Way_Home" in variants
    assert "Spider-Man/No_Way_Home" in variants


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


# ---------------------------------------------------------------------------
# Regression tests for the TVDB-contamination fix (movie alongside TV files)
# ---------------------------------------------------------------------------


def test_generate_filename_tv_series_retains_tvdb_in_output(test_db):
    """TV show assets must still include {tvdb-...} in the generated filename."""
    runner = IdarrRunner(test_db)
    asset = {
        "title": "1883",
        "year": 2021,
        "type": "tv_series",
        "tmdb_id": 118357,
        "tvdb_id": 396390,
        "imdb_id": "tt13991232",
    }
    new_name = runner._generate_new_filename(asset, "1883 (2021).jpg")
    assert "{tmdb-118357}" in new_name, f"Expected tmdb tag in TV filename, got: {new_name!r}"
    assert "{tvdb-396390}" in new_name, f"Expected tvdb tag in TV filename, got: {new_name!r}"


def test_generate_filename_movie_type_never_includes_tvdb(test_db):
    """A movie asset with a tvdb_id (e.g. contaminated from a TV group) must
    NOT emit {tvdb-...} in the generated filename."""
    runner = IdarrRunner(test_db)
    asset = {
        "title": "LEGO Marvel Super Heroes Maximum Overload",
        "year": 2013,
        "type": "movie",
        "tmdb_id": 763861,
        "tvdb_id": 354864,  # incorrectly inherited from the TV show group
    }
    old_name = "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861} {tvdb-354864}.jpg"
    new_name = runner._generate_new_filename(asset, old_name)
    assert "{tmdb-763861}" in new_name, f"Expected tmdb tag retained, got: {new_name!r}"
    assert "{tvdb-354864}" not in new_name, f"tvdb must be stripped from movie, got: {new_name!r}"


def test_scan_assets_movie_not_contaminated_by_tv_group_when_tmdb_differs(test_db, tmp_path):
    """When a movie poster and TV show posters with the same title/year share a folder,
    the movie must keep its own type and must not inherit the TV group's TVDB ID."""
    runner = IdarrRunner(test_db)

    # Movie poster — carries its own TMDB, no TVDB
    (tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg").write_bytes(b"img")
    # TV show base poster and season — carry different TMDB + TVDB
    (tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-62576} {tvdb-354864}.jpg").write_bytes(b"img")
    (tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-62576} {tvdb-354864} - Season 1.jpg").write_bytes(b"img")

    assets = runner._scan_assets(tmp_path)

    movie = next((a for a in assets if a.get("tmdb_id") == 763861), None)
    assert movie is not None, "Movie asset not found in scan results"
    assert movie["type"] == "movie", (
        f"Movie should retain 'movie' type; scan set it to {movie['type']!r}"
    )
    assert movie.get("tvdb_id") is None, (
        f"Movie must not inherit TVDB from TV group; got tvdb_id={movie.get('tvdb_id')}"
    )

    # TV show files must be unaffected
    tv_files = [a for a in assets if a.get("tmdb_id") == 62576]
    assert len(tv_files) == 2, f"Expected 2 TV assets, found {len(tv_files)}"
    assert all(a["type"] == "tv_series" for a in tv_files), "TV assets must keep tv_series type"
    assert all(a.get("tvdb_id") == 354864 for a in tv_files), "TV assets must keep their TVDB ID"


def test_enrich_movie_not_contaminated_by_tv_group_prefill_when_tmdb_differs(test_db, tmp_path, monkeypatch):
    """When a movie poster's TMDB ID differs from the TV group's resolved TMDB ID,
    the enrichment group-prefill must be skipped so the movie is not mis-typed as
    tv_series and does not receive the TV show's TVDB ID."""
    runner = IdarrRunner(test_db)

    def fake_verify(*, api_key, tmdb_id, asset_type, title, year=None):
        if tmdb_id == 62576:
            return {"id": 62576, "name": title, "first_air_date": "2013-01-01"}, "tv_series", None
        if tmdb_id == 763861:
            return {"id": 763861, "title": title, "release_date": "2013-01-01"}, "movie", None
        return None, None, "not_found"

    def fake_external_ids(*, api_key, tmdb_id, asset_type):
        if tmdb_id == 62576:
            return {"tvdb_id": 354864, "imdb_id": "tt3398228"}
        return {}

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(fake_verify))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(fake_external_ids))

    # TV base poster (sorted alphabetically first because {tmdb-62576} < {tmdb-763861})
    tv_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-62576} {tvdb-354864}.jpg"
    tv_file.write_bytes(b"img")
    # Movie poster — type intentionally set to "tv_series" to simulate scan-phase contamination
    movie_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg"
    movie_file.write_bytes(b"img")

    assets = [
        {
            "file_path": tv_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "tv_series",
            "tmdb_id": 62576,
            "tvdb_id": 354864,
            "imdb_id": None,
            "has_id": True,
        },
        {
            # type deliberately set to tv_series to simulate scan contamination
            "file_path": movie_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "tv_series",
            "tmdb_id": 763861,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": True,
        },
    ]

    runner._enrich_assets_with_tmdb(assets, "fake-api-key", frequency_days=30, tvdb_frequency=7)

    tv = next(a for a in assets if a.get("tmdb_id") == 62576)
    movie = next(a for a in assets if a.get("tmdb_id") == 763861)

    assert tv["type"] == "tv_series"
    assert tv.get("tvdb_id") == 354864

    assert movie["type"] == "movie", (
        f"Movie must be reclassified to 'movie' after TMDB verify; got {movie['type']!r}"
    )
    assert movie.get("tvdb_id") is None, (
        f"Movie must not receive TVDB from TV group prefill; got tvdb_id={movie.get('tvdb_id')}"
    )


def test_cache_prefill_does_not_restore_tvdb_for_movie_asset(test_db, tmp_path, monkeypatch):
    """When a movie asset has no tvdb_id but the cache row does, the cache pre-fill
    must NOT copy that tvdb_id onto the movie — movies have no TVDB IDs.
    This guards against the oscillation pattern: run-1 strips TVDB from the filename,
    _store_asset_cache_rows still has the stale tvdb in the row, and run-2 must not
    re-inject it via the cache pre-fill path."""
    runner = IdarrRunner(test_db)

    # Seed a fresh cache row for the movie with a stale tvdb_id (as would exist after
    # an earlier contaminated run stored it under the movie key).
    movie_key = runner._asset_key(
        asset_type="movie",
        title="LEGO Marvel Super Heroes Maximum Overload",
        year=2013,
        tmdb_id=763861,
    )
    stale_cache_row = IdarrAssetCache(
        asset_key=movie_key,
        title="LEGO Marvel Super Heroes Maximum Overload",
        year=2013,
        asset_type="movie",
        tmdb_id=763861,
        tvdb_id=354864,  # stale contamination from a previous tv_series mis-classification
        matched=True,
        last_checked_at=datetime.now(timezone.utc),  # fresh → verify will be skipped
    )
    test_db.add(stale_cache_row)
    test_db.commit()

    # TMDB should never be called because the cache is fresh — fail the test if it is.
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("TMDB API must not be called when cache is fresh")

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_should_not_be_called))
    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_should_not_be_called))

    movie_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg"
    movie_file.write_bytes(b"img")

    assets = [
        {
            "file_path": movie_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": 763861,
            "tvdb_id": None,  # no TVDB in filename on run-2
            "imdb_id": None,
            "has_id": True,
        }
    ]

    runner._enrich_assets_with_tmdb(assets, "fake-api-key", frequency_days=30, tvdb_frequency=7)

    movie = assets[0]
    assert movie.get("tvdb_id") is None, (
        f"Cache pre-fill must not restore tvdb_id for a movie asset; got tvdb_id={movie.get('tvdb_id')}"
    )


def test_store_cache_rows_does_not_persist_tvdb_for_movie_asset(test_db, tmp_path):
    """_store_asset_cache_rows must never write a tvdb_id into a cache row for a movie
    asset, even when the asset dict carries one (e.g. from _parse_asset parsing the
    original contaminated filename).  Persisting a tvdb onto a movie row would cause
    the cache pre-fill on subsequent runs to keep injecting it."""
    runner = IdarrRunner(test_db)

    movie_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg"
    movie_file.write_bytes(b"img")

    assets = [
        {
            "file_path": movie_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": 763861,
            "tvdb_id": 354864,  # contaminated value — must NOT be stored for movie rows
            "imdb_id": None,
            "has_id": True,
            "_cache_touch": True,
        }
    ]

    runner._store_asset_cache_rows(assets)
    test_db.commit()

    expected_key = runner._asset_key(
        asset_type="movie",
        title="LEGO Marvel Super Heroes Maximum Overload",
        year=2013,
        tmdb_id=763861,
    )
    stored = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == expected_key).first()

    assert stored is not None, "Cache row must be created"
    assert stored.tvdb_id is None, (
        f"tvdb_id must not be stored for movie cache rows; got {stored.tvdb_id}"
    )


def test_derive_asset_subtype_maps_each_subfolder():
    """Asset-drive subtype is derived from the subfolder in the source path."""
    from api.idarr import _derive_asset_subtype

    assert _derive_asset_subtype({"files": "/drive/logos/Air (2023) - logo.png"}) == "logo"
    assert _derive_asset_subtype({"files": "/drive/backgrounds/Air (2023) - background.jpg"}) == "background"
    assert _derive_asset_subtype({"files": "/drive/squareart/Air (2023) - squareart.jpg"}) == "squareart"
    # Windows-style separators and the flat root resolve to no subtype.
    assert _derive_asset_subtype({"files": r"C:\drive\squareart\Air (2023) - squareart.jpg"}) == "squareart"
    assert _derive_asset_subtype({"files": "/drive/Air (2023).jpg"}) is None


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
