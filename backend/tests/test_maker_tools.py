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
    _check_show_status,
    _content_disposition,
    _extract_name,
    _merge_recent_missing_items,
    _translate_year_season,
    _fetch_tmdb_image_bytes,
    _measure_logo_density,
    _parse_bool,
    _parse_iso_date,
    _normalize_psd_style,
    _parse_non_negative_int,
    _parse_positive_int,
    _resolve_new_export_template,
    _sanitize_drive_ids,
    _sanitize_monitor_config,
    compute_logo_geometry,
    compute_poster_fit_geometry,
    MakerMonitorConfig,
    MakerMonitorLibraryResult,
    MakerMonitorShowResult,
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
# _content_disposition
# ---------------------------------------------------------------------------


def test_content_disposition_em_dash_is_latin1_safe():
    # Regression: a raw em dash in the header value crashed Starlette's latin-1
    # header encoding (500), so Photopea hung on load. Must stay latin-1 encodable.
    header = _content_disposition("inline", "Alien — Romulus (2024).psd")
    header.encode("latin-1")  # would raise UnicodeEncodeError before the fix
    assert header.startswith("inline; filename*=utf-8''")
    assert "%E2%80%94" in header


def test_content_disposition_ascii_name_uses_plain_filename():
    assert _content_disposition("attachment", "Movie.psd") == 'attachment; filename="Movie.psd"'


# ---------------------------------------------------------------------------
# _build_psd — layer names
# ---------------------------------------------------------------------------


def _png_bytes(color, size, mode="RGB"):
    buf = BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def test_build_psd_non_macroman_title_saves_and_keeps_unicode_name():
    # Regression: psd-tools writes the legacy layer-name field as macroman, and
    # PixelLayer.frompil bypasses the library's macroman guard. A title with a
    # non-macroman glyph (★ U+2605, an em dash, CJK) crashed psd.save() with
    # "'charmap' codec can't encode character". Re-assigning name via the setter
    # keeps the real title as the Unicode layer name that editors display.
    from psd_tools import PSDImage

    data = _build_psd(
        [_png_bytes((120, 30, 40), (300, 450))],
        [_png_bytes((255, 255, 255, 200), (200, 80), mode="RGBA")],
        backdrop_bytes_list=[_png_bytes((0, 0, 80), (800, 450))],
        canvas_w=1000,
        canvas_h=1500,
        title="Classic★Stars",
        year="2025",
    )
    assert data  # would have raised UnicodeEncodeError before the fix

    names = [layer.name for layer in PSDImage.open(BytesIO(data)).descendants()]
    assert any("Classic★Stars (2025)" in name for name in names)


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
# API: GET /api/maker-tools/tv-details
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
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396")

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


# --- TMDB response cache -----------------------------------------------------------------
# A list view mounts ~50 cards, each fetching details + an overview, and revisiting the page
# used to repeat every one of them.

def test_repeat_reads_are_served_from_cache(client, test_db):
    _seed_tmdb_key(test_db)
    calls = []

    def counting_get(*args, **kwargs):
        calls.append(1)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"overview": "Same title, asked for twice."}
        return resp

    with patch("api.maker_tools.requests.get", side_effect=counting_get):
        first = client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=movie")
        second = client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=movie")

    assert first.json() == second.json()
    assert len(calls) == 1


def test_cache_is_keyed_per_title(client, test_db):
    _seed_tmdb_key(test_db)
    calls = []

    def counting_get(*args, **kwargs):
        calls.append(1)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"overview": "x"}
        return resp

    with patch("api.maker_tools.requests.get", side_effect=counting_get):
        client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=movie")
        client.get("/api/maker-tools/tmdb/overview?tmdb_id=79&media_type=movie")
        client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=tv")

    assert len(calls) == 3   # different id, and different media type, are different reads


def test_scan_paths_are_not_cached(client, test_db):
    """The monitor must see fresh air dates, so its fetches pass no cache_ttl."""
    from api.maker_tools import _tmdb_fetch_json

    calls = []

    def counting_get(*args, **kwargs):
        calls.append(1)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"name": "Show"}
        return resp

    with patch("api.maker_tools.requests.get", side_effect=counting_get):
        _tmdb_fetch_json("https://api.themoviedb.org/3/tv/1", {"api_key": "k"}, "show status")
        _tmdb_fetch_json("https://api.themoviedb.org/3/tv/1", {"api_key": "k"}, "show status")

    assert len(calls) == 2


def test_cache_key_ignores_the_api_key(test_db):
    from api.maker_tools import _tmdb_cache_key

    a = _tmdb_cache_key("https://x/y", {"api_key": "one", "language": "en-US"})
    b = _tmdb_cache_key("https://x/y", {"api_key": "two", "language": "en-US"})
    assert a == b


# --- GET /api/maker-tools/tmdb/overview -------------------------------------------------
# Unmatched cards carry ids but no TMDB text, so they look the description up on render.

def test_tmdb_overview_returns_the_description(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"overview": "A blade runner must pursue replicants."}

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=movie")

    assert response.status_code == 200
    assert response.json()["overview"] == "A blade runner must pursue replicants."


