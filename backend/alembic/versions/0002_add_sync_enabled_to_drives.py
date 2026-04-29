"""add sync_enabled to drives

Revision ID: 0002_add_sync_enabled
Revises: 0001_initial
Create Date: 2026-04-29 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_add_sync_enabled"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add sync_enabled column — True by default (existing drives keep rclone sync)
    op.add_column(
        "drives",
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default="1"),
    )

    # Drives that were already configured as local-only (drive_id starts with "manual-")
    # should have sync_enabled set to False since they never ran rclone.
    op.execute(
        "UPDATE drives SET sync_enabled = 0 WHERE drive_id LIKE 'manual-%'"
    )


def downgrade() -> None:
    op.drop_column("drives", "sync_enabled")
