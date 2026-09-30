# UTC Storage and User-Facing Timezone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store every timestamp as aware UTC at the type level, and give schedules and user-facing day boundaries one explicitly configured application timezone that the browser no longer has to guess.

**Architecture:** Split the single `TZ` env var into two independent concepts. Process `TZ` stays UTC and governs storage and log wall-clock. A new instance-wide "application timezone" — `APP_TIMEZONE` env var, overridable by a `timezone` DB setting — governs cron interpretation and what "today" means. A `UTCDateTime` SQLAlchemy `TypeDecorator` makes "naive in SQLite means UTC" a type-level guarantee rather than a hand-maintained convention at six call sites.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 (SQLite), Alembic, APScheduler 3.11, loguru, `zoneinfo` (stdlib), pytest. Frontend: React 19, TypeScript, Vite, vitest.

**Spec:** `docs/superpowers/specs/2026-09-30-timezone-handling-design.md`

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `backend/util/utc_datetime.py` | `UTCDateTime` TypeDecorator — the only place naive/aware conversion happens |
| `backend/core/app_timezone.py` | Resolution + caching of the app timezone, and the day-boundary helpers |
| `backend/tests/test_utc_datetime.py` | Decorator contract tests |
| `backend/tests/test_app_timezone.py` | Resolution order, invalid input, day bounds |
| `backend/tests/test_no_naive_datetime_columns.py` | Guard against the invariant drifting back |
| `frontend/src/utils/datetime.ts` | Shared date formatting + timezone introspection |
| `frontend/tests/utils/datetime.test.ts` | `timeZoneOptions` fallback contract |

**Modified files:**

| Path | Change |
|---|---|
| `backend/models/*.py` (13 files) | `DateTime(timezone=True)` → `UTCDateTime` (35 columns) |
| `backend/core/config.py` | `Settings.app_timezone` field |
| `backend/core/scheduler.py` | Scheduler uses app tz; add `apply_app_timezone()` |
| `backend/core/logging.py` | `!UTC` loguru format; UTC debug timestamps |
| `backend/core/log_stream.py` | Convert `record["time"]` to UTC before strftime |
| `backend/main.py` | Restore `timezone` setting at startup |
| `backend/api/settings.py` | Allowlist key, `effective_timezone` read, rebuild on write |
| `backend/api/drives.py` | Delete redundant field serializer |
| `backend/api/artwork_drives.py` | Delete redundant field serializer |
| `backend/api/poster_reminders.py` | Delete `_iso_utc` |
| `backend/api/stats.py` | Day bounds via app tz |
| `backend/api/maker_tools.py` | `_monitor_today_local` → `_monitor_today` |
| `backend/services/border_replacer.py` | Holiday window uses app tz |
| `frontend/src/hooks/useSettingsCore.ts` | Timezone state + save handler |
| `frontend/src/components/settings/SettingsSchedulingSection.tsx` | Timezone picker |
| `frontend/src/components/settings/ScheduleEditModal.tsx` | Zone labelling |
| `frontend/src/pages/Settings.tsx` | Wire the scheduling timezone section |
| `frontend/src/pages/Dashboard.tsx` | Use shared formatter |
| `README.md`, `docs/native-install.md`, `CHANGELOG.md` | Docs |

**Not needed:** no Alembic migration (the rendered DDL is unchanged), no new dependency, no new endpoint.

---

## Task 1: The `UTCDateTime` type

**Files:**
- Create: `backend/util/utc_datetime.py`
- Create: `backend/tests/test_utc_datetime.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_utc_datetime.py`:

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Integer, create_engine, select
from sqlalchemy.orm import Session, declarative_base
from sqlalchemy.pool import StaticPool

from util.utc_datetime import UTCDateTime

Base = declarative_base()


class Sample(Base):
    __tablename__ = "sample_utc_datetime"

    id = Column(Integer, primary_key=True)
    stamp = Column(UTCDateTime, nullable=True)


@pytest.fixture
def session():
    # StaticPool keeps one connection alive so the in-memory DB survives the session.
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(bind=engine)


def test_aware_utc_round_trips_aware(session):
    session.add(Sample(stamp=datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)))
    session.commit()

    stored = session.execute(select(Sample)).scalar_one()

    assert stored.stamp == datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)
    assert stored.stamp.tzinfo is not None


def test_aware_non_utc_is_normalised_to_utc(session):
    """14:30 Amsterdam is 12:30 UTC. Before UTCDateTime this stored 14:30 raw — a silent 2h skew."""
    session.add(Sample(stamp=datetime(2026, 9, 30, 14, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))))
    session.commit()

    stored = session.execute(select(Sample)).scalar_one()

    assert stored.stamp == datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc)


def test_naive_bind_is_treated_as_utc(session):
    """Every existing row in a live install is naive UTC; binds must keep meaning the same."""
    session.add(Sample(stamp=datetime(2026, 9, 30, 14, 30)))
    session.commit()

    stored = session.execute(select(Sample)).scalar_one()

    assert stored.stamp == datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)


def test_none_round_trips_as_none(session):
    session.add(Sample(stamp=None))
    session.commit()

    assert session.execute(select(Sample)).scalar_one().stamp is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```bash
python -m pytest -q tests/test_utc_datetime.py
```
Expected: FAIL with `ModuleNotFoundError: No module named 'util.utc_datetime'`

- [ ] **Step 3: Write the minimal implementation**

Create `backend/util/utc_datetime.py`:

