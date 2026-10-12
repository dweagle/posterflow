"""Full-run instance scoping: a placed folder that mirrors an Arr instance's media folder only
targets the Plex libraries mapped to that instance."""
import json
from types import SimpleNamespace

from models.setting import upsert_setting
from services import plex_upload as plex_upload_module
from services.plex_upload import PlexUploadService
from util.media_server.types import MediaServerItem
from plex_upload_fakes import _FakePlexItem

HD_FOLDER = "Ahsoka (2023) {tvdb-393187}"
UHD_FOLDER = "Ahsoka (2023) {imdb-tt13622776}"
FOLDER_MAP = {HD_FOLDER.lower(): {"Sonarr"}, UHD_FOLDER.lower(): {"Sonarr 4k"}}
INSTANCE_MAP = {
    "Sonarr": [{"plex_instance": "Plex", "library_key": "2"}, {"plex_instance": "Plex", "library_key": "19"}],
    "Sonarr 4k": [{"plex_instance": "Plex", "library_key": "21"}],
}


def _service(test_db, instance_map=INSTANCE_MAP, folder_map=FOLDER_MAP):
    if instance_map is not None:
        upsert_setting(test_db, PlexUploadService.SETTING_INSTANCE_LIBRARY_MAP, json.dumps(instance_map))
        test_db.commit()
    service = PlexUploadService(test_db)
    service._arr_folder_instances = {k: set(v) for k, v in folder_map.items()}
    return service


def _client():
    return SimpleNamespace(instance_name="Plex", server_id="srv1", server_type="plex")


def _card(rating_key, section, library):
    return MediaServerItem(
        item_id=rating_key, item_type="show", title="Ahsoka", year=2023,
        library_key=f"srv1:/library/sections/{section}", library_name=library, client=_client(),
    )


def _asset(folder):
    return {"path": f"/assets/{folder}/poster.jpg", "media_key": "ahsoka", "asset_type": "main",
            "season_number": None, "folder_year": 2023, "folder_name": folder, "display_name": folder}


def test_scope_keeps_only_the_mirrored_instances_libraries(test_db):
    service = _service(test_db)
    hd, uhd = _card("h1", 2, "TV Shows"), _card("u1", 21, "4k TV Shows")
    assert service._scope_candidates_to_asset(_asset(HD_FOLDER), [hd, uhd]) == [hd]
    assert service._scope_candidates_to_asset(_asset(UHD_FOLDER), [hd, uhd]) == [uhd]
    assert service._instance_scoped_paths == {f"/assets/{HD_FOLDER}/poster.jpg", f"/assets/{UHD_FOLDER}/poster.jpg"}


def test_scope_leaves_unattributed_and_unmapped_assets_alone(test_db):
    service = _service(test_db, instance_map={"Sonarr 4k": INSTANCE_MAP["Sonarr 4k"]})
    hd, uhd = _card("h1", 2, "TV Shows"), _card("u1", 21, "4k TV Shows")
    # Folder mirrors no Arr instance: today's fan-out
    assert service._scope_candidates_to_asset(_asset("Ahsoka (2023)"), [hd, uhd]) == [hd, uhd]
    # Folder mirrors an instance with no mapping: today's fan-out
    assert service._scope_candidates_to_asset(_asset(HD_FOLDER), [hd, uhd]) == [hd, uhd]
    assert service._instance_scoped_paths == set()


def test_scope_accepts_bare_and_full_library_key_forms(test_db):
    service = _service(test_db)
    bare = MediaServerItem(item_id="b", item_type="show", title="Ahsoka", library_key="srv1:2", client=_client())
    full = MediaServerItem(item_id="f", item_type="show", title="Ahsoka", library_key="srv1:/library/sections/21", client=_client())
    assert service._scope_candidates_to_asset(_asset(HD_FOLDER), [bare, full]) == [bare]
    assert service._scope_candidates_to_asset(_asset(UHD_FOLDER), [bare, full]) == [full]


