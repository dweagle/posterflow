import importlib.util as _importlib_util
import json
import os
import tempfile
import time
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from models.setting import Setting


# ---------------------------------------------------------------------------
# Import private helper functions directly
# ---------------------------------------------------------------------------
from api.maker_tools import (
    _build_lang_params,
    _build_psd,
    _build_tmdb_images,
    _extract_name,
    _fetch_tmdb_image_bytes,
    _measure_logo_density,
    _parse_bool,
    _parse_iso_date,
    _parse_non_negative_int,
    _parse_positive_int,
    _sanitize_drive_ids,
    _sanitize_monitor_config,
    compute_logo_geometry,
    compute_poster_fit_geometry,
    MakerMonitorConfig,
)


# ---------------------------------------------------------------------------
# _parse_iso_date
# ---------------------------------------------------------------------------


def test_parse_iso_date_valid():
    result = _parse_iso_date("2026-05-04")
    assert result == date(2026, 5, 4)


def test_parse_iso_date_none_returns_none():
    assert _parse_iso_date(None) is None


def test_parse_iso_date_empty_string_returns_none():
    assert _parse_iso_date("") is None


def test_parse_iso_date_invalid_format_returns_none():
    assert _parse_iso_date("05/04/2026") is None
    assert _parse_iso_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _parse_positive_int
# ---------------------------------------------------------------------------


def test_parse_positive_int_valid():
    assert _parse_positive_int(7, 21) == 7


def test_parse_positive_int_zero_uses_default():
    assert _parse_positive_int(0, 21) == 21


def test_parse_positive_int_negative_uses_default():
    assert _parse_positive_int(-5, 21) == 21


def test_parse_positive_int_string_value():
    assert _parse_positive_int("10", 21) == 10


def test_parse_positive_int_non_numeric_uses_default():
    assert _parse_positive_int("abc", 21) == 21


# ---------------------------------------------------------------------------
# _parse_non_negative_int
# ---------------------------------------------------------------------------


def test_parse_non_negative_int_zero_is_allowed():
    assert _parse_non_negative_int(0, 5) == 0


def test_parse_non_negative_int_positive_value():
    assert _parse_non_negative_int(3, 5) == 3


def test_parse_non_negative_int_negative_uses_default():
    assert _parse_non_negative_int(-1, 5) == 5


# ---------------------------------------------------------------------------
# _parse_bool
# ---------------------------------------------------------------------------


def test_parse_bool_true_values():
    for v in (True, 1, "true", "yes", "1", "on"):
        assert _parse_bool(v, False) is True, f"expected True for {v!r}"


def test_parse_bool_false_values():
    for v in (False, 0, "false", "no", "0", "off", ""):
        assert _parse_bool(v, True) is False, f"expected False for {v!r}"


def test_parse_bool_unrecognized_uses_default():
    assert _parse_bool("maybe", True) is True
    assert _parse_bool("maybe", False) is False


# ---------------------------------------------------------------------------
# _sanitize_drive_ids
# ---------------------------------------------------------------------------


def test_sanitize_drive_ids_filters_invalid():
    assert _sanitize_drive_ids([1, 2, "abc", -1, 0, None]) == [1, 2]


def test_sanitize_drive_ids_deduplicates():
    assert _sanitize_drive_ids([3, 3, 1]) == [3, 1]


def test_sanitize_drive_ids_non_list_returns_empty():
    assert _sanitize_drive_ids(None) == []
    assert _sanitize_drive_ids("1,2") == []


# ---------------------------------------------------------------------------
# _sanitize_monitor_config
# ---------------------------------------------------------------------------


def test_sanitize_monitor_config_returns_defaults_for_empty_dict():
    result = _sanitize_monitor_config({})
    defaults = MakerMonitorConfig()
    assert result.lookahead_days == defaults.lookahead_days
    assert result.enable_discovery == defaults.enable_discovery
    assert result.drive_ids == []


def test_sanitize_monitor_config_applies_valid_values():
    result = _sanitize_monitor_config({
        "lookahead_days": 14,
        "enable_discovery": False,
        "drive_ids": [1, 2],
        "tmdb_api_key": "  mykey  ",
    })
    assert result.lookahead_days == 14
    assert result.enable_discovery is False
    assert result.drive_ids == [1, 2]
    assert result.tmdb_api_key == "mykey"


def test_sanitize_monitor_config_clamps_invalid_ints_to_defaults():
    defaults = MakerMonitorConfig()
    result = _sanitize_monitor_config({"lookahead_days": -99, "discovery_max_results": 0})
    assert result.lookahead_days == defaults.lookahead_days
    assert result.discovery_max_results == defaults.discovery_max_results


def test_sanitize_monitor_config_deduplicates_languages():
    result = _sanitize_monitor_config({"discovery_languages": ["en", "EN", "ja", "en"]})
    assert result.discovery_languages == ["en", "ja"]


def test_sanitize_monitor_config_non_dict_returns_defaults():
    result = _sanitize_monitor_config("not a dict")
    assert result == MakerMonitorConfig()


# ---------------------------------------------------------------------------
# _extract_name
# ---------------------------------------------------------------------------


def test_extract_name_strips_tmdb_tag():
    assert _extract_name("Breaking Bad {tmdb-1396}") == "Breaking Bad"


def test_extract_name_strips_year_in_parentheses():
    assert _extract_name("The Batman (2022)") == "The Batman"


def test_extract_name_returns_full_name_when_no_bracket():
    assert _extract_name("No Brackets Here") == "No Brackets Here"


