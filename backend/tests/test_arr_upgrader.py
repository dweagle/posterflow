"""The Sonarr/Radarr upgrade search tool: a run picks the next monitored items that are out and
not searched yet this cycle, has the arr search each one in turn, reports what it grabbed
against the files it replaces, tags what it searched, and starts over once all are tagged.
Later runs say what became of each grab."""

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from models.setting import get_setting_value

from services import arr_upgrader
from services.arr_upgrader import ArrUpgrader, describe, episodes_text, grab_rows, names_of, normalize_one, released, seasons_text, settings_problem

INSTANCE = {"name": "arr-1", "url": "http://arr", "api_key": "k"}
NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(at):
    return at.replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _quality(name, score=None):
    return {"quality": {"quality": {"name": name}}, **({"customFormatScore": score} if score is not None else {})}


class FakeArr:
    """A Sonarr/Radarr at the request level: the v3 routes an upgrade search uses. A search
    command 'grabs' the releases listed for its item, as the arr's history would show them."""

    def __init__(self, kind, items, *, tags=(), wanted=None, unmonitored_wanted=None, queue=(), running=(), files=None, episodes=None, grabs=None,
                 fail=(), stuck=(), refuse=(), no_tags=False, profiles=None):
        self.url = "http://arr"
        self.kind = kind
        self.noun = "movie" if kind == "radarr" else "series"
        self.id_param = "movieId" if kind == "radarr" else "seriesId"
        self.items = {i["id"]: dict(i) for i in items}
        self.tags = {label: n + 1 for n, label in enumerate(tags)}
        self.wanted = dict(wanted or {})  # "cutoff" / "missing" → records of what is monitored
        self.unmonitored_wanted = dict(unmonitored_wanted or {})  # and of what is not
        self.profiles = dict(profiles or {})  # quality profile name → id
        self.queue = list(queue)
        self.running = list(running)  # commands of something else, queued or started
        self.files = dict(files or {})  # item id → file records
        self.episodes = dict(episodes or {})  # series id → episode records
        self.grabs = dict(grabs or {})  # item id → history records a search of it leaves
        self.fail, self.stuck, self.refuse = set(fail), set(stuck), set(refuse)  # item ids whose search fails, never ends, is not accepted
        self.no_tags = no_tags  # the arr refuses to make a tag
        self.commands, self.statuses, self.puts, self.history, self.reads = [], {}, [], [], []
        self.clock = NOW
        self.down = False

    def make_get_request(self, endpoint):
        url = urlparse(endpoint)
        path = url.path.split("/api/v3/", 1)[1]
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.reads.append(path)
        if self.down:
            return None
        if path == "tag":
            return [{"id": tid, "label": label} for label, tid in self.tags.items()]
        if path == self.noun:
            return [dict(i) for i in self.items.values()]
        if path == "qualityprofile":
            return [{"id": pid, "name": name} for name, pid in self.profiles.items()]
        if path.startswith("wanted/"):
            records = (self.wanted if q["monitored"] == "true" else self.unmonitored_wanted).get(path.split("/")[1], [])
            page, size = int(q["page"]), int(q["pageSize"])
            return {"page": page, "pageSize": size, "totalRecords": len(records), "records": records[(page - 1) * size:page * size]}
        if path == "queue":
            return {"page": 1, "records": list(self.queue) if q["page"] == "1" else []}
        if path == "command":
            own = [{"id": cid, "name": c["name"], "status": self.statuses[cid]} for cid, c in enumerate(self.commands, 1)]
            return own + [dict(c) for c in self.running]
        if path.startswith("command/"):
            cid = int(path.split("/")[1])
            return {"id": cid, "name": self.commands[cid - 1]["name"], "status": self.statuses[cid]}
        if path in ("moviefile", "episodefile"):
            return list(self.files.get(int(q[self.id_param]), []))
        if path == "episode":
            return list(self.episodes.get(int(q["seriesId"]), []))
        if path == "history/since":
            since = datetime.fromisoformat(q["date"].replace("Z", "+00:00"))
            return [h for h in self.history if h["eventType"] == q["eventType"] and datetime.fromisoformat(h["date"].replace("Z", "+00:00")) > since]
        raise AssertionError(f"unexpected GET {path}")

    def make_post_request(self, endpoint, json=None):
        path = endpoint.split("/api/v3/", 1)[1]
        if path == "tag":
            if self.no_tags:
                return None
            self.tags[json["label"]] = len(self.tags) + 1
            return {"id": self.tags[json["label"]], "label": json["label"]}
        if path == "command":
            target = (json.get("movieIds") or [json.get("seriesId")])[0]
            if target in self.refuse:
                return None
            self.commands.append(json)
            cid = len(self.commands)
            self.statuses[cid] = "started" if target in self.stuck else "failed" if target in self.fail else "completed"
            if self.statuses[cid] == "completed" and (json["name"] != "SeasonSearch" or json["seasonNumber"] == 1):
                self.history += [{"eventType": "grabbed", "date": _iso(self.clock), **g} for g in self.grabs.get(target, [])]
            return {"id": cid, "name": json["name"], "status": self.statuses[cid], "queued": _iso(self.clock)}
        raise AssertionError(f"unexpected POST {path}")

    def make_put_request(self, endpoint, json=None):
        path = endpoint.split("/api/v3/", 1)[1]
        assert path == f"{self.noun}/editor"
        self.puts.append(json)
        for mid in json[f"{self.noun}Ids"]:
            tags = set(self.items[mid].get("tags") or [])
            tags = tags | set(json["tags"]) if json["applyTags"] == "add" else tags - set(json["tags"])
            self.items[mid]["tags"] = sorted(tags)
        return {}

    def tagged(self, label):
        return sorted(i["id"] for i in self.items.values() if self.tags.get(label) in (i.get("tags") or []))


def _movie(mid, title, year=2020, *, tags=(), monitored=True, has_file=True, available=True, **more):
    return {"id": mid, "title": title, "sortTitle": title.lower(), "year": year, "tags": list(tags), "monitored": monitored, "hasFile": has_file, "isAvailable": available, **more}


