import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_error, log_warning
from models.setting import get_setting


DISCORD_NOTIFICATION_FEATURES = [
    "workflow",
    "sync",
    "poster_renamer",
    "unmatched_assets",
    "plex_upload",
    "idarr",
    "maker_monitor",
    "system_errors",
]

SPACER_IMAGE_URL = "https://cdn.jsdelivr.net/gh/dweagle/extras@main/spacer.png"

EVENT_TITLE_PREFIX = {
    "success": "✅",
    "error": "❌",
    "start": "🚀",
    "end": "🏁",
    "info": "ℹ️",
}


def _default_feature_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "on_success": True,
        "on_error": True,
        "include_summary": True,
        "include_details": True,
        "webhook_url": "",
        "mention": "",
        "mention_on_error": True,
        "mention_on_success": False,
        "mention_on_info": False,
    }


def _is_valid_discord_webhook(webhook_url: str) -> bool:
    trimmed = str(webhook_url or "").strip()
    return trimmed.startswith("https://discord.com/api/webhooks/") or trimmed.startswith(
        "https://discordapp.com/api/webhooks/"
    )


def _normalize_features(features: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    normalized: Dict[str, Dict[str, Any]] = {
        key: dict(_default_feature_config()) for key in DISCORD_NOTIFICATION_FEATURES
    }

    if not isinstance(features, dict):
        return normalized

    for key in DISCORD_NOTIFICATION_FEATURES:
        value = features.get(key)
        if not isinstance(value, dict):
            continue
        merged = dict(_default_feature_config())
        merged["enabled"] = bool(value.get("enabled", merged["enabled"]))
        merged["on_success"] = bool(value.get("on_success", merged["on_success"]))
        merged["on_error"] = bool(value.get("on_error", merged["on_error"]))
        merged["include_summary"] = bool(value.get("include_summary", merged["include_summary"]))
        merged["include_details"] = bool(value.get("include_details", merged["include_details"]))
        merged["webhook_url"] = str(value.get("webhook_url", "")).strip()
        merged["mention"] = str(value.get("mention", "")).strip()
        merged["mention_on_error"] = bool(value.get("mention_on_error", True))
        merged["mention_on_success"] = bool(value.get("mention_on_success", False))
        normalized[key] = merged

    return normalized


def _build_mention_payload(mention: str) -> Tuple[str, Dict[str, Any]]:
    """Parse a mention string and return (content, allowed_mentions) for a Discord payload."""
    m = mention.strip()
    if not m:
        return "", {}
    if m == "@here":
        return "@here", {"parse": ["here"]}
    if m == "@everyone":
        return "@everyone", {"parse": ["everyone"]}
    user_match = re.match(r"^<@(\d+)>$", m)
    if user_match:
        return m, {"users": [user_match.group(1)]}
    role_match = re.match(r"^<@&(\d+)>$", m)
    if role_match:
        return m, {"roles": [role_match.group(1)]}
    # Bare snowflake — treat as user ID
    if re.match(r"^\d+$", m):
        return f"<@{m}>", {"users": [m]}
    return m, {}


def _read_discord_config(db: Session) -> Dict[str, Any]:
    enabled_raw = get_setting(db, "discord_notifications_enabled")
    webhook_raw = get_setting(db, "discord_notifications_webhook_url")
    features_raw = get_setting(db, "discord_notifications_features")
    mention_raw = get_setting(db, "discord_notifications_mention")
    mention_on_error_raw = get_setting(db, "discord_notifications_mention_on_error")
    mention_on_success_raw = get_setting(db, "discord_notifications_mention_on_success")

    enabled = (
        enabled_raw.value.strip().lower() == "true"
        if enabled_raw and enabled_raw.value is not None
        else False
    )
    webhook_url = webhook_raw.value.strip() if webhook_raw and webhook_raw.value else ""
    mention = mention_raw.value.strip() if mention_raw and mention_raw.value else ""
    mention_on_error = (
        mention_on_error_raw.value.strip().lower() != "false"
        if mention_on_error_raw and mention_on_error_raw.value
        else True
    )
    mention_on_success = (
        mention_on_success_raw.value.strip().lower() == "true"
        if mention_on_success_raw and mention_on_success_raw.value
        else False
    )

    parsed_features: Dict[str, Any] | None = None
    if features_raw and features_raw.value:
        try:
            loaded = json.loads(features_raw.value)
            if isinstance(loaded, dict):
                parsed_features = loaded
        except json.JSONDecodeError:
            log_warning(LogTags.API, "Invalid discord_notifications_features JSON while sending notification")

    return {
        "enabled": enabled,
        "webhook_url": webhook_url,
        "mention": mention,
        "mention_on_error": mention_on_error,
        "mention_on_success": mention_on_success,
        "features": _normalize_features(parsed_features),
    }


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def _format_title(event_type: str, title: str) -> str:
    normalized = str(title or "").strip()
    lowered_title = normalized.lower()

    if "start" in lowered_title:
        prefix = EVENT_TITLE_PREFIX.get("start")
    elif any(token in lowered_title for token in ("finish", "completed", "complete", "done", "end")):
        prefix = EVENT_TITLE_PREFIX.get("end")
    else:
        prefix = EVENT_TITLE_PREFIX.get(str(event_type or "").strip().lower())

    if not normalized:
        normalized = "PosterFlow Notification"
    if prefix and not normalized.startswith(prefix):
        return f"{prefix} {normalized}"
    return normalized


def send_discord_notification(
    db: Session,
    *,
    feature_key: str,
    event_type: str,
    title: str,
    description: str = "",
    fields: Optional[List[Dict[str, Any]]] = None,
    color: int = 0x64B5F6,
    footer_text: str = "PosterFlow Notification",
    username: str = "PosterFlow",
    include_spacer_image: bool = True,
) -> bool:
    """Send a Discord notification if global and feature toggles allow it."""
    try:
        config = _read_discord_config(db)
        if not config.get("enabled"):
            return False

        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url or not _is_valid_discord_webhook(webhook_url):
            return False

        features = config.get("features", {})
        feature_config = features.get(feature_key)
        if not isinstance(feature_config, dict) or not feature_config.get("enabled"):
            return False

        # Use feature-specific webhook if set, fall back to global
        feature_webhook = str(feature_config.get("webhook_url") or "").strip()
        if feature_webhook and _is_valid_discord_webhook(feature_webhook):
            webhook_url = feature_webhook

        if event_type == "success" and not feature_config.get("on_success", True):
            return False
        if event_type == "error" and not feature_config.get("on_error", True):
            return False

        include_summary = bool(feature_config.get("include_summary", True))
        include_details = bool(feature_config.get("include_details", True))

        embed_fields: List[Dict[str, Any]] = []
        if include_details and fields:
            for field in fields[:10]:
                if not isinstance(field, dict):
                    continue
                name = _truncate(str(field.get("name", "Details")), 256)
                value = _truncate(str(field.get("value", "-")) or "-", 1024)
                inline = bool(field.get("inline", False))
                embed_fields.append({"name": name, "value": value, "inline": inline})

        embed: Dict[str, Any] = {
            "title": _truncate(_format_title(event_type, str(title)), 256),
            "color": int(color),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": _truncate(str(footer_text or "PosterFlow Notification"), 2048)},
        }
        if include_spacer_image:
            embed["image"] = {"url": SPACER_IMAGE_URL}

        if include_summary and description:
            embed["description"] = _truncate(str(description), 3500)
        if embed_fields:
            embed["fields"] = embed_fields

        payload = {
            "username": _truncate(str(username or "PosterFlow"), 80),
            "embeds": [embed],
        }

        mention_str = str(feature_config.get("mention") or "").strip()
        mention_on_err = bool(feature_config.get("mention_on_error", True))
        mention_on_ok = bool(feature_config.get("mention_on_success", False))
        mention_on_info = bool(feature_config.get("mention_on_info", False))
        # Fall back to global mention when the feature has no override
        if not mention_str:
            mention_str = str(config.get("mention") or "").strip()
            mention_on_err = bool(config.get("mention_on_error", True))
            mention_on_ok = bool(config.get("mention_on_success", False))
            mention_on_info = bool(config.get("mention_on_info", False))
        if mention_str:
            should_mention = (
                (event_type == "error" and mention_on_err)
                or (event_type == "success" and mention_on_ok)
                or (event_type == "info" and mention_on_info)
            )
            if should_mention:
                content, allowed_mentions = _build_mention_payload(mention_str)
                if content:
                    payload["content"] = content
                    if allowed_mentions:
                        payload["allowed_mentions"] = allowed_mentions

        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code not in (200, 204):
            log_warning(
                LogTags.API,
                "Discord notification rejected",
                feature=feature_key,
                status_code=response.status_code,
            )
            return False

        log_debug(LogTags.API, f"Discord notification sent ({feature_key}:{event_type})")
        return True
    except Exception as exc:
        log_error(LogTags.API, f"Failed to send Discord notification: {exc}")
        return False


def send_discord_workflow_summary(
    db: Session,
    *,
    embeds: List[Dict[str, Any]],
    username: str = "PosterFlow",
    footer_text: str = "PosterFlow Notification",
) -> bool:
    """Send multiple embeds in a single Discord POST, gated on the workflow feature toggle.

    Each item in ``embeds`` should be a dict with keys:
        event_type, title, description, fields (list), color
    """
    try:
        config = _read_discord_config(db)
        if not config.get("enabled"):
            return False

        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url or not _is_valid_discord_webhook(webhook_url):
            return False

        feature_config = config.get("features", {}).get("workflow")
        if not isinstance(feature_config, dict) or not feature_config.get("enabled"):
            return False

        # Use workflow feature-specific webhook if set, fall back to global
        feature_webhook = str(feature_config.get("webhook_url") or "").strip()
        if feature_webhook and _is_valid_discord_webhook(feature_webhook):
            webhook_url = feature_webhook

        include_summary = bool(feature_config.get("include_summary", True))
        include_details = bool(feature_config.get("include_details", True))

        built_embeds: List[Dict[str, Any]] = []
        for spec in embeds[:10]:
            event_type = str(spec.get("event_type") or "info")
            if event_type == "success" and not feature_config.get("on_success", True):
                continue
            if event_type == "error" and not feature_config.get("on_error", True):
                continue

            embed_fields: List[Dict[str, Any]] = []
            if include_details and spec.get("fields"):
                for field in spec["fields"][:25]:
                    if not isinstance(field, dict):
                        continue
                    name = _truncate(str(field.get("name", "Details")), 256)
                    value = _truncate(str(field.get("value") or "-"), 1024)
                    inline = bool(field.get("inline", False))
                    embed_fields.append({"name": name, "value": value, "inline": inline})

            embed: Dict[str, Any] = {
                "title": _truncate(_format_title(event_type, str(spec.get("title") or "PosterFlow")), 256),
                "color": int(spec.get("color") or 0x64B5F6),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": _truncate(str(footer_text), 2048)},
                "image": {"url": SPACER_IMAGE_URL},
            }
            desc = str(spec.get("description") or "")
            if include_summary and desc:
                embed["description"] = _truncate(desc, 3500)
            if embed_fields:
                embed["fields"] = embed_fields

            built_embeds.append(embed)

        if not built_embeds:
            return False

        payload = {
            "username": _truncate(str(username or "PosterFlow"), 80),
            "embeds": built_embeds,
        }

        mention_str = str(feature_config.get("mention") or "").strip()
        mention_on_err = bool(feature_config.get("mention_on_error", True))
        mention_on_ok = bool(feature_config.get("mention_on_success", False))
        mention_on_info = bool(feature_config.get("mention_on_info", False))
        # Fall back to global mention when the workflow feature has no override
        if not mention_str:
            mention_str = str(config.get("mention") or "").strip()
            mention_on_err = bool(config.get("mention_on_error", True))
            mention_on_ok = bool(config.get("mention_on_success", False))
            mention_on_info = bool(config.get("mention_on_info", False))
        if mention_str:
            should_mention = any(
                (str(spec.get("event_type") or "") == "error" and mention_on_err)
                or (str(spec.get("event_type") or "") == "success" and mention_on_ok)
                or (str(spec.get("event_type") or "") == "info" and mention_on_info)
                for spec in embeds
            )
            if should_mention:
                content, allowed_mentions = _build_mention_payload(mention_str)
                if content:
                    payload["content"] = content
                    if allowed_mentions:
                        payload["allowed_mentions"] = allowed_mentions

        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code not in (200, 204):
            log_warning(
                LogTags.API,
                "Discord workflow summary notification rejected",
                status_code=response.status_code,
            )
            return False

        log_debug(LogTags.API, f"Discord workflow summary sent ({len(built_embeds)} embeds)")
        return True
    except Exception as exc:
        log_error(LogTags.API, f"Failed to send Discord workflow summary: {exc}")
        return False


def send_major_error_notification(
    db: Session,
    *,
    source: str,
    message: str,
    job_id: int | None = None,
) -> bool:
    fields: List[Dict[str, Any]] = [
        {"name": "Source", "value": source, "inline": True},
    ]
    if job_id is not None:
        fields.append({"name": "Job ID", "value": str(job_id), "inline": True})

    return send_discord_notification(
        db,
        feature_key="system_errors",
        event_type="error",
        title="PosterFlow Major Error",
        description=message,
        fields=fields,
        color=0xF44336,
    )