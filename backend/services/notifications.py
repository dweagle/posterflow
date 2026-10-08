from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from services.apprise_notifications import send_apprise_notification, send_apprise_workflow_summary
from services.discord_notifications import send_discord_notification, send_discord_workflow_summary


def send_notification(
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
    """Fan out to every channel; each one applies its own enable, feature and event gating."""
    discord_sent = send_discord_notification(
        db,
        feature_key=feature_key,
        event_type=event_type,
        title=title,
        description=description,
        fields=fields,
        color=color,
        footer_text=footer_text,
        username=username,
        include_spacer_image=include_spacer_image,
    )
    apprise_sent = send_apprise_notification(
        db,
        feature_key=feature_key,
        event_type=event_type,
        title=title,
        description=description,
        fields=fields,
    )
    return discord_sent or apprise_sent


def send_workflow_summary(
    db: Session,
    *,
    embeds: List[Dict[str, Any]],
    username: str = "PosterFlow",
    footer_text: str = "PosterFlow Notification",
) -> bool:
    discord_sent = send_discord_workflow_summary(db, embeds=embeds, username=username, footer_text=footer_text)
    apprise_sent = send_apprise_workflow_summary(db, embeds=embeds)
    return discord_sent or apprise_sent


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

    return send_notification(
        db,
        feature_key="system_errors",
        event_type="error",
        title="PosterFlow Major Error",
        description=message,
        fields=fields,
        color=0xF44336,
    )
