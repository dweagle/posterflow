"""Database portability guards: the app must work on SQLite and PostgreSQL.

These run against SQLite only. PostgreSQL-specific SQL is verified by compiling
with the PG dialect, so no server and no driver are needed.
"""
import re
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.dialects import postgresql, sqlite

BACKEND_DIR = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"


def _source(relative_path: str) -> str:
    return (BACKEND_DIR / relative_path).read_text()


def test_startup_log_reports_driver_not_url():
    """A percent-encoded password in DATABASE_URL must never reach the logs."""
    proc = subprocess.run(
        [sys.executable, "-c", "import database"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    assert "Database configured" in output, output[-2000:]
    line = next(l for l in output.splitlines() if "Database configured" in l)

    from core.config import settings

    assert settings.database_url not in line
    assert settings.database_url.split("://")[0] in line


def test_alembic_config_url_percent_escaped():
    """Config interpolation eats bare %, so URLs must be escaped before set."""
    url = "postgresql+psycopg://user:ci%25pass@localhost:5432/posterflow"
    config = Config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url

    # Both migration entry points must escape; Alembic raises on a bare '%'.
    assert 'settings.database_url.replace("%", "%%")' in _source("main.py")
    assert 'settings.database_url.replace("%", "%%")' in _source("alembic/env.py")


def test_env_registers_plex_upload_metadata():
    """Autogenerate and the 0016+ revisions need the table in Base.metadata."""
    from database import Base
    from models.plex_upload import PlexUploadRecord

    assert PlexUploadRecord.__tablename__ in Base.metadata.tables
    assert "from models.plex_upload import PlexUploadRecord" in _source("alembic/env.py")


def test_boolean_defaults_are_dialect_neutral():
    """sa.text("0") is an integer; PostgreSQL rejects it for a boolean column."""
    for dialect, rendered in ((postgresql.dialect(), "false"), (sqlite.dialect(), "0")):
        column = sa.Column("flag", sa.Boolean(), nullable=False, server_default=sa.false())
        ddl = str(sa.schema.CreateColumn(column).compile(dialect=dialect)).lower()
        assert f"default {rendered}" in ddl

    # Only boolean columns matter here; integer columns keep their numeric default.
    for revision in ("0014_add_workflows.py", "0015_add_artwork.py"):
        offenders = [
            line.strip()
            for line in (VERSIONS_DIR / revision).read_text().splitlines()
            if "sa.Boolean()" in line and "server_default=sa.text(" in line
        ]
        assert not offenders, f"{revision}: {offenders}"

    # op.execute() sends the string straight to the driver, so it must be a bool literal.
    source = (VERSIONS_DIR / "0002_add_sync_enabled_to_drives.py").read_text()
    assert "SET sync_enabled = FALSE" in source
    assert "SET sync_enabled = 0" not in source


def test_recency_expression_is_portable():
    """strftime() is SQLite-only; epoch extraction exists on both backends."""
    from api.stats import _content_recency
    from models.poster import Poster

    ddl = str(_content_recency(Poster).compile(dialect=postgresql.dialect()))
    assert "epoch" in ddl.lower()
    assert "strftime" not in ddl.lower()
    assert str(_content_recency(Poster).compile(dialect=sqlite.dialect()))


def test_daily_activity_uses_aware_utc_bounds():
    """Bounds stay UTC-aware so PG comparisons ignore the session timezone."""
    source = _source("api/stats.py")
    daily = source.split("def get_poster_daily_activity")[1]
    assert ".replace(tzinfo=None)" not in daily
    assert ".astimezone(timezone.utc)" in daily


def test_daily_activity_endpoint_smoke(client):
    assert client.get("/api/stats/poster-daily-activity").status_code == 200


def test_idarr_ordering_pushes_nulls_last(client, test_db):
    """PostgreSQL sorts NULLs first on DESC; unfinished runs must sort last."""
    from sqlalchemy import event

    statements: list[str] = []
    engine = test_db.get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        assert client.get(
            "/api/idarr/last-run", params={"sync_target_index": 0}
        ).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    ordering = next(s for s in statements if "ORDER BY" in s and "completed_at" in s)
    assert "NULLS LAST" in ordering.upper()
    # id stays the tiebreaker so equal timestamps keep a stable order.
    assert re.search(r"id\s+DESC", ordering, re.IGNORECASE)