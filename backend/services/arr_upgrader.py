"""Search Sonarr/Radarr for upgrades from the Tools page, a few items a run, the way BZ00001's
upgradinatorr and riyu963's arr_upgrade_search do. A run picks the next monitored items that
are out and do not carry the search tag yet, has the arr search for each one in turn, waits
for the search, reads from the arr's history what it grabbed (against the files it replaces)
and tags the item so the next run moves on. Once every item carries the tag, the tags are
cleared and the cycle starts over. The arr decides what is an upgrade; Posterflow only paces
the searches: one at a time, none for an item that is downloading, none while the arr is
already busy searching. Later runs say what became of each grab: imported, failed, or stuck in
the arr's queue. Settings are per instance."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_info, log_warning
from models.setting import get_setting_value, upsert_setting
from services.arr_renamer import KINDS, NOTIFY_FEATURE, ProgressCallback, _Arr, _Stopped, _connect, load_arr_instances, tag_label_problem

MODES = ("all", "cutoff", "missing")
MODE_TEXT = {"all": "everything", "cutoff": "cutoff unmet", "missing": "missing"}
WANTED_LIST = {"cutoff": "Cutoff Unmet", "missing": "Missing"}
UNITS = ("series", "seasons")
UNIT_TEXT = {"series": "whole series", "seasons": "monitored seasons"}
MONITORED = ("yes", "any", "no")
MONITORED_TEXT = {"yes": "monitored", "any": "monitored or not", "no": "not monitored"}
STATE_KEYS = ("sonarr_upgrade_state", "radarr_upgrade_state")  # settings rows: what a run leaves for the next ones, per instance
DEFAULTS: Dict[str, Any] = {
    "count": 5,                 # items searched per run
    "mode": "all",              # all: every monitored item that is out | cutoff / missing: the items on that Wanted list
    "search_unit": "seasons",   # Sonarr, mode all: one search per monitored season | one of the whole series
    "season_threshold": 50,     # Sonarr, by season: a season is searched when at least this % of its episodes are monitored
    "tag_name": "upgrade-searched",  # tag put on searched items; required, as it is what moves the next run on
    "ignore_tag": "upgrade-ignore",
    "monitored": "yes",         # Radarr: yes | any | no. Sonarr's own series and season searches refuse what is not monitored
    "only_tags": "",            # only items carrying one of these tags (comma separated); blank = any
    "quality_profiles": "",     # only items on one of these quality profiles, by name (comma separated); blank = any
    "min_age_days": 0,          # only items released at least this many days ago (0 = any age)
    "max_grabs": 0,             # a run stops once this many items got a release (0 = no limit)
    "skip_queued": True,        # an item with a download in the arr's queue waits for a later run
    "title_filter": "",         # only items whose title contains this (for trying one item)
    "notify": False,            # a Discord / Apprise summary after every run
    "notify_grabs_only": False,  # only when something was grabbed or needs a look
}
MAX_COUNT = 500
MAX_AGE_DAYS = 3650
_PAGE = 500
_MAX_PAGES = 100            # of a Wanted list or the queue
_QUEUE_HOLD_HOURS = 72      # a download older than this no longer holds its item back, so a stuck one cannot stall the cycle
_MAX_BUSY = 3               # the run is skipped while the arr has this many searches queued or running
_SEARCH_WAIT = 600          # seconds a search is waited for
_SEARCH_GAP = 5             # seconds between searches, to go easy on the indexers
_POLL = 2
_GIVE_UP_FAILS = 3          # searches in a row that fail before the instance's run stops
_GIVE_UP_RUNS = 3           # runs in a row an item's search fails before it is tagged anyway, so the cycle can end
_FOLLOW_DAYS = 7            # days a grab is followed for its import
_MAX_PENDING = 300          # grabs followed at once, per instance
_BUSY = ("queued", "started")
_SEARCHES = ("SeriesSearch", "SeasonSearch", "EpisodeSearch", "MissingEpisodeSearch", "CutoffUnmetEpisodeSearch",
             "MoviesSearch", "MissingMoviesSearch", "CutoffUnmetMoviesSearch")
_QUEUE_STATES = {"importPending": "waiting to import", "importing": "importing", "importBlocked": "stuck importing",
                 "importFailed": "stuck importing", "failedPending": "stuck importing"}
_STUCK_STATES = ("importBlocked", "importFailed", "failedPending")
_GRAB_CHARS = 3300  # of grab blocks in one notification


# ── settings ───────────────────────────────────────────────────────────────
def names_of(given: Any) -> List[str]:
    """The names in a comma separated setting (or a list), each once, in the order given."""
    parts = given if isinstance(given, (list, tuple)) else str(given or "").split(",")
    out: List[str] = []
    for part in parts:
        name = str(part).strip()
        if name and name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out


def normalize_one(given: Any, kind: Optional[str] = None) -> Dict[str, Any]:
    """One instance's settings with the defaults filled in and every value coerced to its type."""
    given = given if isinstance(given, dict) else {}

    def number(key: str, low: int, top: int) -> int:
        try:
            return min(max(int(given.get(key, DEFAULTS[key])), low), top)
        except (TypeError, ValueError):
            return DEFAULTS[key]

    mode = str(given.get("mode", DEFAULTS["mode"]) or "").strip().lower()
    unit = str(given.get("search_unit", DEFAULTS["search_unit"]) or "").strip().lower()
    monitored = str(given.get("monitored", DEFAULTS["monitored"]) or "").strip().lower()
    return {
        "count": number("count", 1, MAX_COUNT),
        "mode": mode if mode in MODES else DEFAULTS["mode"],
        "search_unit": unit if unit in UNITS and kind != "radarr" else DEFAULTS["search_unit"],  # a movie is searched whole
        "season_threshold": number("season_threshold", 0, 100),
        "tag_name": str(given.get("tag_name", DEFAULTS["tag_name"]) or "").strip(),
        "ignore_tag": str(given.get("ignore_tag", DEFAULTS["ignore_tag"]) or "").strip(),
        "monitored": monitored if monitored in MONITORED and kind != "sonarr" else DEFAULTS["monitored"],
        "only_tags": ", ".join(names_of(given.get("only_tags")))[:500],
        "quality_profiles": ", ".join(names_of(given.get("quality_profiles")))[:500],
        "min_age_days": number("min_age_days", 0, MAX_AGE_DAYS),
        "max_grabs": number("max_grabs", 0, MAX_COUNT),
        "skip_queued": bool(given.get("skip_queued", DEFAULTS["skip_queued"])),
        "title_filter": str(given.get("title_filter", DEFAULTS["title_filter"]) or "").strip()[:200],
        "notify": bool(given.get("notify", DEFAULTS["notify"])),
        "notify_grabs_only": bool(given.get("notify_grabs_only", DEFAULTS["notify_grabs_only"])),
    }


def settings_problem(settings: Dict[str, Any]) -> str:
    """Why these settings cannot run, or "": the search tag is required (without it every run
    would search the same items), a tag has letters, digits and - only, and the two differ."""
    if not settings.get("tag_name"):
        return "Set a search tag. It marks what was searched, so the next run moves on."
    for tag in [settings["tag_name"], settings.get("ignore_tag") or "", *names_of(settings.get("only_tags"))]:
        problem = tag_label_problem(tag) if tag else ""
        if problem:
            return problem
    if settings["tag_name"].lower() == (settings.get("ignore_tag") or "").lower():
        return "The search tag and the ignore tag must differ."
    return ""