def test_tmdb_overview_is_blank_when_tmdb_has_none(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=tv")

    assert response.status_code == 200
    assert response.json()["overview"] == ""


def test_tmdb_overview_rejects_an_unknown_media_type(client, test_db):
    _seed_tmdb_key(test_db)
    response = client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=person")
    assert response.status_code == 400


def test_tmdb_overview_no_api_key_returns_400(client):
    response = client.get("/api/maker-tools/tmdb/overview?tmdb_id=78&media_type=movie")
    assert response.status_code == 400


def test_tmdb_tv_details_no_api_key_returns_400(client):
    response = client.get("/api/maker-tools/tv-details?tmdb_id=1396")
    assert response.status_code == 400


def test_tmdb_tv_details_tmdb_error_returns_502(client, test_db):
    _seed_tmdb_key(test_db)
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("api.maker_tools.requests.get", return_value=mock_resp):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=99999")

    assert response.status_code == 502


# --- season source: TheTVDB wins when available, TMDB is the fallback -------------------
# Sonarr's metadata comes from TVDB, so its season list is what the user's library reflects.

def _tmdb_details_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _FAKE_TV_DETAILS
    return mock_resp


def _seed_tvdb_key(test_db):
    test_db.add(Setting(key="tvdb_api_key", value="tvdb-key"))
    test_db.commit()


def test_tv_details_uses_tmdb_seasons_when_tvdb_is_not_configured(client, test_db):
    _seed_tmdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details_response()):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    data = response.json()
    assert data["season_source"] == "tmdb"
    assert data["season_count"] == 2


def test_tv_details_prefers_tvdb_seasons_when_configured(client, test_db):
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    tvdb_rows = [
        {"id": 10, "number": 0, "name": "", "image": "", "year": None, "episode_count": 3},
        {"id": 11, "number": 1, "name": "", "image": "https://artworks.thetvdb.com/s1.jpg", "year": "2020", "episode_count": None},
        {"id": 12, "number": 2, "name": "", "image": "", "year": "2021", "episode_count": None},
        {"id": 13, "number": 3, "name": "", "image": "", "year": "2022", "episode_count": None},
    ]
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details_response()), \
         patch("services.tvdb.fetch_series_seasons", return_value=tvdb_rows):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    data = response.json()
    assert data["season_source"] == "tvdb"
    # TVDB says 3 real seasons where TMDB said 2 — that disagreement is the whole point.
    assert data["season_count"] == 3
    assert [s["season_number"] for s in data["seasons"]] == [0, 1, 2, 3]
    # TVDB leaves season names blank, so the API supplies display names.
    assert data["seasons"][0]["name"] == "Specials"
    assert data["seasons"][1]["name"] == "Season 1"
    # Series type stays TMDB-only — TVDB has no Miniseries equivalent.
    assert data["series_type"] == "Miniseries"
    # Both lists ship regardless of which one won, so the gallery's season picker can follow
    # whichever source's images are being browsed.
    assert [s["season_number"] for s in data["tvdb_seasons"]] == [0, 1, 2, 3]
    assert [s["season_number"] for s in data["tmdb_seasons"]] == [0, 1, 2]


def test_tv_details_always_returns_the_tmdb_season_list(client, test_db):
    """TMDB can list a season TVDB doesn't (an airing show, usually). Browsing TMDB images must
    still reach those seasons, so its list is never replaced by TVDB's."""
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details_response()), \
         patch("services.tvdb.fetch_series_seasons",
               return_value=[{"id": 11, "number": 1, "name": "", "image": "", "year": None, "episode_count": None}]):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    data = response.json()
    assert data["season_source"] == "tvdb"
    assert [s["season_number"] for s in data["tvdb_seasons"]] == [1]
    # TMDB's season 2 survives even though TVDB has never heard of it.
    assert [s["season_number"] for s in data["tmdb_seasons"]] == [0, 1, 2]


def test_tv_details_leaves_tvdb_seasons_empty_without_tvdb(client, test_db):
    _seed_tmdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details_response()):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396")

    data = response.json()
    assert data["tvdb_seasons"] == []
    assert [s["season_number"] for s in data["tmdb_seasons"]] == [0, 1, 2]


# --- empty Specials ----------------------------------------------------------------------
# TheTVDB lists a Specials season for every series even when it holds no episodes. Each list is
# judged by its OWN source's count — TheTVDB is authoritative for shows and carries specials TMDB
# doesn't list (The Witcher's TVDB season 0 has 9 episodes; TMDB lists none at all).

def _tmdb_details(seasons, number_of_seasons=2):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "number_of_seasons": number_of_seasons,
        "type": "Scripted",
        "seasons": seasons,
    }
    return mock_resp


_S1 = {"season_number": 1, "name": "Season 1", "episode_count": 10, "air_date": "2020-06-01", "poster_path": ""}
_EMPTY_S0 = {"season_number": 0, "name": "Specials", "episode_count": 0, "air_date": None, "poster_path": ""}


def _tvdb_rows(specials_episodes):
    """TVDB season rows; specials_episodes None = the lookup couldn't determine a count."""
    return [
        {"id": 10, "number": 0, "name": "", "image": "", "year": None,
         "episode_count": specials_episodes},
        {"id": 11, "number": 1, "name": "", "image": "", "year": "2020", "episode_count": None},
    ]


def test_tvdb_specials_kept_when_tmdb_lists_none(client, test_db):
    """The Witcher's case: TheTVDB has real special episodes, TMDB lists no Specials season.
    TMDB's silence must not hide a season the user's library actually has."""
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details([_S1])), \
         patch("services.tvdb.fetch_series_seasons", return_value=_tvdb_rows(9)):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    assert [s["season_number"] for s in response.json()["seasons"]] == [0, 1]


