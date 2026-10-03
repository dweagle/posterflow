"""migrate tmdb_api_key to global setting

Revision ID: 0004_migrate_tmdb_api_key
Revises: 0003_add_display_name
Create Date: 2026-05-03 00:00:00

Consolidates the TMDB API key from two legacy locations:
  - settings.unmatched_tmdb_api_key (standalone setting)
  - settings.maker_tools_idarr_config (embedded JSON field)
into a single global settings.tmdb_api_key row.
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_migrate_tmdb_api_key"
down_revision: Union[str, None] = "0003_add_display_name"
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

    # Preserve populated globals; distinguish a missing row from an empty value.
    existing_global = conn.execute(
        sa.select(settings.c.value).where(settings.c.key == "tmdb_api_key")
    ).fetchone()
    if existing_global is None or not (existing_global[0] or "").strip():
        migrated_key = None

        # Source 1: unmatched_tmdb_api_key
        legacy = get_value("unmatched_tmdb_api_key")
        if legacy and legacy.strip() and legacy.strip() != "***masked***":
            migrated_key = legacy.strip()

        # Source 2: tmdb_api_key inside maker_tools_idarr_config JSON
        if not migrated_key:
            cfg = _parse_config(get_value("maker_tools_idarr_config"))
            if cfg is not None:
                embedded = str(cfg.get("tmdb_api_key") or "").strip()
                if embedded and embedded != "***masked***":
                    migrated_key = embedded

        # Source 3: tmdb_api_key inside maker_tools_monitor_config JSON
        if not migrated_key:
            cfg = _parse_config(get_value("maker_tools_monitor_config"))
            if cfg is not None:
                embedded = str(cfg.get("tmdb_api_key") or "").strip()
                if embedded and embedded != "***masked***":
                    migrated_key = embedded

        if migrated_key:
            if existing_global is None:
                conn.execute(settings.insert().values(key="tmdb_api_key", value=migrated_key))
            else:
                conn.execute(
                    settings.update().where(settings.c.key == "tmdb_api_key")
                    .values(value=migrated_key)
                )

    # Remove unmatched_tmdb_api_key row
    conn.execute(settings.delete().where(settings.c.key == "unmatched_tmdb_api_key"))

    # Strip tmdb_api_key field from maker_tools_idarr_config JSON
    cfg = _parse_config(get_value("maker_tools_idarr_config"))
    if cfg is not None and "tmdb_api_key" in cfg:
        del cfg["tmdb_api_key"]
        conn.execute(
            settings.update()
            .where(settings.c.key == "maker_tools_idarr_config")
            .values(value=json.dumps(cfg))
        )

    # Strip tmdb_api_key field from maker_tools_monitor_config JSON
    cfg = _parse_config(get_value("maker_tools_monitor_config"))
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
