"""Plex upload endpoints: run queueing, webhook settings/token/stats, library
override, cache endpoints, instance map, and manual settings round-trips."""

import json

from models.setting import Setting
from models.drive import Drive
from models.poster import Poster
from models.plex_upload import PlexUploadRecord
from core.config import settings


def test_run_plex_upload_queues_background_job(client, test_db, monkeypatch):
    submitted_calls = []

    def _capture_submit(*args, **kwargs):
        submitted_calls.append((args, kwargs))
        return None

    monkeypatch.setattr("api.plex_upload.job_queue.submit", _capture_submit)

    response = client.post(
        "/api/posterflow/plex-upload/run",
        json={
            "dry_run": True,
            "reapply": True,
            "remove_overlay_label": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "pending"
    assert isinstance(data["job_id"], int)
    assert submitted_calls

    submit_args, _submit_kwargs = submitted_calls[0]
    assert submit_args[0].__name__ == "run_plex_upload_background_job"
    assert submit_args[3] is True
    assert submit_args[4] is True
    assert submit_args[5] is True


def test_source_search_returns_prioritized_source_candidates(client, test_db, monkeypatch):
    poster_path = (
        settings.gdrive_dir
        / "MM2K"
        / "MM2K_Movies"
        / "The Matrix (1999) {tmdb-603} {imdb-tt0133093}"
        / "poster.jpg"
    )

    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    test_db.add(
        Poster(
            drive_id="drive-1",
            file_name="poster.jpg",
            file_path=str(poster_path),
        )
    )
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/source-search", params={"q": "matrix"})
    assert response.status_code == 200

    payload = response.json()
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["media_type"] == "movie"
    assert item["title"] == "The Matrix"
    assert item["tmdb_id"] == 603


def test_run_single_upload_queues_background_job(client, test_db, monkeypatch):
    submitted_calls = []

    def _capture_submit(*args, **kwargs):
        submitted_calls.append((args, kwargs))
        return None

    monkeypatch.setattr("api.plex_upload.job_queue.submit", _capture_submit)

    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    test_db.commit()

    response = client.post(
        "/api/posterflow/plex-upload/run-single",
        json={
            "media_type": "movie",
            "title": "The Matrix",
            "year": 1999,
            "tmdb_id": 603,
            "imdb_id": "TT0133093",
            "dry_run": True,
            "reapply": True,
            "remove_overlay_label": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "pending"
    assert isinstance(data["job_id"], int)
    assert submitted_calls

    submit_args, _submit_kwargs = submitted_calls[0]
    assert submit_args[0].__name__ == "run_plex_single_manual_background_job"
    run_payload = submit_args[3]
    assert run_payload["media_type"] == "movie"
    assert run_payload["title"] == "The Matrix"
    assert run_payload["dry_run"] is True
    assert run_payload["reapply"] is True
    assert run_payload["remove_overlay_label"] is True
    assert run_payload["imdb_id"] == "tt0133093"


def test_webhook_settings_default_disabled(client):
    """Webhook settings should default to enabled with all options on when not configured."""
    response = client.get("/api/posterflow/plex-upload/webhook-settings")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["remove_overlay_label"] is True
    assert data["rename_then_upload"] is True
    assert data["adopt_existing_processed"] is True
    assert data["retry_attempts"] == 10
    assert data["retry_delay_seconds"] == 30


def test_webhook_settings_toggle(client):
    """Webhook settings endpoint should persist enabled/disabled flag."""
    disable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": False,
            "remove_overlay_label": True,
            "rename_then_upload": True,
            "adopt_existing_processed": True,
            "retry_attempts": 6,
            "retry_delay_seconds": 12,
        },
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["enabled"] is False
    assert disable_resp.json()["remove_overlay_label"] is True
    assert disable_resp.json()["rename_then_upload"] is True
    assert disable_resp.json()["adopt_existing_processed"] is True
    assert disable_resp.json()["retry_attempts"] == 6
    assert disable_resp.json()["retry_delay_seconds"] == 12

    get_resp = client.get("/api/posterflow/plex-upload/webhook-settings")
    assert get_resp.status_code == 200
    assert get_resp.json()["enabled"] is False
    assert get_resp.json()["remove_overlay_label"] is True
    assert get_resp.json()["rename_then_upload"] is True
    assert get_resp.json()["adopt_existing_processed"] is True
    assert get_resp.json()["retry_attempts"] == 6
    assert get_resp.json()["retry_delay_seconds"] == 12

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": True,
            "remove_overlay_label": False,
            "rename_then_upload": False,
            "adopt_existing_processed": False,
            "retry_attempts": 4,
            "retry_delay_seconds": 5,
        },
    )
    assert enable_resp.status_code == 200
    assert enable_resp.json()["enabled"] is True
    assert enable_resp.json()["remove_overlay_label"] is False
    assert enable_resp.json()["rename_then_upload"] is False
    assert enable_resp.json()["adopt_existing_processed"] is False
    assert enable_resp.json()["retry_attempts"] == 4
    assert enable_resp.json()["retry_delay_seconds"] == 5


def test_webhook_settings_retry_bounds_are_coerced(client, test_db):
    """Retry settings should be clamped to allowed min/max bounds."""
    response = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": True,
            "remove_overlay_label": False,
            "rename_then_upload": False,
            "adopt_existing_processed": False,
            "retry_attempts": 999,
            "retry_delay_seconds": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retry_attempts"] == 20
    assert payload["retry_delay_seconds"] == 1

    attempts_setting = test_db.query(Setting).filter(Setting.key == "plex_webhook_retry_attempts").first()
    delay_setting = test_db.query(Setting).filter(Setting.key == "plex_webhook_retry_delay_seconds").first()
    assert attempts_setting is not None
    assert attempts_setting.value == "20"
    assert delay_setting is not None
    assert delay_setting.value == "1"


def test_webhook_settings_invalid_persisted_retry_values_use_defaults(client, test_db):
    """Invalid persisted retry values should fall back to default webhook retry settings."""
    test_db.add(Setting(key="plex_webhook_retry_attempts", value="not-an-int"))
    test_db.add(Setting(key="plex_webhook_retry_delay_seconds", value="also-not-an-int"))
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/webhook-settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["retry_attempts"] == 10
    assert payload["retry_delay_seconds"] == 30


def test_webhook_queue_passes_rename_then_upload_setting(client, monkeypatch):
    """Webhook queue should pass rename_then_upload setting into background worker args."""
    submitted_calls = []

    def _capture_submit(*args, **kwargs):
        submitted_calls.append((args, kwargs))
        return None

    monkeypatch.setattr("api.plex_upload.job_queue.submit", _capture_submit)

    save_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": True,
            "remove_overlay_label": False,
            "rename_then_upload": True,
            "retry_attempts": 7,
            "retry_delay_seconds": 11,
        },
    )
    assert save_resp.status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook",
        json={
            "eventType": "Download",
            "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
        },
    )

    assert response.status_code == 200
    assert response.json()["queued"] is True
    assert submitted_calls

    submit_args, _submit_kwargs = submitted_calls[0]
    assert len(submit_args) >= 8
    assert submit_args[0].__name__ == "run_plex_webhook_background_job"
    assert submit_args[5] is True
    assert submit_args[6] == 7
    assert submit_args[7] == 11


