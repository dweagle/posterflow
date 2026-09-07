"""add poster_reminders table (maker-card reminders with a note)

Revision ID: 0020_poster_reminders
Revises: 0019_override_domain_slot
Create Date: 2026-09-07 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0020_poster_reminders"
down_revision: Union[str, None] = "0019_override_domain_slot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "poster_reminders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="poster"),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("tvdb_id", sa.Integer(), nullable=True),
        sa.Column("imdb_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("year", sa.String(), nullable=True),
        sa.Column("poster_url", sa.String(), nullable=True),
        sa.Column("homepage", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_poster_reminders_tmdb_id", "poster_reminders", ["tmdb_id"])


def downgrade() -> None:
    op.drop_index("ix_poster_reminders_tmdb_id", table_name="poster_reminders")
    op.drop_table("poster_reminders")