```python
"""UTC-aware DateTime column type.

SQLite has no TIMESTAMPTZ: SQLAlchemy renders DateTime(timezone=True) as a plain
DATETIME, so values come back naive and every reader has to guess. Historically
that guess lived in six hand-written `.replace(tzinfo=timezone.utc)` patches and
one place that guessed wrong. This type settles it once, at the column.

Binds are converted to UTC before storage; reads always come back aware. A naive
bind is read as UTC, which is what every already-persisted value means.
"""

from datetime import timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest -q tests/test_utc_datetime.py`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/util/utc_datetime.py backend/tests/test_utc_datetime.py
git commit -m "feat(datetime): add UTCDateTime column type"
```

---

## Task 2: Adopt `UTCDateTime` across the models

**Files:**
- Modify: `backend/models/artwork.py`, `artwork_drive.py`, `idarr.py`, `job.py`, `drive.py`, `manual_media.py`, `plex_upload.py`, `poster.py`, `poster_override.py`, `poster_reminder.py`, `schedule.py`, `setting.py`, `workflow.py`
- Create: `backend/tests/test_no_naive_datetime_columns.py`

- [ ] **Step 1: Write the failing guard test**

Create `backend/tests/test_no_naive_datetime_columns.py`:

```python
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
```

- [ ] **Step 2: Run the guard test to verify it fails**

Run: `python -m pytest -q tests/test_no_naive_datetime_columns.py`
Expected: FAIL listing the 13 offending model files

- [ ] **Step 3: Swap the column type**

From the repo root:
```bash
cd backend
for f in $(rg -l 'DateTime\(timezone=True\)' models/); do
  sed -i '' 's/DateTime(timezone=True)/UTCDateTime/g' "$f"
done
```

- [ ] **Step 4: Add the import and drop the now-unused `DateTime`**

Run the import cleanup **before** adding the new import — once `UTCDateTime` is present, a
`DateTime, ` pattern would also match inside it and corrupt the name.

From `backend/`:
```bash
for f in $(rg -l 'UTCDateTime' models/); do
  sed -i '' -e '/^from sqlalchemy import/ s/DateTime, //' -e '/^from sqlalchemy import/ s/, DateTime//' "$f"
  sed -i '' '/^from sqlalchemy import/a\
from util.utc_datetime import UTCDateTime
' "$f"
done
```

Verify no stray blank lines or misplaced imports were introduced:
```bash
rg -n '^from util.utc_datetime import UTCDateTime$' models/ | wc -l
```
Expected: `13`

- [ ] **Step 5: Verify the import cleanup**

Run: `python -m pyflakes models/`
Expected: no `imported but unused` entries naming `DateTime`. Other pre-existing warnings are
out of scope.

- [ ] **Step 6: Run the guard test to verify it passes**

Run: `python -m pytest -q tests/test_no_naive_datetime_columns.py`
Expected: PASS

- [ ] **Step 7: Run the full backend suite**

Run: `python -m pytest -q tests/`
Expected: all tests pass. A failure here means some code was relying on naive reads — investigate
before continuing; do not weaken the test.

- [ ] **Step 8: Commit**

```bash
git add backend/models backend/tests/test_no_naive_datetime_columns.py
git commit -m "refactor(models): use UTCDateTime for all timestamp columns"
```

---

## Task 3: Delete the redundant UTC patches

**Files:**
- Modify: `backend/api/drives.py:170-179`
- Modify: `backend/api/artwork_drives.py:71-73`
- Modify: `backend/api/poster_reminders.py:46-50`, `:53-68`
- Modify: `backend/api/idarr.py:1459-1460`

- [ ] **Step 1: Remove the drives field serializer**

In `backend/api/drives.py`, delete the whole `serialize_datetime` serializer (the
`@field_serializer('last_synced', 'last_rename_processed')` decorator and the method beneath it).
Leave `model_config = ConfigDict(from_attributes=True)` in place. If `timezone` is now unused in
that file's imports, remove it from the `datetime` import.

- [ ] **Step 2: Remove the artwork drives field serializer**

Apply the identical change to `backend/api/artwork_drives.py`. Prune the `timezone` import if
unused.

- [ ] **Step 3: Remove `_iso_utc`**

In `backend/api/poster_reminders.py`, delete the `_iso_utc` helper and replace every call site with
a plain `.isoformat()`:

```python
"created_at": reminder.created_at.isoformat() if reminder.created_at else None,
"updated_at": reminder.updated_at.isoformat() if reminder.updated_at else None,
```

Preserve whatever key names and surrounding dict structure the call sites already use — change only
the value expression. Remove `timezone` from the `datetime` import if it becomes unused.

- [ ] **Step 4: Confirm the idarr timestamp call is now safe**

Read `backend/api/idarr.py` around lines 1455-1465 and confirm the `row.updated_at.timestamp()`
call now receives an aware value. Add no code — the guard is that `updated_at` is a `UTCDateTime`
column (it is, in `models/idarr.py`). Leave the call as-is.

- [ ] **Step 5: Verify nothing else hand-patches UTC onto a DB value**

Run: `rg -n 'replace\(tzinfo=timezone\.utc\)' api/ services/ modules/ models/`
Expected: only `backend/services/idarr_runner.py` and `backend/services/new_drives.py` may match —
those coerce ISO-8601 *strings* from external sources, which is correct and unrelated. Any match
operating on an ORM attribute must be removed.

- [ ] **Step 6: Run the backend suite**

Run: `python -m pytest -q tests/`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add backend/api
git commit -m "refactor(api): drop UTC patches made redundant by UTCDateTime"
```

---

## Task 4: The application timezone module