def test_tvdb_specials_dropped_when_tvdb_itself_reports_none(client, test_db):
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details([_S1])), \
         patch("services.tvdb.fetch_series_seasons", return_value=_tvdb_rows(0)):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    data = response.json()
    assert [s["season_number"] for s in data["seasons"]] == [1]
    assert [s["season_number"] for s in data["tvdb_seasons"]] == [1]


def test_tvdb_specials_kept_when_the_count_is_unknown(client, test_db):
    """A failed episode lookup must not hide a season — showing a stray badge is the lesser harm."""
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details([_S1])), \
         patch("services.tvdb.fetch_series_seasons", return_value=_tvdb_rows(None)):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    assert [s["season_number"] for s in response.json()["seasons"]] == [0, 1]


def test_tmdb_list_drops_its_own_empty_specials(client, test_db):
    """Each list is filtered by its own source, so TMDB's empty Specials goes from TMDB's list
    while TheTVDB's real one stays in TheTVDB's."""
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details([_EMPTY_S0, _S1])), \
         patch("services.tvdb.fetch_series_seasons", return_value=_tvdb_rows(9)):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    data = response.json()
    assert [s["season_number"] for s in data["tmdb_seasons"]] == [1]
    assert [s["season_number"] for s in data["tvdb_seasons"]] == [0, 1]


def test_dropping_specials_leaves_the_season_count_alone(client, test_db):
    """season_count already excludes specials, so the filter must not move it."""
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details([_S1])), \
         patch("services.tvdb.fetch_series_seasons", return_value=_tvdb_rows(0)):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    assert response.json()["season_count"] == 1


def test_tv_details_ignores_tvdb_without_a_tvdb_id(client, test_db):
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details_response()), \
         patch("services.tvdb.fetch_series_seasons", side_effect=AssertionError("should not be called")):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396")

    assert response.json()["season_source"] == "tmdb"


def test_tv_details_falls_back_to_tmdb_when_tvdb_errors(client, test_db):
    """A TVDB outage must not break the card — it just loses the accuracy upgrade."""
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    import services.tvdb as tvdb_service

    with patch("api.maker_tools.requests.get", return_value=_tmdb_details_response()), \
         patch("services.tvdb.fetch_series_seasons",
               side_effect=tvdb_service.TvdbError("TheTVDB is down")):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    assert response.status_code == 200
    assert response.json()["season_source"] == "tmdb"
    assert response.json()["season_count"] == 2


def test_tv_details_falls_back_when_tvdb_has_no_seasons(client, test_db):
    _seed_tmdb_key(test_db)
    _seed_tvdb_key(test_db)
    with patch("api.maker_tools.requests.get", return_value=_tmdb_details_response()), \
         patch("services.tvdb.fetch_series_seasons", return_value=[]):
        response = client.get("/api/maker-tools/tv-details?tmdb_id=1396&tvdb_id=81189")

    assert response.json()["season_source"] == "tmdb"


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


@_psd_tools_missing
def test_build_psd_poster_layer_names_tag_each_injected_layer():
    """Per-poster tag names (aligned to poster order) name each injected layer for the plugin's batch
    convention; an empty entry / no names fall back to the title-based name."""
    from io import BytesIO
    from pathlib import Path
    from psd_tools import PSDImage
    import api.maker_tools as _mt

    template = Path(_mt.__file__).parent.parent / "assets" / "default_template.psd"
    if not template.exists():
        pytest.skip("bundled default template missing")
    p1, p2 = _make_jpeg_bytes(20, 30), _make_jpeg_bytes(20, 30)

    def poster_group_names(psd_bytes: bytes) -> list[str]:
        grp = PSDImage.open(BytesIO(psd_bytes)).find("POSTER")
        return [layer.name for layer in grp] if grp is not None else []

    # Two tagged posters → each layer gets its own convention name.
    both = poster_group_names(_build_psd([p1, p2], logo_bytes_list=[], template_path=template,
                                         title="Test Show", year="2026", poster_layer_names=["s1", "s0"]))
    assert "s1" in both and "s0" in both

    # Mixed: tagged poster keeps its name; the untagged one falls back to the title-based name.
    mixed = poster_group_names(_build_psd([p1, p2], logo_bytes_list=[], template_path=template,
                                          title="Test Show", year="2026", poster_layer_names=["s1", ""]))
    assert "s1" in mixed
    assert any(n.startswith("Test Show (2026)") for n in mixed)

    # No names → title-based, nothing tagged.
    none = poster_group_names(_build_psd([p1], logo_bytes_list=[], template_path=template,
                                         title="Test Show", year="2026"))
    assert "s1" not in none
    assert "Test Show (2026)" in none


