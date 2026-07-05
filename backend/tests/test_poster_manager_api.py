import time
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from models.setting import Setting
from models.drive import Drive
from models.job import Job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmdb_movie_result(tmdb_id: int, title: str, year: int, popularity: float = 10.0) -> dict:
    return {
        "id": tmdb_id,
        "title": title,
        "release_date": f"{year}-01-01",
        "poster_path": f"/poster{tmdb_id}.jpg",
        "overview": f"Overview of {title}",
        "popularity": popularity,
    }


# ---------------------------------------------------------------------------
# TMDB search endpoint tests
# ---------------------------------------------------------------------------

def test_unmatched_tmdb_search_rejects_missing_api_key(client):
    """Should return 400 when TMDB API key is not configured."""
    response = client.post(
        "/api/posterflow/unmatched-tmdb-search",
        json={"title": "The Batman", "year": 2022, "type": "movie"},
    )
    assert response.status_code == 400
    assert "TMDB API key is not configured" in response.json()["detail"]


def test_unmatched_tmdb_search_rejects_empty_title(client, test_db):
    """Should return 400 when title is empty."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    response = client.post(
        "/api/posterflow/unmatched-tmdb-search",
        json={"title": "   ", "year": None, "type": "movie"},
    )
    assert response.status_code == 400
    assert "title is required" in response.json()["detail"]


def test_unmatched_tmdb_search_rejects_invalid_type(client, test_db):
    """Should return 400 when type is not movie/show/collection."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    response = client.post(
        "/api/posterflow/unmatched-tmdb-search",
        json={"title": "Avatar", "year": 2009, "type": "episode"},
    )
    assert response.status_code == 400
    assert "type must be one of" in response.json()["detail"]


