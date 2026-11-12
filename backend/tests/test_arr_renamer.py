"""The Sonarr/Radarr rename tool: the arr decides what to rename, Posterflow asks per item,
holds back what looks wrong, renames items not downloaded yet in the arr only, skips folders
that are gone while the arr has files on record there, confirms every file is still there,
tags what it processed, and takes the tag off them all in the run that ends the pass."""

from urllib.parse import parse_qs, unquote, urlparse

from services.arr_renamer import ArrRenamer, normalize_one, problems, rename_reasons, tag_problem, tba_fill

INSTANCE = {"name": "arr-1", "url": "http://arr", "api_key": "k"}


class FakeArr:
    """A Sonarr/Radarr at the request level: the v3 routes the renamer uses, over a tiny
    model of each item's folder on disk and the files the arr has on record for it."""

    def __init__(self, kind, items, *, renames=None, accept_commands=True, tags=(), folder_names=None, roots=None,
                 lose=(), stale=False, fail_command=False, stuck=False, move_loses=False, extra_dirs=(), vanish=(), absent=(), caseless=False,
                 episodes=None):
        self.url = "http://arr"
        self.episodes = dict(episodes or {})  # Sonarr: series id → [(file id, season, episode, title)]
        self.title_reads = []  # the series whose episode titles were asked for
        self.kind = kind
        self.noun = "movie" if kind == "radarr" else "series"
        self.file_noun = "moviefile" if kind == "radarr" else "episodefile"
        self.id_param = "movieId" if kind == "radarr" else "seriesId"
        self.file_key = "movieFileId" if kind == "radarr" else "episodeFileId"
        self.items = {i["id"]: dict(i) for i in items}
        self.renames = {k: list(v) for k, v in (renames or {}).items()}
        self.folder_names = dict(folder_names or {})  # item id → the name the arr's format gives; default "Title (Year)"
        self.roots = list(roots) if roots is not None else sorted({i["rootFolderPath"] for i in items})
        self.extra_dirs = list(extra_dirs)  # folders on disk that belong to no item
        self.accept_commands = accept_commands
        self.lose, self.stale, self.fail_command, self.stuck, self.move_loses = set(lose), stale, fail_command, stuck, move_loses
        self.vanish = set(vanish)  # item ids deleted from the arr while their rename runs
        self.caseless = caseless  # a disk that ignores capitals: a folder is found under any capitals
        self.tags = {label: n + 1 for n, label in enumerate(tags)}
        self.commands = []
        self.puts = []
        self.statuses = {}
        self.down = False  # stops answering every read, as the client gives None for any failure
        # Disk and records start from the pending renames (old paths) plus each item's own `files`.
        self.disk = {}  # item path → {relative path}
        self.records = {}  # item id → {file id: relative path}
        for item in self.items.values():
            rels = {r["existingPath"]: r[self.file_key] for r in self.renames.get(item["id"], [])}
            for n, rel in enumerate(item.pop("files", []), 1):
                rels.setdefault(rel, 1000 * item["id"] + n)
            if item["id"] not in absent:  # absent: the arr has the item (and its file records), the disk has no folder
                self.disk[item["path"]] = set(rels)
            self.records[item["id"]] = {fid: rel for rel, fid in rels.items()}

    def _folder(self, path):
        """The folder as the disk has it: `path` itself, or the same name in other capitals on a caseless disk."""
        if path in self.disk or not self.caseless:
            return path
        return next((p for p in self.disk if p.lower() == path.lower()), path)

    def folder_name(self, mid):
        item = self.items[mid]
        return self.folder_names.get(mid, f"{item['title']} ({item['year']})")

    def make_get_request(self, endpoint):
        url = urlparse(endpoint)
        path = url.path.split("/api/v3/", 1)[1]
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if self.down:
            return None
        if path == "system/status":
            return {"version": "1"}
        if path == "tag":
            return [{"id": tid, "label": label} for label, tid in self.tags.items()]
        if path == self.noun:
            return [dict(i) for i in self.items.values()]
        if path == "rootfolder":
            return [{"path": r} for r in self.roots]
        if path == "rename":
            return list(self.renames.get(int(q[self.id_param]), [])) if int(q[self.id_param]) in self.items else None
        if path == "episode":
            self.title_reads.append(int(q["seriesId"]))
            return [{"episodeFileId": fid, "seasonNumber": season, "episodeNumber": number, "title": title}
                    for fid, season, number, title in self.episodes.get(int(q["seriesId"]), [])]
        if path == self.file_noun:
            return [{"id": fid, "relativePath": rel} for fid, rel in self.records.get(int(q[self.id_param]), {}).items()]
        if path == "filesystem/mediafiles":
            return [{"relativePath": rel} for rel in sorted(self.disk.get(self._folder(unquote(q["path"]).rstrip("/")), ()))]
        if path == "filesystem":
            parent = unquote(q["path"]).rstrip("/")
            names = [p.rsplit("/", 1)[1] for p in self.disk if p.rsplit("/", 1)[0] == parent] + [d for d in self.extra_dirs if d.rsplit("/", 1)[0] == parent]
            return {"parent": parent, "directories": [{"name": n.rsplit("/", 1)[-1]} for n in names], "files": []}
        if path == "command":
            return [{"id": cid, "name": c["name"], "status": self.statuses[cid]} for cid, c in enumerate(self.commands, 1) if self.statuses[cid] in ("queued", "started")]
        if path.startswith("command/"):
            cid = int(path.split("/")[1])
            return {"id": cid, "name": self.commands[cid - 1]["name"], "status": self.statuses[cid]}
        if path.endswith("/folder"):
            return {"folder": self.folder_name(int(path.split("/")[1]))} if int(path.split("/")[1]) in self.items else None
        if path.startswith(f"{self.noun}/"):
            item = self.items.get(int(path.split("/")[1]))
            return dict(item) if item else None
        raise AssertionError(f"unexpected GET {path}")

    def make_post_request(self, endpoint, json=None):
        path = endpoint.split("/api/v3/", 1)[1]
        if path == "tag":
            self.tags[json["label"]] = len(self.tags) + 1
            return {"id": self.tags[json["label"]], "label": json["label"]}
        if path == "command":
            self.commands.append(json)
            cid = len(self.commands)
            if not self.accept_commands:
                self.statuses[cid] = "failed"
                return {}
            self.statuses[cid] = "started" if self.stuck else "failed" if self.fail_command else "completed"
            if json["name"] == "RenameFiles" and json[self.id_param] in self.vanish:
                self.disk.pop(self.items.pop(json[self.id_param])["path"])
            elif json["name"] == "RenameFiles" and self.statuses[cid] == "completed":
                self._rename_files(json[self.id_param], json["files"])
            if json["name"].startswith("Refresh"):
                for mid in json.get("movieIds") or [json.get("seriesId")]:
                    self._refresh(mid)
            return {"id": cid, "name": json["name"], "status": self.statuses[cid]}
        raise AssertionError(f"unexpected POST {path}")

    def _rename_files(self, mid, file_ids):
        folder = self._folder(self.items[mid]["path"])
        for r in list(self.renames.get(mid, [])):
            if r[self.file_key] not in file_ids:
                continue
            self.disk[folder].discard(r["existingPath"])
            if r[self.file_key] not in self.lose:
                self.disk[folder].add(r["newPath"])
            if not self.stale:
                self.records[mid][r[self.file_key]] = r["newPath"]
            self.renames[mid].remove(r)
        if not self.renames.get(mid):
            self.renames.pop(mid, None)

    def _refresh(self, mid):
        """The arr re-reads the folder: records match the disk again (a file gone from disk is dropped)."""
        item = self.items.get(mid)
        if not item:
            return
        on_disk = self.disk.get(self._folder(item["path"]), set())
        fresh = {fid: rel for fid, rel in self.records.get(mid, {}).items() if rel in on_disk}
        known = set(fresh.values())
        for n, rel in enumerate(sorted(on_disk - known), 1):  # files the records did not know yet (renamed under them)
            renamed = next((fid for fid, old in self.records.get(mid, {}).items() if fid not in fresh), None)
            fresh[renamed if renamed is not None else 5000 * mid + n] = rel
        self.records[mid] = fresh

    def make_put_request(self, endpoint, json=None):
        path = urlparse(endpoint).path.split("/api/v3/", 1)[1]
        assert path == f"{self.noun}/editor", path  # the renamer never edits one item's path
        self.puts.append(json)
        ids = json[f"{self.noun}Ids"]
        if "applyTags" in json:
            for mid in ids:
                tags = set(self.items[mid].get("tags", []))
                tags = tags | set(json["tags"]) if json["applyTags"] == "add" else tags - set(json["tags"])
                self.items[mid]["tags"] = sorted(tags)
        elif json.get("moveFiles"):
            self.commands.append({"name": "BulkMoveMovie" if self.kind == "radarr" else "BulkMoveSeries"})
            self.statuses[len(self.commands)] = "completed"
            for mid in ids:
                item = self.items[mid]
                new = f"{json['rootFolderPath']}/{self.folder_name(mid)}"
                if item["path"] in self.disk:  # a folder that is not there is not made: only the arr's path changes
                    files = self.disk.pop(item["path"])
                    self.disk[new] = set() if self.move_loses else files
                item["path"] = new
        return {"ok": True}


def _movie(mid, title, year=2020, *, tags=(), path=None, files=(), **ids):
    return {"id": mid, "title": title, "year": year, "tags": list(tags), "path": path or f"/movies/{title}", "rootFolderPath": "/movies",
            "hasFile": True, "files": list(files), **ids}


