"""Tools page API: the catalog with availability and profiles, running a tool as a background
job (a profile's settings or the run's own), the profiles, and the Plex Metadata folder setting."""

import json
import os

import pytest

from models.job import JOB_TYPE_TOOLS, Job
from models.setting import upsert_setting
from modules.tools import run_tool_background_job


def _servers(db, *types):
    upsert_setting(db, "plex_instances", json.dumps([{"name": f"{t}-srv", "url": f"http://{t}", "api_key": "k", "type": t} for t in types]))
    db.commit()


def _arrs(db, *kinds):
    for kind in kinds:
        upsert_setting(db, f"{kind}_instances", json.dumps([{"name": f"{kind}-1", "url": f"http://{kind}", "api_key": "k"}]))
    db.commit()


def _stub_queue(monkeypatch):
    submitted = []
    monkeypatch.setattr("api.tools.job_queue", type("Q", (), {"submit": staticmethod(lambda *a: submitted.append(a))})())
    return submitted


def test_tools_catalog_is_grouped_per_server_type(client, test_db):
    groups = {g["id"]: g for g in client.get("/api/tools").json()["groups"]}
    assert list(groups) == ["plex", "jellyfin", "sonarr", "radarr"]
    assert [t["id"] for t in groups["plex"]["tools"]] == ["plex_cleanup", "plex_reset_posters"]
    assert [t["id"] for t in groups["jellyfin"]["tools"]] == ["jellyfin_clean_image_cache", "jellyfin_reset_posters"]
    assert [t["id"] for t in groups["sonarr"]["tools"]] == ["sonarr_rename", "sonarr_upgrade"]
    assert [t["id"] for t in groups["radarr"]["tools"]] == ["radarr_rename", "radarr_upgrade"]
    assert groups["plex"]["instances"] == [] and all(not t["available"] and "No Plex server" in t["reason"] for t in groups["plex"]["tools"])
    assert all(not t["available"] and "No Jellyfin server" in t["reason"] for t in groups["jellyfin"]["tools"])
    assert groups["sonarr"]["instances"] == [] and "No Sonarr instance" in groups["sonarr"]["tools"][0]["reason"]

    _servers(test_db, "plex", "jellyfin")
    _arrs(test_db, "sonarr")
    groups = {g["id"]: g for g in client.get("/api/tools").json()["groups"]}
    assert groups["plex"]["instances"] == ["plex-srv"] and groups["jellyfin"]["instances"] == ["jellyfin-srv"]
    assert groups["sonarr"]["instances"] == ["sonarr-1"] and groups["radarr"]["instances"] == []
    rename = groups["sonarr"]["tools"][0]
    assert rename["available"] and rename["confirm"] == "Yes, rename them" and rename["dry_run"] is True and rename["group"] == "sonarr"
    assert not groups["radarr"]["tools"][0]["available"] and "No Radarr instance" in groups["radarr"]["tools"][0]["reason"]
    tools = {t["id"]: t for g in groups.values() for t in g["tools"]}
    assert tools["plex_cleanup"]["available"] is False and "Metadata folder" in tools["plex_cleanup"]["reason"]
    assert tools["plex_reset_posters"]["available"] and tools["plex_reset_posters"]["confirm"] == "Yes, reset them"
    assert tools["plex_reset_posters"]["kinds"] == ["poster", "background", "logo", "squareart"] == tools["plex_cleanup"]["kinds"]
    assert (tools["plex_reset_posters"]["run_label"], tools["plex_cleanup"]["run_label"], tools["jellyfin_clean_image_cache"]["run_label"]) == ("Reset", "Clean", "Run")
    assert tools["jellyfin_reset_posters"]["kinds"] == ["poster", "background", "logo"] and tools["jellyfin_clean_image_cache"]["kinds"] == []
    cache = tools["jellyfin_clean_image_cache"]
    assert cache["available"] and cache["confirm"] is None and cache["dry_run"] is False and cache["group"] == "jellyfin"
    assert tools["jellyfin_reset_posters"]["job_type"] == JOB_TYPE_TOOLS
    # Every tool with settings has a Default profile from the first listing on; a tool without has none.
    assert tools["plex_reset_posters"]["defaults"] == {"kinds": ["poster", "background", "logo", "squareart"]}
    [default] = tools["plex_reset_posters"]["profiles"]
    assert default["name"] == "Default" and default["is_default"] is True and default["settings"] == {"kinds": ["poster", "background", "logo", "squareart"]}
    assert tools["jellyfin_reset_posters"]["profiles"][0]["settings"] == {"kinds": ["poster", "background", "logo"]}
    assert cache["profiles"] == [] and cache["defaults"] is None
    # An arr renamer keeps its profiles per instance: a Default for each one configured, none before there is one.
    [of_sonarr] = tools["sonarr_rename"]["profiles"]
    assert (of_sonarr["instance"], of_sonarr["name"], of_sonarr["is_default"], of_sonarr["settings"]["count"]) == ("sonarr-1", "Default", True, 25)
    assert tools["sonarr_rename"]["defaults"]["tag_name"] == "renamed"
    assert tools["radarr_rename"]["profiles"] == [] and default["instance"] is None
    # The upgrader keeps its own profiles per instance, beside the renamer's. It asks first, without being destructive.
    upgrade = tools["sonarr_upgrade"]
    [of_sonarr] = upgrade["profiles"]
    assert (of_sonarr["instance"], of_sonarr["name"], of_sonarr["is_default"]) == ("sonarr-1", "Default", True)
    assert of_sonarr["settings"] == upgrade["defaults"] == {
        "count": 5, "mode": "all", "search_unit": "seasons", "season_threshold": 50, "tag_name": "upgrade-searched", "ignore_tag": "upgrade-ignore",
        "monitored": "yes", "only_tags": "", "quality_profiles": "", "min_age_days": 0, "max_grabs": 0,
        "skip_queued": True, "title_filter": "", "notify": False, "notify_grabs_only": False}
    assert (upgrade["available"], upgrade["confirm"], upgrade["dry_run"], upgrade["per_instance"], upgrade["run_label"]) == (True, "Yes, search", True, True, "Search")
    assert (upgrade["destructive"], rename["destructive"], tools["plex_reset_posters"]["destructive"], cache["destructive"]) == (False, True, True, False)
    assert tools["radarr_upgrade"]["profiles"] == [] and not tools["radarr_upgrade"]["available"]

    assert client.put("/api/tools/plex-metadata-paths", json={"paths": {"plex-srv": "/x/Metadata/", "": "/y"}}).json()["paths"] == {"plex-srv": "/x/Metadata"}
    assert client.get("/api/tools/plex-metadata-paths").json() == {"instances": ["plex-srv"], "paths": {"plex-srv": "/x/Metadata"}}
    tools = {t["id"]: t for g in client.get("/api/tools").json()["groups"] for t in g["tools"]}
    assert tools["plex_cleanup"]["available"] is True and tools["plex_cleanup"]["reason"] == ""


