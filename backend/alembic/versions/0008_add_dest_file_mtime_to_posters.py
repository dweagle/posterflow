"""Add dest_file_mtime to posters

Revision ID: 0008_add_dest_file_mtime_to_posters
Revises: 0007_add_manual_media_entries
Create Date: 2026-06-03

Adds dest_file_mtime (Float, nullable) to posters for tracking the destination
file's mtime after each border replacer write. Used by incremental mode to detect
when a destination file has been externally modified (e.g. replaced by another app)
so it can be reprocessed even when the source file is unchanged.
Existing rows get NULL and will be reprocessed once to populate the value.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_add_dest_file_mtime_to_posters"
down_revision: Union[str, None] = "0007_add_manual_media_entries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("posters") as batch_op:
        batch_op.add_column(sa.Column("dest_file_mtime", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("posters") as batch_op:
        batch_op.drop_column("dest_file_mtime")
