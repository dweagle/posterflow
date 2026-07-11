"""add workflows table

Revision ID: 0014_add_workflows
Revises: 0013_add_poster_tmdb_id
Create Date: 2026-07-10 00:00:00

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0014_add_workflows"
down_revision: Union[str, None] = "0013_add_poster_tmdb_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Inlined default so the migration stays independent of application models.
_DEFAULT_FLOW_CONFIG = {
    "idarr": {"enabled": False, "stop_on_error": False, "scope_indices": [], "sync_after_run": False},
    "sync_drives": {"enabled": True, "stop_on_error": True},
    "rename_posters": {"enabled": True, "stop_on_error": True},
    "detect_unmatched": {"enabled": True, "stop_on_error": True},
    "border_replacer": {"enabled": False, "stop_on_error": True},
    "plex_upload": {"enabled": False, "stop_on_error": False},
    "cleanup_assets": {"enabled": True, "delete_unknown": False},
}


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("config", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflows_id"), "workflows", ["id"], unique=False)

    # Seed a "Default" workflow from the existing single flow config (if any).
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = 'poster_flow_config'")
    ).fetchone()
    config_value = existing[0] if existing and existing[0] else json.dumps(_DEFAULT_FLOW_CONFIG)

    bind.execute(
        sa.text(
            "INSERT INTO workflows (name, config, is_default, created_at) "
            "VALUES (:name, :config, :is_default, CURRENT_TIMESTAMP)"
        ),
        {"name": "Default", "config": config_value, "is_default": True},
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workflows_id"), table_name="workflows")
    op.drop_table("workflows")
