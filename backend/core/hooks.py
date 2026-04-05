"""
Post-job script hooks.

Scripts must be placed in /config/scripts/ and be executable.
They are executed with no shell involvement (subprocess.run without shell=True)
to prevent injection. Failure of a hook never fails the parent job.
"""
import json
import os
import subprocess
import traceback
from typing import Optional

from core.config import settings
from core.logging import LogTags, log_info, log_warning, log_error, log_debug

_TAG = LogTags.SCHEDULER

SCRIPTS_DIR = settings.config_dir / "scripts"

SCRIPT_EXECUTION_TIMEOUT = 60  # seconds

# Keys used to identify hook slots in the `post_job_hooks` setting.
HOOK_KEY_SYNC_ONE = "sync_one"
HOOK_KEY_SYNC_ALL = "sync_all"
HOOK_KEY_WORKFLOW = "poster_workflow"
HOOK_KEY_RENAMER = "poster_renamer"
HOOK_KEY_BORDER = "border_replacer"
HOOK_KEY_UNMATCHED = "unmatched"

HOOK_KEYS: list[str] = [
    HOOK_KEY_SYNC_ONE,
    HOOK_KEY_SYNC_ALL,
    HOOK_KEY_WORKFLOW,
    HOOK_KEY_RENAMER,
    HOOK_KEY_BORDER,
    HOOK_KEY_UNMATCHED,
]

HOOK_KEY_LABELS: dict[str, str] = {
    HOOK_KEY_SYNC_ONE: "Sync (Single Drive)",
    HOOK_KEY_SYNC_ALL: "Sync All",
    HOOK_KEY_WORKFLOW: "Poster Workflow",
    HOOK_KEY_RENAMER: "Poster Renamer",
    HOOK_KEY_BORDER: "Border Replacer",
    HOOK_KEY_UNMATCHED: "Unmatched Detection",
}


def list_available_scripts() -> list[str]:
    """Return sorted list of executable filenames found in the scripts directory."""
    if not SCRIPTS_DIR.exists():
        return []
    scripts = []
    for entry in sorted(SCRIPTS_DIR.iterdir()):
        if entry.is_file() and os.access(entry, os.X_OK):
            scripts.append(entry.name)
    return scripts


def _validate_script(filename: str) -> tuple[bool, str]:
    """
    Validate a script filename.
    Returns (is_valid, error_message).
    """
    if not filename:
        return False, "No script filename provided"

    # Reject any path traversal or directory separators
    if os.sep in filename or "/" in filename or ".." in filename:
        return False, f"Invalid script name (path separators not allowed): '{filename}'"

    script_path = SCRIPTS_DIR / filename

    # Resolve and ensure it's still inside SCRIPTS_DIR
    try:
        resolved = script_path.resolve()
        scripts_dir_resolved = SCRIPTS_DIR.resolve()
        if not str(resolved).startswith(str(scripts_dir_resolved) + os.sep) and resolved != scripts_dir_resolved:
            return False, f"Script path escapes scripts directory: '{filename}'"
    except Exception:
        return False, f"Could not resolve script path: '{filename}'"

    if not script_path.exists():
        return False, f"Script not found: '{filename}'"

    if not script_path.is_file():
        return False, f"Not a file: '{filename}'"

    if not os.access(script_path, os.X_OK):
        return False, f"Script is not executable: '{filename}' — run: chmod +x /config/scripts/{filename}"

    return True, ""


