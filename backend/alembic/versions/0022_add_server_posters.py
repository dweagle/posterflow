"""add server_posters (what Asset Upload last put on each media server item)

Revision ID: 0023_server_posters
Revises: 0021_override_file
Create Date: 2026-10-26 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0022_server_posters"
down_revision: Union[str, None] = "0021_override_file"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server_posters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("hint", sa.String(), nullable=True),
        sa.Column("sha1", sa.String(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("instance", "item_id", name="uq_server_posters_item"),
    )
    op.create_index("ix_server_posters_instance", "server_posters", ["instance"])


def downgrade() -> None:
    op.drop_index("ix_server_posters_instance", table_name="server_posters")
    op.drop_table("server_posters")
