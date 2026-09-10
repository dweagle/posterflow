"""IDarr ignore list: bulk import/replace, asset-key aliases, and ignored-entry
matching across type flips."""

import json
from datetime import datetime, timezone

from models.idarr import IdarrAssetCache, IdarrPendingMatch
from models.setting import Setting
from services.idarr_runner import IdarrRunner


def test_import_ignored_titles_bulk_resolves_exact_title_and_year(client, test_db):
    test_db.add_all([
        IdarrAssetCache(
            asset_key="collection::modernsitcomcollection::",
            title="Modern Sitcom Collection",
            year=None,
            asset_type="collection",
            matched=False,
        ),
        IdarrAssetCache(
            asset_key="movie::bloodandorchids::1986",
            title="Blood & Orchids",
            year=1986,
            asset_type="movie",
            matched=False,
        ),
    ])
    test_db.commit()

    response = client.post(
        "/api/idarr/ignored-titles/import",
        json={"titles": ["Modern Sitcom Collection", "Blood & Orchids (1986)"], "sync_target_index": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["added"] == 2
    assert data["total"] == 2

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    keys = {item["asset_key"] for item in ignored}
    assert "collection::modernsitcomcollection::" in keys
    assert "movie::bloodandorchids::1986" in keys


def test_replace_ignored_titles_bulk_overwrites_existing_entries(client, test_db):
    test_db.add(
        Setting(
            key="maker_tools_idarr_ignored_titles",
            value=json.dumps([
                {
                    "asset_key": "collection::legacy::",
                    "title": "Legacy Collection",
                    "year": None,
                    "type": "collection",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]),
        )
    )
    test_db.commit()

    response = client.post(
        "/api/idarr/ignored-titles/replace",
        json={"titles": ["Mini-Series Collection"], "sync_target_index": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["total"] == 1

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["title"] == "Mini-Series Collection"
    assert ignored[0]["type"] == "collection"


def test_import_ignored_titles_bulk_unresolved_year_creates_single_entry(client):
    response = client.post(
        "/api/idarr/ignored-titles/import",
        json={"titles": ["Tom and Jerry (1940)"], "sync_target_index": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["added"] == 1

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["title"] == "Tom and Jerry"
    assert ignored[0]["year"] == 1940
    assert ignored[0]["type"] == "movie"


def test_idarr_runner_expand_asset_key_aliases_covers_movie_and_show_variants():
    movie_key = "movie::tomandjerry::1940"
    expanded_movie = IdarrRunner._expand_asset_key_aliases(movie_key)
    assert "movie::tomandjerry::1940" in expanded_movie
    assert "tv_series::tomandjerry::1940" in expanded_movie

    show_key = "tv_series::tomandjerry::1940"
    expanded_show = IdarrRunner._expand_asset_key_aliases(show_key)
    assert "tv_series::tomandjerry::1940" in expanded_show
    assert "movie::tomandjerry::1940" in expanded_show


def test_idarr_runner_expand_asset_key_aliases_pending_covers_all_concrete_types():
    """An ignore entry saved from the pending list is keyed ``pending::`` (type unknown).
    It must expand to every concrete type so the ignore still matches once the same item
    is typed concretely on a later run — otherwise ignored items reappear as unmatched."""
    pending_key = "pending::astarwarsstorycollection::::scope=t2_abc"
    expanded = IdarrRunner._expand_asset_key_aliases(pending_key)
    assert "collection::astarwarsstorycollection::::scope=t2_abc" in expanded
    assert "movie::astarwarsstorycollection::::scope=t2_abc" in expanded
    assert "tv_series::astarwarsstorycollection::::scope=t2_abc" in expanded
    assert "pending::astarwarsstorycollection::::scope=t2_abc" in expanded


def test_idarr_runner_expand_asset_key_aliases_handles_id_keyed_and_collection():
    """ID-keyed ignore entries (copied from matched cache rows) and collection-typed
    entries must expand across concrete types, or a runner type flip un-ignores them."""
    id_key = "movie::tmdb=348"
    expanded_id = IdarrRunner._expand_asset_key_aliases(id_key)
    assert "movie::tmdb=348" in expanded_id
    assert "tv_series::tmdb=348" in expanded_id
    assert "collection::tmdb=348" in expanded_id

    collection_key = "collection::jamesbondcollection::1962"
    expanded_collection = IdarrRunner._expand_asset_key_aliases(collection_key)
    assert "movie::jamesbondcollection::1962" in expanded_collection

    movie_key = "movie::jamesbondcollection::1962"
    expanded_movie = IdarrRunner._expand_asset_key_aliases(movie_key)
    assert "collection::jamesbondcollection::1962" in expanded_movie


def test_idarr_runner_is_ignored_matches_id_keyed_ignore_entry(test_db):
    """A matched item's ignore entry is ID-keyed (movie::tmdb=…). The rename-time
    check must catch it via the asset's ids, not only the title-keyed form."""
    runner = IdarrRunner(test_db)
    ignored_keys = runner._expand_asset_key_aliases("movie::tmdb=348")

    asset = {"type": "movie", "title": "Alien", "year": 1979, "tmdb_id": 348}
    assert runner.is_ignored(asset, ignored_keys) is True

    other = {"type": "movie", "title": "Aliens", "year": 1986, "tmdb_id": 679}
    assert runner.is_ignored(other, ignored_keys) is False


def test_idarr_runner_is_ignored_bridges_collection_type_flip(test_db):
    """An entry stored as movie (old year fallback) must still ignore the file once
    the runner types it as a collection."""
    runner = IdarrRunner(test_db)
    stored_key = runner._asset_key(asset_type="movie", title="James Bond Collection", year=1962)
    ignored_keys = runner._expand_asset_key_aliases(stored_key)

    asset = {"type": "collection", "title": "James Bond Collection", "year": 1962}
    assert runner.is_ignored(asset, ignored_keys) is True


def test_import_ignored_titles_collection_word_with_year_types_collection(client):
    response = client.post(
        "/api/idarr/ignored-titles/import",
        json={"titles": ["James Bond Collection (1962)"], "sync_target_index": 0},
    )

    assert response.status_code == 200
    assert response.json()["added"] == 1

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["type"] == "collection"
    assert ignored[0]["asset_key"].startswith("collection::")
    assert ignored[0]["year"] == 1962


def test_ignored_titles_list_infers_collection_type_for_pending_entries(client):
    """Entries ignored while unmatched are stored with type ``pending``; the list
    must still badge collection-named titles as Collection."""
    response = client.post(
        "/api/idarr/ignored-titles/add",
        json={
            "title": "A Star Wars Story Collection",
            "type": "pending",
            "asset_key": "pending::astarwarsstorycollection::",
            "sync_target_index": 0,
        },
    )
    assert response.status_code == 200

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["type"] == "collection"
    # The key keeps its pending:: form so cache/pending row sync still matches.
    assert ignored[0]["asset_key"] == "pending::astarwarsstorycollection::"


def test_replace_keys_a_manual_line_by_title_and_keeps_row_ids_as_aliases(client, test_db):
    """Edit List used to copy whatever row key matched the line — an id key, or a conflict
    card's per-file key. Stripping the file's tags then orphaned the entry. A line now keys
    by title/year, keeps the row's id key as an alias, and never carries a conflict token."""
    test_db.add_all([
        IdarrAssetCache(
            asset_key="tv_series::tmdb=332219",
            title="The Legend of Zelda",
            year=1989,
            asset_type="tv_series",
            matched=True,
            tmdb_id=332219,
        ),
        IdarrPendingMatch(
            asset_key="tv_series::thelegendofzelda::1989::conflict=fbefaa2c36b8",
            title="The Legend of Zelda",
            year=1989,
            asset_type="tv_series",
        ),
    ])
    test_db.commit()

    response = client.post(
        "/api/idarr/ignored-titles/replace",
        json={"titles": ["The Legend of Zelda (1989)"], "sync_target_index": 0},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["asset_key"] == "tv_series::thelegendofzelda::1989"
    assert ignored[0]["alias_keys"] == ["tv_series::tmdb=332219"]
    assert ignored[0]["type"] == "tv_series"


def test_replace_keeps_the_entry_a_line_already_has(client, test_db):
    """Saving the editor must not re-key or re-stamp lines that are already ignored."""
    stamp = "2026-09-09T22:03:40+00:00"
    test_db.add(
        Setting(
            key="maker_tools_idarr_ignored_titles",
            value=json.dumps([
                {
                    "asset_key": "pending::thelegendofzelda::1989",
                    "title": "The Legend of Zelda",
                    "year": 1989,
                    "type": "pending",
                    "created_at": stamp,
                }
            ]),
        )
    )
    test_db.commit()

    response = client.post(
        "/api/idarr/ignored-titles/replace",
        json={"titles": ["The Legend of Zelda (1989)", "Tom and Jerry (1940)"], "sync_target_index": 0},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    by_title = {item["title"]: item for item in ignored}
    assert by_title["The Legend of Zelda"]["asset_key"] == "pending::thelegendofzelda::1989"
    assert by_title["The Legend of Zelda"]["created_at"] == stamp
    assert by_title["Tom and Jerry"]["asset_key"] == "movie::tomandjerry::1940"


def test_add_from_a_conflict_card_stores_the_base_key(client):
    """The pending-card Ignore button passes the card's key through; a conflict card's key
    carries a per-file token the runner never matches on."""
    response = client.post(
        "/api/idarr/ignored-titles/add",
        json={
            "title": "The Legend of Zelda",
            "year": 1989,
            "type": "tv_series",
            "asset_key": "tv_series::thelegendofzelda::1989::conflict=fbefaa2c36b8",
            "sync_target_index": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["asset_key"] == "tv_series::thelegendofzelda::1989"

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert [item["asset_key"] for item in ignored] == ["tv_series::thelegendofzelda::1989"]


def test_ignored_list_folds_legacy_duplicates_and_migrates_id_keys(client, test_db):
    """Entries written by older builds: a pending-keyed and a conflict-keyed twin of one
    title show as one entry, and an id-keyed entry becomes title-keyed with the id as alias."""
    test_db.add(
        Setting(
            key="maker_tools_idarr_ignored_titles",
            value=json.dumps([
                {"asset_key": "pending::thelegendofzelda::1989", "title": "The Legend of Zelda", "year": 1989, "type": "pending", "created_at": "2026-09-10T20:36:35+00:00"},
                {"asset_key": "tv_series::thelegendofzelda::1989::conflict=fbefaa2c36b8", "title": "The Legend of Zelda", "year": 1989, "type": "tv_series", "created_at": "2026-09-10T20:36:35+00:00"},
                {"asset_key": "tv_series::tmdb=35466", "title": "Ghost Stories", "year": 2000, "type": "tv_series", "created_at": "2026-09-10T20:36:35+00:00"},
            ]),
        )
    )
    test_db.commit()

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    by_title = {item["title"]: item for item in ignored}
    assert len(ignored) == 2
    assert by_title["The Legend of Zelda"]["asset_key"] == "tv_series::thelegendofzelda::1989"
    assert by_title["The Legend of Zelda"]["type"] == "tv_series"
    assert "alias_keys" not in by_title["The Legend of Zelda"]
    assert by_title["Ghost Stories"]["asset_key"] == "tv_series::ghoststories::2000"
    assert by_title["Ghost Stories"]["alias_keys"] == ["tv_series::tmdb=35466"]


def test_remove_matches_any_typed_twin_of_the_title(client, test_db):
    test_db.add(
        Setting(
            key="maker_tools_idarr_ignored_titles",
            value=json.dumps([
                {"asset_key": "pending::thelegendofzelda::1989", "title": "The Legend of Zelda", "year": 1989, "type": "pending"},
                {"asset_key": "movie::tomandjerry::1940", "title": "Tom and Jerry", "year": 1940, "type": "movie"},
            ]),
        )
    )
    test_db.commit()

    response = client.post(
        "/api/idarr/ignored-titles/remove",
        json={"title": "The Legend of Zelda", "year": 1989, "type": "tv_series", "sync_target_index": 0},
    )
    assert response.status_code == 200

    ignored = client.get("/api/idarr/ignored-titles", params={"sync_target_index": 0}).json()["items"]
    assert [item["asset_key"] for item in ignored] == ["movie::tomandjerry::1940"]


def test_idarr_runner_ignored_keys_cover_legacy_id_and_conflict_entries(test_db):
    """A manual entry stored under the tagged file's TMDB id stopped matching once the file
    was renamed without tags: the scan-time check only builds title keys for an untagged
    file. Legacy id/conflict-keyed entries now load as title keys, ids kept as aliases."""
    test_db.add(
        Setting(
            key="maker_tools_idarr_ignored_titles",
            value=json.dumps([
                {"asset_key": "tv_series::tmdb=332219", "title": "The Legend of Zelda", "year": 1989, "type": "tv_series"},
                {"asset_key": "tv_series::ghoststories::2000::conflict=abc123", "title": "Ghost Stories", "year": 2000, "type": "tv_series"},
            ]),
        )
    )
    test_db.commit()

    runner = IdarrRunner(test_db)
    keys = runner._load_ignored_asset_keys()

    assert runner.is_ignored({"type": "movie", "title": "The Legend of Zelda", "year": 1989}, keys) is True
    assert runner.is_ignored({"type": "tv_series", "title": "Super Mario", "year": 1989, "tmdb_id": 332219}, keys) is True
    assert runner.is_ignored({"type": "movie", "title": "Ghost Stories", "year": 2000}, keys) is True
    assert runner.is_ignored({"type": "movie", "title": "Ghost Stories", "year": 2019}, keys) is False


def test_idarr_runner_sync_ignore_clears_conflict_rows_for_an_ignored_title(test_db):
    """Ignoring a title also clears its conflict card instead of needing a separate dismiss."""
    runner = IdarrRunner(test_db)
    conflict_key = "tv_series::thelegendofzelda::1989::conflict=fbefaa2c36b8"
    test_db.add(IdarrPendingMatch(asset_key=conflict_key, title="The Legend of Zelda", year=1989, asset_type="tv_series"))
    test_db.add(
        IdarrAssetCache(
            asset_key=conflict_key,
            title="The Legend of Zelda",
            year=1989,
            asset_type="tv_series",
            matched=False,
            payload_json=json.dumps({"status": "not_found"}),
        )
    )
    test_db.commit()

    stats = runner._sync_ignore_and_pending(runner._expand_asset_key_aliases("pending::thelegendofzelda::1989"))

    assert stats["removed_pending"] == 1
    assert stats["marked_ignored"] == 1
    assert test_db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == conflict_key).first() is None
