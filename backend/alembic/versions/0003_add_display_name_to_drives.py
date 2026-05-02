"""add display_name to drives

Revision ID: 0003_add_display_name
Revises: 0002_add_sync_enabled
Create Date: 2026-05-02 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_add_display_name"
down_revision: Union[str, None] = "0002_add_sync_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "drives",
        sa.Column("display_name", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drives", "display_name")