def test_running_tools_gates_then_queues_jobs(client, test_db, monkeypatch):
    submitted = _stub_queue(monkeypatch)
    assert client.post("/api/tools/plex_reset_posters/run", json={"confirm": True}).status_code == 400  # no Plex server yet
    _servers(test_db, "plex", "jellyfin")
    assert client.post("/api/tools/nope/run", json={}).status_code == 404
    assert client.post("/api/tools/plex_cleanup/run", json={}).status_code == 400  # no metadata path yet
    assert client.post("/api/tools/plex_reset_posters/run", json={}).status_code == 400  # needs confirm
    assert submitted == []

    # No profile named: the default profile's settings run, and the job message names it.
    all_kinds = ["poster", "background", "logo", "squareart"]
    resp = client.post("/api/tools/plex_reset_posters/run", json={"confirm": True, "dry_run": True})
    assert resp.status_code == 200 and resp.json()["success"]
    job_id = resp.json()["job_id"]
    assert submitted[-1] == (run_tool_background_job, job_id, job_id, "plex_reset_posters", True, {"kinds": all_kinds}, "Default", None)
    job = test_db.query(Job).filter(Job.id == job_id).one()
    assert job.job_type == JOB_TYPE_TOOLS and "image reset (default)" in job.message.lower() and "dry" in job.message.lower()

    # A profile of its own runs with its settings under its name.
    logos = client.post("/api/tools/plex_reset_posters/profiles", json={"name": "Logos only", "settings": {"kinds": ["logo"]}}).json()
    resp = client.post("/api/tools/plex_reset_posters/run", json={"confirm": True, "profile_id": logos["id"]})
    assert resp.status_code == 200 and submitted[-1][3:] == ("plex_reset_posters", False, {"kinds": ["logo"]}, "Logos only", None)
    assert "(Logos only)" in test_db.query(Job).filter(Job.id == resp.json()["job_id"]).one().message
    assert client.post("/api/tools/plex_reset_posters/run", json={"confirm": True, "profile_id": 9999}).status_code == 404  # gone: the page is stale

    # A run can carry its own settings instead (the Overlays page cleans posters only): the
    # kinds ride along in the tool's own order and show in the job message.
    resp = client.post("/api/tools/plex_reset_posters/run", json={"confirm": True, "settings": {"kinds": ["logo", "poster"]}})
    assert resp.status_code == 200 and submitted[-1][3:] == ("plex_reset_posters", False, {"kinds": ["poster", "logo"]}, None, None)
    assert "(posters, logos)" in test_db.query(Job).filter(Job.id == resp.json()["job_id"]).one().message
    assert client.post("/api/tools/plex_reset_posters/run", json={"confirm": True, "settings": {"kinds": []}}).status_code == 400
    assert client.post("/api/tools/plex_reset_posters/run", json={"confirm": True, "settings": {"kinds": ["theme"]}}).status_code == 400
    assert client.post("/api/tools/jellyfin_reset_posters/run", json={"confirm": True, "settings": {"kinds": ["squareart"]}}).status_code == 400  # not a Jellyfin kind

    client.put("/api/tools/plex-metadata-paths", json={"paths": {"plex-srv": "/x/Metadata"}})
    resp = client.post("/api/tools/plex_cleanup/run", json={})
    assert resp.status_code == 200
    assert submitted[-1] == (run_tool_background_job, resp.json()["job_id"], resp.json()["job_id"], "plex_cleanup", False, {"kinds": all_kinds}, "Default", None)
    resp = client.post("/api/tools/jellyfin_clean_image_cache/run", json={"dry_run": True, "settings": {"kinds": ["poster"]}})  # offers neither: ignored
    assert resp.status_code == 200 and submitted[-1][3:] == ("jellyfin_clean_image_cache", False, {}, None, None)

    assert client.post("/api/tools/radarr_rename/run", json={"confirm": True}).status_code == 400  # no Radarr instance
    _arrs(test_db, "radarr")
    assert client.post("/api/tools/radarr_rename/run", json={}).status_code == 400  # needs confirm
    resp = client.post("/api/tools/radarr_rename/run", json={"confirm": True, "dry_run": True})
    assert resp.status_code == 200 and submitted[-1][3:5] == ("radarr_rename", True) and submitted[-1][6] == "Default"
    assert submitted[-1][5]["instances"]["radarr-1"]["count"] == 25  # the instance's default profile
    job_id = resp.json()["job_id"]
    assert "radarr rename (default)" in test_db.query(Job).filter(Job.id == job_id).one().message.lower()
    # While the job is queued or running, the catalog names it on its tool, so the page shows it as busy after a reload.
    tools = {t["id"]: t for g in client.get("/api/tools").json()["groups"] for t in g["tools"]}
    assert tools["radarr_rename"]["active_job"] == job_id and tools["sonarr_rename"]["active_job"] is None
    job = test_db.query(Job).filter(Job.id == job_id).one()
    job.status = "completed"
    test_db.commit()
    tools = {t["id"]: t for g in client.get("/api/tools").json()["groups"] for t in g["tools"]}
    assert tools["radarr_rename"]["active_job"] is None
    # One instance of several: the run names it, the job message says so, and an unknown one is refused.
    upsert_setting(test_db, "radarr_instances", json.dumps([{"name": "radarr-1", "url": "http://r1", "api_key": "k"}, {"name": "radarr-4k", "url": "http://r4", "api_key": "k"}]))
    test_db.commit()
    resp = client.post("/api/tools/radarr_rename/run", json={"confirm": True, "instance": "radarr-4k"})
    assert resp.status_code == 200 and submitted[-1][3] == "radarr_rename" and submitted[-1][7] == "radarr-4k"
    assert "on radarr-4k" in test_db.query(Job).filter(Job.id == resp.json()["job_id"]).one().message
    assert client.post("/api/tools/radarr_rename/run", json={"confirm": True, "instance": "radarr-9"}).status_code == 400
    assert client.post("/api/tools/plex_cleanup/run", json={"instance": "plex-srv"}).status_code == 400  # not a per-instance tool
    # Each instance runs a profile of its own: the one named for it, or its default.
    slow = client.post("/api/tools/radarr_rename/profiles", json={"name": "Slow", "instance": "radarr-4k", "settings": {"count": 3}}).json()
    resp = client.post("/api/tools/radarr_rename/run", json={"confirm": True, "instance": "radarr-4k", "profile_id": slow["id"]})
    assert resp.status_code == 200 and submitted[-1][6:] == ("Slow", "radarr-4k") and list(submitted[-1][5]["instances"]) == ["radarr-4k"]
    assert submitted[-1][5]["instances"]["radarr-4k"]["count"] == 3
    resp = client.post("/api/tools/radarr_rename/run", json={"confirm": True, "profiles": {"radarr-4k": slow["id"]}})  # every instance at once
    assert resp.status_code == 200 and submitted[-1][7] is None and submitted[-1][6] == "radarr-1: Default, radarr-4k: Slow"
    assert {name: one["count"] for name, one in submitted[-1][5]["instances"].items()} == {"radarr-1": 25, "radarr-4k": 3}
    assert "(radarr-1: Default, radarr-4k: Slow)" in test_db.query(Job).filter(Job.id == resp.json()["job_id"]).one().message
    resp = client.post("/api/tools/radarr_rename/run", json={"confirm": True})  # nothing named: each instance's default
    assert submitted[-1][6] == "Default" and {name: one["count"] for name, one in submitted[-1][5]["instances"].items()} == {"radarr-1": 25, "radarr-4k": 25}
    # A profile only runs on the instance it belongs to.
    assert client.post("/api/tools/radarr_rename/run", json={"confirm": True, "instance": "radarr-1", "profile_id": slow["id"]}).status_code == 404
    assert client.post("/api/tools/radarr_rename/run", json={"confirm": True, "profiles": {"radarr-1": slow["id"]}}).status_code == 404
    assert client.post("/api/tools/radarr_rename/run", json={"confirm": True, "profile_id": slow["id"]}).status_code == 400  # which instance?
    tools = {t["id"]: t for g in client.get("/api/tools").json()["groups"] for t in g["tools"]}
    assert tools["radarr_rename"]["per_instance"] is True and tools["radarr_rename"]["instances"] == ["radarr-1", "radarr-4k"]
    assert tools["plex_cleanup"]["per_instance"] is False and tools["plex_cleanup"]["instances"] == ["plex-srv"]