**Files:**
- Create: `backend/core/app_timezone.py`
- Create: `backend/tests/test_app_timezone.py`
- Modify: `backend/core/config.py` (add `app_timezone` to `Settings`)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_app_timezone.py`:

```python
import importlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def app_tz():
    """A freshly imported module, so each test starts from a clean cache."""
    import core.app_timezone as module

    return importlib.reload(module)


def test_env_var_is_used_when_no_db_setting(app_tz, monkeypatch):
    monkeypatch.setattr(app_tz.settings, "app_timezone", "Europe/Amsterdam", raising=False)

    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_set_app_timezone_wins_and_is_cached(app_tz):
    assert app_tz.set_app_timezone("Europe/Amsterdam") is True
    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"

    app_tz._cached = None
    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_invalid_zone_warns_and_keeps_previous(app_tz):
    assert app_tz.set_app_timezone("Europe/Amsterdam") is True

    assert app_tz.set_app_timezone("Not/AZone") is False
    assert str(app_tz.get_app_timezone()) == "Europe/Amsterdam"


def test_empty_value_falls_back_to_host_lookup(app_tz, monkeypatch):
    monkeypatch.setattr(app_tz, "_host_timezone", lambda: ZoneInfo("UTC"))

    assert app_tz.set_app_timezone("") is True
    assert str(app_tz.get_app_timezone()) == "UTC"


def test_day_bounds_utc_spans_the_local_calendar_day():
    """23:30 UTC on the 30th is already the 1st in Amsterdam; the window must follow."""
    from core.app_timezone import day_bounds_utc

    now = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)

    start, end = day_bounds_utc(now, ZoneInfo("Europe/Amsterdam"))

    assert start == datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def test_day_bounds_utc_is_utc_when_asked():
    from core.app_timezone import day_bounds_utc

    now = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)

    start, end = day_bounds_utc(now, timezone.utc)

    assert start == datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def test_day_window_is_24h_across_a_dst_change():
    """A DST transition makes the local day 23 or 25 hours long — the bounds must reflect that."""
    from core.app_timezone import day_bounds_utc

    # Europe/Amsterdam springs forward on 2026-03-29.
    start, end = day_bounds_utc(datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc), ZoneInfo("Europe/Amsterdam"))

    assert end - start == timedelta(hours=23)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest -q tests/test_app_timezone.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.app_timezone'`

- [ ] **Step 3: Add the config field**

In `backend/core/config.py`, add to the `Settings` class, immediately after the `debug` field:

```python
    # Application timezone — what a schedule's "14:30" means and what "today" means to the
    # user. Distinct from the process TZ, which stays UTC so storage is timezone-independent.
    # No env_prefix is configured, so this reads from APP_TIMEZONE.
    app_timezone: str = ""
```

- [ ] **Step 4: Write the implementation**

Create `backend/core/app_timezone.py`:

```python
"""The application timezone: what schedule times and calendar days mean to the user.

Process TZ stays UTC so storage and logs are timezone-independent. This module owns
the other half — the zone a user types into a schedule and the zone that decides
where "today" starts. It resolves once at import (env var or host lookup) because the
database is not reachable that early; the lifespan overrides it from the persisted
setting before the scheduler starts.

Keep this module free of scheduler imports — core.scheduler imports this, and a
reverse import would be circular. Callers that need the scheduler re-pointed call
core.scheduler.apply_app_timezone().
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import settings
from core.logging import LogTags, log_warning

_cached: Optional[ZoneInfo] = None


def _host_timezone() -> ZoneInfo:
    """Fall back to the host's zone, then UTC. Mirrors the scheduler's tzlocal lookup."""
    from tzlocal import get_localzone_name

    name = get_localzone_name()
    if name:
        return ZoneInfo(name)
    log_warning(
        LogTags.SCHEDULER,
        "No application timezone configured and the host has no zone name; using UTC. "
        "Set the timezone in Settings → Scheduling.",
    )
    return ZoneInfo("UTC")


def _resolve() -> ZoneInfo:
    name = (settings.app_timezone or "").strip()
    return ZoneInfo(name) if name else _host_timezone()


def get_app_timezone() -> ZoneInfo:
    """The resolved application timezone. Cached after the first call."""
    global _cached
    if _cached is None:
        try:
            _cached = _resolve()
        except (ZoneInfoNotFoundError, ValueError) as e:
            log_warning(LogTags.SCHEDULER, f"Invalid application timezone {settings.app_timezone!r} ({e}); using UTC")
            _cached = ZoneInfo("UTC")
    return _cached


def set_app_timezone(name: str) -> bool:
    """Point the application at a new zone. Returns False and keeps the old one if invalid."""
    global _cached
    name = (name or "").strip()
    if not name:
        _cached = _host_timezone()
        return True
    try:
        _cached = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log_warning(LogTags.SCHEDULER, f"Ignoring invalid application timezone {name!r}")
        return False
    return True


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_in_app_tz() -> datetime:
    return datetime.now(get_app_timezone())


def today_in_app_tz() -> date:
    return now_in_app_tz().date()


def day_bounds_utc(now: datetime, tz) -> tuple[datetime, datetime]:
    """[start of `now`'s calendar day in `tz`, start of the next day), as aware UTC.

    The window is not always 24h — across a DST transition the local day is 23 or 25
    hours. Deriving both ends from the same local date handles that for free.
    """
    local_day = now.astimezone(tz).date()
    start = datetime.combine(local_day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_app_timezone.py`
Expected: PASS, 7 passed

- [ ] **Step 6: Run the backend suite**

