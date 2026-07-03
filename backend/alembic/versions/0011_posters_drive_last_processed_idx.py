"""Reindex posters for the drives listing

Revision ID: 0011_posters_drive_last_processed_idx
Revises: 0010_idarr_idkeyed_cache
Create Date: 2026-07-02

Add a composite index on posters(drive_id, last_processed) so the per-drive
"unprocessed" count (WHERE drive_id = ? AND last_processed IS NULL) is
index-only instead of reading every matching row. The standalone drive_id
index is then redundant (the composite covers drive_id-prefix lookups) and is
dropped.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0011_posters_drive_last_processed_idx"
down_revision: Union[str, None] = "0010_idarr_idkeyed_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_posters_drive_last_processed",
        "posters",
        ["drive_id", "last_processed"],
        unique=False,
    )
    # Redundant now that the composite index covers drive_id-prefix lookups.
    op.drop_index("ix_posters_drive_id", table_name="posters")


def downgrade() -> None:
    op.create_index("ix_posters_drive_id", "posters", ["drive_id"], unique=False)
    op.drop_index("ix_posters_drive_last_processed", table_name="posters")
