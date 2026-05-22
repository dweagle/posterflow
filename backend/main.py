from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from core.rate_limiter import limiter
import traceback
from core.logging import setup_logging, LogTags, log_section_start, log_section_end, log_success, log_error, log_warning, log_info, log_debug
from core.config import settings
from core.scheduler import start_scheduler, stop_scheduler
from database import get_db, SessionLocal
from api.drives import router as drives_router, load_drives_from_json
from api.setup import router as setup_router
from api.settings import router as settings_router
from api.auth import router as auth_router
from core.auth import AppAuthMiddleware
from api.test import router as test_router
from api.logs import router as logs_router
from api.jobs import router as jobs_router
from api.job_logs import router as job_logs_router
from api.schedules import router as schedules_router
from api.poster_manager import router as poster_manager_router
from api.plex_upload import router as plex_upload_router
from api.backup import router as backup_router
from api.database import router as database_router
from api.idarr import router as idarr_router
from api.maker_tools import router as maker_tools_router
from api.stats import router as stats_router
from api.scripts import router as scripts_router
from api.community import router as community_router
from services.drive_loader import load_drives_data
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from alembic.config import Config
from alembic import command
import ctypes
import os
import re
import setproctitle
import subprocess  # nosec B404 - used only for hardcoded git commands in version detection
from urllib.request import urlopen

# Set up logging
setup_logging()

def _load_app_base_version() -> str:
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        value = version_file.read_text(encoding="utf-8").strip()
        return value or "0.1.0"
    except Exception:
        return "0.1.0"


APP_BASE_VERSION = _load_app_base_version()


def get_app_version() -> str:
    """Get app version with optional branch suffix."""
    ci_branch = os.getenv("BRANCH")
    if ci_branch:
        return f"{APP_BASE_VERSION}.{ci_branch}"

    try:
        branch = (
            subprocess.check_output(  # nosec B603 B607 - hardcoded git args, no user input
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=Path(__file__).parent,
            )
            .decode()
            .strip()
        )
        return f"{APP_BASE_VERSION}.{branch}"
    except Exception:
        return APP_BASE_VERSION


GITHUB_OWNER = "dweagle"
GITHUB_REPO_NAME = "posterflow"


