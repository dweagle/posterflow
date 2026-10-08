import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import apprise
from apprise.result import AppriseResultStatus
from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_error, log_warning
from models.setting import get_setting
from services.discord_notifications import DISCORD_NOTIFICATION_FEATURES, _format_title


NOTIFY_TIMEOUT_SECONDS = 30

APPRISE_ASSET = apprise.AppriseAsset(
    app_id="PosterFlow",
    app_desc="PosterFlow Notification",
    app_url="https://github.com/dweagle/posterflow",
)

TAG_SPLIT_RE = re.compile(r"[,\s]+")

EVENT_NOTIFY_TYPES = {
    "success": apprise.NotifyType.SUCCESS,
    "error": apprise.NotifyType.FAILURE,
    "info": apprise.NotifyType.INFO,
}

# Apprise's own log lines are relayed from each notify result instead
logging.getLogger("apprise").propagate = False


def _default_feature_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "on_success": True,
        "on_error": True,
        "include_summary": True,
        "include_details": True,
        "urls": "",
        "tags": "",
    }


def _normalize_features(features: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    normalized = {key: _default_feature_config() for key in DISCORD_NOTIFICATION_FEATURES}
    if not isinstance(features, dict):
        return normalized

    for key in DISCORD_NOTIFICATION_FEATURES:
        value = features.get(key)
        if not isinstance(value, dict):
            continue
        merged = _default_feature_config()
        for flag in ("enabled", "on_success", "on_error", "include_summary", "include_details"):
            merged[flag] = bool(value.get(flag, merged[flag]))
        merged["urls"] = str(value.get("urls") or "").strip()
        merged["tags"] = str(value.get("tags") or "").strip()
        normalized[key] = merged

    return normalized


def _read_apprise_config(db: Session) -> Dict[str, Any]:
    enabled_raw = get_setting(db, "apprise_notifications_enabled")
    urls_raw = get_setting(db, "apprise_notifications_urls")
    features_raw = get_setting(db, "apprise_notifications_features")

    enabled = (
        str(enabled_raw.value).strip().lower() == "true"
        if enabled_raw and enabled_raw.value is not None
        else False
    )
    urls = str(urls_raw.value) if urls_raw and urls_raw.value else ""

    parsed_features: Dict[str, Any] | None = None
    if features_raw and features_raw.value:
        try:
            loaded = json.loads(features_raw.value)
            if isinstance(loaded, dict):
                parsed_features = loaded
        except (json.JSONDecodeError, TypeError):
            log_warning(LogTags.API, "Invalid apprise_notifications_features JSON while sending notification")

    return {"enabled": enabled, "urls": urls, "features": _normalize_features(parsed_features)}


def _parse_tags(raw: Any) -> List[str]:
    return [tag for tag in TAG_SPLIT_RE.split(str(raw or "").strip()) if tag]


def _numbered_entries(raw: Any) -> List[Tuple[int, str, List[str]]]:
    # one URL per line, optionally tag-prefixed like Apprise's text config: "home, urgent = pover://..."
    entries: List[Tuple[int, str, List[str]]] = []
    for line_no, line in enumerate(str(raw or "").splitlines(), start=1):
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        tags: List[str] = []
        head, sep, tail = candidate.partition("=")
        if sep and "://" not in head and "://" in tail:
            tags, candidate = _parse_tags(head), tail.strip()
        entries.append((line_no, candidate, tags))
    return entries


def parse_apprise_entries(raw: Any) -> List[Tuple[str, List[str]]]:
    return [(url, tags) for _, url, tags in _numbered_entries(raw)]


def parse_apprise_urls(raw: Any) -> List[str]:
    return [url for _, url, _ in _numbered_entries(raw)]


def invalid_apprise_url_lines(raw: Any) -> List[int]:
    # the URL part of a line must parse as exactly one Apprise service
    bad: List[int] = []
    for line_no, url, _ in _numbered_entries(raw):
        probe = apprise.Apprise(asset=APPRISE_ASSET)
        if not probe.add(url) or len(probe) != 1:
            bad.append(line_no)
    return bad


def _build_body(
    description: Any,
    fields: Optional[List[Dict[str, Any]]],
    *,
    include_summary: bool,
    include_details: bool,
) -> str:
    parts: List[str] = []
    summary = str(description or "").strip()
    if include_summary and summary:
        parts.append(summary)

    if include_details and fields:
        inline: List[str] = []
        block: List[str] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name", "Details")).strip() or "Details"
            value = str(field.get("value", "-")).strip() or "-"
            if field.get("inline") and "\n" not in value:
                inline.append(f"{name}: {value}")
            elif "\n" in value:
                block.append(f"{name}:\n{value}")
            else:
                block.append(f"{name}: {value}")
        lines = ([" | ".join(inline)] if inline else []) + block
        if lines:
            parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _notify(
    entries: List[Tuple[str, List[str]]],
    *,
    title: str,
    body: str,
    event_type: str,
    context: str,
    tags: Optional[List[str]] = None,
) -> bool:
    client = apprise.Apprise(asset=APPRISE_ASSET)
    for url, url_tags in entries:
        if not client.add(url, tag=url_tags or None):
            log_warning(LogTags.API, "Apprise URL could not be parsed and was skipped", context=context)
    if not len(client):
        return False

    # feature tags select tagged URLs (any match); no tags means every URL
    result = client.notify(
        body=body or title,
        title=title,
        notify_type=EVENT_NOTIFY_TYPES.get(str(event_type or "").strip().lower(), apprise.NotifyType.INFO),
        body_format=apprise.NotifyFormat.TEXT,
        tag=list(tags) if tags else "all",
        timeout=NOTIFY_TIMEOUT_SECONDS,
    )
    if result.status == AppriseResultStatus.NOMATCH:
        log_warning(LogTags.API, f"No Apprise URL carries the tags {', '.join(tags or [])}; nothing sent", context=context)
        return False

    for entry in result.logs():
        if str(entry.level).upper() in ("WARNING", "ERROR", "CRITICAL"):
            log_warning(LogTags.API, f"Apprise: {entry.message}", context=context)
    for service in result:
        if not service:
            log_warning(LogTags.API, f"Apprise delivery to {service.name} failed", url=service.url, context=context)

    sent = int(result.success_count)
    if sent:
        log_debug(LogTags.API, f"Apprise notification sent ({context}) to {sent} of {len(client)} service(s)")
    return sent > 0


def send_apprise_notification(
    db: Session,
    *,
    feature_key: str,
    event_type: str,
    title: str,
    description: str = "",
    fields: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Send through Apprise if global and feature toggles allow it."""
    try:
        config = _read_apprise_config(db)
        if not config["enabled"]:
            return False

        feature = config["features"].get(feature_key)
        if not isinstance(feature, dict) or not feature.get("enabled"):
            return False
        if event_type == "success" and not feature.get("on_success", True):
            return False
        if event_type == "error" and not feature.get("on_error", True):
            return False

        entries = parse_apprise_entries(feature.get("urls")) or parse_apprise_entries(config["urls"])
        if not entries:
            return False

        body = _build_body(
            description,
            fields,
            include_summary=bool(feature.get("include_summary", True)),
            include_details=bool(feature.get("include_details", True)),
        )
        return _notify(
            entries,
            title=str(title or "PosterFlow Notification").strip(),
            body=body,
            event_type=event_type,
            context=f"{feature_key}:{event_type}",
            tags=_parse_tags(feature.get("tags")),
        )
    except Exception as exc:
        log_error(LogTags.API, f"Failed to send Apprise notification: {exc}")
        return False


def send_apprise_workflow_summary(db: Session, *, embeds: List[Dict[str, Any]]) -> bool:
    """One Apprise message with a section per workflow step, gated on the workflow feature."""
    try:
        config = _read_apprise_config(db)
        if not config["enabled"]:
            return False

        feature = config["features"].get("workflow")
        if not isinstance(feature, dict) or not feature.get("enabled"):
            return False

        entries = parse_apprise_entries(feature.get("urls")) or parse_apprise_entries(config["urls"])
        if not entries:
            return False

        include_summary = bool(feature.get("include_summary", True))
        include_details = bool(feature.get("include_details", True))
        sections: List[str] = []
        any_error = False
        for spec in embeds[:25]:
            if not isinstance(spec, dict):
                continue
            event_type = str(spec.get("event_type") or "info")
            if event_type == "success" and not feature.get("on_success", True):
                continue
            if event_type == "error" and not feature.get("on_error", True):
                continue
            any_error = any_error or event_type == "error"
            heading = _format_title(event_type, str(spec.get("title") or "PosterFlow"))
            body = _build_body(
                spec.get("description"),
                spec.get("fields"),
                include_summary=include_summary,
                include_details=include_details,
            )
            sections.append(f"{heading}\n{body}" if body else heading)

        if not sections:
            return False

        return _notify(
            entries,
            title="PosterFlow Workflow Summary",
            body="\n\n".join(sections),
            event_type="error" if any_error else "success",
            context="workflow:summary",
            tags=_parse_tags(feature.get("tags")),
        )
    except Exception as exc:
        log_error(LogTags.API, f"Failed to send Apprise workflow summary: {exc}")
        return False


def send_apprise_test_notification(urls: List[str], *, title: str, body: str) -> Dict[str, Any]:
    """Send a test to every URL and report each service's outcome; URLs are already validated."""
    client = apprise.Apprise(asset=APPRISE_ASSET)
    for url in urls:
        client.add(url)

    result = client.notify(
        body=body,
        title=title,
        notify_type=apprise.NotifyType.INFO,
        body_format=apprise.NotifyFormat.TEXT,
        timeout=NOTIFY_TIMEOUT_SECONDS,
    )
    warnings = [
        str(entry.message)
        for entry in result.logs()
        if str(entry.level).upper() in ("WARNING", "ERROR", "CRITICAL")
    ]
    return {
        "results": [
            {"name": str(service.name), "url": str(service.url), "ok": bool(service)}
            for service in result
        ],
        "warnings": warnings,
    }
