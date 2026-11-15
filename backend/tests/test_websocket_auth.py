"""Password check on the websockets. The HTTP middleware never sees them, so each handler
calls core.auth.authorize_websocket right after accepting."""
import time
from unittest.mock import patch

import pytest
from starlette.websockets import WebSocketDisconnect

from api import job_logs
from core import auth
from core.auth import WS_UNAUTHORIZED, _cache, hash_password

SOCKETS = ["/api/jobs/ws", "/api/logs/ws", "/api/job-logs/workflow/live"]
PASSWORD = "testpass"


@pytest.fixture(autouse=True)
def _steady_cache(monkeypatch):
    # the middleware cache reads the real session factory, not the test one: seed it and keep it
    monkeypatch.setattr(auth._PasswordCache, "is_fresh", lambda self: True)
    monkeypatch.setattr(auth, "_WS_AUTH_TIMEOUT", 0.2)
    auth._ws_last_warned.clear()
    yield
    _cache.hash_val = _cache.salt_val = _cache.token_ok = ""
    _cache.loaded_at = 0.0
    auth._ws_last_warned.clear()


@pytest.fixture
def password():
    salt_hex, hash_hex = hash_password(PASSWORD)
    _cache.hash_val, _cache.salt_val, _cache.loaded_at, _cache.token_ok = hash_hex, salt_hex, time.monotonic(), ""
    return PASSWORD


@pytest.fixture
def workflow_log(tmp_path, monkeypatch):
    (tmp_path / "workflow").mkdir()
    (tmp_path / "workflow" / "workflow.log").write_text("hello\n")
    monkeypatch.setattr(job_logs.settings, "log_file", str(tmp_path / "app.log"))


def _close_code(client, path, first_message=None):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(path) as ws:
            if first_message is not None:
                ws.send_text(first_message)
            ws.receive_json()
    return closed.value.code


@pytest.mark.parametrize("path", SOCKETS)
def test_socket_closes_without_the_password(client, password, path):
    assert _close_code(client, path) == WS_UNAUTHORIZED
    assert _close_code(client, path, '{"type": "auth", "token": "wrong"}') == WS_UNAUTHORIZED
    assert _close_code(client, path, '{"type": "auth"}') == WS_UNAUTHORIZED
    assert _close_code(client, path, "not json") == WS_UNAUTHORIZED


def test_job_log_socket_streams_once_the_password_is_sent(client, password, workflow_log):
    with client.websocket_connect("/api/job-logs/workflow/live") as ws:
        ws.send_json({"type": "auth", "token": password})
        assert ws.receive_json() == {"type": "content", "content": "hello\n"}


def test_jobs_socket_streams_once_the_password_is_sent(client, password):
    with patch("api.jobs._recent_jobs_snapshot", return_value=[]):
        with client.websocket_connect("/api/jobs/ws") as ws:
            ws.send_json({"type": "auth", "token": password})
            assert ws.receive_json() == {"jobs": []}


def test_no_password_means_no_auth_message(client, workflow_log):
    with client.websocket_connect("/api/job-logs/workflow/live") as ws:
        assert ws.receive_json() == {"type": "content", "content": "hello\n"}

    # a browser that still holds a saved password sends it anyway; it is ignored
    with client.websocket_connect("/api/job-logs/workflow/live") as ws:
        ws.send_json({"type": "auth", "token": "leftover"})
        assert ws.receive_json() == {"type": "content", "content": "hello\n"}


def test_repeat_rejections_log_each_socket_once_a_minute(client, password):
    with patch("core.auth.log_warning") as warn:
        for _ in range(3):
            _close_code(client, "/api/jobs/ws")
        _close_code(client, "/api/logs/ws", '{"type": "auth", "token": "wrong"}')
    assert [call.args[1] for call in warn.call_args_list] == [
        "Unauthorized websocket – missing token (/api/jobs/ws)",
        "Unauthorized websocket – invalid token (/api/logs/ws)",
    ]
