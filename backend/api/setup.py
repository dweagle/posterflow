from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict
from database import get_db
from models.setting import get_setting

router = APIRouter(prefix="/api/setup", tags=["setup"])

@router.get("/status")
def get_setup_status(db: Session = Depends(get_db)) -> Dict[str, bool]:
    """Check if initial setup is complete"""
    setting = get_setting(db, "setup_complete")
    
    if setting and setting.value == "true":
        return {"setup_complete": True}
    
    return {"setup_complete": False}