def test_webhook_token_endpoint_generates_and_regenerates(client):
    """The token endpoint mints a stable token and can rotate it on demand."""
    resp = client.get("/api/posterflow/plex-upload/webhook-token")
    assert resp.status_code == 200
    body = resp.json()
    token1 = body["token"]
    assert token1
    assert body["password_set"] is False

    # Stable across repeated reads.
    assert client.get("/api/posterflow/plex-upload/webhook-token").json()["token"] == token1

    # Regenerate replaces it.
    regen = client.post("/api/posterflow/plex-upload/webhook-token/regenerate")
    assert regen.status_code == 200
    token2 = regen.json()["token"]
    assert token2 and token2 != token1


def test_webhook_requires_token_when_password_set(client, test_db, monkeypatch):
    """With an app password set, the webhook must reject callers lacking a valid token."""
    from core.auth import set_password, get_or_create_webhook_token, invalidate_auth_cache

    submitted = []
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *a, **k: submitted.append((a, k)))

    # Enable the webhook while no password is set.
    save_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": True,
            "remove_overlay_label": False,
            "rename_then_upload": True,
            "retry_attempts": 5,
            "retry_delay_seconds": 10,
        },
    )
    assert save_resp.status_code == 200

    token = get_or_create_webhook_token(test_db)
    set_password(test_db, "secret123")
    test_db.commit()
    invalidate_auth_cache()

    payload = {
        "eventType": "Download",
        "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
    }

    try:
        # No token -> rejected, nothing queued.
        no_token = client.post("/api/posterflow/plex-upload/webhook", json=payload)
        assert no_token.status_code == 401
        assert not submitted

        # Wrong token -> rejected.
        wrong = client.post("/api/posterflow/plex-upload/webhook?token=nope", json=payload)
        assert wrong.status_code == 401
        assert not submitted

        # Valid token via query string -> queued.
        via_query = client.post(f"/api/posterflow/plex-upload/webhook?token={token}", json=payload)
        assert via_query.status_code == 200
        assert via_query.json()["queued"] is True
        assert submitted

        # Valid token via header -> accepted.
        via_header = client.post(
            "/api/posterflow/plex-upload/webhook",
            json=payload,
            headers={"X-Webhook-Token": token},
        )
        assert via_header.status_code == 200
    finally:
        set_password(test_db, "")
        test_db.commit()
        invalidate_auth_cache()


