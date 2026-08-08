"""Per-instance ARR attribution and webhook dedupe: instance resolution, library
scoping, dedupe identity (structured/ID-aware), and instance-token stats."""

import json

from models.setting import Setting
from services.plex_upload import PlexUploadService


def test_webhook_duplicate_suppression_updates_stats(client, monkeypatch):
    """Duplicate webhook payload should be suppressed and tracked in stats."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }

    first = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert first.status_code == 200
    assert first.json()["queued"] is True

    second = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["queued"] is False
    assert second_data["duplicate"] is True

    stats_resp = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["received"] == 2
    assert stats["queued"] == 1
    assert stats["duplicates"] == 1


def test_webhook_dedupe_clear_single_item_allows_requeue(client, monkeypatch):
    """Clearing webhook dedupe for one item should allow that item to queue again."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }

    first = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert first.status_code == 200
    assert first.json()["queued"] is True

    duplicate = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["queued"] is False
    assert duplicate.json()["duplicate"] is True

    clear_resp = client.post(
        "/api/posterflow/plex-upload/webhook-dedupe/clear",
        json={
            "media_type": "movie",
            "title": "The Matrix",
            "year": 1999,
        },
    )
    assert clear_resp.status_code == 200
    clear_data = clear_resp.json()
    assert clear_data["success"] is True
    assert clear_data["removed"] >= 1

    after_clear = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert after_clear.status_code == 200
    assert after_clear.json()["queued"] is True


def test_webhook_dedupe_clear_all_allows_requeue(client, monkeypatch):
    """Clearing all webhook dedupe entries should allow all targets to queue again."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }
    series_payload = {
        "eventType": "Download",
        "series": {
            "title": "The Office",
            "year": 2005,
            "tvdbId": 73244,
            "imdbId": "tt0386676",
        },
    }

    assert client.post("/api/posterflow/plex-upload/webhook", json=movie_payload).status_code == 200
    assert client.post("/api/posterflow/plex-upload/webhook", json=series_payload).status_code == 200

    movie_dup = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    series_dup = client.post("/api/posterflow/plex-upload/webhook", json=series_payload)
    assert movie_dup.status_code == 200 and movie_dup.json()["queued"] is False
    assert series_dup.status_code == 200 and series_dup.json()["queued"] is False

    clear_resp = client.post(
        "/api/posterflow/plex-upload/webhook-dedupe/clear",
        json={"clear_all": True},
    )
    assert clear_resp.status_code == 200
    clear_data = clear_resp.json()
    assert clear_data["success"] is True
    assert clear_data["removed"] >= 2

    movie_after = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    series_after = client.post("/api/posterflow/plex-upload/webhook", json=series_payload)
    assert movie_after.status_code == 200 and movie_after.json()["queued"] is True
    assert series_after.status_code == 200 and series_after.json()["queued"] is True


def test_webhook_dedupe_entries_search_returns_active_locks(client, monkeypatch):
    """Webhook dedupe entries endpoint should return active lock entries from DB cache."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }
    series_payload = {
        "eventType": "Download",
        "series": {
            "title": "The Office",
            "year": 2005,
            "tvdbId": 73244,
            "imdbId": "tt0386676",
        },
    }

    assert client.post("/api/posterflow/plex-upload/webhook", json=movie_payload).status_code == 200
    assert client.post("/api/posterflow/plex-upload/webhook", json=series_payload).status_code == 200

    response = client.get(
        "/api/posterflow/plex-upload/webhook-dedupe/entries",
        params={"q": "matrix", "media_type": "movie", "limit": 10},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "matrix"
    assert payload["media_type"] == "movie"
    assert payload["count"] >= 1
    assert payload["total"] >= payload["count"]

    first = payload["items"][0]
    assert first["media_type"] == "movie"
    assert "matrix" in first["title"]
    assert first["year"] == 1999
    assert "dedupe_key" in first
    assert "last_seen_at" in first


def test_webhook_dedupe_entries_search_rejects_invalid_media_type(client):
    """Webhook dedupe entries endpoint should validate media_type query parameter."""
    response = client.get(
        "/api/posterflow/plex-upload/webhook-dedupe/entries",
        params={"q": "matrix", "media_type": "invalid"},
    )
    assert response.status_code == 400


def test_webhook_different_seasons_not_deduplicated(client, monkeypatch):
    """Webhooks for different seasons of the same show must each queue a separate job.

    Season 2 arriving within the dedupe window after Season 1 must NOT be treated as
    a duplicate — each season needs its own upload job.  Per-episode spam within the
    same season is still collapsed (second webhook for the same season IS a duplicate).
    """
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    def _make_series_payload(season_number: int) -> dict:
        return {
            "eventType": "Download",
            "series": {
                "title": "Breaking Bad",
                "year": 2008,
                "tvdbId": 81189,
            },
            "episodes": [{"seasonNumber": season_number, "episodeNumber": 1}],
        }

    # Season 1 — should queue
    s1_first = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(1))
    assert s1_first.status_code == 200
    assert s1_first.json()["queued"] is True, "Season 1 first webhook should be queued"

    # Season 1 again — should be deduplicated (same season)
    s1_dup = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(1))
    assert s1_dup.status_code == 200
    assert s1_dup.json()["queued"] is False, "Season 1 repeat within window should be deduplicated"
    assert s1_dup.json()["duplicate"] is True

    # Season 2 — must NOT be deduplicated even though it's within the window
    s2_first = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(2))
    assert s2_first.status_code == 200
    assert s2_first.json()["queued"] is True, "Season 2 should not be deduplicated by Season 1's cache entry"

    # Season 2 again — should be deduplicated
    s2_dup = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(2))
    assert s2_dup.status_code == 200
    assert s2_dup.json()["queued"] is False, "Season 2 repeat within window should be deduplicated"


