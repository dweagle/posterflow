"""
Authentication API endpoints.

All routes here are exempt from the AppAuthMiddleware so the frontend can
check status and unlock the app even before a password has been entered.
"""
import traceback
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Dict, Any
from pydantic import BaseModel
from database import get_db
from core.auth import (
    is_password_set,
    check_password,
    set_password,
    invalidate_auth_cache,
)
from core.logging import LogTags, log_info, log_warning, log_error, log_user_action
from core.rate_limiter import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


class VerifyPasswordRequest(BaseModel):
    password: str


class SetPasswordRequest(BaseModel):
    current_password: str = ""
    new_password: str


class ClearPasswordRequest(BaseModel):
    current_password: str


@router.get("/status")
async def get_auth_status(db: Session = Depends(get_db)) -> Dict[str, bool]:
    """Returns whether a password has been configured. Always unauthenticated."""
    return {"password_set": is_password_set(db)}


@router.post("/verify")
@limiter.limit("10/minute")
async def verify_password_endpoint(
    request: Request,
    payload: VerifyPasswordRequest,
    db: Session = Depends(get_db),
) -> Dict[str, bool]:
    """Verify a password without changing anything. Returns {valid: bool}. Always unauthenticated."""
    valid = check_password(db, payload.password)
    if not valid:
        log_warning(LogTags.API, "Failed unlock attempt")
    return {"valid": valid}


@router.post("/set")
@limiter.limit("10/minute")
async def set_password_endpoint(
    request: Request,
    payload: SetPasswordRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Set or change the app password.

    - If no password is currently set, `current_password` is ignored.
    - If a password is currently set, `current_password` must be correct.
    - `new_password` must be non-empty.
    """
    if not payload.new_password.strip():
        raise HTTPException(status_code=400, detail="New password cannot be empty")

    password_currently_set = is_password_set(db)

    if password_currently_set:
        if not check_password(db, payload.current_password):
            log_warning(LogTags.API, "Failed password change attempt – wrong current password")
            raise HTTPException(status_code=403, detail="Current password is incorrect")

    try:
        set_password(db, payload.new_password.strip())
        db.commit()
        invalidate_auth_cache()
        action = "changed" if password_currently_set else "set"
        log_user_action(f"App password {action}")
        return {"message": f"Password {action} successfully"}
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Failed to set app password: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to save password")


@router.post("/clear")
async def clear_password_endpoint(
    payload: ClearPasswordRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Remove the app password. Requires the current password to confirm."""
    if not is_password_set(db):
        return {"message": "No password was set"}

    if not check_password(db, payload.current_password):
        log_warning(LogTags.API, "Failed password removal attempt – wrong current password")
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    try:
        set_password(db, "")
        db.commit()
        invalidate_auth_cache()
        log_user_action("App password removed")
        return {"message": "Password removed successfully"}
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Failed to remove app password: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to remove password")