def _series(sid, title, year=2020, *, tags=(), path=None, files=(), **ids):
    return {"id": sid, "title": title, "year": year, "tags": list(tags), "path": path or f"/tv/{title}", "rootFolderPath": "/tv", "files": list(files), **ids}


def _rename(fid, old, new, kind="radarr"):
    return {"existingPath": old, "newPath": new, ("movieFileId" if kind == "radarr" else "episodeFileId"): fid}


def _renamer(db, arr, **settings):
    return ArrRenamer(db, arr.kind, instances=[INSTANCE], settings=settings, client_factory=lambda inst: arr, sleep=lambda s: None)


def _names(arr):
    return [c["name"] for c in arr.commands]


def test_dry_run_reports_what_the_arr_would_rename_and_touches_nothing(test_db):
    arr = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta")], renames={1: [_rename(11, "Alpha/a.mkv", "Alpha/Alpha (2020).mkv")]})
    progress = []
    stats = _renamer(test_db, arr, count=0, tag_name="renamed", scope="both").run(dry_run=True, progress=lambda *a: progress.append(a))
    assert (stats["checked"], stats["items"], stats["files"], stats["folders"], stats["tagged"]) == (2, 1, 1, 2, 0)  # both folders lack the year
    assert arr.commands == [] and arr.puts == [] and arr.tags == {}  # no tag made, nothing renamed or tagged
    assert arr.renames[1]  # still pending on the arr
    assert progress[-1][1:3] == (2, 2) and "Beta" in progress[-1][3]


def test_real_run_renames_tags_and_cycles_through_the_library(test_db):
    arr = FakeArr("sonarr", [_series(2, "Beta"), _series(1, "Alpha"), _series(3, "Gamma")],
                  renames={1: [_rename(5, "Season 1/a.mkv", "Season 01/Alpha - S01E01.mkv", "sonarr"), _rename(6, "Season 1/b.mkv", "Season 01/Alpha - S01E02.mkv", "sonarr")],
                           3: [_rename(7, "x.mkv", "Gamma - S01E01.mkv", "sonarr")]})
    run = lambda: _renamer(test_db, arr, count=1, tag_name="renamed").run()  # noqa: E731

    first = run()  # Alpha first (title order): renamed, confirmed, tagged
    assert (first["checked"], first["items"], first["files"], first["failed"], first["tagged"], first["rescans"]) == (1, 1, 2, 0, 1, 0)
    assert arr.commands == [{"name": "RenameFiles", "seriesId": 1, "files": [5, 6]}]  # no refresh: the records already matched
    assert arr.disk["/tv/Alpha"] == {"Season 01/Alpha - S01E01.mkv", "Season 01/Alpha - S01E02.mkv"}
    # The ignore tag is created too (id 1), so it can be picked in the app; the cycle tag is 2.
    assert arr.tags == {"rename-ignore": 1, "renamed": 2} and arr.items[1]["tags"] == [2] and arr.items[2]["tags"] == []

    second = run()  # Beta: nothing to rename, still tagged so the cycle moves on
    assert (second["checked"], second["items"], second["tagged"]) == (1, 0, 1) and arr.items[2]["tags"] == [2]
    third = run()  # Gamma, the last untagged one: this run ends the pass and takes the tag off them all
    assert (third["items"], third["files"], third["cycles"]) == (1, 1, 1) and all(i["tags"] == [] for i in arr.items.values())
    assert arr.puts[-2]["applyTags"] == "add" and (arr.puts[-1]["applyTags"], sorted(arr.puts[-1]["seriesIds"]), arr.puts[-1]["tags"]) == ("remove", [1, 2, 3], [2])

    arr.renames = {1: [_rename(9, "n.mkv", "m.mkv", "sonarr")]}  # Alpha drifted again
    arr.disk["/tv/Alpha"].add("n.mkv")
    arr.records[1][9] = "n.mkv"
    # A series added before the next run joins the new pass in title order, rather than being all that run checks.
    arr.items[4] = {k: v for k, v in _series(4, "Delta").items() if k != "files"}
    arr.disk["/tv/Delta"], arr.records[4] = set(), {}
    fourth = run()
    assert (fourth["cycles"], fourth["checked"], fourth["items"]) == (0, 1, 1) and arr.commands[-1] == {"name": "RenameFiles", "seriesId": 1, "files": [9]}
    assert arr.items[1]["tags"] == [2] and all(arr.items[n]["tags"] == [] for n in (2, 3, 4))

    # A library left fully tagged (an older version, or a clear that failed) is cleared before the run starts.
    for item in arr.items.values():
        item["tags"] = [2]
    fifth = run()
    assert (fifth["cycles"], fifth["checked"]) == (1, 1)
    assert (arr.puts[-2]["applyTags"], sorted(arr.puts[-2]["seriesIds"])) == ("remove", [1, 2, 3, 4]) and arr.puts[-1]["applyTags"] == "add"
    assert arr.items[1]["tags"] == [2] and all(arr.items[n]["tags"] == [] for n in (2, 3, 4))


def test_ignore_tag_and_batches(test_db):
    arr = FakeArr("radarr", [_movie(n, f"M{n}") for n in range(1, 6)] + [_movie(9, "Skip", tags=[1])], tags=("rename-ignore",),
                  renames={n: [_rename(100 + n, f"old{n}.mkv", f"new{n}.mkv")] for n in (1, 3, 5, 9)})
    stats = _renamer(test_db, arr, count=2, batch=True, tag_name="", ignore_tag="rename-ignore").run()
    assert (stats["checked"], stats["items"], stats["files"], stats["failed"], stats["tagged"]) == (5, 3, 3, 0, 0)
    assert arr.renames == {9: arr.renames[9]}  # the ignored movie was never asked about
    assert [c["movieId"] for c in arr.commands if c["name"] == "RenameFiles"] == [1, 3, 5]
    assert "rename-ignore" in arr.tags and len(arr.tags) == 1  # no cycle tag was created


def test_scope_folders_renames_folders_and_the_files_inside_them_only(test_db):
    # Alpha: folder drifted and a file inside; Beta: folder fine, a file drifted; Gamma: folder drifted, files fine.
    arr = FakeArr("radarr", [_movie(1, "Alpha", path="/movies/alpha-old", files=["keep.mkv"]), _movie(2, "Beta", path="/movies/Beta (2020)"),
                             _movie(3, "Gamma", path="/movies/Gamma", files=["Gamma (2020).mkv"])],
                  renames={1: [_rename(11, "a.mkv", "Alpha (2020).mkv")], 2: [_rename(22, "b.mkv", "Beta (2020).mkv")]})
    stats = _renamer(test_db, arr, count=0, tag_name="renamed", scope="folders", refresh_first=True).run()
    assert (stats["items"], stats["files"], stats["folders"], stats["tagged"], stats["failed"]) == (1, 1, 2, 3, 0)
    assert _names(arr) == ["RefreshMovie", "BulkMoveMovie", "RenameFiles", "BulkMoveMovie"]  # refresh first; Alpha's folder, then its file; Gamma's folder
    assert arr.puts[0] == {"movieIds": [1], "rootFolderPath": "/movies", "moveFiles": True}
    assert arr.items[1]["path"] == "/movies/Alpha (2020)" and arr.disk["/movies/Alpha (2020)"] == {"Alpha (2020).mkv", "keep.mkv"}
    assert arr.items[3]["path"] == "/movies/Gamma (2020)" and arr.renames == {2: arr.renames[2]}  # Beta's file waits: its folder was fine
    renamer = _renamer(test_db, arr, count=0, tag_name="", scope="both")
    assert renamer.run()["files"] == 1 and 2 not in arr.renames  # both: Beta's file goes too
    assert renamer.results["arr-1"]["items"][1] == {"label": "Beta (2020)", "files": [("b.mkv", "Beta (2020).mkv")], "folder": None, "why": "", "held": "", "waiting": "", "forced": "",
                                                    "later": False, "gone": False, "missing": False, "failed": "", "note": "", "unsure": 0}


def test_items_whose_folder_is_not_on_disk_are_skipped_and_counted(test_db):
    """The arr has files on record in a folder the disk does not have (a share that is not
    mounted, or files gone): nothing is asked or renamed for it, it counts as checked, and the
    summary says so."""
    arr = FakeArr("radarr", [_movie(1, "Alpha", path="/movies/alpha-old", files=["a.mkv"]), _movie(2, "Beta", path="/movies/beta-old"), _movie(3, "Gamma")],
                  renames={2: [_rename(22, "b.mkv", "Beta (2020).mkv")], 3: [_rename(33, "c.mkv", "Gamma (2020).mkv")]}, absent={1, 2})
    renamer = _renamer(test_db, arr, count=0, tag_name="renamed", scope="both")
    stats = renamer.run()
    assert (stats["checked"], stats["missing"], stats["folders"], stats["items"], stats["tagged"]) == (3, 2, 1, 1, 3)
    assert [c["movieId"] for c in arr.commands if c["name"] == "RenameFiles"] == [3] and 2 in arr.renames  # Beta's file is left alone too
    assert [p["movieIds"] for p in arr.puts if p.get("moveFiles")] == [[3]]  # only Gamma's folder moved
    assert arr.items[1]["path"] == "/movies/alpha-old" and arr.items[2]["path"] == "/movies/beta-old"
    rows = {r["label"]: r for r in renamer.results["arr-1"]["items"]}
    assert rows["Alpha (2020)"]["missing"] and rows["Beta (2020)"]["missing"] and not rows["Gamma (2020)"]["missing"]
    assert "Not on disk: 2 movies skipped" in renamer.results["arr-1"]["lines"]
    # A share that is not mounted: every folder is missing at once, and nothing at all is renamed or moved.
    unmounted = FakeArr("sonarr", [_series(n, f"S{n}", path=f"/tv/s{n}-old", files=["e.mkv"]) for n in range(1, 4)], absent={1, 2, 3},
                        renames={1: [_rename(5, "a.mkv", "S1 - S01E01.mkv", "sonarr")]})
    renamer = _renamer(test_db, unmounted, count=0, tag_name="", scope="both")
    stats = renamer.run()
    assert (stats["checked"], stats["missing"], stats["folders"], stats["items"]) == (3, 3, 0, 0) and unmounted.commands == [] and unmounted.puts == []
    assert renamer.results["arr-1"]["lines"] == ["Not on disk: 3 series skipped"]