def test_webhook_test_event_updates_stats(client):
    """ARR test event should be ignored but counted in webhook stats."""
    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook",
        json={"eventType": "test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["queued"] is False

    stats_resp = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["received"] == 1
    assert stats["skipped_test"] == 1
    assert stats["skipped_cached"] == 0
    assert stats["queued"] == 0


def test_webhook_disabled_rejects_and_tracks_stats(client):
    """Disabled webhook setting should block ingestion and increment disabled counter."""
    disable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": False, "remove_overlay_label": False},
    )
    assert disable_resp.status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook",
        json={
            "eventType": "Download",
            "movie": {"title": "The Matrix", "year": 1999},
        },
    )
    assert response.status_code == 403

    stats_resp = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["received"] == 1
    assert stats["rejected_disabled"] == 1


def test_webhook_stats_reset_endpoint(client):
    """Webhook stats reset endpoint should restore all counters/timestamps to defaults."""
    client.post(
        "/api/posterflow/plex-upload/webhook",
        json={"eventType": "test"},
    )

    pre_reset = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert pre_reset.status_code == 200
    pre_stats = pre_reset.json()
    assert pre_stats["received"] > 0

    reset_response = client.post("/api/posterflow/plex-upload/webhook-stats/reset")
    assert reset_response.status_code == 200
    reset_data = reset_response.json()
    assert reset_data["success"] is True
    assert reset_data["received"] == 0
    assert reset_data["queued"] == 0
    assert reset_data["duplicates"] == 0
    assert reset_data["skipped_test"] == 0
    assert reset_data["skipped_cached"] == 0
    assert reset_data["rejected_disabled"] == 0
    assert reset_data["parse_errors"] == 0
    assert reset_data["internal_errors"] == 0
    assert reset_data["last_event_at"] is None
    assert reset_data["last_queued_at"] is None
    assert reset_data["last_error"] is None


def test_plex_upload_library_override_defaults(client):
    """Plex Upload library override should default to disabled with empty configs."""
    response = client.get("/api/posterflow/plex-upload/library-override")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["configs"] == []
    assert data["global_configs"] == []


