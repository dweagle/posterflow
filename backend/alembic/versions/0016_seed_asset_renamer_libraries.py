"""seed asset_renamer_libraries from poster_renamer_libraries

Revision ID: 0016_seed_asset_renamer_libraries
Revises: 0015_add_artwork
Create Date: 2026-07-25 00:00:00

The merged Asset Renamer reads asset_renamer_libraries everywhere (scheduler,
workflows, API). The frontend seeds it from the legacy poster selection the first
time the Asset Manager library picker loads, but a scheduled run can fire before
that ever happens — and a missing setting means "no filter", silently dropping the
user's library selection. Seed it here so the filter survives the upgrade.

The legacy poster_renamer_libraries row is left in place: pre-0.12 stored job
configs without a library_setting_key still fall back to it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0016_seed_asset_renamer_libraries"
down_revision: Union[str, None] = "0015_add_artwork"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    settings = sa.table("settings", sa.column("key", sa.String), sa.column("value", sa.String))

    def get_value(key: str):
        row = conn.execute(sa.select(settings.c.value).where(settings.c.key == key)).fetchone()
        return row[0] if row else None

    existing = get_value("asset_renamer_libraries")
    if existing and existing.strip():
        return

    legacy = get_value("poster_renamer_libraries")
    if not legacy or not legacy.strip():
        return

    row = conn.execute(
        sa.select(settings.c.key).where(settings.c.key == "asset_renamer_libraries")
    ).fetchone()
    if row:
        conn.execute(
            settings.update()
            .where(settings.c.key == "asset_renamer_libraries")
            .values(value=legacy)
        )
    else:
        conn.execute(settings.insert().values(key="asset_renamer_libraries", value=legacy))


def downgrade() -> None:
    # Data migrations are not reversible
    pass