def test_a_missing_folder_is_never_swapped_for_one_that_looks_alike(test_db):
    """The arr's folder for an item is gone, and a folder beside it looks like the item's own
    (the same id, or the same title and year). The renamer does not guess: with files on record
    the item is skipped as not on disk, and with none only the name the arr has is brought up
    to date. The look-alike folder and the arr's file records are left as they are."""
    arr = FakeArr("radarr", [
        _movie(1, "Delta", path="/movies/Delta (2020)", imdbId="tt1", tmdbId=5, files=["d.mkv"]),  # its folder got an id added by hand
        _movie(2, "Echo", path="/movies/echo-old", files=["e.mkv"]),  # nothing like it on disk
        _movie(3, "Foxtrot", path="/movies/Foxtrot", imdbId="tt3"),  # no files on record, and a folder with its id beside it
    ], absent={1, 2, 3}, extra_dirs=["/movies/Delta (2020) {imdb-tt1}", "/movies/Foxtrot (2020) {imdb-tt3}"],
        folder_names={3: "Foxtrot (2020) {imdb-tt3} {tmdb-7}"})
    renamer = _renamer(test_db, arr, count=0, tag_name="", scope="both")
    stats = renamer.run()
    assert (stats["checked"], stats["missing"], stats["folders"], stats["files"], stats["failed"]) == (3, 2, 1, 0, 0)
    assert [r["label"] for r in renamer.results["arr-1"]["items"] if r["missing"]] == ["Delta (2020)", "Echo (2020)"]
    assert arr.items[1]["path"] == "/movies/Delta (2020)" and arr.items[2]["path"] == "/movies/echo-old"
    assert arr.records[1] and arr.records[2]  # the arr keeps its file records for them
    assert arr.items[3]["path"] == "/movies/Foxtrot (2020) {imdb-tt3} {tmdb-7}" and _names(arr) == ["BulkMoveMovie"]  # the name only, no rescan
    assert renamer.results["arr-1"]["items"][2]["note"] == "not downloaded yet"
    assert arr.extra_dirs == ["/movies/Delta (2020) {imdb-tt1}", "/movies/Foxtrot (2020) {imdb-tt3}"] and arr.disk == {}
    assert renamer.results["arr-1"]["lines"] == ["Not on disk: 2 movies skipped"]


def test_a_folder_listed_in_other_capitals_counts_only_when_the_arr_sees_its_files(test_db):
    """The disk lists the item's folder in other capitals than the arr has. On a disk that
    ignores capitals the arr still reaches its files there, so the item is renamed as usual; on
    one that tells them apart the arr's folder is gone, and the item is skipped as not on disk."""
    def show(caseless):
        arr = FakeArr("sonarr", [_series(7, "Show", path="/tv/Show")], renames={7: [_rename(35001, "s01e01.mkv", "Show - S01E01.mkv", "sonarr")]}, caseless=caseless)
        arr.disk["/tv/show"] = arr.disk.pop("/tv/Show")
        return arr

    arr = show(caseless=True)
    stats = _renamer(test_db, arr, count=0, tag_name="", scope="files").run()
    assert (stats["missing"], stats["files"], stats["failed"]) == (0, 1, 0) and _names(arr) == ["RenameFiles"]
    assert arr.disk == {"/tv/show": {"Show - S01E01.mkv"}} and arr.items[7]["path"] == "/tv/Show"

    arr = show(caseless=False)
    stats = _renamer(test_db, arr, count=0, tag_name="", scope="files").run()
    assert (stats["missing"], stats["files"]) == (1, 0) and arr.commands == [] and arr.puts == []
    assert arr.disk == {"/tv/show": {"s01e01.mkv"}} and arr.items[7]["path"] == "/tv/Show"


def test_a_file_is_not_renamed_onto_a_name_another_file_has(test_db):
    """Two copies of one episode: the arr wants one renamed onto the name the other has, which it
    cannot do. That rename is not sent and counts as cannot rename, in a dry run too; the item's
    other files and the rest of the run go on. A name one of the renames frees is not taken."""
    def twins():
        return FakeArr("sonarr", [
            _series(1, "Cheers", path="/tv/Cheers (2020)", files=["Specials/S00E08 new.mp4"]),  # the copy that has the new name
            _series(2, "Frasier", path="/tv/Frasier (2020)", files=["f new.mkv"]),  # its only rename is taken too
            _series(3, "Wings", path="/tv/Wings"),  # and a folder whose new name is taken
        ], renames={1: [_rename(11, "Specials/S00E08 old.mp4", "Specials/S00E08 new.mp4", "sonarr"), _rename(12, "s01e01.mkv", "Cheers - S01E01.mkv", "sonarr")],
                    2: [_rename(21, "f old.mkv", "f new.mkv", "sonarr")]}, extra_dirs=["/tv/Wings (2020)"])

    arr = twins()
    renamer = _renamer(test_db, arr, count=0, tag_name="renamed", scope="both")
    stats = renamer.run(dry_run=True)
    assert (stats["checked"], stats["items"], stats["files"], stats["folders"]) == (3, 1, 1, 0) and arr.commands == [] and arr.puts == []
    result = renamer.results["arr-1"]
    assert (result["blocked_files"], result["blocked"]) == (2, 1) and "Cannot rename: 1 folder, 2 files (see the log)" in result["lines"]
    assert result["items"][0]["files"] == [("s01e01.mkv", "Cheers - S01E01.mkv")] and result["items"][1]["files"] == []

    renamer = _renamer(test_db, arr, count=0, tag_name="renamed", scope="both")
    stats = renamer.run()
    assert (stats["items"], stats["files"], stats["failed"], stats["stopped"], stats["tagged"]) == (1, 1, 0, 0, 3)
    assert [(c["name"], c["files"]) for c in arr.commands] == [("RenameFiles", [12])]  # the taken rename was never asked for
    assert arr.disk["/tv/Cheers (2020)"] == {"Specials/S00E08 old.mp4", "Specials/S00E08 new.mp4", "Cheers - S01E01.mkv"}
    assert arr.disk["/tv/Frasier (2020)"] == {"f old.mkv", "f new.mkv"}
    assert "Cannot rename: 1 folder, 2 files (see the log)" in renamer.results["arr-1"]["lines"]

    # A chain: b.mkv is free once it is renamed itself, so a.mkv → b.mkv is left to the arr.
    chain = FakeArr("sonarr", [_series(5, "Chain", path="/tv/Chain (2020)")],
                    renames={5: [_rename(51, "a.mkv", "b.mkv", "sonarr"), _rename(52, "b.mkv", "c.mkv", "sonarr")]})
    renamer = _renamer(test_db, chain, count=0, tag_name="", scope="files")
    assert renamer.run(dry_run=True)["files"] == 2 and renamer.results["arr-1"]["blocked_files"] == 0


def test_an_item_not_downloaded_yet_is_renamed_in_the_arr_only(test_db):
    """No folder and no files yet (an announced movie the arr named before it knew the year): the
    folder name the arr has is brought up to date, so the download lands in the right folder.
    Nothing is made on disk, and an item whose name is still right is left alone."""
    def upcoming():
        return FakeArr("radarr", [_movie(1, "Quiet", 2027, path="/movies/Quiet () {tmdb-9}", tmdbId=9, imdbId="tt9"), _movie(2, "Rush", 0, path="/movies/Rush ()")],
                       absent={1, 2}, folder_names={1: "Quiet (2027) {imdb-tt9} {tmdb-9}", 2: "Rush ()"})

    arr = upcoming()
    renamer = _renamer(test_db, arr, count=0, tag_name="", scope="both")
    stats = renamer.run(dry_run=True)
    assert (stats["checked"], stats["missing"], stats["folders"], stats["files"]) == (2, 0, 1, 0) and arr.commands == [] and arr.puts == []
    rows = {r["label"]: r for r in renamer.results["arr-1"]["items"]}
    assert rows["Quiet (2027)"]["folder"] == ("/movies/Quiet () {tmdb-9}", "/movies/Quiet (2027) {imdb-tt9} {tmdb-9}")
    assert rows["Quiet (2027)"]["note"] == "not downloaded yet" and rows["Rush"]["folder"] is None and not rows["Rush"]["missing"]

    sent = []
    renamer = ArrRenamer(test_db, "radarr", instances=[INSTANCE], settings={"count": 0, "tag_name": "", "scope": "both", "notify": True},
                         client_factory=lambda inst: arr, sleep=lambda s: None, notifier=lambda **kw: sent.append(kw))
    stats = renamer.run()
    assert (stats["missing"], stats["folders"], stats["files"], stats["failed"]) == (0, 1, 0, 0)
    assert arr.puts == [{"movieIds": [1], "rootFolderPath": "/movies", "moveFiles": True}] and _names(arr) == ["BulkMoveMovie"]
    assert arr.items[1]["path"] == "/movies/Quiet (2027) {imdb-tt9} {tmdb-9}" and arr.disk == {} and arr.items[2]["path"] == "/movies/Rush ()"
    assert renamer.results["arr-1"]["items"][0]["note"] == "not downloaded yet"
    assert "**Quiet (2027) (not downloaded yet)**\n```diff\n- Quiet () {tmdb-9}\n+ Quiet (2027) {imdb-tt9} {tmdb-9}\n  no year; no imdb id\n```" in sent[0]["description"]

    # Renaming files only: nothing to do for it, and it is not reported as missing.
    arr = upcoming()
    stats = _renamer(test_db, arr, count=0, tag_name="", scope="files").run()
    assert (stats["checked"], stats["missing"], stats["folders"]) == (2, 0, 0) and arr.commands == [] and arr.puts == []