def test_plex_upload_library_override_round_trip(client, test_db):
    """Plex Upload library override settings should persist and return global configs."""
    global_config = [
        {
            "instance_name": "Main Plex",
            "libraries": [
                {"title": "Movies", "key": "1", "type": "movie", "enabled": True},
                {"title": "TV Shows", "key": "2", "type": "show", "enabled": True},
            ],
        }
    ]
    test_db.add(Setting(key="plex_library_config", value=json.dumps(global_config)))
    test_db.commit()

    payload = {
        "enabled": True,
        "configs": [
            {
                "instance_name": "Main Plex",
                "libraries": [
                    {"title": "Movies", "key": "1", "type": "movie", "enabled": True},
                    {"title": "TV Shows", "key": "2", "type": "show", "enabled": False},
                ],
            }
        ],
    }

    save_response = client.post("/api/posterflow/plex-upload/library-override", json=payload)
    assert save_response.status_code == 200
    save_data = save_response.json()
    assert save_data["success"] is True
    assert save_data["enabled"] is True
    assert len(save_data["configs"]) == 1
    assert len(save_data["global_configs"]) == 1

    get_response = client.get("/api/posterflow/plex-upload/library-override")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["enabled"] is True
    assert get_data["configs"][0]["instance_name"] == "Main Plex"
    assert get_data["configs"][0]["libraries"][0]["enabled"] is True
    assert get_data["configs"][0]["libraries"][1]["enabled"] is False
    assert get_data["global_configs"][0]["instance_name"] == "Main Plex"


def test_plex_upload_cache_defaults(client):
    """Plex upload cache endpoint should return empty summary by default."""
    response = client.get("/api/posterflow/plex-upload/upload-cache")
    assert response.status_code == 200
    data = response.json()
    assert data["entries_count"] == 0
    assert data["total_library_refs"] == 0
    assert data["total_edition_refs"] == 0
    assert data["entries"] == []


def test_plex_upload_cache_clear_round_trip(client, test_db):
    """Plex upload cache clear endpoint should clear one file and all files."""
    test_db.add(PlexUploadRecord(
        file_path="/tmp/a.jpg",
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps([]),
        uploaded_editions=json.dumps(["default"]),
        uploaded_media_types=json.dumps([]),
    ))
    test_db.add(PlexUploadRecord(
        file_path="/tmp/b.jpg",
        file_hash=None,
        uploaded_to_libraries=json.dumps(["TV Shows"]),
        uploaded_to_library_keys=json.dumps([]),
        uploaded_editions=json.dumps([]),
        uploaded_media_types=json.dumps([]),
    ))
    test_db.commit()

    get_response = client.get("/api/posterflow/plex-upload/upload-cache")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["entries_count"] == 2

    clear_one_response = client.post(
        "/api/posterflow/plex-upload/upload-cache/clear",
        json={"file_path": "/tmp/a.jpg"},
    )
    assert clear_one_response.status_code == 200
    clear_one_data = clear_one_response.json()
    assert clear_one_data["success"] is True
    assert clear_one_data["removed"] == 1
    assert clear_one_data["entries_count"] == 1

    clear_all_response = client.post("/api/posterflow/plex-upload/upload-cache/clear", json={})
    assert clear_all_response.status_code == 200
    clear_all_data = clear_all_response.json()
    assert clear_all_data["success"] is True
    assert clear_all_data["removed"] == 1
    assert clear_all_data["entries_count"] == 0


