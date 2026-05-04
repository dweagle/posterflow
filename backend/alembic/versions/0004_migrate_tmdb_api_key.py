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


def upgrade() -> None:
    conn = op.get_bind()
    settings = sa.table("settings", sa.column("key", sa.String), sa.column("value", sa.String))

    def get_value(key: str):
        row = conn.execute(sa.select(settings.c.value).where(settings.c.key == key)).fetchone()
        return row[0] if row else None

    # Only write global key if it doesn't already exist
    existing_global = get_value("tmdb_api_key")
    if not existing_global or not existing_global.strip():
        migrated_key = None

        # Source 1: unmatched_tmdb_api_key
        legacy = get_value("unmatched_tmdb_api_key")
        if legacy and legacy.strip():
            migrated_key = legacy.strip()

        # Source 2: tmdb_api_key inside maker_tools_idarr_config JSON
        if not migrated_key:
            idarr_raw = get_value("maker_tools_idarr_config")
            if idarr_raw:
                try:
                    cfg = json.loads(idarr_raw)
                    embedded = str(cfg.get("tmdb_api_key") or "").strip()
                    if embedded and embedded != "***masked***":
                        migrated_key = embedded
                except Exception:
                    pass

        if migrated_key:
            conn.execute(settings.insert().values(key="tmdb_api_key", value=migrated_key))

    # Remove unmatched_tmdb_api_key row
    conn.execute(settings.delete().where(settings.c.key == "unmatched_tmdb_api_key"))

    # Strip tmdb_api_key field from maker_tools_idarr_config JSON
    idarr_raw = get_value("maker_tools_idarr_config")
    if idarr_raw:
        try:
            cfg = json.loads(idarr_raw)
            if "tmdb_api_key" in cfg:
                del cfg["tmdb_api_key"]
                conn.execute(
                    settings.update()
                    .where(settings.c.key == "maker_tools_idarr_config")
                    .values(value=json.dumps(cfg))
                )
        except Exception:
            pass


def downgrade() -> None:
    # Data migrations are not reversible
    pass