def test_rename_reasons_say_what_a_new_file_name_changes():
    assert rename_reasons("The Damned (2025) {imdb-tt1} [Remux-2160p x264  HDR10]-GRP.mkv", "The Damned (2025) {imdb-tt1} {tmdb-850439} [Remux-2160p x264 HDR10]-GRP.mkv") == "adds tmdb id 850439"
    assert rename_reasons("Season 1/Show - S01E05 - TBA [WEBDL-1080p].mkv", "Season 01/Show - S01E05 - Pilot [WEBDL-1080p].mkv") == "moves from Season 1 to Season 01; TBA → Pilot"
    assert rename_reasons("Movie (2020) [WEBDL-1080p].mkv", "Movie (2020) [Bluray-1080p].mkv") == "[WEBDL-1080p] → [Bluray-1080p]"
    assert rename_reasons("Movie (2020) {tmdb-5} [Bluray-1080p].mkv", "Movie (2020) [Bluray-1080p].mkv") == "drops tmdb id 5"
    assert rename_reasons("Movie [Bluray-1080p].mkv", "Movie (2020) [Bluray-1080p].mkv") == "adds year 2020"
    assert rename_reasons("movie (2020).mkv", "Movie (2020).mkv") == "capitals"
    assert rename_reasons("What's Up (2020).mkv", "Whats Up (2020).mkv") == "punctuation, drops '"
    assert rename_reasons("Movie (2020) [tmdbid-5].mkv", "Movie (2020) {tmdb-5}.mkv") == "[tmdbid-5] → {tmdb-5}"
    assert rename_reasons("Movie {tmdb-5} {imdb-tt1}.mkv", "Movie {imdb-tt1} {tmdb-5}.mkv") == "id order"
    assert rename_reasons("Movie  (2020).mkv", "Movie (2020).mkv") == "spacing"
    assert rename_reasons("Show - S01E01 [HDTV-720p].avi", "Show - S01E01 [HDTV-720p].mkv") == ".avi → .mkv"
    # Ids and the year are told apart from the rest of a change, and "&" is a word, not punctuation.
    assert rename_reasons("Deadpool (2016) {imdb-tt1} {tmdb-293660} [DVD-480p h265  Opus]-GRP.mkv", "Deadpool (2016) {imdb-tt1} [DVD][AAC 2.0][x264]-GRP.mkv") \
        == "drops tmdb id 293660; [DVD-480p h265 Opus] → [DVD] [AAC 2.0] [x264]"
    # A run of words is one phrase. One small edit in it shows on its own; scattered edits, or one
    # that covers most of it, show the phrase whole, so an episode title is never cut up.
    assert rename_reasons("Deadpool & Wolverine (2024).mkv", "Deadpool and Wolverine (2024).mkv") == "& → and"
    assert rename_reasons("The Matrix (1999).mkv", "The Matrix Reloaded (1999).mkv") == "adds Reloaded"
    assert rename_reasons("The A-Team (1983) - S04E21 - The Trouble with Harry [Bluray-1080p][AAC 2.0][x265]-KONTRAST.mp4",
                          "The A-Team (1983) - S04E21 - A Little Town with an Accent [Bluray-1080p][AAC 2.0][x265]-KONTRAST.mp4") \
        == "The Trouble with Harry → A Little Town with an Accent"
    assert rename_reasons("Show - S01E01 - Old Title [WEBDL-1080p].mkv", "Show - S01E01 - New Title [Bluray-1080p].mkv") == "Old Title → New Title; [WEBDL-1080p] → [Bluray-1080p]"
    assert rename_reasons("Movie (2019) {imdb-tt1}.mkv", "Movie (2020) {imdb-tt2}.mkv") == "year 2019 → 2020; imdb id tt1 → tt2"
    assert rename_reasons("Movie {imdb-}.mkv", "Movie {imdb-tt9}.mkv") == "imdb id (empty) → tt9"


def test_folder_reasons_name_what_differs():
    """The reasons a folder is renamed, as the log and the notification give them. Only the
    wording is checked here: what counts as a problem is covered by the runs above."""
    def why(name, expected, **ids):
        return "; ".join(problems(name, expected, ids)[0])
    assert why("the matrix (1999)", "The Matrix (1999)") == "capitals"
    assert why("What's Up (2020)", "Whats Up (2020)") == "punctuation, drops '"
    assert why("what's up (2020)", "Whats Up (2020)") == "punctuation, drops '; capitals"
    assert why("Mission - Impossible (1996)", "Mission Impossible (1996)") == "punctuation, drops -"
    assert why("Deadpool & Wolverine (2024)", "Deadpool and Wolverine (2024)") == "title: & → and"
    assert why("Untitled Sequel () {imdb-tt2} {tmdb-3}", "Aang (2026) {imdb-tt2} {tmdb-3}", imdb="tt2", tmdb=3) == "title: Untitled Sequel → Aang; no year"
    assert why("Movie  (2020)", "Movie (2020)") == "spacing"
    assert why("Movie (2020) [tmdbid-5]", "Movie (2020) {tmdb-5}", tmdb=5) == "[tmdbid-5] → {tmdb-5}"
    assert why("Movie (2020) {tmdb-5} {imdb-tt1}", "Movie (2020) {imdb-tt1} {tmdb-5}", tmdb=5, imdb="tt1") == "id order"
    assert why("Pokémon (2019)".replace("é", "e\u0301"), "Pokémon (2019)") == "accents encoded differently"
    assert why("Quiet () {tmdb-9}", "Quiet (2027) {imdb-tt9} {tmdb-9}", tmdb=9, imdb="tt9") == "no year; no imdb id"
    assert why("Movie (2020) {tmdb-4}", "Movie (2020) {tmdb-5}", tmdb=5) == "tmdb id 4 should be 5"


def test_folder_blockers_and_irregular_names_are_held_back(test_db):
    arr = FakeArr("radarr", [
        _movie(1, "Alpha", path="/movies/Alpha (2020) {tmdb-5}", tmdbId=5),  # the arr's format now drops the id: irregular
        _movie(2, "Beta", path="/movies/sub/Beta"),  # not directly in a root folder
        _movie(3, "Gamma", path="/movies/Gamma"), _movie(4, "Gamma", 2020, path="/movies/Gamma (2020)"),  # 3 would land on 4's folder
        _movie(5, "Delta", path="/movies/Delta"),  # a folder of that name exists but belongs to nothing
        _movie(6, "Weird", path="/movies/Weird"),
    ], folder_names={1: "Alpha", 6: "We/ird"}, extra_dirs=["/movies/Delta (2020)"],
        renames={1: [_rename(11, "a.mkv", "Alpha.mkv")]})
    renamer = _renamer(test_db, arr, count=0, tag_name="renamed", scope="both")
    stats = renamer.run()
    assert (stats["folders"], stats["items"], stats["held"], stats["tagged"]) == (0, 0, 1, 6)  # nothing moved; held and blocked ones are still tagged
    assert [p["applyTags"] for p in arr.puts[-2:]] == ["add", "remove"]  # all six checked: tagged, and the pass ends with this run
    assert arr.renames == {1: arr.renames[1]}  # the irregular folder's file waits with it
    rows = {r["label"]: r for r in renamer.results["arr-1"]["items"]}
    assert rows["Alpha (2020)"]["held"] == "irregular folder name, check the item's metadata in the arr: the new name drops the year 2020; the new name drops the tmdb id 5"
    assert renamer.results["arr-1"]["blocked"] == 4 and renamer.results["arr-1"]["lines"][-1] == "Cannot rename: 4 folders (see the log)"


def test_a_folder_name_an_earlier_rename_gave_up_is_free_for_a_later_item(test_db):
    """One movie's new folder name is the name another movie just gave up: the same run renames
    both. The name the first one took is taken from then on, and a dry run foresees all of it."""
    def library():
        return FakeArr("radarr", [
            _movie(1, "Alpha", path="/movies/Alpha (2020)"),  # becomes Alpha (2021), giving its name up
            _movie(2, "Beta", path="/movies/Beta"),           # becomes Alpha (2020): the name Alpha left
            _movie(3, "Gamma", path="/movies/Gamma"),         # would become Alpha (2021): Alpha has it now
            _movie(4, "delta", path="/movies/delta (2020)"),  # capitals only: its name stays its own
            _movie(5, "Echo", path="/movies/Echo"),           # would become Delta (2020): still Delta's, in other capitals
        ], folder_names={1: "Alpha (2021)", 2: "Alpha (2020)", 3: "Alpha (2021)", 4: "Delta (2020)", 5: "delta (2020)"})

    for dry_run in (True, False):
        arr = library()
        renamer = _renamer(test_db, arr, count=0, tag_name="renamed", scope="folders")
        stats = renamer.run(dry_run=dry_run)
        result = renamer.results["arr-1"]
        assert (stats["folders"], result["blocked"]) == (3, 2), dry_run
        rows = {r["label"]: r for r in result["items"]}
        assert rows["Beta (2020)"]["folder"] == ("/movies/Beta", "/movies/Alpha (2020)")
        if not dry_run:
            assert {i["id"]: i["path"] for i in arr.items.values()} == {
                1: "/movies/Alpha (2021)", 2: "/movies/Alpha (2020)", 3: "/movies/Gamma", 4: "/movies/Delta (2020)", 5: "/movies/Echo"}


