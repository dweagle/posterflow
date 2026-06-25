"""Headless reconciliation of this instance's own community list items.

When a poster is produced outside the request/list path (made by hand, dropped in
a drive, picked up by the renamer), the item leaves the local unmatched / style-
fallback sets. This removes the now-resolved items from the user's own published
community list so the list and the local state stay in sync.

Runs from scan jobs (so it works headless, with no browser connected) and logs a
summary via log_user_action so it's visible on the Logs page. Best-effort: any
failure is logged and swallowed — it never breaks the job that triggered it.

Authorization model: the matching is done here (we own the unmatched/fallback
data); removal goes through the reconcile-list-items edge function, authorized by
RECONCILE_SECRET, scoped to this instance's stored Discord identity. Only the
user's own list items are ever touched.
"""
import base64
import json
import time
from typing import Any, Iterable, Optional

import httpx
from sqlalchemy.orm import Session

from models.setting import get_setting_value
from core.logging import LogTags, log_info, log_user_action, log_error

# Same public Supabase project as api/community.py (read-only publishable key).
SUPABASE_URL = "https://qwudwkxfqowjtisdlplv.supabase.co"
SUPABASE_KEY = "sb_publishable_N83-fB74swOKM5XGbMhO7A_qk7LXgel"
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

SETTING_DISCORD_IDENTITY = "community_discord_identity"
SETTING_UNMATCHED_STATS = "poster_unmatched_stats"
SETTING_RENAMER_STATS = "poster_renamer_stats"
SETTING_DRIVE_PRIORITY = "poster_drive_priority"

VALID_SOURCES = ("unmatched", "style_fallback")

# Community poster styles a style_fallback item can ask for (matches the Priority
# tab's COMMUNITY_STYLES). Used to pick the instance's preferred style.
COMMUNITY_STYLES = {"MM2K", "CL2K"}

# Only these media types are reconciled. Season items use a partial
# "Seasons: 1,2,3" encoding that absence-matching can't resolve safely, so we
# leave them alone for now.
_RECONCILABLE_TYPES = {"movie", "show", "collection"}

# Edge function caps ids per call; chunk to match.
_CHUNK = 500


def _norm_group(media_type: str) -> str:
    """Collapse the various type spellings to one bucket so list items and the
    unmatched/fallback sets compare on equal footing."""
    t = (media_type or "").strip().lower()
    if t in ("movie", "movies"):
        return "movie"
    if t in ("show", "series", "tv"):
        return "series"
    if t in ("collection", "collections"):
        return "collection"
    return t


def _norm_style(style: Optional[str]) -> str:
    """Normalize a style name so drive style_types, the renamer's style_fallbacks
    bucket keys, and style_counts all compare on equal footing (e.g. 'CL2K')."""
    return (style or "").strip().upper()


def _load_json_setting(db: Session, key: str) -> Optional[Any]:
    raw = get_setting_value(db, key)
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None


def _token_exp(token: str) -> Optional[int]:
    """Read the `exp` from the signed Discord token without verifying it (the
    edge function verifies; we only need to know if it's worth trying). The token
    is `base64(JSON).hexsig`, signed via btoa() so the payload is standard base64."""
    try:
        part = token.rsplit(".", 1)[0]
        payload = json.loads(base64.b64decode(part + "=" * (-len(part) % 4)).decode("utf-8"))
        exp = payload.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def _unmatched_keys(db: Session) -> Optional[tuple[set, set]]:
    """((group, tmdb_id) for items still missing a poster, {groups actually
    scanned}). None if the stats are absent/unreadable. The scanned set is the
    partial-scan guard: a media type whose summary total is 0 wasn't scanned, so
    its absence from the unmatched list must NOT be read as 'resolved'."""
    data = _load_json_setting(db, SETTING_UNMATCHED_STATS)
    if not isinstance(data, dict):
        return None
    unmatched = data.get("unmatched")
    if not isinstance(unmatched, dict):
        return None
    keys: set = set()
    for bucket in ("movies", "series", "collections"):
        for it in unmatched.get(bucket) or []:
            tid = it.get("tmdb_id")
            if tid:
                keys.add((_norm_group(bucket), int(tid)))
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    scanned: set = set()
    for bucket in ("movies", "series", "collections"):
        b = summary.get(bucket) if isinstance(summary.get(bucket), dict) else {}
        if int(b.get("total") or 0) > 0:
            scanned.add(_norm_group(bucket))
    return keys, scanned