def _series(sid, title, year=2020, *, tags=(), monitored=True, files=10, first_aired="2020-01-01T00:00:00Z", seasons=(1,), unmonitored_seasons=()):
    return {"id": sid, "title": title, "sortTitle": title.lower(), "year": year, "tags": list(tags), "monitored": monitored,
            "statistics": {"episodeFileCount": files}, "firstAired": first_aired,
            "seasons": [{"seasonNumber": n, "monitored": n not in unmonitored_seasons} for n in seasons]}


def _episode(eid, season, number, *, monitored=True, file_id=0, aired="2020-01-01T00:00:00Z"):
    return {"id": eid, "seasonNumber": season, "episodeNumber": number, "monitored": monitored, "episodeFileId": file_id, "hasFile": bool(file_id), "airDateUtc": aired}


def _upgrader(db, arr, *, sent=None, sleeps=None, now=None, **settings):
    return ArrUpgrader(db, arr.kind, instances=[INSTANCE], settings=settings, client_factory=lambda inst: arr,
                       sleep=(sleeps.append if sleeps is not None else lambda s: None), notifier=(lambda **kw: sent.append(kw)) if sent is not None else None,
                       now=lambda: now or arr.clock)


@pytest.fixture
def logs(monkeypatch):
    lines = []
    for level in ("log_info", "log_warning"):
        monkeypatch.setattr(arr_upgrader, level, lambda tag, message, **kw: lines.append(message))
    return lines


def test_settings_are_coerced_and_checked():
    s = normalize_one({"count": "0", "mode": "CUTOFF", "search_unit": "seasons", "season_threshold": 250, "tag_name": " searched ", "ignore_tag": None, "skip_queued": 0, "notify": 1}, "sonarr")
    assert (s["count"], s["mode"], s["search_unit"], s["season_threshold"], s["tag_name"], s["ignore_tag"], s["skip_queued"], s["notify"]) == (1, "cutoff", "seasons", 100, "searched", "", False, True)
    big = normalize_one({"count": 9999, "mode": "junk", "min_age_days": 99999, "max_grabs": -4}, "radarr")
    assert (big["count"], big["mode"], big["min_age_days"], big["max_grabs"]) == (500, "all", 3650, 0)
    assert normalize_one(None, "sonarr")["tag_name"] == "upgrade-searched" and normalize_one("junk")["count"] == 5
    assert normalize_one({}, "sonarr")["search_unit"] == "seasons" and normalize_one({"search_unit": "series"}, "sonarr")["search_unit"] == "series"
    # Unmonitored items are a Radarr choice: Sonarr's own series and season searches refuse them.
    assert [normalize_one({"monitored": "ANY"}, kind)["monitored"] for kind in ("radarr", "sonarr")] == ["any", "yes"]
    assert normalize_one({"monitored": "junk"}, "radarr")["monitored"] == "yes"
    # Lists of names are kept as tidy comma separated text.
    assert names_of(" a , B,a,, c ") == ["a", "B", "c"] and names_of(["x", "X", " y "]) == ["x", "y"] and names_of(None) == []
    listed = normalize_one({"only_tags": "4k ,kids,4K", "quality_profiles": ["HD - 720p/1080p", " Ultra-HD "]})
    assert (listed["only_tags"], listed["quality_profiles"]) == ("4k, kids", "HD - 720p/1080p, Ultra-HD")
    assert "letters, digits and -" in settings_problem(normalize_one({"only_tags": "kids, not ok"}))
    assert settings_problem(normalize_one({})) == ""
    assert "Set a search tag" in settings_problem(normalize_one({"tag_name": ""}))
    assert "letters, digits and -" in settings_problem(normalize_one({"tag_name": "up graded"}))
    assert "letters, digits and -" in settings_problem(normalize_one({"ignore_tag": "no_search"}))
    assert "must differ" in settings_problem(normalize_one({"tag_name": "Same", "ignore_tag": "same"}))
    assert settings_problem(normalize_one({"ignore_tag": ""})) == ""


def test_files_and_grabs_are_described_by_quality_and_score():
    assert describe([]) == "no file"
    assert describe([_quality("WEBDL-1080p", 100)]) == "WEBDL-1080p, score 100"
    assert describe([_quality("WEBDL-1080p", 0), _quality("WEBDL-1080p", 50), _quality("HDTV-720p")]) == "WEBDL-1080p ×2, HDTV-720p ×1, score 0 to 50"
    assert describe([_quality("WEBDL-1080p", 5)], without=2) == "WEBDL-1080p, score 5, 2 without a file"
    assert describe([{}]) == "unknown quality"
    eps = [_episode(n, 1, n) for n in (1, 2, 3)]
    assert episodes_text(eps[:1]) == "S01E01" and episodes_text(eps) == "S01E01-E03" and episodes_text([]) == ""
    assert episodes_text(eps + [_episode(9, 2, 1)]) == "S01-S02 (4 episodes)"
    assert seasons_text([2]) == "season 2" and seasons_text([3, 1, 2, 7, 9, 10]) == "seasons 1-3, 7, 9-10"
    # A season pack is one grab per episode: one row, against the files of those episodes.
    episodes = [_episode(1, 1, 1, file_id=11), _episode(2, 1, 2, file_id=12), _episode(3, 1, 3)]
    files = [{"id": 11, **_quality("HDTV-720p", 10)}, {"id": 12, **_quality("HDTV-720p", 20)}]
    pack = [{"sourceTitle": "Show.S01.1080p", "episodeId": n, **_quality("WEBDL-1080p", 300), "customFormats": [{"name": "AMZN"}, {"name": "5.1"}]} for n in (1, 2, 3)]
    [row] = grab_rows("sonarr", pack, files, episodes)
    assert row == {"release": "Show.S01.1080p", "episodes": "S01E01-E03", "before": "HDTV-720p ×2, score 10 to 20, 1 without a file",
                   "after": "WEBDL-1080p, score 300", "formats": ["AMZN", "5.1"], "download": ""}
    [single] = grab_rows("sonarr", [{"sourceTitle": "Show.S01E03", "episodeId": 3, **_quality("WEBDL-1080p", 300)}], files, episodes)
    assert (single["episodes"], single["before"]) == ("S01E03", "no file")
    [movie] = grab_rows("radarr", [{"sourceTitle": "Movie.2160p", "downloadId": "ABC", **_quality("Remux-2160p", 900)}], [{"id": 5, **_quality("Bluray-1080p", 100)}], [])
    assert (movie["episodes"], movie["before"], movie["after"], movie["formats"], movie["download"]) == ("", "Bluray-1080p, score 100", "Remux-2160p, score 900", [], "ABC")


