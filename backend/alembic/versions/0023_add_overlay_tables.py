"""add overlay_items and overlay_metadata_cache (overlay state moves from JSON files into the DB)

Revision ID: 0023_overlay_tables
Revises: 0022_server_posters
Create Date: 2026-10-25 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0023_overlay_tables"
down_revision: Union[str, None] = "0022_server_posters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "overlay_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("item_type", sa.String(), nullable=True),
        sa.Column("library", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("sig", sa.String(), nullable=True),
        sa.Column("source_path", sa.String(), nullable=True),
        sa.Column("source_sha1", sa.String(), nullable=True),
        sa.Column("composite_sha1", sa.String(), nullable=True),
        sa.Column("server_key", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("instance", "item_id", name="uq_overlay_items_instance_item"),
    )
    op.create_index("ix_overlay_items_id", "overlay_items", ["id"])
    op.create_index("ix_overlay_items_instance", "overlay_items", ["instance"])
    op.create_table(
        "overlay_metadata_cache",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Float(), nullable=True),
    )
    op.create_index("ix_overlay_metadata_cache_fetched_at", "overlay_metadata_cache", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_overlay_metadata_cache_fetched_at", table_name="overlay_metadata_cache")
    op.drop_table("overlay_metadata_cache")
    op.drop_index("ix_overlay_items_instance", table_name="overlay_items")
    op.drop_index("ix_overlay_items_id", table_name="overlay_items")
    op.drop_table("overlay_items")
