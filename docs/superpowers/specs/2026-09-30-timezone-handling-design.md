# Timezone handling: server stores UTC, user sees local

Date: 2026-09-30
Status: approved

## Problem

Posterflow conflates two unrelated meanings of "timezone" into a single OS env var (`TZ`):

1. **Process timezone** — what wall-clock the storage layer and logs write. This should always be UTC.
2. **Application timezone** — what a schedule's `14:30` means, and what "today" means to a user.

They are coupled today because `backend/core/scheduler.py:47-57` resolves the scheduler zone
from `tzlocal.get_localzone_name()`, which reads the process `TZ`. Since `docker-compose.yml:14`
ships `TZ=UTC` to keep storage safe, every user's schedules and every user-facing day boundary
are also silently UTC.

Three concrete defects follow:

- A `daily` schedule entered as `14:30` by a user in Amsterdam fires at 16:30 local.
- The dashboard "daily activity" card (`backend/api/stats.py:517-529`) computes "today" from
  the *server's* local day, so the day rolls over at 20:00 for an Amsterdam user. Its own
  comment claims the opposite.
- The schedule editor never states which zone a time is interpreted in.

A fourth, latent: the naive/aware contract on datetime columns is a convention maintained by
hand at six call sites and already leaking — `backend/api/idarr.py:1459-1460` calls
`.timestamp()` on a naive SQLite value with no guard, silently interpreting UTC as local.

## Goals

- Storage holds UTC, always, and the invariant is enforced by the type rather than by convention.
- Schedules and user-facing day boundaries use one explicitly configured application timezone.
- The browser renders timestamps in the viewer's own timezone, and the UI never hides which zone
  a schedule time is expressed in.
- No migration, no data rewrite, no behaviour change for an existing install that has not set a
  timezone.

## Non-goals

- Per-schedule timezones. The application timezone is instance-wide. (Decided: there are no
  users in Posterflow — auth is a single shared app password — so per-user timezone would mean
  inventing multi-tenancy.)
- Per-user timezone profiles.
- Migrating off SQLite or to a database with a native `TIMESTAMPTZ`.
- Changing the log line format, the schedule model schema, or APScheduler's trigger types.
- Adding a datetime library. `zoneinfo` (stdlib) and `tzlocal` (already a dependency) suffice.

## Architecture

Two timezones, one resolution path, two consumers.

| | Process TZ | Application TZ |
|---|---|---|
| Source | OS env `TZ` | `APP_TIMEZONE` env var, overridden by the DB `timezone` setting |
| Value | Always `UTC` | IANA name, e.g. `Europe/Amsterdam` |
| Governs | Storage wall-clock, log wall-clock | Cron interpretation, "what day is today", schedule-editor labels |
| Default | `UTC` (already in `Dockerfile:35`, `docker-compose.yml:14`) | Falls back to the existing `tzlocal` lookup |

Resolution order for the application timezone:

1. DB `timezone` setting (settable from Settings → Scheduling)
2. `APP_TIMEZONE` environment variable
3. `_local_timezone()` — the existing `tzlocal.get_localzone_name()` lookup, including its
   warn-and-fall-back-to-UTC behaviour for hosts with no zone name

An unresolvable IANA name logs a warning and retains the previously resolved zone. It never
raises and never blocks startup.

### New module: `backend/core/app_timezone.py`

Single cached source of truth, so the scheduler and the API always agree.

- `get_app_timezone() -> ZoneInfo` — resolved zone, cached
- `set_app_timezone(name: str) -> None` — validate, cache, and report success; invalid input
  warns and leaves the current value untouched

### Wiring

- `backend/core/config.py` — `Settings` gains `app_timezone: str = ""`. `Settings` sets no
  `env_prefix` (`backend/core/config.py:81`), so this is read from the environment as
  `APP_TIMEZONE`, matching the existing unprefixed convention (`CONFIG_DIR` ← `config_dir`,
  `DATABASE_URL` ← `database_url`)
- `backend/main.py` (~line 282, alongside the existing `gdrive_storage_path` and `debug_enabled`
  restore blocks) — restore the persisted `timezone` key at startup
- `backend/api/settings.py:61` — add `"timezone"` to `BULK_SETTINGS_ALLOWLIST`
- `POST /api/settings/bulk` — when `timezone` is written, call `update_schedules()` after commit so
  cron triggers rebuild in the new zone without a container restart. `update_schedules()` already
  tears down and recreates every job (`backend/core/scheduler.py:424-425`).

### Initialisation order

`get_app_timezone()` cannot read the database at import time. It resolves at import from
`APP_TIMEZONE` / the `tzlocal` fallback; the lifespan then overrides it from the DB setting. The
existing startup order already satisfies this — setting restoration at `backend/main.py:256-297`
runs before `start_scheduler()` at `backend/main.py:391-398`, and `start_scheduler()` calls
`update_schedules()` (`backend/core/scheduler.py:670-676`). Do not reorder these.

## Component 1 — Storage: `UTCDateTime`

