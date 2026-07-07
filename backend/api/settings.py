import json
import traceback
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import Dict, List, Any
from pydantic import BaseModel
import requests
from database import get_db
from models.setting import Setting, get_setting, upsert_setting
from core.config import settings as app_settings
from core.logging import LogTags, log_user_action, log_error, log_info, log_warning

router = APIRouter(prefix="/api/settings", tags=["settings"])

# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------

MASKED_VALUE = "***masked***"

# Plain string setting keys whose values should never be returned in full
SENSITIVE_PLAIN_KEYS: set[str] = {
    "plex_token",
    "google_client_secret",
    "google_token",
    "discord_notifications_webhook_url",
    "tmdb_api_key",
    # App password – hash and salt must never leave the backend
    "app_password_hash",
    "app_password_salt",
}

# Setting keys that hold JSON blobs whose inner fields must be masked.
# Maps key -> list of JSON field names inside each element that are sensitive.
SENSITIVE_JSON_KEYS: Dict[str, List[str]] = {
    "plex_instances": ["api_key"],
    "radarr_instances": ["api_key"],
    "sonarr_instances": ["api_key"],
    "maker_tools_monitor_config": ["tmdb_api_key"],
    "discord_notifications_features": ["webhook_url"],
}

NON_REVEALABLE_KEYS: set[str] = {
    "app_password_hash",
    "app_password_salt",
}

# ---------------------------------------------------------------------------
# Bulk-settings allowlist
# Only keys in this set can be written via POST /api/settings/bulk.
# All other keys are silently dropped (+ a warning is logged).
# Internal services write directly via upsert_setting() and are not affected.
# ---------------------------------------------------------------------------
BULK_SETTINGS_ALLOWLIST: frozenset = frozenset({
    # Google / Rclone credentials
    "google_client_id",
    "google_client_secret",
    "google_token",
    "google_refresh_token",
    "google_service_account_file",
    # App setup
    "setup_complete",
    "poster_destination",
    # Media server instances
    "plex_instances",
    "sonarr_instances",
    "radarr_instances",
    # Poster Renamer / Unmatched
    "poster_renamer_libraries",
    "unmatched_assets_libraries",
    "unmatched_ignore_root_folders",
    "unmatched_ignore_collections",
    "unmatched_ignore_unmonitored",
    "tmdb_api_key",
    # Border Replacer
    "border_replacer_colors",
    "border_replacer_width",
    "border_replacer_band_width",
    "border_replacer_mode",
    "border_replacer_holidays",
    "border_replacer_skip_non_holiday",
    "border_replacer_remove_borders",
    "border_replacer_season_mode",
    "border_replacer_season_colors",
    "border_replacer_season_width",
    # Border Replacer — main border style / inner effects
    "border_replacer_style",
    "border_replacer_gradient_colors",
    "border_replacer_gradient_direction",
    "border_replacer_overlay_image",
    "border_replacer_overlay_remove_existing",
    "border_replacer_inner_effect",
    "border_replacer_inner_color",
    "border_replacer_inner_opacity",
    "border_replacer_inner_width",
    "border_replacer_fade_width",
    # Border Replacer — season border style / inner effects
    "border_replacer_season_style",
    "border_replacer_season_overlay_image",
    "border_replacer_season_overlay_remove_existing",
    "border_replacer_season_gradient_colors",
    "border_replacer_season_gradient_direction",
    "border_replacer_season_inner_effect",
    "border_replacer_season_inner_color",
    "border_replacer_season_inner_opacity",
    "border_replacer_season_inner_width",
    "border_replacer_season_fade_width",
    # Border Replacer — Plex label/genre/collection rules
    "border_replacer_plex_rules",
    "border_replacer_rule_run_types",
    "border_replacer_rule_libraries",
    "auto_run_border",
    # Asset Cleanup
    "auto_run_cleanup",
    "cleanup_delete_unknown",
    "asset_cleanup_ignore",
    # GDrive storage path
    "gdrive_storage_path",
    # PSD export
    "psd_export_folder",
    "psd_image_export_folder",
    "psd_template_path",
    "psd_export_folder_mm2k",
    "psd_image_export_folder_mm2k",
    "psd_template_path_mm2k",
    "psd_open_photopea",
    "psd_poster_fit_border",
    # Sidebar layout
    "sidebar_config",
    # Community Requests maker preferences
    "idarr_quick_add_community",
})


