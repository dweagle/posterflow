from unittest.mock import MagicMock, patch

from services.discord_notifications import (
    DISCORD_NOTIFICATION_FEATURES,
    _format_title,
    _is_valid_discord_webhook,
    _normalize_features,
    _truncate,
    send_discord_notification,
    send_major_error_notification,
)


# ---------------------------------------------------------------------------
# _is_valid_discord_webhook
# ---------------------------------------------------------------------------


def test_valid_discord_webhook_accepts_discord_url():
    assert _is_valid_discord_webhook("https://discord.com/api/webhooks/123/abc") is True


def test_valid_discord_webhook_accepts_discordapp_url():
    assert _is_valid_discord_webhook("https://discordapp.com/api/webhooks/123/abc") is True


def test_valid_discord_webhook_rejects_empty_string():
    assert _is_valid_discord_webhook("") is False


def test_valid_discord_webhook_rejects_arbitrary_url():
    assert _is_valid_discord_webhook("https://example.com/webhook") is False


def test_valid_discord_webhook_rejects_none():
    assert _is_valid_discord_webhook(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


def test_truncate_leaves_short_strings_unchanged():
    assert _truncate("hello", 10) == "hello"


def test_truncate_cuts_long_strings_with_ellipsis():
    result = _truncate("a" * 20, 10)
    assert len(result) == 10
    assert result.endswith("...")


def test_truncate_exact_length_unchanged():
    assert _truncate("hello", 5) == "hello"


# ---------------------------------------------------------------------------
# _format_title
# ---------------------------------------------------------------------------


def test_format_title_adds_success_prefix_for_completed_title():
    result = _format_title("success", "Sync Completed")
    assert result.startswith("🏁")


def test_format_title_adds_start_prefix_for_starting_title():
    result = _format_title("info", "Starting sync")
    assert result.startswith("🚀")


def test_format_title_adds_error_prefix_for_error_event():
    result = _format_title("error", "Something failed")
    assert result.startswith("❌")


def test_format_title_uses_default_when_title_empty():
    result = _format_title("info", "")
    assert "PosterFlow Notification" in result


def test_format_title_does_not_double_prefix():
    """Already-prefixed titles should not be double-prefixed."""
    first = _format_title("error", "Something failed")
    second = _format_title("error", first)
    assert second == first


# ---------------------------------------------------------------------------
# _normalize_features
# ---------------------------------------------------------------------------


def test_normalize_features_returns_all_keys_when_none():
    result = _normalize_features(None)
    for key in DISCORD_NOTIFICATION_FEATURES:
        assert key in result
        assert result[key]["enabled"] is False


def test_normalize_features_merges_provided_values():
    result = _normalize_features({"sync": {"enabled": True, "on_success": False}})
    assert result["sync"]["enabled"] is True
    assert result["sync"]["on_success"] is False
    # Other features should remain at defaults
    assert result["workflow"]["enabled"] is False


def test_normalize_features_ignores_unknown_keys():
    result = _normalize_features({"unknown_feature": {"enabled": True}})
    assert "unknown_feature" not in result


def test_normalize_features_handles_non_dict_feature_value():
    # A feature set to a non-dict value should be ignored (use defaults)
    result = _normalize_features({"sync": "invalid"})
    assert result["sync"]["enabled"] is False


# ---------------------------------------------------------------------------
# send_discord_notification (integration-style with mocked requests + DB)
# ---------------------------------------------------------------------------


def _make_db_with_config(
    *,
    enabled: bool = True,
    webhook: str = "https://discord.com/api/webhooks/123/abc",
    features_json: str | None = None,
) -> MagicMock:
    """Return a mock DB session whose get_setting calls return appropriate values."""
    import json

    default_features = {
        "sync": {
            "enabled": True,
            "on_success": True,
            "on_error": True,
            "include_summary": True,
            "include_details": True,
        }
    }
    features_value = features_json if features_json is not None else json.dumps(default_features)

    def mock_get_setting(db, key):
        setting = MagicMock()
        if key == "discord_notifications_enabled":
            setting.value = "true" if enabled else "false"
        elif key == "discord_notifications_webhook_url":
            setting.value = webhook
        elif key == "discord_notifications_features":
            setting.value = features_value
        else:
            return None
        return setting

    db = MagicMock()
    with patch("services.discord_notifications.get_setting", side_effect=mock_get_setting):
        return db, mock_get_setting


def test_send_discord_notification_returns_false_when_disabled():
    db = MagicMock()

    def mock_get_setting(session, key):
        s = MagicMock()
        s.value = "false" if key == "discord_notifications_enabled" else ""
        return s

    with patch("services.discord_notifications.get_setting", side_effect=mock_get_setting):
        result = send_discord_notification(
            db,
            feature_key="sync",
            event_type="success",
            title="Sync done",
        )

    assert result is False


def test_send_discord_notification_returns_false_for_invalid_webhook():
    db = MagicMock()

    def mock_get_setting(session, key):
        s = MagicMock()
        if key == "discord_notifications_enabled":
            s.value = "true"
        elif key == "discord_notifications_webhook_url":
            s.value = "https://example.com/bad"
        else:
            s.value = ""
        return s

    with patch("services.discord_notifications.get_setting", side_effect=mock_get_setting):
        result = send_discord_notification(
            db,
            feature_key="sync",
            event_type="success",
            title="Sync done",
        )

    assert result is False


def test_send_discord_notification_posts_to_webhook_and_returns_true():
    import json

    db = MagicMock()
    features = {
        "sync": {
            "enabled": True,
            "on_success": True,
            "on_error": True,
            "include_summary": True,
            "include_details": True,
        }
    }

    def mock_get_setting(session, key):
        s = MagicMock()
        if key == "discord_notifications_enabled":
            s.value = "true"
        elif key == "discord_notifications_webhook_url":
            s.value = "https://discord.com/api/webhooks/123/abc"
        elif key == "discord_notifications_features":
            s.value = json.dumps(features)
        else:
            return None
        return s

    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch("services.discord_notifications.get_setting", side_effect=mock_get_setting):
        with patch("requests.post", return_value=mock_response) as mock_post:
            result = send_discord_notification(
                db,
                feature_key="sync",
                event_type="success",
                title="Sync completed",
                description="10 files synced",
            )

    assert result is True
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
    assert payload["embeds"][0]["title"] is not None


def test_send_discord_notification_returns_false_when_feature_disabled():
    import json

    db = MagicMock()
    features = {"sync": {"enabled": False}}

    def mock_get_setting(session, key):
        s = MagicMock()
        if key == "discord_notifications_enabled":
            s.value = "true"
        elif key == "discord_notifications_webhook_url":
            s.value = "https://discord.com/api/webhooks/123/abc"
        elif key == "discord_notifications_features":
            s.value = json.dumps(features)
        else:
            return None
        return s

    with patch("services.discord_notifications.get_setting", side_effect=mock_get_setting):
        with patch("requests.post") as mock_post:
            result = send_discord_notification(
                db,
                feature_key="sync",
                event_type="success",
                title="Sync done",
            )

    assert result is False
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# send_major_error_notification (delegates to send_discord_notification)
# ---------------------------------------------------------------------------


def test_send_major_error_notification_uses_system_errors_feature():
    import json

    db = MagicMock()
    features = {
        "system_errors": {
            "enabled": True,
            "on_success": True,
            "on_error": True,
            "include_summary": True,
            "include_details": True,
        }
    }

    def mock_get_setting(session, key):
        s = MagicMock()
        if key == "discord_notifications_enabled":
            s.value = "true"
        elif key == "discord_notifications_webhook_url":
            s.value = "https://discord.com/api/webhooks/123/abc"
        elif key == "discord_notifications_features":
            s.value = json.dumps(features)
        else:
            return None
        return s

    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch("services.discord_notifications.get_setting", side_effect=mock_get_setting):
        with patch("requests.post", return_value=mock_response) as mock_post:
            result = send_major_error_notification(
                db,
                source="test_module",
                message="Something went wrong",
                job_id=42,
            )

    assert result is True
    mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# Artwork reporting: artwork must reach Discord everywhere posters do —
# especially artwork-only runs, which used to be completely silent.
# ---------------------------------------------------------------------------

from models.artwork_drive import ArtworkDrive
from models.job import Job, JOB_STATUS_PENDING


def _job(test_db, job_type="Test Job"):
    job = Job(job_type=job_type, status=JOB_STATUS_PENDING, progress=0, message="")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def _fake_artwork_service(result):
    class _Svc:
        def __init__(self, db):
            pass

        def sync_multiple_drives(self, *args, **kwargs):
            return result

    return _Svc


def test_artwork_sync_all_success_reports_same_fields_as_posters(test_db, monkeypatch):
    import modules.sync as sync_module
    import services.artwork_sync as art_sync

    test_db.add(ArtworkDrive(name="Art", drive_id="a1", subscribed=True))
    test_db.commit()
    job = _job(test_db, "Artwork Sync All")

    monkeypatch.setattr(art_sync, "ArtworkSyncService", _fake_artwork_service(
        {"success": True, "drives_synced": 2, "added": 5, "updated": 3, "deleted": 1}))
    sent: list = []
    monkeypatch.setattr(sync_module, "send_discord_notification", lambda *a, **k: sent.append(k))

    sync_module._sync_all_artwork_drives(test_db, job.id)

    assert len(sent) == 1
    note = sent[0]
    assert note["title"] == "Artwork Drive Sync Summary"
    values = {f["name"]: f["value"] for f in note["fields"]}
    assert values == {"New": "5", "Replaced": "3", "Deleted": "1"}


def test_artwork_sync_all_failure_is_not_silent(test_db, monkeypatch):
    """The regression that mattered: an artwork-only sync failure reported nothing."""
    import modules.sync as sync_module
    import services.artwork_sync as art_sync

    test_db.add(ArtworkDrive(name="Art", drive_id="a1", subscribed=True))
    test_db.commit()
    job = _job(test_db, "Artwork Sync All")

    monkeypatch.setattr(art_sync, "ArtworkSyncService", _fake_artwork_service(
        {"success": False, "error": "rclone exploded"}))
    sent: list = []
    errors: list = []
    monkeypatch.setattr(sync_module, "send_discord_notification", lambda *a, **k: sent.append(k))
    monkeypatch.setattr(sync_module, "send_major_error_notification", lambda *a, **k: errors.append(k))

    sync_module._sync_all_artwork_drives(test_db, job.id)

    assert any(n["title"] == "Artwork Drive Sync Failed" for n in sent)
    assert any("rclone exploded" in str(n.get("description", "")) for n in sent)
    assert errors and errors[0]["source"] == "sync.all.artwork"


def test_artwork_sync_all_respects_skip_discord(test_db, monkeypatch):
    """Inside a workflow the embed reports it, so the sub-module must stay quiet."""
    import modules.sync as sync_module
    import services.artwork_sync as art_sync

    test_db.add(ArtworkDrive(name="Art", drive_id="a1", subscribed=True))
    test_db.commit()
    job = _job(test_db, "Artwork Sync All")

    monkeypatch.setattr(art_sync, "ArtworkSyncService", _fake_artwork_service({"success": True, "drives_synced": 1}))
    sent: list = []
    monkeypatch.setattr(sync_module, "send_discord_notification", lambda *a, **k: sent.append(k))

    sync_module._sync_all_artwork_drives(test_db, job.id, skip_discord=True)

    assert sent == []


def _unmatched_result(with_posters=True, with_artwork=True):
    result = {}
    if with_posters:
        result["summary"] = {
            "grand_total": {"unmatched": 3, "total": 10, "percent_complete": 70.0},
            "movies": {"unmatched": 2}, "series": {"unmatched": 1},
            "seasons": {"unmatched": 0}, "collections": {"unmatched": 0},
        }
        result["unmatched"] = {}
    if with_artwork:
        result["artwork"] = {
            "logo": {"summary": {"grand_total": {"unmatched": 4, "total": 10}}},
            "background": {"summary": {"grand_total": {"unmatched": 6, "total": 10}}},
        }
    return result


def _patch_unmatched(monkeypatch, result):
    import modules.unmatched as um

    class _Svc:
        def __init__(self, db):
            pass

        def detect_unmatched(self, *args, **kwargs):
            return result

    monkeypatch.setattr(um, "UnmatchedAssetsService", _Svc)
    monkeypatch.setattr(um, "reconcile_community_lists", lambda *a, **k: None)
    sent: list = []
    monkeypatch.setattr(um, "send_discord_notification", lambda *a, **k: sent.append(k))
    return um, sent


def test_unmatched_notification_includes_artwork_per_type(test_db, monkeypatch):
    um, sent = _patch_unmatched(monkeypatch, _unmatched_result())
    job = _job(test_db, "Unmatched Detection")

    um._run_detection(test_db, job, job.id, {}, "/tmp/dest", False,
                      check_posters=True, artwork_types=["logo", "background"], asset_folders=True)

    assert len(sent) == 1
    note = sent[0]
    names = [f["name"] for f in note["fields"]]
    # Poster fields preserved...
    for expected in ("Total", "Movies", "Shows", "Seasons", "Collections"):
        assert expected in names
    # ...plus one field per artwork type.
    assert "Logo" in names and "Background" in names
    values = {f["name"]: f["value"] for f in note["fields"]}
    assert values["Logo"] == "4 missing of 10"
    assert "missing posters" in note["description"] and "missing artwork" in note["description"]


def test_unmatched_artwork_only_run_still_notifies(test_db, monkeypatch):
    """Artwork-only detection used to send nothing at all."""
    um, sent = _patch_unmatched(monkeypatch, _unmatched_result(with_posters=False))
    job = _job(test_db, "Unmatched Detection")

    um._run_detection(test_db, job, job.id, {}, "/tmp/dest", False,
                      check_posters=False, artwork_types=["logo", "background"], asset_folders=True)

    assert len(sent) == 1
    names = [f["name"] for f in sent[0]["fields"]]
    assert names == ["Logo", "Background"]
    assert "missing artwork" in sent[0]["description"]


def test_webhook_accumulates_artwork_across_targets():
    """Artwork is nested, so the webhook's flat-int aggregation loop skipped it entirely —
    this exercises the real accumulator that now sums it per target."""
    from modules.upload import _accumulate_artwork_stats

    aggregated = {"scanned": 0, "uploaded": 0, "by_type": {}}
    for target_stats in [
        {"uploaded": 2, "artwork": {"scanned": 3, "uploaded": 2, "by_type": {"logo": 1, "background": 1}}},
        {"uploaded": 1, "artwork": {"scanned": 2, "uploaded": 1, "by_type": {"logo": 1}}},
    ]:
        _accumulate_artwork_stats(aggregated, target_stats)

    assert aggregated == {"scanned": 5, "uploaded": 3, "by_type": {"logo": 2, "background": 1}}


def test_webhook_artwork_accumulator_ignores_targets_without_artwork():
    from modules.upload import _accumulate_artwork_stats

    aggregated = {"scanned": 0, "uploaded": 0, "by_type": {}}
    _accumulate_artwork_stats(aggregated, {"uploaded": 4})           # artwork disabled
    _accumulate_artwork_stats(aggregated, {"uploaded": 1, "artwork": {}})
    assert aggregated == {"scanned": 0, "uploaded": 0, "by_type": {}}
