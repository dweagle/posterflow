"""add tool_profiles (named settings sets of the Tools page tools)

A tool with settings keeps named profiles. A tool that runs per instance (the arr renamers)
keeps a profile list, with a default of its own, for each instance: `instance` says whose a
profile is, NULL for a tool with one list.

Revision ID: 0024_tool_profiles
Revises: 0023_overlay_tables
Create Date: 2026-11-08 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0024_tool_profiles"
down_revision: Union[str, None] = "0023_overlay_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("instance", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("settings", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_profiles_id"), "tool_profiles", ["id"], unique=False)
    op.create_index(op.f("ix_tool_profiles_tool_id"), "tool_profiles", ["tool_id"], unique=False)
    op.create_index(op.f("ix_tool_profiles_instance"), "tool_profiles", ["instance"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_profiles_instance"), table_name="tool_profiles")
    op.drop_index(op.f("ix_tool_profiles_tool_id"), table_name="tool_profiles")
    op.drop_index(op.f("ix_tool_profiles_id"), table_name="tool_profiles")
    op.drop_table("tool_profiles")