def _preferred_style(db: Session, style_counts: dict) -> Optional[str]:
    """The instance's preferred style (normalized) — the style its style_fallback
    items were published asking for. Mirrors the Priority tab: the first style in
    the drive-priority order that was used this run (a community style wins), else
    the first prioritized style used, else the most-used style. None if unknown."""
    counts = {_norm_style(k): int(v or 0) for k, v in (style_counts or {}).items() if int(v or 0) > 0}
    if not counts:
        return None
    priority_styles: list[str] = []
    raw = _load_json_setting(db, SETTING_DRIVE_PRIORITY)
    drive_ids = raw.get("drive_ids") if isinstance(raw, dict) else None
    if isinstance(drive_ids, list) and drive_ids:
        from models.drive import Drive
        by_id = {d.id: d for d in db.query(Drive).filter(Drive.id.in_(drive_ids)).all()}
        for did in drive_ids:
            drive = by_id.get(did)
            style = _norm_style(getattr(drive, "style_type", None)) if drive is not None else ""
            if style and style not in priority_styles:
                priority_styles.append(style)
    for style in priority_styles:
        if style in COMMUNITY_STYLES and style in counts:
            return style
    for style in priority_styles:
        if style in counts:
            return style
    return max(counts, key=counts.get)


def _fallback_present_by_style(db: Session) -> Optional[tuple[dict[str, set], Optional[str]]]:
    """(({normalized style -> {(group, tmdb_id) matched under it}}), preferred style).
    None if the stats are absent/unreadable.

    A style_fallback list item asks for the instance's preferred style; it's resolved
    once the item wins under that style locally. We key the matched set by style
    (instead of flattening every style together — flattening never resolved an
    upgrade because the item is always matched under *some* style) and compare against
    the instance's own preferred style rather than the shared row's style_tag, which
    is set by whoever first created the row and may differ or be absent."""
    data = _load_json_setting(db, SETTING_RENAMER_STATS)
    if not isinstance(data, dict):
        return None
    fallbacks = data.get("style_fallbacks")
    if not isinstance(fallbacks, dict):
        return None
    present_by_style: dict[str, set] = {}
    for style, items in fallbacks.items():
        bucket = present_by_style.setdefault(_norm_style(style), set())
        for it in items or []:
            tid = it.get("tmdb_id")
            if tid:
                bucket.add((_norm_group(it.get("type", "")), int(tid)))
    style_counts = data.get("style_counts")
    preferred = _preferred_style(db, style_counts if isinstance(style_counts, dict) else {})
    return present_by_style, preferred


