from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from core.config import settings
from core.logging import log_info, LogTags
import logging
from typing import Generator

# Suppress SQLAlchemy query logging to prevent log spam
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

# Create SQLAlchemy engine
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # Needed for SQLite
    echo=False  # Disable SQL query logging (causes log spam)
)

# Enable WAL mode so readers don't block writers and vice versa
@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except Exception:
        # WAL is a persistent DB-level setting; if another connection already
        # set it (or the DB is briefly locked during startup), this is safe to ignore.
        pass
    finally:
        cursor.close()

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

# Dependency for FastAPI endpoints
def get_db() -> Generator[Session, None, None]:
    """
    Database session dependency for FastAPI routes.
    Usage: def my_endpoint(db: Session = Depends(get_db))
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


log_info(LogTags.STARTUP, f"Database configured: {settings.database_url}")