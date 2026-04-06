import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict

from database import get_db
from models.setting import get_setting_value, upsert_setting
from core.hooks import list_available_scripts, HOOK_KEYS, HOOK_KEY_LABELS, SCRIPTS_DIR
from core.logging import LogTags, log_user_action, log_info

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

_VALID_RUN_WHEN = {"always", "scheduled", "manual"}
_VALID_TRIGGER_ON = {"always", "success", "failure"}

SETTING_SCRIPT_LOGGING = "hooks_script_logging"


class HookConfig(BaseModel):
    enabled: bool = False
    script: str = ""
    run_when: str = "always"
    trigger_on: str = "always"


class HooksPayload(BaseModel):
    hooks: Dict[str, HookConfig]


class ScriptLoggingPayload(BaseModel):
    enabled: bool


@router.get("/available")
def get_available_scripts():
    """List files in /config/scripts/ that can be selected as hooks."""
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    return {"scripts": list_available_scripts(), "scripts_dir": str(SCRIPTS_DIR)}


@router.get("/hooks")
def get_hooks(db: Session = Depends(get_db)):
    """Return current post-job hook configuration."""
    raw = get_setting_value(db, "post_job_hooks")

    # Build defaults for every key
    defaults: dict = {
        key: {"enabled": False, "script": "", "run_when": "always", "trigger_on": "always"}
        for key in HOOK_KEYS
    }

    if raw:
        try:
            stored = json.loads(raw)
            if isinstance(stored, dict):
                for key in HOOK_KEYS:
                    if key in stored and isinstance(stored[key], dict):
                        defaults[key].update(stored[key])
        except (json.JSONDecodeError, TypeError):
            pass

    # Attach human-readable labels for the frontend
    result = {}
    for key in HOOK_KEYS:
        result[key] = {**defaults[key], "label": HOOK_KEY_LABELS[key]}

    return {"hooks": result, "scripts_dir": str(SCRIPTS_DIR)}


@router.put("/hooks")
def save_hooks(payload: HooksPayload, db: Session = Depends(get_db)):
    """Persist post-job hook configuration."""
    validated: dict = {}

    for key, config in payload.hooks.items():
        if key not in HOOK_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown hook key: '{key}'")

        if config.run_when not in _VALID_RUN_WHEN:
            raise HTTPException(status_code=400, detail=f"Invalid run_when '{config.run_when}' for key '{key}'")

        if config.trigger_on not in _VALID_TRIGGER_ON:
            raise HTTPException(status_code=400, detail=f"Invalid trigger_on '{config.trigger_on}' for key '{key}'")

        validated[key] = {
            "enabled": bool(config.enabled),
            "script": str(config.script or "").strip(),
            "run_when": config.run_when,
            "trigger_on": config.trigger_on,
        }

    upsert_setting(db, "post_job_hooks", json.dumps(validated))
    db.commit()

    log_user_action("Updated post-job hooks configuration", hook_count=len(validated))
    log_info(LogTags.API, f"Saved {len(validated)} hook(s)")

    return {"message": "Hooks saved", "hooks": validated}


@router.get("/logging")
def get_script_logging(db: Session = Depends(get_db)):
    """Return whether script stdout/stderr logging is enabled."""
    raw = get_setting_value(db, SETTING_SCRIPT_LOGGING)
    # Default: enabled
    enabled = raw.lower() == "true" if raw is not None else True
    return {"enabled": enabled}


@router.put("/logging")
def set_script_logging(payload: ScriptLoggingPayload, db: Session = Depends(get_db)):
    """Enable or disable script stdout/stderr logging."""
    upsert_setting(db, SETTING_SCRIPT_LOGGING, "true" if payload.enabled else "false")
    db.commit()
    log_user_action("Updated script logging setting", enabled=payload.enabled)
    return {"enabled": payload.enabled}
