import json
from datetime import date
from unittest.mock import MagicMock, patch

from models.setting import Setting


# ---------------------------------------------------------------------------
# Import private helper functions directly
# ---------------------------------------------------------------------------
from api.maker_tools import (
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
