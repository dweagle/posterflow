"""
App-level password authentication.

Passwords are stored as PBKDF2-HMAC-SHA256 hashes (stdlib only, no extra deps).
The middleware guards all /api/* routes when a password has been configured.
Routes under /api/auth/ and a few utility endpoints are always exempt.
"""
import hashlib
import secrets
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session
from database import SessionLocal
from models.setting import get_setting, upsert_setting
from core.logging import LogTags, log_info, log_warning

APP_PASSWORD_HASH_KEY = "app_password_hash"  # nosec B105 - this is a DB setting key name, not a password value
APP_PASSWORD_SALT_KEY = "app_password_salt"  # nosec B105 - this is a DB setting key name, not a password value
WEBHOOK_TOKEN_KEY = "plex_webhook_token"  # nosec B105 - this is a DB setting key name, not a password value

# Paths always exempt from auth (must be reachable before the user unlocks the app)
_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/api/health",
    "/api/version",
    "/api/version/update",
    "/api/auth/status",
    "/api/auth/verify",
    "/api/posterflow/plex-upload/webhook",
})

# Path prefixes that are always exempt (static frontend assets, WebSocket, etc.)
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/auth/",
    "/ws",
    "/api/stats/posters/",
    "/api/idarr/pending-matches/source-image",
    "/api/posterflow/plex-upload/source-image",
    "/api/posterflow/plex-upload/plex-thumb",
)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _pbkdf2(password: str, salt: bytes) -> str:
    """Return hex-encoded PBKDF2-HMAC-SHA256 digest."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
    return dk.hex()


def hash_password(password: str) -> tuple[str, str]:
    """Generate a fresh salt, hash the password. Returns (salt_hex, hash_hex)."""
    salt = secrets.token_bytes(32)
    return salt.hex(), _pbkdf2(password, salt)


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Constant-time comparison of a candidate password against stored values."""
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = _pbkdf2(password, salt)
    return secrets.compare_digest(candidate, hash_hex)


# ---------------------------------------------------------------------------
# DB-level helpers (used by the API router)
# ---------------------------------------------------------------------------

def is_password_set(db: Session) -> bool:
    """Return True if an app password has been configured in the database."""
    setting = get_setting(db, APP_PASSWORD_HASH_KEY)
    return bool(setting and setting.value)


def check_password(db: Session, password: str) -> bool:
    """Return True if `password` matches the stored hash (or no password is set)."""
    hash_setting = get_setting(db, APP_PASSWORD_HASH_KEY)
    salt_setting = get_setting(db, APP_PASSWORD_SALT_KEY)

    if not hash_setting or not hash_setting.value:
        return True  # no password configured – open access

    if not salt_setting or not salt_setting.value:
        return False  # hash exists but salt is missing – deny

    return verify_password(password, salt_setting.value, hash_setting.value)


def set_password(db: Session, new_password: str) -> None:
    """Hash and persist a new password. Pass empty string to remove authentication."""
    if not new_password:
        upsert_setting(db, APP_PASSWORD_HASH_KEY, "")
        upsert_setting(db, APP_PASSWORD_SALT_KEY, "")
        log_info(LogTags.API, "App password removed")
    else:
        salt_hex, hash_hex = hash_password(new_password)
        upsert_setting(db, APP_PASSWORD_HASH_KEY, hash_hex)
        upsert_setting(db, APP_PASSWORD_SALT_KEY, salt_hex)
        log_info(LogTags.API, "App password updated")


# ---------------------------------------------------------------------------
# Webhook token
# ---------------------------------------------------------------------------
# Inbound ARR/Plex webhooks can't carry the app Bearer token, so the webhook
# path is exempt from the password middleware. When a password IS set, the
# webhook handler instead authenticates the caller with this standalone token
# (appended to the webhook URL as ?token=...), so the endpoint isn't left open.

def get_webhook_token(db: Session) -> str:
    """Return the stored webhook token, or '' if none has been generated yet."""
    setting = get_setting(db, WEBHOOK_TOKEN_KEY)
    return setting.value if setting and setting.value else ""


def get_or_create_webhook_token(db: Session) -> str:
    """Return the webhook token, generating and persisting one if absent."""
    existing = get_webhook_token(db)
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    upsert_setting(db, WEBHOOK_TOKEN_KEY, token)
    log_info(LogTags.API, "Generated Plex webhook token")
    return token


def regenerate_webhook_token(db: Session) -> str:
    """Generate a fresh webhook token, replacing any existing one."""
    token = secrets.token_urlsafe(32)
    upsert_setting(db, WEBHOOK_TOKEN_KEY, token)
    log_info(LogTags.API, "Regenerated Plex webhook token")
    return token


def verify_webhook_token(db: Session, candidate: str) -> bool:
    """Constant-time comparison of a candidate webhook token against the stored value."""
    stored = get_webhook_token(db)
    if not stored or not candidate:
        return False
    return secrets.compare_digest(candidate, stored)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# Simple time-based cache so we don't hit the DB on every single request.
_CACHE_TTL = 5.0  # seconds

class _PasswordCache:
    __slots__ = ("hash_val", "salt_val", "loaded_at")

    def __init__(self) -> None:
        self.hash_val: str = ""
        self.salt_val: str = ""
        self.loaded_at: float = 0.0

    def is_fresh(self) -> bool:
        return (time.monotonic() - self.loaded_at) < _CACHE_TTL

    def load(self, db: Session) -> None:
        hash_s = get_setting(db, APP_PASSWORD_HASH_KEY)
        salt_s = get_setting(db, APP_PASSWORD_SALT_KEY)
        self.hash_val = hash_s.value if hash_s and hash_s.value else ""
        self.salt_val = salt_s.value if salt_s and salt_s.value else ""
        self.loaded_at = time.monotonic()

    def invalidate(self) -> None:
        self.loaded_at = 0.0


_cache = _PasswordCache()


def invalidate_auth_cache() -> None:
    """Call this after changing the password so the next request re-reads from the DB."""
    _cache.invalidate()


class AppAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces the app password when one is configured."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only guard /api/* — static frontend files pass through freely
        if not path.startswith("/api/"):
            return await call_next(request)

        # Always-exempt individual paths
        if path in _EXEMPT_PATHS:
            return await call_next(request)

        # Always-exempt prefixes
        for prefix in _EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Load cached password (refresh from DB if stale)
        db: Session = SessionLocal()
        try:
            if not _cache.is_fresh():
                _cache.load(db)
        finally:
            db.close()

        # If no password is configured, allow everything through
        if not _cache.hash_val:
            return await call_next(request)

        # Check the Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            log_warning(LogTags.API, f"Unauthorized API request – missing Bearer token ({path})")
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        token = auth_header[7:]
        if not verify_password(token, _cache.salt_val, _cache.hash_val):
            log_warning(LogTags.API, f"Unauthorized API request – invalid token ({path})")
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        return await call_next(request)