def test_plex_upload_cache_export(client, test_db):
    """Plex upload cache export endpoint should return downloadable JSON payload."""
    for fp, libs, editions, media_types in [
        ("/tmp/a.jpg", ["Movies"], ["default_edition"], ["movies"]),
        ("/tmp/The Show/poster.jpg", ["Movies"], [], ["shows"]),
        ("/tmp/The Show/Season01.jpg", ["Movies"], [], ["seasons"]),
        ("/tmp/Zeta Collection/poster.jpg", ["Movies"], [], ["collections"]),
        ("/tmp/Alpha Collection/poster.jpg", ["Movies"], [], ["collections"]),
    ]:
        test_db.add(PlexUploadRecord(
            file_path=fp,
            file_hash=None,
            uploaded_to_libraries=json.dumps(libs),
            uploaded_to_library_keys=json.dumps([]),
            uploaded_editions=json.dumps(editions),
            uploaded_media_types=json.dumps(media_types),
        ))
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/upload-cache/export")
    assert response.status_code == 200
    payload = response.json()
    assert payload["entries_count"] == 5
    assert "exported_at" in payload
    assert "cache" not in payload
    assert payload["library_totals"]["totals"] == {
        "libraries": 1,
        "collections": 2,
        "movies": 1,
        "shows": 1,
        "seasons": 1,
    }
    assert payload["library_totals"]["per_library"]["Movies"] == {
        "collections": 2,
        "movies": 1,
        "shows": 1,
        "seasons": 1,
    }
    assert "grouped_by_library" in payload
    assert "Movies" in payload["grouped_by_library"]
    grouped_movies = payload["grouped_by_library"]["Movies"]
    assert list(grouped_movies.keys()) == ["totals", "collections", "movies", "shows"]
    assert grouped_movies["totals"] == {
        "collections": 2,
        "movies": 1,
        "shows": 1,
        "seasons": 1,
    }
    assert grouped_movies["movies"][0]["file_path"] == "/tmp/a.jpg"
    assert grouped_movies["movies"][0]["uploaded_editions"] == ["default_edition"]
    assert [item["title"] for item in grouped_movies["collections"]] == ["Alpha Collection", "Zeta Collection"]
    assert len(grouped_movies["shows"]) == 1
    assert grouped_movies["shows"][0]["title"] == "The Show"
    assert grouped_movies["shows"][0]["counts"] == {"show_posters": 1, "seasons": 1}
    assert grouped_movies["shows"][0]["show_posters"][0]["file_path"] == "/tmp/The Show/poster.jpg"
    assert grouped_movies["shows"][0]["seasons"][0]["file_path"] == "/tmp/The Show/Season01.jpg"
    assert grouped_movies["shows"][0]["seasons"][0]["season_number"] == 1
    assert grouped_movies["shows"][0]["seasons"][0]["uploaded_media_types"] == ["seasons"]


def test_plex_upload_cache_entries_pagination_and_search(client, test_db):
    """Paginated cache entries endpoint should support offset/limit and text filtering."""
    for fp, libs, media_types, editions in [
        ("/tmp/alpha_movie.jpg", ["Movies"], ["movies"], ["default"]),
        ("/tmp/beta_show_poster.jpg", ["TV Shows"], ["shows"], []),
        ("/tmp/gamma_collection.jpg", ["Collections"], ["collections"], []),
    ]:
        test_db.add(PlexUploadRecord(
            file_path=fp,
            file_hash=None,
            uploaded_to_libraries=json.dumps(libs),
            uploaded_to_library_keys=json.dumps([]),
            uploaded_editions=json.dumps(editions),
            uploaded_media_types=json.dumps(media_types),
        ))
    test_db.commit()

    first_page = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"limit": 2, "offset": 0},
    )
    assert first_page.status_code == 200
    first_data = first_page.json()
    assert first_data["total"] == 3
    assert first_data["limit"] == 2
    assert first_data["offset"] == 0
    assert first_data["has_more"] is True
    assert len(first_data["entries"]) == 2

    second_page = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"limit": 2, "offset": 2},
    )
    assert second_page.status_code == 200
    second_data = second_page.json()
    assert second_data["total"] == 3
    assert second_data["offset"] == 2
    assert second_data["has_more"] is False
    assert len(second_data["entries"]) == 1

    filtered = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"q": "tv shows", "limit": 25, "offset": 0},
    )
    assert filtered.status_code == 200
    filtered_data = filtered.json()
    assert filtered_data["total"] == 1
    assert filtered_data["entries"][0]["file_path"] == "/tmp/beta_show_poster.jpg"


