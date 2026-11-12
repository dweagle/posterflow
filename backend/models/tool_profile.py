"""A tool profile: one named settings set of a Tools page tool. The Tools page edits one, a run
or a schedule names one, and every tool with settings keeps a default profile. A tool that runs
per instance (the arr renamers) keeps a list of profiles, and a default, for each instance."""

from typing import List, Optional

from sqlalchemy import Boolean, Column, Integer, String, Text
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from database import Base
from util.utc_datetime import UTCDateTime


class ToolProfile(Base):
    __tablename__ = "tool_profiles"

    id = Column(Integer, primary_key=True, index=True)
    tool_id = Column(String, nullable=False, index=True)
    instance = Column(String, nullable=True, index=True)  # the arr instance the profile belongs to; NULL for a tool with one list
    name = Column(String, nullable=False)
    settings = Column(Text, nullable=False)  # JSON in the shape the tool's normalize_settings gives
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(UTCDateTime, server_default=func.now())
    updated_at = Column(UTCDateTime, onupdate=func.now())

    def __repr__(self) -> str:
        return f"<ToolProfile(id={self.id}, tool_id='{self.tool_id}', instance={self.instance!r}, name='{self.name}')>"


def list_tool_profiles(db: Session, tool_id: str, instance: Optional[str] = None) -> List[ToolProfile]:
    """One profile list, oldest first: the tool's own, or the one of `instance`."""
    query = db.query(ToolProfile).filter(ToolProfile.tool_id == tool_id)
    query = query.filter(ToolProfile.instance == instance) if instance is not None else query.filter(ToolProfile.instance.is_(None))
    return query.order_by(ToolProfile.id.asc()).all()


def all_tool_profiles(db: Session, tool_id: str) -> List[ToolProfile]:
    """Every profile of the tool, whatever instance it belongs to."""
    return db.query(ToolProfile).filter(ToolProfile.tool_id == tool_id).order_by(ToolProfile.id.asc()).all()


def get_tool_profile(db: Session, tool_id: str, profile_id: int) -> Optional[ToolProfile]:
    return db.query(ToolProfile).filter(ToolProfile.tool_id == tool_id, ToolProfile.id == profile_id).first()
