from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    # Application
    app_name: str = "Posterflow"
    debug: bool = False
    
    # Paths
    config_dir: Path = Path("/config")   # Database, rclone config, drives cache
    gdrive_dir: Path = Path("/config/posters/gdrive")  # Synced GDrive poster folders (overrideable via DB setting)
    artwork_gdrive_dir: Path = Path("/config/artwork/gdrive")  # Synced GDrive artwork folders (logos/backgrounds/squareart)
    logs_dir: Path = Path("/config/logs")  # Application logs

    
    # Database
    database_url: str = "sqlite:////config/posterflow.db"
    
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

    # CORS
    cors_origins: str = "http://localhost:8357,http://127.0.0.1:8357,http://localhost:5173,http://127.0.0.1:5173,https://www.photopea.com"
    
    # Logging
    log_level: str = "INFO"
    log_file: str = "/config/logs/posterflow.log"
    max_log_size: int = 10 * 1024 * 1024  # 10 MB
    backup_count: int = 1
    
    model_config = SettingsConfigDict(case_sensitive=False)

    def get_cors_origins(self) -> list[str]:
        """Return normalized CORS allowlist from comma-separated config."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

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