def test_a_scheduled_tool_run_queues_the_tool_job_when_the_tool_is_ready(test_db, monkeypatch):
    import core.scheduler as scheduler_module
    from models.job import Job
    from modules.tools import TOOLS
    from services.tool_profiles import create_profile

    submitted = []
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)
    monkeypatch.setattr(scheduler_module.job_queue, "submit", lambda runner, job_id, *args, **kwargs: submitted.append((runner, args)))
    scheduler_module.run_tool_for_schedule("radarr_rename", True, None)  # no Radarr instance: skipped, no job
    assert submitted == [] and test_db.query(Job).count() == 0
    _arrs(test_db, "radarr")
    scheduler_module.run_tool_for_schedule("radarr_rename", True, 12345)  # a profile that is gone: the default one runs
    assert len(submitted) == 1 and submitted[0][0] is scheduler_module.run_tool_background_job
    job = test_db.query(Job).one()
    assert job.job_type == "Tools" and submitted[0][1][:3] == (job.id, "radarr_rename", True) and submitted[0][1][4] == "Default"
    assert submitted[0][1][3]["instances"]["radarr-1"]["count"] == 25
    # A profile of an instance: the schedule runs that instance with the settings the profile holds now.
    nightly = create_profile(test_db, TOOLS["radarr_rename"], "Nightly", {"count": 3}, "radarr-1")
    scheduler_module.run_tool_for_schedule("radarr_rename", False, nightly.id, "radarr-1")
    assert submitted[1][1][1:] == ("radarr_rename", False, {"instances": {"radarr-1": {**submitted[0][1][3]["instances"]["radarr-1"], "count": 3}}}, "Nightly", "radarr-1")
    scheduler_module.run_tool_for_schedule("nope")
    assert len(submitted) == 2
    # One instance and no profile: its default runs; an instance that is gone skips the run.
    scheduler_module.run_tool_for_schedule("radarr_rename", True, None, "radarr-1")
    assert len(submitted) == 3 and submitted[2][1][4:] == ("Default", "radarr-1") and submitted[2][1][3]["instances"]["radarr-1"]["count"] == 25
    scheduler_module.run_tool_for_schedule("radarr_rename", True, None, "radarr-gone")
    assert len(submitted) == 3
    # Every instance: each runs its own default profile, whatever the others have.
    upsert_setting(test_db, "radarr_instances", json.dumps([{"name": "radarr-1", "url": "http://r1", "api_key": "k"}, {"name": "radarr-4k", "url": "http://r4", "api_key": "k"}]))
    test_db.commit()
    from services.tool_profiles import update_profile
    update_profile(test_db, TOOLS["radarr_rename"], nightly, is_default=True)
    scheduler_module.run_tool_for_schedule("radarr_rename", False)
    assert submitted[3][1][5] is None and submitted[3][1][4] == "radarr-1: Nightly, radarr-4k: Default"
    assert {name: one["count"] for name, one in submitted[3][1][3]["instances"].items()} == {"radarr-1": 3, "radarr-4k": 25}