def test_unmatched_tmdb_search_strips_language_region_tags(client, test_db):
    """Should strip parenthetical 2-3 letter language/region tags (e.g. (NL), (UK)) before searching."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [
        {"id": 55, "name": "Foute Vrienden", "first_air_date": "2015-01-01",
         "poster_path": "/fv.jpg", "overview": "", "popularity": 20.0}
    ]}
    mock_resp.raise_for_status = MagicMock()

    mock_ext = MagicMock()
    mock_ext.json.return_value = {"tvdb_id": None, "imdb_id": None}
    mock_ext.raise_for_status = MagicMock()

    with patch("api.poster_manager.http_requests.get") as mock_get:
        mock_get.side_effect = [mock_resp, mock_ext]

        response = client.post(
            "/api/posterflow/unmatched-tmdb-search",
            json={"title": "Foute Vrienden (NL)", "year": 2015, "type": "show"},
        )

    assert response.status_code == 200
    # Verify the query used the cleaned title without "(NL)"
    search_call_params = mock_get.call_args_list[0][1]["params"]
    assert search_call_params["query"] == "Foute Vrienden"
    candidates = response.json()["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["title"] == "Foute Vrienden"


def test_unmatched_tmdb_search_returns_scored_candidates(client, test_db):
    """Should return candidates sorted by score with correct fields."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    mock_results = [
        _tmdb_movie_result(100, "The Batman", 2022, popularity=50.0),
        _tmdb_movie_result(200, "Batman Begins", 2005, popularity=80.0),
    ]

    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = {"results": mock_results}
    mock_search_resp.raise_for_status = MagicMock()

    mock_ext_resp = MagicMock()
    mock_ext_resp.status_code = 200
    mock_ext_resp.json.return_value = {"imdb_id": "tt1234567", "tvdb_id": None}
    mock_ext_resp.raise_for_status = MagicMock()

    with patch("api.poster_manager.http_requests.get") as mock_get:
        mock_get.side_effect = [mock_search_resp, mock_ext_resp, mock_ext_resp]

        response = client.post(
            "/api/posterflow/unmatched-tmdb-search",
            json={"title": "The Batman", "year": 2022, "type": "movie"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "candidates" in data
    candidates = data["candidates"]
    assert len(candidates) == 2

    # Exact title + year match should be first
    assert candidates[0]["title"] == "The Batman"
    assert candidates[0]["year"] == 2022
    assert candidates[0]["tmdb_id"] == 100
    assert candidates[0]["media_type"] == "movie"
    assert candidates[0]["match_reason"] == "exact"
    assert "poster_url" in candidates[0]
    assert candidates[0]["poster_url"].startswith("https://image.tmdb.org")


def test_unmatched_tmdb_search_show_maps_to_tv_endpoint(client, test_db):
    """Should call /search/tv endpoint for show type."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [
        {"id": 1, "name": "Breaking Bad", "first_air_date": "2008-01-20",
         "poster_path": "/bb.jpg", "overview": "", "popularity": 99.0}
    ]}
    mock_resp.raise_for_status = MagicMock()

    mock_ext = MagicMock()
    mock_ext.json.return_value = {"tvdb_id": 81189, "imdb_id": "tt0903747"}
    mock_ext.raise_for_status = MagicMock()

    with patch("api.poster_manager.http_requests.get") as mock_get:
        mock_get.side_effect = [mock_resp, mock_ext]

        response = client.post(
            "/api/posterflow/unmatched-tmdb-search",
            json={"title": "Breaking Bad", "year": 2008, "type": "show"},
        )

    assert response.status_code == 200
    # Verify the search URL used the /search/tv path
    search_call_url = mock_get.call_args_list[0][0][0]
    assert "/search/tv" in search_call_url
    candidates = response.json()["candidates"]
    assert candidates[0]["tvdb_id"] == 81189


def test_unmatched_tmdb_search_returns_502_on_tmdb_failure(client, test_db):
    """Should return 502 when TMDB API call fails."""
    import requests as real_requests
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    with patch("api.poster_manager.http_requests.get") as mock_get:
        mock_get.side_effect = real_requests.RequestException("connection refused")

        response = client.post(
            "/api/posterflow/unmatched-tmdb-search",
            json={"title": "Inception", "year": 2010, "type": "movie"},
        )

    assert response.status_code == 502
    assert "TMDB search failed" in response.json()["detail"]


def test_unmatched_tmdb_search_collection_skips_external_ids(client, test_db):
    """Collections should not call /external_ids endpoint."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [
        {"id": 10, "name": "Avengers Collection", "poster_path": "/av.jpg",
         "overview": "", "popularity": 30.0}
    ]}
    mock_resp.raise_for_status = MagicMock()

    with patch("api.poster_manager.http_requests.get") as mock_get:
        mock_get.return_value = mock_resp

        response = client.post(
            "/api/posterflow/unmatched-tmdb-search",
            json={"title": "Avengers Collection", "type": "collection"},
        )

    assert response.status_code == 200
    # Only 1 call: the search. No external_ids call for collections.
    assert mock_get.call_count == 1
    candidates = response.json()["candidates"]
    assert candidates[0]["tvdb_id"] is None
    assert candidates[0]["imdb_id"] is None
    assert candidates[0]["media_type"] == "collection"