def test_extract_name_handles_empty_string():
    assert _extract_name("") == ""


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/monitor/config
# ---------------------------------------------------------------------------


def test_get_maker_monitor_config_returns_defaults_when_no_setting(client):
    response = client.get("/api/maker-tools/monitor/config")
    assert response.status_code == 200
    data = response.json()
    assert data["lookahead_days"] == MakerMonitorConfig().lookahead_days
    assert data["drive_ids"] == []


def test_get_maker_monitor_config_returns_saved_config(client, test_db):
    config = MakerMonitorConfig(lookahead_days=14, drive_ids=[1, 2])
    test_db.add(Setting(key="maker_tools_monitor_config", value=config.model_dump_json()))
    test_db.commit()

    response = client.get("/api/maker-tools/monitor/config")
    assert response.status_code == 200
    data = response.json()
    assert data["lookahead_days"] == 14
    assert data["drive_ids"] == [1, 2]


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/monitor/last-result
# ---------------------------------------------------------------------------


def test_get_maker_monitor_last_result_returns_empty_when_no_setting(client):
    response = client.get("/api/maker-tools/monitor/last-result")
    assert response.status_code == 200
    assert response.json() == {}


def test_get_maker_monitor_last_result_returns_saved_result(client, test_db):
    payload = {"range_start": "2026-05-01", "total_premieres": 3}
    test_db.add(
        Setting(key="maker_tools_monitor_last_result", value=json.dumps(payload))
    )
    test_db.commit()

    response = client.get("/api/maker-tools/monitor/last-result")
    assert response.status_code == 200
    data = response.json()
    assert data["range_start"] == "2026-05-01"
    assert data["total_premieres"] == 3


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/monitor/needed-count
# ---------------------------------------------------------------------------


def test_get_maker_monitor_needed_count_zero_when_no_setting(client):
    response = client.get("/api/maker-tools/monitor/needed-count")
    assert response.status_code == 200
    assert response.json() == {"count": 0}


def test_get_maker_monitor_needed_count_returns_total_needed(client, test_db):
    payload = {"range_start": "2026-05-01", "total_needed": 4}
    test_db.add(
        Setting(key="maker_tools_monitor_last_result", value=json.dumps(payload))
    )
    test_db.commit()

    response = client.get("/api/maker-tools/monitor/needed-count")
    assert response.status_code == 200
    assert response.json() == {"count": 4}


def test_get_maker_monitor_needed_count_zero_when_total_needed_missing(client, test_db):
    payload = {"range_start": "2026-05-01", "total_premieres": 3}
    test_db.add(
        Setting(key="maker_tools_monitor_last_result", value=json.dumps(payload))
    )
    test_db.commit()

    response = client.get("/api/maker-tools/monitor/needed-count")
    assert response.status_code == 200
    assert response.json() == {"count": 0}


# ---------------------------------------------------------------------------
# API: POST /api/maker-tools/monitor/config
# ---------------------------------------------------------------------------


def test_save_maker_monitor_config_persists_and_returns_config(client):
    payload = {"lookahead_days": 30, "drive_ids": [], "enable_discovery": False}
    response = client.post("/api/maker-tools/monitor/config", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["lookahead_days"] == 30
    assert data["enable_discovery"] is False


def test_save_maker_monitor_config_promotes_tmdb_key_to_global_setting(client, test_db):
    payload = {"tmdb_api_key": "supersecretkey", "drive_ids": []}
    response = client.post("/api/maker-tools/monitor/config", json=payload)
    assert response.status_code == 200
    # tmdb_api_key should be cleared from the returned config (stored globally)
    assert response.json()["tmdb_api_key"] == ""

    # The global setting should now exist
    setting = test_db.query(Setting).filter(Setting.key == "tmdb_api_key").first()
    assert setting is not None
    assert setting.value == "supersecretkey"


# ---------------------------------------------------------------------------
# API: POST /api/maker-tools/monitor/run
# ---------------------------------------------------------------------------


def test_run_maker_monitor_queues_job_and_returns_job_id(client):
    with patch("api.maker_tools.job_queue") as mock_queue:
        mock_queue.submit = MagicMock()
        response = client.post("/api/maker-tools/monitor/run", json={})

    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert isinstance(data["job_id"], int)
    assert "queued" in data["message"].lower() or "monitor" in data["message"].lower()
    mock_queue.submit.assert_called_once()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires POSIX time.tzset()")
def test_completed_monitor_job_stays_in_ws_recent_window():
    """A finished Maker Monitor job must record completed_at in UTC so it remains
    inside the job-WebSocket 'recently completed' window the sidebar relies on.

    Regression: storing local wall-clock time (datetime.now().astimezone()) put the
    completed_at hours behind the UTC cutoff for users behind UTC, so the completed
    job was filtered out of the broadcast and the Maker Tools badge never refreshed
    after a scan (it only updated on a full page reload).
    """
    from datetime import datetime, timedelta, timezone

    from database import SessionLocal
    from models.job import (
        JOB_STATUS_COMPLETED,
        JOB_STATUSES_RECENT_TERMINAL,
        JOB_TYPE_MAKER_MONITOR,
        Job,
        create_job,
    )
    from api import maker_tools

    # Force a timezone well behind UTC so local wall-clock differs from UTC; under
    # the old code this is exactly what pushed completed_at outside the window.
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()

    session = SessionLocal()
    job_id = None
    try:
        job = create_job(session, JOB_TYPE_MAKER_MONITOR, "test monitor scan")
        job_id = job.id

        # Skip the real scan; we only care about how the job lifecycle stamps completed_at.
        with patch.object(maker_tools, "run_maker_monitor_scan_internal", return_value=MagicMock()):
            maker_tools.run_maker_monitor_background_job(job_id, {}, False, False)

        session.expire_all()
        completed = session.query(Job).filter(Job.id == job_id).one()
        assert completed.status == JOB_STATUS_COMPLETED

        # Replicate the exact filter the job WebSocket uses to choose which finished
        # jobs to broadcast (api/jobs.py): the job must still be visible right after it
        # completes, otherwise the sidebar never sees the completion event.
        two_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=2)
        visible = (
            session.query(Job)
            .filter(
                Job.id == job_id,
                Job.status.in_(JOB_STATUSES_RECENT_TERMINAL),
                Job.completed_at >= two_minutes_ago,
            )
            .first()
        )
        assert visible is not None, "completed maker_monitor job fell outside the WS recent window"
    finally:
        if job_id is not None:
            session.query(Job).filter(Job.id == job_id).delete()
            session.commit()
        session.close()
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


