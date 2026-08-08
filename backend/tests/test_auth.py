"""
Tests for the authentication system:
  - core/auth.py  (PBKDF2 helpers, DB-level helpers, AppAuthMiddleware)
  - api/auth.py   (verify, set, clear, status endpoints)
"""
import pytest
import time
from core.auth import (
    hash_password,
    verify_password,
    check_password,
    is_password_set,
    set_password,
    invalidate_auth_cache,
    _cache,
    APP_PASSWORD_HASH_KEY,
    APP_PASSWORD_SALT_KEY,
)
from models.setting import upsert_setting, get_setting


# ---------------------------------------------------------------------------
# Low-level PBKDF2 helpers
# ---------------------------------------------------------------------------

class TestVerifyPassword:
    def test_correct_password_matches(self):
        salt_hex, hash_hex = hash_password("supersecret")
        assert verify_password("supersecret", salt_hex, hash_hex) is True

    def test_wrong_password_rejected(self):
        salt_hex, hash_hex = hash_password("supersecret")
        assert verify_password("wrongpassword", salt_hex, hash_hex) is False

    def test_empty_password_rejected_when_hash_set(self):
        salt_hex, hash_hex = hash_password("supersecret")
        assert verify_password("", salt_hex, hash_hex) is False

    def test_invalid_salt_hex_returns_false(self):
        _, hash_hex = hash_password("supersecret")
        assert verify_password("supersecret", "not-valid-hex!", hash_hex) is False

    def test_distinct_hashes_for_same_password(self):
        """Each call produces a different salt, so hashes differ."""
        salt1, hash1 = hash_password("pw")
        salt2, hash2 = hash_password("pw")
        assert hash1 != hash2
        assert salt1 != salt2

    def test_each_hash_still_verifies(self):
        salt1, hash1 = hash_password("pw")
        salt2, hash2 = hash_password("pw")
        assert verify_password("pw", salt1, hash1) is True
        assert verify_password("pw", salt2, hash2) is True


# ---------------------------------------------------------------------------
# DB-level helpers
# ---------------------------------------------------------------------------

class TestCheckPassword:
    def test_no_password_set_returns_true_for_any_input(self, test_db):
        """When no password is configured open access is granted."""
        assert check_password(test_db, "") is True
        assert check_password(test_db, "anything") is True

    def test_correct_password_accepted(self, test_db):
        set_password(test_db, "mypassword")
        test_db.commit()
        invalidate_auth_cache()
        assert check_password(test_db, "mypassword") is True

    def test_wrong_password_rejected(self, test_db):
        set_password(test_db, "mypassword")
        test_db.commit()
        invalidate_auth_cache()
        assert check_password(test_db, "notmypassword") is False

    def test_hash_present_but_missing_salt_is_denied(self, test_db):
        upsert_setting(test_db, APP_PASSWORD_HASH_KEY, "deadbeef")
        # intentionally leave salt unset
        test_db.commit()
        assert check_password(test_db, "anything") is False


class TestIsPasswordSet:
    def test_false_when_not_configured(self, test_db):
        assert is_password_set(test_db) is False

    def test_true_after_password_set(self, test_db):
        set_password(test_db, "secret")
        test_db.commit()
        assert is_password_set(test_db) is True

    def test_false_after_password_cleared(self, test_db):
        set_password(test_db, "secret")
        test_db.commit()
        set_password(test_db, "")
        test_db.commit()
        assert is_password_set(test_db) is False


# ---------------------------------------------------------------------------
# AppAuthMiddleware behaviour
# ---------------------------------------------------------------------------