Run: `python -m pytest -q tests/`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add backend/core/app_timezone.py backend/core/config.py backend/tests/test_app_timezone.py
git commit -m "feat(timezone): add application timezone resolution and day bounds"
```

---

## Task 5: Point the scheduler at the application timezone

**Files:**
- Modify: `backend/core/scheduler.py:43-44`, `:72`, add `apply_app_timezone()` after `:73`
- Modify: `backend/tests/test_schedules.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_schedules.py`:

```python
def test_scheduler_uses_the_application_timezone_not_process_tz(monkeypatch):
    """TZ governs storage; the app timezone governs when a cron fires. They must not be the same knob."""
    import core.scheduler as scheduler_module
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(scheduler_module, "get_app_timezone", lambda: ZoneInfo("Europe/Amsterdam"))

    scheduler_module.apply_app_timezone()

    assert str(scheduler_module.scheduler.timezone) == "Europe/Amsterdam"


def test_apply_app_timezone_picks_up_a_later_change(monkeypatch):
    """Changing the setting at runtime must re-point the scheduler without a restart."""
    import core.scheduler as scheduler_module
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(scheduler_module, "get_app_timezone", lambda: ZoneInfo("Asia/Tokyo"))

    scheduler_module.apply_app_timezone()

    assert str(scheduler_module.scheduler.timezone) == "Asia/Tokyo"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_schedules.py -k application_timezone`
Expected: FAIL — `AttributeError: module 'core.scheduler' has no attribute 'apply_app_timezone'`

- [ ] **Step 3: Change the scheduler's timezone source**

In `backend/core/scheduler.py`, replace the import at line 43-44 region:

```python
from core.app_timezone import get_app_timezone
```

Keep `from tzlocal import get_localzone_name` and `from zoneinfo import ZoneInfo` — `_local_timezone()`
still uses them as the fallback path.

- [ ] **Step 4: Use it in the scheduler constructor**

Change line 72 from:

```python
    timezone=_local_timezone()
```

to:

```python
    timezone=get_app_timezone()
```

- [ ] **Step 5: Add the runtime re-point helper**

Insert immediately after the `scheduler = BackgroundScheduler(...)` block (after line 73):

```python
def apply_app_timezone() -> None:
    """Re-point the scheduler at the current application timezone.

    APScheduler's BaseScheduler._create_trigger_instance does
    trigger_args.setdefault('timezone', self.timezone), so assigning the attribute is
    enough for every subsequent add_job to pick it up. configure() is not usable here —
    it raises SchedulerAlreadyRunningError once the scheduler has started.
    """
    scheduler.timezone = get_app_timezone()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_schedules.py`
Expected: PASS, including the two pre-existing `_local_timezone` tests and the two new ones

- [ ] **Step 7: Run the backend suite**

Run: `python -m pytest -q tests/`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/core/scheduler.py backend/tests/test_schedules.py
git commit -m "feat(scheduler): run cron triggers in the application timezone"
```

---

## Task 6: Wire the setting through startup and the settings API

**Files:**
- Modify: `backend/main.py` (after the debug-restore block ending at line 297)
- Modify: `backend/api/settings.py:61` (allowlist), `:769-776` (GET), `:906` (bulk POST)

- [ ] **Step 1: Add the allowlist key**

In `backend/api/settings.py`, find the `# App setup` group in `BULK_SETTINGS_ALLOWLIST` and add
`"timezone"` next to `"setup_complete"`:

```python
    # App setup
    "setup_complete",
    "timezone",
    "poster_destination",
```

- [ ] **Step 2: Expose the effective zone on the settings GET**

Replace the body of `get_settings` at line 769-776 with:

```python
@router.get("/")
def get_settings(db: Session = Depends(get_db)) -> Dict[str, str]:
    """Get all settings (sensitive values are masked)"""
    settings = db.query(Setting).all()
    payload: Dict[str, str] = {}
    for setting in settings:
        payload[setting.key] = setting.value
    payload["effective_timezone"] = str(get_app_timezone())
    return _mask_settings_payload(payload)
```

Add the import at the top of the file:

```python
from core.app_timezone import get_app_timezone
```

`effective_timezone` is not in the allowlist, so it is read-only: a POST attempting it is dropped
with the existing "rejected disallowed key" warning.

- [ ] **Step 3: Rebuild jobs when the timezone changes**

In `save_bulk_settings`, insert between the `db.commit()` block and the final return (after line
904):

```python
    # A new zone re-interprets every cron expression, so the jobs have to be rebuilt.
    # set_app_timezone must land first — update_schedules() reads it via get_app_timezone().
    if "timezone" in allowed:
        from core.app_timezone import set_app_timezone
        from core.scheduler import apply_app_timezone, update_schedules

        set_app_timezone(allowed["timezone"])
        apply_app_timezone()
        update_schedules()
```

Import inside the function deliberately: `core.scheduler` pulls in every job module, and importing
it at settings-module scope would create an import cycle on startup.

- [ ] **Step 4: Restore the setting at startup**

In `backend/main.py`, insert immediately after the debug-restore block (which ends at line 297),
inside the same `else:` branch of the `is_testing` check:

```python
        # Restore persisted application timezone (if configured)
        try:
            from models.setting import get_setting
            from core.app_timezone import set_app_timezone
            from core.scheduler import apply_app_timezone
            persisted_db = SessionLocal()
            try:
                tz_setting = get_setting(persisted_db, "timezone")
                if tz_setting and tz_setting.value and tz_setting.value.strip():
                    if set_app_timezone(tz_setting.value):
                        apply_app_timezone()
                        log_info(LogTags.STARTUP, f"Application timezone: {tz_setting.value.strip()}")
            finally:
                persisted_db.close()
        except Exception as e:
            log_warning(LogTags.STARTUP, f"Could not restore application timezone setting: {e}")
```