def _fetch_wanted_items(discord_id: str, sources: set) -> list:
    """Active posters this instance wants where THIS user added them from one of
    `sources`. Each returned row carries the user's own 'wanter_source' (not the
    row's first-creator source), so reconciliation uses the right context."""
    with httpx.Client(timeout=15.0) as client:
        wresp = client.get(
            f"{SUPABASE_URL}/rest/v1/poster_list_wanters",
            headers=SUPABASE_HEADERS,
            params={
                "select": "item_id,source",
                "discord_id": f"eq.{discord_id}",
                "source": f"in.({','.join(sorted(sources))})",
                "limit": 2000,
            },
        )
        wresp.raise_for_status()
        source_by_item = {r["item_id"]: r.get("source") for r in wresp.json()}
        item_ids = list(source_by_item.keys())
        if not item_ids:
            return []
        resp = client.get(
            f"{SUPABASE_URL}/rest/v1/poster_list_items",
            headers=SUPABASE_HEADERS,
            params={
                "select": "id,tmdb_id,media_type,status",
                "id": f"in.({','.join(item_ids[:1000])})",
                "status": "in.(open,in_progress)",
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        items = resp.json()
        for it in items:
            it["wanter_source"] = source_by_item.get(it["id"])
        return items


def _resolve_items(token: str, item_ids: list) -> int:
    """Detach this user from the resolved posters AND flag the rows others still
    want as "available in a drive", via the update-list-item edge function. The
    owner is derived from the verified token, so only the caller's own want is
    ever dropped — same auth path as a manual remove."""
    removed = 0
    with httpx.Client(timeout=20.0) as client:
        for i in range(0, len(item_ids), _CHUNK):
            chunk = item_ids[i:i + _CHUNK]
            resp = client.post(
                f"{SUPABASE_URL}/functions/v1/update-list-item",
                headers=SUPABASE_HEADERS,
                json={"token": token, "action": "available_ids", "item_ids": chunk},
            )
            resp.raise_for_status()
            removed += int(resp.json().get("removed", 0))
    return removed


def reconcile_community_lists(db: Session, sources: Iterable[str]) -> int:
    """Remove the user's own list items resolved locally for the given sources.

    sources: which list sources have fresh data this run ('unmatched' and/or
    'style_fallback'). Pass only the sources whose snapshot the triggering job
    just refreshed. Returns the number removed. Never raises.
    """
    wanted = {s for s in sources if s in VALID_SOURCES}
    if not wanted:
        return 0
    try:
        identity = _load_json_setting(db, SETTING_DISCORD_IDENTITY) or {}
        if not isinstance(identity, dict):
            return 0
        discord_id = identity.get("discord_user_id")
        token = identity.get("discord_token")
        if not discord_id or not token:
            return 0  # Discord not connected (or no stored token) — nothing to do

        # The stored token is what authorizes removal. Discord tokens last 30 days
        # and refresh on every reconnect; if it has lapsed, skip and tell the user.
        exp = _token_exp(token)
        if exp is not None and exp <= int(time.time()):
            log_info(
                LogTags.API,
                "Skipped community list sync — Discord token expired; reconnect Discord to resume",
            )
            return 0

        # Fresh per-source snapshots. A None result means no usable data — drop
        # that source so we can never wrongly detach a wanter.
        unmatched_data: Optional[tuple[set, set]] = None
        if "unmatched" in wanted:
            unmatched_data = _unmatched_keys(db)
            if unmatched_data is None:
                wanted.discard("unmatched")
        fallback_data: Optional[tuple[dict[str, set], Optional[str]]] = None
        if "style_fallback" in wanted:
            fallback_data = _fallback_present_by_style(db)
            if fallback_data is None:
                wanted.discard("style_fallback")
        if not wanted:
            return 0

        resolved_ids: list = []
        for it in _fetch_wanted_items(discord_id, wanted):
            src = it.get("wanter_source")
            if src not in wanted:
                continue
            media_type = (it.get("media_type") or "").lower()
            if media_type not in _RECONCILABLE_TYPES:
                continue  # season items skipped in v1
            tid = it.get("tmdb_id")
            if not tid:
                continue  # custom items can't be matched by id
            group = _norm_group(media_type)
            key = (group, int(tid))
            if src == "unmatched" and unmatched_data is not None:
                keys, scanned = unmatched_data
                # Resolved only if this type was actually scanned (partial-scan
                # guard) AND the poster is no longer in the outstanding set.
                if group in scanned and key not in keys:
                    resolved_ids.append(it["id"])
            elif src == "style_fallback" and fallback_data is not None:
                # Resolved once the item wins under the instance's preferred style
                # locally — i.e. the user now actually has the style they asked for.
                # Presence is self-validating, so no partial-scan guard is needed.
                present_by_style, preferred = fallback_data
                if preferred and key in present_by_style.get(preferred, set()):
                    resolved_ids.append(it["id"])

        if not resolved_ids:
            return 0

        removed = _resolve_items(token, resolved_ids)
        if removed:
            log_user_action(
                f"Reconciled community list — cleared {removed} item(s) you now have; flagged them available for others",
                sources=",".join(sorted(wanted)),
            )
        return removed
    except Exception as e:
        log_error(LogTags.API, f"Community list reconcile failed: {e}")
        return 0