# ---------------------------------------------------------------------------
# Per-instance attribution: identity, dedupe scoping, and library routing
# ---------------------------------------------------------------------------


def test_parse_arr_webhook_payload_captures_instance_fields():
    from modules.upload import _parse_arr_webhook_payload

    movie = _parse_arr_webhook_payload({
        "eventType": "Download",
        "instanceName": "Radarr-4K",
        "applicationUrl": "http://radarr4k:7878",
        "movie": {"title": "Aliens", "year": 1986, "tmdbId": 679},
    })
    assert movie["instance_name"] == "Radarr-4K"
    assert movie["application_url"] == "http://radarr4k:7878"

    series = _parse_arr_webhook_payload({
        "eventType": "Download",
        "instanceName": "Sonarr-Anime",
        "series": {"title": "Cowboy Bebop", "year": 1998, "tvdbId": 76885},
        "seasonNumber": 1,
    })
    assert series["instance_name"] == "Sonarr-Anime"
    assert series["application_url"] is None


def test_parse_arr_webhook_payload_instance_fields_default_none():
    from modules.upload import _parse_arr_webhook_payload

    result = _parse_arr_webhook_payload({
        "eventType": "Download",
        "movie": {"title": "Aliens", "year": 1986, "tmdbId": 679},
    })
    assert result["instance_name"] is None
    assert result["application_url"] is None


def _make_request_stub(instance_token=None):
    import types

    query = {"instance": instance_token} if instance_token is not None else {}
    return types.SimpleNamespace(query_params=query)


def _seed_radarr_instances(test_db):
    import json as _json
    from models.setting import upsert_setting as _upsert_setting

    _upsert_setting(test_db, "radarr_instances", _json.dumps([
        {"name": "Radarr-4K", "url": "http://radarr4k:7878", "api_key": "a"},
        {"name": "Radarr-1080p", "url": "http://radarr1080:7878", "api_key": "b"},
    ]))
    test_db.commit()