def test_plex_upload_cache_entries_limit_and_offset_safety(client, test_db):
    """Paginated cache endpoint should clamp invalid limit/offset values."""
    test_db.add(PlexUploadRecord(
        file_path="/tmp/one.jpg",
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps([]),
        uploaded_editions=json.dumps([]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    response = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"limit": 0, "offset": -5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 1
    assert data["offset"] == 0
    assert data["total"] == 1
    assert len(data["entries"]) == 1


def test_plex_upload_instance_map_endpoints_round_trip(client):
    empty = client.get("/api/settings/plex-upload-instance-map")
    assert empty.status_code == 200
    assert empty.json()["map"] == {}

    save = client.post("/api/settings/plex-upload-instance-map", json={
        "map": {
            "Radarr-4K": [{"plex_instance": "Main", "library_key": "k_movies4k"}],
            "Radarr-Empty": [],
        }
    })
    assert save.status_code == 200
    saved_map = save.json()["map"]
    assert saved_map["Radarr-4K"] == [{"plex_instance": "Main", "library_key": "k_movies4k"}]
    # Instances with no usable rows are dropped.
    assert "Radarr-Empty" not in saved_map

    fetched = client.get("/api/settings/plex-upload-instance-map")
    assert fetched.json()["map"]["Radarr-4K"][0]["library_key"] == "k_movies4k"


# ---------------------------------------------------------------------------
# Manual settings: upload_artwork
# ---------------------------------------------------------------------------


def _manual_payload(**overrides):
    payload = {
        "dry_run": True,
        "reapply": False,
        "remove_overlay_label": False,
        "sync_before_upload": False,
        "rename_before_upload": True,
        "border_before_upload": False,
        "upload_delay_ms": 50,
        "upload_artwork": False,
    }
    payload.update(overrides)
    return payload


def test_manual_settings_round_trip_upload_artwork(client, test_db):
    """upload_artwork saves with the other manual options instead of its own endpoint,
    so the one toggle that used to save on click now rides the shared Save button."""
    response = client.post("/api/posterflow/plex-upload/manual-settings", json=_manual_payload(upload_artwork=True))
    assert response.status_code == 200
    assert response.json()["upload_artwork"] is True

    setting = test_db.query(Setting).filter(Setting.key == "plex_upload_artwork").first()
    assert setting.value == "true"

    assert client.get("/api/posterflow/plex-upload/manual-settings").json()["upload_artwork"] is True


def test_manual_settings_upload_artwork_defaults_off(client):
    assert client.get("/api/posterflow/plex-upload/manual-settings").json()["upload_artwork"] is False


def test_manual_settings_upload_artwork_can_be_turned_off(client, test_db):
    test_db.add(Setting(key="plex_upload_artwork", value="true"))
    test_db.commit()

    response = client.post("/api/posterflow/plex-upload/manual-settings", json=_manual_payload(upload_artwork=False))

    assert response.json()["upload_artwork"] is False
    test_db.expire_all()
    assert test_db.query(Setting).filter(Setting.key == "plex_upload_artwork").first().value == "false"


def test_manual_settings_does_not_touch_the_webhook_artwork_toggle(test_db, client):
    """The webhook keeps its own artwork setting — manual/workflow and per-event uploads
    are configured separately on purpose."""
    test_db.add(Setting(key="plex_webhook_artwork", value="true"))
    test_db.commit()

    client.post("/api/posterflow/plex-upload/manual-settings", json=_manual_payload(upload_artwork=False))

    test_db.expire_all()
    assert test_db.query(Setting).filter(Setting.key == "plex_webhook_artwork").first().value == "true"
