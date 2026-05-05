import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.drive_loader import (
    fetch_remote_drives,
    load_drives_data,
    load_from_cache,
    save_to_cache,
)

SAMPLE_DATA = {"drives": [{"name": "MM2K", "drive_id": "abc123"}]}


# ---------------------------------------------------------------------------
# save_to_cache / load_from_cache
# ---------------------------------------------------------------------------


def test_save_to_cache_writes_json_file(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    with patch("services.drive_loader.CACHE_PATH", cache_path):
        save_to_cache(SAMPLE_DATA)

    assert cache_path.exists()
    content = json.loads(cache_path.read_text())
    assert content == SAMPLE_DATA


def test_load_from_cache_returns_data_when_file_exists(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    cache_path.write_text(json.dumps(SAMPLE_DATA))

    with patch("services.drive_loader.CACHE_PATH", cache_path):
        result = load_from_cache()

    assert result == SAMPLE_DATA


def test_load_from_cache_returns_none_when_file_missing(tmp_path):
    cache_path = tmp_path / "missing_cache.json"
    with patch("services.drive_loader.CACHE_PATH", cache_path):
        result = load_from_cache()

    assert result is None


def test_load_from_cache_returns_none_on_invalid_json(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    cache_path.write_text("not valid json {{{")

    with patch("services.drive_loader.CACHE_PATH", cache_path):
        result = load_from_cache()

    assert result is None


# ---------------------------------------------------------------------------
# fetch_remote_drives
# ---------------------------------------------------------------------------


def test_fetch_remote_drives_returns_data_on_success(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    mock_response = MagicMock()
    mock_response.json.return_value = SAMPLE_DATA

    with patch("services.drive_loader.CACHE_PATH", cache_path):
        with patch("requests.get", return_value=mock_response):
            result = fetch_remote_drives("https://example.com/drives.json")

    assert result == SAMPLE_DATA
    # Should also have cached the result
    assert cache_path.exists()


def test_fetch_remote_drives_returns_none_on_timeout(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    with patch("services.drive_loader.CACHE_PATH", cache_path):
        with patch("requests.get", side_effect=requests.exceptions.Timeout):
            result = fetch_remote_drives("https://example.com/drives.json")

    assert result is None


def test_fetch_remote_drives_returns_none_on_request_error(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    with patch("services.drive_loader.CACHE_PATH", cache_path):
        with patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("no connection"),
        ):
            result = fetch_remote_drives("https://example.com/drives.json")

    assert result is None


def test_fetch_remote_drives_returns_none_on_bad_json(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    mock_response = MagicMock()
    mock_response.json.side_effect = json.JSONDecodeError("bad json", "", 0)

    with patch("services.drive_loader.CACHE_PATH", cache_path):
        with patch("requests.get", return_value=mock_response):
            result = fetch_remote_drives("https://example.com/drives.json")

    assert result is None


# ---------------------------------------------------------------------------
# load_drives_data
# ---------------------------------------------------------------------------


def test_load_drives_data_returns_remote_data_when_available(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    with patch("services.drive_loader.CACHE_PATH", cache_path):
        with patch("services.drive_loader.fetch_remote_drives", return_value=SAMPLE_DATA):
            result = load_drives_data()

    assert result == SAMPLE_DATA


def test_load_drives_data_falls_back_to_cache_when_remote_fails(tmp_path):
    cache_path = tmp_path / "drives_cache.json"
    cache_path.write_text(json.dumps(SAMPLE_DATA))

    with patch("services.drive_loader.CACHE_PATH", cache_path):
        with patch("services.drive_loader.fetch_remote_drives", return_value=None):
            result = load_drives_data()

    assert result == SAMPLE_DATA


def test_load_drives_data_raises_when_both_sources_fail(tmp_path):
    cache_path = tmp_path / "missing.json"
    with patch("services.drive_loader.CACHE_PATH", cache_path):
        with patch("services.drive_loader.fetch_remote_drives", return_value=None):
            with pytest.raises(RuntimeError, match="Failed to load drives.json"):
                load_drives_data()
