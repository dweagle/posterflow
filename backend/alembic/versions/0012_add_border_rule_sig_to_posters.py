"""Add border_rule_sig to posters

Revision ID: 0012_add_border_rule_sig_to_posters
Revises: 0011_posters_drive_last_processed_idx
Create Date: 2026-07-05

Adds border_rule_sig (String, nullable) to posters — a per-poster fingerprint of the
Plex label/genre/collection border rule applied on the last run. Incremental mode compares
each item's current applicable rule to this stored value so it reprocesses ONLY the items
whose rule actually changed (e.g. when Kometa adds/removes a label so an item starts or
stops matching), instead of resetting and reprocessing every poster. Existing rows get NULL
(equivalent to "no rule applied") and only rule-matched items reprocess once to populate it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_add_border_rule_sig_to_posters"
down_revision: Union[str, None] = "0011_posters_drive_last_processed_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("posters") as batch_op:
        batch_op.add_column(sa.Column("border_rule_sig", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("posters") as batch_op:
        batch_op.drop_column("border_rule_sig")