@_psd_tools_missing
def test_build_psd_backdrop_layer_names_tag_the_backdrop_layer():
    """A tagged backdrop keeps the backdrop fit but its layer is named per the convention (batch-visible);
    untagged backdrops keep the '… - Backdrop' name."""
    from io import BytesIO
    from pathlib import Path
    from psd_tools import PSDImage
    import api.maker_tools as _mt

    template = Path(_mt.__file__).parent.parent / "assets" / "default_template.psd"
    if not template.exists():
        pytest.skip("bundled default template missing")
    backdrop = _make_jpeg_bytes(40, 20)  # landscape

    def poster_group_names(psd_bytes: bytes) -> list[str]:
        grp = PSDImage.open(BytesIO(psd_bytes)).find("POSTER")
        return [layer.name for layer in grp] if grp is not None else []

    tagged = poster_group_names(_build_psd([], logo_bytes_list=[], backdrop_bytes_list=[backdrop],
                                           template_path=template, title="Test Show", year="2026",
                                           backdrop_layer_names=["s1"]))
    assert "s1" in tagged

    untagged = poster_group_names(_build_psd([], logo_bytes_list=[], backdrop_bytes_list=[backdrop],
                                             template_path=template, title="Test Show", year="2026"))
    assert "s1" not in untagged
    assert any(n.startswith("Test Show (2026) - Backdrop") for n in untagged)


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

    with tempfile.TemporaryDirectory():
        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"), \
             patch("api.maker_tools.app_settings" if hasattr(__import__("api.maker_tools", fromlist=["app_settings"]), "app_settings") else "api.maker_tools.Path"), \
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


def test_serve_psd_export_em_dash_filename_returns_bytes(client, test_db):
    """Regression: an em-dash filename must not crash the latin-1 Content-Disposition
    header — the serve endpoint (Photopea's files:[url] load) should return the PSD
    bytes, not a 500 that leaves Photopea stuck on 'loading'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db.add(Setting(key="psd_export_folder", value=tmpdir))
        test_db.commit()
        filename = "Alien — Romulus (2024).psd"
        (Path(tmpdir) / filename).write_bytes(b"FAKEPSD")

        response = client.get(f"/api/maker-tools/psd-exports/{filename}")

    assert response.status_code == 200
    assert response.content == b"FAKEPSD"
    assert "utf-8''" in response.headers["content-disposition"]


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
# Per-style (CL2K / MM2K) routing
# ---------------------------------------------------------------------------


def test_normalize_psd_style_defaults_to_cl2k():
    assert _normalize_psd_style("") == "CL2K"
    assert _normalize_psd_style(None) == "CL2K"
    assert _normalize_psd_style("CL2K") == "CL2K"
    assert _normalize_psd_style("something else") == "CL2K"


def test_normalize_psd_style_detects_mm2k():
    # Accepts the bare style and the community "… Style" tag form, case-insensitively.
    assert _normalize_psd_style("MM2K") == "MM2K"
    assert _normalize_psd_style("mm2k") == "MM2K"
    assert _normalize_psd_style("MM2K Style") == "MM2K"


def test_resolve_new_export_template_uses_mm2k_setting(test_db):
    """An MM2K export prefers the psd_template_path_mm2k setting over the CL2K one."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cl2k_tpl = Path(tmpdir) / "cl2k.psd"
        mm2k_tpl = Path(tmpdir) / "mm2k.psd"
        cl2k_tpl.write_bytes(b"CL2K")
        mm2k_tpl.write_bytes(b"MM2K")
        test_db.add(Setting(key="psd_template_path", value=str(cl2k_tpl)))
        test_db.add(Setting(key="psd_template_path_mm2k", value=str(mm2k_tpl)))
        test_db.commit()

        assert _resolve_new_export_template(test_db, "CL2K") == cl2k_tpl
        assert _resolve_new_export_template(test_db, "MM2K") == mm2k_tpl
        # Blank/unknown style falls back to CL2K.
        assert _resolve_new_export_template(test_db, "") == cl2k_tpl


