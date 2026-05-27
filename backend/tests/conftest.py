import os
import sys
import tempfile
import unittest.mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

_TEST_ROOT = Path(tempfile.gettempdir()) / "posterflow-tests"
_TEST_CONFIG_DIR = _TEST_ROOT / "config"
_TEST_LOGS_DIR = _TEST_CONFIG_DIR / "logs"
_TEST_GDRIVE_DIR = _TEST_ROOT / "posters" / "gdrive"

os.environ.setdefault("CONFIG_DIR", str(_TEST_CONFIG_DIR))
os.environ.setdefault("GDRIVE_DIR", str(_TEST_GDRIVE_DIR))
os.environ.setdefault("LOGS_DIR", str(_TEST_LOGS_DIR))
os.environ.setdefault("LOG_FILE", str(_TEST_LOGS_DIR / "posterflow.log"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_CONFIG_DIR / 'posterflow-test.db'}")
os.environ.setdefault("POSTERFLOW_TESTING", "true")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from database import Base, get_db

# Import models to register with Base
import models.setting  # noqa: F401
import models.drive  # noqa: F401
import models.poster  # noqa: F401
import models.job  # noqa: F401
import models.idarr  # noqa: F401
import models.schedule  # noqa: F401
import models.plex_upload  # noqa: F401
import models.manual_media  # noqa: F401

# Use the same DB URL that app SessionLocal uses so startup/middleware queries
# and dependency-overridden route sessions share one schema.
TEST_DATABASE_URL = os.environ["DATABASE_URL"]

# Bootstrap schema before importing main/app (which triggers startup logic).
_bootstrap_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=_bootstrap_engine)
_bootstrap_engine.dispose()

# Import app after models
from main import app

@pytest.fixture(scope="session")
def test_engine():
    """Create test engine once for all tests"""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()

@pytest.fixture(scope="function")
def test_db(test_engine):
    """Create a new database session for each test"""
    connection = test_engine.connect()
    transaction = connection.begin()
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestingSessionLocal()
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture(scope="function")
def client(test_db):
    """Create test client with migrations patched out (tables created by Base.metadata.create_all)"""
    def override_get_db():
        try:
            yield test_db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    with unittest.mock.patch("main.run_database_migrations"):
        with TestClient(app) as c:
            yield c
    
    app.dependency_overrides.clear()