def _mask_settings_payload(payload: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of the settings dict with sensitive values replaced."""
    result: Dict[str, str] = {}
    for key, value in payload.items():
        if key in SENSITIVE_PLAIN_KEYS:
            result[key] = MASKED_VALUE if value else value
        elif key in SENSITIVE_JSON_KEYS:
            sensitive_fields = SENSITIVE_JSON_KEYS[key]
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            for field in sensitive_fields:
                                if item.get(field):
                                    item[field] = MASKED_VALUE
                elif isinstance(parsed, dict):
                    for field in sensitive_fields:
                        if parsed.get(field):
                            parsed[field] = MASKED_VALUE
                result[key] = json.dumps(parsed)
            except (json.JSONDecodeError, TypeError):
                result[key] = value
        else:
            result[key] = value
    return result


def _unmask_setting_value(key: str, incoming_value: str, db: Session) -> str:
    """
    Before saving, if the incoming value (or a field within it) is the masked
    placeholder, restore the real value stored in the database so that the
    actual secret is never overwritten with the placeholder string.
    """
    if key in SENSITIVE_PLAIN_KEYS:
        if incoming_value == MASKED_VALUE:
            existing = get_setting(db, key)
            return existing.value if existing and existing.value else incoming_value
        return incoming_value

    if key in SENSITIVE_JSON_KEYS:
        sensitive_fields = SENSITIVE_JSON_KEYS[key]
        try:
            incoming_parsed = json.loads(incoming_value)
        except (json.JSONDecodeError, TypeError):
            return incoming_value

        # Detect whether any sensitive field is masked
        has_masked = False
        if isinstance(incoming_parsed, list):
            for item in incoming_parsed:
                if isinstance(item, dict) and any(item.get(f) == MASKED_VALUE for f in sensitive_fields):
                    has_masked = True
                    break
        elif isinstance(incoming_parsed, dict):
            if any(incoming_parsed.get(f) == MASKED_VALUE for f in sensitive_fields):
                has_masked = True

        if not has_masked:
            return incoming_value

        # Load existing DB value and restore masked fields
        existing_setting = get_setting(db, key)
        if not existing_setting or not existing_setting.value:
            return incoming_value

        try:
            existing_parsed = json.loads(existing_setting.value)
        except (json.JSONDecodeError, TypeError):
            return incoming_value

        if isinstance(incoming_parsed, list) and isinstance(existing_parsed, list):
            # Build a lookup by `name` field so order changes are handled safely
            existing_by_name = {
                item.get("name", ""): item for item in existing_parsed if isinstance(item, dict)
            }
            for item in incoming_parsed:
                if not isinstance(item, dict):
                    continue
                existing_item = existing_by_name.get(item.get("name", ""), {})
                for field in sensitive_fields:
                    if item.get(field) == MASKED_VALUE:
                        item[field] = existing_item.get(field, "")
        elif isinstance(incoming_parsed, dict) and isinstance(existing_parsed, dict):
            for field in sensitive_fields:
                if incoming_parsed.get(field) == MASKED_VALUE:
                    incoming_parsed[field] = existing_parsed.get(field, "")

        return json.dumps(incoming_parsed)

    return incoming_value


# ---------------------------------------------------------------------------

class PlexLibraryConfig(BaseModel):
    instance_name: str
    libraries: List[Dict[str, Any]]


class RevealSensitiveSettingRequest(BaseModel):
    setting_key: str
    field: str | None = None
    instance_name: str | None = None
    index: int | None = None


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


class DiscordNotificationFeatureConfig(BaseModel):
    enabled: bool = False
    on_success: bool = True
    on_error: bool = True
    include_summary: bool = True
    include_details: bool = True
    webhook_url: str = ""
    mention: str = ""
    mention_on_error: bool = True
    mention_on_success: bool = False
    mention_on_info: bool = False


class DiscordNotificationConfigRequest(BaseModel):
    enabled: bool = False
    webhook_url: str = ""
    mention: str = ""
    mention_on_error: bool = True
    mention_on_success: bool = False
    mention_on_info: bool = False
    features: Dict[str, DiscordNotificationFeatureConfig]


def _default_discord_features() -> Dict[str, Dict[str, Any]]:
    return {
        key: DiscordNotificationFeatureConfig().model_dump()
        for key in DISCORD_NOTIFICATION_FEATURES
    }


def _normalize_discord_features(features: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    normalized = _default_discord_features()
    if not isinstance(features, dict):
        return normalized

    for key in DISCORD_NOTIFICATION_FEATURES:
        candidate = features.get(key)
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump()
        elif hasattr(candidate, "dict") and callable(getattr(candidate, "dict")):
            candidate = candidate.dict()
        if not isinstance(candidate, dict):
            continue
        normalized_candidate: Dict[str, Any] | None = None
        try:
            normalized_candidate = DiscordNotificationFeatureConfig(**candidate).model_dump()
        except Exception:
            normalized_candidate = None
        if normalized_candidate is None:
            continue
        normalized[key] = normalized_candidate

    return normalized


def _is_valid_discord_webhook(webhook_url: str) -> bool:
    trimmed = webhook_url.strip()
    return trimmed.startswith("https://discord.com/api/webhooks/") or trimmed.startswith(
        "https://discordapp.com/api/webhooks/"
    )


def _mask_webhook(webhook_url: str) -> str:
    trimmed = webhook_url.strip()
    if len(trimmed) <= 12:
        return "***"
    return f"{trimmed[:8]}...{trimmed[-4:]}"


@router.get("/notifications/discord")
def get_discord_notification_config(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get Discord notification configuration and per-feature toggles."""
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
    webhook_url = webhook_raw.value if webhook_raw and webhook_raw.value else ""
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

    features: Dict[str, Any] | None = None
    if features_raw and features_raw.value:
        try:
            parsed = json.loads(features_raw.value)
            if isinstance(parsed, dict):
                features = parsed
        except json.JSONDecodeError:
            log_warning(LogTags.API, "Invalid JSON for discord_notifications_features; using defaults")

    return {
        "enabled": enabled,
        "webhook_url": MASKED_VALUE if webhook_url else webhook_url,
        "mention": mention,
        "mention_on_error": mention_on_error,
        "mention_on_success": mention_on_success,
        "features": {
            fkey: {
                **fval,
                "webhook_url": MASKED_VALUE if fval.get("webhook_url") else fval.get("webhook_url", ""),
            }
            for fkey, fval in _normalize_discord_features(features).items()
        },
    }


@router.post("/notifications/discord")
def save_discord_notification_config(
    payload: DiscordNotificationConfigRequest,
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    """Save Discord notification configuration and per-feature preferences."""
    webhook_url = payload.webhook_url.strip()

    # If the client sent back the masked placeholder, restore the real value from DB
    if webhook_url == MASKED_VALUE:
        existing = get_setting(db, "discord_notifications_webhook_url")
        webhook_url = existing.value.strip() if existing and existing.value else ""

    if payload.enabled and not webhook_url:
        raise HTTPException(status_code=400, detail="Discord webhook URL is required when notifications are enabled")

    if webhook_url and not _is_valid_discord_webhook(webhook_url):
        raise HTTPException(status_code=400, detail="Discord webhook URL must start with https://discord.com/api/webhooks/")

    features = _normalize_discord_features(payload.features)

    # Restore masked per-feature webhook URLs from the database
    existing_features_setting = get_setting(db, "discord_notifications_features")
    existing_features: Dict[str, Any] = {}
    if existing_features_setting and existing_features_setting.value:
        try:
            parsed_existing = json.loads(existing_features_setting.value)
            if isinstance(parsed_existing, dict):
                existing_features = parsed_existing
        except json.JSONDecodeError:
            pass

    for fkey, fval in features.items():
        fwh = fval.get("webhook_url", "")
        if fwh == MASKED_VALUE:
            old_webhook = existing_features.get(fkey, {}).get("webhook_url", "")
            fval["webhook_url"] = old_webhook
        elif fwh and not _is_valid_discord_webhook(fwh):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid Discord webhook URL for feature '{fkey}'",
            )
        else:
            fval["webhook_url"] = fwh.strip() if fwh else ""

    upsert_setting(db, "discord_notifications_enabled", "true" if payload.enabled else "false")
    upsert_setting(db, "discord_notifications_webhook_url", webhook_url)
    upsert_setting(db, "discord_notifications_mention", payload.mention.strip())
    upsert_setting(db, "discord_notifications_mention_on_error", "true" if payload.mention_on_error else "false")
    upsert_setting(db, "discord_notifications_mention_on_success", "true" if payload.mention_on_success else "false")
    upsert_setting(db, "discord_notifications_features", json.dumps(features))

    try:
        db.commit()
        log_user_action(
            "Updated Discord notification settings",
            enabled=payload.enabled,
            webhook=_mask_webhook(webhook_url) if webhook_url else "",
        )
        log_info(LogTags.API, "Discord notification settings saved")
        return {"message": "Discord notification settings saved"}
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Failed to save Discord notification settings: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to save Discord notification settings")


@router.post("/notifications/discord/test")
def test_discord_notification(
    payload: DiscordNotificationConfigRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Send a test Discord notification to validate webhook settings."""
    webhook_url = payload.webhook_url.strip()

    # If the client sent back the masked placeholder, restore the real value from DB
    if webhook_url == MASKED_VALUE:
        existing = get_setting(db, "discord_notifications_webhook_url")
        webhook_url = existing.value.strip() if existing and existing.value else ""

    if not webhook_url:
        raise HTTPException(status_code=400, detail="Discord webhook URL is required")
    if not _is_valid_discord_webhook(webhook_url):
        raise HTTPException(status_code=400, detail="Discord webhook URL must start with https://discord.com/api/webhooks/")

    enabled_features = [name for name, cfg in _normalize_discord_features(payload.features).items() if cfg.get("enabled")]

    discord_payload = {
        "username": "PosterFlow",
        "embeds": [
            {
                "title": "PosterFlow Discord Notifications Test",
                "description": "Your webhook is configured correctly.",
                "color": 0x64B5F6,
                "fields": [
                    {
                        "name": "Notifications Enabled",
                        "value": "Yes" if payload.enabled else "No",
                        "inline": True,
                    },
                    {
                        "name": "Enabled Features",
                        "value": ", ".join(enabled_features) if enabled_features else "None selected",
                        "inline": False,
                    },
                ],
            }
        ],
    }

    try:
        response = requests.post(webhook_url, json=discord_payload, timeout=10)
    except requests.RequestException as e:
        log_error(LogTags.API, f"Discord test notification request failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to send Discord test notification")

    if response.status_code not in (200, 204):
        detail = response.text.strip() or "Discord rejected the webhook payload"
        log_warning(
            LogTags.API,
            "Discord test notification failed",
            status_code=response.status_code,
            webhook=_mask_webhook(webhook_url),
        )
        raise HTTPException(status_code=400, detail=f"Discord webhook returned {response.status_code}: {detail}")

    return {"success": True, "message": "Discord test notification sent successfully"}

@router.get("/")
def get_settings(db: Session = Depends(get_db)) -> Dict[str, str]:
    """Get all settings (sensitive values are masked)"""
    settings = db.query(Setting).all()
    payload: Dict[str, str] = {}
    for setting in settings:
        payload[setting.key] = setting.value
    return _mask_settings_payload(payload)


@router.post("/reveal")
def reveal_sensitive_setting(
    payload: RevealSensitiveSettingRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Reveal a specific sensitive value on explicit user request."""
    setting_key = payload.setting_key.strip()
    if not setting_key:
        raise HTTPException(status_code=400, detail="setting_key is required")

    if setting_key in NON_REVEALABLE_KEYS:
        raise HTTPException(status_code=403, detail="This setting cannot be revealed")

    if setting_key in SENSITIVE_PLAIN_KEYS:
        setting = get_setting(db, setting_key)
        return {
            "setting_key": setting_key,
            "value": setting.value if setting and setting.value is not None else "",
        }

    if setting_key in SENSITIVE_JSON_KEYS:
        allowed_fields = SENSITIVE_JSON_KEYS[setting_key]
        field = (payload.field or "").strip() or allowed_fields[0]
        if field not in allowed_fields:
            raise HTTPException(status_code=400, detail="Requested field is not revealable for this setting")

        setting = get_setting(db, setting_key)
        if not setting or not setting.value:
            return {"setting_key": setting_key, "field": field, "value": ""}

        try:
            parsed = json.loads(setting.value)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Stored setting JSON is invalid")

        if isinstance(parsed, dict):
            # dict-of-dicts: instance_name is used as the sub-key (e.g. feature key)
            if payload.instance_name:
                sub_dict = parsed.get(payload.instance_name.strip())
                if not isinstance(sub_dict, dict):
                    raise HTTPException(status_code=404, detail="Requested item was not found")
                return {
                    "setting_key": setting_key,
                    "field": field,
                    "instance_name": payload.instance_name.strip(),
                    "value": str(sub_dict.get(field) or ""),
                }
            return {
                "setting_key": setting_key,
                "field": field,
                "value": str(parsed.get(field) or ""),
            }

        if isinstance(parsed, list):
            target_item: Dict[str, Any] | None = None

            if isinstance(payload.index, int) and 0 <= payload.index < len(parsed):
                candidate = parsed[payload.index]
                if isinstance(candidate, dict):
                    target_item = candidate

            if target_item is None and payload.instance_name:
                normalized_name = payload.instance_name.strip()
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("name") or "").strip() == normalized_name:
                        target_item = item
                        break

            if target_item is None:
                raise HTTPException(status_code=404, detail="Requested instance was not found")

            return {
                "setting_key": setting_key,
                "field": field,
                "value": str(target_item.get(field) or ""),
            }

        raise HTTPException(status_code=400, detail="Unsupported setting format")

    raise HTTPException(status_code=400, detail="Setting is not marked as sensitive/revealable")

@router.post("/bulk")
def save_bulk_settings(settings: Dict[str, str], db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Save multiple settings at once.

    Only keys in BULK_SETTINGS_ALLOWLIST are accepted; unknown keys are dropped
    with a warning. Masked-placeholder values are silently preserved.
    """
    allowed: Dict[str, str] = {}
    rejected: List[str] = []

    for key, value in settings.items():
        if key in BULK_SETTINGS_ALLOWLIST:
            allowed[key] = value
        else:
            rejected.append(key)

    if rejected:
        log_warning(
            LogTags.API,
            f"Bulk settings: rejected {len(rejected)} disallowed key(s): {', '.join(sorted(rejected))}",
        )

    if not allowed:
        return {"message": "Settings saved", "count": 0}

    log_user_action(f"Saving settings: {', '.join(sorted(allowed.keys()))}")

    for key, value in allowed.items():
        resolved = _unmask_setting_value(key, value, db)
        upsert_setting(db, key, resolved)

    try:
        db.commit()
        log_info(LogTags.API, f"Successfully saved {len(allowed)} settings", count=len(allowed))
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Database error saving bulk settings: {e}\n{traceback.format_exc()}", count=len(allowed))
        raise HTTPException(status_code=500, detail="Failed to save settings")

    return {"message": "Settings saved", "count": len(allowed)}


@router.post("/service-account/upload")
async def upload_service_account_json(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    """Upload and persist a Google service account JSON file path."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    if not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Service account file must be a .json file")

    if file.size is not None and file.size > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Service account file must not exceed 50 MB")

    try:
        raw_content = await file.read()
        try:
            parsed = json.loads(raw_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="Invalid JSON file")

        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Service account file must contain a JSON object")

        service_account_dir = app_settings.config_dir / "service_accounts"
        service_account_dir.mkdir(parents=True, exist_ok=True)

        safe_stem = Path(file.filename).stem.replace(" ", "_")
        saved_name = f"{safe_stem}_{uuid4().hex[:8]}.json"
        saved_path = service_account_dir / saved_name
        saved_path.write_bytes(raw_content)

        upsert_setting(db, "google_service_account_file", str(saved_path))
        db.commit()

        log_user_action(
            f"Uploaded Google service account JSON: {file.filename}",
            saved_path=str(saved_path),
        )
        log_success_message = "Service account JSON uploaded"
        log_info(LogTags.API, log_success_message, saved_path=str(saved_path))

        return {
            "message": "Service account JSON uploaded successfully",
            "path": str(saved_path),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Failed to upload service account JSON: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to upload service account JSON")

@router.get("/plex-libraries")
def get_plex_library_config(db: Session = Depends(get_db)) -> Dict[str, List[Dict[str, Any]]]:
    """Get Plex library configurations for all instances"""
    # Get stored library configurations
    config = get_setting(db, "plex_library_config")
    
    if not config or not config.value:
        log_warning(LogTags.API, "No Plex library configurations found")
        return {"configs": []}
    
    try:
        configs = json.loads(config.value)
        return {"configs": configs}
    except json.JSONDecodeError as e:
        log_error(LogTags.API, f"Invalid JSON in plex_library_config: {e}\n{traceback.format_exc()}")
        return {"configs": []}
    except Exception as e:
        log_error(LogTags.API, f"Error parsing plex_library_config: {e}\n{traceback.format_exc()}")
        return {"configs": []}

@router.post("/plex-libraries")
def save_plex_library_config(config: PlexLibraryConfig, db: Session = Depends(get_db)) -> Dict[str, str]:
    """Save library configuration for a Plex instance"""
    log_user_action(f"Saving Plex library config for instance: {config.instance_name}")
    
    # Get existing configs
    existing_setting = get_setting(db, "plex_library_config")
    
    if existing_setting and existing_setting.value:
        try:
            configs = json.loads(existing_setting.value)
        except json.JSONDecodeError as e:
            log_error(LogTags.API, f"Invalid JSON in existing plex_library_config, resetting: {e}\n{traceback.format_exc()}")
            configs = []
        except Exception as e:
            log_error(LogTags.API, f"Error parsing existing plex_library_config, resetting: {e}\n{traceback.format_exc()}")
            configs = []
    else:
        configs = []
    
    # Find and update or append
    found = False
    for i, cfg in enumerate(configs):
        if cfg.get("instance_name") == config.instance_name:
            log_warning(LogTags.API, f"Overwriting existing Plex library config for instance: {config.instance_name}", instance=config.instance_name)
            configs[i] = config.dict()
            found = True
            break
    
    if not found:
        configs.append(config.dict())
    
    # Save back
    config_json = json.dumps(configs)
    upsert_setting(db, "plex_library_config", config_json)
    
    try:
        db.commit()
        log_info(LogTags.API, f"Successfully saved Plex library config for instance: {config.instance_name}", instance=config.instance_name, library_count=len(config.libraries))
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Database error saving Plex library config: {e}\n{traceback.format_exc()}", instance=config.instance_name)
        raise HTTPException(status_code=500, detail="Failed to save library configuration")
    
    return {"message": "Library configuration saved", "instance": config.instance_name}


# ---------------------------------------------------------------------------
# Plex Upload: Radarr/Sonarr instance -> Plex library routing map
# ---------------------------------------------------------------------------

PLEX_UPLOAD_INSTANCE_MAP_KEY = "plex_upload_instance_library_map"


class PlexUploadInstanceMapEntry(BaseModel):
    plex_instance: str
    library_key: str


class PlexUploadInstanceMapRequest(BaseModel):
    # Full replacement of the map: { "<arr instance name>": [ {plex_instance, library_key}, ... ] }
    map: Dict[str, List[PlexUploadInstanceMapEntry]]


@router.get("/plex-upload-instance-map")
def get_plex_upload_instance_map(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return the Radarr/Sonarr instance -> Plex library routing map for Plex Upload."""
    setting = get_setting(db, PLEX_UPLOAD_INSTANCE_MAP_KEY)
    if not setting or not setting.value:
        return {"map": {}}
    try:
        parsed = json.loads(setting.value)
        return {"map": parsed if isinstance(parsed, dict) else {}}
    except json.JSONDecodeError as e:
        log_error(LogTags.API, f"Invalid JSON in {PLEX_UPLOAD_INSTANCE_MAP_KEY}: {e}\n{traceback.format_exc()}")
        return {"map": {}}


@router.post("/plex-upload-instance-map")
def save_plex_upload_instance_map(
    payload: PlexUploadInstanceMapRequest, db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Replace the Plex Upload instance -> library routing map.

    Instances absent from the map fall back to uploading to all enabled libraries.
    Empty library lists are dropped so an instance with no targets stays unmapped.
    """
    cleaned: Dict[str, List[Dict[str, str]]] = {}
    for instance_name, entries in payload.map.items():
        name = instance_name.strip()
        if not name:
            continue
        rows = [
            {"plex_instance": e.plex_instance.strip(), "library_key": e.library_key.strip()}
            for e in entries
            if e.plex_instance.strip() and e.library_key.strip()
        ]
        if rows:
            cleaned[name] = rows

    upsert_setting(db, PLEX_UPLOAD_INSTANCE_MAP_KEY, json.dumps(cleaned))
    try:
        db.commit()
        log_info(LogTags.API, "Saved Plex Upload instance->library map", instances=len(cleaned))
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Database error saving {PLEX_UPLOAD_INSTANCE_MAP_KEY}: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to save instance routing map")
    return {"message": "Instance routing map saved", "map": cleaned}


# ---------------------------------------------------------------------------
# GDrive storage path
# ---------------------------------------------------------------------------

class GdriveStoragePathRequest(BaseModel):
    path: str


@router.get("/gdrive-storage")
def get_gdrive_storage_path(db: Session = Depends(get_db)) -> Dict[str, str]:
    """Return the current GDrive poster storage path setting."""
    setting = get_setting(db, "gdrive_storage_path")
    current_path = setting.value.strip() if setting and setting.value else ""
    return {"path": current_path}


@router.post("/gdrive-storage")
def save_gdrive_storage_path(
    payload: GdriveStoragePathRequest,
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    """Persist the GDrive poster storage path and apply it immediately."""
    from pathlib import Path as _Path
    from core.config import settings as app_settings

    raw = payload.path.strip()

    if raw:
        # Validate: must be an absolute path, no traversal
        try:
            resolved = _Path(raw).resolve()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path")
        if ".." in _Path(raw).parts:
            raise HTTPException(status_code=400, detail="Path traversal not allowed")
        if not _Path(raw).is_absolute():
            raise HTTPException(status_code=400, detail="Path must be absolute")
        save_value = raw
        new_dir = _Path(raw)
    else:
        # Empty = revert to default
        save_value = ""
        new_dir = _Path("/config/posters/gdrive")

    upsert_setting(db, "gdrive_storage_path", save_value)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Failed to save gdrive_storage_path: {e}")
        raise HTTPException(status_code=500, detail="Failed to save setting")

    # Apply immediately (without restart)
    app_settings.gdrive_dir = new_dir
    new_dir.mkdir(parents=True, exist_ok=True)

    log_user_action("Updated GDrive storage path", path=str(new_dir))
    return {"path": str(new_dir)}
