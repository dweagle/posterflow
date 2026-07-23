"""add artwork feature tables (artwork_drives + artwork index)

Revision ID: 0015_add_artwork
Revises: 0014_add_workflows
Create Date: 2026-07-14 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0015_add_artwork"
down_revision: Union[str, None] = "0014_add_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Artwork drives (Google Drives holding logos/backgrounds/squareart)
    op.create_table(
        "artwork_drives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("drive_id", sa.String(), nullable=False),
        sa.Column("subscribed", sa.Boolean(), nullable=True, server_default=sa.text("0")),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("priority", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("custom_path", sa.String(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=True, server_default=sa.text("0")),
        sa.Column("is_deprecated", sa.Boolean(), nullable=True, server_default=sa.text("0")),
        sa.Column("last_synced", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rename_processed", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_file_count", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("last_files_transferred", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_artwork_drives_id"), "artwork_drives", ["id"], unique=False)
    op.create_index(op.f("ix_artwork_drives_drive_id"), "artwork_drives", ["drive_id"], unique=True)

    # Artwork file index (one row per logo/background/squareart file on disk)
    op.create_table(
        "artwork",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artwork_drive_id", sa.String(), nullable=False),
        sa.Column("artwork_type", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("tvdb_id", sa.Integer(), nullable=True),
        sa.Column("imdb_id", sa.String(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_mtime", sa.Float(), nullable=True),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.Column("last_processed", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_path"),
    )
    op.create_index(op.f("ix_artwork_id"), "artwork", ["id"], unique=False)
    op.create_index(op.f("ix_artwork_tmdb_id"), "artwork", ["tmdb_id"], unique=False)
    op.create_index("ix_artwork_drive_type", "artwork", ["artwork_drive_id", "artwork_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_artwork_drive_type", table_name="artwork")
    op.drop_index(op.f("ix_artwork_tmdb_id"), table_name="artwork")
    op.drop_index(op.f("ix_artwork_id"), table_name="artwork")
    op.drop_table("artwork")
    op.drop_index(op.f("ix_artwork_drives_drive_id"), table_name="artwork_drives")
    op.drop_index(op.f("ix_artwork_drives_id"), table_name="artwork_drives")
    op.drop_table("artwork_drives")
