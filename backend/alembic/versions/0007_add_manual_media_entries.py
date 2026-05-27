"""add manual_media_entries table

Revision ID: 0007_add_manual_media_entries
Revises: 0006_add_file_mtime_to_plex_upload_records
Create Date: 2026-05-26 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007_add_manual_media_entries"
down_revision: Union[str, None] = "0006_add_file_mtime_to_plex_upload_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manual_media_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("tvdb_id", sa.Integer(), nullable=True),
        sa.Column("imdb_id", sa.String(), nullable=True),
        sa.Column("seasons_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_manual_media_entries_id"), "manual_media_entries", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_manual_media_entries_id"), table_name="manual_media_entries")
    op.drop_table("manual_media_entries")