This must stay above `start_scheduler()` at line 391 — the existing ordering already places setting
restoration first, and the scheduler is built from the restored zone.

- [ ] **Step 5: Confirm no import cycle was introduced**

Run: `python -c "import main"` from `backend/`
Expected: no output, exit 0

- [ ] **Step 6: Verify the setting is rejected when not allowlisted**

Temporarily confirm the guard by running:
```bash
python -m pytest -q tests/ -k settings
```
Expected: existing settings tests pass. If the suite has no coverage of the allowlist, that is
acceptable — Steps 1 and 2 are both verified by Step 5 and the frontend task.

- [ ] **Step 7: Run the backend suite**

Run: `python -m pytest -q tests/`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/api/settings.py
git commit -m "feat(settings): expose and apply the application timezone"
```

---

## Task 7: User-facing day boundaries

**Files:**
- Modify: `backend/api/stats.py:511-531`
- Modify: `backend/api/maker_tools.py:151-152`, `:742`, `:3901`, `:3958`
- Modify: `backend/services/border_replacer.py:1034`
- Create: `backend/tests/test_stats_daily_activity.py`

- [ ] **Step 1: Write the failing test**

`day_bounds_utc` itself is already covered in `backend/tests/test_app_timezone.py`. What is missing
is proof that the *endpoint* honours the application timezone, so this file tests that.

Create `backend/tests/test_stats_daily_activity.py`:

```python
from datetime import datetime, timezone

from core.app_timezone import set_app_timezone


def test_daily_activity_window_follows_the_application_timezone(client, test_db):
    """The code this replaces read "today" from the server's local day, so a UTC host
    flipped the card's day at 20:00 for an Amsterdam user.

    Noon UTC always falls inside today's UTC window and always sits exactly on the
    exclusive end of today's window twelve hours behind it. Counting the same poster
    under both zones therefore proves which zone the endpoint used. `test_db` rolls
    back after each test, so the counts are exact rather than deltas.
    """
    from models.poster import Poster

    edge = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    test_db.add(
        Poster(
            drive_id="tz-edge",
            file_name="tz-edge.jpg",
            file_path="/tmp/tz-edge.jpg",
            last_processed=edge,
        )
    )
    test_db.commit()

    try:
        set_app_timezone("UTC")
        in_utc = client.get("/api/stats/poster-daily-activity").json()["posters_renamed_today"]

        set_app_timezone("Etc/GMT+12")
        behind_utc = client.get("/api/stats/poster-daily-activity").json()["posters_renamed_today"]
    finally:
        set_app_timezone("UTC")

    assert in_utc == 1
    assert behind_utc == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest -q tests/test_stats_daily_activity.py`
Expected: FAIL — the current code derives its window from `datetime.now().astimezone()`, so both
requests use the same window and `behind_utc` equals `in_utc`.

- [ ] **Step 3: Rewrite the stats day-window computation**

In `backend/api/stats.py`, replace lines 514-531 with:

```python
    # Day boundaries follow the application timezone, so "today" matches the user's
    # calendar day rather than the server's. Column values are aware UTC after
    # UTCDateTime, so the bounds stay aware too.
    tz = get_app_timezone()
    today_start_utc, tomorrow_start_utc = day_bounds_utc(now_utc(), tz)
    week_ago_start_utc = today_start_utc - timedelta(days=7)
    month_ago_start_utc = today_start_utc - timedelta(days=30)
```

Add imports:
```python
from core.app_timezone import day_bounds_utc, get_app_timezone, now_utc
```

`dt_time` (aliased `time`) is no longer used in this module — remove it from the `datetime` import
if nothing else references it.

- [ ] **Step 4: Rename and repoint the maker tools day helper**

In `backend/api/maker_tools.py`, replace lines 151-152:

```python
def _monitor_today() -> date:
    """Today in the application timezone — 'local' no longer means the process TZ."""
    return today_in_app_tz()
```

Add `from core.app_timezone import today_in_app_tz` to the imports. Rename all three call sites:
`rg -n '_monitor_today_local' backend/` should return nothing.

- [ ] **Step 5: Repoint the border replacer holiday window**

In `backend/services/border_replacer.py`, change line 1034 from:

```python
    now = datetime.now()
```

to:

```python
    now = now_in_app_tz()
```

and add `from core.app_timezone import now_in_app_tz` to the imports.

- [ ] **Step 6: Confirm no server-local day windows remain**

Run: `rg -n 'datetime\.now\(\)\.astimezone\(\)|datetime\.now\(\)$' backend/api backend/services`
Expected: no matches. Any hit using `datetime.now()` for a *calendar day* must move to the app tz.

- [ ] **Step 7: Run the backend suite**

Run: `python -m pytest -q tests/`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/api/stats.py backend/api/maker_tools.py backend/services/border_replacer.py backend/tests/test_stats_daily_activity.py
git commit -m "fix(stats): use the application timezone for user-facing day boundaries"
```

---

## Task 8: Pin log timestamps to UTC

**Files:**
- Modify: `backend/core/logging.py:9`, `:252`, `:337`
- Modify: `backend/core/log_stream.py:79`

- [ ] **Step 1: Add the UTC suffix to the loguru time format**

In `backend/core/logging.py`, change line 9:

