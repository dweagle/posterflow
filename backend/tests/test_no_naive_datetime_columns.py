import re
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
PLAIN_DATETIME = re.compile(r"(?<!UTC)DateTime\b")


def test_models_use_utc_datetime_columns():
    """Plain DateTime columns come back naive on SQLite; every model column must be UTCDateTime."""
    offenders = [
        path.name
        for path in sorted(MODELS_DIR.glob("*.py"))
        if PLAIN_DATETIME.search(path.read_text())
    ]

    assert offenders == [], f"Use UTCDateTime instead of DateTime in: {offenders}"
