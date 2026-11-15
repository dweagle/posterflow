"""The editor panels' community endpoints: claim lookup, the upload (Discord, IDarr or both, as the
web app's Upload button is set), the Upload button's pick, a finish's staged posters, complete.

The community service and Discord are faked: nothing here leaves the machine."""
import base64
import json
import os
import time

import pytest
from fastapi import HTTPException

import api.community_maker as cm
from models.setting import Setting

REQ = "11111111-2222-3333-4444-555555555555"
REQ2 = "11111111-2222-3333-4444-666666666666"
ITEM = "99999999-2222-3333-4444-555555555555"
BASE = "/api/maker-tools/community"
SPY = "The Spy (2019) {tmdb-5} {imdb-tt1}"
DARK = "Dark (2017) {tmdb-70523} {tvdb-1}"
DISCORD = {"will": "post to Discord", "aside": "", "problem": ""}


def _token(exp_in: int = 3600) -> str:
    payload = {"discord_user_id": "42", "discord_username": "maker", "is_maker": True, "exp": int(time.time()) + exp_in}
    return base64.b64encode(json.dumps(payload).encode()).decode() + ".deadbeef"


def _connect(db, exp_in: int = 3600) -> str:
    token = _token(exp_in)
    db.add(Setting(key="community_discord_identity", value=json.dumps({"discord_user_id": "42", "discord_username": "maker", "discord_token": token})))
    db.commit()
    return token


def _set(db, key, value):
    db.add(Setting(key=key, value=value))
    db.commit()


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.is_success:
            raise cm.httpx.HTTPStatusError("bad", request=None, response=None)


@pytest.fixture
def service(monkeypatch):
    """A fake community service. .tables holds the rows each table answers with; .posts answers post-poster
    calls in order (default 200); .calls records everything sent; .jobs records the IDarr runs started."""
    state = type("S", (), {})()
    state.tables = {"poster_requests": [], "poster_list_items": []}
    state.posts, state.calls, state.sleeps, state.edge, state.jobs = [], [], [], {}, []
    state.job_error = None

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None):
            table = url.rsplit("/", 1)[1]
            state.calls.append(("GET", table, params))
            return _Resp(200, state.tables[table])

        def post(self, url, headers=None, json=None, data=None, files=None):
            fn = url.rsplit("/", 1)[1]
            state.calls.append(("POST", fn, {"json": json, "data": data, "files": files, "headers": headers}))
            if fn == "post-poster":
                return state.posts.pop(0) if state.posts else _Resp(200, {"success": True})
            return state.edge.get(fn, _Resp(200, {"ok": True}))

    def start_job(request, db):
        if state.job_error:
            raise HTTPException(status_code=400, detail=state.job_error)
        state.jobs.append(request)
        return type("Job", (), {"id": 40 + len(state.jobs)})()

    monkeypatch.setattr(cm.httpx, "Client", Client)
    monkeypatch.setattr(cm, "_sleep", lambda s: state.sleeps.append(s))
    monkeypatch.setattr(cm, "start_idarr_job", start_job)
    for held in (cm._staged, cm._picked, cm._idarr_sent, cm._parts):
        held.clear()
    yield state
    for held in (cm._staged, cm._picked, cm._idarr_sent, cm._parts):
        held.clear()


def _req(**over):
    row = {"id": REQ, "title": "The Spy", "year": 2019, "media_type": "movie", "season_number": None, "notes": None,
           "tmdb_id": 5, "tvdb_id": None, "status": "in_progress", "discord_message_id": "777"}
    row.update(over)
    return row


def _item(**over):
    row = {"id": ITEM, "title": "The Spy", "year": 2019, "media_type": "movie", "season_number": None, "notes": None,
           "tmdb_id": 5, "tvdb_id": None, "status": "in_progress"}
    row.update(over)
    return row


def _dark(**over):
    return _req(title="Dark", year=2017, tmdb_id=70523, tvdb_id=1, **over)


@pytest.fixture
def exports(tmp_path, test_db):
    """Two export folders set on the server. .add(folder, name, age) writes a file `age` seconds old."""
    box = type("E", (), {})()
    box.cl2k, box.poster = tmp_path / "cl2k", tmp_path / "poster"
    box.cl2k.mkdir()
    box.poster.mkdir()
    _set(test_db, "psd_image_export_folder", str(box.cl2k))
    _set(test_db, "poster_export_folder", str(box.poster))

    def add(folder, name, age, data=b"\xff\xd8jpeg"):
        path = folder / name
        path.write_bytes(data)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path
    box.add = add
    return box


@pytest.fixture
def idarr(tmp_path, test_db):
    """IDarr with an artwork drive, then two poster drives. .on(action) flips the web app's upload settings."""
    box = type("I", (), {})()
    box.art, box.one, box.two = tmp_path / "art", tmp_path / "drive-one", tmp_path / "drive-two"
    box.config = {"sync_targets": [
        {"label": "Artwork", "personal_drive_id": "a", "source_dir": str(box.art), "scope_token": "tok-art", "is_asset_drive": True},
        {"label": "Posters", "personal_drive_id": "b", "source_dir": str(box.one), "scope_token": "tok-one"},
        {"label": "", "personal_drive_id": "c", "source_dir": str(box.two), "scope_token": "tok-two"},
    ], "auto_rename_quick_add": True, "auto_upload_quick_add": True}
    _set(test_db, "maker_tools_idarr_config", json.dumps(box.config))

    def on(action="discord_idarr"):
        _set(test_db, "idarr_quick_add_community", "true")
        _set(test_db, "community_upload_action", action)
    box.on = on
    return box


def _claim(client, name, kind=None):
    return client.get(f"{BASE}/claim", params={"name": name, **({"kind": kind} if kind else {})})


def _pick(client, name):
    return client.get(f"{BASE}/upload", params={"name": name}).json()


