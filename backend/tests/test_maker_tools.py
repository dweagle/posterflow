import json
from datetime import date
from unittest.mock import MagicMock, patch

from models.setting import Setting


# ---------------------------------------------------------------------------
# Import private helper functions directly
# ---------------------------------------------------------------------------
from api.maker_tools import (
    _build_lang_params,
    _build_tmdb_images,
    _extract_name,
    _parse_bool,
    _parse_iso_date,
    _parse_non_negative_int,
    _parse_positive_int,
    _sanitize_drive_ids,
    _sanitize_monitor_config,
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