def test_arr_rename_execute_limits_itself_to_the_named_instance(test_db, monkeypatch):
    from modules import tools as tools_module
    upsert_setting(test_db, "radarr_instances", json.dumps([{"name": "radarr-1", "url": "http://r1", "api_key": "k"}, {"name": "radarr-4k", "url": "http://r4", "api_key": "k"}]))
    test_db.commit()
    seen = []

    class Stub:
        def __init__(self, db, kind, *, per_instance=None, instances=None, **kw):
            seen.append([i["name"] for i in instances] if instances is not None else None)

        def run(self, *, dry_run=False, progress=None):
            return {"files": 0, "items": 0, "checked": 0, "folders": 0, "held": 0, "later": 0, "deleted": 0, "missing": 0, "failed": 0, "stopped": 0, "failed_instances": 0, "unreachable": 0}

    monkeypatch.setattr(tools_module, "ArrRenamer", Stub)
    execute = tools_module.TOOLS["radarr_rename"].execute
    execute(test_db, lambda *a: None, None, True, {"instances": {}})
    execute(test_db, lambda *a: None, None, True, {"instances": {}}, "radarr-4k")
    assert seen == [None, ["radarr-4k"]]
    with pytest.raises(RuntimeError, match="radarr-9"):
        execute(test_db, lambda *a: None, None, True, {"instances": {}}, "radarr-9")


