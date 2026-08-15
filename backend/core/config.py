import os
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


def _default_config_dir() -> Path:
    """/config when usable (the Docker volume); XDG data dir for native installs."""
    docker_dir = Path("/config")
    if docker_dir.is_dir() and os.access(docker_dir, os.W_OK):
        return docker_dir
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "posterflow"


def running_in_container() -> bool:
    """Docker/Podman detection — lets the UI phrase paths for the install type."""
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


_CONFIG_DIR = _default_config_dir()

# Always-allowed CORS origins (Photopea save-back depends on being listed here)
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:8357",
    "http://127.0.0.1:8357",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://www.photopea.com",
)


class Settings(BaseSettings):
    # Application
    app_name: str = "Posterflow"
    debug: bool = False

    # Paths — config_dir is the root; the others re-derive from it when only CONFIG_DIR is overridden
    config_dir: Path = _CONFIG_DIR   # Database, rclone config, drives cache
    gdrive_dir: Path = _CONFIG_DIR / "posters" / "gdrive"  # Synced GDrive poster folders (overrideable via DB setting)
    artwork_gdrive_dir: Path = _CONFIG_DIR / "artwork" / "gdrive"  # Synced GDrive artwork folders (logos/backgrounds/squareart)
    logs_dir: Path = _CONFIG_DIR / "logs"  # Application logs

    # Server bind — 8357 is Posterflow's port everywhere; the Docker image pins
    # PORT=8000 internally so existing 8357:8000 compose mappings keep working
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8357

    # Database
    database_url: str = f"sqlite:///{_CONFIG_DIR / 'posterflow.db'}"
    
    # Jobs
    max_concurrent_jobs: int = 1  # Maximum concurrent sync jobs
    job_ws_poll_interval_active: float = 0.2  # seconds when jobs are running/pending
    job_ws_poll_interval_idle: float = 0.75  # seconds when no active jobs

    # Rclone / GDrive sync tuning. Batch sync is sequential by design (run_sync_all_job); concurrency comes from rclone_transfers.
    rclone_tps_limit: int = 10  # Max Drive API tps per rclone process; downloads cost 200 quota units, per-user cap ~27/sec
    rclone_pacer_min_sleep: str = "60ms"  # Drive backend pacer; must allow more tps than rclone_tps_limit or it caps us
    rclone_transfers: int = 8  # Concurrent file transfers per rclone process
    rclone_upload_chunk_size: str = "64Mi"  # Buffered in RAM per transfer (64Mi x 4 uploads = 256MiB peak)
    rclone_upload_transfers: int = 4  # Small uploads are latency-bound (~1s each) so throughput tracks this; too high earns 403s rclone absorbs as backoff
    # Upload-only rate ceilings (uploads cost 50 quota units vs 200 for downloads); move both together — tps above 1/pacer_min_sleep is pacer-capped. None = share download values.
    rclone_upload_tps_limit: int | None = None
    rclone_upload_pacer_min_sleep: str | None = None

    # CORS — extra origins appended to the built-in defaults, never replacing them
    cors_origins: str = ""

    # Iframe embedding — comma-separated origins (e.g. an Organizr dashboard) allowed to
    # embed the app; empty keeps the strict X-Frame-Options: SAMEORIGIN default
    allowed_frame_origins: str = ""
    
    # Logging
    log_level: str = "INFO"
    log_file: str = str(_CONFIG_DIR / "logs" / "posterflow.log")
    max_log_size: int = 10 * 1024 * 1024  # 10 MB
    backup_count: int = 1
    
    model_config = SettingsConfigDict(case_sensitive=False)

    @model_validator(mode="after")
    def _rederive_paths(self) -> "Settings":
        """When CONFIG_DIR is overridden, follow it for every path not itself overridden."""
        explicit = self.model_fields_set
        if "config_dir" not in explicit:
            return self
        if "gdrive_dir" not in explicit:
            self.gdrive_dir = self.config_dir / "posters" / "gdrive"
        if "artwork_gdrive_dir" not in explicit:
            self.artwork_gdrive_dir = self.config_dir / "artwork" / "gdrive"
        if "logs_dir" not in explicit:
            self.logs_dir = self.config_dir / "logs"
        if "database_url" not in explicit:
            self.database_url = f"sqlite:///{self.config_dir / 'posterflow.db'}"
        if "log_file" not in explicit:
            self.log_file = str(self.logs_dir / "posterflow.log")
        return self

    def get_cors_origins(self) -> list[str]:
        """Built-in origins plus any CORS_ORIGINS extras, deduped."""
        extras = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        return list(dict.fromkeys([*_DEFAULT_CORS_ORIGINS, *extras]))

# Create settings instance
settings = Settings()

# Ensure directories exist
settings.config_dir.mkdir(parents=True, exist_ok=True)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
# Note: gdrive_dir is created at startup after reading the persisted path setting
(settings.config_dir / "idarr").mkdir(parents=True, exist_ok=True)
(settings.config_dir / "scripts").mkdir(parents=True, exist_ok=True)
(settings.config_dir / "border_overlays").mkdir(parents=True, exist_ok=True)  # User border-frame uploads
(settings.config_dir / "artwork" / "fonts").mkdir(parents=True, exist_ok=True)  # User text-logo font overrides
(settings.config_dir / "artwork" / "art").mkdir(parents=True, exist_ok=True)  # User's own reusable artwork