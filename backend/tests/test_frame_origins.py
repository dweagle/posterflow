import pytest

import core.frame_origins as frame_origins
from core.frame_origins import get_allowed_frame_origins, normalize_frame_origin


class TestNormalizeFrameOrigin:
    @pytest.mark.parametrize("raw,expected", [
        ("http://192.168.1.50:8080", "http://192.168.1.50:8080"),
        ("https://dash.example.com", "https://dash.example.com"),
        ("HTTPS://Dash.Example.com/", "https://dash.example.com"),
        ("http://[::1]:3000", "http://[::1]:3000"),
        ("  http://organizr.local  ", "http://organizr.local"),
    ])
    def test_valid_origins_normalize(self, raw, expected):
        assert normalize_frame_origin(raw) == expected

    @pytest.mark.parametrize("raw", [
        "",
        "*",
        "http://*",
        "https://*.example.com",
        "example.com",                       # missing scheme
        "ftp://example.com",
        "http://example.com/dashboard",      # path
        "http://user:pw@example.com",        # userinfo
        "http://example.com?x=1",
        "http://example.com#frag",
        "http://example.com:99999",          # invalid port
        "http://exa mple.com",
        "http://example.com\r\nX-Evil: 1",   # header injection
        "http://",
    ])
    def test_invalid_origins_rejected(self, raw):
        assert normalize_frame_origin(raw) is None


@pytest.fixture
def env_origins(monkeypatch):
    """Set the env-derived origins string and clear the parse-once cache."""
    def _set(value: str):
        monkeypatch.setattr(frame_origins.settings, "allowed_frame_origins", value)
        frame_origins._parsed = None

    yield _set
    frame_origins._parsed = None


class TestGetAllowedFrameOrigins:
    def test_empty_by_default(self, env_origins):
        env_origins("")
        assert get_allowed_frame_origins() == []

    def test_parses_comma_separated_list(self, env_origins):
        env_origins("http://192.168.1.50:8080, https://dash.example.com")
        assert get_allowed_frame_origins() == ["http://192.168.1.50:8080", "https://dash.example.com"]

    def test_invalid_entries_dropped_valid_kept(self, env_origins):
        env_origins("*, http://dash.local, http://bad/path")
        assert get_allowed_frame_origins() == ["http://dash.local"]

    def test_dedupes_normalized_entries(self, env_origins):
        env_origins("HTTP://Dash.Local/,http://dash.local")
        assert get_allowed_frame_origins() == ["http://dash.local"]


class TestSecurityHeadersMiddleware:
    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        yield
        frame_origins._parsed = None

    def test_default_sends_xfo_sameorigin(self, client):
        frame_origins._parsed = []
        resp = client.get("/api/health")
        assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert "Content-Security-Policy" not in resp.headers
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_user_origins_switch_to_frame_ancestors(self, client):
        frame_origins._parsed = ["http://dash.local:8080"]
        resp = client.get("/api/health")
        assert "X-Frame-Options" not in resp.headers
        assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'self' http://dash.local:8080"

    def test_photopea_page_always_allows_photopea(self, client):
        frame_origins._parsed = []
        resp = client.get("/photopea-plugin.html")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "https://www.photopea.com" in csp
        assert "X-Frame-Options" not in resp.headers

    def test_photopea_page_includes_user_origins(self, client):
        frame_origins._parsed = ["http://dash.local"]
        resp = client.get("/photopea-plugin.html")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "https://www.photopea.com" in csp
        assert "http://dash.local" in csp