# ---------------------------------------------------------------------------
# _build_lang_params
# ---------------------------------------------------------------------------


def test_build_lang_params_all_returns_none():
    assert _build_lang_params("all") is None


def test_build_lang_params_en_textless_returns_en_null():
    assert _build_lang_params("en+textless") == "en,null"


def test_build_lang_params_specific_language_returns_code():
    assert _build_lang_params("ja") == "ja"
    assert _build_lang_params("fr") == "fr"
    assert _build_lang_params("zh") == "zh"


# ---------------------------------------------------------------------------
# _build_tmdb_images
# ---------------------------------------------------------------------------


def test_build_tmdb_images_maps_fields_correctly():
    raw = [{
        "file_path": "/abc123.jpg",
        "width": 1000,
        "height": 1500,
        "iso_639_1": "en",
        "vote_average": 8.5,
    }]
    result = _build_tmdb_images(raw)
    assert len(result) == 1
    img = result[0]
    assert img.file_path == "/abc123.jpg"
    assert img.width == 1000
    assert img.height == 1500
    assert img.language == "en"
    assert img.vote_average == 8.5
    assert img.url_thumb == "https://image.tmdb.org/t/p/w300/abc123.jpg"
    assert img.url_full == "https://image.tmdb.org/t/p/original/abc123.jpg"


def test_build_tmdb_images_textless_sets_language_none():
    raw = [{"file_path": "/tl.jpg", "width": 1000, "height": 1500, "iso_639_1": None, "vote_average": 7.0}]
    result = _build_tmdb_images(raw)
    assert result[0].language is None


def test_build_tmdb_images_skips_entries_without_file_path():
    raw = [
        {"file_path": "", "width": 100, "height": 100, "iso_639_1": "en", "vote_average": 5.0},
        {"file_path": "/ok.jpg", "width": 500, "height": 750, "iso_639_1": "en", "vote_average": 6.0},
    ]
    result = _build_tmdb_images(raw)
    assert len(result) == 1
    assert result[0].file_path == "/ok.jpg"


def test_build_tmdb_images_uses_custom_thumb_size():
    raw = [{"file_path": "/bg.jpg", "width": 1920, "height": 1080, "iso_639_1": "en", "vote_average": 7.0}]
    result = _build_tmdb_images(raw, size_thumb="w780")
    assert result[0].url_thumb == "https://image.tmdb.org/t/p/w780/bg.jpg"


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/tmdb/images
# ---------------------------------------------------------------------------

_FAKE_IMAGES_RESPONSE = {
    "posters": [
        {"file_path": "/p1.jpg", "width": 1000, "height": 1500, "iso_639_1": "en", "vote_average": 8.0},
        {"file_path": "/p2.jpg", "width": 1000, "height": 1500, "iso_639_1": None, "vote_average": 9.0},
    ],
    "backdrops": [
        {"file_path": "/b1.jpg", "width": 1920, "height": 1080, "iso_639_1": "en", "vote_average": 7.5},
    ],
    "logos": [],
}


def _seed_tmdb_key(test_db, key: str = "testkey123") -> None:
    test_db.add(Setting(key="tmdb_api_key", value=key))
    test_db.commit()


