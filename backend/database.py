from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from core.config import settings
from core.logging import log_info, LogTags
import logging
from typing import Generator

# Suppress SQLAlchemy query logging to prevent log spam
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

# Create SQLAlchemy engine (NullPool avoids connection pool exhaustion with SQLite)
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # Needed for SQLite
    echo=False,  # Disable SQL query logging (causes log spam)
    poolclass=NullPool,
)

# Per-connection SQLite pragmas: WAL (readers don't block writers) and a 5s
# busy_timeout (wait for a write lock instead of erroring with "database is locked").
@event.listens_for(engine, "connect")
def set_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")  # ms; per-connection, so set on every connect
    except Exception:  # nosec B110
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
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


log_info(LogTags.STARTUP, f"Database configured: {settings.database_url}")