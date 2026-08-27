"""add domain + slot to poster_overrides (artwork override support)

Revision ID: 0019_override_domain_slot
Revises: 0018_poster_overrides
Create Date: 2026-08-24 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0019_override_domain_slot"
down_revision: Union[str, None] = "0018_poster_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "poster_overrides",
        sa.Column("domain", sa.String(), nullable=False, server_default="poster"),
    )
    op.add_column("poster_overrides", sa.Column("slot", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("poster_overrides", "slot")
    op.drop_column("poster_overrides", "domain")