class TestAuthMiddleware:
    def test_exempt_path_passes_without_token(self, client):
        """GET /api/auth/status is always exempt."""
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200

    def test_health_exempt_without_token(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_protected_route_passes_when_no_password_set(self, client):
        """All routes are open when no password has been configured."""
        resp = client.get("/api/drives/")
        # We don't care about the exact response code (could be 200 or 404 depending on DB
        # state) — we only care it is NOT 401.
        assert resp.status_code != 401

    def test_protected_route_blocked_without_token_when_password_set(self, client):
        """Seed the cache directly so the middleware enforces the password."""
        salt_hex, hash_hex = hash_password("testpass")
        _cache.hash_val = hash_hex
        _cache.salt_val = salt_hex
        _cache.loaded_at = time.monotonic()
        try:
            resp = client.get("/api/drives/")
            assert resp.status_code == 401
        finally:
            _cache.hash_val = ""
            _cache.salt_val = ""
            _cache.loaded_at = 0.0

    def test_plex_webhook_reachable_without_token_when_no_password(self, client):
        """The webhook path is exempt from the password middleware: with no app
        password configured it must be reachable without any token. (Token-based
        enforcement when a password IS set is covered in test_plex_upload_api.py.)"""
        resp = client.post("/api/posterflow/plex-upload/webhook", json={})
        # The handler answers (400/403/200 depending on payload/enabled state),
        # but the middleware must never 401 this path.
        assert resp.status_code != 401

    def test_protected_route_passes_with_correct_token(self, client):
        """Seed the cache directly so the middleware enforces the password."""
        salt_hex, hash_hex = hash_password("testpass")
        _cache.hash_val = hash_hex
        _cache.salt_val = salt_hex
        _cache.loaded_at = time.monotonic()
        try:
            resp = client.get("/api/drives/", headers={"Authorization": "Bearer testpass"})
            assert resp.status_code != 401
        finally:
            _cache.hash_val = ""
            _cache.salt_val = ""
            _cache.loaded_at = 0.0

    def test_protected_route_blocked_with_wrong_token(self, client):
        """Seed the cache directly so the middleware enforces the password."""
        salt_hex, hash_hex = hash_password("testpass")
        _cache.hash_val = hash_hex
        _cache.salt_val = salt_hex
        _cache.loaded_at = time.monotonic()
        try:
            resp = client.get("/api/drives/", headers={"Authorization": "Bearer wrongpass"})
            assert resp.status_code == 401
        finally:
            _cache.hash_val = ""
            _cache.salt_val = ""
            _cache.loaded_at = 0.0


# ---------------------------------------------------------------------------
# /api/auth endpoints
# ---------------------------------------------------------------------------

class TestVerifyEndpoint:
    def test_verify_returns_valid_false_when_no_password_set(self, client):
        # No password configured → check_password() returns True for any input,
        # so the API should reflect that open-access state.
        resp = client.post("/api/auth/verify", json={"password": "anything"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_verify_correct_password(self, client, test_db):
        set_password(test_db, "mypass")
        test_db.commit()
        invalidate_auth_cache()
        try:
            resp = client.post("/api/auth/verify", json={"password": "mypass"})
            assert resp.status_code == 200
            assert resp.json()["valid"] is True
        finally:
            set_password(test_db, "")
            test_db.commit()
            invalidate_auth_cache()

    def test_verify_wrong_password(self, client, test_db):
        set_password(test_db, "mypass")
        test_db.commit()
        invalidate_auth_cache()
        try:
            resp = client.post("/api/auth/verify", json={"password": "wrong"})
            assert resp.status_code == 200
            assert resp.json()["valid"] is False
        finally:
            set_password(test_db, "")
            test_db.commit()
            invalidate_auth_cache()


class TestSetPasswordEndpoint:
    def test_set_password_when_none_configured(self, client, test_db):
        try:
            resp = client.post(
                "/api/auth/set",
                json={"current_password": "", "new_password": "newpass"},
            )
            assert resp.status_code == 200
            assert is_password_set(test_db)
        finally:
            set_password(test_db, "")
            test_db.commit()
            invalidate_auth_cache()

    def test_set_password_rejects_empty_new_password(self, client):
        resp = client.post(
            "/api/auth/set",
            json={"current_password": "", "new_password": ""},
        )
        assert resp.status_code == 400

    def test_change_password_requires_correct_current(self, client, test_db):
        set_password(test_db, "original")
        test_db.commit()
        invalidate_auth_cache()
        try:
            resp = client.post(
                "/api/auth/set",
                json={"current_password": "wrong", "new_password": "newpass"},
            )
            assert resp.status_code == 403
        finally:
            set_password(test_db, "")
            test_db.commit()
            invalidate_auth_cache()

    def test_change_password_succeeds_with_correct_current(self, client, test_db):
        set_password(test_db, "original")
        test_db.commit()
        invalidate_auth_cache()
        try:
            resp = client.post(
                "/api/auth/set",
                json={"current_password": "original", "new_password": "updated"},
            )
            assert resp.status_code == 200
        finally:
            set_password(test_db, "")
            test_db.commit()
            invalidate_auth_cache()


class TestStatusEndpoint:
    def test_status_false_when_no_password(self, client):
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {"password_set": False}

    def test_status_true_after_password_set(self, client, test_db):
        set_password(test_db, "secret")
        test_db.commit()
        try:
            resp = client.get("/api/auth/status")
            assert resp.status_code == 200
            assert resp.json()["password_set"] is True
        finally:
            set_password(test_db, "")
            test_db.commit()
            invalidate_auth_cache()


def test_artwork_local_image_exempt_from_password(client):
    """<img> tags can't send the Bearer header, so /local-image is auth-exempt (path containment
    inside the configured picker folder is its guard). With a password enforced it must still
    reach the endpoint — a 400 (no folder configured) here, never a 401."""
    salt_hex, hash_hex = hash_password("testpass")
    _cache.hash_val = hash_hex
    _cache.salt_val = salt_hex
    _cache.loaded_at = time.monotonic()
    try:
        resp = client.get("/api/artwork-finder/local-image", params={"path": "x.jpg"})
        assert resp.status_code == 400
    finally:
        _cache.hash_val = ""
        _cache.salt_val = ""
        _cache.loaded_at = 0.0