def test_arr_upgrade_runs_each_instance_with_its_own_profile(client, test_db, monkeypatch):
    from modules import tools as tools_module
    upsert_setting(test_db, "radarr_instances", json.dumps([{"name": "radarr-1", "url": "http://r1", "api_key": "k"}, {"name": "radarr-4k", "url": "http://r4", "api_key": "k"}]))
    test_db.commit()
    submitted = _stub_queue(monkeypatch)
    profiles = {p["instance"]: p for p in next(t for g in client.get("/api/tools").json()["groups"] for t in g["tools"] if t["id"] == "radarr_upgrade")["profiles"]}
    assert set(profiles) == {"radarr-1", "radarr-4k"}
    # A profile with a tag the arr cannot make, or none, is refused; a good one is saved as given.
    put = lambda settings: client.put(f"/api/tools/radarr_upgrade/profiles/{profiles['radarr-4k']['id']}", json={"settings": settings})  # noqa: E731
    assert "Set a search tag" in put({"tag_name": ""}).json()["detail"] and "must differ" in put({"tag_name": "x", "ignore_tag": "X"}).json()["detail"]
    assert "letters, digits and -" in put({"only_tags": "kids, not ok"}).json()["detail"]
    saved = put({"count": 2, "mode": "cutoff", "monitored": "any", "only_tags": "kids,4k", "quality_profiles": " Ultra-HD ", "min_age_days": "30", "max_grabs": 1}).json()["settings"]
    assert (saved["count"], saved["mode"], saved["monitored"], saved["only_tags"], saved["quality_profiles"], saved["min_age_days"], saved["max_grabs"]) == (2, "cutoff", "any", "kids, 4k", "Ultra-HD", 30, 1)

    # A run asks first. Every instance runs its own default; one named runs alone.
    assert client.post("/api/tools/radarr_upgrade/run", json={"dry_run": True}).status_code == 400
    assert client.post("/api/tools/radarr_upgrade/run", json={"dry_run": True, "confirm": True}).status_code == 200
    assert client.post("/api/tools/radarr_upgrade/run", json={"instance": "radarr-4k", "confirm": True}).status_code == 200
    everyone, one = submitted[0], submitted[1]  # (fn, job id, job id, tool id, dry run, settings, profile name, instance)
    assert everyone[3:5] == ("radarr_upgrade", True) and {n: s["count"] for n, s in everyone[5]["instances"].items()} == {"radarr-1": 5, "radarr-4k": 2}
    assert one[4] is False and list(one[5]["instances"]) == ["radarr-4k"] and one[7] == "radarr-4k"

    seen = []

    class Stub:
        def __init__(self, db, kind, *, per_instance=None, instances=None, **kw):
            seen.append(([i["name"] for i in instances] if instances is not None else None, per_instance))

        def run(self, *, dry_run=False, progress=None):
            return {"searched": 3, "grabbed": 2, "held": 1, "passed": 0, "failed": 1, "cycles": 1, "skipped": 0, "stopped": 0, "failed_instances": 0, "unreachable": 1,
                    "imported": 2, "not_imported": 0, "stuck": 1}

    monkeypatch.setattr(tools_module, "ArrUpgrader", Stub)
    execute = tools_module.TOOLS["radarr_upgrade"].execute
    assert execute(test_db, lambda *a: None, None, False, one[5], "radarr-4k") == ("3 item(s) searched, 2 release(s) grabbed, 1 held back, 1 failed, 1 cycle(s) restarted, "
                                                                                   "2 earlier grab(s) imported, 1 stuck in the queue, 1 instance(s) unreachable")
    assert execute(test_db, lambda *a: None, None, True, {"instances": {}}).startswith("3 item(s) would be searched, 1 held back")
    assert seen[0][0] == ["radarr-4k"] and seen[0][1]["radarr-4k"]["mode"] == "cutoff" and seen[1][0] is None
    with pytest.raises(RuntimeError, match="radarr-9"):
        execute(test_db, lambda *a: None, None, True, {"instances": {}}, "radarr-9")

    # What a run leaves for the next ones is kept out of the settings the pages fetch.
    upsert_setting(test_db, "radarr_upgrade_state", json.dumps({"radarr-1": {"pending": [], "fails": {"7": 1}}}))
    test_db.commit()
    assert "radarr_upgrade_state" not in client.get("/api/settings/").json()