def test_psd_export_mm2k_saves_to_mm2k_folder(client, test_db):
    """style=MM2K → the file lands in psd_export_folder_mm2k; the CL2K folder is untouched."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)

    with tempfile.TemporaryDirectory() as cl2k_dir, tempfile.TemporaryDirectory() as mm2k_dir:
        test_db.add(Setting(key="psd_export_folder", value=cl2k_dir))
        test_db.add(Setting(key="psd_export_folder_mm2k", value=mm2k_dir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "style": "MM2K", "poster_paths": ["/p1.jpg"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["style"] == "MM2K"
        assert "style=MM2K" in data["psd_url"]
        filename = data["filename"]
        assert (Path(mm2k_dir) / filename).is_file(), "PSD should be saved in the MM2K folder"
        assert not (Path(cl2k_dir) / filename).exists(), "CL2K folder must be untouched"


def test_psd_export_default_style_saves_to_cl2k_folder(client, test_db):
    """No style (and style=CL2K) still uses the existing psd_export_folder — back-compat."""
    _seed_tmdb_key(test_db)
    poster_bytes = _make_jpeg_bytes(20, 30)

    with tempfile.TemporaryDirectory() as cl2k_dir, tempfile.TemporaryDirectory() as mm2k_dir:
        test_db.add(Setting(key="psd_export_folder", value=cl2k_dir))
        test_db.add(Setting(key="psd_export_folder_mm2k", value=mm2k_dir))
        test_db.commit()

        with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=poster_bytes), \
             patch("api.maker_tools._build_psd", return_value=b"FAKEPSD"):
            response = client.post(
                "/api/maker-tools/tmdb/psd-export",
                json={"title": "My Show", "year": "2026", "poster_paths": ["/p1.jpg"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["style"] == "CL2K"
        filename = data["filename"]
        assert (Path(cl2k_dir) / filename).is_file(), "PSD should be saved in the CL2K folder"
        assert not (Path(mm2k_dir) / filename).exists(), "MM2K folder must be untouched"


def test_serve_psd_export_reads_from_style_folder(client, test_db):
    """?style=MM2K serves from the MM2K folder even when a CL2K folder is also set."""
    with tempfile.TemporaryDirectory() as cl2k_dir, tempfile.TemporaryDirectory() as mm2k_dir:
        (Path(mm2k_dir) / "Show (2026).psd").write_bytes(b"MM2KPSD")
        test_db.add(Setting(key="psd_export_folder", value=cl2k_dir))
        test_db.add(Setting(key="psd_export_folder_mm2k", value=mm2k_dir))
        test_db.commit()

        response = client.get("/api/maker-tools/psd-exports/Show (2026).psd?style=MM2K")

    assert response.status_code == 200
    assert response.content == b"MM2KPSD"


def test_save_image_export_mm2k_writes_to_mm2k_folder(client, test_db):
    """PUT /image-exports/{name}?style=MM2K writes to psd_image_export_folder_mm2k."""
    with tempfile.TemporaryDirectory() as cl2k_dir, tempfile.TemporaryDirectory() as mm2k_dir:
        test_db.add(Setting(key="psd_image_export_folder", value=cl2k_dir))
        test_db.add(Setting(key="psd_image_export_folder_mm2k", value=mm2k_dir))
        test_db.commit()

        response = client.put(
            "/api/maker-tools/image-exports/Show (2026).jpg",
            params={"style": "MM2K"},
            content=b"JPGBYTES",
            headers={"Content-Type": "application/octet-stream"},
        )

        assert response.status_code == 200
        assert (Path(mm2k_dir) / "Show (2026).jpg").is_file()
        assert not (Path(cl2k_dir) / "Show (2026).jpg").exists()


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



# ---------------------------------------------------------------------------
# TMDB search — resolve-by-id (foreign titles + preselect)
# ---------------------------------------------------------------------------

def _tmdb_resp(json_data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    m.headers = {}
    return m


def test_tmdb_search_resolves_foreign_title_by_tvdb_id(client, test_db):
    """A show whose TVDB English name isn't on TMDB should still resolve via tvdb_id /find."""
    _seed_tmdb_key(test_db)

    def fake_get(url, params=None, timeout=None):
        if "/search/tv" in url:
            return _tmdb_resp({"results": []})  # English name finds nothing
        if "/find/369137" in url:
            return _tmdb_resp({"movie_results": [], "tv_results": [
                {"id": 117057, "name": "Wer stiehlt mir die Show?",
                 "first_air_date": "2017-09-01", "poster_path": "/x.jpg",
                 "overview": "", "popularity": 5.0}
            ]})
        if "/tv/117057/external_ids" in url:
            return _tmdb_resp({"imdb_id": "tt12345", "tvdb_id": 369137})
        return _tmdb_resp({})

    with patch("api.maker_tools.requests.get", side_effect=fake_get) as mock_get:
        response = client.get("/api/maker-tools/tmdb/search?q=Who+Steals+the+Show&type=tv&tvdb_id=369137")

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["tmdb_id"] == 117057
    assert results[0]["media_type"] == "tv"
    assert results[0]["auto_matched"] is True
    assert results[0]["title"] == "Wer stiehlt mir die Show?"
    assert results[0]["tvdb_id"] == 369137
    find_calls = [c for c in mock_get.call_args_list if "/find/369137" in c[0][0]]
    assert find_calls and find_calls[0][1]["params"]["external_source"] == "tvdb_id"


def test_tmdb_search_pins_id_match_and_dedupes(client, test_db):
    """A carried tmdb_id should pin the exact entity first and drop its fuzzy duplicate."""
    _seed_tmdb_key(test_db)

    search_results = [
        {"id": 100, "title": "The Batman", "release_date": "2022-01-01",
         "poster_path": "/p100.jpg", "overview": "", "popularity": 50.0},
        {"id": 200, "title": "Batman Begins", "release_date": "2005-01-01",
         "poster_path": "/p200.jpg", "overview": "", "popularity": 80.0},
    ]

    def fake_get(url, params=None, timeout=None):
        if "/search/movie" in url:
            return _tmdb_resp({"results": search_results})
        if url.endswith("/external_ids"):
            return _tmdb_resp({"imdb_id": "tt" + url.split("/")[-2], "tvdb_id": None})
        if url.rstrip("/").endswith("/movie/100"):
            return _tmdb_resp(search_results[0])
        return _tmdb_resp({})

    with patch("api.maker_tools.requests.get", side_effect=fake_get):
        response = client.get("/api/maker-tools/tmdb/search?q=The+Batman&type=movie&tmdb_id=100")

    assert response.status_code == 200
    results = response.json()
    assert [r["tmdb_id"] for r in results] == [100, 200]
    assert results[0]["auto_matched"] is True
    assert results[0]["media_type"] == "movie"
    assert results[1]["auto_matched"] is False


# ---------------------------------------------------------------------------
# _translate_year_season / monitor year-numbered seasons (Shark Week)
# ---------------------------------------------------------------------------


def _tvdb_year_rows(*years):
    return [{"id": 1000 + i, "number": y, "name": "", "image": None, "year": str(y)}
            for i, y in enumerate(years)]


def test_translate_year_season_maps_by_air_year():
    with patch("services.tvdb.fetch_series_seasons",
               return_value=_tvdb_year_rows(0, 1988, 1989, 2025, 2026)):
        assert _translate_year_season(37, 2026, 81189, ("tvdb-key", "")) == 2026


