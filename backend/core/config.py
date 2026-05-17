from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    # Application
    app_name: str = "Posterflow"
    debug: bool = False
    
    # Paths
    config_dir: Path = Path("/config")   # Database, rclone config, drives cache
    gdrive_dir: Path = Path("/config/posters/gdrive")  # Synced GDrive poster folders (overrideable via DB setting)
    logs_dir: Path = Path("/config/logs")  # Application logs

    
    # Database
    database_url: str = "sqlite:////config/posterflow.db"
    
    # Jobs
    max_concurrent_jobs: int = 1  # Maximum concurrent sync jobs
    job_ws_poll_interval_active: float = 0.2  # seconds when jobs are running/pending
    job_ws_poll_interval_idle: float = 0.75  # seconds when no active jobs

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