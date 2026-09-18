"""add file to poster_overrides (pin one specific drive file instead of a whole drive)

Revision ID: 0021_override_file
Revises: 0020_poster_reminders
Create Date: 2026-09-17 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0021_override_file"
down_revision: Union[str, None] = "0020_poster_reminders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("poster_overrides", sa.Column("file", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("poster_overrides", "file")