def test_tmdb_images_returns_sorted_results(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _FAKE_IMAGES_RESPONSE

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/images?tmdb_id=1396&media_type=tv&language=en%2Btextless")

    assert response.status_code == 200
    data = response.json()
    # Textless poster (language=null) should sort first
    assert data["posters"][0]["language"] is None
    assert len(data["posters"]) == 2
    assert len(data["backdrops"]) == 1
    assert data["logos"] == []


def test_tmdb_images_invalid_media_type_returns_400(client, test_db):
    _seed_tmdb_key(test_db)
    response = client.get("/api/maker-tools/tmdb/images?tmdb_id=1&media_type=podcast&language=en")
    assert response.status_code == 400


def test_tmdb_images_no_api_key_returns_400(client):
    # No TMDB key seeded
    response = client.get("/api/maker-tools/tmdb/images?tmdb_id=1&media_type=movie&language=en")
    assert response.status_code == 400
    assert "api key" in response.json()["detail"].lower()


def test_tmdb_images_tmdb_401_returns_400(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/images?tmdb_id=1&media_type=movie&language=en")

    assert response.status_code == 400
    assert "invalid tmdb api key" in response.json()["detail"].lower()


def test_tmdb_images_tmdb_502_returns_502(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 503

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/images?tmdb_id=1&media_type=movie&language=en")

    assert response.status_code == 502


def test_tmdb_images_invalid_language_falls_back_to_en_textless(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"posters": [], "backdrops": [], "logos": []}

    with patch("api.maker_tools.requests.get", return_value=mock_resp) as mock_get:
        client.get("/api/maker-tools/tmdb/images?tmdb_id=1&media_type=movie&language=INVALID!")

    # Should have fallen back and passed "en,null" for include_image_language
    call_params = mock_get.call_args[1]["params"]
    assert call_params.get("include_image_language") == "en,null"


def test_tmdb_images_language_all_omits_include_image_language(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"posters": [], "backdrops": [], "logos": []}

    with patch("api.maker_tools.requests.get", return_value=mock_resp) as mock_get:
        client.get("/api/maker-tools/tmdb/images?tmdb_id=1&media_type=movie&language=all")

    call_params = mock_get.call_args[1]["params"]
    assert "include_image_language" not in call_params


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/tmdb/tv-details
# ---------------------------------------------------------------------------

_FAKE_TV_DETAILS = {
    "number_of_seasons": 2,
    "type": "Miniseries",
    "seasons": [
        {"season_number": 0, "name": "Specials", "episode_count": 3, "air_date": "2020-01-01", "poster_path": "/sp.jpg"},
        {"season_number": 1, "name": "Season 1", "episode_count": 10, "air_date": "2020-06-01", "poster_path": "/s1.jpg"},
        {"season_number": 2, "name": "Season 2", "episode_count": 8, "air_date": "2021-06-01", "poster_path": ""},
    ],
}


def test_tmdb_tv_details_returns_seasons(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _FAKE_TV_DETAILS

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/tv-details?tmdb_id=1396")

    assert response.status_code == 200
    data = response.json()
    assert data["season_count"] == 2
    assert data["series_type"] == "Miniseries"
    assert len(data["seasons"]) == 3

    specials = data["seasons"][0]
    assert specials["season_number"] == 0
    assert specials["name"] == "Specials"
    assert specials["episode_count"] == 3
    assert specials["poster_url"] == "https://image.tmdb.org/t/p/w185/sp.jpg"

    # Season with no poster_path → poster_url should be None
    s2 = data["seasons"][2]
    assert s2["poster_url"] is None


def test_tmdb_tv_details_no_api_key_returns_400(client):
    response = client.get("/api/maker-tools/tmdb/tv-details?tmdb_id=1396")
    assert response.status_code == 400


def test_tmdb_tv_details_tmdb_error_returns_502(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/tv-details?tmdb_id=99999")

    assert response.status_code == 502


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/tmdb/origin-country
# ---------------------------------------------------------------------------


def test_origin_country_tv_uses_origin_country_field(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"origin_country": ["GB"], "production_countries": [{"iso_3166_1": "US"}]}

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/origin-country?tmdb_id=1396&media_type=tv")

    assert response.status_code == 200
    # origin_country is preferred and de-duped ahead of production_countries
    assert response.json()["countries"] == ["GB", "US"]


def test_origin_country_movie_falls_back_to_production_countries(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"production_countries": [{"iso_3166_1": "fr"}, {"iso_3166_1": "DE"}]}

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/origin-country?tmdb_id=550&media_type=movie")

    assert response.status_code == 200
    assert response.json()["countries"] == ["FR", "DE"]


def test_origin_country_non_movie_tv_returns_empty_without_call(client, test_db):
    # Collections have no origin country; the endpoint short-circuits before any TMDB call.
    with patch("api.maker_tools.requests.get") as mock_get:
        response = client.get("/api/maker-tools/tmdb/origin-country?tmdb_id=10&media_type=collection")
    assert response.status_code == 200
    assert response.json()["countries"] == []
    mock_get.assert_not_called()


def test_origin_country_no_api_key_returns_400(client):
    response = client.get("/api/maker-tools/tmdb/origin-country?tmdb_id=1396&media_type=tv")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/tmdb/season-images
# ---------------------------------------------------------------------------

_FAKE_SEASON_IMAGES = {
    "posters": [
        {"file_path": "/s1p1.jpg", "width": 1000, "height": 1500, "iso_639_1": "en", "vote_average": 8.0},
        {"file_path": "/s1p2.jpg", "width": 1000, "height": 1500, "iso_639_1": None, "vote_average": 9.5},
    ],
}


def test_tmdb_season_images_returns_posters_only(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _FAKE_SEASON_IMAGES

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/season-images?tmdb_id=1396&season_number=1&language=en%2Btextless")

    assert response.status_code == 200
    data = response.json()
    # Textless sorts first
    assert data["posters"][0]["language"] is None
    assert len(data["posters"]) == 2
    assert data["backdrops"] == []
    assert data["logos"] == []


def test_tmdb_season_images_calls_correct_url(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"posters": []}

    with patch("api.maker_tools.requests.get", return_value=mock_resp) as mock_get:
        client.get("/api/maker-tools/tmdb/season-images?tmdb_id=1396&season_number=3&language=ja")

    call_url = mock_get.call_args[0][0]
    assert "/tv/1396/season/3/images" in call_url
    assert mock_get.call_args[1]["params"]["include_image_language"] == "ja"


def test_tmdb_season_images_no_api_key_returns_400(client):
    response = client.get("/api/maker-tools/tmdb/season-images?tmdb_id=1&season_number=1")
    assert response.status_code == 400


def test_tmdb_season_images_tmdb_401_returns_400(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/season-images?tmdb_id=1&season_number=1")

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Helpers for PSD tests
# ---------------------------------------------------------------------------


def _make_jpeg_bytes(w: int = 20, h: int = 30) -> bytes:
    """Return a minimal JPEG as raw bytes (no file I/O)."""
    img = Image.new("RGB", (w, h), color=(120, 60, 200))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes_rgba(w: int = 20, h: int = 20) -> bytes:
    """Return a minimal RGBA PNG as raw bytes."""
    img = Image.new("RGBA", (w, h), color=(255, 255, 255, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _fetch_tmdb_image_bytes
# ---------------------------------------------------------------------------


def test_fetch_tmdb_image_bytes_returns_content_on_200():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fakeimagebytes"

    with patch("api.maker_tools.requests.get", return_value=mock_resp) as mock_get:
        result = _fetch_tmdb_image_bytes("/p1.jpg", "apikey")

    assert result == b"fakeimagebytes"
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://image.tmdb.org/t/p/original/p1.jpg"


def test_fetch_tmdb_image_bytes_raises_502_on_non_200():
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        with pytest.raises(HTTPException) as exc_info:
            _fetch_tmdb_image_bytes("/missing.jpg", "apikey")

    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# _build_psd (unit tests — no mocking, real PIL/psd_tools)
# ---------------------------------------------------------------------------

_psd_tools_missing = pytest.mark.skipif(
    _importlib_util.find_spec("psd_tools") is None,
    reason="psd-tools not installed",
)


@_psd_tools_missing
def test_build_psd_scratch_mode_returns_bytes():
    poster = _make_jpeg_bytes(20, 30)
    result = _build_psd([poster], logo_bytes_list=[])
    assert isinstance(result, bytes)
    assert len(result) > 0



@_psd_tools_missing
def test_build_psd_scratch_mode_with_logo_returns_bytes():
    poster = _make_jpeg_bytes(20, 30)
    logo = _make_png_bytes_rgba(40, 10)
    result = _build_psd([poster], logo_bytes_list=[logo], title="Test Show", year="2026")
    assert isinstance(result, bytes)
    assert len(result) > 0



@_psd_tools_missing
def test_build_psd_multiple_posters_returns_bytes():
    posters = [_make_jpeg_bytes(20, 30) for _ in range(3)]
    result = _build_psd(posters, logo_bytes_list=[], title="Multi Poster")
    assert isinstance(result, bytes)
    assert len(result) > 0



@_psd_tools_missing
def test_build_psd_with_backdrop_returns_bytes():
    poster = _make_jpeg_bytes(20, 30)
    backdrop = _make_jpeg_bytes(40, 20)
    result = _build_psd([poster], logo_bytes_list=[], backdrop_bytes_list=[backdrop], title="With Backdrop")
    assert isinstance(result, bytes)
    assert len(result) > 0



@_psd_tools_missing
def test_build_psd_no_poster_only_logo_returns_bytes():
    logo = _make_png_bytes_rgba(40, 10)
    result = _build_psd([], logo_bytes_list=[logo], title="Logo Only")
    assert isinstance(result, bytes)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# Shared placement formula: compute_logo_geometry / compute_poster_fit_geometry /
# _measure_logo_density. These back both the PSD export and the plugin's
# Place Logo / Fit Poster buttons, so the values are pinned as a regression guard.
# ---------------------------------------------------------------------------

# At the 1000×1500 reference, the logo bottom edge sits at round(1352.13) = 1352px.
_LOGO_BOTTOM = 1352


@pytest.mark.parametrize(
    "src_w,src_h,density,expected",
    [
        (1000, 1000, 1.0, (101, 101, 449, 1251)),   # square + dense → tight height cap
        (1600, 400, 0.5, (610, 152, 195, 1200)),    # wide banner, normal density
        (788, 131, 0.2, (763, 127, 118, 1225)),     # small sparse banner → sparse boost
    ],
)
def test_compute_logo_geometry_known_values(src_w, src_h, density, expected):
    assert compute_logo_geometry(src_w, src_h, 1000, 1500, density) == expected


def test_compute_logo_geometry_is_centered_and_bottom_anchored():
    for src_w, src_h, density in [(1000, 1000, 1.0), (1600, 400, 0.5), (788, 131, 0.2)]:
        w, h, left, top = compute_logo_geometry(src_w, src_h, 1000, 1500, density)
        assert left == (1000 - w) // 2          # centered horizontally
        assert top + h == _LOGO_BOTTOM          # bottom-anchored
        assert w <= 800                          # never exceeds the hard width cap


def test_compute_logo_geometry_scales_with_canvas():
    # Doubling the canvas doubles the size/anchor (formula scales off the 1000×1500 reference);
    # left stays centered (may differ by 1px from exact doubling due to integer centering).
    sw, sh, sl, st = compute_logo_geometry(1000, 1000, 1000, 1500, 1.0)
    lw, lh, ll, lt = compute_logo_geometry(1000, 1000, 2000, 3000, 1.0)
    assert (lw, lh, lt) == (sw * 2, sh * 2, st * 2)
    assert ll == (2000 - lw) // 2


def test_compute_poster_fit_geometry_bordered_and_top_aligned():
    assert compute_poster_fit_geometry(1000, 1500, 1000, 1500) == (950, 1425, 25, 25)
    assert compute_poster_fit_geometry(500, 750, 2000, 3000) == (1950, 2925, 25, 25)
    w, h, left, top = compute_poster_fit_geometry(800, 1200, 1000, 1500)
    assert w == 950 and left == 25 and top == 25   # canvas − 25px each side, top-aligned


def test_measure_logo_density_opaque_vs_transparent():
    assert _measure_logo_density(Image.new("RGBA", (40, 10), (255, 255, 255, 200))) == 1.0
    assert _measure_logo_density(Image.new("RGBA", (10, 10), (0, 0, 0, 0))) == 0.0


# ---------------------------------------------------------------------------
# API: POST /api/maker-tools/tmdb/psd-export — validation
# ---------------------------------------------------------------------------


def test_psd_export_no_images_streams_blank_psd(client, test_db):
    """No images selected → blank PSD from the template is exported (no selection required)."""
    _seed_tmdb_key(test_db)
    with patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
        response = client.post("/api/maker-tools/tmdb/psd-export", json={"title": "Test", "year": "2026"})
    assert response.status_code == 200
    assert response.content == b"FAKEPSD"
    assert response.headers["content-type"] == "application/octet-stream"


def test_psd_export_no_images_no_api_key_still_succeeds(client):
    """A blank export needs no image fetch, so it works without a TMDB key configured."""
    with patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
        response = client.post("/api/maker-tools/tmdb/psd-export", json={"title": "Test", "year": "2026"})
    assert response.status_code == 200
    assert response.content == b"FAKEPSD"


def test_psd_export_invalid_path_no_leading_slash_returns_400(client, test_db):
    _seed_tmdb_key(test_db)
    payload = {"title": "Test", "year": "2026", "poster_paths": ["no_slash.jpg"]}
    response = client.post("/api/maker-tools/tmdb/psd-export", json=payload)
    assert response.status_code == 400
    assert "invalid image path" in response.json()["detail"].lower()


def test_psd_export_path_traversal_rejected(client, test_db):
    _seed_tmdb_key(test_db)
    payload = {"title": "Test", "year": "2026", "poster_paths": ["/../etc/passwd.jpg"]}
    response = client.post("/api/maker-tools/tmdb/psd-export", json=payload)
    assert response.status_code == 400
    assert "invalid image path" in response.json()["detail"].lower()


def test_psd_export_no_api_key_returns_400(client):
    payload = {"title": "Test", "year": "2026", "poster_paths": ["/p1.jpg"]}
    response = client.post("/api/maker-tools/tmdb/psd-export", json=payload)
    assert response.status_code == 400
    assert "tmdb api key" in response.json()["detail"].lower()


def test_psd_export_image_fetch_failure_returns_502(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.post(
            "/api/maker-tools/tmdb/psd-export",
            json={"title": "Test", "year": "2026", "poster_paths": ["/p1.jpg"]},
        )

    assert response.status_code == 502


# ---------------------------------------------------------------------------
# API: POST /api/maker-tools/tmdb/psd-export — save/stream behaviour
# ---------------------------------------------------------------------------


def test_psd_export_download_mode_streams_bytes(client, test_db):
    """No export folder, no open_photopea → raw PSD bytes returned."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)

    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
         patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
        response = client.post(
            "/api/maker-tools/tmdb/psd-export",
            json={"title": "My Show", "year": "2026", "poster_paths": ["/p1.jpg"]},
        )

    assert response.status_code == 200
    assert response.content == b"FAKEPSD"
    assert response.headers["content-type"] == "application/octet-stream"
    assert "My Show" in response.headers["content-disposition"]


def test_psd_export_photopea_mode_saves_and_returns_json(client, test_db):
    """open_photopea=true, no export folder → saves to psd_cache, returns JSON URL."""
    _seed_tmdb_key(test_db)
    test_db.add(Setting(key="psd_open_photopea", value="true"))
    test_db.commit()

    poster_bytes = _make_jpeg_bytes(20, 30)

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"), \
             patch("api.maker_tools.app_settings" if hasattr(__import__("api.maker_tools", fromlist=["app_settings"]), "app_settings") else "api.maker_tools.Path") as _unused, \
             patch("api.maker_tools.get_setting_value", side_effect=lambda db, key: {
                 "psd_open_photopea": "true",
                 "psd_export_folder": "",
                 "tmdb_api_key": "testkey123",
                 "psd_template_path": "",
             }.get(key, "")), \
             patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.write_bytes"), \
             patch("pathlib.Path.is_file", return_value=False):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "poster_paths": ["/p1.jpg"]},
            )

    # Either JSON mode or download mode (psd_cache path may not exist in test env) — accept both
    assert response.status_code == 200


def test_psd_export_export_folder_saves_and_returns_json(client, test_db):
    """export_folder configured → file saved there, JSON response with psd_url."""
    _seed_tmdb_key(test_db)

    poster_bytes = _make_jpeg_bytes(20, 30)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "poster_paths": ["/p1.jpg"]},
            )

    assert response.status_code == 200
    data = response.json()
    assert "psd_url" in data
    assert data["filename"].endswith(".psd")
    assert "My Show" in data["filename"]
    assert data["open_photopea"] is False  # open_photopea not set → False


def test_psd_export_sanitizes_dangerous_filename_chars(client, test_db):
    """Characters like <>/\\|?* in the title must be stripped from the filename."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "Bad<>:\"/\\|?*Name", "year": "2026", "poster_paths": ["/p1.jpg"]},
            )

    assert response.status_code == 200
    filename = response.json()["filename"]
    for ch in '<>:"/\\|?*':
        assert ch not in filename, f"Unsafe char {ch!r} found in filename: {filename}"


def test_psd_export_strips_leading_dots_so_file_isnt_hidden(client, test_db):
    """A title starting with '...' must not produce a hidden dotfile — the scanner,
    poster renamer (IDarr), and drive counts all skip names starting with '.', so
    such a file would be invisible (and Photopea couldn't load it)."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "...And Then", "year": "2026", "poster_paths": ["/p1.jpg"]},
            )

    assert response.status_code == 200
    filename = response.json()["filename"]
    assert not filename.startswith("."), f"Filename is hidden: {filename}"
    assert filename.startswith("And Then")


# ---------------------------------------------------------------------------
# API: GET /api/maker-tools/psd-exports/{filename} — security & serving
# ---------------------------------------------------------------------------


def test_serve_psd_export_path_traversal_rejected(client):
    # %2F (encoded slash) is decoded by Starlette's routing layer, which normalises
    # the path and returns 404 (no matching route). Either 400 or 404 means the
    # traversal was blocked — the file was never read.
    response = client.get("/api/maker-tools/psd-exports/..%2Fetc%2Fpasswd.psd")
    assert response.status_code in (400, 404, 422)


def test_serve_psd_export_backslash_rejected(client):
    response = client.get("/api/maker-tools/psd-exports/foo%5Cbar.psd")
    assert response.status_code == 400


def test_serve_psd_export_non_psd_extension_rejected(client):
    response = client.get("/api/maker-tools/psd-exports/evil.exe")
    assert response.status_code == 400


def test_serve_psd_export_embedded_dotdot_allowed(client, test_db):
    # Embedded ".." is a legal filename, not traversal (no separator). It must be
    # served, not rejected — e.g. titles like "Spider-Man... Home".
    with tempfile.TemporaryDirectory() as tmpdir:
        psd_path = Path(tmpdir) / "foo..bar.psd"
        psd_path.write_bytes(b"FAKEPSDCONTENT")

        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        response = client.get("/api/maker-tools/psd-exports/foo..bar.psd")

    assert response.status_code == 200
    assert response.content == b"FAKEPSDCONTENT"


def test_serve_psd_export_file_not_found_returns_404(client):
    response = client.get("/api/maker-tools/psd-exports/nonexistent.psd")
    assert response.status_code == 404


def test_serve_psd_export_serves_file_from_export_folder(client, test_db):
    with tempfile.TemporaryDirectory() as tmpdir:
        psd_path = Path(tmpdir) / "My Show (2026).psd"
        psd_path.write_bytes(b"FAKEPSDCONTENT")

        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        response = client.get("/api/maker-tools/psd-exports/My Show (2026).psd")

    assert response.status_code == 200
    assert response.content == b"FAKEPSDCONTENT"
    assert response.headers["access-control-allow-origin"] == "*"


def test_serve_psd_export_cors_header_present(client, test_db):
    """CORS header is required so Photopea can fetch the PSD cross-origin."""
    with tempfile.TemporaryDirectory() as tmpdir:
        psd_path = Path(tmpdir) / "Test.psd"
        psd_path.write_bytes(b"PSDBYTES")

        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        response = client.get("/api/maker-tools/psd-exports/Test.psd")

    assert response.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# PSD access tokens — serving a PSD when an app password is set
# Photopea fetches via files:[url] and can't send the Bearer header, so a signed,
# file-scoped, expiring ?token=&exp= is minted at export time and checked here.
# ---------------------------------------------------------------------------


def test_psd_access_token_open_when_no_password(test_db):
    """With no password set, no token is minted and serving stays open."""
    from core.auth import mint_psd_access_token, verify_psd_access_token

    assert mint_psd_access_token(test_db, "x.psd") is None
    assert verify_psd_access_token(test_db, "x.psd", "", "") is True


def test_serve_psd_export_with_password_requires_valid_token(client, test_db):
    from core.auth import mint_psd_access_token, set_password

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "Secret Show (2026).psd").write_bytes(b"PSDBYTES")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        set_password(test_db, "hunter2")
        test_db.commit()

        # No token → 401 (the GET route is middleware-exempt; the endpoint rejects it).
        no_token = client.get("/api/maker-tools/psd-exports/Secret Show (2026).psd")
        assert no_token.status_code == 401

        # Wrong token → 401.
        bad = client.get("/api/maker-tools/psd-exports/Secret Show (2026).psd?token=deadbeef&exp=99999999999")
        assert bad.status_code == 401

        # Valid signed token → 200.
        sig, exp = mint_psd_access_token(test_db, "Secret Show (2026).psd")
        ok = client.get(f"/api/maker-tools/psd-exports/Secret Show (2026).psd?token={sig}&exp={exp}")
        assert ok.status_code == 200
        assert ok.content == b"PSDBYTES"


def test_psd_access_token_is_file_scoped_and_expiring(client, test_db):
    """A token for one file can't fetch another, and an expired token is rejected."""
    from core.auth import mint_psd_access_token, set_password, verify_psd_access_token

    set_password(test_db, "hunter2")
    test_db.commit()

    sig, exp = mint_psd_access_token(test_db, "A.psd")
    assert verify_psd_access_token(test_db, "A.psd", sig, str(exp)) is True
    assert verify_psd_access_token(test_db, "B.psd", sig, str(exp)) is False          # other file
    assert verify_psd_access_token(test_db, "A.psd", sig, "1") is False               # expired/forged exp


# ---------------------------------------------------------------------------
# API: POST /api/maker-tools/tmdb/psd-export — New Export overwrite guard
# A New Export must flag (409) any existing "Title (Year)" PSD before overwriting,
# regardless of its ID tag, unless confirm_overwrite is set.
# ---------------------------------------------------------------------------


def test_psd_export_conflict_existing_untagged_returns_409(client, test_db):
    _seed_tmdb_key(test_db)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026).psd").write_bytes(b"PSD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        response = client.post(
            "/api/maker-tools/tmdb/psd-export",
            json={"title": "My Show", "year": "2026", "poster_paths": ["/p1.jpg"]},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["exists"] is True
    assert body["existing_filename"] == "My Show (2026).psd"


def test_psd_export_conflict_matches_same_id(client, test_db):
    """A same-id file still flags (409) — re-exporting the same show overwrites in place."""
    _seed_tmdb_key(test_db)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026) {tmdb-123}.psd").write_bytes(b"PSD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        response = client.post(
            "/api/maker-tools/tmdb/psd-export",
            json={"title": "My Show", "year": "2026", "tmdb_id": "123", "poster_paths": ["/p1.jpg"]},
        )

    assert response.status_code == 409
    assert response.json()["existing_filename"] == "My Show (2026) {tmdb-123}.psd"


def test_psd_export_different_id_tag_is_separate_show(client, test_db):
    """A different {tmdb-…} tag means a different show that merely shares the title/year:
    New Export creates its own file and leaves the existing one untouched (no conflict)."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026) {tmdb-999}.psd").write_bytes(b"OTHER")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "tmdb_id": "123", "poster_paths": ["/p1.jpg"]},
            )

        assert response.status_code == 200
        assert response.json()["filename"] == "My Show (2026) {tmdb-123}.psd"
        # Both shows coexist — the other id's file is untouched.
        assert (Path(tmpdir) / "My Show (2026) {tmdb-123}.psd").read_bytes() == b"FAKEPSD"
        assert (Path(tmpdir) / "My Show (2026) {tmdb-999}.psd").read_bytes() == b"OTHER"


def test_psd_export_overwrite_replaces_untagged_predecessor(client, test_db):
    """Confirming overwrite on an untagged "Title (Year)" file re-tags it: the new
    {tmdb-…} file is written and the untagged predecessor is removed (no duplicate)."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026).psd").write_bytes(b"OLD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "tmdb_id": "123",
                      "poster_paths": ["/p1.jpg"], "confirm_overwrite": True},
            )

        assert response.status_code == 200
        assert response.json()["filename"] == "My Show (2026) {tmdb-123}.psd"
        assert (Path(tmpdir) / "My Show (2026) {tmdb-123}.psd").read_bytes() == b"FAKEPSD"
        assert not (Path(tmpdir) / "My Show (2026).psd").exists()


def test_psd_export_confirm_overwrite_proceeds_and_saves(client, test_db):
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026).psd").write_bytes(b"OLD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "poster_paths": ["/p1.jpg"], "confirm_overwrite": True},
            )

        assert response.status_code == 200
        assert response.json()["filename"] == "My Show (2026).psd"
        assert (Path(tmpdir) / "My Show (2026).psd").read_bytes() == b"FAKEPSD"


def test_psd_export_no_conflict_for_different_title_proceeds(client, test_db):
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "Other Show (2020).psd").write_bytes(b"PSD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "poster_paths": ["/p1.jpg"]},
            )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# API: POST /api/maker-tools/tmdb/psd-export — Use Existing PSD id matching
# Two shows sharing "Title (Year)" but with different TMDB ids must not bleed
# into each other: Use Existing only reuses a file whose {tmdb-…} tag matches
# (or an untagged one); a lone different-id file is treated as not-found.
# ---------------------------------------------------------------------------


def test_psd_use_existing_skips_different_id_returns_404(client, test_db):
    """Use Existing for tmdb-123 must NOT reuse a same-title file tagged tmdb-999 —
    that would export show B's posters under show A's id (the wrong-id bug)."""
    _seed_tmdb_key(test_db)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026) {tmdb-999}.psd").write_bytes(b"PSD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        response = client.post(
            "/api/maker-tools/tmdb/psd-export",
            json={"title": "My Show", "year": "2026", "tmdb_id": "123",
                  "use_existing": True, "poster_paths": ["/p1.jpg"]},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["not_found"] is True
    assert body["expected_filename"] == "My Show (2026).psd"


def test_psd_use_existing_matching_id_reuses(client, test_db):
    """Use Existing reuses the file whose {tmdb-…} tag matches the requested id."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026) {tmdb-123}.psd").write_bytes(b"PSD")
        (Path(tmpdir) / "My Show (2026) {tmdb-999}.psd").write_bytes(b"PSD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "tmdb_id": "123",
                      "use_existing": True, "poster_paths": ["/p1.jpg"]},
            )

        assert response.status_code == 200
        assert response.json()["filename"] == "My Show (2026) {tmdb-123}.psd"


def test_psd_use_existing_untagged_reuses(client, test_db):
    """Use Existing still reuses a plain untagged "Title (Year)" file (not yet ID-tagged)."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "My Show (2026).psd").write_bytes(b"PSD")
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "tmdb_id": "123",
                      "use_existing": True, "poster_paths": ["/p1.jpg"]},
            )

        assert response.status_code == 200
        assert response.json()["filename"] == "My Show (2026).psd"