def test_a_run_searches_the_next_untagged_monitored_items_that_are_out_one_at_a_time(test_db, logs):
    arr = FakeArr("radarr", [
        _movie(1, "Delta"), _movie(2, "Alpha"), _movie(3, "Charlie", monitored=False), _movie(4, "Bravo", has_file=False, available=False),
        _movie(5, "Echo", has_file=False), _movie(6, "Beta", tags=[2]), _movie(7, "Aardvark", tags=[1]),
    ], tags=("upgrade-searched", "upgrade-ignore"),
        files={2: [{"id": 21, **_quality("Bluray-720p", 10)}]},
        grabs={2: [{"movieId": 2, "sourceTitle": "Alpha.2020.1080p.BluRay", **_quality("Bluray-1080p", 250), "customFormats": [{"name": "HD Bluray Tier 01"}]}]})
    sleeps, progress = [], []
    stats = _upgrader(test_db, arr, sleeps=sleeps, count=2).run(progress=lambda *a: progress.append(a))
    # Title order among the untagged: Alpha, Delta, Echo. Not monitored, not out, ignored and already searched ones are left out.
    assert arr.commands == [{"name": "MoviesSearch", "movieIds": [2]}, {"name": "MoviesSearch", "movieIds": [1]}]
    assert sleeps == [5]  # the gap between two searches
    assert arr.tagged("upgrade-searched") == [1, 2, 7]
    assert (stats["instances"], stats["searched"], stats["grabbed"], stats["grabbed_items"], stats["tagged"], stats["failed"], stats["cycles"]) == (1, 2, 1, 1, 2, 0, 0)
    assert progress[-1][1:3] == (2, 2)
    text = "\n".join(logs)
    assert "7 movies in the library" in text and "5 monitored and out (1 not monitored, 1 not out yet)" in text
    assert "leaving out 1 tagged 'upgrade-ignore'" in text and "3 of 4 not searched yet this cycle" in text
    assert "[1/2] Alpha (2020): SEARCH the movie" in text and "[1/2] Alpha (2020): has Bluray-720p, score 10" in text
    assert "[1/2] Alpha (2020): GRABBED Alpha.2020.1080p.BluRay: Bluray-720p, score 10 → Bluray-1080p, score 250 [custom formats: HD Bluray Tier 01]" in text
    assert "[2/2] Delta (2020): has no file" in text and "[2/2] Delta (2020): nothing grabbed" in text
    assert "arr-1: Searched 2 movies, 1 release grabbed" in text
    assert "arr-1: Cycle: 3 of 4 searched, 1 left, about 1 more run at 2 a run" in text
    assert "Radarr upgrade search (run): 1 instance(s), 2 item(s) searched, 1 release(s) grabbed for 1 item(s), 2 tagged" in text


def test_once_every_item_carries_the_tag_the_tags_are_cleared_and_the_cycle_starts_over(test_db, logs):
    arr = FakeArr("radarr", [_movie(1, "Alpha", tags=[1]), _movie(2, "Beta", tags=[1]), _movie(3, "Gone", tags=[1], monitored=False)], tags=("upgrade-searched",))
    stats = _upgrader(test_db, arr, count=1).run()
    assert arr.puts[0] == {"movieIds": [1, 2], "tags": [1], "applyTags": "remove"}  # the items a run looks at; the unmonitored one keeps its tag
    assert arr.commands == [{"name": "MoviesSearch", "movieIds": [1]}] and arr.tagged("upgrade-searched") == [1, 3]
    assert stats["cycles"] == 1 and any("the cycle is complete. Clearing the tags to start over" in line for line in logs)
    assert any("Cycle: 1 of 2 searched, 1 left" in line for line in logs)
    # The run that searches the last one says so; the next run starts over.
    logs.clear()
    _upgrader(test_db, arr, count=1).run()
    assert arr.tagged("upgrade-searched") == [1, 2, 3] and any("Cycle: all 2 searched; the next run starts over" in line for line in logs)


def test_a_dry_run_lists_what_it_would_search_and_changes_nothing(test_db, logs):
    arr = FakeArr("radarr", [_movie(1, "Alpha", tags=[1]), _movie(2, "Beta", tags=[1])], tags=("upgrade-searched",), running=[{"name": "MoviesSearch", "status": "queued"}] * 3)
    sent = []
    stats = _upgrader(test_db, arr, sent=sent, count=5, ignore_tag="not-there-yet", notify=True).run(dry_run=True)
    assert arr.commands == [] and arr.puts == [] and "not-there-yet" not in arr.tags and arr.tagged("upgrade-searched") == [1, 2]
    assert (stats["searched"], stats["grabbed"], stats["tagged"], stats["cycles"], stats["skipped"]) == (2, 0, 0, 1, 0)
    text = "\n".join(logs)
    assert "a real run would be skipped" in text and "A real run would clear the tags and start over" in text
    assert "[1/5] Alpha (2020): WOULD SEARCH the movie" in text and "Would search 2 movies" in text and "Cycle: all 2 searched" in text
    [message] = sent
    assert message["event_type"] == "info" and message["title"] == "Radarr upgrade search (dry run)"
    assert [f["name"] for f in message["fields"]] == ["arr-1: settings", "arr-1: would search 2", "arr-1: this run"]
    assert message["fields"][0]["value"].endswith(", dry run") and message["fields"][1]["value"] == "Alpha (2020)\nBeta (2020)"