# ── what the arr says about an item, a file and a grab ─────────────────────
def _when(text: Any) -> Optional[datetime]:
    """A time from the arr ("2026-11-11T12:08:43Z"), or None."""
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed and parsed.tzinfo is None else parsed


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def item_label(item: Dict[str, Any]) -> str:
    title, year = str(item.get("title") or "Unknown"), item.get("year")
    return f"{title} ({year})" if year and not title.endswith(f"({year})") else title


def is_out(item: Dict[str, Any], kind: str, today: date) -> bool:
    """Can a search find it? It has files, or it is a movie Radarr counts as available, or a
    series whose first episode has aired."""
    if kind == "radarr":
        return bool(item.get("hasFile") or item.get("isAvailable", item.get("status") == "released"))
    if ((item.get("statistics") or {}).get("episodeFileCount") or 0) > 0:
        return True
    first = _when(item.get("firstAired"))
    return first is not None and first.date() <= today


def released(item: Dict[str, Any], kind: str) -> Optional[date]:
    """When a movie came out (the earliest of cinema, digital and disc) or a series first aired;
    1 January of its year when the arr has no date; None when it has no year either."""
    keys = ("inCinemas", "digitalRelease", "physicalRelease") if kind == "radarr" else ("firstAired",)
    dates = [at.date() for at in (_when(item.get(key)) for key in keys) if at]
    if dates:
        return min(dates)
    try:
        return date(int(item.get("year") or 0), 1, 1)
    except (TypeError, ValueError):
        return None


def _quality(record: Dict[str, Any]) -> str:
    return str((((record or {}).get("quality") or {}).get("quality") or {}).get("name") or "unknown quality")


def _score(record: Dict[str, Any]) -> Optional[int]:
    score = (record or {}).get("customFormatScore")
    return score if isinstance(score, int) and not isinstance(score, bool) else None


def describe(records: List[Dict[str, Any]], without: int = 0) -> str:
    """The quality and custom format score of files (or of a grab): "WEBDL-1080p, score 100";
    several: "WEBDL-1080p ×6, HDTV-720p ×2, score 0 to 50"; none: "no file"."""
    if not records:
        return "no file"
    kinds = Counter(_quality(r) for r in records)
    names = ", ".join(q if len(records) == 1 else f"{q} ×{n}" for q, n in kinds.most_common())
    scores = sorted({s for s in (_score(r) for r in records) if s is not None})
    scored = "" if not scores else f", score {scores[0]}" if len(scores) == 1 else f", score {scores[0]} to {scores[-1]}"
    return names + scored + (f", {without} without a file" if without else "")


def _formats(record: Dict[str, Any]) -> List[str]:
    return [str(cf.get("name")) for cf in (record.get("customFormats") or []) if isinstance(cf, dict) and cf.get("name")]


def episodes_text(episodes: List[Dict[str, Any]]) -> str:
    """Episodes as "S01E03", "S01E01-E08" or "S01-S02 (14 episodes)"."""
    episodes = sorted(episodes, key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))
    if not episodes:
        return ""
    seasons = sorted({e.get("seasonNumber") or 0 for e in episodes})
    if len(seasons) > 1:
        return f"S{seasons[0]:02d}-S{seasons[-1]:02d} ({len(episodes)} episodes)"
    first, last = episodes[0].get("episodeNumber") or 0, episodes[-1].get("episodeNumber") or 0
    return f"S{seasons[0]:02d}E{first:02d}" + (f"-E{last:02d}" if last != first else "")


def seasons_text(numbers: List[int]) -> str:
    """Season numbers with runs folded: "season 2", "seasons 1-3, 7"."""
    numbers = sorted(set(numbers))
    runs: List[List[int]] = []
    for n in numbers:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    listed = ", ".join(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs)
    return f"season{'s' if len(numbers) != 1 else ''} {listed}"


