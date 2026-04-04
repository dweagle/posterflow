from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from typing import Any, Optional
from database import Base

class Setting(Base):
    """
    Stores application settings and user credentials.
    Each setting has a key-value pair.
    """
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(String, nullable=True)  # Plain text storage
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Setting(key='{self.key}')>"


def get_setting(db: Session, key: str) -> Optional[Setting]:
    """Fetch a Setting row by key."""
    return db.query(Setting).filter(Setting.key == key).first()


def upsert_setting(db: Session, key: str, value: str) -> Setting:
    """Update an existing setting value or create it if missing."""
    setting = get_setting(db, key)
    if setting:
        setting.value = value
    else:
        setting = Setting(key=key, value=value)
        db.add(setting)
    return setting


def get_setting_value(db: Session, key: str, default: Any = None) -> Any:
    """Fetch a Setting value by key with a default fallback."""
    setting = get_setting(db, key)
    return setting.value if setting and setting.value is not None else default