def test_unmatched_tmdb_search_resolves_foreign_title_by_tvdb_id(client, test_db):
    """A show whose TVDB English name isn't on TMDB should still resolve via tvdb_id /find."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "/search/tv" in url:
            resp.json.return_value = {"results": []}  # English name finds nothing
        elif "/find/" in url:
            resp.json.return_value = {"movie_results": [], "tv_results": [
                {"id": 117057, "name": "Wer stiehlt mir die Show?",
                 "first_air_date": "2017-09-01", "poster_path": "/x.jpg",
                 "overview": "", "popularity": 5.0}
            ]}
        elif "/external_ids" in url:
            resp.json.return_value = {"tvdb_id": 369137, "imdb_id": "tt12345"}
        else:
            resp.json.return_value = {}
        return resp

    with patch("api.poster_manager.http_requests.get", side_effect=fake_get) as mock_get:
        response = client.post(
            "/api/posterflow/unmatched-tmdb-search",
            json={"title": "Who Steals the Show", "year": 2017, "type": "show", "tvdb_id": 369137},
        )

    assert response.status_code == 200
    candidates = response.json()["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["tmdb_id"] == 117057
    assert candidates[0]["auto_matched"] is True
    assert candidates[0]["match_reason"] == "id_exact"
    assert candidates[0]["title"] == "Wer stiehlt mir die Show?"
    # /find was hit with the numeric tvdb_id as the external source
    find_calls = [c for c in mock_get.call_args_list if "/find/369137" in c[0][0]]
    assert find_calls
    assert find_calls[0][1]["params"]["external_source"] == "tvdb_id"


def test_unmatched_tmdb_search_pins_id_match_and_dedupes(client, test_db):
    """A carried tmdb_id should pin the exact entity first and drop its fuzzy duplicate."""
    test_db.add(Setting(key="tmdb_api_key", value="fake_key"))
    test_db.commit()

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "/search/movie" in url:
            resp.json.return_value = {"results": [
                _tmdb_movie_result(100, "The Batman", 2022, popularity=50.0),
                _tmdb_movie_result(200, "Batman Begins", 2005, popularity=80.0),
            ]}
        elif "/external_ids" in url:
            resp.json.return_value = {"imdb_id": "tt0", "tvdb_id": None}
        elif url.rstrip("/").endswith("/movie/100"):
            resp.json.return_value = _tmdb_movie_result(100, "The Batman", 2022)
        else:
            resp.json.return_value = {}
        return resp

    with patch("api.poster_manager.http_requests.get", side_effect=fake_get):
        response = client.post(
            "/api/posterflow/unmatched-tmdb-search",
            json={"title": "The Batman", "year": 2022, "type": "movie", "tmdb_id": 100},
        )

    assert response.status_code == 200
    candidates = response.json()["candidates"]
    # id match pinned first, deduped from the fuzzy results (id 100 not repeated)
    assert candidates[0]["tmdb_id"] == 100
    assert candidates[0]["auto_matched"] is True
    assert candidates[0]["match_reason"] == "id_exact"
    assert [c["tmdb_id"] for c in candidates] == [100, 200]
    assert candidates[1]["auto_matched"] is False



    """Rename should fail when destination is missing from payload config."""
    response = client.post("/api/posterflow/rename", json={"config": {}})
    assert response.status_code == 400
    assert "Destination directory not specified" in response.json()["detail"]


def test_rename_rejects_without_drive_priority(client):
    """Rename should fail when drive priority is not configured."""
    response = client.post(
        "/api/posterflow/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 400
    assert "No drive priority configured" in response.json()["detail"]


def test_rename_rejects_invalid_drive_priority_json(client, test_db):
    """Rename should fail when drive priority setting is invalid JSON."""
    test_db.add(Setting(key="poster_drive_priority", value="{invalid-json"))
    test_db.commit()

    response = client.post(
        "/api/posterflow/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 400
    assert "Drive priority configuration is invalid" in response.json()["detail"]


def test_rename_rejects_when_priority_drives_not_subscribed(client, test_db):
    """Rename should fail when priority drives exist but none are subscribed."""
    drive = Drive(
        name="Unsubscribed Drive",
        drive_id="drive-unsubscribed-1",
        style_type="MM2K",
        subscribed=False,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    test_db.add(Setting(key="poster_drive_priority", value=f'{{"drive_ids": [{drive.id}]}}'))
    test_db.commit()

    response = client.post(
        "/api/posterflow/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 400
    assert "No drives in priority list are subscribed" in response.json()["detail"]


def test_border_replacer_rejects_when_destination_missing(client):
    """Border replacer should fail when destination setting is not configured."""
    response = client.post("/api/posterflow/border-replacer/run", json={})
    assert response.status_code == 400
    assert "No destination directory configured" in response.json()["detail"]


def test_border_replacer_rejects_when_destination_path_missing(client, test_db):
    """Border replacer should fail when destination setting path does not exist."""
    missing_path = "/tmp/posterflow-test-path-does-not-exist"
    test_db.add(Setting(key="poster_destination", value=missing_path))
    test_db.commit()

    response = client.post("/api/posterflow/border-replacer/run", json={})
    assert response.status_code == 400
    assert "Destination directory does not exist" in response.json()["detail"]


def test_rename_starts_successfully_with_valid_priority(client, test_db, monkeypatch):
    """Rename should start and create a job when config and priority are valid."""
    drive = Drive(
        name="Subscribed Drive",
        drive_id="drive-subscribed-1",
        style_type="MM2K",
        subscribed=True,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    test_db.add(Setting(key="poster_drive_priority", value=f'{{"drive_ids": [{drive.id}]}}'))
    test_db.commit()

    def _noop_rename_job(job_id, config_data):
        return None

    monkeypatch.setattr("api.poster_manager.run_rename_background_job", _noop_rename_job)

    response = client.post(
        "/api/posterflow/rename",
        json={"config": {"destination": "/tmp/posters"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert isinstance(data["job_id"], int)

    job = test_db.query(Job).filter(Job.id == data["job_id"]).first()
    assert job is not None
    assert job.job_type == "Poster Renamer"


def test_border_replacer_starts_successfully(client, test_db, monkeypatch, tmp_path):
    """Border replacer should start and create a job when destination exists."""
    test_db.add(Setting(key="poster_destination", value=str(tmp_path)))
    test_db.commit()

    def _noop_border_job(job_id, dry_run, mode):
        return None

    monkeypatch.setattr("api.poster_manager.run_border_replacer_background_job", _noop_border_job)

    response = client.post(
        "/api/posterflow/border-replacer/run",
        json={"dry_run": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert isinstance(data["job_id"], int)

    job = test_db.query(Job).filter(Job.id == data["job_id"]).first()
    assert job is not None
    assert job.job_type == "Border Replacer"


def test_flow_config_round_trip(client):
    """Flow config should save and then load with updated values."""
    payload = {
        "sync_drives": {"enabled": True, "stop_on_error": False},
        "rename_posters": {"enabled": True, "stop_on_error": True},
        "detect_unmatched": {"enabled": False, "stop_on_error": False},
        "border_replacer": {"enabled": True, "stop_on_error": False},
        "plex_upload": {"enabled": False, "stop_on_error": False},
    }

    save_response = client.post("/api/posterflow/flow/config", json=payload)
    assert save_response.status_code == 200
    assert save_response.json()["success"] is True

    get_response = client.get("/api/posterflow/flow/config")
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["border_replacer"]["enabled"] is True
    assert data["detect_unmatched"]["enabled"] is False


def test_flow_run_rejects_when_already_running(client, test_db):
    """Flow run should return 409 while a workflow job is already running."""
    import api.poster_manager as poster_manager_module

    existing_job = Job(
        job_type="Poster Workflow",
        status="running",
        progress=10,
        message="Running",
    )
    test_db.add(existing_job)
    test_db.commit()

    poster_manager_module._flow_running = True
    poster_manager_module._flow_started_at = datetime.now(timezone.utc)

    try:
        response = client.post("/api/posterflow/flow/run", json={"dry_run": False})
        assert response.status_code == 409
        assert "Workflow is already running" in response.json()["detail"]
    finally:
        poster_manager_module._flow_running = False
        poster_manager_module._flow_started_at = None


def test_flow_run_recovers_stale_lock_and_starts(client, test_db, monkeypatch):
    """Flow run should recover a stale lock when no running jobs exist and start successfully."""
    import api.poster_manager as poster_manager_module

    def _run_flow_now(job_id, dry_run, release_flow_lock):
        release_flow_lock()

    monkeypatch.setattr("api.poster_manager.run_flow_background_job", _run_flow_now)

    poster_manager_module._flow_running = True
    poster_manager_module._flow_started_at = datetime.now(timezone.utc)

    try:
        response = client.post("/api/posterflow/flow/run", json={"dry_run": True})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["job_id"], int)

        job = test_db.query(Job).filter(Job.id == data["job_id"]).first()
        assert job is not None
        assert job.job_type == "Poster Workflow"
        assert job.status in {"pending", "running"}

        for _ in range(50):
            if poster_manager_module._flow_running is False:
                break
            time.sleep(0.01)

        assert poster_manager_module._flow_running is False
        assert poster_manager_module._flow_started_at is None
    finally:
        poster_manager_module._flow_running = False
        poster_manager_module._flow_started_at = None


def test_flow_run_forces_release_after_long_lock_age(client, test_db, monkeypatch):
    """Flow run should force lock release when lock age exceeds threshold and then start."""
    import api.poster_manager as poster_manager_module

    existing_job = Job(
        job_type="Poster Workflow",
        status="running",
        progress=40,
        message="Long-running",
    )
    test_db.add(existing_job)
    test_db.commit()

    def _run_flow_now(job_id, dry_run, release_flow_lock):
        release_flow_lock()

    monkeypatch.setattr("api.poster_manager.run_flow_background_job", _run_flow_now)

    poster_manager_module._flow_running = True
    poster_manager_module._flow_started_at = datetime.now(timezone.utc) - timedelta(minutes=16)

    try:
        response = client.post("/api/posterflow/flow/run", json={"dry_run": False})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["job_id"], int)
    finally:
        poster_manager_module._flow_running = False
        poster_manager_module._flow_started_at = None


def test_flow_module_delegates_to_module_runners(test_db, monkeypatch):
    """Flow runner should orchestrate by delegating each enabled step to module runner entrypoints."""
    import modules.flow as flow_module

    call_order: list[str] = []
    rename_config: dict = {}

    monkeypatch.setattr("modules.flow.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.flow.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.flow.remove_job_log_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "modules.flow._promote_child_progress_to_parent",
        lambda _db, _parent_job, child_job_id, _start, _end, _fallback, runner, *runner_args: runner(child_job_id, *runner_args),
    )

    def _complete_child(child_job_id: int, step_name: str) -> None:
        child = test_db.query(Job).filter(Job.id == child_job_id).first()
        assert child is not None
        child.status = "completed"
        child.progress = 100
        child.message = f"{step_name} completed"
        test_db.commit()

    def _sync_runner(child_job_id: int, skip_discord: bool = False, **kwargs):
        call_order.append("sync_drives")
        _complete_child(child_job_id, "sync")
        return {"success": True}

    def _rename_runner(child_job_id: int, _config_data: dict, skip_discord: bool = False, **kwargs):
        call_order.append("rename_posters")
        rename_config.update(_config_data)
        _complete_child(child_job_id, "rename")

    def _border_runner(child_job_id: int, _dry_run: bool, _mode: str, **kwargs):
        call_order.append("border_replacer")
        _complete_child(child_job_id, "border")

    def _unmatched_runner(child_job_id: int, skip_discord: bool = False, **kwargs):
        call_order.append("detect_unmatched")
        _complete_child(child_job_id, "unmatched")

    monkeypatch.setattr("modules.flow.run_sync_all_job", _sync_runner)
    monkeypatch.setattr("modules.flow.run_rename_background_job", _rename_runner)
    monkeypatch.setattr("modules.flow.run_border_replacer_background_job", _border_runner)
    monkeypatch.setattr("modules.flow.run_unmatched_detection_background_job", _unmatched_runner)

    flow_config = {
        "sync_drives": {"enabled": True, "stop_on_error": True},
        "rename_posters": {"enabled": True, "stop_on_error": True},
        "border_replacer": {"enabled": True, "stop_on_error": True},
        "detect_unmatched": {"enabled": True, "stop_on_error": True},
    }
    test_db.add(Setting(key="poster_flow_config", value=json.dumps(flow_config)))

    workflow_job = Job(
        job_type="Poster Workflow",
        status="pending",
        progress=0,
        message="Queued",
    )
    test_db.add(workflow_job)
    test_db.commit()
    test_db.refresh(workflow_job)
    workflow_job_id = workflow_job.id

    flow_module.run_flow_background_job(workflow_job_id, dry_run=False)

    refreshed = test_db.query(Job).filter(Job.id == workflow_job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert call_order == [
        "sync_drives",
        "rename_posters",
        "border_replacer",
        "detect_unmatched",
    ]
    assert rename_config.get("auto_run_border") is False
    assert rename_config.get("skip_border_post_processing") is True


def test_flow_config_save_returns_500_when_storage_fails(client, monkeypatch):
    """Flow config save should return 500 when persistence layer raises."""
    import api.poster_manager as poster_manager_module

    def _fail_upsert(*_args, **_kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(poster_manager_module, "upsert_setting", _fail_upsert)

    payload = {
        "sync_drives": {"enabled": True, "stop_on_error": False},
        "rename_posters": {"enabled": True, "stop_on_error": True},
        "detect_unmatched": {"enabled": True, "stop_on_error": False},
        "border_replacer": {"enabled": False, "stop_on_error": False},
        "plex_upload": {"enabled": False, "stop_on_error": False},
    }

    response = client.post("/api/posterflow/flow/config", json=payload)
    assert response.status_code == 500
    assert "Error saving flow config" in response.json()["detail"]


def test_flow_run_returns_500_and_releases_lock_when_create_job_fails(client, monkeypatch):
    """Flow run should release lock and return 500 when job creation fails."""
    import api.poster_manager as poster_manager_module

    def _fail_create_job(*_args, **_kwargs):
        raise RuntimeError("simulated job creation failure")

    monkeypatch.setattr(poster_manager_module, "create_job", _fail_create_job)

    poster_manager_module._flow_running = False
    poster_manager_module._flow_started_at = None

    response = client.post("/api/posterflow/flow/run", json={"dry_run": False})
    assert response.status_code == 500
    assert "Failed to start workflow" in response.json()["detail"]

    assert poster_manager_module._flow_running is False
    assert poster_manager_module._flow_started_at is None


# ---------------------------------------------------------------------------
# Border preview endpoint test
# ---------------------------------------------------------------------------

def test_border_preview_renders_png(client):
    """The preview endpoint renders a PNG (placeholder base when no default/drive poster)."""
    preview = client.get(
        "/api/posterflow/border-replacer/preview",
        params={"style": "solid", "color": "#112233", "border_width": 26},
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"


def test_border_preview_passthrough_returns_original(client):
    """With passthrough=true the preview returns the untouched sample poster (no border)."""
    preview = client.get(
        "/api/posterflow/border-replacer/preview",
        params={"style": "solid", "border_width": 26, "passthrough": "true"},
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"


def _overlay_png_bytes(mode: str, size=(1000, 1500), transparent: bool = True) -> bytes:
    import io as _io
    from PIL import Image

    if mode == "RGBA":
        img = Image.new("RGBA", size, (255, 0, 0, 0 if transparent else 255))
    elif mode == "P":
        img = Image.new("P", size, 0)  # opaque palette, no transparency info
    else:
        img = Image.new(mode, size, (255, 0, 0))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_upload_overlay_accepts_transparent_png(client):
    resp = client.post(
        "/api/posterflow/border-replacer/overlays/upload",
        files={"file": ("test_frame_rgba.png", _overlay_png_bytes("RGBA"), "image/png")},
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "user"


def test_upload_overlay_rejects_opaque_palette_png(client):
    # An opaque palette PNG has no real transparency and must be rejected.
    resp = client.post(
        "/api/posterflow/border-replacer/overlays/upload",
        files={"file": ("test_frame_opaque.png", _overlay_png_bytes("P"), "image/png")},
    )
    assert resp.status_code == 400
    assert "transparency" in resp.json()["detail"].lower()


def test_upload_overlay_rejects_wrong_size(client):
    resp = client.post(
        "/api/posterflow/border-replacer/overlays/upload",
        files={"file": ("test_frame_small.png", _overlay_png_bytes("RGBA", size=(500, 500)), "image/png")},
    )
    assert resp.status_code == 400