```python
LOGURU_TIMESTAMP_FORMAT = "YY/MM/DD HH:mm:ss!UTC"
```

This constant is interpolated into all three `logger.add()` format strings (lines 171, 179, 242),
so output stays byte-identical in shape while becoming UTC regardless of process `TZ`. loguru's
`!UTC` suffix converts the datetime before formatting — no record patcher is involved.

- [ ] **Step 2: Make the Python-side debug stamps UTC**

Change line 252 and line 337 from `datetime.now()` to `datetime.now(timezone.utc)`. Add `timezone`
to the `datetime` import in that file.

- [ ] **Step 3: Convert the WebSocket stream timestamp**

In `backend/core/log_stream.py`, change line 79. This sink is registered with
`format="{message}"` (`backend/core/logging.py:189`), so it bypasses loguru formatting entirely and
needs its own conversion:

```python
        "timestamp": record["time"].astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT),
```

Add `from datetime import timezone` to that file's imports.

- [ ] **Step 4: Verify the format still parses in the job log reader**

`backend/api/jobs.py:256-264` parses this exact `%y/%m/%d %H:%M:%S` string and stamps it UTC. That
round-trip is now genuinely correct rather than accidentally correct. Run:

```bash
python -m pytest -q tests/ -k job
```
Expected: PASS. If a test asserts on a log timestamp value, update the expectation to UTC — do not
re-add a local-time branch.

- [ ] **Step 5: Confirm the rendered format is unchanged**

Run: `python -m pytest -q tests/`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/core/logging.py backend/core/log_stream.py
git commit -m "fix(logging): render log timestamps in UTC"
```

---

## Task 9: Frontend date utilities

**Files:**
- Create: `frontend/src/utils/datetime.ts`
- Create: `frontend/tests/utils/datetime.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/utils/datetime.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { browserTimeZone, timeZoneOptions } from '../../src/utils/datetime'

describe('timeZoneOptions', () => {
  it('never throws when Intl.supportedValuesOf is unavailable or rejects the key', () => {
    // The picker degrades to free text rather than crashing on older engines.
    const intl = Intl as unknown as Record<string, unknown>
    const original = intl.supportedValuesOf

    try {
      delete intl.supportedValuesOf
      expect(timeZoneOptions()).toEqual([])

      intl.supportedValuesOf = () => {
        throw new Error('unsupported key')
      }
      expect(timeZoneOptions()).toEqual([])
    } finally {
      if (original) {
        intl.supportedValuesOf = original
      }
    }
  })
})