def test_resolve_arr_instance_prefers_url_token(test_db):
    from api.plex_upload import resolve_arr_instance

    _seed_radarr_instances(test_db)
    parsed = {"source": "radarr", "instance_name": "Radarr-1080p"}
    # URL token wins over the payload instanceName.
    assert resolve_arr_instance(test_db, _make_request_stub("Radarr-4K"), parsed) == "Radarr-4K"


def test_resolve_arr_instance_falls_back_to_payload_name(test_db):
    from api.plex_upload import resolve_arr_instance

    _seed_radarr_instances(test_db)
    parsed = {"source": "radarr", "instance_name": "Radarr-1080p"}
    assert resolve_arr_instance(test_db, _make_request_stub(), parsed) == "Radarr-1080p"


def test_resolve_arr_instance_falls_back_to_application_url(test_db):
    from api.plex_upload import resolve_arr_instance

    _seed_radarr_instances(test_db)
    parsed = {"source": "radarr", "instance_name": None, "application_url": "http://radarr4k:7878/"}
    assert resolve_arr_instance(test_db, _make_request_stub(), parsed) == "Radarr-4K"


def test_resolve_arr_instance_returns_none_when_unmatched(test_db):
    from api.plex_upload import resolve_arr_instance

    _seed_radarr_instances(test_db)
    parsed = {"source": "radarr", "instance_name": "Radarr-Unknown"}
    assert resolve_arr_instance(test_db, _make_request_stub("nope"), parsed) is None


def test_dedupe_does_not_collapse_distinct_instances(client, test_db, monkeypatch):
    """Two different Radarr instances importing the same movie should both queue."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)
    _seed_radarr_instances(test_db)
    assert client.post("/api/posterflow/plex-upload/webhook-settings", json={"enabled": True}).status_code == 200

    base_movie = {"title": "The Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093"}

    first = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "instanceName": "Radarr-4K", "movie": base_movie,
    })
    second = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "instanceName": "Radarr-1080p", "movie": base_movie,
    })

    assert first.json()["queued"] is True
    assert second.json()["queued"] is True


def test_dedupe_still_collapses_same_instance(client, test_db, monkeypatch):
    """The same instance importing the same movie twice still dedupes."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)
    _seed_radarr_instances(test_db)
    assert client.post("/api/posterflow/plex-upload/webhook-settings", json={"enabled": True}).status_code == 200

    payload = {
        "eventType": "Download", "instanceName": "Radarr-4K",
        "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
    }
    first = client.post("/api/posterflow/plex-upload/webhook", json=payload)
    second = client.post("/api/posterflow/plex-upload/webhook", json=payload)

    assert first.json()["queued"] is True
    assert second.json()["queued"] is False
    assert second.json()["duplicate"] is True


def test_clear_dedupe_parses_legacy_keys(test_db):
    """Legacy 5/6-part dedupe keys (no instance prefix) still clear correctly."""
    import json as _json
    import time as _time
    from models.setting import upsert_setting as _upsert_setting
    from modules.upload import _clear_webhook_dedupe_cache, SETTING_PLEX_WEBHOOK_DEDUPE_CACHE

    now = int(_time.time())
    cache = {
        "radarr:download:movie:thematrix:1999:": now,          # legacy 6-part
        "radarr:download:movie:inception:2010": now,           # legacy 5-part
        "Radarr-4K:radarr:download:movie:dune:2021:": now,     # new 7-part
    }
    _upsert_setting(test_db, SETTING_PLEX_WEBHOOK_DEDUPE_CACHE, _json.dumps(cache))
    test_db.commit()

    result = _clear_webhook_dedupe_cache(test_db, clear_all=True)
    assert result["removed"] == 3
    assert result["remaining"] == 0


def _filter_service(test_db, instance_map=None):
    import json as _json
    from models.setting import upsert_setting as _upsert_setting

    if instance_map is not None:
        _upsert_setting(test_db, "plex_upload_instance_library_map", _json.dumps(instance_map))
        test_db.commit()
    return PlexUploadService(test_db)


