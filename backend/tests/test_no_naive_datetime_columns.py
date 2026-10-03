import re
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# Whitespace-tolerant, and tolerant of the positional spelling: DateTime(True),
# DateTime(timezone = True) and DateTime( timezone=True ) all mean the same
# naive-on-SQLite thing and must not slip past.
NAIVE_DATETIME = re.compile(r"DateTime\s*\(\s*(?:timezone\s*=\s*)?True\s*\)")


def test_no_models_declare_naive_datetime_columns():
    """DateTime(timezone=True) looks aware but is not on SQLite — it comes back naive."""
    offenders = [
        path.name
        for path in sorted(MODELS_DIR.glob("*.py"))
        if NAIVE_DATETIME.search(path.read_text())
    ]

    assert offenders == [], f"Use UTCDateTime instead of DateTime(timezone=True) in: {offenders}"