def _stage(client, name, data=b"\xff\xd8jpeg", row_id=REQ, where="requests"):
    return client.put(f"{BASE}/{where}/{row_id}/posters/{name}", content=data)


def _posts(service):
    return [c[2] for c in service.calls if c[1] == "post-poster"]


# ---- claim lookup ----

def test_claim_needs_a_tmdb_id_in_the_name(client, test_db, service):
    _connect(test_db)
    assert _claim(client, "The Spy (2019)", "movie").json() == {"status": "no_id"}
    assert service.calls == []


def test_claim_without_a_discord_login_or_with_an_expired_one(client, test_db, service):
    assert _claim(client, "The Spy (2019) {tmdb-5}", "movie").json() == {"status": "not_connected"}
    _connect(test_db, exp_in=-10)
    assert _claim(client, "The Spy (2019) {tmdb-5}", "movie").json() == {"status": "expired"}
    assert service.calls == []   # nothing is asked of the community service without a usable login


def test_claim_matches_by_tmdb_id_type_and_claimer(client, test_db, service):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    r = _claim(client, SPY, "movie")
    assert r.json() == {"status": "match", "type": "request", "where": "requests", "id": REQ, "label": "The Spy (2019)",
                        "has_thread": True, "upload": DISCORD}
    method, table, params = service.calls[0]
    assert (method, table) == ("GET", "poster_requests")
    assert params["tmdb_id"] == "eq.5" and params["claimed_by_discord_id"] == "eq.42"
    assert params["media_type"] == "in.(movie)" and params["status"] == "in.(in_progress,fulfilled)"
    assert len(service.calls) == 1   # a request match never needs the list items


def test_claim_kinds_pick_their_request_types(client, test_db, service):
    _connect(test_db)
    _claim(client, "Dark (2017) {tmdb-70523} {tvdb-1}", "show")
    _claim(client, "Alien Collection {tmdb-8091}", "collection")
    # a person poster is "Name {tmdb-N}": no year. It is finished like a movie but lives in its own id space.
    _claim(client, "Tom Hanks {tmdb-31}", "movie")
    _claim(client, SPY)
    types = [c[2]["media_type"] for c in service.calls if c[1] == "poster_requests"]
    assert types == ["in.(show,season)", "in.(collection)", "in.(person)", "in.(movie,show,season,collection,person)"]
    assert _claim(client, "x {tmdb-1}", "tv").status_code == 400


def test_claim_with_several_in_progress_requests_names_them(client, test_db, service):
    _connect(test_db)
    service.tables["poster_requests"] = [
        _req(media_type="season", season_number=2, title="Dark", year=2017),
        _req(id=REQ2, media_type="season", season_number=3, title="Dark", year=2017),
    ]
    r = _claim(client, "Dark (2017) {tmdb-70523}", "show")
    assert r.json() == {"status": "several", "type": "request", "labels": ["Dark (2017) S2", "Dark (2017) S3"]}


def test_claim_falls_back_to_a_list_item_then_done_then_none(client, test_db, service):
    _connect(test_db)
    service.tables["poster_list_items"] = [_item(year=None)]
    r = _claim(client, "The Spy (2019) {tmdb-5}", "movie").json()
    assert (r["status"], r["type"], r["where"], r["id"], r["label"], r["has_thread"]) == ("match", "list_item", "list-items", ITEM, "The Spy · list", False)
    assert r["upload"]["problem"] == "The Spy · list is a list item. It has no Discord thread, and Adds to IDarr is off in Posterflow"

    service.tables["poster_list_items"] = []
    service.tables["poster_requests"] = [_req(status="fulfilled")]
    assert _claim(client, "The Spy (2019) {tmdb-5}", "movie").json() == {"status": "done", "label": "The Spy (2019)"}

    service.tables["poster_requests"] = []
    assert _claim(client, "The Spy (2019) {tmdb-5}", "movie").json() == {"status": "none"}


def test_a_lookup_with_no_kind_drops_rows_the_psd_name_contradicts(client, test_db, service):
    _connect(test_db)
    claim = lambda name: _claim(client, name).json()
    # TMDB number 5 as a movie from another year, as a person and as a collection
    service.tables["poster_requests"] = [_req(year=1984)]
    assert claim(SPY) == {"status": "none"}
    service.tables["poster_requests"] = [_req(media_type="person", year=None, title="Tom Hanks")]
    assert claim(SPY) == {"status": "none"}
    assert claim("Tom Hanks {tmdb-5}")["label"] == "Tom Hanks"
    service.tables["poster_requests"] = [_req(media_type="collection", year=None, title="Spy Collection"), _req(id=REQ2)]
    assert claim(SPY)["id"] == REQ2                                     # the movie, not the collection that shares its number
    assert claim("Spy Collection {tmdb-5}")["id"] == REQ


# ---- what an upload does: the web app's upload settings ----

def test_upload_action_follows_the_web_apps_two_settings(test_db):
    assert cm._upload_action(test_db) == "discord"
    _set(test_db, "community_upload_action", "idarr")
    assert cm._upload_action(test_db) == "discord"                      # the flavour waits while the switch is off
    _set(test_db, "idarr_quick_add_community", "true")
    assert cm._upload_action(test_db) == "idarr"
    test_db.query(Setting).filter(Setting.key == "community_upload_action").update({"value": "discord_idarr"})
    test_db.commit()
    assert cm._upload_action(test_db) == "discord_idarr"


def test_idarr_drive_is_the_one_picked_in_the_app_else_the_first_poster_drive(test_db, idarr):
    assert cm._idarr_drive(test_db) == (1, "Posters")                   # nothing picked: the first that takes posters
    _set(test_db, "idarr_selected_sync_target", "scope:tok-two")
    assert cm._idarr_drive(test_db) == (2, str(idarr.two))              # unlabelled: named by its folder
    setting = test_db.query(Setting).filter(Setting.key == "idarr_selected_sync_target")
    setting.update({"value": "scope:tok-art"})
    test_db.commit()
    assert cm._idarr_drive(test_db) == (1, "Posters")                   # an artwork drive never takes posters
    setting.update({"value": f"c::{idarr.two}::"})
    test_db.commit()
    assert cm._idarr_drive(test_db) == (2, str(idarr.two))              # a pick stored before the drive had a token
    setting.update({"value": "scope:gone"})
    test_db.commit()
    assert cm._idarr_drive(test_db) == (1, "Posters")


