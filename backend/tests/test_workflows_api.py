import time
from models.workflow import Workflow


def test_create_list_and_default(client):
    """First workflow becomes default; subsequent ones do not."""
    r1 = client.post("/api/posterflow/workflows", json={"name": "Alpha"})
    assert r1.status_code == 200
    wf1 = r1.json()
    assert wf1["is_default"] is True
    assert wf1["config"]["sync_drives"]["enabled"] is True  # normalized defaults

    r2 = client.post("/api/posterflow/workflows", json={
        "name": "Beta",
        "config": {"sync_drives": {"enabled": False, "stop_on_error": True}},
    })
    assert r2.status_code == 200
    wf2 = r2.json()
    assert wf2["is_default"] is False
    assert wf2["config"]["sync_drives"]["enabled"] is False

    listed = client.get("/api/posterflow/workflows").json()
    assert {w["name"] for w in listed} == {"Alpha", "Beta"}


def test_create_requires_name(client):
    r = client.post("/api/posterflow/workflows", json={"name": "   "})
    assert r.status_code == 400


def test_update_name_config_and_default(client):
    a = client.post("/api/posterflow/workflows", json={"name": "A"}).json()
    b = client.post("/api/posterflow/workflows", json={"name": "B"}).json()

    # Rename + config change
    upd = client.put(f"/api/posterflow/workflows/{b['id']}", json={
        "name": "B2",
        "config": {"border_replacer": {"enabled": True, "stop_on_error": False}},
    }).json()
    assert upd["name"] == "B2"
    assert upd["config"]["border_replacer"]["enabled"] is True

    # Promote B to default; A should lose default
    client.put(f"/api/posterflow/workflows/{b['id']}", json={"is_default": True})
    listed = {w["id"]: w for w in client.get("/api/posterflow/workflows").json()}
    assert listed[b["id"]]["is_default"] is True
    assert listed[a["id"]]["is_default"] is False


def test_delete_reassigns_default_and_blocks_last(client):
    a = client.post("/api/posterflow/workflows", json={"name": "A"}).json()  # default
    b = client.post("/api/posterflow/workflows", json={"name": "B"}).json()

    # Delete the default (A) -> B becomes default
    assert client.delete(f"/api/posterflow/workflows/{a['id']}").status_code == 200
    remaining = client.get("/api/posterflow/workflows").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == b["id"]
    assert remaining[0]["is_default"] is True

    # Cannot delete the only remaining workflow
    assert client.delete(f"/api/posterflow/workflows/{b['id']}").status_code == 400


def test_run_flow_uses_selected_workflow_config(client, test_db, monkeypatch):
    """Running with workflow_id should pass that workflow's config as override."""
    import api.poster_manager as poster_manager_module

    wf = client.post("/api/posterflow/workflows", json={
        "name": "OnlySync",
        "config": {
            "sync_drives": {"enabled": True, "stop_on_error": True},
            "rename_posters": {"enabled": False, "stop_on_error": True},
            "border_replacer": {"enabled": False, "stop_on_error": True},
            "plex_upload": {"enabled": False, "stop_on_error": False},
            "detect_unmatched": {"enabled": False, "stop_on_error": False},
        },
    }).json()

    captured = {}

    def _capture(job_id, dry_run, release_flow_lock, config_override=None):
        captured["config_override"] = config_override
        release_flow_lock()

    monkeypatch.setattr("api.poster_manager.run_flow_background_job", _capture)

    poster_manager_module._flow_running = False
    poster_manager_module._flow_started_at = None

    try:
        resp = client.post("/api/posterflow/flow/run", json={"workflow_id": wf["id"]})
        assert resp.status_code == 200

        for _ in range(50):
            if "config_override" in captured:
                break
            time.sleep(0.01)

        override = captured.get("config_override")
        assert override is not None
        assert override["sync_drives"]["enabled"] is True
        assert override["rename_posters"]["enabled"] is False
    finally:
        poster_manager_module._flow_running = False
        poster_manager_module._flow_started_at = None