def _get_remote_owner_repo() -> tuple[str, str]:
    """Return the GitHub owner/repo, falling back to the hardcoded default."""
    try:
        remote = (
            subprocess.check_output(  # nosec B603 B607 - hardcoded git args, no user input
                ["git", "config", "--get", "remote.origin.url"],
                stderr=subprocess.DEVNULL,
                cwd=Path(__file__).parent,
            )
            .decode()
            .strip()
        )
        ssh_match = re.match(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?$", remote)
        https_match = re.match(r"https?://github\.com/([^/]+)/(.+?)(?:\.git)?$", remote)
        if ssh_match:
            return ssh_match.group(1), ssh_match.group(2)
        if https_match:
            return https_match.group(1), https_match.group(2)
    except Exception:  # nosec B110
        pass

    return GITHUB_OWNER, GITHUB_REPO_NAME


def _get_missed_releases(owner: str, repo: str, since_version: str, limit: int = 10) -> list[dict[str, str]]:
    """Return up to `limit` releases newer than since_version, ordered newest-first."""
    import json
    results: list[dict[str, str]] = []
    base_semver = _parse_semver(since_version)
    page = 1
    while page <= 3 and len(results) < limit:  # max 3 pages (60 releases) as a safety cap
        url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=20&page={page}"
        try:
            with urlopen(url, timeout=6) as response:  # nosec B310
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            break
        if not data:
            break
        found_older = False
        for release in data:
            tag = release.get("tag_name", "").lstrip("v")
            if not tag:
                continue
            if _parse_semver(tag) > base_semver:
                results.append({"version": tag, "notes": release.get("body") or ""})
                if len(results) >= limit:
                    break
            else:
                found_older = True
                break
        if found_older or len(results) >= limit:
            break
        page += 1
    return results


def _parse_semver(version: str) -> tuple[int, ...]:
    """Parse a semantic version string into a comparable tuple."""
    try:
        return tuple(int(x) for x in version.lstrip("v").split(".")[:3])
    except (ValueError, AttributeError):
        return (0, 0, 0)


def get_version_update_status() -> dict[str, Any]:
    local_full = get_app_version()
    status: dict[str, Any] = {
        "current_version": local_full,
        "latest_version": None,
        "update_available": False,
        "versions_behind": 0,
        "releases_url": None,
        "release_notes": None,
        "repo": None,
    }

    owner, repo = _get_remote_owner_repo()
    status["repo"] = f"{owner}/{repo}"
    status["releases_url"] = f"https://github.com/{owner}/{repo}/releases"

    missed = _get_missed_releases(owner, repo, APP_BASE_VERSION)
    if not missed:
        return status

    latest_tag = missed[0]["version"]  # list is newest-first
    status["latest_version"] = latest_tag

    local_semver = _parse_semver(APP_BASE_VERSION)
    latest_semver = _parse_semver(latest_tag)
    status["update_available"] = latest_semver > local_semver

    if status["update_available"]:
        status["versions_behind"] = len(missed)
        if len(missed) == 1:
            status["release_notes"] = missed[0]["notes"] or None
        else:
            parts: list[str] = []
            for r in missed:
                parts.append(f"### v{r['version']}")
                if r["notes"].strip():
                    parts.append(r["notes"].strip())
            status["release_notes"] = "\n\n".join(parts) or None

    return status


def run_database_migrations() -> None:
    """Apply Alembic migrations to head before app startup tasks."""
    alembic_ini = Path(__file__).parent / "alembic.ini"
    if not alembic_ini.exists():
        raise RuntimeError(f"Alembic configuration file not found: {alembic_ini}")

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


def set_process_name(name: str) -> None:
    """Set the process title for resource monitors.

    - argv[0] via setproctitle (htop, GNOME System Monitor, ps aux)
    - kernel comm name via prctl PR_SET_NAME (/proc/self/comm, max 15 chars)
    """
    setproctitle.setproctitle(name)
    try:
        libc = ctypes.CDLL("libc.so.6")
        result = libc.prctl(15, ctypes.c_char_p(name.encode("utf-8")[:15]), 0, 0, 0)
        if result != 0:
            log_debug(LogTags.STARTUP, f"prctl returned non-zero setting process name to '{name}'")
    except Exception as e:
        log_debug(LogTags.STARTUP, f"prctl process name skipped: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    is_testing = os.getenv("POSTERFLOW_TESTING", "").strip().lower() in {"1", "true", "yes", "on"}

    set_process_name("posterflow")

    # Startup
    log_section_start(LogTags.STARTUP, "Posterflow API Starting")
    try:
        run_database_migrations()
        log_success(LogTags.STARTUP, "Database migrations applied")
    except Exception as e:
        log_error(LogTags.STARTUP, f"Database migration failed: {e}\n{traceback.format_exc()}")
        raise

    if is_testing:
        log_info(LogTags.STARTUP, "Testing mode enabled - skipping startup side effects")
    else:
        # Restore persisted gdrive storage path (if configured)
        try:
            from models.setting import get_setting as _get_setting
            _storage_db = SessionLocal()
            try:
                storage_setting = _get_setting(_storage_db, "gdrive_storage_path")
                if storage_setting and storage_setting.value and storage_setting.value.strip():
                    custom_path = Path(storage_setting.value.strip())
                    settings.gdrive_dir = custom_path
                    log_info(LogTags.STARTUP, f"Using custom GDrive storage path: {custom_path}")
            finally:
                _storage_db.close()
        except Exception as e:
            log_warning(LogTags.STARTUP, f"Could not restore gdrive storage path setting: {e}")

        # Restore persisted debug toggle (if configured)
        try:
            from models.setting import get_setting
            persisted_db = SessionLocal()
            try:
                debug_setting = get_setting(persisted_db, "debug_enabled")
                if debug_setting and debug_setting.value is not None:
                    persisted_debug = debug_setting.value.strip().lower() == "true"
                    if settings.debug != persisted_debug:
                        settings.debug = persisted_debug
                        setup_logging(debug_enabled=persisted_debug)
                        log_info(LogTags.STARTUP, f"Restored persisted debug mode: {'enabled' if persisted_debug else 'disabled'}")
            finally:
                persisted_db.close()
        except Exception as e:
            log_warning(LogTags.STARTUP, f"Could not restore persisted debug setting: {e}")

        # Clean up stale jobs from previous runs
        try:
            from models.job import Job, JOB_STATUS_FAILED, JOB_STATUSES_ACTIVE, JOB_TYPE_IDARR, prune_job_history
            from datetime import datetime, timezone

            db = SessionLocal()
            try:
                stale_jobs = db.query(Job).filter(
                    Job.status.in_(JOB_STATUSES_ACTIVE)
                ).all()

                if stale_jobs:
                    log_info(LogTags.STARTUP, f"Cleaning up {len(stale_jobs)} stale job(s)...")
                    for job in stale_jobs:
                        job.status = JOB_STATUS_FAILED
                        if job.job_type == JOB_TYPE_IDARR:
                            job.error = 'Stale IDarr job after application shutdown; job was not resumed'
                        else:
                            job.error = 'Job interrupted by application restart'
                        job.completed_at = datetime.now(timezone.utc)
                        log_debug(LogTags.STARTUP, f"Marked job {job.id} ({job.job_type}) as failed")
                    db.commit()
                    prune_job_history(db)
                    log_success(LogTags.STARTUP, f"Cleaned up {len(stale_jobs)} stale job(s)")
                else:
                    log_info(LogTags.STARTUP, "No stale jobs to clean up")
            finally:
                db.close()
        except Exception as e:
            log_error(LogTags.STARTUP, f"Failed to clean up stale jobs: {e}\\n{traceback.format_exc()}")

        # Load drives from hybrid source (remote with local fallback)
        try:
            log_info(LogTags.STARTUP, "Loading drives configuration...")
            drives_data = load_drives_data()

            db = next(get_db())
            try:
                result = load_drives_from_json(db, drives_data)
                if result.get("success", True):
                    log_success(LogTags.STARTUP, f"Drives synced: {result.get('added', 0)} added, {result.get('updated', 0)} updated, {result.get('deprecated', 0)} deprecated, {result.get('reactivated', 0)} reactivated")
                else:
                    log_warning(LogTags.STARTUP, f"Drive loading issues: {result}")
            finally:
                db.close()

        except Exception as e:
            log_error(LogTags.STARTUP, f"Failed to load drives: {e}\\n{traceback.format_exc()}")
            log_warning(LogTags.STARTUP, "Application will continue, but drives may not be available")

        # Start APScheduler for background jobs
        try:
            log_info(LogTags.STARTUP, "Starting scheduler...")
            start_scheduler()
            log_success(LogTags.STARTUP, "Scheduler started successfully")
        except Exception as e:
            log_error(LogTags.STARTUP, f"Failed to start scheduler: {e}\\n{traceback.format_exc()}")
            log_warning(LogTags.STARTUP, "Scheduled jobs will not run")
    
    log_section_end(LogTags.STARTUP, "Posterflow API Ready")

    try:
        yield
    finally:
        # Shutdown
        log_section_start(LogTags.SHUTDOWN, "Posterflow API Shutting Down")

        if is_testing:
            log_info(LogTags.SHUTDOWN, "Testing mode enabled - skipping shutdown side effects")
            log_section_end(LogTags.SHUTDOWN, "Shutdown Complete")
        else:
            # Signal WebSocket polling loops to exit cleanly before uvicorn
            # cancels them with a CancelledError.
            try:
                from core.websocket import shutdown_event
                shutdown_event.set()
            except Exception as e:
                log_error(LogTags.SHUTDOWN, f"Error signaling WebSocket shutdown: {e}")

            # Close all active WebSocket connections
            try:
                from api.jobs import _ws as job_ws
                from api.logs import _ws as log_ws
                
                job_connections = job_ws.active_connections
                log_connections = log_ws.active_connections
                total_connections = len(job_connections) + len(log_connections)
                if total_connections > 0:
                    log_info(LogTags.SHUTDOWN, f"Closing {total_connections} WebSocket connection(s)...")
                    
                    # Close job WebSocket connections
                    for ws in list(job_connections):
                        try:
                            await ws.close()
                        except Exception as e:
                            log_debug(LogTags.WEBSOCKET, f"Job WS already closed during shutdown: {e}")
                    job_connections.clear()
                    
                    # Close log WebSocket connections
                    for ws in list(log_connections):
                        try:
                            await ws.close()
                        except Exception as e:
                            log_debug(LogTags.WEBSOCKET, f"Log WS already closed during shutdown: {e}")
                    log_connections.clear()
                    
                    log_success(LogTags.SHUTDOWN, "WebSocket connections closed")
            except Exception as e:
                log_error(LogTags.SHUTDOWN, f"Error closing WebSocket connections: {e}\\n{traceback.format_exc()}")
            
            # Stop scheduler
            try:
                stop_scheduler()
                log_success(LogTags.SHUTDOWN, "Scheduler stopped")
            except Exception as e:
                log_error(LogTags.SHUTDOWN, f"Error stopping scheduler: {e}\\n{traceback.format_exc()}")

            # Stop background job queue and cancel queued futures so stale jobs never start after shutdown
            try:
                from core.job_queue import job_queue
                job_queue.shutdown(wait=False, cancel_futures=True)
                log_success(LogTags.SHUTDOWN, "Job queue stopped")
            except Exception as e:
                log_error(LogTags.SHUTDOWN, f"Error stopping job queue: {e}\\n{traceback.format_exc()}")
            
            log_section_end(LogTags.SHUTDOWN, "Shutdown Complete")

# Create FastAPI app
app = FastAPI(
    title="Posterflow",
    description="Automated poster management for media servers",
    version=APP_BASE_VERSION,
    lifespan=lifespan,
)

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# App password authentication middleware (no-op when no password is set)
app.add_middleware(AppAuthMiddleware)

# Rate limiting middleware and error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def allow_private_network_access(request: Request, call_next):
    """Add the Chrome Private Network Access header to every response.

    When Photopea (https://www.photopea.com) saves a file back to a LAN address
    (e.g. 192.168.x.x or localhost), Chrome sends an OPTIONS preflight with
    Access-Control-Request-Private-Network: true. The server must echo back
    Access-Control-Allow-Private-Network: true or the browser blocks the request.
    This middleware runs outermost so it can annotate responses from CORS too.
    """
    response = await call_next(request)
    if request.headers.get("Access-Control-Request-Private-Network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


# Include routers
app.include_router(auth_router)
app.include_router(drives_router)
app.include_router(setup_router)
app.include_router(settings_router)
app.include_router(test_router)
app.include_router(logs_router)
app.include_router(jobs_router)
app.include_router(job_logs_router)
app.include_router(schedules_router)
app.include_router(poster_manager_router)
app.include_router(plex_upload_router)
app.include_router(backup_router)
app.include_router(database_router)
app.include_router(idarr_router)
app.include_router(maker_tools_router)
app.include_router(stats_router)
app.include_router(scripts_router)
app.include_router(community_router)

# API health check (must be before catch-all frontend route)
@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/api/version", response_class=PlainTextResponse)
async def version_check() -> str:
    return get_app_version()


@app.get("/api/version/update")
async def version_update_check() -> dict[str, Any]:
    return get_version_update_status()

# Check if frontend static files exist
frontend_dist = Path(__file__).parent / "frontend" / "dist"
if frontend_dist.exists():
    # Mount static files (CSS, JS, images, etc.)
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")
    
    # Serve index.html for all non-API routes (SPA routing)
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str) -> Any:
        # Don't catch API routes
        if full_path.startswith("api/"):
            return {"error": "Not found"}
        
        # If requesting a file with extension and it exists, serve it
        if "." in full_path:
            file_path = frontend_dist / full_path
            # Prevent path traversal outside the frontend dist directory
            if not file_path.resolve().is_relative_to(frontend_dist.resolve()):
                return PlainTextResponse("Not found", status_code=404)
            if file_path.exists():
                return FileResponse(file_path)
        
        # Otherwise serve index.html (for SPA routing)
        return FileResponse(frontend_dist / "index.html")
else:
    log_warning(LogTags.STARTUP, "Frontend dist folder not found - API-only mode")
    
    # Health check endpoint
    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "app": "Posterflow",
            "version": get_app_version(),
            "status": "running"
        }

if __name__ == "__main__":
    import asyncio
    import uvicorn
    from core.websocket import shutdown_event as _ws_shutdown_event

    class _ShutdownAwareServer(uvicorn.Server):
        """Signals the WebSocket shutdown event the moment SIGTERM/SIGINT fires,
        so polling loops exit cleanly before uvicorn's graceful-shutdown timeout."""

        def handle_exit(self, sig: int, frame: object) -> None:  # type: ignore[override]
            _ws_shutdown_event.set()
            super().handle_exit(sig, frame)

    _config = uvicorn.Config(
        app,
        host="0.0.0.0",  # nosec B104
        port=8000,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=3,
    )
    asyncio.run(_ShutdownAwareServer(_config).serve())