def test_an_item_that_is_downloading_waits_and_the_next_one_takes_its_place(test_db, logs):
    queue = [{"movieId": 1, "added": _iso(NOW - timedelta(hours=2)), "trackedDownloadState": "downloading"},
             {"movieId": 2, "added": _iso(NOW - timedelta(hours=5)), "trackedDownloadState": "importBlocked"},
             {"movieId": 3, "added": _iso(NOW - timedelta(hours=80)), "trackedDownloadState": "importBlocked"},
             {"movieId": None, "added": _iso(NOW)}]
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta"), _movie(3, "Charlie"), _movie(4, "Delta")], queue=queue)
    stats = _upgrader(test_db, arr, count=2).run()
    # A download over three days old no longer holds its movie back.
    assert [c["movieIds"] for c in arr.commands] == [[3], [4]] and arr.tagged("upgrade-searched") == [3, 4]
    assert stats["held"] == 2 and stats["searched"] == 2
    text = "\n".join(logs)
    assert "2 movies not searched yet with a download in the queue: Alpha (2020) (downloading, 2h in the queue), Beta (2020) (stuck importing, 5h in the queue)" in text
    assert "HELD BACK Alpha (2020): downloading, 2h in the queue. It stays untagged for a later run" in text
    assert "HELD BACK Beta (2020): stuck importing, 5h in the queue" in text and "Held back: 2 with a download in the queue" in text
    # Switched off, the queue is not even read.
    arr2 = FakeArr("radarr", [_movie(1, "Alpha")], queue=queue)
    _upgrader(test_db, arr2, count=1, skip_queued=False).run()
    assert [c["movieIds"] for c in arr2.commands] == [[1]] and "queue" not in arr2.reads


def test_a_run_is_skipped_while_the_arr_is_busy_searching(test_db, logs):
    busy = [{"name": "MissingMoviesSearch", "status": "started"}, {"name": "MoviesSearch", "status": "queued"}, {"name": "MoviesSearch", "status": "queued"},
            {"name": "RssSync", "status": "started"}, {"name": "MoviesSearch", "status": "completed"}]
    arr = FakeArr("radarr", [_movie(1, "Alpha")], running=busy)
    sent = []
    stats = _upgrader(test_db, arr, sent=sent, notify=True).run()
    assert arr.commands == [] and arr.puts == [] and (stats["skipped"], stats["instances"], stats["searched"]) == (1, 0, 0)
    assert any("skipped: Radarr already has 3 searches queued or running" in line for line in logs)
    assert sent[0]["event_type"] == "info" and sent[0]["fields"] == [{"name": "arr-1: skipped", "value": "Radarr already has 3 searches queued or running. The next run searches.", "inline": False}]
    # Two are not enough to hold a run back.
    arr = FakeArr("radarr", [_movie(1, "Alpha")], running=busy[1:])
    assert _upgrader(test_db, arr).run()["searched"] == 1


