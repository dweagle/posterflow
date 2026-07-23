"""Unified Asset Renamer: /asset-rename endpoint dispatch + flow-config forward-map."""

import json

from models.setting import Setting
from api.poster_manager import _normalize_flow_config


def _set_include(test_db, include):
    test_db.add(Setting(key="asset_renamer_include", value=json.dumps(include)))
    test_db.commit()


def _capture(monkeypatch):
    calls = []

    def _submit(fn, *a, **k):
        calls.append({"fn": fn.__name__, "config": a[-1] if a else None})

    monkeypatch.setattr("api.asset_rename.job_queue.submit", _submit)
    return calls


def test_asset_rename_posters_only(client, test_db, monkeypatch):
    _set_include(test_db, ["posters"])
    calls = _capture(monkeypatch)
    resp = client.post("/api/posterflow/asset-rename", json={"dry_run": True})
    assert resp.status_code == 200
    # One merged Asset Renamer job now handles every selected type.
    assert [j["type"] for j in resp.json()["jobs"]] == ["assets"]
    assert [c["fn"] for c in calls] == ["run_rename_background_job"]
    assert calls[0]["config"]["include"] == ["posters"]


def test_asset_rename_artwork_only(client, test_db, monkeypatch):
    _set_include(test_db, ["logo", "background"])
    calls = _capture(monkeypatch)
    resp = client.post("/api/posterflow/asset-rename", json={"dry_run": False})
    assert resp.status_code == 200
    assert [j["type"] for j in resp.json()["jobs"]] == ["assets"]
    assert [c["fn"] for c in calls] == ["run_rename_background_job"]
    assert calls[0]["config"]["include"] == ["logo", "background"]


def test_asset_rename_both(client, test_db, monkeypatch):
    _set_include(test_db, ["posters", "logo", "squareart"])
    calls = _capture(monkeypatch)
    resp = client.post("/api/posterflow/asset-rename", json={"dry_run": False})
    assert resp.status_code == 200
    assert [j["type"] for j in resp.json()["jobs"]] == ["assets"]
    assert [c["fn"] for c in calls] == ["run_rename_background_job"]
    assert calls[0]["config"]["include"] == ["posters", "logo", "squareart"]


def test_asset_rename_default_is_posters(client, test_db, monkeypatch):
    calls = _capture(monkeypatch)  # no asset_renamer_include set
    resp = client.post("/api/posterflow/asset-rename", json={"dry_run": True})
    assert resp.status_code == 200
    assert [j["type"] for j in resp.json()["jobs"]] == ["assets"]
    assert [c["fn"] for c in calls] == ["run_rename_background_job"]
    assert calls[0]["config"]["include"] == ["posters"]


def test_asset_rename_noop_when_nothing_selected(client, test_db, monkeypatch):
    _set_include(test_db, [])
    calls = _capture(monkeypatch)
    resp = client.post("/api/posterflow/asset-rename", json={"dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["jobs"] == []
    assert calls == []


# ── flow-config forward-map (legacy split rename steps → rename_assets) ───────

def test_normalize_forward_maps_legacy_rename_keys():
    legacy = {
        "rename_posters": {"enabled": True, "stop_on_error": True},
        "rename_artwork": {"enabled": False, "stop_on_error": False},
    }
    n = _normalize_flow_config(legacy)
    assert "rename_assets" in n
    assert n["rename_assets"]["enabled"] is True  # posters were on
    assert "rename_posters" not in n and "rename_artwork" not in n


def test_normalize_forward_map_enabled_when_only_artwork_on():
    n = _normalize_flow_config({"rename_artwork": {"enabled": True, "stop_on_error": False}})
    assert n["rename_assets"]["enabled"] is True


def test_normalize_keeps_explicit_rename_assets():
    n = _normalize_flow_config({"rename_assets": {"enabled": False, "stop_on_error": True}})
    assert n["rename_assets"] == {"enabled": False, "stop_on_error": True}
