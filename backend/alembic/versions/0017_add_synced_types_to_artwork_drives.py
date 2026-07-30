"""add synced_types to artwork_drives

Revision ID: 0017_artwork_synced_types
Revises: 0016_seed_asset_renamer_libraries
Create Date: 2026-07-29 00:00:00

Per-drive artwork type selection: a drive can sync any subset of logos / backgrounds /
squareart. Existing drives keep all three so nothing changes on upgrade.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0017_artwork_synced_types"
down_revision: Union[str, None] = "0016_seed_asset_renamer_libraries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL_TYPES = "logo,background,squareart"


def upgrade() -> None:
    op.add_column(
        "artwork_drives",
        sa.Column("synced_types", sa.String(), nullable=False, server_default=ALL_TYPES),
    )


def downgrade() -> None:
    op.drop_column("artwork_drives", "synced_types")