def test_cutoff_and_missing_search_only_what_is_on_that_wanted_list(test_db, logs):
    movies = [_movie(1, "Alpha"), _movie(2, "Beta"), _movie(3, "Charlie", has_file=False), _movie(4, "Delta", tags=[1])]
    arr = FakeArr("radarr", movies, tags=("upgrade-searched",), wanted={"cutoff": [{"id": 2}, {"id": 4}, {"id": 99}], "missing": [{"id": 3}]})
    _upgrader(test_db, arr, mode="cutoff").run()
    assert [c["movieIds"] for c in arr.commands] == [[2]] and any("3 movies on the Cutoff Unmet list" in line for line in logs)
    assert any("Cycle: all 2 searched" in line for line in logs)
    arr.commands.clear()
    _upgrader(test_db, arr, mode="missing").run()
    assert [c["movieIds"] for c in arr.commands] == [[3]]

    # Sonarr lists episodes: a season search for a season with several, one episode search for the rest.
    listed = [{"id": 101, "seriesId": 1, "seasonNumber": 1}, {"id": 102, "seriesId": 1, "seasonNumber": 1}, {"id": 201, "seriesId": 1, "seasonNumber": 2},
              {"id": 301, "seriesId": 1, "seasonNumber": 3}, {"id": 901, "seriesId": 2, "seasonNumber": 4}]
    sonarr = FakeArr("sonarr", [_series(1, "Alpha"), _series(2, "Beta"), _series(3, "Charlie")], wanted={"cutoff": listed})
    logs.clear()
    _upgrader(test_db, sonarr, mode="cutoff", search_unit="seasons").run()
    assert sonarr.commands == [{"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 1}, {"name": "EpisodeSearch", "episodeIds": [201, 301]},
                               {"name": "EpisodeSearch", "episodeIds": [901]}]
    assert sonarr.tagged("upgrade-searched") == [1, 2]
    text = "\n".join(logs)
    assert "2 series on the Cutoff Unmet list (5 episode(s))" in text
    assert "[1/5] Alpha (2020): SEARCH season 1 (2 listed episodes), 2 listed episodes" in text and "[2/5] Beta (2020): SEARCH 1 listed episode" in text


def test_a_wanted_list_is_read_page_by_page(test_db, monkeypatch):
    monkeypatch.setattr(arr_upgrader, "_PAGE", 2)
    arr = FakeArr("radarr", [_movie(n, f"Movie {n}") for n in range(1, 6)], wanted={"cutoff": [{"id": n} for n in range(1, 6)]})
    assert _upgrader(test_db, arr, mode="cutoff", count=10).run()["searched"] == 5
    assert [r for r in arr.reads if r.startswith("wanted/")] == ["wanted/cutoff"] * 3


def test_sonarr_searches_the_whole_series_or_each_season_with_enough_monitored_episodes(test_db, logs):
    series = [_series(1, "Alpha", seasons=(0, 1, 2, 3, 4), unmonitored_seasons=(2,)), _series(2, "Beta", seasons=(1,)), _series(3, "Charlie", seasons=(1,))]
    episodes = {
        1: ([_episode(1, 0, 1)] + [_episode(10 + n, 1, n) for n in range(1, 5)] + [_episode(20 + n, 2, n) for n in range(1, 3)]
            + [_episode(30 + n, 3, n, monitored=n == 1) for n in range(1, 5)] + [_episode(40 + n, 4, n, aired="2027-01-01T00:00:00Z") for n in range(1, 3)]),
        2: [_episode(50 + n, 1, n, monitored=False) for n in range(1, 3)],
        3: [_episode(60 + n, 1, n) for n in range(1, 3)],
    }
    whole = FakeArr("sonarr", series, episodes=episodes)
    _upgrader(test_db, whole, count=1, search_unit="series").run()
    assert whole.commands == [{"name": "SeriesSearch", "seriesId": 1}]

    arr = FakeArr("sonarr", series, episodes=episodes)
    stats = _upgrader(test_db, arr, count=2).run()  # by season, at least half monitored, unless set otherwise
    # Alpha: season 1 only. Beta has no season to search: tagged without a search, and Charlie takes its place.
    assert arr.commands == [{"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 1}, {"name": "SeasonSearch", "seriesId": 3, "seasonNumber": 1}]
    assert arr.tagged("upgrade-searched") == [1, 2, 3] and (stats["searched"], stats["passed"], stats["tagged"]) == (2, 1, 3)
    text = "\n".join(logs)
    assert ("[1/2] Alpha (2020): SEARCH season 1 (season 3 left out, 1 of 4 episodes monitored (25%, under 50%); "
            "season 4 left out, nothing aired yet; season 2 not monitored)") in text
    assert "NOTHING TO SEARCH Beta (2020): season 1 left out, 0 of 2 episodes monitored (0%, under 50%). Tagged so the cycle moves on" in text
    assert "Nothing to search: 1 (no season with enough monitored episodes)" in text and "Cycle: all 3 searched" in text
    # A lower threshold lets season 3 in.
    low = FakeArr("sonarr", series[:1], episodes=episodes)
    _upgrader(test_db, low, search_unit="seasons", season_threshold=20).run()
    assert [c["seasonNumber"] for c in low.commands] == [1, 3]


def test_a_season_pack_is_reported_once_against_the_files_it_replaces(test_db, logs):
    episodes = {1: [_episode(11, 1, 1, file_id=101), _episode(12, 1, 2, file_id=102), _episode(21, 2, 1, file_id=201)]}
    files = {1: [{"id": 101, **_quality("HDTV-720p", 5)}, {"id": 102, **_quality("HDTV-720p", 5)}, {"id": 201, **_quality("WEBDL-1080p", 90)}]}
    pack = [{"seriesId": 1, "episodeId": eid, "sourceTitle": "Alpha.S01.1080p.WEB", **_quality("WEBDL-1080p", 400)} for eid in (11, 12)]
    arr = FakeArr("sonarr", [_series(1, "Alpha", seasons=(1, 2))], episodes=episodes, files=files, grabs={1: pack + [{"seriesId": 7, "episodeId": 1, "sourceTitle": "Other.Show"}]})
    sent = []
    stats = _upgrader(test_db, arr, sent=sent, search_unit="seasons", season_threshold=0, notify=True, notify_grabs_only=True).run()
    assert [c["seasonNumber"] for c in arr.commands] == [1, 2] and (stats["grabbed"], stats["grabbed_items"]) == (1, 1)
    assert any("GRABBED Alpha.S01.1080p.WEB (S01E01-E02): HDTV-720p ×2, score 5 → WEBDL-1080p, score 400" in line for line in logs)
    [message] = sent
    assert message["event_type"] == "success" and message["title"] == "Sonarr upgrade search"
    assert message["description"] == ("**arr-1: 1 grabbed**\n\n**Alpha (2020) · S01E01-E02**\n```diff\n- HDTV-720p ×2, score 5\n+ WEBDL-1080p, score 400\n"
                                      "  Alpha.S01.1080p.WEB\n```")
    assert message["color"] == 0x2ECC71
    assert [f["name"] for f in message["fields"]] == ["arr-1: settings", "arr-1: searched 1", "arr-1: this run"]
    assert message["fields"][1]["value"] == "Alpha (2020): season 1, season 2"
    assert "monitored seasons (at least 0% monitored)" in message["fields"][0]["value"]


def test_only_when_something_is_grabbed_keeps_a_quiet_run_quiet(test_db):
    sent = []
    _upgrader(test_db, FakeArr("radarr", [_movie(1, "Alpha")]), sent=sent, notify=True, notify_grabs_only=True).run()
    assert sent == []
    _upgrader(test_db, FakeArr("radarr", [_movie(1, "Alpha")]), sent=sent, notify=True).run()
    assert [m["event_type"] for m in sent] == ["info"]
    _upgrader(test_db, FakeArr("radarr", [_movie(1, "Alpha")]), sent=sent, notify=False).run()
    assert len(sent) == 1


def test_a_failed_search_is_not_tagged_and_three_in_a_row_stop_the_run(test_db, logs):
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta"), _movie(3, "Charlie")], fail={1}, refuse={3})
    sent = []
    stats = _upgrader(test_db, arr, sent=sent, notify=True, notify_grabs_only=True).run()
    assert arr.tagged("upgrade-searched") == [2] and (stats["searched"], stats["failed"], stats["tagged"], stats["stopped"]) == (1, 2, 1, 0)
    text = "\n".join(logs)
    assert ("[1/5] Alpha (2020): FAILED: the search of the movie ended failed in Radarr (see System, Tasks there). "
            "Failed run 1 of 3 in a row. It stays untagged, so the next run tries again") in text
    assert "[3/5] Charlie (2020): FAILED: Radarr did not accept the search of the movie" in text
    assert "Searched 1 movie, 0 releases grabbed" in text and "Failed: 2 (see the log)" in text
    [message] = sent  # a failure is sent even when only grabs are wanted, as an error
    assert message["event_type"] == "error" and message["description"] == "Needs a look: arr-1: 2 search(es) failed" and message["color"] == 0xE74C3C
    assert "arr-1: 2 failed" in [f["name"] for f in message["fields"]]

    # The third run in a row that fails tags the movie anyway, so one bad search cannot keep the cycle from ending.
    logs.clear()
    stats = _upgrader(test_db, arr).run()
    assert arr.tagged("upgrade-searched") == [2] and stats["tagged"] == 0 and any("Failed run 2 of 3 in a row" in line for line in logs)
    logs.clear()
    stats = _upgrader(test_db, arr).run()
    assert arr.tagged("upgrade-searched") == [1, 2, 3] and (stats["failed"], stats["tagged"]) == (2, 2)
    assert any("FAILED: Radarr did not accept the search of the movie. It failed 3 runs in a row: tagged anyway so the cycle can end, and searched again next cycle" in line for line in logs)
    assert any("Cycle: all 3 searched" in line for line in logs)
    # A search that works again starts the count over.
    arr.fail.clear()
    arr.refuse.clear()
    _upgrader(test_db, arr).run()  # all tagged: the cycle starts over and all three are searched
    assert json.loads(get_setting_value(test_db, "radarr_upgrade_state"))["arr-1"]["fails"] == {}

    arr = FakeArr("radarr", [_movie(n, f"Movie {n}") for n in range(1, 6)], fail={1, 2, 3, 4, 5})
    stats = _upgrader(test_db, arr, count=5).run()
    assert len(arr.commands) == 3 and arr.tagged("upgrade-searched") == [] and (stats["failed"], stats["stopped"]) == (3, 1)
    assert any("stopping: 3 searches in a row failed" in line for line in logs)


def test_a_search_still_going_after_ten_minutes_is_tagged_and_ends_the_run(test_db, logs):
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta")], stuck={1})
    sleeps = []
    stats = _upgrader(test_db, arr, sleeps=sleeps).run()
    assert sum(sleeps) == 600 and len(arr.commands) == 1 and arr.tagged("upgrade-searched") == [1]
    assert (stats["searched"], stats["tagged"], stats["stopped"], stats["failed"]) == (1, 1, 0, 0)
    text = "\n".join(logs)
    assert "still searching after 10 minutes" in text and "Stopped early: the search of Alpha (2020) was still going after 10 minutes. The rest waits for the next run" in text


def test_an_instance_that_cannot_run_is_reported_and_the_others_still_run(test_db, logs):
    good, no_tag = FakeArr("radarr", [_movie(1, "Alpha")]), FakeArr("radarr", [_movie(1, "Alpha")], no_tags=True)
    arrs = {"good": good, "no-tag": no_tag, "down": None}
    sent = []
    upgrader = ArrUpgrader(test_db, "radarr", instances=[{"name": name, "url": "http://x", "api_key": "k"} for name in ("down", "no-tag", "bad", "good")],
                           per_instance={"bad": {"tag_name": ""}, "good": {"notify": True}, "down": {"notify": True}, "no-tag": {"ignore_tag": "", "notify": True}},
                           client_factory=lambda inst: arrs[inst["name"]], sleep=lambda s: None, notifier=lambda **kw: sent.append(kw), now=lambda: NOW)
    stats = upgrader.run()
    assert (stats["instances"], stats["unreachable"], stats["failed_instances"], stats["searched"]) == (1, 1, 2, 1)
    assert upgrader.results["down"]["error"] == "cannot connect" and "Set a search tag" in upgrader.results["bad"]["error"]
    assert upgrader.results["no-tag"]["error"] == "could not create the search tag 'upgrade-searched'" and no_tag.commands == []
    assert good.commands == [{"name": "MoviesSearch", "movieIds": [1]}]
    [message] = sent
    assert message["event_type"] == "error" and message["description"] == "Needs a look: down: the run failed; no-tag: the run failed"
    assert [f["name"] for f in message["fields"]][:2] == ["down: run failed", "no-tag: run failed"]
    assert ArrUpgrader(test_db, "sonarr", instances=[]).run()["instances"] == 0
    with pytest.raises(ValueError):
        ArrUpgrader(test_db, "lidarr")


def test_each_instance_runs_start_to_finish_with_its_own_settings(test_db, logs):
    first, second = FakeArr("radarr", [_movie(n, f"Movie {n}") for n in (1, 2, 3)]), FakeArr("radarr", [_movie(n, f"Film {n}") for n in (1, 2, 3)])
    arrs = {"one": first, "two": second}
    progress = []
    upgrader = ArrUpgrader(test_db, "radarr", instances=[{"name": name, "url": "http://x", "api_key": "k"} for name in arrs],
                           per_instance={"one": {"count": 1, "tag_name": "seen"}, "two": {"count": 3}},
                           client_factory=lambda inst: arrs[inst["name"]], sleep=lambda s: None, now=lambda: NOW)
    stats = upgrader.run(progress=lambda *a: progress.append(a[1:3]))
    assert len(first.commands) == 1 and first.tagged("seen") == [1] and len(second.commands) == 3 and second.tagged("upgrade-searched") == [1, 2, 3]
    assert (stats["instances"], stats["searched"], stats["tagged"]) == (2, 4, 4)
    assert progress[0] == (0, 4) and progress[-1] == (4, 4) and [p[0] for p in progress] == sorted(p[0] for p in progress)
    owners = [line.split(":")[0].split(" ")[1] if line.startswith("──") else line.split(":")[0] for line in logs[:-1]]
    assert owners == sorted(owners)  # every line of "one" comes before the first of "two"


def test_an_arr_that_stops_answering_mid_run_stops_that_instance(test_db, logs):
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta")])
    original = arr.make_put_request

    def put_then_die(endpoint, json=None):
        out = original(endpoint, json)
        arr.down = True
        return out

    arr.make_put_request = put_then_die
    stats = _upgrader(test_db, arr).run()
    assert len(arr.commands) == 1 and arr.tagged("upgrade-searched") == [1] and (stats["searched"], stats["stopped"]) == (1, 1)
    assert any("stopping: the arr did not answer when asked for its file records" in line for line in logs)


def test_a_title_filter_narrows_a_run_to_matching_titles(test_db, logs):
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Alphabet"), _movie(3, "Beta")])
    _upgrader(test_db, arr, title_filter="ALPHAB").run()
    assert [c["movieIds"] for c in arr.commands] == [[2]] and any("title filter 'ALPHAB' matches 1 of 3" in line for line in logs)


def test_tag_profile_and_age_filters_narrow_the_pool(test_db, logs):
    movies = [
        _movie(1, "Alpha", tags=[3], qualityProfileId=7, digitalRelease="2026-11-01T00:00:00Z"),
        _movie(2, "Beta", tags=[3, 4], qualityProfileId=8, inCinemas="2020-01-01T00:00:00Z", digitalRelease="2020-06-01T00:00:00Z"),
        _movie(3, "Charlie", tags=[4], qualityProfileId=7, physicalRelease="2019-03-01T00:00:00Z"),
        _movie(4, "Delta", qualityProfileId=7, digitalRelease="2018-01-01T00:00:00Z"),
        _movie(5, "Echo", tags=[4], qualityProfileId=7, year=0),
    ]
    make = lambda: FakeArr("radarr", movies, tags=("upgrade-searched", "upgrade-ignore", "kids", "4k"), profiles={"HD - 720p/1080p": 7, "Ultra-HD": 8})  # noqa: E731
    arr = make()
    _upgrader(test_db, arr, only_tags="Kids, 4K").run()
    assert [c["movieIds"] for c in arr.commands] == [[1], [2], [3], [5]] and any("4 of 5 carry the tag 'Kids' or '4K'" in line for line in logs)
    arr = make()
    _upgrader(test_db, arr, quality_profiles="ultra-hd").run()
    assert [c["movieIds"] for c in arr.commands] == [[2]] and any("1 of 5 use the quality profile 'ultra-hd'" in line for line in logs)
    arr = make()
    _upgrader(test_db, arr, min_age_days=30).run()  # Alpha came out 10 days ago; Echo has no date and no year
    assert [c["movieIds"] for c in arr.commands] == [[2], [3], [4]]
    assert any("3 of 5 released at least 30 day(s) ago (1 newer, 1 with no release date)" in line for line in logs)
    arr = make()
    _upgrader(test_db, arr, only_tags="4k", quality_profiles="HD - 720p/1080p", min_age_days=30).run()
    assert [c["movieIds"] for c in arr.commands] == [[3]]
    assert any("only tagged 4k, only quality profile HD - 720p/1080p, released at least 30 day(s) ago" in line for line in logs)
    assert released(movies[1], "radarr") == date(2020, 1, 1) and released({"year": 1999}, "sonarr") == date(1999, 1, 1) and released({}, "radarr") is None

    # A name the arr does not have stops that instance before anything is searched, and says what it has.
    for settings, said in (({"only_tags": "nope"}, "Radarr has no tag 'nope'. Its tags: 4k, kids, upgrade-ignore, upgrade-searched"),
                           ({"quality_profiles": "SD"}, "Radarr has no quality profile 'SD'. Its profiles: HD - 720p/1080p, Ultra-HD")):
        arr = make()
        upgrader = _upgrader(test_db, arr, **settings)
        stats = upgrader.run()
        assert arr.commands == [] and stats["failed_instances"] == 1 and upgrader.results["arr-1"]["error"] == said


def test_radarr_can_search_what_is_not_monitored(test_db, logs):
    movies = [_movie(1, "Alpha"), _movie(2, "Beta", monitored=False), _movie(3, "Charlie", monitored=False, has_file=False, available=False)]
    arr = FakeArr("radarr", movies, wanted={"cutoff": [{"id": 1}]}, unmonitored_wanted={"cutoff": [{"id": 2}]})
    _upgrader(test_db, arr, monitored="no").run()
    assert [c["movieIds"] for c in arr.commands] == [[2]] and any("1 not monitored and out (1 monitored, 1 not out yet)" in line for line in logs)
    arr = FakeArr("radarr", movies, wanted={"cutoff": [{"id": 1}]}, unmonitored_wanted={"cutoff": [{"id": 2}]})
    _upgrader(test_db, arr, monitored="any").run()
    assert [c["movieIds"] for c in arr.commands] == [[1], [2]] and any("2 monitored or not and out (1 not out yet)" in line for line in logs)
    for monitored, picked in (("no", [[2]]), ("any", [[1], [2]]), ("yes", [[1]])):
        arr = FakeArr("radarr", movies, wanted={"cutoff": [{"id": 1}]}, unmonitored_wanted={"cutoff": [{"id": 2}]})
        _upgrader(test_db, arr, monitored=monitored, mode="cutoff").run()
        assert [c["movieIds"] for c in arr.commands] == picked
    # Sonarr keeps to what is monitored whatever a stored profile says.
    sonarr = FakeArr("sonarr", [_series(1, "Alpha"), _series(2, "Beta", monitored=False)])
    _upgrader(test_db, sonarr, monitored="any", search_unit="series").run()
    assert [c["seriesId"] for c in sonarr.commands] == [1]


def test_a_run_stops_once_enough_items_got_a_release(test_db, logs):
    grab = lambda mid: [{"movieId": mid, "sourceTitle": f"Movie.{mid}.1080p", "downloadId": f"D{mid}", **_quality("Bluray-1080p", 100)}]  # noqa: E731
    arr = FakeArr("radarr", [_movie(n, f"Movie {n}") for n in range(1, 7)], grabs={2: grab(2), 3: grab(3), 5: grab(5)})
    stats = _upgrader(test_db, arr, count=6, max_grabs=2).run()
    assert [c["movieIds"] for c in arr.commands] == [[1], [2], [3]] and arr.tagged("upgrade-searched") == [1, 2, 3]
    assert (stats["searched"], stats["grabbed_items"]) == (3, 2)
    text = "\n".join(logs)
    assert "grab limit 2 reached; the rest waits for the next runs" in text and "arr-1: Grab limit 2 reached; the rest waits for the next runs" in text
    assert "Cycle: 3 of 6 searched, 3 left" in text


def test_later_runs_say_what_became_of_a_grab(test_db, logs):
    grab = lambda mid, title: [{"movieId": mid, "sourceTitle": f"{title}.1080p.BluRay", "downloadId": f"D{mid}", **_quality("Bluray-1080p", 250)}]  # noqa: E731
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta"), _movie(3, "Charlie"), _movie(4, "Delta")],
                  files={1: [{"id": 11, **_quality("WEBDL-720p", 10)}]},
                  grabs={1: grab(1, "Alpha"), 2: grab(2, "Beta"), 3: grab(3, "Charlie"), 4: [{"movieId": 4, "sourceTitle": "Delta.no.download", **_quality("HDTV-720p", 1)}]})
    sent = []
    settings = {"count": 4, "notify": True, "notify_grabs_only": True}
    stats = _upgrader(test_db, arr, sent=sent, **settings).run()
    assert stats["grabbed"] == 4 and stats["imported"] == 0 and "Earlier grabs" not in "\n".join(logs)
    kept = json.loads(get_setting_value(test_db, "radarr_upgrade_state"))["arr-1"]["pending"]
    assert [p["download"] for p in kept] == ["D1", "D2", "D3"]  # a grab with no download cannot be followed
    assert kept[0] == {"id": 1, "label": "Alpha (2020)", "release": "Alpha.1080p.BluRay", "episodes": "", "before": "WEBDL-720p, score 10",
                       "after": "Bluray-1080p, score 250", "download": "D1", "since": "2026-11-11T12:00:00Z", "t": NOW.timestamp()}

    # An hour on: Alpha imported, Beta's download failed, Charlie sits blocked in the queue. Nothing new is grabbed from here on.
    arr.clock = NOW + timedelta(hours=1)
    arr.grabs = {}
    arr.history += [{"eventType": "downloadFolderImported", "date": _iso(NOW + timedelta(minutes=20)), "downloadId": "D1", "movieId": 1, **_quality("Bluray-1080p", 260)},
                    {"eventType": "downloadFailed", "date": _iso(NOW + timedelta(minutes=30)), "downloadId": "D2", "movieId": 2, "data": {"message": "Unpacking failed"}}]
    arr.queue = [{"movieId": 3, "downloadId": "D3", "added": _iso(NOW), "trackedDownloadState": "importBlocked", "trackedDownloadStatus": "warning",
                  "statusMessages": [{"title": "x", "messages": ["Not a Custom Format upgrade"]}, {"title": "y", "messages": ["Not a Custom Format upgrade", "Sample"]}]}]
    logs.clear()
    sent.clear()
    dry = _upgrader(test_db, arr, sent=sent, **settings).run(dry_run=True)  # a dry run looks, and forgets nothing
    assert (dry["imported"], dry["not_imported"], dry["stuck"]) == (1, 1, 1)
    assert len(json.loads(get_setting_value(test_db, "radarr_upgrade_state"))["arr-1"]["pending"]) == 3
    logs.clear()
    sent.clear()
    stats = _upgrader(test_db, arr, sent=sent, **settings).run()
    assert (stats["imported"], stats["not_imported"], stats["stuck"]) == (1, 1, 1)
    assert (stats["searched"], stats["held"], stats["cycles"]) == (3, 1, 1)  # all four were tagged: a new cycle, with Charlie waiting on its download
    text = "\n".join(logs)
    assert "arr-1: IMPORTED Alpha (2020): Alpha.1080p.BluRay: WEBDL-720p, score 10 → Bluray-1080p, score 260" in text
    assert "arr-1: NOT IMPORTED Beta (2020): Beta.1080p.BluRay: Unpacking failed" in text
    assert "arr-1: STUCK Charlie (2020): Charlie.1080p.BluRay: Not a Custom Format upgrade; Sample" in text
    assert "arr-1: Earlier grabs: 1 of 3 imported, 1 not imported, 1 still waiting (1 stuck in the queue)" in text
    assert "1 earlier grab(s) imported, 1 earlier grab(s) not imported, 1 stuck in the queue" in logs[-1]
    [message] = sent
    assert message["event_type"] == "error"
    assert message["description"] == "Needs a look: arr-1: 1 earlier grab(s) did not import; arr-1: 1 grab(s) stuck in the queue"
    fields = {f["name"]: f["value"] for f in message["fields"]}
    assert fields["arr-1: 1 earlier grab(s) imported"] == "Alpha (2020): WEBDL-720p, score 10 → Bluray-1080p, score 260"
    assert fields["arr-1: 1 did not import"] == "Beta (2020): Beta.1080p.BluRay: Unpacking failed"
    assert fields["arr-1: 1 stuck in the queue"] == "Charlie (2020): Charlie.1080p.BluRay: Not a Custom Format upgrade; Sample"
    assert [p["download"] for p in json.loads(get_setting_value(test_db, "radarr_upgrade_state"))["arr-1"]["pending"]] == ["D3"]

    # The stuck one is an error once. The next run still lists it in the log, and stays quiet.
    logs.clear()
    sent.clear()
    stats = _upgrader(test_db, arr, sent=sent, **settings).run()
    assert stats["stuck"] == 1 and sent == [] and any("STUCK Charlie (2020)" in line for line in logs)
    # It imports after all: good news, sent as a success.
    arr.queue = []
    arr.history.append({"eventType": "downloadFolderImported", "date": _iso(NOW + timedelta(hours=2)), "downloadId": "D3", "movieId": 3, **_quality("Bluray-1080p", 250)})
    stats = _upgrader(test_db, arr, sent=sent, **settings).run()
    assert stats["imported"] == 1 and [m["event_type"] for m in sent] == ["success"]
    assert json.loads(get_setting_value(test_db, "radarr_upgrade_state"))["arr-1"]["pending"] == []


def test_a_grab_that_never_imports_is_let_go_after_a_week_and_a_busy_arr_still_reports(test_db, logs):
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta")], grabs={1: [{"movieId": 1, "sourceTitle": "Alpha.1080p", "downloadId": "D1", **_quality("Bluray-1080p", 5)}]})
    _upgrader(test_db, arr, count=1).run()
    arr.clock = NOW + timedelta(days=3)
    arr.running = [{"name": "MoviesSearch", "status": "queued"}] * 3
    logs.clear()
    sent = []
    stats = _upgrader(test_db, arr, sent=sent, count=1, notify=True).run()  # skipped as busy: the earlier grab is still looked at
    assert (stats["skipped"], stats["imported"], stats["not_imported"]) == (1, 0, 0) and len(arr.commands) == 1
    assert any("arr-1: Earlier grabs: 0 of 1 imported, 1 still waiting" in line for line in logs)
    assert [f["name"] for f in sent[0]["fields"]] == ["arr-1: skipped"]
    arr.clock = NOW + timedelta(days=8)
    logs.clear()
    stats = _upgrader(test_db, arr, count=1).run()
    assert stats["not_imported"] == 1 and any("NOT IMPORTED Alpha (2020): Alpha.1080p: not imported after 7 days" in line for line in logs)
    assert json.loads(get_setting_value(test_db, "radarr_upgrade_state"))["arr-1"]["pending"] == []