def test_an_irregular_folder_can_be_renamed_anyway_and_says_so(test_db):
    arr = FakeArr("radarr", [_movie(1, "Alpha", path="/movies/Alpha (2020) {tmdb-5}", tmdbId=5)], folder_names={1: "Alpha (2020)"}, renames={1: [_rename(11, "a.mkv", "Alpha (2020).mkv")]})
    sent = []
    renamer = ArrRenamer(test_db, "radarr", instances=[INSTANCE], settings={"tag_name": "", "scope": "both", "allow_irregular": True, "notify": True}, client_factory=lambda inst: arr,
                         sleep=lambda s: None, notifier=lambda **kw: sent.append(kw))
    stats = renamer.run()
    assert (stats["folders"], stats["files"], stats["held"], stats["forced"]) == (1, 1, 0, 1) and arr.items[1]["path"] == "/movies/Alpha (2020)"
    assert sent[0]["event_type"] == "error" and sent[0]["description"].startswith("Needs a look: arr-1: 1 irregular folder(s) renamed anyway")
    assert {"name": "arr-1: 1 irregular", "value": "Alpha (2020): the new name drops the tmdb id 5", "inline": False} in sent[0]["fields"]


def test_an_item_deleted_mid_run_is_skipped_and_an_arr_that_stops_answering_stops_the_run(test_db):
    def deleting(arr, victim, on):
        read = arr.make_get_request

        def get(endpoint):
            if on in endpoint:
                arr.items.pop(victim, None)
            return read(endpoint)
        arr.make_get_request = get
        return arr

    # Beta goes while Alpha is checked; Gamma still runs, and Beta is neither renamed nor tagged.
    arr = deleting(FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta"), _movie(3, "Gamma")], renames={n: [_rename(10 + n, f"o{n}.mkv", f"n{n}.mkv")] for n in (2, 3)}), 2, "rename?movieId=1")
    sent = []
    renamer = ArrRenamer(test_db, "radarr", instances=[INSTANCE], settings={"count": 0, "tag_name": "renamed", "notify": True}, client_factory=lambda inst: arr,
                         sleep=lambda s: None, notifier=lambda **kw: sent.append(kw))
    stats = renamer.run()
    assert (stats["checked"], stats["deleted"], stats["items"], stats["stopped"], stats["tagged"]) == (3, 1, 1, 0, 2)
    assert [c["movieId"] for c in arr.commands if c["name"] == "RenameFiles"] == [3] and stats["cycles"] == 1
    # Beta is gone, so nothing is left to check: the pass ends, and only items that still exist go in the requests.
    assert [(p["applyTags"], sorted(p["movieIds"])) for p in arr.puts[-2:]] == [("add", [1, 3]), ("remove", [1, 3])]
    assert renamer.results["arr-1"]["lines"][0] == "Checked all 3 movies this run; next run starts over"
    assert sent[0]["description"].startswith("Needs a look: arr-1: 1 deleted from Radarr mid-run") and {"name": "arr-1: 1 deleted mid-run", "value": "Beta (2020)", "inline": False} in sent[0]["fields"]

    # Deleted between the check and the rename: the files vanish with it, which is no reason to stop.
    late = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta")], renames={1: [_rename(11, "a.mkv", "b.mkv")], 2: [_rename(22, "c.mkv", "d.mkv")]}, vanish={1})
    stats = _renamer(test_db, late, count=0, tag_name="").run()
    assert (stats["deleted"], stats["items"], stats["stopped"], stats["failed"]) == (1, 1, 0, 0)

    # The arr goes down: nothing passes for "up to date", nothing is tagged, and the run stops.
    down = FakeArr("radarr", [_movie(1, "Alpha"), _movie(2, "Beta")])
    read = down.make_get_request

    def fail_at_beta(endpoint):
        if "rename?movieId=2" in endpoint:
            down.down = True
        return read(endpoint)
    down.make_get_request = fail_at_beta
    renamer = _renamer(test_db, down, count=0, tag_name="renamed")
    stats = renamer.run()
    assert (stats["checked"], stats["deleted"], stats["stopped"], stats["tagged"]) == (1, 0, 1, 1) and sorted(down.puts[-1]["movieIds"]) == [1]
    assert renamer.results["arr-1"]["stopped"] == "the arr did not answer when asked for what needs renaming" and renamer.results["arr-1"]["not_reviewed"] == 1

    # An arr too old for the folder name route says so instead of skipping the check.
    old = FakeArr("radarr", [_movie(1, "Alpha")])
    read_old = old.make_get_request
    old.make_get_request = lambda endpoint: None if endpoint.endswith("/folder") else read_old(endpoint)
    renamer = _renamer(test_db, old, count=0, tag_name="", scope="folders")
    assert renamer.run()["stopped"] == 1 and "Folder renames need Sonarr 4.0.11 or Radarr 5.22.3 or newer" in renamer.results["arr-1"]["stopped"]


def test_a_library_the_arr_will_not_list_fails_the_instance(test_db):
    arr = FakeArr("radarr", [_movie(1, "Alpha")])
    arr.down = True
    renamer = _renamer(test_db, arr, count=0, tag_name="")
    stats = renamer.run()
    assert (stats["instances"], stats["failed_instances"], stats["checked"]) == (0, 1, 0) and renamer.results["arr-1"]["error"] == "the arr did not answer when asked for its library"


def test_the_safety_checks_stop_the_run_when_the_arr_will_not_answer_them(test_db):
    def failing(arr, suffix):
        read = arr.make_get_request
        arr.make_get_request = lambda endpoint: None if endpoint.split("?")[0].endswith(suffix) else read(endpoint)
        return arr

    # No tag list: the ignore tag cannot be honoured, so nothing is renamed.
    tags = failing(FakeArr("radarr", [_movie(1, "Alpha", tags=[1])], tags=("rename-ignore",), renames={1: [_rename(11, "a.mkv", "b.mkv")]}), "/api/v3/tag")
    renamer = _renamer(test_db, tags, count=0, tag_name="")
    stats = renamer.run()
    assert (stats["failed_instances"], stats["items"]) == (1, 0) and tags.commands == [] and renamer.results["arr-1"]["error"] == "the arr did not answer when asked for its tags"
    # No root folder list: a nested folder must not be moved out to the root.
    roots = failing(FakeArr("radarr", [_movie(1, "Alpha", path="/movies/sub/Alpha")], roots=["/movies"]), "/api/v3/rootfolder")
    renamer = _renamer(test_db, roots, count=0, tag_name="", scope="folders")
    stats = renamer.run()
    assert (stats["stopped"], stats["folders"]) == (1, 0) and roots.puts == [] and "its root folders" in renamer.results["arr-1"]["stopped"]
    # No folder listing: a folder could be renamed onto one that exists.
    listing = failing(FakeArr("radarr", [_movie(1, "Alpha", path="/movies/Alpha")]), "/api/v3/filesystem")
    renamer = _renamer(test_db, listing, count=0, tag_name="", scope="folders")
    assert renamer.run()["stopped"] == 1 and listing.puts == [] and "the folders in /movies" in renamer.results["arr-1"]["stopped"]


def test_rename_limit_leaves_the_rest_for_later_runs_untagged(test_db):
    arr = FakeArr("radarr", [_movie(n, f"M{n}") for n in range(1, 6)], renames={n: [_rename(100 + n, f"old{n}.mkv", f"new{n}.mkv")] for n in (1, 2, 4, 5)})
    renamer = _renamer(test_db, arr, count=0, tag_name="renamed", max_renames=2)
    stats = renamer.run()
    assert (stats["items"], stats["later"], stats["tagged"]) == (2, 2, 3)  # M1, M2 renamed; M3 fine; M4, M5 wait and stay untagged
    assert [c["movieId"] for c in arr.commands if c["name"] == "RenameFiles"] == [1, 2] and sorted(arr.puts[-1]["movieIds"]) == [1, 2, 3]
    assert renamer.results["arr-1"]["lines"] == ["Checked 5 movies this run; total 3 of 5 movies checked, 2 left to check, 1 more run", "Rename limit 2 - 2 more to rename in 1 run"]
    again = _renamer(test_db, arr, count=0, tag_name="renamed", max_renames=2)
    assert again.run()["items"] == 2 and again.results["arr-1"]["lines"][-1] == "Rename limit 2 - none left to rename"
    dry = _renamer(test_db, FakeArr("radarr", [_movie(n, f"M{n}") for n in range(1, 4)], renames={n: [_rename(100 + n, "o.mkv", "n.mkv")] for n in (1, 2, 3)}),
                   count=0, tag_name="", max_renames=1)
    assert dry.run(dry_run=True)["later"] == 2  # a dry run stops at the same point


def test_scan_until_the_rename_limit_checks_as_many_items_as_it_takes(test_db):
    """With the option on, a run ignores the count and checks items in turn until it has renamed
    the limit's worth; what it did not reach stays untagged for the next run."""
    def library():
        return FakeArr("radarr", [_movie(n, f"M{n:02}") for n in range(1, 41)],
                       renames={n: [_rename(100 + n, f"old{n}.mkv", f"new{n}.mkv")] for n in (2, 5, 33, 38)})
    arr = library()
    progress = []
    renamer = _renamer(test_db, arr, count=3, batch=True, tag_name="renamed", max_renames=2, scan_to_limit=True)
    stats = renamer.run(progress=lambda *a: progress.append(a))
    assert (stats["checked"], stats["items"], stats["later"], stats["tagged"]) == (5, 2, 0, 5)  # M01 to M05; stops at the 2nd rename, past the count of 3
    assert [c["movieId"] for c in arr.commands if c["name"] == "RenameFiles"] == [2, 5]
    assert renamer.results["arr-1"]["lines"] == ["Checked 5 movies this run; total 5 of 40 movies checked, 35 left to check", "Rename limit 2 - reached"]
    assert progress[-1][1:3] == (40, 40)  # the items it never reached count as done for the progress bar

    # The next run carries on from M06, through several of its small batches, to the end of the pass.
    renamer = _renamer(test_db, arr, count=3, tag_name="renamed", max_renames=2, scan_to_limit=True)
    stats = renamer.run()
    assert (stats["checked"], stats["items"], stats["cycles"]) == (33, 2, 0) and [c["movieId"] for c in arr.commands if c["name"] == "RenameFiles"][2:] == [33, 38]
    assert renamer.results["arr-1"]["lines"] == ["Checked 33 movies this run; total 38 of 40 movies checked, 2 left to check", "Rename limit 2 - reached"]
    renamer = _renamer(test_db, arr, count=3, tag_name="renamed", max_renames=2, scan_to_limit=True)
    stats = renamer.run()  # the last two need nothing: the limit is not reached and the pass ends
    assert (stats["checked"], stats["items"], stats["cycles"]) == (2, 0, 1)
    assert renamer.results["arr-1"]["lines"] == ["Checked 2 movies this run; total 40 of 40 movies checked, next run starts over", "Rename limit 2 - not reached, 0 movies renamed"]

    # A dry run stops at the same point and changes nothing; without a limit the option does nothing.
    arr = library()
    dry = _renamer(test_db, arr, count=3, tag_name="renamed", max_renames=1, scan_to_limit=True)
    assert dry.run(dry_run=True)["checked"] == 2 and arr.commands == [] and arr.puts == []
    assert dry.results["arr-1"]["lines"] == ["Would check 2 movies this run; total 2 of 40 movies checked, 38 left to check", "Rename limit 1 - reached"]
    assert _renamer(test_db, arr, count=3, tag_name="", max_renames=0, scan_to_limit=True).run(dry_run=True)["checked"] == 3


def test_tba_titles_wait_and_case_or_punctuation_only_renames_can_be_ignored(test_db):
    arr = FakeArr("sonarr", [_series(1, "Alpha", path="/tv/alpha (2020)"), _series(2, "Beta", path="/tv/Beta (2020)")],
                  renames={1: [_rename(5, "S01E01.mkv", "S01E01 - TBA.mkv", "sonarr"), _rename(6, "S01E02 - pilot.mkv", "S01E02 - Pilot.mkv", "sonarr"),
                               _rename(7, "S01E03 - TBA.mkv", "S01E03 - Football.mkv", "sonarr")],
                           2: [_rename(8, "Whats Up.mkv", "What's Up!.mkv", "sonarr"), _rename(9, "x.mkv", "Beta - S01E01 - TBA.mkv", "sonarr")]})
    renamer = _renamer(test_db, arr, count=0, tag_name="", scope="both", ignore_case=True, ignore_punctuation=True)
    stats = renamer.run()
    assert (stats["files"], stats["held"], stats["ignored"], stats["ignored_files"], stats["folders"]) == (1, 2, 1, 2, 0)
    assert arr.commands == [{"name": "RenameFiles", "seriesId": 1, "files": [7]}]  # only the TBA that got a real title
    rows = renamer.results["arr-1"]["items"]
    assert rows[0]["waiting"] == "1 file(s) whose episode title is still TBA" and rows[0]["files"] == [("S01E03 - TBA.mkv", "S01E03 - Football.mkv")]
    assert rows[1]["held"] == "1 file(s) whose episode title is still TBA" and rows[1]["files"] == []
    assert renamer.results["arr-1"]["lines"] == ["Ignored folders: 1 capitals", "Ignored files: 1 capitals, 1 punctuation"]
    strict = _renamer(test_db, arr, count=0, tag_name="", scope="both", hold_tba=False)  # file 7 is renamed already: 4 left
    assert strict.run(dry_run=True)["files"] == 4 and strict.results["arr-1"]["items"][0]["folder"] == ("/tv/alpha (2020)", "/tv/Alpha (2020)")


def test_tba_fill_tells_a_title_being_filled_in_from_everything_else():
    fill, unsure, nothing = ("fill", ""), "unsure", ("", "")
    # The one change is TBA becoming the title.
    assert tba_fill("Season 01/Show - S01E01 - TBA.mkv", "Season 01/Show - S01E01 - Pilot.mkv", ["Pilot"]) == fill
    assert tba_fill("Show - S01E01 - TBA [WEBDL-1080p].mkv", "Show - S01E01 - The Long Night [WEBDL-1080p].mkv", ["The Long Night"]) == fill
    # The naming format may tidy the title: punctuation, capitals, accents on Latin letters.
    assert tba_fill("S01E02 - TBA.mkv", "S01E02 - Whats Up Doc.mkv", ["What's Up, Doc?"]) == fill
    assert tba_fill("S01E03 - TBA.mkv", "S01E03 - Marnukh.mkv", ["Marnûkh"]) == fill
    # A file holding several episodes: the titles joined, or merged without their part endings; a long one cut short.
    assert tba_fill("S01E04-E05 - TBA.mkv", "S01E04-E05 - Arrival + Departure.mkv", ["Arrival", "Departure"]) == fill
    assert tba_fill("S01E06-E07 - TBA.mkv", "S01E06-E07 - The End.mkv", ["The End (1)", "The End (2)"]) == fill
    assert tba_fill("S01E08 - TBA.mkv", "S01E08 - A Very Long Title That Goes....mkv", ["A Very Long Title That Goes On And On"]) == fill
    # Not clear-cut: a person should look.
    assert tba_fill("Season 1/S01E01 - TBA.mkv", "Season 01/S01E01 - Pilot.mkv", ["Pilot"]) == (unsure, "it also moves to another season folder")
    assert tba_fill("S01E01 - TBA [SDTV].mkv", "S01E01 - Pilot [HDTV-720p].mkv", ["Pilot"]) == (unsure, "more than the episode title changes")
    assert tba_fill("S01E01 - TBA.mkv", "S01E01 - Title TBA Soon.mkv", ["Title TBA Soon"]) == (unsure, "its new title has TBA in it")
    assert tba_fill("S01E01 - TBA.mkv", "S01E01 - Pilot.mkv", []) == (unsure, "the arr gave no episode title to check it against")
    assert tba_fill("S01E01 - TBA.mkv", "S01E01 - Something Else.mkv", ["Pilot"]) == (unsure, "more than the episode title changes")
    # Nothing to do with a TBA title being filled in.
    assert tba_fill("S01E01 - Old.mkv", "S01E01 - New.mkv", ["New"]) == nothing        # no TBA to begin with
    assert tba_fill("S01E01 - TBA.mkv", "S01E01 - Tba.mkv", ["TBA"]) == nothing         # capitals only
    assert tba_fill("S01E01 - FOOTBALL.mkv", "S01E01 - Soccer.mkv", ["Soccer"]) == nothing  # no TBA as a word of its own
    assert tba_fill("S01E01 - TBA.mkv", "Show - S01E01 - TBA.mkv", ["TBA"]) == nothing  # the format changed, the title is still unknown


def test_scope_tba_renames_only_files_whose_tba_title_got_filled_in(test_db):
    def library():
        return FakeArr("sonarr", [_series(1, "Alpha", path="/tv/Alpha"), _series(2, "Beta", path="/tv/Beta")], renames={
            1: [_rename(11, "Season 01/Alpha - S01E01 - TBA.mkv", "Season 01/Alpha - S01E01 - Pilot.mkv", "sonarr"),          # a title filled in
                _rename(12, "Season 01/Alpha - S01E02 - TBA [SDTV].mkv", "Season 01/Alpha - S01E02 - Second [HDTV-720p].mkv", "sonarr"),  # more than the title
                _rename(13, "Season 01/Alpha - S01E03 - Old Name.mkv", "Season 01/Alpha - S01E03 - New Name.mkv", "sonarr"),  # no TBA: not this run's
                _rename(14, "Season 1/Alpha - S01E04 - TBA.mkv", "Season 01/Alpha - S01E04 - Fourth.mkv", "sonarr")],          # and a season folder move
            2: [_rename(21, "b.mkv", "Beta.mkv", "sonarr")],
        }, episodes={1: [(11, 1, 1, "Pilot"), (12, 1, 2, "Second"), (13, 1, 3, "New Name"), (14, 1, 4, "Fourth")]})

    arr = library()
    renamer = _renamer(test_db, arr, count=0, tag_name="", scope="tba", notify=True)
    sent = []
    renamer._notifier = lambda **kw: sent.append(kw)
    stats = renamer.run()
    assert (stats["items"], stats["files"], stats["folders"], stats["held"], stats["failed"]) == (1, 1, 0, 1, 0)
    assert [(c["name"], c.get("files")) for c in arr.commands] == [("RenameFiles", [11])]  # no folder move: both folders lack the year and stay
    assert [r["episodeFileId"] for r in arr.renames[1]] == [12, 13, 14] and arr.renames[2]  # the rest is left for a files run
    assert arr.title_reads == [1]  # titles are read only for a series with a TBA file to rename
    result = renamer.results["arr-1"]
    row = result["items"][0]
    assert row["files"] == [("Season 01/Alpha - S01E01 - TBA.mkv", "Season 01/Alpha - S01E01 - Pilot.mkv")]
    assert (row["unsure"], row["waiting"], row["held"]) == (2, "2 file(s) where more than a TBA title may change", "")
    assert "Left alone: 2 files where more than a TBA title may change (see the log)" in result["lines"]
    # The unclear ones need a look, so the summary goes out as an error and says why.
    assert sent[0]["event_type"] == "error" and "arr-1: 2 file(s) held back, more than a TBA title may change" in sent[0]["description"]
    assert "TBA titles only" in next(f["value"] for f in sent[0]["fields"] if f["name"] == "arr-1: settings")

    # A dry run reports the same and renames nothing; a series with only unclear ones is held back as a whole.
    arr = library()
    arr.renames[1] = [r for r in arr.renames[1] if r["episodeFileId"] != 11]
    renamer = _renamer(test_db, arr, count=0, tag_name="", scope="tba")
    stats = renamer.run(dry_run=True)
    assert (stats["items"], stats["files"], stats["held"]) == (0, 0, 1) and arr.commands == []
    assert renamer.results["arr-1"]["items"][0]["held"] == "2 file(s) where more than a TBA title may change"

    # Movies have no episode titles: the scope is Sonarr's, and Radarr settings fall back to files.
    assert normalize_one({"scope": "tba"}, "sonarr")["scope"] == "tba" and normalize_one({"scope": "tba"})["scope"] == "tba"
    assert normalize_one({"scope": "tba"}, "radarr")["scope"] == "files"


def test_verification_stops_the_run_when_files_go_missing_or_the_arr_loses_track(test_db):
    lost = FakeArr("radarr", [_movie(1, "Alpha", path="/movies/Alpha (2020)", files=["extra.mkv"]), _movie(2, "Beta", path="/movies/Beta (2020)")],
                   renames={1: [_rename(11, "a.mkv", "Alpha (2020).mkv")], 2: [_rename(22, "b.mkv", "Beta (2020).mkv")]}, lose={11})
    renamer = _renamer(test_db, lost, count=0, tag_name="renamed")
    stats = renamer.run()
    assert (stats["items"], stats["failed"], stats["stopped"], stats["tagged"]) == (0, 1, 1, 0)  # nothing else is touched or tagged
    assert renamer.results["arr-1"]["stopped"] == "Alpha (2020): 1 file(s) are gone from /movies/Alpha (2020). Not refreshing, as a refresh would drop them from the arr"
    assert renamer.results["arr-1"]["not_reviewed"] == 1 and _names(lost) == ["RenameFiles"] and lost.renames[2]

    stale = FakeArr("radarr", [_movie(1, "Alpha", path="/movies/Alpha (2020)")], renames={1: [_rename(11, "a.mkv", "Alpha (2020).mkv")]}, stale=True)
    renamer = _renamer(test_db, stale, count=0, tag_name="")
    stats = renamer.run()  # the records drifted: refreshed, then fine
    assert (stats["items"], stats["rescans"], stats["failed"]) == (1, 1, 0) and _names(stale) == ["RenameFiles", "RefreshMovie"]
    assert renamer.results["arr-1"]["items"][0]["note"] == "rescan needed" and stale.records[1] == {11: "Alpha (2020).mkv"}

    moved = FakeArr("radarr", [_movie(1, "Alpha", path="/movies/alpha", files=["a.mkv"])], move_loses=True)
    renamer = _renamer(test_db, moved, count=0, tag_name="", scope="folders")
    stats = renamer.run()
    assert (stats["folders"], stats["failed"], stats["stopped"]) == (0, 1, 1) and "did not all arrive" in renamer.results["arr-1"]["stopped"]


def test_rejected_failed_and_stuck_commands_stop_the_instance(test_db, monkeypatch):
    monkeypatch.setattr("services.arr_renamer.time.monotonic", iter(range(0, 1_000_000, 1000)).__next__)  # every wait deadline passes at once
    for arr, why in ((FakeArr("sonarr", [_series(1, "Alpha")], accept_commands=False, renames={1: [_rename(1, "a.mkv", "b.mkv", "sonarr")]}), "was not accepted"),
                     (FakeArr("sonarr", [_series(1, "Alpha")], fail_command=True, renames={1: [_rename(1, "a.mkv", "b.mkv", "sonarr")]}), "ended 'failed'"),
                     (FakeArr("sonarr", [_series(1, "Alpha")], stuck=True, renames={1: [_rename(1, "a.mkv", "b.mkv", "sonarr")]}), "still was not done after 60 minutes")):
        renamer = _renamer(test_db, arr, count=0, tag_name="")
        stats = renamer.run()
        assert (stats["items"], stats["failed"], stats["stopped"]) == (0, 1, 1) and why in renamer.results["arr-1"]["stopped"]

    down = ArrRenamer(test_db, "radarr", instances=[INSTANCE, {**INSTANCE, "name": "arr-2"}], settings={},
                      client_factory=lambda inst: None if inst["name"] == "arr-2" else FakeArr("radarr", [_movie(1, "Alpha")]), sleep=lambda s: None)
    stats = down.run()
    assert (stats["instances"], stats["unreachable"], stats["checked"]) == (1, 1, 1)


def test_rename_settings_normalize_and_validate(test_db):
    defaults = {"count": 25, "batch": False, "tag_name": "renamed", "ignore_tag": "rename-ignore", "scope": "files", "max_renames": 0, "scan_to_limit": False, "ignore_case": False,
                "ignore_punctuation": False, "allow_irregular": False, "hold_tba": True, "refresh_first": False, "title_filter": "", "notify": False, "notify_all": False}
    assert normalize_one(None) == defaults
    one = normalize_one({"count": "7", "batch": 1, "tag_name": " done ", "ignore_tag": "", "scope": "BOTH", "max_renames": "3", "ignore_case": True, "hold_tba": 0,
                         "title_filter": " Alien ", "notify": "yes", "notify_all": 1})
    assert one == {**defaults, "count": 7, "batch": True, "tag_name": "done", "ignore_tag": "", "scope": "both", "max_renames": 3, "ignore_case": True,
                   "hold_tba": False, "title_filter": "Alien", "notify": True, "notify_all": True}
    assert normalize_one({"count": 3, "scope": "junk"}) == {**defaults, "count": 3}
    assert normalize_one({"count": -3})["count"] == 0 and normalize_one({"count": "lots"})["count"] == 25 and len(normalize_one({"title_filter": "x" * 300})["title_filter"]) == 200
    assert normalize_one({"max_renames": 5000})["max_renames"] == 1000
    # A profile holds settings per instance: a 4K Radarr can run its own count; an instance the
    # profile does not name gets the defaults.
    renamer = ArrRenamer(test_db, "radarr", instances=[], per_instance={"Radarr4K": {"count": 3, "scope": "junk"}})
    assert renamer.settings_for("Radarr4K") == {**defaults, "count": 3} and renamer.settings_for("Other") == defaults
    assert ArrRenamer(test_db, "radarr", instances=[], settings={"count": 2}).settings_for("Anything")["count"] == 2  # one set for all (tests, previews)
    # A tag the arr would never make, or a cycle tag doubling as the ignore tag, cannot be saved.
    assert tag_problem({"tag_name": "my tag"}) == "The cycle tag 'my tag' can only have letters, digits and -."
    assert tag_problem({"tag_name": "Same", "ignore_tag": "same"}) == "The cycle tag and the ignore tag must differ."
    assert tag_problem(defaults) == ""


def test_a_run_with_an_unusable_tag_does_not_start(test_db):
    arr = FakeArr("radarr", [_movie(1, "Alien")], renames={1: [_rename(11, "a", "b")]})
    sent = []
    renamer = ArrRenamer(test_db, "radarr", instances=[INSTANCE], settings={"tag_name": "re named", "notify": True}, client_factory=lambda inst: arr,
                         sleep=lambda s: None, notifier=lambda **kw: sent.append(kw))
    stats = renamer.run()
    assert (stats["instances"], stats["failed_instances"], stats["checked"]) == (0, 1, 0) and arr.commands == []
    assert renamer.results["arr-1"]["error"] == "did not start: The cycle tag 're named' can only have letters, digits and -."
    assert sent[0]["event_type"] == "error" and sent[0]["fields"] == [{"name": "arr-1: run failed", "value": renamer.results["arr-1"]["error"], "inline": False}]


def test_title_filter_and_the_ignore_tag_that_gets_created(test_db):
    arr = FakeArr("radarr", [_movie(1, "Alien"), _movie(2, "Aliens"), _movie(3, "Brazil")], renames={1: [_rename(11, "a", "b")], 3: [_rename(33, "c", "d")]})
    stats = _renamer(test_db, arr, title_filter="alien", tag_name="").run()
    assert (stats["checked"], stats["items"], stats["files"]) == (2, 1, 1) and [c["movieId"] for c in arr.commands if c["name"] == "RenameFiles"] == [1]
    assert "rename-ignore" in arr.tags  # the ignore tag is created, so it can be picked in the app
    nothing = FakeArr("radarr", [_movie(1, "Alien")])
    assert _renamer(test_db, nothing, title_filter="zzz").run()["checked"] == 0 and nothing.commands == []
    dry = FakeArr("radarr", [_movie(1, "Alien")])
    _renamer(test_db, dry).run(dry_run=True)
    assert dry.tags == {}  # a dry run creates no tag at all


def test_progress_line_tracks_the_pass_through_the_library(test_db):
    arr = FakeArr("sonarr", [_series(n, f"S{n}") for n in range(1, 8)])
    first = _renamer(test_db, arr, count=3, tag_name="renamed")
    first.run()
    assert first.results["arr-1"]["lines"] == ["Checked 3 series this run; total 3 of 7 series checked, 4 left to check, 2 more runs at 3 a run"]
    second = _renamer(test_db, arr, count=3, tag_name="renamed")
    second.run(dry_run=True)  # a dry run says what it would do, and tags nothing
    assert second.results["arr-1"]["lines"] == ["Would check 3 series this run; total 6 of 7 series checked, 1 left to check, 1 more run at 3 a run"]
    second.run()
    third = _renamer(test_db, arr, count=3, tag_name="renamed")
    third.run()
    assert third.results["arr-1"]["lines"] == ["Checked 1 series this run; total 7 of 7 series checked, next run starts over"]
    whole = _renamer(test_db, arr, count=0, tag_name="renamed")
    whole.run()  # the third run ended the pass, so this one checks them all again
    assert whole.results["arr-1"]["lines"] == ["Checked all 7 series this run; next run starts over"]
    untracked = _renamer(test_db, arr, count=3, tag_name="")
    untracked.run()
    assert untracked.results["arr-1"]["lines"] == []  # no cycle tag: every run checks the same items, nothing to track


def test_notification_pages_every_rename_when_asked_and_trims_otherwise(test_db):
    """A long list: with notify_all the summary goes first, then every rename, a page per message
    without a ping; without it one message lists what fits and counts the rest."""
    renames = [_rename(100 + i, f"old {i:02d} - Alien (2020) - S01E{i:02d} - Episode {i} [WEBDL-1080p][x264]-GRP.mkv",
                       f"new {i:02d} - Alien (2020) - S01E{i:02d} - Episode {i} [WEBDL-1080p][x264]-GRP.mkv") for i in range(60)]
    arr = FakeArr("radarr", [_movie(1, "Alien", path="/movies/Alien (2020)")], renames={1: renames})
    sent = []
    ArrRenamer(test_db, "radarr", instances=[INSTANCE], settings={"notify": True, "notify_all": True, "tag_name": "renamed"}, sleep=lambda s: None,
               client_factory=lambda inst: arr, notifier=lambda **kw: sent.append(kw)).run(dry_run=True)
    pages = sent[1:]
    assert len(pages) >= 2 and [m["title"] for m in pages] == [f"Radarr rename (dry run) · changes {n} of {len(pages)}" for n in range(1, len(pages) + 1)]
    assert all(m["mention"] is False and m["fields"] == [] and m["description"].startswith("**arr-1: 1 of 1 would change**\n**Alien (2020)**\n```diff\n- old ") for m in pages)
    assert pages[0]["description"].startswith("**arr-1: 1 of 1 would change**\n**Alien (2020)**\n```diff\n- old 00 - ")
    assert all(m["description"].endswith("\n```") for m in pages)  # every piece of a long item keeps its own fences
    assert all(len(m["description"]) <= 3500 for m in pages)
    shown = "\n".join(m["description"] for m in pages)
    assert all(f"- old {i:02d} - " in shown and f"+ new {i:02d} - " in shown for i in range(60))
    for m in pages:  # a page break never parts a rename from its reason
        body = m["description"].split("```diff\n", 1)[1][: -len("\n```")].split("\n")
        assert [line[:2] for line in body] == ["- ", "+ ", "  "] * (len(body) // 3)
        assert all(why == f"  old {why[6:8]} → new {why[6:8]}" for why in body[2::3])  # each reason names its own rename
    # The summary came first: the counts, where the list is, and the progress line; it is the one that may ping.
    assert sent[0]["title"] == "Radarr rename (dry run)" and "mention" not in sent[0] and sent[0]["description"] == ""
    assert sent[0]["fields"] == [
        {"name": "arr-1: settings", "value": "files, 25 per run, cycle tag 'renamed', ignore tag 'rename-ignore', dry run", "inline": False},
        {"name": "arr-1: 1 of 1 would change", "value": f"1 movie with file renames, 60 files total\nListed in the {len(pages)} message(s) below", "inline": False},
        {"name": "arr-1: this run", "value": "Would check all 1 movies this run; next run starts over", "inline": False},
    ]
    sent.clear()
    ArrRenamer(test_db, "radarr", instances=[INSTANCE], settings={"notify": True, "tag_name": ""}, sleep=lambda s: None,
               client_factory=lambda inst: arr, notifier=lambda **kw: sent.append(kw)).run(dry_run=True)
    assert len(sent) == 1 and sent[0]["description"].startswith("**arr-1: 1 of 1 would change**\n**Alien (2020)**\n```diff\n")
    assert "- old 00 - " in sent[0]["description"] and "- old 59 - " not in sent[0]["description"]
    assert sent[0]["description"].endswith("\n```")  # one item: it is cut inside its block, nothing is counted after it
    assert len(sent[0]["description"]) <= 3500


def test_notification_lists_changes_held_items_progress_and_pings_on_trouble(test_db):
    good = FakeArr("radarr", [_movie(1, "Alien", path="/movies/Alien (2020)"), _movie(2, "Brazil", path="/movies/Brazil (2020) {tmdb-9}", tmdbId=9)],
                   folder_names={2: "Brazil"}, renames={1: [_rename(11, "a.mkv", "b.mkv")]})

    class Broken:
        url = "http://broken"

        def make_get_request(self, endpoint):
            raise RuntimeError("boom")

    instances = [{"name": "bad", "url": "http://broken", "api_key": "k"}, {"name": "good", "url": "http://arr", "api_key": "k"}]
    sent = []
    renamer = ArrRenamer(test_db, "radarr", instances=instances, settings={"notify": True, "tag_name": "renamed", "count": 1, "scope": "both"}, sleep=lambda s: None,
                         client_factory=lambda inst: Broken() if inst["name"] == "bad" else good, notifier=lambda **kw: sent.append(kw))
    stats = renamer.run()
    assert (stats["instances"], stats["failed_instances"], stats["items"]) == (1, 1, 1) and renamer.results["bad"]["error"] == "boom"
    # One message: the renames as a diff block in the description (old on -, new on +), one field per
    # instance and topic for the rest: the failure named, then the progress line.
    assert len(sent) == 1 and sent[0]["feature_key"] == "tools" and sent[0]["title"] == "Radarr rename" and sent[0]["event_type"] == "error"
    assert sent[0]["description"] == "Needs a look: bad: the run failed\n\n**good: 1 of 1 changed**\n**Alien (2020)**\n```diff\n- a.mkv\n+ b.mkv\n  a → b\n```"
    assert [(f["name"], f["value"]) for f in sent[0]["fields"]] == [
        ("bad: run failed", "boom"),
        ("good: settings", "files and folders, 1 per run, cycle tag 'renamed', ignore tag 'rename-ignore', apply"),
        ("good: 1 of 1 changed", "1 movie with file renames, 1 file total"),
        ("good: this run", "Checked 1 movies this run; total 1 of 2 movies checked, 1 left to check, 1 more run at 1 a run"),
    ]
    sent.clear()
    renamer = ArrRenamer(test_db, "radarr", instances=instances[1:], settings={"notify": True, "tag_name": "renamed", "count": 1, "scope": "both"}, sleep=lambda s: None,
                         client_factory=lambda inst: good, notifier=lambda **kw: sent.append(kw))
    renamer.run()  # Brazil: an irregular folder name is held back, which is worth a ping
    assert sent[0]["event_type"] == "error" and sent[0]["color"] == 0xE74C3C and sent[0]["description"] == "Needs a look: good: 1 irregular folder(s) held back"
    assert sent[0]["fields"][0]["name"] == "good: settings"
    assert sent[0]["fields"][1] == {"name": "good: 1 held back", "value": "Brazil (2020): irregular folder name, check the item's metadata in the arr: the new name drops the year 2020; the new name drops the tmdb id 9", "inline": False}
    assert sent[0]["fields"][2]["value"] == "Checked 1 movies this run; total 2 of 2 movies checked, next run starts over"
    sent.clear()
    good.renames = {1: [_rename(12, "c.mkv", "d.mkv")]}
    good.disk["/movies/Alien (2020)"].add("c.mkv")
    good.records[1][12] = "c.mkv"
    ArrRenamer(test_db, "radarr", instances=instances[1:], settings={"notify": True, "tag_name": "", "title_filter": "alien"}, sleep=lambda s: None,
               client_factory=lambda inst: good, notifier=lambda **kw: sent.append(kw)).run()
    assert sent[0]["event_type"] == "success" and sent[0]["color"] == 0x2ECC71  # a plain rename: no "needs a look"
    assert sent[0]["description"] == "**good: 1 of 1 changed**\n**Alien (2020)**\n```diff\n- c.mkv\n+ d.mkv\n  c → d\n```"
    sent.clear()
    ArrRenamer(test_db, "radarr", instances=instances[1:], settings={"notify": True, "tag_name": "", "title_filter": "alien"}, sleep=lambda s: None,
               client_factory=lambda inst: good, notifier=lambda **kw: sent.append(kw)).run(dry_run=True)
    assert sent[0]["event_type"] == "info" and sent[0]["fields"] == [
        {"name": "good: settings", "value": "files, 25 per run, no cycle tag, ignore tag 'rename-ignore', titles containing 'alien', dry run", "inline": False},
        {"name": "good: this run", "value": "No items needed renaming (1 checked)", "inline": False},
    ]
    quiet = []
    ArrRenamer(test_db, "radarr", instances=instances[1:], settings={"notify": False}, sleep=lambda s: None, client_factory=lambda inst: good, notifier=lambda **kw: quiet.append(kw)).run(dry_run=True)
    assert quiet == []  # off by default
