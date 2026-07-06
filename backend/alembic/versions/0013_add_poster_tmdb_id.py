"""add indexed tmdb_id column to posters

Revision ID: 0013_add_poster_tmdb_id
Revises: 0012_add_border_rule_sig_to_posters
Create Date: 2026-07-05 00:00:00

The TMDB poster-availability check (green check / red X on TMDB item cards) matched
each item with a leading-wildcard `file_name ILIKE '%{tmdb-<id>}%'`, forcing a full
table scan per item. This adds an indexed integer `tmdb_id` column, backfilled from
the `{tmdb-<id>}` tag already embedded in every synced filename, so the check can do
an index lookup (`WHERE tmdb_id IN (...)`) instead. New rows populate it at sync time.
"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_add_poster_tmdb_id"
down_revision: Union[str, None] = "0012_add_border_rule_sig_to_posters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Strict "{tmdb-<digits>}" tag — the same form the runtime helper (extract_tmdb_id)
# uses, so a malformed tag like "{tmdb-422-8}" is left NULL and handled by the title
# fallback, matching the old `file_name ILIKE '%{tmdb-<id>}%'` behavior exactly.
_TMDB_TAG_RE = re.compile(r"\{tmdb-(\d+)\}", re.IGNORECASE)


def upgrade() -> None:
    op.add_column("posters", sa.Column("tmdb_id", sa.Integer(), nullable=True))

    conn = op.get_bind()
    posters = sa.table(
        "posters",
        sa.column("id", sa.Integer),
        sa.column("file_name", sa.String),
        sa.column("tmdb_id", sa.Integer),
    )
    updates = []
    for pid, file_name in conn.execute(sa.select(posters.c.id, posters.c.file_name)):
        match = _TMDB_TAG_RE.search(file_name or "")
        if match:
            updates.append({"pid": pid, "tid": int(match.group(1))})
    if updates:
        conn.execute(
            posters.update().where(posters.c.id == sa.bindparam("pid")).values(tmdb_id=sa.bindparam("tid")),
            updates,
        )

    op.create_index("ix_posters_tmdb_id", "posters", ["tmdb_id"])


def downgrade() -> None:
    op.drop_index("ix_posters_tmdb_id", table_name="posters")
    with op.batch_alter_table("posters") as batch_op:
        batch_op.drop_column("tmdb_id")
