"""migrate monitor tmdb_api_key to global setting

Revision ID: 0005_migrate_monitor_tmdb_api_key
Revises: 0004_migrate_tmdb_api_key
Create Date: 2026-05-04 00:00:00

Migration 0004 missed the tmdb_api_key embedded inside maker_tools_monitor_config.
This migration copies that key to the global tmdb_api_key setting (if not already set)
and strips it from the monitor config JSON.
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "0005_migrate_monitor_tmdb_api_key"
down_revision: Union[str, None] = "0004_migrate_tmdb_api_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _parse_config(raw) -> dict | None:
    try:
        config = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return config if isinstance(config, dict) else None


def upgrade() -> None:
    conn = op.get_bind()
    settings = sa.table("settings", sa.column("key", sa.String), sa.column("value", sa.String))

    def get_value(key: str):
        row = conn.execute(sa.select(settings.c.value).where(settings.c.key == key)).fetchone()
        return row[0] if row else None

    # Only promote if global key is not already set
    existing_global = conn.execute(
        sa.select(settings.c.value).where(settings.c.key == "tmdb_api_key")
    ).fetchone()
    cfg = _parse_config(get_value("maker_tools_monitor_config"))
    if existing_global is None or not (existing_global[0] or "").strip():
        if cfg is not None:
            embedded = str(cfg.get("tmdb_api_key") or "").strip()
            if embedded and embedded != "***masked***":
                if existing_global is None:
                    conn.execute(settings.insert().values(key="tmdb_api_key", value=embedded))
                else:
                    conn.execute(
                        settings.update().where(settings.c.key == "tmdb_api_key")
                        .values(value=embedded)
                    )

    # Strip tmdb_api_key from maker_tools_monitor_config JSON regardless
    if cfg is not None and "tmdb_api_key" in cfg:
        del cfg["tmdb_api_key"]
        conn.execute(
            settings.update()
            .where(settings.c.key == "maker_tools_monitor_config")
            .values(value=json.dumps(cfg))
        )


def downgrade() -> None:
    # Data migrations are not reversible
    pass