def test_translate_year_season_falls_back_to_ordinal_position():
    """Premiere air year missing from TVDB's list: TMDB season k maps to the k-th aired season."""
    with patch("services.tvdb.fetch_series_seasons",
               return_value=_tvdb_year_rows(1988, 1989, 1990)):
        assert _translate_year_season(2, 2027, 81189, ("tvdb-key", "")) == 1989


def test_translate_year_season_leaves_sequential_shows_alone():
    with patch("services.tvdb.fetch_series_seasons",
               return_value=_tvdb_year_rows(0, 1, 2, 3)):
        assert _translate_year_season(4, 2026, 81189, ("tvdb-key", "")) is None


def test_translate_year_season_skips_without_tvdb_key():
    with patch("services.tvdb.fetch_series_seasons",
               side_effect=AssertionError("should not be called")):
        assert _translate_year_season(37, 2026, 81189, ("", "")) is None


def test_translate_year_season_survives_tvdb_error():
    import services.tvdb as tvdb_service
    with patch("services.tvdb.fetch_series_seasons",
               side_effect=tvdb_service.TvdbError("TheTVDB is down")):
        assert _translate_year_season(37, 2026, 81189, ("tvdb-key", "")) is None


def _shark_week_payload():
    return {
        "name": "Shark Week",
        "first_air_date": "1988-07-17",
        "poster_path": "/shark.jpg",
        "external_ids": {"imdb_id": "tt0000001", "tvdb_id": 81189},
        "next_episode_to_air": {"air_date": "2026-07-27", "season_number": 37, "episode_number": 1},
    }


def test_check_show_status_matches_year_numbered_drive_files():
    existing = {1988, 1989, 2025, 2026}
    with patch("api.maker_tools._tmdb_fetch_json", return_value=_shark_week_payload()), \
         patch("api.maker_tools._monitor_today_local", return_value=date(2026, 7, 26)), \
         patch("services.tvdb.fetch_series_seasons",
               return_value=_tvdb_year_rows(1988, 1989, 2025, 2026)):
        result = _check_show_status("3959", existing, "tmdb-key", 21, ("tvdb-key", ""))

    assert result is not None
    assert result.season_number == 2026
    assert result.poster_exists is True


def test_check_show_status_reports_year_season_as_needed_when_missing():
    existing = {1988, 1989, 2025}
    with patch("api.maker_tools._tmdb_fetch_json", return_value=_shark_week_payload()), \
         patch("api.maker_tools._monitor_today_local", return_value=date(2026, 7, 26)), \
         patch("services.tvdb.fetch_series_seasons",
               return_value=_tvdb_year_rows(1988, 1989, 2025, 2026)):
        result = _check_show_status("3959", existing, "tmdb-key", 21, ("tvdb-key", ""))

    assert result is not None
    assert result.season_number == 2026
    assert result.poster_exists is False


def test_check_show_status_keeps_tmdb_number_without_tvdb_key():
    with patch("api.maker_tools._tmdb_fetch_json", return_value=_shark_week_payload()), \
         patch("api.maker_tools._monitor_today_local", return_value=date(2026, 7, 26)), \
         patch("services.tvdb.fetch_series_seasons",
               side_effect=AssertionError("should not be called")):
        result = _check_show_status("3959", {1988, 1989}, "tmdb-key", 21)

    assert result is not None
    assert result.season_number == 37
    assert result.poster_exists is False


def _merge_fixture():
    """Current run has Shark Week as Season 2026; the saved result kept it as Season 37."""
    current = [MakerMonitorLibraryResult(
        library_name="TV", library_type="TV", total_scanned=1, premieres_found=1, posters_needed=1,
        shows=[MakerMonitorShowResult(
            tmdb_id="3959", name="Shark Week", homepage="https://www.themoviedb.org/tv/3959",
            season_number=2026, date="2026-07-27", poster_exists=False, tvdb_id=81189,
        )],
    )]
    previous = {
        "range_start": "2026-07-20",
        "libraries": [{
            "library_name": "TV", "library_type": "TV",
            "shows": [{
                "tmdb_id": "3959", "name": "Shark Week", "season_number": 37,
                "date": "2026-07-27", "poster_exists": False, "tvdb_id": 81189,
            }],
        }],
    }
    return current, previous


def _merge_kwargs(current, previous, inventory):
    return dict(
        current_results=current,
        previous_payload=previous,
        reference_today=date(2026, 7, 26),
        retention_days=14,
        scanned_tmdb_ids={("TV", "TV"): {show["tmdb_id"] for lib in previous["libraries"] for show in lib["shows"]} | {"3959"}},
        scanned_seasons={("TV", "TV"): inventory},
        tvdb_credentials=("tvdb-key", ""),
    )


def test_merge_rekeys_retained_ordinal_season_and_drops_duplicate():
    current, previous = _merge_fixture()
    with patch("services.tvdb.fetch_series_seasons",
               return_value=_tvdb_year_rows(1988, 1989, 2025, 2026)):
        _, items_added = _merge_recent_missing_items(
            **_merge_kwargs(current, previous, {"3959": {1988, 1989, 2025}}))

    assert items_added == 0
    assert [show.season_number for show in current[0].shows] == [2026]


