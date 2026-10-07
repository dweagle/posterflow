import re
from pathlib import Path

import sqlalchemy as sa

import main
from core.config import settings
from database import Base

BACKEND = Path(__file__).resolve().parent.parent


def test_migrations_run_with_a_percent_sign_in_the_database_path(tmp_path, monkeypatch):
    """Alembic's config treats % as interpolation; an unescaped path used to fail at startup."""
    config_dir = tmp_path / "50% full"
    config_dir.mkdir()
    db_path = config_dir / "posterflow.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")

    main.run_database_migrations()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        assert conn.execute(sa.text("select version_num from alembic_version")).scalar_one()
    engine.dispose()
    assert set(Base.metadata.tables) <= tables


def test_alembic_env_imports_every_model_module():
    """Autogenerate only sees the tables env.py imports; a missing one shows up as a DROP TABLE."""
    env_source = (BACKEND / "alembic" / "env.py").read_text()
    modules = sorted(p.stem for p in (BACKEND / "models").glob("*.py") if p.stem != "__init__")

    missing = [name for name in modules if not re.search(rf"^from models\.{name} import ", env_source, re.M)]

    assert missing == []
