"""Quick-add notice: ignore a pending drop from the popup and push its file to the drive unchanged."""

import json
from types import SimpleNamespace

import pytest

from api.idarr import _resolve_scope_token
from core.job_queue import job_queue
from models.idarr import IdarrPendingMatch, build_idarr_asset_key
from models.job import Job
from models.setting import Setting
import modules.idarr as idarr_module

URL = "/api/idarr/pending-matches/ignore-and-upload"


@pytest.fixture
def scope(test_db, tmp_path, monkeypatch):
    source_dir = tmp_path / "scope"
    source_dir.mkdir()
    (source_dir / "who knows.jpg").write_bytes(b"x")
    test_db.add(Setting(key="maker_tools_idarr_config", value=json.dumps({
        "sync_targets": [{"label": "CL2K", "personal_drive_id": "drive-1", "source_dir": str(source_dir)}],
        "tmdb_api_key": "k",
    })))
    test_db.commit()

    token = _resolve_scope_token(test_db, 0)
    key = build_idarr_asset_key("pending", "Who Knows", 2021, token)
    test_db.add(IdarrPendingMatch(asset_key=key, title="Who Knows", year=2021, asset_type="pending"))
    test_db.commit()

    submitted = []
    monkeypatch.setattr(job_queue, "submit", lambda *args, **kwargs: submitted.append(args))
    return SimpleNamespace(source_dir=source_dir, key=key, token=token, submitted=submitted)


def _ignored_keys(client):
    items = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    return {item["asset_key"] for item in items}


def _pending(test_db, key):
    return test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == key).first()


def test_ignores_the_title_and_queues_the_file_as_is(client, test_db, scope):
    response = client.post(URL, json={"asset_key": scope.key, "relative_path": "who knows.jpg", "sync_target_index": 0, "upload": True})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert scope.key in _ignored_keys(client)
    assert _pending(test_db, scope.key) is None

    ((job_fn, job_id, _, config),) = scope.submitted
    assert job_fn is idarr_module.run_idarr_file_upload_job
    assert job_id == data["upload_job_id"]
    assert config["personal_drive_id"] == "drive-1"
    assert config["relative_paths"] == ["who knows.jpg"]
    assert test_db.query(Job).filter(Job.id == job_id).first().status == "pending"


def test_ignore_without_upload_queues_nothing(client, test_db, scope):
    response = client.post(URL, json={"asset_key": scope.key, "sync_target_index": 0, "upload": False})

    assert response.status_code == 200, response.text
    assert response.json()["upload_job_id"] is None
    assert scope.submitted == []
    assert scope.key in _ignored_keys(client)
    assert _pending(test_db, scope.key) is None


def test_missing_file_leaves_the_pending_row_alone(client, test_db, scope):
    response = client.post(URL, json={"asset_key": scope.key, "relative_path": "gone.jpg", "sync_target_index": 0})

    assert response.status_code == 404
    assert _pending(test_db, scope.key) is not None
    assert scope.key not in _ignored_keys(client)
    assert scope.submitted == []


def test_path_outside_the_sync_folder_is_rejected(client, test_db, scope):
    (scope.source_dir.parent / "outside.jpg").write_bytes(b"x")

    response = client.post(URL, json={"asset_key": scope.key, "relative_path": "../outside.jpg", "sync_target_index": 0})

    assert response.status_code == 403
    assert _pending(test_db, scope.key) is not None
    assert scope.submitted == []


def test_row_already_resolved_elsewhere_is_reported(client, scope):
    key = build_idarr_asset_key("pending", "Not Here", None, scope.token)

    response = client.post(URL, json={"asset_key": key, "relative_path": "who knows.jpg", "sync_target_index": 0})

    assert response.status_code == 404
    assert scope.submitted == []
