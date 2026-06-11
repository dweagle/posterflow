"""Add uploaded_to_rating_keys to plex_upload_records

Revision ID: 0009_add_rating_keys_to_plex_upload_records
Revises: 0008_add_dest_file_mtime_to_posters
Create Date: 2026-06-11

Adds uploaded_to_rating_keys (String, nullable) to plex_upload_records. Stores a JSON
list of the Plex ratingKeys a poster file has been applied to. Plex assigns a new
ratingKey when an item is removed and re-added, so a matched item whose ratingKey is
absent triggers a re-upload. Existing rows get NULL (treated as an empty list); the
current ratingKey is backfilled on the next run without forcing a re-upload.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_add_rating_keys_to_plex_upload_records"
down_revision: Union[str, None] = "0008_add_dest_file_mtime_to_posters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("plex_upload_records") as batch_op:
        batch_op.add_column(sa.Column("uploaded_to_rating_keys", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plex_upload_records") as batch_op:
        batch_op.drop_column("uploaded_to_rating_keys")
