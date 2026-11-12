"""Tool profiles: the named settings sets of a Tools page tool (the image types of a cleanup or
reset, the rename settings of an arr instance). A tool with settings always has at least one, and
one of them is the default: what a run or a schedule gets when it names none, or names one that
is gone. A tool that runs per instance (the arr renamers) keeps its profiles per instance, so a
4K Radarr has a list and a default of its own. The tool itself says what its settings look like
(default_settings, normalize_settings, settings_problem); this module only stores and picks profiles."""

import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.logging import LogTags, log_warning
from models.tool_profile import ToolProfile, all_tool_profiles, list_tool_profiles

DEFAULT_PROFILE_NAME = "Default"
MAX_NAME = 60


def has_profiles(tool: Any) -> bool:
    return tool.default_settings is not None


def per_instance(tool: Any) -> bool:
    """Does each configured instance of the tool keep profiles of its own?"""
    return has_profiles(tool) and bool(tool.per_instance)


def _scope(db: Session, tool: Any, instance: Optional[str]) -> Optional[str]:
    """The instance a profile list belongs to: None for a tool with one list. Raises ValueError."""
    if not per_instance(tool):
        return None
    instance = str(instance or "").strip()
    if not instance:
        raise ValueError(f"Name the {tool.group.capitalize()} instance the profile belongs to.")
    if instance not in tool.instance_names(db):
        raise ValueError(f"No {tool.group.capitalize()} instance named '{instance}'.")
    return instance


def profiles_for(db: Session, tool: Any, instance: Optional[str] = None) -> List[ToolProfile]:
    """The profiles of the tool (of one instance, for a tool that keeps them per instance), oldest
    first. A list with no profile yet gets its Default one. Raises ValueError for an unknown instance."""
    if not has_profiles(tool):
        return []
    scope = _scope(db, tool, instance)
    rows = list_tool_profiles(db, tool.id, scope)
    if not rows:
        row = ToolProfile(tool_id=tool.id, instance=scope, name=DEFAULT_PROFILE_NAME, settings=json.dumps(tool.default_settings(db)), is_default=True)
        db.add(row)
        db.commit()
        db.refresh(row)
        rows = [row]
    return rows


def every_profile(db: Session, tool: Any) -> List[ToolProfile]:
    """Every profile the Tools page shows for the tool: one list, or one per configured instance."""
    if not per_instance(tool):
        return profiles_for(db, tool)
    return [row for name in tool.instance_names(db) for row in profiles_for(db, tool, name)]


def default_of(rows: List[ToolProfile]) -> ToolProfile:
    return next((r for r in rows if r.is_default), rows[0])


def profile_settings(db: Session, tool: Any, row: ToolProfile) -> Dict[str, Any]:
    """The profile's settings in the tool's current shape (a setting added since gets its default)."""
    try:
        raw = json.loads(row.settings)
    except (ValueError, TypeError):
        raw = {}
    try:
        return tool.normalize_settings(db, raw)
    except ValueError as exc:
        log_warning(LogTags.TOOLS, f"{tool.label} profile '{row.name}' has unusable settings ({exc}); using the defaults")
        return tool.default_settings(db)


def describe_profile(db: Session, tool: Any, row: ToolProfile) -> Dict[str, Any]:
    return {"id": row.id, "name": row.name, "instance": row.instance, "is_default": bool(row.is_default), "settings": profile_settings(db, tool, row)}


def resolve_profile(db: Session, tool: Any, profile_id: Optional[int], instance: Optional[str] = None) -> Tuple[Optional[ToolProfile], Dict[str, Any]]:
    """The named profile and its settings, or the default profile of the list when none is named
    or it is gone. (None, {}) for a tool without settings."""
    if not has_profiles(tool):
        return None, {}
    rows = profiles_for(db, tool, instance)
    row = next((r for r in rows if profile_id is not None and r.id == profile_id), None) or default_of(rows)
    return row, profile_settings(db, tool, row)


