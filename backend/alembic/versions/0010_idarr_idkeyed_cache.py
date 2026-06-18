"""Re-key resolved idarr_asset_cache rows by identity (id-only), collapsing duplicates

Revision ID: 0010_idarr_idkeyed_cache
Revises: 0009_add_rating_keys_to_plex_upload_records
Create Date: 2026-06-18

Resolved rows were keyed by ``<type>::<title>::<year>::<id>::scope``, so an item forked
into duplicate rows when its filename changed. The builder now keys by id alone. Per
(scope, type, id) this keeps one survivor (canonical filename + freshest), merges
filenames, re-keys it to the id-only form, and deletes the rest. Unresolved rows stay
title/year keyed.
"""
from typing import Any, Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "0010_idarr_idkeyed_cache"
down_revision: Union[str, None] = "0009_add_rating_keys_to_plex_upload_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _scope_of(asset_key: str) -> str | None:
    marker = "::scope="
    idx = asset_key.find(marker)
    return asset_key[idx + len(marker):] if idx != -1 else None


def _id_only_key(asset_type: str, tmdb_id, tvdb_id, imdb_id, scope: str | None) -> str | None:
    """Mirror build_idarr_asset_key's resolved form: <type>::<idtype>=<id>[::scope=...]."""
    t = (asset_type or "").strip().lower()
    if tmdb_id is not None:
        core = f"{t}::tmdb={int(tmdb_id)}"
    elif tvdb_id is not None:
        core = f"{t}::tvdb={int(tvdb_id)}"
    elif imdb_id and str(imdb_id).strip():
        core = f"{t}::imdb={str(imdb_id).strip()}"
    else:
        return None  # unresolved — leave it title/year keyed
    return f"{core}::scope={scope}" if scope else core


def _parse_payload(raw) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _is_canonical_filename(payload: dict) -> bool:
    names = payload.get("current_filenames") or []
    return any(isinstance(n, str) and "{tmdb-" in n for n in names)


def _survivor_rank(row: dict) -> tuple:
    # Higher sorts first: canonical filename, has a timestamp, freshest, then highest id.
    payload = row["payload"]
    return (
        _is_canonical_filename(payload),
        row["last_checked_at"] is not None,
        row["last_checked_at"] or "",
        row["id"],
    )


def rekey_idarr_cache(conn) -> None:
    """Collapse + re-key resolved rows to the id-only form on ``conn`` (split out so it's
    unit-testable with a plain connection)."""
    cache = sa.table(
        "idarr_asset_cache",
        sa.column("id", sa.Integer),
        sa.column("asset_key", sa.String),
        sa.column("asset_type", sa.String),
        sa.column("tmdb_id", sa.Integer),
        sa.column("tvdb_id", sa.Integer),
        sa.column("imdb_id", sa.String),
        sa.column("last_checked_at", sa.String),
        sa.column("payload_json", sa.String),
    )
    pending = sa.table("idarr_pending_matches", sa.column("asset_key", sa.String))

    rows = conn.execute(
        sa.select(
            cache.c.id, cache.c.asset_key, cache.c.asset_type,
            cache.c.tmdb_id, cache.c.tvdb_id, cache.c.imdb_id,
            cache.c.last_checked_at, cache.c.payload_json,
        )
    ).fetchall()

    # Group resolved rows by their target id-only key.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        scope = _scope_of(r.asset_key)
        target = _id_only_key(r.asset_type, r.tmdb_id, r.tvdb_id, r.imdb_id, scope)
        if target is None:
            continue  # unresolved — leave untouched
        groups.setdefault(target, []).append({
            "id": r.id,
            "asset_key": r.asset_key,
            "last_checked_at": r.last_checked_at,
            "payload": _parse_payload(r.payload_json),
        })

    for target_key, grp in groups.items():
        survivor = max(grp, key=_survivor_rank)
        losers = [g for g in grp if g["id"] != survivor["id"]]

        # Merge filenames + freshest timestamp from the whole group into the survivor.
        payload = dict(survivor["payload"])
        current = set(payload.get("current_filenames") or [])
        original = set(payload.get("original_filenames") or [])
        for g in grp:
            current.update(g["payload"].get("current_filenames") or [])
            original.update(g["payload"].get("original_filenames") or [])
        if current:
            payload["current_filenames"] = sorted(current)
        if original:
            payload["original_filenames"] = sorted(original)
        newest = max((g["last_checked_at"] for g in grp if g["last_checked_at"]), default=None)

        # Delete losers before rewriting the survivor's key (asset_key UNIQUE constraint).
        for g in losers:
            conn.execute(pending.delete().where(pending.c.asset_key == g["asset_key"]))
            conn.execute(cache.delete().where(cache.c.id == g["id"]))

        if survivor["asset_key"] != target_key or losers or newest != survivor["last_checked_at"]:
            values: dict[str, Any] = {
                "asset_key": target_key,
                "payload_json": json.dumps(payload),
            }
            if newest is not None:
                values["last_checked_at"] = newest
            conn.execute(cache.update().where(cache.c.id == survivor["id"]).values(**values))


def upgrade() -> None:
    rekey_idarr_cache(op.get_bind())


def downgrade() -> None:
    # One-way: the old title isn't recoverable; a full idarr run rebuilds rows from disk.
    pass
