import json

from models.setting import Setting
from services.community_reconcile import SETTING_UNMATCHED_STATS, _item_key, _unmatched_keys


def test_item_key_prefers_tmdb_then_tvdb_then_none():
    assert _item_key("series", {"tmdb_id": 6618, "tvdb_id": 83294}) == ("series", "tmdb", 6618)
    assert _item_key("series", {"tmdb_id": None, "tvdb_id": 83294}) == ("series", "tvdb", 83294)
    assert _item_key("series", {"tmdb_id": 0, "tvdb_id": "78435"}) == ("series", "tvdb", 78435)
    assert _item_key("movie", {"title": "Custom"}) is None


def test_unmatched_keys_index_both_ids(test_db):
    stats = {
        "summary": {"movies": {"total": 0}, "series": {"total": 2}, "collections": {"total": 0}},
        "unmatched": {
            "movies": [],
            "series": [
                {"title": "P90X2", "tmdb_id": 498801, "tvdb_id": 394871},
                {"title": "Popeye the Sailor", "tmdb_id": None, "tvdb_id": 78435},
            ],
            "collections": [],
        },
    }
    test_db.add(Setting(key=SETTING_UNMATCHED_STATS, value=json.dumps(stats)))
    test_db.commit()

    keys, scanned = _unmatched_keys(test_db)

    assert scanned == {"series"}
    assert keys == {
        ("series", "tmdb", 498801),
        ("series", "tvdb", 394871),
        ("series", "tvdb", 78435),
    }
    # A TheTVDB-only list item is still outstanding; one that carries a tmdb id compares by that.
    assert _item_key("series", {"tmdb_id": None, "tvdb_id": 78435}) in keys
    assert _item_key("series", {"tmdb_id": None, "tvdb_id": 83294}) not in keys
