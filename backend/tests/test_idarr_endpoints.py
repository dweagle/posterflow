"""IDarr API endpoints: config round-trips, run history retention, uploads,
pending-match resolve/dismiss, cache stats/maintenance, last-run and export."""

import json
from datetime import datetime, timezone

from models.idarr import IdarrAssetCache, IdarrPendingMatch, IdarrRun, prune_idarr_run_history, compact_idarr_run_details_history
from models.setting import Setting


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