def test_idarr_drive_is_none_without_a_poster_drive(test_db):
    assert cm._idarr_drive(test_db) is None
    _set(test_db, "maker_tools_idarr_config", json.dumps({"sync_targets": [{"label": "Art", "source_dir": "/x", "is_asset_drive": True}]}))
    assert cm._idarr_drive(test_db) is None


def test_upload_plan_says_what_will_happen(test_db, idarr):
    plan = lambda **k: cm._upload_plan(test_db, k.get("list_item", False), k.get("thread", True), "X")
    assert (plan()["will"], plan()["idarr"]) == ("post to Discord", False)
    assert plan(thread=False)["problem"] == "X has no Discord thread"
    assert plan(list_item=True)["problem"] == "X is a list item. It has no Discord thread, and Adds to IDarr is off in Posterflow"
    idarr.on("discord_idarr")
    assert plan()["will"] == "post to Discord and add to IDarr (Posters)" and plan()["aside"] == ""
    assert (plan(thread=False)["will"], plan(thread=False)["aside"]) == ("add to IDarr (Posters)", "No Discord thread, so nothing goes to Discord")
    assert plan(list_item=True)["will"] == "add to IDarr (Posters)" and not plan(list_item=True)["discord"]
    test_db.query(Setting).filter(Setting.key == "community_upload_action").update({"value": "idarr"})
    test_db.commit()
    assert (plan()["will"], plan()["discord"], plan()["aside"]) == ("add to IDarr (Posters)", False, "")
    test_db.query(Setting).filter(Setting.key == "maker_tools_idarr_config").update({"value": json.dumps({"sync_targets": []})})
    test_db.commit()
    assert plan()["problem"] == "IDarr has no poster drive in Posterflow"
    test_db.query(Setting).filter(Setting.key == "community_upload_action").update({"value": "discord_idarr"})
    test_db.commit()
    assert (plan()["will"], plan()["aside"]) == ("post to Discord", "IDarr has no poster drive, so nothing goes to IDarr")
    assert plan(thread=False)["problem"] == "X has no Discord thread, and IDarr has no poster drive in Posterflow"


# ---- the Upload button's pick ----

def test_age_labels_match_the_upload_picker():
    now = 1_000_000_000.0
    assert [cm._age(now - s, now) for s in (5, 300, 7200, 3 * 86400)] == ["just now", "5m ago", "2h ago", "3d ago"]
    assert cm._age(now - 30 * 86400, now) not in ("just now", "30d ago")


def test_pick_takes_the_newest_poster_for_a_movie_request(client, test_db, service, exports):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    exports.add(exports.cl2k, SPY + ".jpg", 4000)
    exports.add(exports.poster, SPY + " (1).jpg", 120)                 # a newer re-export, in the other folder
    exports.add(exports.cl2k, SPY + " - Collection.jpg", 30)           # newer still, but a collection poster
    exports.add(exports.cl2k, "Other Film (2019) {tmdb-6}.jpg", 10)
    exports.add(exports.cl2k, SPY + ".psd", 5)
    r = _pick(client, SPY)
    assert r["status"] == "match" and r["id"] == REQ and r["folders"] == 2 and r["upload"] == DISCORD
    assert r["files"] == [{"name": SPY + " (1).jpg", "poster": "main", "age": "2m ago"}]


def test_pick_follows_the_requested_seasons(client, test_db, service, exports):
    _connect(test_db)
    for i, name in enumerate([".jpg", " - Season 1.jpg", " - Season 2.jpg", " - Season 3.jpg", " - Specials.jpg", " - Collection.jpg"]):
        exports.add(exports.cl2k, DARK + name, 600 - i)
    exports.add(exports.cl2k, DARK + " - Season 2 (1).jpg", 9000)      # an older copy of season 2

    service.tables["poster_requests"] = [_dark(media_type="season", season_number=2)]
    assert [f["poster"] for f in _pick(client, DARK)["files"]] == ["Season 2"]
    assert _pick(client, DARK)["files"][0]["name"] == DARK + " - Season 2.jpg"

    service.tables["poster_requests"] = [_dark(media_type="season", notes="Seasons: 3, 0")]
    assert [f["poster"] for f in _pick(client, DARK)["files"]] == ["Season 3", "Specials"]

    service.tables["poster_requests"] = [_dark(media_type="show")]
    assert [f["poster"] for f in _pick(client, DARK)["files"]] == ["main", "Season 1", "Season 2", "Season 3", "Specials"]


def test_pick_with_no_export_folder_set(client, test_db, service):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    r = _pick(client, SPY)
    assert r["status"] == "match" and r["files"] == [] and r["folders"] == 0
    assert client.post(f"{BASE}/requests/{REQ}/upload").status_code == 409   # an empty pick uploads nothing


def test_pick_reports_no_exports_and_other_answers(client, test_db, service, exports):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    r = _pick(client, SPY)
    assert r["status"] == "match" and r["files"] == [] and r["folders"] == 2
    service.tables["poster_requests"] = [_req(discord_message_id=None)]
    r = _pick(client, SPY)
    assert r["upload"]["problem"] == "The Spy (2019) has no Discord thread" and "files" not in r   # nothing to pick for
    service.tables["poster_requests"] = []
    assert _pick(client, SPY) == {"status": "none"}
    assert _pick(client, "The Spy (2019)") == {"status": "no_id"}


