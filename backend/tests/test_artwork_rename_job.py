"""Artwork placement via the merged Asset Renamer job.

The service-level placement is covered in test_artwork_scan.py. This covers the merged
`run_rename_background_job` orchestrator running its *artwork pass*: an artwork-only Include
(posters unchecked) must place logo/background/square into the item folders WITHOUT needing
poster drives, and the real configs the endpoint + workflow build must actually place files.
"""

import json

import pytest

from models.artwork_drive import ArtworkDrive
from models.job import Job, JOB_TYPE_POSTER_RENAMER, create_job
from models.setting import Setting, upsert_setting
from modules.renamer import run_rename_background_job
from asset_helpers import _seed_artwork


def _make_drive(db, name, drive_id, path):
    drive = ArtworkDrive(name=name, drive_id=drive_id, subscribed=True, custom_path=str(path))
    db.add(drive)
    db.commit()
    db.refresh(drive)
    return drive


FOLDER = "Inception (2010) {tmdb-27205}"
MEDIA = {
    "movies": [{"title": "Inception", "year": 2010, "tmdb_id": 27205, "folder": FOLDER, "instance": "plex"}],
    "series": [],
    "collections": [],
}


@pytest.fixture
def artwork_env(test_db, tmp_path, monkeypatch):
    """A subscribed artwork drive holding all three types, plus a stubbed media library.
    The merged job opens its own session and fetches media once — both are pointed at the test."""
    drive_root = tmp_path / "makerA"
    _seed_artwork(
        drive_root,
        logo=f"{FOLDER}.png",
        background=f"{FOLDER}.jpg",
        squareart=f"{FOLDER}.jpg",
    )
    drive = _make_drive(test_db, "MakerA", "makerA", drive_root)
    upsert_setting(test_db, "artwork_drive_priority", '{"drive_ids": [%d]}' % drive.id)
    test_db.commit()

    monkeypatch.setattr("modules.renamer.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    seen = {}

    def fake_media(self, log_tag=None, setting_key=None):
        seen["setting_key"] = setting_key
        return MEDIA

    monkeypatch.setattr(
        "services.poster_renamer.PosterRenameService.get_media_from_instances", fake_media
    )
    return {"dest": tmp_path / "dest", "seen": seen}


def _job(db) -> int:
    job = create_job(db, job_type=JOB_TYPE_POSTER_RENAMER, message="test")
    return job.id


def _run(test_db, config, *, triggered_by="manual"):
    """Run the merged job with cleanup disabled so these tests stay focused on placement."""
    config.setdefault("run_cleanup", False)
    config.setdefault("library_setting_key", "asset_renamer_libraries")
    run_rename_background_job(_job(test_db), config, True, triggered_by)


def test_artwork_only_places_all_types(test_db, artwork_env):
    """Posters unchecked (artwork-only Include): all three types land, no poster drives needed."""
    dest = artwork_env["dest"]
    _run(test_db, {
        "destination": str(dest), "action_type": "copy", "dry_run": False,
        "asset_folders": True, "include": ["logo", "background", "squareart"],
    })
    item = dest / FOLDER
    assert (item / "logo.png").is_file()
    assert (item / "background.jpg").is_file()
    assert (item / "square.jpg").is_file()
    # The single fetch used the shared asset-renamer library selection.
    assert artwork_env["seen"]["setting_key"] == "asset_renamer_libraries"


def test_artwork_pass_falls_back_to_poster_destination(test_db, artwork_env):
    """No destination in config → the artwork pass falls back to the poster destination setting."""
    dest = artwork_env["dest"]
    upsert_setting(test_db, "poster_destination", str(dest))
    upsert_setting(test_db, "poster_asset_folders", "true")
    test_db.commit()

    _run(test_db, {"dry_run": False, "include": ["logo", "background", "squareart"]})

    item = dest / FOLDER
    assert (item / "logo.png").is_file()
    assert (item / "background.jpg").is_file()
    assert (item / "square.jpg").is_file()


def test_include_limits_which_types_are_placed(test_db, artwork_env):
    """Only the Include-selected artwork types are placed."""
    dest = artwork_env["dest"]
    _run(test_db, {"destination": str(dest), "dry_run": False, "asset_folders": True, "include": ["logo"]})

    item = dest / FOLDER
    assert (item / "logo.png").is_file()
    assert not (item / "background.jpg").exists()
    assert not (item / "square.jpg").exists()


def test_flat_layout_places_artwork(test_db, artwork_env):
    """asset_folders=False mirrors the poster renamer's flat naming."""
    dest = artwork_env["dest"]
    _run(test_db, {"destination": str(dest), "dry_run": False, "asset_folders": False, "include": ["logo"]})

    assert (dest / f"{FOLDER}-logo.png").is_file()


def test_kometa_compatible_square_art_names(test_db, artwork_env):
    """Square art is written 'square' (Kometa-detectable); nested → 'square.ext', flat → '{name}-square.ext'."""
    dest = artwork_env["dest"]
    _run(test_db, {"destination": str(dest), "dry_run": False, "asset_folders": True, "include": ["squareart"]})
    assert (dest / FOLDER / "square.jpg").is_file()
    assert not (dest / FOLDER / "squareart.jpg").exists()

    flat_dest = dest.parent / "flat"
    _run(test_db, {"destination": str(flat_dest), "dry_run": False, "asset_folders": False, "include": ["squareart"]})
    assert (flat_dest / f"{FOLDER}-square.jpg").is_file()
    assert not (flat_dest / f"{FOLDER} - squareart.jpg").exists()


def test_dry_run_reports_without_writing(test_db, artwork_env):
    dest = artwork_env["dest"]
    _run(test_db, {"destination": str(dest), "dry_run": True, "asset_folders": True, "include": ["logo"]})

    assert not (dest / FOLDER / "logo.png").exists()


def test_job_is_marked_completed_with_a_count(test_db, artwork_env):
    job_id = _job(test_db)
    run_rename_background_job(
        job_id,
        {"destination": str(artwork_env["dest"]), "dry_run": False, "asset_folders": True,
         "include": ["logo", "background"], "run_cleanup": False, "library_setting_key": "asset_renamer_libraries"},
        True,
    )

    job = test_db.query(Job).filter(Job.id == job_id).first()
    assert job.status == "completed"
    # Artwork-only run: the merged Asset Renamer reports just the artwork tally.
    assert job.message == "Asset Renamer complete: 2 artwork organized"


# ---------------------------------------------------------------------------
# The real configs the two entrypoints build
#
# These capture what the /asset-rename endpoint and the workflow actually pass for an
# artwork-only Include, then run the real merged job with them — so the tests can't drift.
# ---------------------------------------------------------------------------


def test_endpoint_config_actually_places_artwork(client, test_db, artwork_env, monkeypatch):
    """The /asset-rename endpoint's config for an artwork-only Include places the files."""
    dest = artwork_env["dest"]
    upsert_setting(test_db, "poster_destination", str(dest))
    upsert_setting(test_db, "poster_asset_folders", "true")
    test_db.add(Setting(key="asset_renamer_include", value=json.dumps(["logo", "background", "squareart"])))
    test_db.commit()

    captured = {}

    def _submit(fn, *args, **kwargs):
        if fn.__name__ == "run_rename_background_job":
            captured["config"] = args[-1]

    monkeypatch.setattr("api.asset_rename.job_queue.submit", _submit)
    assert client.post("/api/posterflow/asset-rename", json={"dry_run": False}).status_code == 200

    config = captured["config"]
    config["run_cleanup"] = False
    run_rename_background_job(_job(test_db), config, True)

    item = dest / FOLDER
    assert (item / "logo.png").is_file()
    assert (item / "background.jpg").is_file()
    assert (item / "square.jpg").is_file()


def test_workflow_step_config_actually_places_artwork(test_db, artwork_env, monkeypatch):
    """Capture the config modules/flow.py builds for its merged rename child, then run it."""
    import modules.flow as flow_module

    dest = artwork_env["dest"]
    upsert_setting(test_db, "poster_destination", str(dest))
    upsert_setting(test_db, "poster_asset_folders", "true")
    upsert_setting(test_db, "poster_action_type", "copy")
    upsert_setting(test_db, "asset_renamer_include", json.dumps(["logo", "background", "squareart"]))
    upsert_setting(test_db, "poster_flow_config", json.dumps({"rename_assets": {"enabled": True, "stop_on_error": True}}))
    test_db.commit()

    captured = {}

    def _promote(db, job, child_id, lo, hi, msg, runner, *args):
        if getattr(runner, "func", runner).__name__ == "run_rename_background_job":
            captured["config"] = args[0]
        from models.job import JOB_STATUS_COMPLETED, update_job_state
        child = db.query(Job).filter(Job.id == child_id).first()
        update_job_state(db, child, status=JOB_STATUS_COMPLETED, progress=100, message="stubbed")

    monkeypatch.setattr(flow_module, "_promote_child_progress_to_parent", _promote)
    monkeypatch.setattr(flow_module, "SessionLocal", lambda: test_db)

    workflow_job = Job(job_type="Poster Workflow", status="pending", progress=0, message="Queued")
    test_db.add(workflow_job)
    test_db.commit()
    test_db.refresh(workflow_job)

    flow_module.run_flow_background_job(workflow_job.id, dry_run=False)

    assert "config" in captured, "workflow never dispatched a merged rename child job"
    # Real workflow runs cleanup at its own tail, so run the child as a workflow child.
    run_rename_background_job(_job(test_db), captured["config"], True, "workflow")

    item = dest / FOLDER
    assert (item / "logo.png").is_file()
    assert (item / "background.jpg").is_file()
    assert (item / "square.jpg").is_file()