def _sample_selected():
    return {
        "Main": [
            {"key": "k_movies", "title": "Movies", "enabled": True},
            {"key": "k_movies4k", "title": "Movies 4K", "enabled": True},
        ]
    }


def test_filter_libraries_unmapped_instance_returns_all(test_db):
    service = _filter_service(test_db)
    selected = _sample_selected()
    filtered, error = service._filter_libraries_for_instance(selected, None)
    assert error is None
    assert filtered == selected


def test_filter_libraries_no_map_returns_all(test_db):
    service = _filter_service(test_db)
    selected = _sample_selected()
    filtered, error = service._filter_libraries_for_instance(selected, "Radarr-4K")
    assert error is None
    assert filtered == selected


def test_filter_libraries_scopes_to_mapped_library(test_db):
    service = _filter_service(test_db, instance_map={
        "Radarr-4K": [{"plex_instance": "Main", "library_key": "k_movies4k"}],
    })
    filtered, error = service._filter_libraries_for_instance(_sample_selected(), "Radarr-4K")
    assert error is None
    assert list(filtered.keys()) == ["Main"]
    assert [lib["key"] for lib in filtered["Main"]] == ["k_movies4k"]


def test_filter_libraries_error_when_mapping_matches_no_enabled_library(test_db):
    service = _filter_service(test_db, instance_map={
        "Radarr-4K": [{"plex_instance": "Main", "library_key": "does_not_exist"}],
    })
    filtered, error = service._filter_libraries_for_instance(_sample_selected(), "Radarr-4K")
    assert filtered == {}
    assert error is not None and "Radarr-4K" in error


# ---------------------------------------------------------------------------
# Structured dedupe cache: ID-aware identity (problem 4)
# ---------------------------------------------------------------------------


def _enable_webhook(client):
    assert client.post("/api/posterflow/plex-upload/webhook-settings", json={"enabled": True}).status_code == 200


def test_dedupe_distinguishes_movies_sharing_title_year(client, monkeypatch):
    """Two different movies with the same title+year but different tmdbId must not collide."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)
    _enable_webhook(client)

    first = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "movie": {"title": "Crash", "year": 2004, "tmdbId": 1640},
    })
    second = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "movie": {"title": "Crash", "year": 2004, "tmdbId": 75},
    })

    assert first.json()["queued"] is True
    assert second.json()["queued"] is True


def test_dedupe_collapses_same_id_despite_title_variation(client, monkeypatch):
    """Same tmdbId with a different title string is still a duplicate (ID is authoritative)."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)
    _enable_webhook(client)

    first = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
    })
    second = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "movie": {"title": "Matrix, The", "year": 1999, "tmdbId": 603},
    })

    assert first.json()["queued"] is True
    assert second.json()["queued"] is False
    assert second.json()["duplicate"] is True