def test_merge_drops_same_date_duplicate_when_tvdb_is_down():
    import services.tvdb as tvdb_service
    current, previous = _merge_fixture()
    with patch("services.tvdb.fetch_series_seasons",
               side_effect=tvdb_service.TvdbError("TheTVDB is down")):
        _, items_added = _merge_recent_missing_items(
            **_merge_kwargs(current, previous, {"3959": {1988, 1989, 2025}}))

    assert items_added == 0
    assert [show.season_number for show in current[0].shows] == [2026]


def test_merge_still_retains_sequential_show_missing_from_current_run():
    current, previous = _merge_fixture()
    previous["libraries"][0]["shows"].append({
        "tmdb_id": "1234", "name": "Foundation", "season_number": 3,
        "date": "2026-07-21", "poster_exists": False, "tvdb_id": 999,
    })
    with patch("services.tvdb.fetch_series_seasons",
               return_value=_tvdb_year_rows(1, 2, 3)):
        _, items_added = _merge_recent_missing_items(
            **_merge_kwargs(current, previous, {"3959": {1988, 1989, 2025}, "1234": {1, 2}}))

    assert items_added == 1
    retained = [show for show in current[0].shows if show.tmdb_id == "1234"]
    assert len(retained) == 1
    assert retained[0].season_number == 3
    assert len(current[0].shows) == 2


# ---------------------------------------------------------------------------
# Photoshop open queue
# ---------------------------------------------------------------------------

def _ps_queue_reset():
    from api import maker_tools as mt
    mt._PS_OPEN_QUEUE.clear()


def test_photoshop_queue_enqueue_then_poll_claims_and_empties():
    from api import maker_tools as mt
    _ps_queue_reset()
    with patch.object(mt, "mint_psd_access_token", return_value=None):
        resp = mt.enqueue_photoshop_open(
            mt.PhotoshopQueueRequest(filename="Show (2020) {tmdb-1}.psd", style="MM2K", name="Show (2020) {tmdb-1}"),
            db=MagicMock(),
        )
    assert json.loads(resp.body) == {"queued": True, "pending": 1}

    polled = json.loads(mt.poll_photoshop_queue(client="ps-1").body)
    assert len(polled["items"]) == 1
    item = polled["items"][0]
    assert item["filename"] == "Show (2020) {tmdb-1}.psd"
    assert item["style"] == "MM2K"
    assert item["psd_url"].startswith("/api/maker-tools/psd-exports/Show%20%282020%29%20%7Btmdb-1%7D.psd?style=MM2K")
    assert "token=" not in item["psd_url"]   # no password → no signed token

    # the poll CLAIMED the item — a second poll gets nothing
    assert json.loads(mt.poll_photoshop_queue().body) == {"items": []}


def test_photoshop_queue_reenqueue_replaces_pending_entry():
    from api import maker_tools as mt
    _ps_queue_reset()
    with patch.object(mt, "mint_psd_access_token", return_value=None):
        for _ in range(2):
            mt.enqueue_photoshop_open(mt.PhotoshopQueueRequest(filename="A.psd", style="CL2K"), db=MagicMock())
        resp = mt.enqueue_photoshop_open(mt.PhotoshopQueueRequest(filename="B.psd", style="CL2K"), db=MagicMock())
    assert json.loads(resp.body)["pending"] == 2   # A replaced itself; A + B remain
    polled = json.loads(mt.poll_photoshop_queue().body)
    assert [it["filename"] for it in polled["items"]] == ["A.psd", "B.psd"]


def test_photoshop_queue_signs_download_url_when_password_set():
    from api import maker_tools as mt
    _ps_queue_reset()
    with patch.object(mt, "mint_psd_access_token", return_value=("sig123", 1900000000)):
        mt.enqueue_photoshop_open(mt.PhotoshopQueueRequest(filename="A.psd", style=""), db=MagicMock())
    item = json.loads(mt.poll_photoshop_queue().body)["items"][0]
    assert item["style"] == "CL2K"   # blank style normalizes to the default
    assert "token=sig123" in item["psd_url"] and "exp=1900000000" in item["psd_url"]


def test_photoshop_queue_rejects_bad_filenames():
    from api import maker_tools as mt
    from fastapi import HTTPException
    _ps_queue_reset()
    for bad in ("../evil.psd", "x/evil.psd", "notapsd.txt"):
        with pytest.raises(HTTPException):
            mt.enqueue_photoshop_open(mt.PhotoshopQueueRequest(filename=bad), db=MagicMock())


# ---------------------------------------------------------------------------
# Save gallery artwork to the export folders (POST /artwork-exports)
# ---------------------------------------------------------------------------

def _artwork_image_bytes(mode: str = "RGBA", fmt: str = "PNG", size: tuple[int, int] = (10, 4)) -> bytes:
    buf = BytesIO()
    Image.new(mode, size, (255, 0, 0, 255) if mode == "RGBA" else (255, 0, 0)).save(buf, fmt)
    return buf.getvalue()


def _artwork_request(**overrides):
    from api import maker_tools as mt
    base = dict(path="/logo.png", subtype="logo", title="Show", media_type="tv", year=2020, tmdb_id=1)
    base.update(overrides)
    return mt.SaveGalleryArtworkRequest(**base)


def test_save_gallery_artwork_requires_configured_folder():
    from api import maker_tools as mt
    from fastapi import HTTPException
    with patch.object(mt, "get_setting_value", return_value=""):
        with pytest.raises(HTTPException) as exc:
            mt.save_gallery_artwork(_artwork_request(), db=MagicMock())
    assert exc.value.status_code == 400


