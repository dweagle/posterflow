"""Tests for services/rejected_keys.py — one log line per rejected API key."""
import pytest

import services.rejected_keys as rk


@pytest.fixture(autouse=True)
def _reset():
    rk._logged_at.clear()
    yield
    rk._logged_at.clear()


def _capture(monkeypatch):
    lines = []
    monkeypatch.setattr(rk, "log_warning", lambda tag, msg, **kw: lines.append(msg))
    return lines


class _Req:
    def __init__(self, url):
        self.url = url


class _Resp:
    def __init__(self, status, url=""):
        self.status_code = status
        self.request = _Req(url)


def test_warns_once_per_source_and_key(monkeypatch):
    lines = _capture(monkeypatch)
    for _ in range(3):
        rk.warn_rejected_key("tmdb", "k")
    rk.warn_rejected_key("fanart", "k")
    rk.warn_rejected_key("tmdb", "other")

    assert len(lines) == 3
    assert lines[0].startswith("TMDB rejected the API key. ")
    assert "API Read Access Token" in lines[0]
    assert lines[1].startswith("fanart.tv rejected the API key. ")
    assert "Settings > General > API Keys" in lines[1]


def test_warns_again_after_the_interval(monkeypatch):
    lines = _capture(monkeypatch)
    rk.warn_rejected_key("tmdb", "k")
    rk._logged_at[("tmdb", "k")] -= rk.INTERVAL + 1
    rk.warn_rejected_key("tmdb", "k")
    assert len(lines) == 2


def test_what_names_the_pin_when_one_was_sent(monkeypatch):
    lines = _capture(monkeypatch)
    rk.warn_rejected_key("tvdb", "k/1234", what="API key and PIN")
    assert lines[0].startswith("TheTVDB rejected the API key and PIN. Legacy v3 keys")


def test_tmdb_response_reads_the_key_from_the_request(monkeypatch):
    lines = _capture(monkeypatch)
    rk.note_tmdb_response(_Resp(401, "https://api.themoviedb.org/3/movie/1?api_key=abc&language=en-US"))
    rk.note_tmdb_response(_Resp(401, "https://api.themoviedb.org/3/tv/2?api_key=abc"))
    assert len(lines) == 1
    assert list(rk._logged_at) == [("tmdb", "abc")]


def test_tmdb_response_ignores_other_statuses(monkeypatch):
    lines = _capture(monkeypatch)
    for status in (200, 404, 429, 500):
        rk.note_tmdb_response(_Resp(status, "https://x?api_key=abc"))
    assert lines == []


def test_tmdb_response_takes_an_explicit_key_and_survives_a_bare_response(monkeypatch):
    lines = _capture(monkeypatch)

    class Bare:
        status_code = 401

    rk.note_tmdb_response(_Resp(401), "explicit")
    rk.note_tmdb_response(Bare())
    assert len(lines) == 2
    assert set(rk._logged_at) == {("tmdb", "explicit"), ("tmdb", "")}