def test_tool_profiles_endpoints(client, test_db):
    assert client.post("/api/tools/nope/profiles", json={"name": "x"}).status_code == 404
    assert client.post("/api/tools/jellyfin_clean_image_cache/profiles", json={"name": "x"}).status_code == 400  # no settings, no profiles
    upsert_setting(test_db, "sonarr_instances", json.dumps([{"name": "sonarr-1", "url": "http://s1", "api_key": "k"}, {"name": "sonarr-4k", "url": "http://s4", "api_key": "k"}]))
    test_db.commit()
    defaults = {"count": 25, "batch": False, "tag_name": "renamed", "ignore_tag": "rename-ignore", "scope": "files", "max_renames": 0, "scan_to_limit": False, "ignore_case": False,
                "ignore_punctuation": False, "allow_irregular": False, "hold_tba": True, "refresh_first": False, "title_filter": "", "notify": False, "notify_all": False}

    def profiles(instance="sonarr-1"):
        every = {t["id"]: t["profiles"] for g in client.get("/api/tools").json()["groups"] for t in g["tools"]}["sonarr_rename"]
        return [p for p in every if p["instance"] == instance]

    # Each instance starts with a Default profile of its own.
    [default] = profiles()
    [default_4k] = profiles("sonarr-4k")
    assert default["name"] == "Default" and default["is_default"] and default["settings"] == defaults and default["id"] != default_4k["id"]

    # A new profile belongs to the instance it is made for: normalized, the name trimmed.
    resp = client.post("/api/tools/sonarr_rename/profiles", json={"name": " Nightly ", "instance": "sonarr-1", "settings": {"count": 10, "scope": "both", "max_renames": 4, "tag_name": "rn"}})
    assert resp.status_code == 200
    nightly = resp.json()
    assert nightly["name"] == "Nightly" and nightly["is_default"] is False and nightly["instance"] == "sonarr-1"
    assert nightly["settings"] == {**defaults, "count": 10, "scope": "both", "max_renames": 4, "tag_name": "rn"}
    assert [p["name"] for p in profiles()] == ["Default", "Nightly"] and [p["name"] for p in profiles("sonarr-4k")] == ["Default"]
    assert client.post("/api/tools/sonarr_rename/profiles", json={"name": "nightly", "instance": "sonarr-1"}).status_code == 400  # taken, whatever the case
    assert client.post("/api/tools/sonarr_rename/profiles", json={"name": "  ", "instance": "sonarr-1"}).status_code == 400
    assert client.post("/api/tools/sonarr_rename/profiles", json={"name": "Blank", "instance": "sonarr-1"}).json()["settings"] == defaults  # no settings: the defaults
    # The other instance may use the same name: its list is its own. A profile needs an instance, and a configured one.
    other = client.post("/api/tools/sonarr_rename/profiles", json={"name": "Nightly", "instance": "sonarr-4k", "settings": {"count": 2}})
    assert other.status_code == 200 and other.json()["instance"] == "sonarr-4k" and other.json()["settings"]["count"] == 2
    assert "instance" in client.post("/api/tools/sonarr_rename/profiles", json={"name": "Loose"}).json()["detail"]
    assert client.post("/api/tools/sonarr_rename/profiles", json={"name": "Ghost", "instance": "ghost"}).status_code == 400

    # A tag the arr could never make is refused with a message, and nothing of that save lands.
    bad = client.put(f"/api/tools/sonarr_rename/profiles/{nightly['id']}", json={"name": "Nightly 2", "settings": {"tag_name": "re named"}})
    assert bad.status_code == 400 and bad.json()["detail"] == "The cycle tag 're named' can only have letters, digits and -."
    kept = next(p for p in profiles() if p["id"] == nightly["id"])
    assert kept["name"] == "Nightly" and kept["settings"]["count"] == 10
    assert client.put(f"/api/tools/sonarr_rename/profiles/{nightly['id']}", json={"name": "Blank"}).status_code == 400  # another profile of this instance
    assert client.put("/api/tools/sonarr_rename/profiles/9999", json={"name": "x"}).status_code == 404

    # Rename, resave and promote; the default moves over within the instance, the other instance keeps its own.
    resp = client.put(f"/api/tools/sonarr_rename/profiles/{nightly['id']}", json={"name": "Nightly 2", "settings": {"count": 7}, "is_default": True})
    assert resp.status_code == 200 and resp.json()["name"] == "Nightly 2" and resp.json()["is_default"] is True
    assert resp.json()["settings"] == {**defaults, "count": 7}
    assert [(p["name"], p["is_default"]) for p in profiles()] == [("Default", False), ("Nightly 2", True), ("Blank", False)]
    assert [(p["name"], p["is_default"]) for p in profiles("sonarr-4k")] == [("Default", True), ("Nightly", False)]

    # Deleting the default passes it to the oldest one left in that list; the only profile of a list stays.
    assert client.delete(f"/api/tools/sonarr_rename/profiles/{nightly['id']}").status_code == 200
    assert [(p["name"], p["is_default"]) for p in profiles()] == [("Default", True), ("Blank", False)]
    blank_id = next(p["id"] for p in profiles() if p["name"] == "Blank")
    assert client.delete(f"/api/tools/sonarr_rename/profiles/{blank_id}").status_code == 200
    assert client.delete(f"/api/tools/sonarr_rename/profiles/{default['id']}").status_code == 400
    assert client.delete("/api/tools/sonarr_rename/profiles/9999").status_code == 404
    assert [p["name"] for p in profiles("sonarr-4k")] == ["Default", "Nightly"]  # untouched by all of that

    # Image tools keep their kinds, in the tool's order, at least one of them.
    _servers(test_db, "plex")
    logos = client.post("/api/tools/plex_reset_posters/profiles", json={"name": "Logos and posters", "settings": {"kinds": ["logo", "poster"]}})
    assert logos.status_code == 200 and logos.json()["settings"] == {"kinds": ["poster", "logo"]}
    assert client.post("/api/tools/plex_reset_posters/profiles", json={"name": "Nothing", "settings": {"kinds": []}}).json()["detail"] == "Switch on at least one image type."
    assert "Unknown image kind" in client.post("/api/tools/plex_reset_posters/profiles", json={"name": "Odd", "settings": {"kinds": ["theme"]}}).json()["detail"]