def test_dedupe_yearless_movies_with_distinct_ids_do_not_collide(client, monkeypatch):
    """Year-less imports with different IDs must not collapse into one (old title:'' bug)."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)
    _enable_webhook(client)

    first = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "movie": {"title": "Untitled", "tmdbId": 111},
    })
    second = client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "movie": {"title": "Untitled", "tmdbId": 222},
    })

    assert first.json()["queued"] is True
    assert second.json()["queued"] is True


def test_media_id_token_prefers_tmdb_then_tvdb_then_imdb():
    from modules.upload import _media_id_token

    assert _media_id_token({"tmdb_id": 603, "tvdb_id": 1, "imdb_id": "tt1"}) == "tmdb-603"
    assert _media_id_token({"tmdb_id": None, "tvdb_id": 73244, "imdb_id": "tt1"}) == "tvdb-73244"
    assert _media_id_token({"tmdb_id": None, "tvdb_id": None, "imdb_id": "tt0133093"}) == "imdb-tt0133093"
    assert _media_id_token({"tmdb_id": None, "tvdb_id": None, "imdb_id": None}) is None


def test_dedupe_identity_falls_back_to_title_year_without_id():
    from modules.upload import _webhook_dedupe_record, _webhook_dedupe_identity

    with_id = _webhook_dedupe_identity(_webhook_dedupe_record(
        {"source": "radarr", "event_type": "download", "media_type": "movie", "title": "The Matrix", "year": 1999, "tmdb_id": 603}
    ))
    no_id = _webhook_dedupe_identity(_webhook_dedupe_record(
        {"source": "radarr", "event_type": "download", "media_type": "movie", "title": "The Matrix", "year": 1999}
    ))

    assert "id|tmdb-603" in with_id
    assert "tmdb-603" not in no_id
    assert no_id.endswith("|tt|thematrix|1999|")


def test_structured_dedupe_cache_persists_record_fields(client, test_db, monkeypatch):
    """The persisted dedupe cache stores structured records (not delimited strings)."""
    import json as _json
    from models.setting import get_setting as _get_setting
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)
    _enable_webhook(client)

    client.post("/api/posterflow/plex-upload/webhook", json={
        "eventType": "Download", "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
    })

    setting = _get_setting(test_db, "plex_webhook_dedupe_cache")
    cache = _json.loads(setting.value)
    record = next(iter(cache.values()))
    assert isinstance(record, dict)
    assert record["id"] == "tmdb-603"
    assert record["media"] == "movie"
    assert record["title"] == "thematrix"
    assert isinstance(record["ts"], int)


def test_webhook_unknown_instance_token_recorded_in_stats(client, test_db, monkeypatch):
    """A ?instance= token matching no configured arr instance is recorded in webhook
    stats (stale webhook URL after a rename); a matching token records nothing, and
    the stats reset clears the notice."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    test_db.add(Setting(key="radarr_instances", value=json.dumps([
        {"name": "Radarr 1080p", "url": "http://radarr:7878", "api_key": "key"},
    ])))
    test_db.commit()

    assert client.post(
        "/api/posterflow/plex-upload/webhook-settings", json={"enabled": True}
    ).status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook?instance=Radarr%20HD",
        json={
            "eventType": "Download",
            "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
        },
    )
    assert response.status_code == 200

    stats = client.get("/api/posterflow/plex-upload/webhook-stats").json()
    token = stats["unknown_instance_tokens"]["Radarr HD"]
    assert token["count"] == 1
    assert token["source"] == "radarr"
    assert token["last_seen"]

    # A token that matches a configured instance records nothing new.
    response = client.post(
        "/api/posterflow/plex-upload/webhook?instance=Radarr%201080p",
        json={
            "eventType": "Download",
            "movie": {"title": "Inception", "year": 2010, "tmdbId": 27205},
        },
    )
    assert response.status_code == 200
    stats = client.get("/api/posterflow/plex-upload/webhook-stats").json()
    assert list(stats["unknown_instance_tokens"].keys()) == ["Radarr HD"]

    reset = client.post("/api/posterflow/plex-upload/webhook-stats/reset")
    assert reset.status_code == 200
    stats = client.get("/api/posterflow/plex-upload/webhook-stats").json()
    assert stats["unknown_instance_tokens"] == {}


def test_webhook_instance_token_ignored_when_no_instances_configured(client, monkeypatch):
    """Pre-setup installs (no arr instances at all) must not accumulate warnings."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    assert client.post(
        "/api/posterflow/plex-upload/webhook-settings", json={"enabled": True}
    ).status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook?instance=Whatever",
        json={
            "eventType": "Download",
            "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
        },
    )
    assert response.status_code == 200

    stats = client.get("/api/posterflow/plex-upload/webhook-stats").json()
    assert stats["unknown_instance_tokens"] == {}