describe('browserTimeZone', () => {
  it('returns a non-empty zone name', () => {
    // The scheduling field is prefilled from this, so an empty string would show nothing.
    expect(browserTimeZone().length).toBeGreaterThan(0)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `frontend/`: `npm run -s test -- datetime`
Expected: FAIL — cannot resolve `../../src/utils/datetime`

- [ ] **Step 3: Write the implementation**

Create `frontend/src/utils/datetime.ts`:

```typescript
// Timestamps arrive from the API as ISO-8601 UTC. Rendering always defers to the
// browser's zone — that is the whole point of storing UTC — so nothing here passes a
// timeZone option to Intl.

const EMPTY = '—'

const parse = (value: string | null | undefined): Date | null => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTime(value: string | null | undefined): string {
  return parse(value)?.toLocaleString() ?? EMPTY
}

export function formatDate(value: string | null | undefined): string {
  return parse(value)?.toLocaleDateString() ?? EMPTY
}

export function formatTime(value: string | null | undefined): string {
  return parse(value)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) ?? EMPTY
}

export function formatNextRun(value: string | null | undefined): string {
  const date = parse(value)
  if (!date) return EMPTY
  return date.toLocaleString(undefined, {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  })
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export function timeZoneOptions(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  if (typeof intl.supportedValuesOf !== 'function') return []
  try {
    return intl.supportedValuesOf('timeZone')
  } catch {
    return []
  }
}
```

`formatNextRun` no longer hardcodes `'en-US'` — it uses the browser locale, so the weekday and
meridiem match the rest of the UI.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run -s test -- datetime`
Expected: PASS, 2 passed

- [ ] **Step 5: Run the full frontend suite and build**

Run: `npm run -s test && npm run -s build`
Expected: all tests pass, build succeeds

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/datetime.ts frontend/tests/utils/datetime.test.ts
git commit -m "feat(frontend): add shared date and timezone helpers"
```

---

## Task 10: Adopt the shared formatters and fix the hardcoded locale

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx:242-255`, `:521-522`, `:623`
- Modify: `frontend/src/pages/GDrives.tsx:808`, `:957`
- Modify: `frontend/src/components/ArtworkDrivesPanel.tsx:512`
- Modify: `frontend/src/components/maker-tools/RemindersTab.tsx:20-22`, `:170`
- Modify: `frontend/src/components/community/RequestItemCard.tsx:20-23`
- Modify: `frontend/src/components/poster-manager/UnmatchedItemsModal.tsx:522`
- Modify: `frontend/src/components/settings/ScheduleEditModal.tsx:602-605`
- Modify: `frontend/src/pages/PlexUpload.tsx:268`

- [ ] **Step 1: Replace the Dashboard next-run formatter**

In `frontend/src/pages/Dashboard.tsx`, delete the local `formatNextRun` function (lines 242-255) and
import the shared one:

```typescript
import { formatDateTime, formatNextRun } from '../utils/datetime'
```

Replace the call sites at lines 521-522 (`new Date(a.next_run).getTime()`) with
`new Date(a.next_run).getTime()` left as-is — sorting is a comparison, not a format, and it needs no
change. Replace line 623 (`new Date(activeCoverage.last_run).toLocaleString()`) with
`formatDateTime(activeCoverage.last_run)`.

- [ ] **Step 2: Replace the drive and artwork date renderings**

- `frontend/src/pages/GDrives.tsx` lines 808 and 957: replace the
  `Synced: ${new Date(d.last_synced).toLocaleDateString()} ${...toLocaleTimeString(...)}` template
  with `` `Synced: ${formatDateTime(d.last_synced)}` ``
- `frontend/src/components/ArtworkDrivesPanel.tsx` line 512: same substitution

Add the import to each file.

- [ ] **Step 3: Replace the remaining inline formatters**

- `frontend/src/components/maker-tools/RemindersTab.tsx` lines 20-22 and 170: use
  `formatDate` / `formatTime` where a date and a time are rendered separately, `formatDateTime`
  for the title
- `frontend/src/components/community/RequestItemCard.tsx` lines 20-23: same
- `frontend/src/components/poster-manager/UnmatchedItemsModal.tsx` line 522: `formatDate(sortDate)`
- `frontend/src/pages/PlexUpload.tsx` line 268: `formatDateTime(normalized)`

Add the import to each file.

- [ ] **Step 4: Leave the schedule-value math alone**

`frontend/src/pages/Dashboard.tsx:257-284` (`formatTimeValue` / `formatTimes`) converts the raw
`schedule_value` string between 24h and 12h. That string is a wall-clock in the application zone
and needs no timezone conversion — only a label, which Task 11 adds. Do not route it through
`formatTime`.

- [ ] **Step 5: Verify nothing still hardcodes a locale for dates**

Run from `frontend/`: `rg -n "toLocale\w*String\('en-US'" src/`
Expected: no matches

- [ ] **Step 6: Run tests and build**

Run: `npm run -s test && npm run -s build`
Expected: all pass, build succeeds

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "refactor(frontend): use shared date formatters"
```

---

## Task 11: The timezone picker and schedule-editor labels

**Files:**
- Modify: `frontend/src/hooks/useSettingsCore.ts`
- Modify: `frontend/src/components/settings/SettingsSchedulingSection.tsx`
- Modify: `frontend/src/components/settings/ScheduleEditModal.tsx`
- Modify: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: Add timezone state to the settings hook**

In `frontend/src/hooks/useSettingsCore.ts`, add next to the other `useState` calls (around line 79):

```typescript
  const [appTimezone, setAppTimezone] = useState('')
  const [effectiveTimezone, setEffectiveTimezone] = useState('')
```

In `fetchSettings`, after `const settings = await getSettings()` (line 83), add:

```typescript
      setAppTimezone((settings.timezone || '').trim())
      // Read-only: what the server actually resolved, which may differ when unset.
      setEffectiveTimezone((settings.effective_timezone || '').trim())
```

- [ ] **Step 2: Add the save handler**

In the same hook, after `handleSaveFanartApiKey` (line 256):

```typescript
  const handleSaveAppTimezone = async (): Promise<boolean> => {
    const valueToSave = appTimezone.trim()
    try {
      setSaving(true)
      await saveBulkSettings({ timezone: valueToSave })
      // The server re-resolves and rebuilds jobs; read back what it landed on so the
      // label is authoritative even when the submitted value was blank.
      const refreshed = await getSettings()
      setEffectiveTimezone((refreshed.effective_timezone || '').trim())
      showToast('Timezone saved')
      return true
    } catch (error) {
      console.error('Error saving timezone:', error)
      showToast('Failed to save timezone', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }
```

Extend the hook's return object with `appTimezone`, `setAppTimezone`, `effectiveTimezone`, and
`handleSaveAppTimezone`.

- [ ] **Step 3: Add the picker to the scheduling section**

In `frontend/src/components/settings/SettingsSchedulingSection.tsx`, add to the props type:

```typescript
  appTimezone: string
  onChangeAppTimezone: (value: string) => void
  effectiveTimezone: string
  onSaveAppTimezone: () => void
  saving: boolean
```

Destructure them in the function signature, then render a block above the "Scheduled Tasks"
`<div className="server-section">`:

```tsx
      <div className="server-section">
        <div className="server-section-header">
          <h3>Timezone</h3>
        </div>
        <p className="server-section-description">
          Schedule times and calendar days use this zone. Timestamps are stored in UTC and
          displayed in your browser&apos;s own zone.
        </p>
        <input
          type="text"
          list="app-timezone-options"
          value={appTimezone}
          placeholder={browserTimeZone()}
          onChange={(e) => onChangeAppTimezone(e.target.value)}
          aria-label="Application timezone"
        />
        <datalist id="app-timezone-options">
          {timeZoneOptions().map((zone) => (
            <option key={zone} value={zone} />
          ))}
        </datalist>
        {!appTimezone && (
          <p className="schedule-summary">
            <span className="summary-disabled">
              Not set — falling back to {effectiveTimezone || 'UTC'}. Detected from your browser:{' '}
              {browserTimeZone()}
            </span>
          </p>
        )}
        {!!appTimezone && browserTimeZone() !== appTimezone && (
          <p className="schedule-summary">
            <span className="summary-disabled">
              Your browser is in {browserTimeZone()}; schedules fire at a different
              wall-clock time there.
            </span>
          </p>
        )}
        <button className="btn-primary" onClick={onSaveAppTimezone} disabled={saving}>
          {saving ? 'Saving…' : 'Save Timezone'}
        </button>
      </div>
```

Import `browserTimeZone` and `timeZoneOptions` from `../../utils/datetime`.

The empty `appTimezone` shows the browser zone as a placeholder and names it in the hint — that is
the adopt-once suggestion. It writes nothing until the user clicks Save, so no other browser can
hijack the value.

- [ ] **Step 4: Wire it through Settings.tsx**

In `frontend/src/pages/Settings.tsx`, destructure the four new values from the settings hook near
the other hook destructuring (around line 468), and pass them to the section at line 1428:

```tsx
            appTimezone={appTimezone}
            onChangeAppTimezone={setAppTimezone}
            effectiveTimezone={effectiveTimezone}
            onSaveAppTimezone={handleSaveAppTimezone}
            saving={saving}
```

- [ ] **Step 5: Label the schedule editor**

In `frontend/src/components/settings/ScheduleEditModal.tsx`, import `browserTimeZone` and add
directly beneath the time inputs:

```tsx
        <p className="schedule-summary">
          <span className="summary-disabled">
            Times are in {appTimezone || effectiveTimezone || 'UTC'}
            {browserTimeZone() !== (appTimezone || effectiveTimezone)
              ? ` — your browser is in ${browserTimeZone()}`
              : ''}
          </span>
        </p>
```

Accept `appTimezone` and `effectiveTimezone` as props on the modal and pass them from
`Settings.tsx` where the modal is rendered.

- [ ] **Step 6: Run tests and build**

Run: `npm run -s test && npm run -s build`
Expected: all pass, build succeeds (TypeScript will catch any missing prop wiring)

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): timezone picker with browser-zone suggestion"
```

---

## Task 12: Documentation

**Files:**
- Modify: `README.md:83`, `:99`, `:119`
- Modify: `docs/native-install.md:177`, `:238`, `:258`, `:335-338`
- Modify: `CHANGELOG.md:7`

- [ ] **Step 1: Fix the README**

Replace line 83:

```
      - TZ=America/New_York   # Host timezone. Drives scheduler local-time interpretation.
```

with:

```
      - TZ=UTC                # Keep UTC. Set your timezone in Settings → Scheduling instead.
```

Replace line 99:

```
  -e TZ=America/New_York \
```

with:

```
  -e TZ=UTC \
```

Replace line 119:

```
| `TZ` | `UTC` | Timezone |
```

with:

```
| `TZ` | `UTC` | Process timezone. Leave UTC; timestamps are stored in UTC. |
| `APP_TIMEZONE` | unset | Timezone for schedules and calendar days. Overridable in Settings → Scheduling. |
```

The old advice — setting `TZ` to a local zone — is the root cause: it silently moves every schedule.

- [ ] **Step 2: Fix the native install guide**

Replace line 177:

```
# Environment=TZ=America/New_York
```

with:

```
# Environment=TZ=UTC
```

Replace line 238:

```
| `TZ` | host timezone | Scheduler local-time interpretation |
```

with:

```
| `TZ` | host timezone | Process timezone for logs; leave UTC |
| `APP_TIMEZONE` | unset | Timezone for schedules and calendar days; also settable in the UI |
```

Replace line 258:

```
Environment=TZ=America/New_York
```

with:

```
Environment=TZ=UTC
Environment=APP_TIMEZONE=Europe/Amsterdam
```

Update lines 335-338 to point at the new setting rather than the scheduler's host lookup:

```
- The scheduler needs a named timezone. Set it in **Settings → Scheduling**, or set
  `APP_TIMEZONE=Region/City` in the service environment. On FreeBSD, `tzsetup`
  (which writes `/var/db/zoneinfo`) is what makes host-local fallback work.
```

- [ ] **Step 3: Add the changelog entry**

In `CHANGELOG.md`, under `## [Unreleased]` on line 7, add:

```markdown
### Added
- Settings → Scheduling: a timezone for schedules and calendar days. Pre-filled from your browser the first time. Timestamps continue to be stored in UTC and are displayed in your own zone.

### Fixed
- Schedules and dashboard day boundaries no longer follow the server's system timezone, so a UTC host no longer runs a 14:30 schedule at the wrong hour.
- Datetime columns are now read and written as aware UTC in one place, removing several hand-maintained conversions and a `.timestamp()` call that misread UTC as local time.
```

- [ ] **Step 4: Verify the docs no longer tell users to set a local TZ**

Run: `rg -n 'TZ=(America|Europe|Asia)' README.md docs/`
Expected: no matches

- [ ] **Step 5: Run the full verification**

From `backend/`: `python -m pytest -q tests/`
From `frontend/`: `npm run -s test && npm run -s build`
Expected: everything passes

- [ ] **Step 6: Commit**

```bash
git add README.md docs/native-install.md CHANGELOG.md
git commit -m "docs: explain the UTC storage and application timezone split"
```

---

## Manual verification after Task 12

Run the stack with `TZ=UTC` and no application timezone configured:

1. Settings → Scheduling shows an empty field, a placeholder matching your browser zone, and a
   hint naming the `UTC` fallback.
2. Save `Europe/Amsterdam`. The hint changes to note your browser is in a different zone.
3. Create a `daily` schedule at `14:30`. Next run shows an aware offset (`+02:00` in summer) on the
   dashboard — proving the cron was built in the app zone, not UTC.
4. Set the browser devtools timezone override to `America/New_York`. The dashboard next-run
   wall-clock changes but the *instant* is unchanged; the Logs page timestamps are UTC.
5. Restart the app. The timezone survives and the hint no longer claims a fallback.