def run_post_job_hook(
    hook_key: str,
    success: bool,
    triggered_by: str = "manual",
    db=None,
) -> None:
    """
    Execute the post-job script hook for the given hook key if configured.

    This function is non-fatal — any error is logged but never propagates.

    Args:
        hook_key: One of HOOK_KEYS (e.g. "sync_one", "poster_workflow").
        success: Whether the job completed successfully.
        triggered_by: "manual", "scheduled", or "workflow" (sub-steps of a workflow run).
        db: Optional open DB session. If None, a new session is opened and closed.
    """
    # Sub-module runners inside a workflow pass triggered_by="workflow" so their
    # individual hooks are suppressed — only the single workflow-level hook fires.
    if triggered_by == "workflow":
        return

    session = db
    close_db = False
    if session is None:
        from database import SessionLocal
        session = SessionLocal()
        close_db = True

    try:
        from models.setting import get_setting_value

        raw = get_setting_value(session, "post_job_hooks")
        if not raw:
            return

        try:
            hooks_config = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log_warning(LogTags.MODULE, "post_job_hooks setting contains invalid JSON — skipping hooks")
            return

        hook = hooks_config.get(hook_key)
        if not hook or not isinstance(hook, dict):
            return

        if not hook.get("enabled", False):
            return

        script = str(hook.get("script") or "").strip()
        if not script:
            return

        # Check run_when condition
        run_when = str(hook.get("run_when") or "always").lower()
        if run_when == "scheduled" and triggered_by != "scheduled":
            log_debug(_TAG, f"Skipping hook '{hook_key}': run_when=scheduled but triggered_by={triggered_by}")
            return
        if run_when == "manual" and triggered_by != "manual":
            log_debug(_TAG, f"Skipping hook '{hook_key}': run_when=manual but triggered_by={triggered_by}")
            return

        # Check trigger_on condition
        trigger_on = str(hook.get("trigger_on") or "always").lower()
        if trigger_on == "success" and not success:
            log_debug(_TAG, f"Skipping hook '{hook_key}': trigger_on=success but job failed")
            return
        if trigger_on == "failure" and success:
            log_debug(_TAG, f"Skipping hook '{hook_key}': trigger_on=failure but job succeeded")
            return

        # Validate before executing
        valid, error = _validate_script(script)
        if not valid:
            log_warning(_TAG, f"Hook '{hook_key}' skipped: {error}", hook_key=hook_key, script=script)
            return

        script_path = SCRIPTS_DIR / script
        label = HOOK_KEY_LABELS.get(hook_key, hook_key)
        log_info(
            _TAG,
            f"Running post-job script for '{label}': {script}",
            hook_key=hook_key,
            script=script,
            triggered_by=triggered_by,
        )

        # Minimal, safe environment — no shell, no inherited env
        env = {
            "POSTERFLOW_JOB": hook_key,
            "POSTERFLOW_STATUS": "success" if success else "failure",
            "POSTERFLOW_TRIGGERED_BY": triggered_by,
            "HOME": os.environ.get("HOME", "/root"),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }

        try:
            result = subprocess.run(  # nosec B603 — no shell=True; path validated against /config/scripts/
                [str(script_path)],
                timeout=SCRIPT_EXECUTION_TIMEOUT,
                capture_output=True,
                text=True,
                env=env,
            )
            if result.stdout.strip():
                log_info(_TAG, f"Hook stdout:\n{result.stdout.strip()}", hook_key=hook_key)
            if result.stderr.strip():
                log_warning(_TAG, f"Hook stderr:\n{result.stderr.strip()}", hook_key=hook_key)
            if result.returncode != 0:
                log_warning(
                    _TAG,
                    f"Hook '{script}' exited with code {result.returncode}",
                    hook_key=hook_key,
                    returncode=result.returncode,
                )
            else:
                log_info(_TAG, f"Hook '{script}' completed successfully", hook_key=hook_key)
        except subprocess.TimeoutExpired:
            log_error(
                _TAG,
                f"Hook '{script}' timed out after {SCRIPT_EXECUTION_TIMEOUT}s",
                hook_key=hook_key,
                script=script,
            )
        except Exception as exc:
            log_error(
                _TAG,
                f"Hook '{script}' execution error: {exc}\n{traceback.format_exc()}",
                hook_key=hook_key,
            )

    except Exception as exc:
        # Catch-all: hook errors must never crash the caller
        log_error(_TAG, f"Unexpected error in run_post_job_hook: {exc}\n{traceback.format_exc()}")
    finally:
        if close_db:
            session.close()
