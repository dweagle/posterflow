from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def test_no_models_declare_naive_datetime_columns():
    """DateTime(timezone=True) looks aware but is not on SQLite — it comes back naive."""
    offenders = [
        path.name
        for path in sorted(MODELS_DIR.glob("*.py"))
        if "DateTime(timezone=True)" in path.read_text()
    ]

    assert offenders == [], f"Use UTCDateTime instead of DateTime(timezone=True) in: {offenders}"
