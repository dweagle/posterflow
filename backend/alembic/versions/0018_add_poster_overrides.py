"""add poster_overrides table (per-item drive picks from the Drive Usage report)

Revision ID: 0018_poster_overrides
Revises: 0017_artwork_synced_types
Create Date: 2026-08-22 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0018_poster_overrides"
down_revision: Union[str, None] = "0017_artwork_synced_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "poster_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("tvdb_id", sa.Integer(), nullable=True),
        sa.Column("imdb_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False, server_default="slot"),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("drive_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_poster_overrides_tmdb_id", "poster_overrides", ["tmdb_id"])


def downgrade() -> None:
    op.drop_index("ix_poster_overrides_tmdb_id", table_name="poster_overrides")
    op.drop_table("poster_overrides")
