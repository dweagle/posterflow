from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from typing import Any, Dict, List, Optional
import json
from database import Base


# Canonical default workflow step configuration. Mirrors the FlowConfig defaults
# used by the poster manager API and the flow executor.
DEFAULT_FLOW_CONFIG: Dict[str, Any] = {
    "idarr": {"enabled": False, "stop_on_error": False, "scope_indices": [], "sync_after_run": False},
    "sync_drives": {"enabled": True, "stop_on_error": True},
    "rename_assets": {"enabled": True, "stop_on_error": True},
    "detect_unmatched": {"enabled": True, "stop_on_error": True},
    "border_replacer": {"enabled": False, "stop_on_error": True},
    "plex_upload": {"enabled": False, "stop_on_error": False},
    "cleanup_assets": {"enabled": True, "delete_unknown": False},
}


def default_flow_config() -> Dict[str, Any]:
    """Return a fresh deep copy of the default step configuration."""
    return json.loads(json.dumps(DEFAULT_FLOW_CONFIG))


class Workflow(Base):
    """A named, saved poster workflow (a combination of enabled steps)."""
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    config = Column(Text, nullable=False)  # JSON string of the FlowConfig step selection
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Workflow(id={self.id}, name='{self.name}')>"


def list_workflows(db: Session) -> List[Workflow]:
    return db.query(Workflow).order_by(Workflow.id.asc()).all()


def get_workflow(db: Session, workflow_id: int) -> Optional[Workflow]:
    return db.query(Workflow).filter(Workflow.id == workflow_id).first()


def get_default_workflow(db: Session) -> Optional[Workflow]:
    """Return the workflow flagged as default, falling back to the earliest one."""
    wf = db.query(Workflow).filter(Workflow.is_default == True).order_by(Workflow.id.asc()).first()  # noqa: E712
    if wf:
        return wf
    return db.query(Workflow).order_by(Workflow.id.asc()).first()


def get_workflow_config(db: Session, workflow_id: Optional[int] = None) -> Dict[str, Any]:
    """Resolve the step config for a workflow.

    Falls back to the default workflow when workflow_id is None or not found,
    and to the hardcoded defaults when no workflows exist at all.
    """
    wf: Optional[Workflow] = None
    if workflow_id is not None:
        wf = get_workflow(db, workflow_id)
    if wf is None:
        wf = get_default_workflow(db)
    if wf is None:
        return default_flow_config()
    try:
        parsed = json.loads(wf.config)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass
    return default_flow_config()