New `backend/util/utc_datetime.py`:

```python
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

**No migration.** SQLite has no `TIMESTAMPTZ`; `DateTime(timezone=True)` already renders as
plain `DATETIME`, so `impl = DateTime` produces byte-identical DDL. Only Python-side coercion
changes. `server_default=func.now()` and `onupdate=func.now()` remain SQL-side defaults — SQLite's
`CURRENT_TIMESTAMP` is already UTC, and the result processor stamps it on read.

**No data rewrite.** The existing data already satisfies "naive in the database means UTC":
no code writes naive *local* time into a DateTime column. The naive `datetime.now()` call sites
are all filenames, log text, or read-only day-window arithmetic.

**One behaviour change, deliberate:** binding an aware *non-UTC* datetime now converts correctly
instead of silently persisting the local wall clock. Nothing in the codebase does this today.

### Model migration

Replace `DateTime(timezone=True)` with `UTCDateTime` — 35 occurrences across 14 files in
`backend/models/`: `schedule.py`, `job.py`, `drive.py`, `artwork_drive.py`, `poster.py`,
`artwork.py`, `idarr.py`, `setting.py`, `workflow.py`, `plex_upload.py`, `poster_override.py`,
`poster_reminder.py`, `manual_media.py`.

### Removals made redundant

- `backend/api/drives.py:170-179` — `field_serializer` for `last_synced` / `last_rename_processed`
- `backend/api/artwork_drives.py:71-73` — same pattern
- `backend/api/poster_reminders.py:46-50` — `_iso_utc()` helper, reduced to plain `.isoformat()`

### Defect fixed

- `backend/api/idarr.py:1459-1460` — `.timestamp()` on `row.updated_at` is now safe, because
  reads are guaranteed aware.

## Component 2 — Scheduler

`backend/core/scheduler.py:61-73` — replace `timezone=_local_timezone()` with
`timezone=get_app_timezone()`.

No other scheduler change. The trigger construction at `backend/core/scheduler.py:497-659` is
already correct: it interprets the raw `schedule_value` string in the scheduler's zone, and
APScheduler returns a timezone-aware UTC `next_run_time`, which serializes correctly today.

The DB `schedules` table gains no column — the application timezone is instance-wide, so the
lookup takes no arguments.

## Component 3 — User-facing day boundaries

Three sites compute "today" from the server's local zone. All switch to the application timezone.

- `backend/api/stats.py:517-529` — use `datetime.now(get_app_timezone())`, and drop the
  `.replace(tzinfo=None)` calls, since column comparisons are now aware-vs-aware
- `backend/api/maker_tools.py:151-152` — rename `_monitor_today_local()` to `_monitor_today()`,
  returning `datetime.now(get_app_timezone()).date()`. Call sites `:742`, `:3901`, `:3958`
- `backend/services/border_replacer.py:1034` — holiday-window `now` becomes
  `datetime.now(get_app_timezone())`

The rename is required: "local" no longer means the process timezone.

## Component 4 — Logs pinned to UTC

Log timestamps are local wall-clock with no offset (`backend/core/log_stream.py:79`), and
`backend/api/jobs.py:261-262` re-parses that same string and force-labels it UTC — a round-trip
that is only correct while `TZ=UTC`.

loguru's documented mechanism for this is the `!UTC` suffix on a time format, which converts the
datetime before formatting. No `patcher` is used; loguru's docs explicitly recommend against
modifying the record dict.

- `backend/core/logging.py:9` — `LOGURU_TIMESTAMP_FORMAT = "YY/MM/DD HH:mm:ss!UTC"`. One edit,
  since the constant is interpolated into all three `logger.add()` format strings
  (`:171`, `:179`, `:242`)
- `backend/core/logging.py:189` registers `broadcast_sink` with `format="{message}"`, so that
  callable sink bypasses formatting and needs an explicit conversion at
  `backend/core/log_stream.py:79`:
  `record["time"].astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)`
- `backend/core/logging.py:252, 337` — Python-side debug lines switch to
  `datetime.now(timezone.utc)`, leaving `PYTHON_TIMESTAMP_FORMAT` unchanged

Output format is byte-identical. Log wall-clock becomes UTC end to end, independent of process
`TZ`, and the `api/jobs.py` round-trip becomes genuinely correct.

## Component 5 — Frontend

Display is already correct: every timestamp is rendered with `toLocaleString()` and no
`timeZone` option, so the browser renders UTC-stored values in the viewer's own zone. No display
behaviour changes. What is missing is labelling.

### New `frontend/src/utils/datetime.ts`

There is no shared date helper today; ten-plus call sites inline the formatting.

| Export | Purpose |
|---|---|
| `formatDateTime(iso)` | `toLocaleString()`, browser default locale |
| `formatDate(iso)` / `formatTime(iso)` | split variants |
| `formatNextRun(iso)` | weekday + time, browser locale (no hardcoded locale) |
| `browserTimeZone()` | `Intl.DateTimeFormat().resolvedOptions().timeZone` |
| `timeZoneOptions()` | `Intl.supportedValuesOf('timeZone')`, `[]` where unsupported |

### Timezone picker, seeded from the browser

`frontend/src/components/settings/SettingsSchedulingSection.tsx` gains a timezone field: a text
input with a `<datalist>` populated from `timeZoneOptions()`. A text input rather than a `<select>`
because `Intl.supportedValuesOf` is Baseline but not universal, and an empty datalist degrades to
free text with no branching.

While the stored `timezone` is empty, the field is pre-filled with `browserTimeZone()` and
labelled "Detected from your browser". The user saves once and the value is pinned. This is a
suggestion, never an override — see "Authority" below.

### Authority: why the browser only suggests

The browser timezone is authoritative for *display* and nothing else. The application timezone
decides when a cron fires, which is server-authoritative behaviour, so it must be explicit and
server-side.

Posterflow has no per-user identity — auth is a single shared app password
(`backend/core/auth.py:20-22`). Nothing distinguishes the owner from any other browser that
reaches the app, so an automatic browser-derived zone could be written by a guest, a VPN, or a
family member on another continent, silently shifting every schedule with no way to tell why. A
GET must also not mutate future scheduling behaviour.

Adopt-once-and-pin avoids both: nothing changes until a human saves, and after that the stored
value wins regardless of who opens the app.

The current fallback is the actual problem this fixes. Docker ships `TZ=UTC`, so an unconfigured
install falls through to `tzlocal` and gets UTC schedules — the state most likely to be wrong, in
the configuration most users start from. Prefilling gives the common single-operator case a
correct default without giving up authority over scheduling.

### Schedule editor

`frontend/src/components/settings/ScheduleEditModal.tsx` — the user-visible fix:

- Show the active zone alongside the time inputs
- When `browserTimeZone()` differs from the effective application zone, state that the schedule
  fires at a different wall-clock time in this browser

### Dashboard

`frontend/src/pages/Dashboard.tsx:242-255` — drop the hardcoded `'en-US'` from `formatNextRun` in
favour of the browser locale. When the browser zone differs from the application zone, append the
zone abbreviation to the next-run line so the wall-clock time is never ambiguous.

### Backend read

The editor needs the *resolved* application zone, not the stored string — it may be unset and
falling back. Expose the effective value from `core/app_timezone.py` as one read-only field
(`effective_timezone`) on the existing settings GET, alongside the editable `timezone` key.
Read-only because writing it is not meaningful; the field exists so the editor can label itself
correctly on a fresh install. No new endpoint.

The prefill itself needs no round trip — it is `browserTimeZone()`, evaluated client-side.

### Unchanged

`frontend/src/pages/Dashboard.tsx:257-284`'s manual 24h→12h conversion operates on the raw
`schedule_value` string. That string is a wall-clock time in the application zone and needs no
conversion — only a label.

## Testing

Backend (pytest, run from `backend/`):

| Test | Asserts |
|---|---|
| `test_utc_datetime.py` | Aware UTC round-trips; aware non-UTC binds to the correct UTC wall clock; naive bind is treated as UTC; `None` passes through |
| `test_app_timezone.py` | Resolution order (DB beats env var beats `tzlocal`); invalid IANA name warns and retains the previous zone; empty string falls back |
| `test_schedules.py` (extend) | Scheduler timezone equals the configured application zone, not process `TZ`. The existing `test_scheduler_timezone_resolves_by_name` (`:1-30`) continues to cover the fallback path |
| `test_stats.py` (new) | `poster-daily-activity` day boundary under a non-UTC application zone — a UTC-stored 23:30 counts as "today" in `Europe/Amsterdam`. This endpoint has no test today |
| `test_no_naive_datetime_columns.py` | Guard: no `DateTime(timezone=True)` remains in `backend/models/` |

The last test is the cheap enforcement that keeps the invariant from drifting back.

Frontend (vitest): `timeZoneOptions()` contains `Europe/Amsterdam`; `browserTimeZone()` returns a
string. Formatted output is deliberately not asserted — it is the platform's responsibility, and
pinning literal strings to the runner's zone produces brittle tests. The zone prefill is
likewise untested: it is a single `stored || browserTimeZone()` fallback in JSX, and a component
test for one conditional expression costs more than it catches.

## Rollout

- No migration, no data rewrite.
- Backwards compatible: with no application timezone configured, resolution falls through to
  `_local_timezone()`, which is exactly today's behaviour. No existing install changes meaning.
- Rollback is a revert. Nothing persists outside the `timezone` setting key.

## Documentation

`README.md:83, 99, 119` currently tells users to set `TZ=America/New_York`. That advice is the
root cause and must change: keep `TZ=UTC`, set the timezone in **Settings → Scheduling**.

Same update for `docs/native-install.md:177, 238, 258`. Plus a `CHANGELOG.md` entry.

## Verification

- `python -m pytest -q tests/` from `backend/`
- `npm run -s test` and `npm run -s build` from `frontend/`