def test_plex_metadata_path_check(client, tmp_path):
    bad = client.post("/api/tools/plex-metadata-paths/check", json={"instance": "srv", "path": str(tmp_path / "missing")}).json()
    assert bad["ok"] is False and "not found" in bad["reason"]
    (tmp_path / "empty").mkdir()
    nope = client.post("/api/tools/plex-metadata-paths/check", json={"instance": "srv", "path": str(tmp_path / "empty")}).json()
    assert nope["ok"] is False and "Movies" in nope["reason"]
    (tmp_path / "meta" / "Movies").mkdir(parents=True)
    ok = client.post("/api/tools/plex-metadata-paths/check", json={"instance": "srv", "path": str(tmp_path / "meta")}).json()
    assert ok["ok"] is True and ok["verified_title"] is None and ok["reason"] == "Found the Metadata folder."
    (tmp_path / "pms" / "Metadata" / "TV Shows").mkdir(parents=True)
    pms = client.post("/api/tools/plex-metadata-paths/check", json={"instance": "srv", "path": str(tmp_path / "pms")}).json()
    assert pms["ok"] is True and pms["reason"] == "Found the Metadata folder inside it."


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_plex_metadata_path_check_reports_a_folder_it_cannot_delete_in(client, test_db, tmp_path, monkeypatch):
    from services.plex_bundles import plex_bundle_dir
    from services.poster_tools import PosterTools
    _servers(test_db, "plex")
    item = type("Item", (), {"item_id": "1", "item_type": "movie", "title": "Film", "guid": "plex://movie/xyz", "edition_title": None, "native": None})()
    fake = type("Client", (), {"get_library_items": lambda self, key: [item]})()
    monkeypatch.setattr("api.tools.create_media_server_client", lambda inst: fake)
    monkeypatch.setattr(PosterTools, "enabled_libraries", lambda self: {"plex-srv": [{"key": "1", "title": "Movies", "type": "movie", "enabled": True}]})
    root = tmp_path / "meta"
    uploads = plex_bundle_dir(str(root), "plex://movie/xyz", "movie") / "Uploads" / "posters"
    uploads.mkdir(parents=True)
    ok = client.post("/api/tools/plex-metadata-paths/check", json={"instance": "plex-srv", "path": str(root)}).json()
    assert ok["ok"] is True and ok["verified_title"] == "Film"
    uploads.chmod(0o555)
    try:
        locked = client.post("/api/tools/plex-metadata-paths/check", json={"instance": "plex-srv", "path": str(root)}).json()
        assert locked["ok"] is False and "cannot delete" in locked["reason"] and "PGID" in locked["reason"]
    finally:
        uploads.chmod(0o755)


class _TagArr:
    """An arr at the request level, for the tag lists: its library, its tags and the editor."""

    def __init__(self, kind, items):
        self.url = "http://arr"
        self.noun = "movie" if kind == "radarr" else "series"
        self.items = {i["id"]: dict(i) for i in items}
        self.tags = {}
        self.puts = []
        self.down = False  # answers no read, as the client gives None for any failure

    def make_get_request(self, endpoint):
        path = endpoint.split("/api/v3/", 1)[1]
        if self.down:
            return None
        if path == "tag":
            return [{"id": tid, "label": label} for label, tid in self.tags.items()]
        assert path == self.noun, path
        return [dict(i) for i in self.items.values()]

    def make_post_request(self, endpoint, json=None):
        assert endpoint.endswith("/api/v3/tag"), endpoint
        self.tags[json["label"]] = len(self.tags) + 1
        return {"id": self.tags[json["label"]], "label": json["label"]}

    def make_put_request(self, endpoint, json=None):
        assert endpoint.endswith(f"/api/v3/{self.noun}/editor"), endpoint
        self.puts.append(json)
        for mid in json[f"{self.noun}Ids"]:
            tags = set(self.items[mid].get("tags", []))
            self.items[mid]["tags"] = sorted(tags | set(json["tags"]) if json["applyTags"] == "add" else tags - set(json["tags"]))
        return [{}]


def test_an_arr_instance_lists_its_tags_and_quality_profiles_to_pick_from(client, test_db, monkeypatch):
    _arrs(test_db, "radarr")
    movies = [{"id": 1, "tags": [2, 3], "qualityProfileId": 7}, {"id": 2, "tags": [3], "qualityProfileId": 7}, {"id": 3, "qualityProfileId": 9}]

    class Arr:
        url, down = "http://arr", False

        def make_get_request(self, endpoint):
            if self.down:
                return None
            return {"tag": [{"id": 3, "label": "kids"}, {"id": 2, "label": "4k"}, {"id": 5, "label": "unused"}],
                    "qualityprofile": [{"id": 9, "name": "Ultra-HD"}, {"id": 7, "name": "HD - 720p/1080p"}, {"id": 4, "name": "any"}],
                    "movie": movies}[endpoint.split("/api/v3/", 1)[1]]

    arr = Arr()
    monkeypatch.setattr("services.arr_upgrader._connect", lambda inst, kind: arr)
    url = "/api/tools/radarr_upgrade/arr-options"
    assert client.get(url, params={"instance": "radarr-1"}).json() == {
        "instance": "radarr-1",
        "tags": [{"name": "4k", "count": 1}, {"name": "kids", "count": 2}, {"name": "unused", "count": 0}],
        "quality_profiles": [{"name": "any", "count": 0}, {"name": "HD - 720p/1080p", "count": 2}, {"name": "Ultra-HD", "count": 1}],
    }
    assert client.get(url, params={"instance": "nope"}).status_code == 400
    assert client.get("/api/tools/plex_cleanup/arr-options", params={"instance": "radarr-1"}).status_code == 400
    assert client.get("/api/tools/nope/arr-options", params={"instance": "radarr-1"}).status_code == 404
    arr.down = True
    resp = client.get(url, params={"instance": "radarr-1"})
    assert resp.status_code == 502 and resp.json()["detail"].startswith("radarr-1: ")
    monkeypatch.setattr("services.arr_upgrader._connect", lambda inst, kind: None)
    assert client.get(url, params={"instance": "radarr-1"}).json()["detail"] == "Cannot connect to Radarr 'radarr-1'."