def test_scope_drops_cross_type_id_collisions_without_warning(test_db, monkeypatch):
    """A TV show sharing the movie's TMDB number lives outside Radarr's libraries: dropped, no fallback."""
    service = _service(test_db, instance_map={"Radarr": [{"plex_instance": "Plex", "library_key": "1"}]},
                       folder_map={"alice (2016) {tmdb-241259}": {"Radarr"}})
    warnings = []
    monkeypatch.setattr(plex_upload_module, "log_warning", lambda tag, msg, **kw: warnings.append(msg))
    movie = MediaServerItem(item_id="m", item_type="movie", title="Alice", year=2016, library_key="srv1:/library/sections/1", client=_client())
    show = MediaServerItem(item_id="s", item_type="show", title="Baby Reindeer", year=2024, library_key="srv1:/library/sections/2", client=_client())
    asset = {"path": "/assets/Alice (2016) {tmdb-241259}/poster.jpg", "media_key": "alice", "display_name": "Alice (2016)"}
    assert service._scope_candidate_lists(asset, [movie], [show], []) == ([movie], [], [])
    assert warnings == []
    assert service._instance_scoped_paths == {asset["path"]}


def test_scope_falls_back_only_when_every_list_empties(test_db, monkeypatch):
    service = _service(test_db)
    warnings = []
    monkeypatch.setattr(plex_upload_module, "log_warning", lambda tag, msg, **kw: warnings.append(msg))
    uhd = _card("u1", 21, "4k TV Shows")
    # Sonarr maps to 2 and 19; the only match sits in 21, so the asset keeps its match and warns once
    assert service._scope_candidate_lists(_asset(HD_FOLDER), [], [uhd], []) == ([], [uhd], [])
    assert service._scope_candidate_lists(_asset(HD_FOLDER), [], [uhd], []) == ([], [uhd], [])
    assert len(warnings) == 1
    assert service._instance_scoped_paths == set()


def test_scope_falls_back_with_one_warning_when_map_holds_no_match(test_db, monkeypatch):
    service = _service(test_db, instance_map={"Sonarr": [{"plex_instance": "Plex", "library_key": "99"}]})
    warnings = []
    monkeypatch.setattr(plex_upload_module, "log_warning", lambda tag, msg, **kw: warnings.append(msg))
    hd, uhd = _card("h1", 2, "TV Shows"), _card("u1", 21, "4k TV Shows")
    assert service._scope_candidates_to_asset(_asset(HD_FOLDER), [hd, uhd]) == [hd, uhd]
    assert service._scope_candidates_to_asset(_asset(HD_FOLDER), [hd]) == [hd]
    assert len(warnings) == 1 and "Sonarr" in warnings[0] and "using every matched library" in warnings[0]
    assert service._instance_scoped_paths == set()


def test_availability_build_records_arr_folders_per_instance(test_db, monkeypatch):
    upsert_setting(test_db, PlexUploadService.SETTING_SONARR_INSTANCES, json.dumps([
        {"name": "Sonarr", "url": "http://hd", "api_key": "k1"},
        {"name": "Sonarr 4k", "url": "http://uhd", "api_key": "k2"},
    ]))
    test_db.commit()
    service = PlexUploadService(test_db)
    folders = {"http://hd": f"/data/tv/{HD_FOLDER}", "http://uhd": f"/data/tv4k/{UHD_FOLDER}"}

    class _FakeClient:
        connect_status = True

        def __init__(self, url):
            self.url = url

        def get_parsed_media(self, include_unmonitored=True):
            return [{"title": "Ahsoka", "year": 2023, "folder": folders[self.url], "tvdb_id": 393187,
                     "imdb_id": "tt13622776", "has_episodes": True, "seasons": [{"season_number": 1, "season_has_episodes": True}]}]

    monkeypatch.setattr(plex_upload_module, "create_arr_client", lambda url, key, kind, logger=None: _FakeClient(url))
    service._build_arr_availability_index(media_type_filter="series")
    assert service._arr_folder_instances == {HD_FOLDER.lower(): {"Sonarr"}, UHD_FOLDER.lower(): {"Sonarr 4k"}}


def test_upload_asset_targets_only_the_mirrored_instances_library(test_db):
    service = _service(test_db)
    hd = _FakePlexItem("show", "Ahsoka", 2023, "TV Shows", section_id=2, server_id="srv1", rating_key="h1")
    uhd = _FakePlexItem("show", "Ahsoka", 2023, "4k TV Shows", section_id=21, server_id="srv1", rating_key="u1")
    for item in (hd, uhd):
        item.client = _client()
    index = {"movies": {}, "shows": {"ahsoka": [hd, uhd]}, "collections": {}}

    outcome = service._upload_asset(_asset(HD_FOLDER), index, dry_run=True)
    assert outcome.matched is True
    assert outcome.plex_targets == 1 and outcome.uploaded == 1

    outcome = service._upload_asset(_asset("Ahsoka (2023)"), index, dry_run=True)
    assert outcome.plex_targets == 2 and outcome.uploaded == 2