def grab_rows(kind: str, grabs: List[Dict[str, Any]], files: List[Dict[str, Any]], episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per release the arr grabbed, against what it replaces: {"release", "episodes",
    "before", "after", "formats", "download" (its download id, to follow it to its import)}.
    A season pack is one grab per episode in the arr's history."""
    by_release: Dict[str, List[Dict[str, Any]]] = {}
    for grab in grabs:
        by_release.setdefault(str(grab.get("sourceTitle") or "unknown release"), []).append(grab)
    rows = []
    for release, own in by_release.items():
        if kind == "radarr":
            before, covered = describe(files), ""
        else:
            by_id = {e.get("id"): e for e in episodes}
            file_by_id = {f.get("id"): f for f in files}
            mine = [by_id[g.get("episodeId")] for g in own if g.get("episodeId") in by_id]
            old_ids = {e.get("episodeFileId") for e in mine if e.get("episodeFileId") in file_by_id}
            before = describe([file_by_id[i] for i in old_ids], without=sum(1 for e in mine if e.get("episodeFileId") not in file_by_id) if old_ids else 0)
            covered = episodes_text(mine)
        rows.append({"release": release, "episodes": covered, "before": before, "after": describe(own[:1]), "formats": _formats(own[0]),
                     "download": str(next((g.get("downloadId") for g in own if g.get("downloadId")), ""))})
    return rows


# ── the arr ────────────────────────────────────────────────────────────────
class _SearchArr(_Arr):
    """The v3 calls an upgrade search needs, on top of the renamer's."""

    def __init__(self, client: Any, kind: str) -> None:
        super().__init__(client, kind)
        self._queue: Optional[List[Dict[str, Any]]] = None

    def library(self) -> List[Dict[str, Any]]:
        rows = self._read(self.noun, "its library")
        return [r for r in rows if isinstance(r, dict) and r.get("id")] if isinstance(rows, list) else []

    def _pages(self, path: str, what: str) -> List[Dict[str, Any]]:
        """Every record of a paged list."""
        out: List[Dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            got = self._read(f"{path}{'&' if '?' in path else '?'}page={page}&pageSize={_PAGE}", what)
            records = [r for r in (got.get("records") or []) if isinstance(r, dict)] if isinstance(got, dict) else []
            out += records
            if len(records) < _PAGE:
                break
        return out

    def tag_ids(self) -> Dict[str, int]:
        """The arr's tags: {label in lower case: id}."""
        rows = self._read("tag", "its tags")
        return {str(t.get("label") or "").lower(): int(t["id"]) for t in rows if isinstance(t, dict) and t.get("id")} if isinstance(rows, list) else {}

    def quality_profiles(self) -> Dict[str, int]:
        """The arr's quality profiles: {name: id}."""
        rows = self._read("qualityprofile", "its quality profiles")
        return {str(p.get("name") or ""): int(p["id"]) for p in rows if isinstance(p, dict) and p.get("id")} if isinstance(rows, list) else {}

    def wanted(self, mode: str, monitored: str = "yes") -> Dict[int, List[Dict[str, int]]]:
        """The items on a Wanted list (the monitored ones, the others, or both): {item id: its listed
        episodes as {"id", "season"}} (none for a movie)."""
        out: Dict[int, List[Dict[str, int]]] = {}
        for flag in {"yes": ("true",), "no": ("false",), "any": ("true", "false")}[monitored]:
            for r in self._pages(f"wanted/{mode}?monitored={flag}", f"its {WANTED_LIST[mode]} list"):
                if self.kind == "radarr":
                    if r.get("id"):
                        out.setdefault(int(r["id"]), [])
                elif r.get("seriesId") is not None and r.get("id"):
                    out.setdefault(int(r["seriesId"]), []).append({"id": int(r["id"]), "season": int(r.get("seasonNumber") or 0)})
        return out

    def queue_records(self) -> List[Dict[str, Any]]:
        """The arr's download queue, read once a run."""
        if self._queue is None:
            self._queue = self._pages("queue", "its download queue")
        return self._queue

    def stuck(self, downloads: set) -> Dict[str, str]:
        """Those of these downloads the queue has stuck (blocked or failed importing, or with a
        warning or an error): {download id: the arr's own reasons}."""
        said: Dict[str, List[str]] = {}
        for r in self.queue_records():
            download, state = r.get("downloadId"), str(r.get("trackedDownloadState") or "")
            if download not in downloads or (r.get("trackedDownloadStatus") not in ("warning", "error") and state not in _STUCK_STATES):
                continue
            texts = [m for group in (r.get("statusMessages") or []) if isinstance(group, dict) for m in (group.get("messages") or []) if m]
            for text in texts or [r.get("errorMessage") or _QUEUE_STATES.get(state, "stuck in the queue")]:
                if str(text) not in said.setdefault(download, []):  # a season pack is in the queue once per episode
                    said[download].append(str(text))
        return {download: "; ".join(texts)[:300] for download, texts in said.items()}

    def queued(self, now: datetime) -> Dict[int, str]:
        """The items with a download in the queue under _QUEUE_HOLD_HOURS old: {item id: "downloading, 2h in the queue"}."""
        out: Dict[int, str] = {}
        for r in self.queue_records():
            if r.get(self.id_param) is None:
                continue
            added = _when(r.get("added"))
            age = (now - added).total_seconds() / 3600 if added else 0
            if age > _QUEUE_HOLD_HOURS:
                continue
            state = _QUEUE_STATES.get(str(r.get("trackedDownloadState") or ""), "downloading")
            out.setdefault(int(r[self.id_param]), f"{state}, {age:.0f}h in the queue")
        return out

    def searches_busy(self) -> int:
        rows = self._read("command", "its running commands")
        return sum(1 for c in rows if isinstance(c, dict) and c.get("name") in _SEARCHES and c.get("status") in _BUSY) if isinstance(rows, list) else 0

    def start_search(self, body: Dict[str, Any]) -> Tuple[Optional[int], Optional[datetime]]:
        """(command id, when the arr queued it by its own clock), or (None, None) when it was not accepted."""
        resp = self.client.make_post_request(self._url("command"), body)
        if not isinstance(resp, dict) or not resp.get("id"):
            return None, None
        return int(resp["id"]), _when(resp.get("queued"))

    def files(self, media_id: int) -> List[Dict[str, Any]]:
        rows = self._read(f"{self.file_noun}?{self.id_param}={media_id}", "its file records")
        return [f for f in rows if isinstance(f, dict) and f.get("id")] if isinstance(rows, list) else []

    def episodes(self, series_id: int) -> List[Dict[str, Any]]:
        rows = self._read(f"episode?seriesId={series_id}", "the episodes")
        return [e for e in rows if isinstance(e, dict) and e.get("id")] if isinstance(rows, list) else []

    def history_since(self, since: datetime, event: str) -> Optional[List[Dict[str, Any]]]:
        """The arr's history of one event after `since`, or None when it did not answer."""
        rows = self._get(f"history/since?date={quote(_stamp(since))}&eventType={event}")
        return [h for h in rows if isinstance(h, dict) and h.get("eventType") == event] if isinstance(rows, list) else None

    def grabs_since(self, since: datetime, media_id: int) -> Optional[List[Dict[str, Any]]]:
        """What the arr grabbed for the item after `since`, or None when it did not answer."""
        rows = self.history_since(since, "grabbed")
        return None if rows is None else [h for h in rows if h.get(self.id_param) == media_id]


# ── the run ────────────────────────────────────────────────────────────────
class ArrUpgrader:
    """One Tools run over every configured instance of one arr kind (or the ones given)."""

    def __init__(self, db: Session, kind: str, *, instances: Optional[List[Dict[str, str]]] = None,
                 settings: Optional[Dict[str, Any]] = None, per_instance: Optional[Dict[str, Dict[str, Any]]] = None,
                 client_factory: Optional[Callable[[Dict[str, str]], Any]] = None, sleep: Callable[[float], None] = time.sleep,
                 notifier: Optional[Callable[..., Any]] = None, now: Optional[Callable[[], datetime]] = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown arr kind '{kind}'")
        self.db = db
        self.kind = kind
        self.label = kind.capitalize()
        self.noun = "movie" if kind == "radarr" else "series"
        self.nouns = "movies" if kind == "radarr" else "series"
        # `settings` given = the same settings for every instance (tests); else each instance's own, the defaults when not named.
        self._flat = normalize_one(settings, kind) if settings is not None else None
        self._saved = {} if settings is not None else {str(name): normalize_one(one, kind) for name, one in (per_instance or {}).items()}
        self.instances = list(instances) if instances is not None else load_arr_instances(db, kind)
        self._client_factory = client_factory or (lambda inst: _connect(inst, kind))
        self._sleep = sleep
        self._notifier = notifier
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._progress: Optional[ProgressCallback] = None
        self._done = 0
        self._total = 0
        self._message = ""
        self.results: Dict[str, Dict[str, Any]] = {}  # instance name → what happened, for the summary and the notification

    def settings_for(self, name: str) -> Dict[str, Any]:
        return self._flat if self._flat is not None else (self._saved.get(name) or normalize_one({}, self.kind))

    def count_text(self, n: int) -> str:
        return f"{n:,} {self.noun if n == 1 else self.nouns}"

    @staticmethod
    def _new_result() -> Dict[str, Any]:
        return {"error": "", "skipped": "", "stopped": "", "cut_short": "", "capped": False, "pool": 0, "untagged": 0, "restarted": False,
                "searched": [], "held": [], "failed": [], "passed": [], "tagged": 0, "lines": [],
                "followed": 0, "imported": [], "not_imported": [], "stuck": []}  # earlier runs' grabs, and what became of them

    def _tick(self, message: Optional[str] = None, advance: int = 0) -> None:
        """Report progress (and give a cancel a chance to land, also while a search is waited for)."""
        self._done += advance
        if message is not None:
            self._message = message
        if self._progress:
            self._progress("upgrade", self._done, max(self._total, 1), self._message)

    def _settings_line(self, s: Dict[str, Any]) -> str:
        parts = [f"{s['count']} {self.noun if s['count'] == 1 else self.nouns} per run", MODE_TEXT[s["mode"]]]
        if self.kind == "sonarr" and s["mode"] == "all":
            parts.append(UNIT_TEXT[s["search_unit"]] + (f" (at least {s['season_threshold']}% monitored)" if s["search_unit"] == "seasons" else ""))
        if s["monitored"] != "yes":
            parts.append(MONITORED_TEXT[s["monitored"]])
        if s["only_tags"]:
            parts.append(f"only tagged {s['only_tags']}")
        if s["quality_profiles"]:
            parts.append(f"only quality profile {s['quality_profiles']}")
        if s["min_age_days"]:
            parts.append(f"released at least {s['min_age_days']} day(s) ago")
        if s["max_grabs"]:
            parts.append(f"grab limit {s['max_grabs']}")
        parts.append(f"search tag '{s['tag_name']}'")
        if s["ignore_tag"]:
            parts.append(f"ignore tag '{s['ignore_tag']}'")
        if s["skip_queued"]:
            parts.append("downloading items wait")
        if s["title_filter"]:
            parts.append(f"titles containing '{s['title_filter']}'")
        return ", ".join(parts)

    # ── what a run leaves for the next ones ────────────────────────────────
    def _stored(self) -> Dict[str, Any]:
        try:
            stored = json.loads(get_setting_value(self.db, f"{self.kind}_upgrade_state") or "{}")
        except (json.JSONDecodeError, TypeError):
            stored = {}
        return stored if isinstance(stored, dict) else {}

    def _load_state(self, name: str) -> Dict[str, Any]:
        """One instance's grabs still followed to their import, and each item's failed runs in a row."""
        raw = self._stored().get(name) if self.db is not None else None
        raw = raw if isinstance(raw, dict) else {}
        pending = [p for p in (raw.get("pending") or []) if isinstance(p, dict) and p.get("download") and p.get("label")]
        fails = {str(k): v for k, v in (raw.get("fails") or {}).items() if isinstance(v, int)} if isinstance(raw.get("fails"), dict) else {}
        return {"pending": pending, "fails": fails}

    def _save_state(self, name: str, state: Dict[str, Any]) -> None:
        if self.db is None:
            return
        try:
            stored = self._stored()  # read again: a run of another instance may have written since
            stored[name] = {"pending": state["pending"][-_MAX_PENDING:], "fails": state["fails"]}
            upsert_setting(self.db, f"{self.kind}_upgrade_state", json.dumps(stored))
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            log_debug(LogTags.TOOLS, f"{name}: could not keep the run's state: {exc}")

    def run(self, *, dry_run: bool = False, progress: Optional[ProgressCallback] = None) -> Dict[str, Any]:
        self._progress = progress
        started = time.monotonic()
        stats = {"instances": 0, "unreachable": 0, "failed_instances": 0, "skipped": 0, "stopped": 0, "searched": 0, "grabbed": 0,
                 "grabbed_items": 0, "held": 0, "passed": 0, "failed": 0, "tagged": 0, "cycles": 0, "imported": 0, "not_imported": 0, "stuck": 0}
        mode = "dry run" if dry_run else "run"
        if not self.instances:
            log_warning(LogTags.TOOLS, f"No {self.label} instance is configured: nothing to search")
            return stats

        # One instance after the other, so each one's log lines stay together. The progress total is
        # what every instance may search at most; one that searches fewer is counted as done.
        self._total = sum(self.settings_for(i.get("name") or i.get("url", self.label))["count"] for i in self.instances)
        for instance in self.instances:
            name = instance.get("name") or instance.get("url", self.label)
            upto = self._done + self.settings_for(name)["count"]
            try:
                self._run_instance(instance, name, dry_run, stats)
            finally:
                self._done = upto
            self._tick()

        log_info(LogTags.TOOLS, f"{self.label} upgrade search ({mode}): {stats['instances']} instance(s), {stats['searched']} item(s) {'would be ' if dry_run else ''}searched"
                 + ("" if dry_run else f", {stats['grabbed']} release(s) grabbed for {stats['grabbed_items']} item(s)")
                 + (f", {stats['held']} held back" if stats["held"] else "") + (f", {stats['passed']} with nothing to search" if stats["passed"] else "")
                 + (f", {stats['failed']} failed" if stats["failed"] else "") + (f", {stats['tagged']} tagged" if stats["tagged"] else "")
                 + (f", {stats['cycles']} cycle(s) restarted" if stats["cycles"] else "")
                 + (f", {stats['imported']} earlier grab(s) imported" if stats["imported"] else "")
                 + (f", {stats['not_imported']} earlier grab(s) not imported" if stats["not_imported"] else "")
                 + (f", {stats['stuck']} stuck in the queue" if stats["stuck"] else "")
                 + (f", {stats['skipped']} instance(s) skipped as busy" if stats["skipped"] else "")
                 + (f", {stats['stopped']} instance(s) stopped" if stats["stopped"] else "")
                 + (f", {stats['unreachable']} instance(s) unreachable" if stats["unreachable"] else "")
                 + (f", {stats['failed_instances']} instance(s) failed" if stats["failed_instances"] else "")
                 + f", {time.monotonic() - started:.1f}s")
        self._notify(dry_run)
        return stats

    def _run_instance(self, instance: Dict[str, str], name: str, dry_run: bool, stats: Dict[str, Any]) -> None:
        """Pick and search on one instance. One that fails is reported; the others still run."""
        started = time.monotonic()
        s = self.settings_for(name)
        result = self.results[name] = self._new_result()
        log_info(LogTags.TOOLS, f"── {name} ({self.label}) ── {'dry run' if dry_run else 'run'}: {self._settings_line(s)}")
        problem = settings_problem(s)
        if problem:
            stats["failed_instances"] += 1
            result["error"] = f"did not start: {problem}"
            log_warning(LogTags.TOOLS, f"{name}: not starting: {problem}")
            return
        try:
            client = self._client_factory(instance)
        except Exception as exc:
            log_debug(LogTags.TOOLS, f"{name}: connection failed: {exc}")
            client = None
        if client is None:
            stats["unreachable"] += 1
            result["error"] = "cannot connect"
            log_warning(LogTags.TOOLS, f"Cannot connect to {self.label} '{name}': skipping it")
            return
        plan = _InstanceSearch(self, _SearchArr(client, self.kind), name, s, dry_run, stats, result)
        try:
            plan.follow()
            plan.select()
            if result["skipped"]:
                stats["skipped"] += 1
            else:
                stats["instances"] += 1
                self._tick(f"{self.label} upgrade search: {name}")
                plan.search()
        except _Stopped as exc:
            if not plan.selected:  # nothing was picked yet: the run did not get going
                stats["failed_instances"] += 1
                result["error"] = str(exc)
                log_warning(LogTags.TOOLS, f"{name}: run failed: {exc}")
                return
            stats["stopped"] += 1
            result["stopped"] = str(exc)
            log_warning(LogTags.TOOLS, f"{name}: stopping: {exc}. What was not searched stays untagged for the next run")
        except Exception as exc:
            stats["failed_instances"] += 1
            result["error"] = str(exc) or exc.__class__.__name__
            log_warning(LogTags.TOOLS, f"{name}: run failed: {exc}")
            return
        plan.finish()
        for line in result["lines"]:
            log_info(LogTags.TOOLS, f"{name}: {line}")
        log_info(LogTags.TOOLS, f"{name}: finished in {time.monotonic() - started:.1f}s")

    # ── notification: one summary per run ──────────────────────────────────
    @staticmethod
    def _field(name: str, value: str) -> Dict[str, Any]:
        return {"name": name, "value": value if len(value) <= 1000 else value[:996] + "\n…", "inline": False}

    @staticmethod
    def _grab_block(item: Dict[str, Any], grab: Dict[str, Any]) -> str:
        """One grab, as the renamer shows a rename: the item in bold over a diff code block with what
        it replaces on a - line and the grab on a + line, which Discord shows in red and green, and
        the release under them."""
        head = item["label"] + (f" · {grab['episodes']}" if grab["episodes"] else "")
        body = "\n".join(line.replace("`", "'") for line in (f"- {grab['before']}", f"+ {grab['after']}", f"  {grab['release']}"))
        return f"**{head.replace('`', chr(39))}**\n```diff\n{body}\n```"

    @staticmethod
    def _followed_name(grab: Dict[str, Any]) -> str:
        return grab["label"] + (f" ({grab['episodes']})" if grab.get("episodes") else "")

    def _notify(self, dry_run: bool) -> None:
        fields: List[Dict[str, Any]] = []
        reasons: List[str] = []  # what a person has to look at; any makes this an error event, so the mention settings for errors decide on a ping
        blocks: List[str] = []
        good_news = False  # something was grabbed, or an earlier grab imported
        for name, result in self.results.items():
            s = self.settings_for(name)
            if not s.get("notify"):
                continue
            grabs = [(item, grab) for item in result["searched"] for grab in item["grabs"]]
            imported, lost, stuck = result["imported"], result["not_imported"], result["stuck"]
            newly_stuck = [g for g in stuck if g["new"]]  # an error once; later runs only list it
            needs_look = bool(result["error"] or result["stopped"] or result["failed"] or lost or newly_stuck)
            if s.get("notify_grabs_only") and not grabs and not imported and not needs_look:
                continue
            if result["error"]:
                reasons.append(f"{name}: the run failed")
                fields.append(self._field(f"{name}: run failed", result["error"]))
                continue
            if imported:
                good_news = True
                fields.append(self._field(f"{name}: {len(imported)} earlier grab(s) imported",
                                          "\n".join(f"{self._followed_name(g)}: {g['before']} → {g['imported']}" for g in imported)))
            if lost:
                reasons.append(f"{name}: {len(lost)} earlier grab(s) did not import")
                fields.append(self._field(f"{name}: {len(lost)} did not import", "\n".join(f"{self._followed_name(g)}: {g['release']}: {g['why']}" for g in lost)))
            if stuck:
                if newly_stuck:
                    reasons.append(f"{name}: {len(newly_stuck)} grab(s) stuck in the queue")
                fields.append(self._field(f"{name}: {len(stuck)} stuck in the queue", "\n".join(f"{self._followed_name(g)}: {g['release']}: {g['why']}" for g in stuck)))
            if result["skipped"]:
                fields.append(self._field(f"{name}: skipped", result["skipped"]))
                continue
            if result["stopped"]:
                reasons.append(f"{name}: the run stopped")
                fields.append(self._field(f"{name}: run stopped", result["stopped"]))
            fields.append(self._field(f"{name}: settings", self._settings_line(s) + (", dry run" if dry_run else "")))
            if result["searched"]:
                fields.append(self._field(f"{name}: {'would search' if dry_run else 'searched'} {len(result['searched'])}",
                                          "\n".join(i["label"] + (f": {i['what']}" if i["what"] else "") for i in result["searched"])))
            if grabs:
                good_news = True
                blocks.append(f"**{name}: {len(grabs)} grabbed**")
                blocks.extend(self._grab_block(item, grab) for item, grab in grabs)
            if result["failed"]:
                reasons.append(f"{name}: {len(result['failed'])} search(es) failed")
                fields.append(self._field(f"{name}: {len(result['failed'])} failed", "\n".join(f"{i['label']}: {i['why']}" for i in result["failed"])))
            if result["held"]:
                fields.append(self._field(f"{name}: {len(result['held'])} held back", "\n".join(f"{i['label']}: {i['why']}" for i in result["held"])))
            if result["lines"]:
                fields.append(self._field(f"{name}: this run", "\n".join(result["lines"])))
        if not fields and not blocks:
            return
        send = self._notifier
        if send is None:
            from services.notifications import send_notification

            send = lambda **kw: send_notification(self.db, **kw)  # noqa: E731
        # An error event pings per the feature's mention settings; a grab or an import is a success; anything else only informs.
        event = "error" if reasons else "success" if good_news and not dry_run else "info"
        color = {"error": 0xE74C3C, "success": 0x2ECC71, "info": 0x3498DB}[event]
        parts = ["Needs a look: " + "; ".join(reasons)] if reasons else []
        shown, used = [], 0
        for block in blocks:
            if shown and used + len(block) + 2 > _GRAB_CHARS:
                break
            shown.append(block)
            used += len(block) + 2
        if shown:
            left = len(blocks) - len(shown)
            parts.append("\n\n".join(shown) + (f"\n\n…{left} more not shown. The Tools log lists every grab." if left else ""))
        try:
            send(feature_key=NOTIFY_FEATURE, event_type=event, title=f"{self.label} upgrade search" + (" (dry run)" if dry_run else ""),
                 description="\n\n".join(parts), fields=fields, color=color)
        except Exception as exc:
            log_debug(LogTags.TOOLS, f"{self.label} upgrade search: notification failed: {exc}")


class _InstanceSearch:
    """The work on one instance: pick what this run searches, search each item in turn, report
    what the arr grabbed, tag what was searched, and sum the run up."""

    def __init__(self, owner: ArrUpgrader, arr: _SearchArr, name: str, s: Dict[str, Any], dry_run: bool,
                 stats: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.owner, self.arr, self.name, self.s, self.dry_run, self.stats, self.result = owner, arr, name, s, dry_run, stats, result
        self.candidates: List[Dict[str, Any]] = []  # untagged pool items, in title order
        self.held: Dict[int, str] = {}  # item id → why its download holds it back
        self.wanted: Optional[Dict[int, List[Dict[str, int]]]] = None  # mode cutoff / missing: the list's items with their episodes
        self.tag: Optional[int] = None
        self.selected = False  # select() got through: there is a pool to search
        self.started = 0   # items a search was started for (a dry run: would be)
        self.commands = 0  # searches sent
        self.fails = 0     # failed searches in a row
        self.grabbed_items = 0  # items that got a release this run, for the grab limit
        self.state = owner._load_state(name)
        self.state_changed = False
        self._episodes: Dict[int, List[Dict[str, Any]]] = {}

    # ── earlier runs' grabs ────────────────────────────────────────────────
    def follow(self) -> None:
        """Say what became of the grabs of earlier runs: imported, failed, given up on after
        _FOLLOW_DAYS, or stuck in the arr's queue. A dry run looks and forgets nothing."""
        pending = self.state["pending"]
        if not pending:
            return
        arr, name, result, stats = self.arr, self.name, self.result, self.stats
        now = self.owner._now()
        # From a minute before the earliest grab's search, by the arr's own clock, as its history is dated.
        since = min((_when(p.get("since")) or now) for p in pending) - timedelta(seconds=60)
        found: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for event in ("downloadFolderImported", "downloadFailed"):
            rows = arr.history_since(since, event)
            if rows is None:
                log_warning(LogTags.TOOLS, f"{name}: could not read whether earlier grabs imported ({self.owner.label} did not answer)")
                return
            found[event] = {}
            for row in rows:
                if row.get("downloadId"):
                    found[event].setdefault(str(row["downloadId"]), row)
        result["followed"] = len(pending)
        still: List[Dict[str, Any]] = []
        for p in pending:
            what = ArrUpgrader._followed_name(p)
            done, failed = found["downloadFolderImported"].get(p["download"]), found["downloadFailed"].get(p["download"])
            if done:
                stats["imported"] += 1
                result["imported"].append({**p, "imported": describe([done])})
                log_info(LogTags.TOOLS, f"{name}: IMPORTED {what}: {p['release']}: {p.get('before') or 'no file'} → {describe([done])}")
            elif failed or now.timestamp() - float(p.get("t") or 0) > _FOLLOW_DAYS * 86400:
                why = str((failed.get("data") or {}).get("message") or "the download failed")[:200] if failed else f"not imported after {_FOLLOW_DAYS} days"
                stats["not_imported"] += 1
                result["not_imported"].append({**p, "why": why})
                log_warning(LogTags.TOOLS, f"{name}: NOT IMPORTED {what}: {p['release']}: {why}")
            else:
                still.append(p)
        changed = len(still) != len(pending)
        try:
            reasons = arr.stuck({p["download"] for p in still}) if still else {}
        except _Stopped as exc:
            log_warning(LogTags.TOOLS, f"{name}: could not read the download queue ({exc})")
            reasons = {}
        for p in still:
            why = reasons.get(p["download"])
            if not why:
                continue
            stats["stuck"] += 1
            result["stuck"].append({**p, "why": why, "new": not p.get("stuck")})
            log_warning(LogTags.TOOLS, f"{name}: STUCK {ArrUpgrader._followed_name(p)}: {p['release']}: {why}")
            if not p.get("stuck"):
                p["stuck"] = True
                changed = True
        if changed and not self.dry_run:
            self.state["pending"] = still
            self.owner._save_state(name, self.state)

    # ── picking ────────────────────────────────────────────────────────────
    def select(self) -> None:
        arr, s, name, result, owner = self.arr, self.s, self.name, self.result, self.owner
        busy = arr.searches_busy()
        if busy >= _MAX_BUSY:
            if not self.dry_run:
                result["skipped"] = f"{owner.label} already has {busy} searches queued or running. The next run searches."
                log_info(LogTags.TOOLS, f"{name}: skipped: {owner.label} already has {busy} searches queued or running; the next run searches")
                return
            log_info(LogTags.TOOLS, f"{name}: {owner.label} has {busy} searches queued or running; a real run would be skipped")
        library = arr.library()
        log_info(LogTags.TOOLS, f"{name}: {owner.count_text(len(library))} in the library")
        today = owner._now().date()
        here = {str(m["id"]) for m in library}
        self.state["fails"] = {key: n for key, n in self.state["fails"].items() if key in here}  # an item gone from the arr has no count to keep
        monitored = s["monitored"]
        kept = [m for m in library if monitored == "any" or bool(m.get("monitored")) == (monitored == "yes")]
        pool = [m for m in kept if is_out(m, arr.kind, today)]
        left_out = ([f"{len(library) - len(kept):,} {'monitored' if monitored == 'no' else 'not monitored'}"] if monitored != "any" else []) + [f"{len(kept) - len(pool):,} not out yet"]
        log_info(LogTags.TOOLS, f"{name}: {len(pool):,} {MONITORED_TEXT[monitored]} and out ({', '.join(left_out)})")
        if s["title_filter"]:
            needle = s["title_filter"].lower()
            kept = [m for m in pool if needle in str(m.get("title") or "").lower()]
            log_info(LogTags.TOOLS, f"{name}: title filter '{s['title_filter']}' matches {len(kept):,} of {len(pool):,}")
            pool = kept
        if s["ignore_tag"]:
            # The tag is created when missing (not on a dry run), so it can be picked in the arr straight away.
            ignore_id = arr.tag_id(s["ignore_tag"], create=not self.dry_run)
            if ignore_id is not None:
                kept = [m for m in pool if ignore_id not in (m.get("tags") or [])]
                if len(kept) != len(pool):
                    log_info(LogTags.TOOLS, f"{name}: leaving out {len(pool) - len(kept):,} tagged '{s['ignore_tag']}'")
                pool = kept
        only_tags = names_of(s["only_tags"])
        if only_tags:
            known = arr.tag_ids()
            unknown = [tag for tag in only_tags if tag.lower() not in known]
            if unknown:
                raise _Stopped(f"{owner.label} has no tag '{unknown[0]}'. " + (f"Its tags: {', '.join(sorted(known))}" if known else "It has no tags"))
            wanted_ids = {known[tag.lower()] for tag in only_tags}
            kept = [m for m in pool if wanted_ids & set(m.get("tags") or [])]
            log_info(LogTags.TOOLS, f"{name}: {len(kept):,} of {len(pool):,} carry the tag {' or '.join(repr(tag) for tag in only_tags)}")
            pool = kept
        profiles = names_of(s["quality_profiles"])
        if profiles:
            having = arr.quality_profiles()
            known = {profile.lower(): pid for profile, pid in having.items()}
            unknown = [profile for profile in profiles if profile.lower() not in known]
            if unknown:
                raise _Stopped(f"{owner.label} has no quality profile '{unknown[0]}'. Its profiles: {', '.join(sorted(having))}")
            wanted_ids = {known[profile.lower()] for profile in profiles}
            kept = [m for m in pool if m.get("qualityProfileId") in wanted_ids]
            log_info(LogTags.TOOLS, f"{name}: {len(kept):,} of {len(pool):,} use the quality profile {' or '.join(repr(profile) for profile in profiles)}")
            pool = kept
        if s["min_age_days"]:
            latest = today - timedelta(days=s["min_age_days"])
            dated = [(m, released(m, arr.kind)) for m in pool]
            kept = [m for m, out_on in dated if out_on is not None and out_on <= latest]
            undated = sum(1 for _, out_on in dated if out_on is None)  # one with no date cannot be known to be old enough
            log_info(LogTags.TOOLS, f"{name}: {len(kept):,} of {len(pool):,} released at least {s['min_age_days']} day(s) ago "
                     f"({len(pool) - len(kept) - undated:,} newer, {undated:,} with no release date)")
            pool = kept
        if s["mode"] != "all":
            self.wanted = arr.wanted(s["mode"], monitored)
            listed = sum(len(v) for v in self.wanted.values())
            log_info(LogTags.TOOLS, f"{name}: {owner.count_text(len(self.wanted))} on the {WANTED_LIST[s['mode']]} list"
                     + (f" ({listed:,} episode(s))" if arr.kind == "sonarr" else ""))
            pool = [m for m in pool if int(m["id"]) in self.wanted]
        result["pool"] = len(pool)
        self.tag = arr.tag_id(s["tag_name"], create=not self.dry_run)
        if self.tag is None and not self.dry_run:
            raise _Stopped(f"could not create the search tag '{s['tag_name']}'")
        untagged = [m for m in pool if self.tag is None or self.tag not in (m.get("tags") or [])]
        if pool and not untagged:
            self.stats["cycles"] += 1
            result["restarted"] = True
            log_info(LogTags.TOOLS, f"{name}: all {len(pool):,} carry '{s['tag_name']}': the cycle is complete. "
                     + ("A real run would clear the tags and start over" if self.dry_run else "Clearing the tags to start over"))
            if not self.dry_run and not arr.set_tags([int(m["id"]) for m in pool], self.tag, add=False):
                raise _Stopped(f"could not clear the search tag '{s['tag_name']}'")
            untagged = pool
        else:
            log_info(LogTags.TOOLS, f"{name}: {len(untagged):,} of {len(pool):,} not searched yet this cycle")
        result["untagged"] = len(untagged)
        untagged.sort(key=lambda m: (str(m.get("sortTitle") or m.get("title") or "").lower(), m.get("year") or 0, m["id"]))
        self.candidates = untagged
        if s["skip_queued"] and untagged:
            self.held = arr.queued(owner._now())
            waiting = [f"{item_label(m)} ({self.held[int(m['id'])]})" for m in untagged if int(m["id"]) in self.held]
            if waiting:
                log_info(LogTags.TOOLS, f"{name}: {owner.count_text(len(waiting))} not searched yet with a download in the queue: "
                         + ", ".join(waiting[:10]) + (f" and {len(waiting) - 10} more" if len(waiting) > 10 else ""))
        if not untagged:
            log_info(LogTags.TOOLS, f"{name}: nothing to search")
        self.selected = True

    def _episodes_of(self, series_id: int) -> List[Dict[str, Any]]:
        if series_id not in self._episodes:
            self._episodes[series_id] = self.arr.episodes(series_id)
        return self._episodes[series_id]

    def _plan(self, item: Dict[str, Any]) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
        """The searches for one item as (what it covers, the arr command), and why seasons were left out."""
        media_id = int(item["id"])
        if self.arr.kind == "radarr":
            return [("the movie", {"name": "MoviesSearch", "movieIds": [media_id]})], []
        if self.wanted is not None:
            # As the arr's own Wanted search does it: a season search for a season with several listed episodes, one episode search for the rest.
            seasons: Dict[int, List[int]] = {}
            for episode in self.wanted.get(media_id, []):
                seasons.setdefault(episode["season"], []).append(episode["id"])
            searches = [(f"season {n} ({len(ids)} listed episodes)", {"name": "SeasonSearch", "seriesId": media_id, "seasonNumber": n})
                        for n, ids in sorted(seasons.items()) if len(ids) > 1]
            singles = [ids[0] for _, ids in sorted(seasons.items()) if len(ids) == 1]
            if singles:
                searches.append((f"{len(singles)} listed episode{'s' if len(singles) != 1 else ''}", {"name": "EpisodeSearch", "episodeIds": singles}))
            return searches, []
        if self.s["search_unit"] == "series":
            return [("the whole series", {"name": "SeriesSearch", "seriesId": media_id})], []
        by_season: Dict[int, List[Dict[str, Any]]] = {}
        for episode in self._episodes_of(media_id):
            by_season.setdefault(int(episode.get("seasonNumber") or 0), []).append(episode)
        now = self.owner._now()
        searches, notes = [], []
        unmonitored: List[int] = []
        for season in sorted((x for x in (item.get("seasons") or []) if isinstance(x, dict)), key=lambda x: x.get("seasonNumber") or 0):
            n = int(season.get("seasonNumber") or 0)
            episodes = by_season.get(n, [])
            if n == 0 or not episodes:  # specials are not searched by season
                continue
            if not season.get("monitored"):
                unmonitored.append(n)
                continue
            watched = sum(1 for e in episodes if e.get("monitored"))
            share = watched * 100 / len(episodes)
            if not watched or share < self.s["season_threshold"]:
                notes.append(f"season {n} left out, {watched} of {len(episodes)} episodes monitored ({share:.0f}%, under {self.s['season_threshold']}%)")
            elif not any(e.get("hasFile") or ((aired := _when(e.get("airDateUtc"))) and aired <= now) for e in episodes):
                notes.append(f"season {n} left out, nothing aired yet")
            else:
                searches.append((f"season {n}", {"name": "SeasonSearch", "seriesId": media_id, "seasonNumber": n}))
        if unmonitored:
            notes.append(f"{seasons_text(unmonitored)} not monitored")
        return searches, notes

    # ── searching ──────────────────────────────────────────────────────────
    def search(self) -> None:
        name, result, stats = self.name, self.result, self.stats
        passed: List[int] = []
        try:
            for item in self.candidates:
                if self.started >= self.s["count"]:
                    break
                if self.s["max_grabs"] and self.grabbed_items >= self.s["max_grabs"]:
                    result["capped"] = True
                    log_info(LogTags.TOOLS, f"{name}: grab limit {self.s['max_grabs']} reached; the rest waits for the next runs")
                    break
                label = item_label(item)
                why = self.held.get(int(item["id"]))
                if why:
                    stats["held"] += 1
                    result["held"].append({"label": label, "why": why})
                    log_info(LogTags.TOOLS, f"{name}: HELD BACK {label}: {why}. It stays untagged for a later run")
                    continue
                searches, notes = self._plan(item)
                noted = f" ({'; '.join(notes)})" if notes else ""
                if not searches:
                    stats["passed"] += 1
                    passed.append(int(item["id"]))
                    result["passed"].append({"label": label, "why": "; ".join(notes) or "no season to search"})
                    log_info(LogTags.TOOLS, f"{name}: NOTHING TO SEARCH {label}: {'; '.join(notes) or 'no season to search'}. "
                             + ("It would be tagged" if self.dry_run else "Tagged") + " so the cycle moves on")
                    continue
                self.started += 1
                what = ", ".join(w for w, _ in searches)
                row = {"label": label, "what": "" if self.arr.kind == "radarr" else what, "grabs": []}
                prefix = f"{name}: [{self.started}/{self.s['count']}] {label}"
                if self.dry_run:
                    stats["searched"] += 1
                    result["searched"].append(row)
                    log_info(LogTags.TOOLS, f"{prefix}: WOULD SEARCH {what}{noted}")
                    self.owner._tick(f"{self.owner.label} upgrade search: {label}", advance=1)
                    continue
                self.owner._tick(f"{self.owner.label} upgrade search: {label}")
                log_info(LogTags.TOOLS, f"{prefix}: SEARCH {what}{noted}")
                done = self._search_item(item, label, searches, row, prefix)
                self.owner._tick(advance=1)
                if not done:
                    break
        finally:
            # One request for every item checked without a search; kept when the run stops on the way.
            if passed and not self.dry_run:
                if self.arr.set_tags(passed, self.tag, add=True):
                    result["tagged"] += len(passed)
                    stats["tagged"] += len(passed)
                else:
                    log_warning(LogTags.TOOLS, f"{name}: could not tag {len(passed)} item(s) with nothing to search; they are checked again next run")
            if self.state_changed and not self.dry_run:
                self.owner._save_state(name, self.state)

    def _pause(self) -> None:
        self.owner._sleep(_SEARCH_GAP)
        self.owner._tick()

    def _wait(self, command_id: int) -> Tuple[str, int]:
        """How the search ended ("timeout" while it is still going after _SEARCH_WAIT), and the seconds waited."""
        waited = 0
        status = self.arr.command_status(command_id)
        while status in _BUSY and waited < _SEARCH_WAIT:
            self.owner._sleep(_POLL)
            waited += _POLL
            self.owner._tick()
            status = self.arr.command_status(command_id)
        return ("timeout" if status in _BUSY else status or "unknown"), waited

    def _search_item(self, item: Dict[str, Any], label: str, searches: List[Tuple[str, Dict[str, Any]]], row: Dict[str, Any], prefix: str) -> bool:
        """Search one item, report its grabs and tag it. False = the run stops here for this instance."""
        arr, result, stats = self.arr, self.result, self.stats
        media_id = int(item["id"])
        # Read before the search: a grab can import while the search is still going.
        files = arr.files(media_id)
        episodes = self._episodes_of(media_id) if arr.kind == "sonarr" else []
        log_info(LogTags.TOOLS, f"{prefix}: has {describe(files)}")
        since: Optional[datetime] = None
        status, problem = "completed", ""
        for what, body in searches:
            if self.commands:
                self._pause()
            self.commands += 1
            asked = self.owner._now()
            command_id, queued = arr.start_search(body)
            if command_id is None:
                status, problem = "refused", f"{self.owner.label} did not accept the search of {what}"
                break
            since = min(since, queued or asked) if since else (queued or asked)
            status, waited = self._wait(command_id)
            if status == "completed":
                log_info(LogTags.TOOLS, f"{prefix}: search of {what} finished after {waited}s")
            elif status == "timeout":
                break
            else:
                problem = f"the search of {what} ended {status} in {self.owner.label} (see System, Tasks there)"
                break

        if since is not None:
            # From just before the arr queued the first search, by its own clock, as its history is dated.
            grabs = arr.grabs_since(since - timedelta(seconds=2), media_id)
            if grabs is None:
                log_warning(LogTags.TOOLS, f"{prefix}: could not read what was grabbed ({self.owner.label} did not answer)")
            else:
                row["grabs"] = grab_rows(arr.kind, grabs, files, episodes)
        for grab in row["grabs"]:
            log_info(LogTags.TOOLS, f"{prefix}: GRABBED {grab['release']}" + (f" ({grab['episodes']})" if grab["episodes"] else "")
                     + f": {grab['before']} → {grab['after']}" + (f" [custom formats: {', '.join(grab['formats'])}]" if grab["formats"] else ""))
        if row["grabs"]:
            stats["grabbed"] += len(row["grabs"])
            stats["grabbed_items"] += 1
            self.grabbed_items += 1
            # Each grab with a download is followed by the next runs, to say whether it imported.
            taken = self.owner._now().timestamp()
            self.state["pending"] += [{"id": media_id, "label": label, "release": grab["release"], "episodes": grab["episodes"], "before": grab["before"],
                                       "after": grab["after"], "download": grab["download"], "since": _stamp(since), "t": taken}
                                      for grab in row["grabs"] if grab["download"]]
            self.state_changed = True

        if status not in ("completed", "timeout"):
            self.fails += 1
            stats["failed"] += 1
            runs = self.state["fails"][str(media_id)] = self.state["fails"].get(str(media_id), 0) + 1
            self.state_changed = True
            if runs >= _GIVE_UP_RUNS and arr.set_tags([media_id], self.tag, add=True):
                # Tagged anyway: a search that always fails must not keep the cycle from ending.
                del self.state["fails"][str(media_id)]
                result["tagged"] += 1
                stats["tagged"] += 1
                after = f"It failed {_GIVE_UP_RUNS} runs in a row: tagged anyway so the cycle can end, and searched again next cycle"
            else:
                after = f"Failed run {min(runs, _GIVE_UP_RUNS)} of {_GIVE_UP_RUNS} in a row. It stays untagged, so the next run tries again"
            result["failed"].append({"label": label, "why": f"{problem}. {after}"})
            if row["grabs"]:
                result["searched"].append(row)
            log_warning(LogTags.TOOLS, f"{prefix}: FAILED: {problem}. {after}")
            if self.fails >= _GIVE_UP_FAILS:
                raise _Stopped(f"{_GIVE_UP_FAILS} searches in a row failed")
            return True
        self.fails = 0
        if self.state["fails"].pop(str(media_id), None) is not None:
            self.state_changed = True
        stats["searched"] += 1
        result["searched"].append(row)
        if status == "completed" and not row["grabs"]:
            log_info(LogTags.TOOLS, f"{prefix}: nothing grabbed")
        if arr.set_tags([media_id], self.tag, add=True):
            result["tagged"] += 1
            stats["tagged"] += 1
        else:
            log_warning(LogTags.TOOLS, f"{prefix}: could not put the tag '{self.s['tag_name']}' on; it is searched again next run")
        if status == "timeout":
            result["cut_short"] = f"the search of {label} was still going after {_SEARCH_WAIT // 60} minutes"
            log_warning(LogTags.TOOLS, f"{prefix}: still searching after {_SEARCH_WAIT // 60} minutes. {self.owner.label} finishes it on its own; "
                        "what it grabs from here on is not in this report. The run stops here and the rest waits for the next run")
            return False
        return True

    # ── the instance's summary lines ───────────────────────────────────────
    def finish(self) -> None:
        result, s, lines, owner = self.result, self.s, self.result["lines"], self.owner
        searched = self.started - len(result["failed"])
        grabs = sum(len(i["grabs"]) for i in result["searched"])
        if result["skipped"]:
            pass  # nothing was picked: only the earlier grabs are summed up
        elif self.dry_run:
            lines.append(f"Would search {owner.count_text(searched)}")
        else:
            lines.append(f"Searched {owner.count_text(searched)}, {grabs:,} release{'s' if grabs != 1 else ''} grabbed")
        pool = result["pool"]
        if pool:
            through = searched + len(result["passed"]) if self.dry_run else result["tagged"]  # a dry run: what it would tag
            done = min(pool - result["untagged"] + through, pool)
            left = pool - done
            if left:
                runs = -(-left // s["count"])
                lines.append(f"Cycle: {done:,} of {pool:,} searched, {left:,} left, about {runs:,} more run{'s' if runs != 1 else ''} at {s['count']} a run")
            else:
                lines.append(f"Cycle: all {pool:,} searched; the next run starts over")
        if result["held"]:
            lines.append(f"Held back: {len(result['held']):,} with a download in the queue")
        if result["passed"]:
            lines.append(f"Nothing to search: {len(result['passed']):,} (no season with enough monitored episodes)")
        if result["failed"]:
            lines.append(f"Failed: {len(result['failed']):,} (see the log)")
        if result["capped"]:
            lines.append(f"Grab limit {s['max_grabs']:,} reached; the rest waits for the next runs")
        if result["cut_short"]:
            lines.append(f"Stopped early: {result['cut_short']}. The rest waits for the next run")
        if result["followed"]:
            imported, lost = len(result["imported"]), len(result["not_imported"])
            lines.append(f"Earlier grabs: {imported:,} of {result['followed']:,} imported" + (f", {lost:,} not imported" if lost else "")
                         + f", {result['followed'] - imported - lost:,} still waiting" + (f" ({len(result['stuck']):,} stuck in the queue)" if result["stuck"] else ""))


# ── what a filter can pick from, for the Tools page ────────────────────────
def arr_options(db: Session, kind: str, instance: str) -> Dict[str, Any]:
    """One instance's tags and quality profiles, each with how many of its items carry or use it."""
    found = next((i for i in load_arr_instances(db, kind) if (i["name"] or i["url"]) == instance), None)
    if found is None:
        raise LookupError(f"No {kind.capitalize()} instance named '{instance}'.")
    try:
        client = _connect(found, kind)
    except Exception as exc:
        log_debug(LogTags.TOOLS, f"{instance}: connection failed: {exc}")
        client = None
    if client is None:
        raise ConnectionError(f"Cannot connect to {kind.capitalize()} '{instance}'.")
    arr = _SearchArr(client, kind)
    try:
        tags, profiles, library = arr.tag_ids(), arr.quality_profiles(), arr.library()
    except _Stopped as exc:
        raise ConnectionError(f"{instance}: {exc}.") from None
    carrying = Counter(tag for item in library for tag in (item.get("tags") or []))
    using = Counter(item.get("qualityProfileId") for item in library)
    return {
        "instance": instance,
        "tags": [{"name": label, "count": carrying.get(tag_id, 0)} for label, tag_id in sorted(tags.items())],
        "quality_profiles": [{"name": name, "count": using.get(profile_id, 0)} for name, profile_id in sorted(profiles.items(), key=lambda kv: kv[0].lower())],
    }