def test_a_renamer_tag_list_reads_and_sets_the_tag_in_the_arr(client, test_db, monkeypatch):
    _arrs(test_db, "radarr")
    arr = _TagArr("radarr", [{"id": 2, "title": "Beta", "year": 2021, "tags": [7]}, {"id": 1, "title": "Alpha", "year": 2020}, {"id": 3, "title": "gamma", "year": 0}])
    monkeypatch.setattr("services.arr_renamer._connect", lambda inst, kind: arr)
    url, who = "/api/tools/radarr_rename/tagged", {"instance": "radarr-1", "tag": "rename-ignore"}
    listed = client.get(url, params=who).json()
    assert listed == {"instance": "radarr-1", "tag": "rename-ignore", "items": [
        {"id": 1, "title": "Alpha", "year": 2020, "tagged": False}, {"id": 2, "title": "Beta", "year": 2021, "tagged": False},
        {"id": 3, "title": "gamma", "year": None, "tagged": False}]}
    assert arr.tags == {}  # a read never makes the tag
    assert client.put(url, json={**who, "remove": [2]}).json() == {"success": True, "added": 0, "removed": 0} and arr.puts == []  # nothing carries it yet

    # The first item to get the tag makes it in the arr.
    assert client.put(url, json={**who, "add": [3, 1]}).json() == {"success": True, "added": 2, "removed": 0}
    assert arr.tags == {"rename-ignore": 1} and arr.puts == [{"movieIds": [1, 3], "tags": [1], "applyTags": "add"}]
    assert client.put(url, json={**who, "add": [2], "remove": [3]}).json() == {"success": True, "added": 1, "removed": 1}
    assert arr.puts[1:] == [{"movieIds": [2], "tags": [1], "applyTags": "add"}, {"movieIds": [3], "tags": [1], "applyTags": "remove"}]
    assert [i["title"] for i in client.get(url, params=who).json()["items"] if i["tagged"]] == ["Alpha", "Beta"]
    # The cycle tag works the same way, apart from the ignore tag.
    assert client.put(url, json={**who, "tag": "renamed", "add": [3]}).json() == {"success": True, "added": 1, "removed": 0}
    assert arr.tags == {"rename-ignore": 1, "renamed": 2} and arr.items[3]["tags"] == [2]
    assert [i["title"] for i in client.get(url, params={**who, "tag": "renamed"}).json()["items"] if i["tagged"]] == ["gamma"]
    assert arr.items[2]["tags"] == [1, 7]  # its other tags stay

    # The titles alone, for trying a title filter: the same list for the renamer and the upgrader.
    for tool in ("radarr_rename", "radarr_upgrade"):
        assert client.get(f"/api/tools/{tool}/titles", params={"instance": "radarr-1"}).json() == {"instance": "radarr-1", "items": [
            {"id": 1, "title": "Alpha", "year": 2020}, {"id": 2, "title": "Beta", "year": 2021}, {"id": 3, "title": "gamma", "year": None}]}
    assert client.get("/api/tools/radarr_rename/titles", params={"instance": "nope"}).status_code == 400
    assert client.get("/api/tools/plex_cleanup/titles", params={"instance": "radarr-1"}).status_code == 400
    assert client.get("/api/tools/nope/titles", params={"instance": "radarr-1"}).status_code == 404

    # Refused before the arr is asked anything.
    assert client.get(url, params={**who, "instance": "nope"}).status_code == 400
    assert client.get(url, params={**who, "tag": " "}).json()["detail"] == "Set the tag first."
    assert client.get(url, params={**who, "tag": "rename ignore"}).json()["detail"] == "The tag 'rename ignore' can only have letters, digits and -."
    assert client.get("/api/tools/plex_cleanup/tagged", params=who).json()["detail"] == "Clean up Plex uploads has no tags to set."
    assert client.get("/api/tools/nope/tagged", params=who).status_code == 404
    assert client.put(url, json={**who, "add": [1], "remove": [1]}).status_code == 400
    # An arr that stops answering is a 502 naming the instance.
    arr.down = True
    resp = client.get(url, params=who)
    assert resp.status_code == 502 and resp.json()["detail"].startswith("radarr-1: ")
    resp = client.get("/api/tools/radarr_rename/titles", params={"instance": "radarr-1"})
    assert resp.status_code == 502 and resp.json()["detail"].startswith("radarr-1: ")
