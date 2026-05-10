"""Add file_mtime to plex_upload_records

Revision ID: 0006_add_file_mtime_to_plex_upload_records
Revises: 0005_migrate_monitor_tmdb_api_key
Create Date: 2026-05-10

Adds file_mtime (Float, nullable) to plex_upload_records for a fast mtime pre-check
before sha256 hashing. Existing rows get NULL and fall back to hash comparison once,
then populate mtime going forward.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_add_file_mtime_to_plex_upload_records"
down_revision: Union[str, None] = "0005_migrate_monitor_tmdb_api_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("plex_upload_records") as batch_op:
        batch_op.add_column(sa.Column("file_mtime", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plex_upload_records") as batch_op:
        batch_op.drop_column("file_mtime")