def test_pick_says_what_the_upload_will_do(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    service.tables["poster_requests"] = [_req()]
    assert _pick(client, SPY)["upload"]["will"] == "post to Discord and add to IDarr (Posters)"
    service.tables["poster_requests"] = []
    service.tables["poster_list_items"] = [_item()]
    r = _pick(client, SPY)                                              # a list item goes to IDarr while that is on
    assert (r["where"], r["upload"]["will"], [f["poster"] for f in r["files"]]) == ("list-items", "add to IDarr (Posters)", ["main"])


# ---- uploading the pick ----

def test_upload_posts_exactly_the_pick_once(client, test_db, service, exports):
    token = _connect(test_db)
    service.tables["poster_requests"] = [_dark(media_type="show")]
    exports.add(exports.cl2k, DARK + ".jpg", 60, b"main")
    exports.add(exports.poster, DARK + " - Season 1.jpg", 50, b"one")
    assert len(_pick(client, DARK)["files"]) == 2
    exports.add(exports.cl2k, DARK + " - Season 2.jpg", 1, b"late")     # exported after the first press: not in the pick
    r = client.post(f"{BASE}/requests/{REQ}/upload").json()
    assert (r["posted"], r["messages"], r["idarr"], r["did"]) == (2, 1, 0, "posted 2 posters to Dark (2017)")
    post = _posts(service)[0]
    assert post["data"]["token"] == token and post["data"]["request_id"] == REQ
    assert [f[1][:2] for f in post["files"]] == [(DARK + ".jpg", b"main"), (DARK + " - Season 1.jpg", b"one")]
    r = client.post(f"{BASE}/requests/{REQ}/upload")
    assert r.status_code == 409 and r.json()["detail"] == "Nothing is picked for this request. Press Upload again."
    assert service.jobs == []


def test_upload_expires_and_refuses_a_file_that_went_away(client, test_db, service, exports):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    path = exports.add(exports.cl2k, SPY + ".jpg", 60)
    _pick(client, SPY)
    path.unlink()
    assert client.post(f"{BASE}/requests/{REQ}/upload").status_code == 404
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    _pick(client, SPY)
    cm._picked[f"requests/{REQ}"]["at"] -= cm.PICK_TTL + 1
    assert client.post(f"{BASE}/requests/{REQ}/upload").status_code == 409
    assert _posts(service) == []
    assert client.post(f"{BASE}/other/{REQ}/upload").status_code == 404
    assert client.post(f"{BASE}/requests/nope/upload").status_code == 400


def test_upload_posts_to_discord_and_adds_to_idarr(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    _set(test_db, "idarr_selected_sync_target", "scope:tok-two")
    service.tables["poster_requests"] = [_dark(media_type="show")]
    exports.add(exports.cl2k, DARK + ".jpg", 60, b"main")
    exports.add(exports.cl2k, DARK + " - Season 1.jpg", 50, b"one")
    assert _pick(client, DARK)["upload"]["will"] == f"post to Discord and add to IDarr ({idarr.two})"
    r = client.post(f"{BASE}/requests/{REQ}/upload").json()
    assert (r["posted"], r["idarr"], r["drive"], r["warnings"]) == (2, 2, str(idarr.two), [])
    assert r["idarr_job"] == {"id": 41, "index": 2, "sync_after": True}   # the web app watches this run for its notice
    assert r["did"] == f"posted 2 posters to Dark (2017) and added them to IDarr ({idarr.two})"
    assert (idarr.two / (DARK + ".jpg")).read_bytes() == b"main" and (idarr.two / (DARK + " - Season 1.jpg")).read_bytes() == b"one"
    assert not idarr.one.exists()
    job = service.jobs[0]                                                # the targeted run the app starts after a quick add
    assert (job.sync_target_index, job.source_filenames, job.sync_after_run, job.dry_run) == (2, [DARK + ".jpg", DARK + " - Season 1.jpg"], True, False)
    assert len(service.jobs) == 1 and len(_posts(service)) == 1


def test_upload_idarr_only_posts_nothing(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("idarr")
    idarr.config["auto_rename_quick_add"] = False
    test_db.query(Setting).filter(Setting.key == "maker_tools_idarr_config").update({"value": json.dumps(idarr.config)})
    test_db.commit()
    service.tables["poster_requests"] = [_req(discord_message_id=None)]   # no thread needed
    exports.add(exports.cl2k, SPY + ".jpg", 60, b"x" * (cm.MAX_BYTES + 1))   # nor the Discord size limit
    assert _pick(client, SPY)["upload"] == {"will": "add to IDarr (Posters)", "aside": "", "problem": ""}
    r = client.post(f"{BASE}/requests/{REQ}/upload").json()
    assert (r["posted"], r["idarr"], r["did"], r["idarr_job"]) == (0, 1, "added 1 poster for The Spy (2019) to IDarr (Posters)", None)
    assert (idarr.one / (SPY + ".jpg")).exists() and _posts(service) == []
    assert service.jobs == []                                           # quick-add rename is off: files only


def test_upload_for_a_list_item_goes_to_idarr(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    service.tables["poster_list_items"] = [_item()]
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    assert _pick(client, SPY)["where"] == "list-items"
    r = client.post(f"{BASE}/list-items/{ITEM}/upload").json()
    assert (r["posted"], r["idarr"]) == (0, 1) and r["did"] == "added 1 poster for The Spy (2019) · list to IDarr (Posters)"
    assert _posts(service) == [] and len(service.jobs) == 1


def test_upload_reports_an_idarr_run_that_did_not_start(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    service.job_error = "TMDB API key is not configured. Add it in Settings."
    service.tables["poster_requests"] = [_req()]
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    _pick(client, SPY)
    r = client.post(f"{BASE}/requests/{REQ}/upload").json()
    assert r["did"] == ("posted 1 poster to The Spy (2019) and added it to IDarr (Posters). "
                        "The IDarr run did not start: TMDB API key is not configured. Add it in Settings")
    assert (r["posted"], r["idarr"]) == (1, 1)


def test_a_failed_idarr_add_does_not_stop_the_discord_post(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    idarr.one.write_text("a file where the drive's folder should be")
    service.tables["poster_requests"] = [_req()]
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    _pick(client, SPY)
    r = client.post(f"{BASE}/requests/{REQ}/upload").json()
    assert (r["posted"], r["idarr"]) == (1, 0) and r["did"].startswith("posted 1 poster to The Spy (2019). IDarr add failed: Cannot create or access source folder")
    # with IDarr only, the failed add is the upload's failure
    test_db.query(Setting).filter(Setting.key == "community_upload_action").update({"value": "idarr"})
    test_db.commit()
    _pick(client, SPY)
    r = client.post(f"{BASE}/requests/{REQ}/upload")
    assert r.status_code == 502 and r.json()["detail"].startswith("IDarr add failed: Cannot create or access source folder")


def test_a_retry_after_a_failed_post_does_not_add_to_idarr_again(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    service.tables["poster_requests"] = [_req()]
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    _pick(client, SPY)
    service.posts = [_Resp(404, {"error": "No Discord thread found for this request"})]
    r = client.post(f"{BASE}/requests/{REQ}/upload")
    assert r.status_code == 502
    assert r.json()["detail"] == "Discord post failed at message 1 of 1: No Discord thread found for this request. The posters are in IDarr (Posters)"
    assert len(service.jobs) == 1

    _pick(client, SPY)
    r = client.post(f"{BASE}/requests/{REQ}/upload").json()
    assert r["did"] == "posted 1 poster to The Spy (2019). They were already in IDarr (Posters)"
    assert len(service.jobs) == 1                                        # no second add, no second run
    _pick(client, SPY)
    client.post(f"{BASE}/requests/{REQ}/upload")
    assert len(service.jobs) == 2                                        # a later, deliberate upload adds again


def test_upload_refuses_a_poster_over_the_discord_limit(client, test_db, service, exports, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    service.tables["poster_requests"] = [_req()]
    exports.add(exports.cl2k, SPY + ".jpg", 60, b"x" * (cm.MAX_BYTES + 1))
    _pick(client, SPY)
    r = client.post(f"{BASE}/requests/{REQ}/upload")
    assert r.status_code == 413 and "over the 8 MB Discord limit" in r.json()["detail"]
    assert not idarr.one.exists() and service.jobs == []                 # refused before anything was done


# ---- the web app's own uploads: the posters it names or sends, what it is set to on screen ----

def test_the_web_app_names_the_exports_to_upload(client, test_db, service, exports):
    _connect(test_db)
    service.tables["poster_requests"] = [_dark(media_type="season", season_number=1)]
    exports.add(exports.cl2k, DARK + " - Season 1.jpg", 60, b"one")
    exports.add(exports.poster, DARK + ".jpg", 50, b"main")             # not asked for, but ticked by hand in the picker
    body = {"files": [{"source": "cl2k", "name": DARK + " - Season 1.jpg"}, {"source": "poster", "name": DARK + ".jpg"}]}
    r = client.post(f"{BASE}/requests/{REQ}/upload", json=body).json()
    assert (r["posted"], r["did"]) == (2, "posted 2 posters to Dark (2017) S1")
    assert [f[1][:2] for f in _posts(service)[0]["files"]] == [(DARK + " - Season 1.jpg", b"one"), (DARK + ".jpg", b"main")]
    # only files inside an export folder, and at least one
    assert client.post(f"{BASE}/requests/{REQ}/upload", json={"files": [{"source": "cl2k", "name": "../secret.jpg"}]}).status_code in (400, 403, 404)
    assert client.post(f"{BASE}/requests/{REQ}/upload", json={"files": [{"source": "nope", "name": DARK + ".jpg"}]}).status_code == 400
    assert client.post(f"{BASE}/requests/{REQ}/upload", json={"files": []}).status_code == 400
    assert len(_posts(service)) == 1


def test_the_web_app_sends_files_from_the_computer(client, test_db, service):
    token = _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    files = [("files", ("My poster; final.jpg", b"one", "image/jpeg")), ("files", ("second.png", b"two", "image/png"))]
    r = client.post(f"{BASE}/requests/{REQ}/upload-files", files=files).json()
    assert (r["posted"], r["messages"], r["did"]) == (2, 1, "posted 2 posters to The Spy (2019)")
    post = _posts(service)[0]
    assert post["data"]["token"] == token and json.loads(post["data"]["filenames"]) == ["My poster; final.jpg", "second.png"]
    assert post["files"] == [("file", ("My poster; final.jpg", b"one", "image/jpeg")), ("file", ("second.png", b"two", "image/png"))]
    # Discord takes images only; nothing is posted when one file is not
    r = client.post(f"{BASE}/requests/{REQ}/upload-files", files=[("files", ("a.jpg", b"x", "image/jpeg")), ("files", ("notes.txt", b"x", "text/plain"))])
    assert r.status_code == 400 and r.json()["detail"] == '"notes.txt" is not a JPG, PNG or WebP image.'
    assert len(_posts(service)) == 1


def test_the_web_app_says_what_its_upload_is_set_to(client, test_db, service, idarr):
    """The switch and the drive as they stand on screen win over what was last saved."""
    _connect(test_db)
    idarr.on("idarr")                                                    # saved: IDarr only, into the first poster drive
    service.tables["poster_requests"] = [_req()]
    one = [("files", (SPY + ".jpg", b"jpg", "image/jpeg"))]
    r = client.post(f"{BASE}/requests/{REQ}/upload-files", files=one, data={"action": "discord"}).json()
    assert (r["posted"], r["idarr"]) == (1, 0) and not idarr.one.exists()
    r = client.post(f"{BASE}/requests/{REQ}/upload-files", files=one, data={"action": "discord_idarr", "drive": "scope:tok-two"}).json()
    assert (r["posted"], r["idarr"], r["drive"]) == (1, 1, str(idarr.two)) and (idarr.two / (SPY + ".jpg")).exists()
    assert r["idarr_job"] == {"id": 41, "index": 2, "sync_after": True}
    assert client.post(f"{BASE}/requests/{REQ}/upload-files", files=one, data={"action": "everything"}).status_code == 400
    # the Lists tab: a list item goes to IDarr, in the drive on screen
    service.tables["poster_list_items"] = [_item()]
    r = client.post(f"{BASE}/list-items/{ITEM}/upload-files", files=one, data={"action": "discord_idarr"}).json()
    assert (r["posted"], r["idarr"], r["drive"]) == (0, 1, "Posters")
    r = client.post(f"{BASE}/list-items/{ITEM}/upload-files", files=one, data={"action": "discord"})
    assert r.status_code == 409 and "is a list item" in r.json()["detail"]


def test_the_web_app_acts_with_its_own_discord_login(client, test_db, service, exports):
    """Two makers can share one server: each call carries the login of whoever pressed the button."""
    stored = _connect(test_db)                                           # another maker's login is the stored one
    mine = _token()[:-8] + "cafef00d"
    service.tables["poster_requests"] = [_req()]
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    one = [("files", (SPY + ".jpg", b"jpg", "image/jpeg"))]
    client.post(f"{BASE}/requests/{REQ}/upload-files", files=one, data={"token": mine})
    client.post(f"{BASE}/requests/{REQ}/upload", json={"files": [{"source": "cl2k", "name": SPY + ".jpg"}], "token": mine})
    client.post(f"{BASE}/requests/{REQ}/complete", json={"token": mine})
    assert [p["data"]["token"] for p in _posts(service)] == [mine, mine]
    assert [c[2]["json"]["token"] for c in service.calls if c[1] == "update-request-status"] == [mine]
    assert stored != mine
    # with no stored login at all, the caller's own is enough
    test_db.query(Setting).filter(Setting.key == "community_discord_identity").delete()
    test_db.commit()
    assert client.post(f"{BASE}/requests/{REQ}/upload-files", files=one, data={"token": mine}).json()["posted"] == 1
    # an expired one is refused in the app's words
    r = client.post(f"{BASE}/requests/{REQ}/complete", json={"token": _token(exp_in=-10)})
    assert r.status_code == 409 and r.json()["detail"] == "Your Discord login has expired. Reconnect Discord."


def _send_part(client, names, batch, n, of, **more):
    files = [("files", (name, b"x", "image/jpeg")) for name in names]
    return client.post(f"{BASE}/requests/{REQ}/upload-files", files=files, data={"batch": batch, "part": str(n), "of": str(of), **more})


def test_a_large_upload_arrives_in_parts_of_ten_and_idarr_runs_once(client, test_db, service, idarr):
    """The web app sends ten posters a request, so no request is big or slow. Each part is one Discord
    message; IDarr runs once, from the last part, on what every part added."""
    _connect(test_db)
    idarr.on("discord_idarr")
    service.tables["poster_requests"] = [_req(media_type="show")]
    names = [f"p - Season {i}.jpg" for i in range(23)]
    batch = "ab" * 16
    results = [_send_part(client, names[at:at + 10], batch, n, 3).json() for n, at in enumerate(range(0, 23, 10), 1)]
    assert [(r["posted"], r["messages"], r["idarr"]) for r in results] == [(10, 1, 10), (10, 1, 10), (3, 1, 3)]
    assert [len(p["files"]) for p in _posts(service)] == [10, 10, 3]
    assert [r["idarr_job"] for r in results] == [None, None, {"id": 41, "index": 1, "sync_after": True}]
    assert len(service.jobs) == 1 and service.jobs[0].source_filenames == names
    assert all((idarr.one / name).exists() for name in names) and cm._parts == {}
    assert service.sleeps == []                                          # the web app paces the messages itself


def test_a_part_that_fails_is_sent_again_without_repeating_what_went_before(client, test_db, service, idarr):
    """Part 2's Discord message fails after its posters reached IDarr. Sent again, it is posted, nothing
    goes into IDarr twice, and the one run at the end still covers every part."""
    _connect(test_db)
    idarr.on("discord_idarr")
    service.tables["poster_requests"] = [_req(media_type="show")]
    names = [f"p - Season {i}.jpg" for i in range(12)]
    batch = "cd" * 16
    assert _send_part(client, names[:10], batch, 1, 2).status_code == 200
    service.posts = [_Resp(404, {"error": "No Discord thread found for this request"})]
    r = _send_part(client, names[10:], batch, 2, 2)
    assert r.status_code == 502
    assert r.json()["detail"] == "Discord post failed at message 2 of 2 (earlier messages were posted): No Discord thread found for this request. The posters are in IDarr (Posters)"
    assert len(service.jobs) == 1 and service.jobs[0].source_filenames == names      # IDarr ran on all twelve before the post
    r = _send_part(client, names[10:], batch, 2, 2).json()
    assert (r["posted"], r["idarr"], r["idarr_job"]) == (2, 0, None) and r["warnings"] == ["They were already in IDarr (Posters)"]
    assert len(service.jobs) == 1
    duplicates = [p for p in (idarr.one.parent).rglob("*") if "duplicates" in p.parts and p.is_file()]
    assert duplicates == []


def test_a_middle_part_that_fails_keeps_its_place_in_the_idarr_run(client, test_db, service, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    service.tables["poster_requests"] = [_req(media_type="show")]
    names = [f"p - Season {i}.jpg" for i in range(21)]
    batch = "ef" * 16
    service.posts = [_Resp(404, {"error": "gone"})]
    assert _send_part(client, names[:10], batch, 1, 3).status_code == 502          # part 1: in IDarr, not posted, no run yet
    assert service.jobs == []
    for n, at in ((1, 0), (2, 10), (3, 20)):
        assert _send_part(client, names[at:at + 10], batch, n, 3).status_code == 200
    assert len(service.jobs) == 1 and service.jobs[0].source_filenames == names


def test_an_upload_part_must_say_which_one_it_is(client, test_db, service):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    one = [SPY + ".jpg"]
    for batch, n, of in (("ab" * 16, 0, 2), ("ab" * 16, 3, 2), ("../../etc", 1, 2), ("", 1, 2), ("ab" * 16, 1, 9999)):
        r = _send_part(client, one, batch, n, of)
        assert r.status_code == 400 and r.json()["detail"] == "Invalid upload part.", (batch, n, of)
    r = client.post(f"{BASE}/requests/{REQ}/upload", json={"files": [{"source": "cl2k", "name": SPY + ".jpg"}], "part": 1})
    assert r.status_code == 400 and r.json()["detail"] == "Invalid upload part."
    assert _posts(service) == []


def test_exports_picked_on_the_server_have_the_same_size_limit(client, test_db, service, exports, monkeypatch):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    exports.add(exports.cl2k, SPY + ".jpg", 60)
    exports.add(exports.cl2k, SPY + " - Season 1.jpg", 60)
    refs = [{"source": "cl2k", "name": SPY + ".jpg"}, {"source": "cl2k", "name": SPY + " - Season 1.jpg"}]
    both = sum((exports.cl2k / ref["name"]).stat().st_size for ref in refs)
    monkeypatch.setattr(cm, "MAX_UPLOAD_BYTES", both - 1)
    r = client.post(f"{BASE}/requests/{REQ}/upload", json={"files": refs})
    assert r.status_code == 413 and r.json()["detail"] == "Too much for one upload. Send fewer posters at a time."
    assert _posts(service) == []
    monkeypatch.setattr(cm, "MAX_UPLOAD_BYTES", both)
    assert client.post(f"{BASE}/requests/{REQ}/upload", json={"files": refs}).json()["posted"] == 2


def test_an_upload_from_the_computer_has_limits(client, test_db, service, monkeypatch):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    monkeypatch.setattr(cm, "MAX_UPLOAD_BYTES", 5)
    r = client.post(f"{BASE}/requests/{REQ}/upload-files", files=[("files", ("a.jpg", b"1234", "image/jpeg")), ("files", ("b.jpg", b"5678", "image/jpeg"))])
    assert r.status_code == 413 and r.json()["detail"] == "Too much for one upload. Send fewer posters at a time."
    monkeypatch.setattr(cm, "MAX_UPLOAD_FILES", 1)
    r = client.post(f"{BASE}/requests/{REQ}/upload-files", files=[("files", ("a.jpg", b"1", "image/jpeg")), ("files", ("b.jpg", b"2", "image/jpeg"))])
    assert r.status_code == 400
    assert _posts(service) == []


# ---- a finish's staged posters ----

def test_stage_validates_and_keeps_order(client, service):
    assert _stage(client, "a.jpg", row_id="not-a-uuid").status_code == 400
    assert _stage(client, "a.jpg", where="movies").status_code == 404
    assert _stage(client, "a.psd").status_code == 400
    assert _stage(client, "a.jpg", data=b"").status_code == 400
    assert _stage(client, "big.jpg", data=b"x" * (cm.MAX_BYTES + 1)).status_code == 413
    assert _stage(client, "a.jpg", data=b"first").json() == {"staged": 1}
    assert _stage(client, "b - Season 1.jpg").json() == {"staged": 2}
    assert _stage(client, "a.jpg", data=b"again").json() == {"staged": 2}   # a re-export replaces, it does not add
    assert list(cm._staged[f"requests/{REQ}"]["files"].items())[0] == ("a.jpg", b"again")


def test_staged_upload_sends_ten_per_message_in_order_and_clears(client, test_db, service):
    token = _connect(test_db)
    service.tables["poster_requests"] = [_req(media_type="show")]
    for i in range(23):
        _stage(client, f"p - Season {i}.jpg", data=b"img%d" % i)
    r = client.post(f"{BASE}/requests/{REQ}/posters").json()
    assert (r["posted"], r["messages"], r["idarr"], r["skipped"], r["did"]) == (23, 3, 0, [], "posted 23 posters to The Spy (2019)")
    posts = _posts(service)
    assert [len(p["files"]) for p in posts] == [10, 10, 3]
    assert posts[0]["data"]["token"] == token and posts[0]["data"]["request_id"] == REQ
    assert json.loads(posts[2]["data"]["filenames"]) == ["p - Season 20.jpg", "p - Season 21.jpg", "p - Season 22.jpg"]
    assert posts[0]["files"][0] == ("file", ("p - Season 0.jpg", b"img0", "image/jpeg"))
    assert "Content-Type" not in posts[0]["headers"]   # the multipart body sets its own
    assert service.sleeps == [cm.MESSAGE_GAP, cm.MESSAGE_GAP]   # paced between messages only
    assert cm._staged == {}
    assert client.post(f"{BASE}/requests/{REQ}/posters").status_code == 400   # nothing left to post twice


def test_staged_upload_retries_a_failing_service_but_not_a_refusal(client, test_db, service):
    _connect(test_db)
    service.tables["poster_requests"] = [_req()]
    _stage(client, "a.jpg")
    service.posts = [_Resp(502, {"error": "Discord is busy"}), _Resp(200, {"success": True})]
    assert client.post(f"{BASE}/requests/{REQ}/posters").json()["posted"] == 1
    assert len(_posts(service)) == 2

    service.calls.clear()
    _stage(client, "a.jpg")
    service.posts = [_Resp(404, {"error": "No Discord thread found for this request"})]
    r = client.post(f"{BASE}/requests/{REQ}/posters")
    assert r.status_code == 502 and r.json()["detail"] == "Discord post failed at message 1 of 1: No Discord thread found for this request"
    assert len(_posts(service)) == 1


def test_staged_upload_failure_after_the_first_message_says_so(client, test_db, service):
    _connect(test_db)
    service.tables["poster_requests"] = [_req(media_type="show")]
    for i in range(12):
        _stage(client, f"p - Season {i}.jpg")
    service.posts = [_Resp(200, {"success": True})] + [_Resp(500, {"error": "boom"})] * cm.ATTEMPTS
    r = client.post(f"{BASE}/requests/{REQ}/posters")
    assert r.status_code == 502
    assert r.json()["detail"] == "Discord post failed at message 2 of 2 (earlier messages were posted): boom"
    assert cm._staged == {}   # a retry of the finish stages afresh instead of adding to leftovers


def test_staged_upload_keeps_only_the_requested_posters(client, test_db, service):
    _connect(test_db)
    names = [DARK + ".jpg", DARK + " - Season 1.jpg", DARK + " - Season 2.jpg", DARK + " - Collection.jpg"]
    service.tables["poster_requests"] = [_req(media_type="season", season_number=2)]
    for name in names:
        _stage(client, name)
    r = client.post(f"{BASE}/requests/{REQ}/posters").json()
    assert (r["posted"], r["skipped"]) == (1, [names[0], names[1], names[3]])
    assert json.loads(_posts(service)[0]["data"]["filenames"]) == [names[2]]

    service.calls.clear()
    service.tables["poster_requests"] = [_req(media_type="show")]
    for name in names:
        _stage(client, name)
    r = client.post(f"{BASE}/requests/{REQ}/posters").json()
    assert r["posted"] == 3 and r["skipped"] == [names[3]]              # a whole show takes all but the collection poster

    service.calls.clear()
    service.tables["poster_requests"] = [_req(media_type="season", season_number=7)]
    _stage(client, names[1])
    r = client.post(f"{BASE}/requests/{REQ}/posters").json()
    assert (r["posted"], r["idarr"], r["did"], r["skipped"]) == (0, 0, "", [names[1]])
    assert _posts(service) == []


def test_staged_upload_follows_the_upload_settings(client, test_db, service, idarr):
    _connect(test_db)
    idarr.on("discord_idarr")
    service.tables["poster_requests"] = [_req()]
    _stage(client, SPY + ".jpg", data=b"jpg")
    r = client.post(f"{BASE}/requests/{REQ}/posters").json()
    assert r["did"] == "posted 1 poster to The Spy (2019) and added it to IDarr (Posters)" and r["skipped"] == []
    assert (idarr.one / (SPY + ".jpg")).read_bytes() == b"jpg" and len(service.jobs) == 1
    # a list item's finish: staged under list-items, IDarr only
    service.tables["poster_list_items"] = [_item()]
    _stage(client, SPY + ".jpg", data=b"jpg2", row_id=ITEM, where="list-items")
    r = client.post(f"{BASE}/list-items/{ITEM}/posters").json()
    assert (r["posted"], r["idarr"]) == (0, 1) and len(_posts(service)) == 1


def test_staged_upload_needs_the_request(client, test_db, service):
    _connect(test_db)
    _stage(client, "a.jpg")
    r = client.post(f"{BASE}/requests/{REQ}/posters")
    assert r.status_code == 404 and r.json()["detail"] == "The request was not found."
    assert f"requests/{REQ}" in cm._staged                              # kept: nothing was decided about them


def test_upload_and_complete_need_a_discord_login(client, test_db, service):
    service.tables["poster_requests"] = [_req()]
    _stage(client, "a.jpg")
    r = client.post(f"{BASE}/requests/{REQ}/posters")
    assert r.status_code == 409 and r.json()["detail"] == "Discord is not connected in Posterflow."
    r = client.post(f"{BASE}/requests/{REQ}/complete")
    assert r.status_code == 409
    _connect(test_db, exp_in=-10)
    r = client.post(f"{BASE}/list-items/{ITEM}/complete")
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert [c for c in service.calls if c[0] == "POST"] == []


# ---- complete ----

def test_complete_request_and_list_item(client, test_db, service, monkeypatch):
    token = _connect(test_db)
    cleared = []
    monkeypatch.setattr(cm, "_cache_clear", lambda: cleared.append(1))
    assert client.post(f"{BASE}/requests/{REQ}/complete").json() == {"ok": True}
    assert client.post(f"{BASE}/list-items/{ITEM}/complete").json() == {"ok": True}
    sent = [(c[1], c[2]["json"]) for c in service.calls]
    assert sent == [
        ("update-request-status", {"token": token, "request_id": REQ, "action": "complete"}),
        ("update-list-item", {"token": token, "item_id": ITEM, "action": "complete"}),
    ]
    assert cleared == [1, 1]   # the web app's request lists drop the completed row
    # the community service's answer goes back whole: the web app updates its card from it
    service.edge["update-request-status"] = _Resp(200, {"status": "fulfilled", "claimed_by": "maker", "fulfilled_by": "maker"})
    assert client.post(f"{BASE}/requests/{REQ}/complete").json() == {"status": "fulfilled", "claimed_by": "maker", "fulfilled_by": "maker"}


def test_complete_passes_on_a_refusal(client, test_db, service, monkeypatch):
    _connect(test_db)
    cleared = []
    monkeypatch.setattr(cm, "_cache_clear", lambda: cleared.append(1))
    service.edge["update-request-status"] = _Resp(409, {"error": "Only in-progress requests can be marked as complete"})
    r = client.post(f"{BASE}/requests/{REQ}/complete")
    assert r.status_code == 409 and r.json()["detail"] == "Only in-progress requests can be marked as complete"
    assert cleared == []
    assert client.post(f"{BASE}/requests/nope/complete").status_code == 400
    assert client.post(f"{BASE}/people/{REQ}/complete").status_code == 404