def test_save_gallery_artwork_rejects_bad_path_and_subtype():
    from api import maker_tools as mt
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        mt.save_gallery_artwork(_artwork_request(path="/../evil.png"), db=MagicMock())
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        mt.save_gallery_artwork(_artwork_request(subtype="poster"), db=MagicMock())
    assert exc.value.status_code == 400


def test_save_gallery_artwork_reads_subtype_folder_setting(tmp_path):
    from api import maker_tools as mt
    with patch.object(mt, "get_setting_value", return_value=str(tmp_path)) as gsv, \
         patch.object(mt, "_fetch_tmdb_image_bytes", return_value=_artwork_image_bytes("RGB", "JPEG")):
        mt.save_gallery_artwork(_artwork_request(subtype="background", path="/bg.jpg"), db=MagicMock())
        assert gsv.call_args[0][1] == mt.SETTING_BACKGROUND_EXPORT_FOLDER
        # Gallery logo saves use their own folder, NOT the panel's logo_export_folder.
        mt.save_gallery_artwork(_artwork_request(), db=MagicMock())
    assert gsv.call_args[0][1] == mt.SETTING_ARTWORK_LOGO_EXPORT_FOLDER


def test_save_gallery_logo_writes_canonical_png(tmp_path):
    from api import maker_tools as mt
    # JPEG source → re-encoded to PNG under IDarr's canonical " - logo.png" name.
    with patch.object(mt, "get_setting_value", return_value=str(tmp_path)), \
         patch.object(mt, "_fetch_tmdb_image_bytes", return_value=_artwork_image_bytes("RGB", "JPEG")):
        resp = json.loads(mt.save_gallery_artwork(_artwork_request(path="/logo.jpg"), db=MagicMock()).body)
    assert resp["status"] == "added"
    written = resp["written"]
    assert written.endswith(" - logo.png") and "{tmdb-1}" in written and "Show (2020)" in written
    saved = Image.open(tmp_path / written)
    assert saved.format == "PNG"


def test_save_gallery_background_writes_canonical_jpg(tmp_path):
    from api import maker_tools as mt
    with patch.object(mt, "get_setting_value", return_value=str(tmp_path)), \
         patch.object(mt, "_fetch_tmdb_image_bytes", return_value=_artwork_image_bytes("RGB", "JPEG")):
        resp = json.loads(mt.save_gallery_artwork(_artwork_request(subtype="background", path="/bg.jpg"), db=MagicMock()).body)
    written = resp["written"]
    assert written.endswith(" - background.jpg")
    assert Image.open(tmp_path / written).format == "JPEG"


def test_save_gallery_squareart_crops_to_square(tmp_path):
    from api import maker_tools as mt
    src = _artwork_image_bytes("RGB", "JPEG", size=(1000, 1500))
    with patch.object(mt, "get_setting_value", return_value=str(tmp_path)), \
         patch.object(mt, "_fetch_tmdb_image_bytes", return_value=src):
        resp = json.loads(mt.save_gallery_artwork(
            _artwork_request(subtype="squareart", path="/poster.jpg", crop_x=100, crop_y=200, crop_size=800),
            db=MagicMock()).body)
    written = resp["written"]
    assert written.endswith(" - squareart.jpg")
    saved = Image.open(tmp_path / written)
    assert saved.format == "JPEG" and saved.size == (800, 800)


def test_save_gallery_artwork_rejects_crop_on_non_squareart():
    from api import maker_tools as mt
    from fastapi import HTTPException
    with patch.object(mt, "get_setting_value", return_value="/tmp/x"):
        with pytest.raises(HTTPException) as exc:
            mt.save_gallery_artwork(_artwork_request(subtype="background", crop_size=800), db=MagicMock())
    assert exc.value.status_code == 400


def test_save_gallery_artwork_exists_then_overwrites_on_confirm(tmp_path):
    from api import maker_tools as mt
    with patch.object(mt, "get_setting_value", return_value=str(tmp_path)), \
         patch.object(mt, "_fetch_tmdb_image_bytes", return_value=_artwork_image_bytes()) as fetch:
        first = json.loads(mt.save_gallery_artwork(_artwork_request(), db=MagicMock()).body)
        existing = tmp_path / first["written"]
        # Same name already on disk → "exists", and nothing is downloaded.
        fetch.reset_mock()
        second = json.loads(mt.save_gallery_artwork(_artwork_request(), db=MagicMock()).body)
        assert second == {"status": "exists", "written": first["written"]}
        fetch.assert_not_called()
        confirmed = json.loads(mt.save_gallery_artwork(_artwork_request(confirm_overwrite=True), db=MagicMock()).body)
    assert confirmed["status"] == "added"
    assert existing.exists()


def test_photoshop_plugin_ccx_contains_manifest_and_excludes_tests():
    import zipfile as _zipfile
    from api import maker_tools as mt
    resp = mt.download_photoshop_plugin()
    zf = _zipfile.ZipFile(BytesIO(resp.body))
    names = zf.namelist()
    assert "manifest.json" in names
    assert "main.js" in names and "remote.js" in names
    assert any(n.startswith("icons/") for n in names)
    assert not any(n.startswith("test/") for n in names)
    assert not any(n.endswith(".md") for n in names)
