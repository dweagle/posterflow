"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-03-08 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("drive_id", sa.String(), nullable=False),
        sa.Column("style_type", sa.String(), nullable=False),
        sa.Column("subscribed", sa.Boolean(), nullable=True),
        sa.Column("last_synced", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="50", nullable=False),
        sa.Column("custom_path", sa.String(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("is_deprecated", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("last_rename_processed", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_file_count", sa.Integer(), server_default="0", nullable=True),
        sa.Column("last_files_transferred", sa.Integer(), server_default="0", nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_drives_id"), "drives", ["id"], unique=False)
    op.create_index(op.f("ix_drives_drive_id"), "drives", ["drive_id"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_id"), "jobs", ["id"], unique=False)

    op.create_table(
        "posters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drive_id", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("gdrive_file_id", sa.String(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("file_mtime", sa.Float(), nullable=True),
        sa.Column("last_processed", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_path"),
    )
    op.create_index(op.f("ix_posters_id"), "posters", ["id"], unique=False)
    op.create_index(op.f("ix_posters_drive_id"), "posters", ["drive_id"], unique=False)

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_settings_id"), "settings", ["id"], unique=False)
    op.create_index(op.f("ix_settings_key"), "settings", ["key"], unique=True)

    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("drive_id", sa.Integer(), nullable=True),
        sa.Column("drive_group", sa.String(), nullable=True),
        sa.Column("schedule_type", sa.String(), nullable=False),
        sa.Column("schedule_value", sa.String(), nullable=True),
        sa.Column("job_config", sa.Text(), nullable=True),
        sa.Column("last_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["drive_id"], ["drives.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_schedules_id"), "schedules", ["id"], unique=False)
    op.create_index(op.f("ix_schedules_job_type"), "schedules", ["job_type"], unique=False)


    op.create_table(
        "idarr_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_dir", sa.String(), nullable=True),
        sa.Column("destination_dir", sa.String(), nullable=True),
        sa.Column("stats_json", sa.Text(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("unmatched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scope_token", sa.String(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_idarr_runs_id"), "idarr_runs", ["id"], unique=False)
    op.create_index(op.f("ix_idarr_runs_job_id"), "idarr_runs", ["job_id"], unique=False)
    op.create_index(op.f("ix_idarr_runs_scope_token"), "idarr_runs", ["scope_token"], unique=False)

    op.create_table(
        "idarr_pending_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_idarr_pending_matches_id"), "idarr_pending_matches", ["id"], unique=False)
    op.create_index(op.f("ix_idarr_pending_matches_asset_key"), "idarr_pending_matches", ["asset_key"], unique=True)

    op.create_table(
        "idarr_asset_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("tvdb_id", sa.Integer(), nullable=True),
        sa.Column("imdb_id", sa.String(), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_idarr_asset_cache_id"), "idarr_asset_cache", ["id"], unique=False)
    op.create_index(op.f("ix_idarr_asset_cache_asset_key"), "idarr_asset_cache", ["asset_key"], unique=True)

    op.create_table(
        "plex_upload_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_hash", sa.String(), nullable=True),
        sa.Column("uploaded_to_libraries", sa.String(), nullable=True),
        sa.Column("uploaded_to_library_keys", sa.String(), nullable=True),
        sa.Column("uploaded_editions", sa.String(), nullable=True),
        sa.Column("uploaded_media_types", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plex_upload_records_id"), "plex_upload_records", ["id"], unique=False)
    op.create_index(op.f("ix_plex_upload_records_file_path"), "plex_upload_records", ["file_path"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_plex_upload_records_file_path"), table_name="plex_upload_records")
    op.drop_index(op.f("ix_plex_upload_records_id"), table_name="plex_upload_records")
    op.drop_table("plex_upload_records")

    op.drop_index(op.f("ix_idarr_asset_cache_asset_key"), table_name="idarr_asset_cache")
    op.drop_index(op.f("ix_idarr_asset_cache_id"), table_name="idarr_asset_cache")
    op.drop_table("idarr_asset_cache")

    op.drop_index(op.f("ix_idarr_pending_matches_asset_key"), table_name="idarr_pending_matches")
    op.drop_index(op.f("ix_idarr_pending_matches_id"), table_name="idarr_pending_matches")
    op.drop_table("idarr_pending_matches")

    op.drop_index(op.f("ix_idarr_runs_scope_token"), table_name="idarr_runs")
    op.drop_index(op.f("ix_idarr_runs_job_id"), table_name="idarr_runs")
    op.drop_index(op.f("ix_idarr_runs_id"), table_name="idarr_runs")
    op.drop_table("idarr_runs")

    op.drop_index(op.f("ix_schedules_job_type"), table_name="schedules")
    op.drop_index(op.f("ix_schedules_id"), table_name="schedules")
    op.drop_table("schedules")

    op.drop_index(op.f("ix_settings_key"), table_name="settings")
    op.drop_index(op.f("ix_settings_id"), table_name="settings")
    op.drop_table("settings")

    op.drop_index(op.f("ix_posters_drive_id"), table_name="posters")
    op.drop_index(op.f("ix_posters_id"), table_name="posters")
    op.drop_table("posters")

    op.drop_index(op.f("ix_jobs_id"), table_name="jobs")
    op.drop_table("jobs")

    op.drop_index(op.f("ix_drives_drive_id"), table_name="drives")
    op.drop_index(op.f("ix_drives_id"), table_name="drives")
    op.drop_table("drives")