def run_settings(db: Session, tool: Any, *, instance: Optional[str] = None, profile_id: Optional[int] = None,
                 chosen: Optional[Dict[str, int]] = None) -> Tuple[Optional[str], Dict[str, Any], List[str]]:
    """What a run gets: (the profile name for the job message, the settings, the named profiles that
    are gone). A tool with one profile list runs `profile_id` or its default. A per-instance tool runs
    the one `instance` with `profile_id` or that instance's default, or every configured instance,
    each with the profile `chosen` names for it or its own default; its settings come back as
    {"instances": {name: settings}}. A profile that is gone falls back to the default of its list."""
    if not has_profiles(tool):
        return None, {}, []
    if not per_instance(tool):
        row, settings = resolve_profile(db, tool, profile_id)
        return row.name, settings, [str(profile_id)] if profile_id is not None and row.id != profile_id else []
    wanted = {instance: profile_id} if instance else {name: (chosen or {}).get(name) for name in tool.instance_names(db)}
    names: Dict[str, str] = {}
    settings: Dict[str, Any] = {"instances": {}}
    gone: List[str] = [str(profile_id)] if profile_id is not None and not instance else []  # a profile goes with its instance
    for name, wanted_id in wanted.items():
        row, one = resolve_profile(db, tool, wanted_id, name)
        if wanted_id is not None and row.id != wanted_id:
            gone.append(f"{wanted_id} ({name})")
        names[name] = row.name
        settings["instances"][name] = one
    distinct = set(names.values())
    label = next(iter(distinct)) if len(distinct) == 1 else ", ".join(f"{name}: {profile}" for name, profile in names.items())
    return label or None, settings, gone


def _checked_name(db: Session, tool: Any, name: Any, exclude_id: Optional[int], instance: Optional[str]) -> str:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Give the profile a name.")
    if len(name) > MAX_NAME:
        raise ValueError(f"Keep the profile name under {MAX_NAME} characters.")
    for other in list_tool_profiles(db, tool.id, instance):
        if other.id != exclude_id and other.name.lower() == name.lower():
            raise ValueError(f"A profile named '{other.name}' already exists.")
    return name


def _checked_settings(db: Session, tool: Any, raw: Any) -> Dict[str, Any]:
    settings = tool.normalize_settings(db, raw)
    problem = tool.settings_problem(settings) if tool.settings_problem else ""
    if problem:
        raise ValueError(problem)
    return settings


def create_profile(db: Session, tool: Any, name: Any, raw_settings: Any = None, instance: Optional[str] = None) -> ToolProfile:
    """A new profile from the given settings (the tool's defaults when None), in the list of
    `instance` for a tool that keeps profiles per instance. Raises ValueError."""
    scope = _scope(db, tool, instance)
    profiles_for(db, tool, scope)  # the Default one exists first, so a new profile is never the only one
    name = _checked_name(db, tool, name, exclude_id=None, instance=scope)
    settings = _checked_settings(db, tool, raw_settings if raw_settings is not None else tool.default_settings(db))
    row = ToolProfile(tool_id=tool.id, instance=scope, name=name, settings=json.dumps(settings), is_default=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _same_list(tool_id: str, instance: Optional[str]):
    """The filter for one profile list: a tool's, or one instance's."""
    return (ToolProfile.tool_id == tool_id, ToolProfile.instance == instance if instance is not None else ToolProfile.instance.is_(None))


def update_profile(db: Session, tool: Any, row: ToolProfile, *, name: Any = None, raw_settings: Any = None,
                   is_default: Optional[bool] = None) -> ToolProfile:
    """Rename, resave or promote a profile within its list. Raises ValueError."""
    new_name = _checked_name(db, tool, name, exclude_id=row.id, instance=row.instance) if name is not None else None
    new_settings = json.dumps(_checked_settings(db, tool, raw_settings)) if raw_settings is not None else None
    if new_name is not None:
        row.name = new_name
    if new_settings is not None:
        row.settings = new_settings
    if is_default:
        db.query(ToolProfile).filter(*_same_list(tool.id, row.instance), ToolProfile.id != row.id).update({ToolProfile.is_default: False})
        row.is_default = True
    db.commit()
    db.refresh(row)
    return row


def delete_profile(db: Session, tool: Any, row: ToolProfile) -> None:
    """Removes a profile; the default passes to the oldest one left in its list. The only profile
    of a list stays (ValueError)."""
    if len(list_tool_profiles(db, tool.id, row.instance)) <= 1:
        raise ValueError("The only profile stays. Make another one first.")
    was_default = bool(row.is_default)
    scope = row.instance
    db.delete(row)
    db.flush()
    if was_default:
        replacement = db.query(ToolProfile).filter(*_same_list(tool.id, scope)).order_by(ToolProfile.id.asc()).first()
        if replacement:
            replacement.is_default = True
    db.commit()


def rekey_arr_instances(db: Session, tool_id: str, renames: Dict[str, str]) -> List[str]:
    """An arr instance was renamed: its profiles follow it (profiles already saved under the new
    name win). Returns the names that moved. Flushes; the caller commits."""
    rows = all_tool_profiles(db, tool_id)
    taken = {row.instance for row in rows}
    moved = set()
    for row in rows:
        target = renames.get(row.instance) if row.instance else None
        if target and target not in taken:
            moved.add(row.instance)
            row.instance = target
    if moved:
        db.flush()
    return sorted(moved)
