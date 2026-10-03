"""Historical migrations replayed through Alembic in isolated SQLite processes."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


BACKEND = Path(__file__).resolve().parents[1]
REVISIONS = ["0004_migrate_tmdb_api_key", "0005_migrate_monitor_tmdb_api_key"]
PRELUDE = """
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from database import engine

config = Config(str(Path.cwd() / "alembic.ini"))
scripts = ScriptDirectory.from_config(config)

def upgrade(revision="head"):
    command.upgrade(config, revision)

def table(name):
    return sa.Table(name, sa.MetaData(), autoload_with=engine)
"""


def _run_child(root, code):
    env = os.environ.copy()
    env.pop("POSTGRES_TEST_URL", None)
    env.update({
        "CONFIG_DIR": str(root / "config"),
        "GDRIVE_DIR": str(root / "posters"),
        "ARTWORK_GDRIVE_DIR": str(root / "artwork"),
        "LOGS_DIR": str(root / "logs"),
        "LOG_FILE": str(root / "logs" / "posterflow.log"),
        "DATABASE_URL": f"sqlite:///{root / 'config' / 'test.db'}",
        "POSTERFLOW_TESTING": "true",
        "TZ": "UTC",
    })
    result = subprocess.run(
        [sys.executable, "-c", PRELUDE + textwrap.dedent(code)],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"Child exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("revision", REVISIONS)
@pytest.mark.parametrize("case", [
    "absent", "null", "empty", "whitespace", "nonempty", "global-masked",
    "malformed", "list", "json-null", "string", "number", "boolean", "masked",
])
def test_key_promotion(tmp_path, revision, case):
    _run_child(tmp_path, f"""
        revision, case = {revision!r}, {case!r}
        # Monitor data must be seeded after 0004, which also strips its legacy key.
        upgrade(scripts.get_revision(revision).down_revision)
        settings = table("settings")
        config_keys = ["maker_tools_monitor_config"]
        if revision == "0004_migrate_tmdb_api_key":
            config_keys.insert(0, "maker_tools_idarr_config")
        invalid = {{
            "malformed": '{{"tmdb_api_key":',
            "list": '["tmdb_api_key", {{"tmdb_api_key": "legacy-key"}}]',
            "json-null": "null", "string": '"tmdb_api_key"',
            "number": "42", "boolean": "true",
        }}
        keep = {{"keep": [1, None, {{"nested": True}}], "enabled": False}}
        raw = invalid.get(case, json.dumps({{
            **keep, "tmdb_api_key": "***masked***" if case == "masked" else "  legacy-key  ",
        }}))
        config_values = dict.fromkeys(config_keys, raw)
        if revision == "0004_migrate_tmdb_api_key" and case not in invalid and case != "masked":
            config_values["maker_tools_monitor_config"] = json.dumps({{
                **keep, "tmdb_api_key": "monitor-third",
            }})
        global_values = {{
            "null": None, "empty": "", "whitespace": " \\t ",
            "nonempty": "  global-key  ", "global-masked": "***masked***",
        }}
        original_id = None
        with engine.begin() as conn:
            conn.execute(settings.insert(), [
                {{"key": key, "value": value}} for key, value in config_values.items()
            ])
            if revision == "0004_migrate_tmdb_api_key" and case == "masked":
                conn.execute(settings.insert().values(
                    key="unmatched_tmdb_api_key", value="***masked***"
                ))
            if case in global_values:
                conn.execute(settings.insert().values(
                    key="tmdb_api_key", value=global_values[case], description="retain metadata"
                ))
                original_id = conn.execute(sa.select(settings.c.id).where(
                    settings.c.key == "tmdb_api_key"
                )).scalar_one()

        def check():
            with engine.connect() as conn:
                values = dict(conn.execute(sa.select(settings.c.key, settings.c.value)).all())
                if original_id is not None:
                    row = conn.execute(sa.select(settings).where(
                        settings.c.key == "tmdb_api_key"
                    )).mappings().one()
                    assert row["id"] == original_id
                    assert row["description"] == "retain metadata"
            if case in invalid or case == "masked":
                assert "tmdb_api_key" not in values, values
            else:
                expected = global_values[case] if case in ("nonempty", "global-masked") else "legacy-key"
                assert values["tmdb_api_key"] == expected, values
            assert "unmatched_tmdb_api_key" not in values
            for key in config_keys:
                if case in invalid:
                    assert values[key] == raw
                else:
                    assert json.loads(values[key]) == keep

        upgrade(revision)
        check()
        upgrade()
        check()
        with engine.connect() as conn:
            assert conn.execute(sa.select(table("alembic_version").c.version_num)).scalar_one() == scripts.get_current_head()
    """)


@pytest.mark.parametrize("unmatched,idarr,expected", [
    ("  unmatched-first  ", "idarr-second", "unmatched-first"),
    (None, "idarr-second", "idarr-second"),
    (" ", "idarr-second", "idarr-second"),
    ("***masked***", "idarr-second", "idarr-second"),
    (None, "***masked***", "monitor-third"),
    (None, " ", "monitor-third"),
])
def test_0004_key_precedence(tmp_path, unmatched, idarr, expected):
    _run_child(tmp_path, f"""
        upgrade("0003_add_display_name")
        settings = table("settings")
        with engine.begin() as conn:
            conn.execute(settings.insert(), [
                {{"key": "unmatched_tmdb_api_key", "value": {unmatched!r}}},
                {{"key": "maker_tools_idarr_config", "value": json.dumps({{"tmdb_api_key": {idarr!r}, "keep": 1}})}},
                {{"key": "maker_tools_monitor_config", "value": json.dumps({{"tmdb_api_key": "monitor-third", "keep": 2}})}},
            ])
        upgrade("0004_migrate_tmdb_api_key")
        with engine.connect() as conn:
            values = dict(conn.execute(sa.select(settings.c.key, settings.c.value)).all())
        assert values["tmdb_api_key"] == {expected!r}
        assert "unmatched_tmdb_api_key" not in values
        assert json.loads(values["maker_tools_idarr_config"]) == {{"keep": 1}}
        assert json.loads(values["maker_tools_monitor_config"]) == {{"keep": 2}}
    """)


@pytest.mark.parametrize("revision", REVISIONS)
@pytest.mark.parametrize("write", ["promotion", "cleanup"])
def test_sql_write_failure_aborts_migration(tmp_path, revision, write):
    _run_child(tmp_path, f"""
        revision, write = {revision!r}, {write!r}
        predecessor = scripts.get_revision(revision).down_revision
        upgrade(predecessor)
        settings = table("settings")
        config_key = "maker_tools_idarr_config" if revision == "0004_migrate_tmdb_api_key" else "maker_tools_monitor_config"
        raw = json.dumps({{"tmdb_api_key": "legacy-key", "keep": True}})
        blocked_key = "tmdb_api_key" if write == "promotion" else config_key
        with engine.begin() as conn:
            conn.execute(settings.insert(), [
                {{"key": config_key, "value": raw}},
                {{"key": "tmdb_api_key", "value": None if write == "promotion" else "global-key"}},
            ])
            conn.exec_driver_sql(
                "CREATE TRIGGER reject_migration_write BEFORE UPDATE ON settings "
                "WHEN OLD.key = '" + blocked_key + "' "
                "BEGIN SELECT RAISE(ABORT, 'forced migration write failure'); END"
            )
        try:
            upgrade(revision)
        except sa.exc.IntegrityError as error:
            assert "forced migration write failure" in str(error), str(error)
        else:
            raise AssertionError("SQL failure was swallowed and migration advanced")
        with engine.connect() as conn:
            assert conn.execute(sa.select(table("alembic_version").c.version_num)).scalar_one() == predecessor
            assert conn.execute(sa.select(settings.c.value).where(settings.c.key == config_key)).scalar_one() == raw
            assert conn.execute(sa.select(settings.c.value).where(settings.c.key == "tmdb_api_key")).scalar_one() == (None if write == "promotion" else "global-key")
    """)


def test_fresh_head_and_repeat_preserve_marker_and_workflow(tmp_path):
    _run_child(tmp_path, """
        upgrade()
        with engine.begin() as conn:
            workflow = conn.execute(sa.select(table("workflows"))).mappings().one()
            assert workflow["name"] == "Default" and workflow["is_default"] is True
            flow = json.loads(workflow["config"])
            assert flow["sync_drives"]["enabled"] is True
            assert flow["rename_posters"]["enabled"] is True
            conn.execute(table("settings").insert(), [
                {"key": "repeat_marker", "value": "preserved"},
                {"key": "workflow_snapshot", "value": json.dumps(dict(workflow), default=str)},
                {"key": "tmdb_api_key", "value": ""},
                {"key": "unmatched_tmdb_api_key", "value": "must-not-replay"},
                {"key": "maker_tools_monitor_config", "value": '{"tmdb_api_key": "must-not-strip"}'},
            ])
    """)
    _run_child(tmp_path, """
        upgrade()
        settings = table("settings")
        with engine.connect() as conn:
            assert conn.execute(sa.select(table("alembic_version").c.version_num)).scalar_one() == scripts.get_current_head()
            values = dict(conn.execute(sa.select(settings.c.key, settings.c.value)).all())
            assert values["repeat_marker"] == "preserved"
            workflow = conn.execute(sa.select(table("workflows"))).mappings().one()
            assert json.dumps(dict(workflow), default=str) == values["workflow_snapshot"]
            # Historical data migrations must not replay on an already-at-head database.
            assert values["tmdb_api_key"] == ""
            assert values["unmatched_tmdb_api_key"] == "must-not-replay"
            assert json.loads(values["maker_tools_monitor_config"]) == {"tmdb_api_key": "must-not-strip"}
    """)


def test_full_legacy_replay_and_timestamp_contract(tmp_path):
    _run_child(tmp_path, """
        upgrade("0003_add_display_name")
        settings = table("settings")
        flow = {"rename_posters": {"enabled": False, "stop_on_error": False}}
        with engine.begin() as conn:
            conn.execute(settings.insert(), [
                {"key": "unmatched_tmdb_api_key", "value": "  unmatched-first  "},
                {"key": "maker_tools_idarr_config", "value": '{"tmdb_api_key": "idarr-second", "keep": 1}'},
                {"key": "maker_tools_monitor_config", "value": '{"tmdb_api_key": "monitor-third", "keep": 2}'},
                {"key": "poster_flow_config", "value": json.dumps(flow)},
            ])
        upgrade("0009_add_rating_keys_to_plex_upload_records")
        cache, pending = table("idarr_asset_cache"), table("idarr_pending_matches")
        older = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        middle = datetime(2026, 6, 2, 12, tzinfo=timezone.utc)
        newer = datetime(2026, 6, 3, 12, tzinfo=timezone.utc)
        canonical = "movie::canonical::2020::tmdb=42::scope=legacy"
        target = "movie::tmdb=42::scope=legacy"
        unresolved = "movie::unknown::2020::scope=legacy"
        seeds = [
            ("movie::old::2020::tmdb=42::scope=legacy", "Old", "Old {tmdb-42}.jpg", older, 42),
            (canonical, "Canonical", "Canonical {tmdb-42}.jpg", middle, 42),
            (target, "Dirty", "Dirty.jpg", newer, 42),
            ("movie::unstamped::2020::tmdb=42::scope=legacy", "Unstamped", "Unstamped {tmdb-42}.jpg", None, 42),
            (unresolved, "Unknown", "Unknown.jpg", older, None),
            ("movie::elsewhere::2020::tmdb=42::scope=other", "Elsewhere", "Elsewhere.jpg", older, 42),
        ]
        with engine.begin() as conn:
            conn.execute(cache.insert(), [
                {"asset_key": key, "title": title, "year": 2020, "asset_type": "movie",
                 "tmdb_id": tmdb, "matched": tmdb is not None, "last_checked_at": stamp,
                 "payload_json": json.dumps({"canonical_title": title,
                    "current_filenames": [name], "original_filenames": [title + " original.jpg"]})}
                for key, title, name, stamp, tmdb in seeds
            ])
            canonical_id = conn.execute(sa.select(cache.c.id).where(cache.c.asset_key == canonical)).scalar_one()
            unknown_before = dict(conn.execute(sa.select(cache).where(cache.c.asset_key == unresolved)).mappings().one())
            conn.execute(pending.insert(), [
                {"asset_key": key, "title": "Pending", "asset_type": "movie"}
                for key in (target, unresolved)
            ])

        selected_types, updated_binds = [], []
        def capture(conn, clause, multiparams, params, execution_options):
            if isinstance(clause, sa.sql.Select):
                for column in clause.selected_columns:
                    if column.name == "last_checked_at" and column.table.name == "idarr_asset_cache":
                        selected_types.append(column.type)
            elif isinstance(clause, sa.sql.Update) and clause.table.name == "idarr_asset_cache":
                compiled = clause.compile()
                if "last_checked_at" in compiled.binds:
                    bind = compiled.binds["last_checked_at"]
                    updated_binds.append((clause.table.c.last_checked_at.type, bind.type, bind.value))

        # Alembic creates its own engine; observe real statements before DBAPI conversion.
        sa.event.listen(sa.engine.Engine, "before_execute", capture)
        try:
            upgrade()
        finally:
            sa.event.remove(sa.engine.Engine, "before_execute", capture)

        with engine.connect() as conn:
            rows = {row["asset_key"]: row for row in conn.execute(sa.select(cache)).mappings()}
            assert set(rows) == {target, unresolved, "movie::tmdb=42::scope=other"}
            survivor = rows[target]
            assert survivor["id"] == canonical_id
            assert survivor["title"] == "Canonical" and survivor["tmdb_id"] == 42
            payload = json.loads(survivor["payload_json"])
            assert payload["canonical_title"] == "Canonical"
            assert payload["current_filenames"] == sorted(seed[2] for seed in seeds[:4])
            assert payload["original_filenames"] == sorted(seed[1] + " original.jpg" for seed in seeds[:4])
            assert isinstance(survivor["last_checked_at"], datetime)
            assert survivor["last_checked_at"] == newer.replace(tzinfo=None)
            assert dict(rows[unresolved]) == unknown_before
            assert conn.execute(sa.select(pending.c.asset_key)).scalars().all() == [unresolved]
            values = dict(conn.execute(sa.select(settings.c.key, settings.c.value)).all())
            assert values["tmdb_api_key"] == "unmatched-first"
            assert "unmatched_tmdb_api_key" not in values
            assert json.loads(values["maker_tools_idarr_config"]) == {"keep": 1}
            assert json.loads(values["maker_tools_monitor_config"]) == {"keep": 2}
            workflow = conn.execute(sa.select(table("workflows"))).mappings().one()
            assert workflow["name"] == "Default" and workflow["is_default"] is True
            assert json.loads(workflow["config"]) == flow
            assert json.loads(values["poster_flow_config"]) == flow
            assert conn.execute(sa.select(table("alembic_version").c.version_num)).scalar_one() == scripts.get_current_head()
        assert selected_types and updated_binds
        assert all(isinstance(kind, sa.DateTime) and kind.timezone for kind in selected_types), selected_types
        for column_type, bind_type, value in updated_binds:
            assert isinstance(column_type, sa.DateTime) and column_type.timezone, column_type
            assert isinstance(bind_type, sa.DateTime) and bind_type.timezone, bind_type
            assert isinstance(value, datetime), value
        assert updated_binds[0][2] == newer.replace(tzinfo=None)
    """)
