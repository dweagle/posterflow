"""add poster_borders (the holiday or border rule each placed poster's border came from)

Overlays linked to a holiday or a border rule read it to know which posters they apply to.

Revision ID: 0025_poster_borders
Revises: 0024_tool_profiles
Create Date: 2026-11-23 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0025_poster_borders"
down_revision: Union[str, None] = "0024_tool_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "poster_borders",
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("border", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("file_path"),
    )


def downgrade() -> None:
    op.drop_table("poster_borders")
