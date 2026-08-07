import json
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
        {"uploaded": 2, "artwork": {"scanned": 3, "uploaded": 2, "uploaded_files": 2, "already_current": 1,
                                    "by_type": {"logo": 1, "background": 1}}},
        {"uploaded": 1, "artwork": {"scanned": 2, "uploaded": 1, "uploaded_files": 1, "already_current": 1,
                                    "by_type": {"logo": 1}}},
    ]:
        _accumulate_artwork_stats(aggregated, target_stats)

    # already_current has to survive the merge, or a webhook run that re-applied nothing
    # reports "0 uploaded" with no explanation.
    assert aggregated == {
        "scanned": 5, "uploaded": 3, "uploaded_files": 3, "already_current": 2,
        "by_type": {"logo": 2, "background": 1},
    }


def test_webhook_artwork_accumulator_ignores_targets_without_artwork():
    from modules.upload import _accumulate_artwork_stats

    aggregated = {"scanned": 0, "uploaded": 0, "by_type": {}}
    _accumulate_artwork_stats(aggregated, {"uploaded": 4})           # artwork disabled
    _accumulate_artwork_stats(aggregated, {"uploaded": 1, "artwork": {}})
    assert aggregated == {"scanned": 0, "uploaded": 0, "by_type": {}}


def test_webhook_artwork_accumulator_tolerates_a_bare_aggregate():
    """Callers seed their own dict; missing bucket keys must not raise."""
    from modules.upload import _accumulate_artwork_stats

    aggregated = {"scanned": 0, "uploaded": 0, "by_type": {}}
    _accumulate_artwork_stats(aggregated, {"artwork": {"scanned": 1, "uploaded": 0, "already_current": 1}})
    assert aggregated["already_current"] == 1
    assert aggregated["uploaded_files"] == 0


# ---------------------------------------------------------------------------
# Discord embed: artwork and posters must render through one builder
# ---------------------------------------------------------------------------

def _outcome(**kw):
    base = {"scanned": 0, "uploaded_files": 0, "already_current": 0, "awaiting_plex": 0, "errors": 0}
    base.update(kw)
    return base


def test_discord_fields_read_identically_for_posters_and_artwork():
    from modules.upload import _discord_outcome_fields

    stats = _outcome(scanned=10, uploaded_files=2, already_current=8)
    posters = _discord_outcome_fields("Posters", stats, dry_run=False, type_counts=[("Movies", 2)])
    artwork = _discord_outcome_fields("Artwork", stats, dry_run=False, type_counts=[("Logos", 2)])

    assert [f["name"].replace("Posters", "X") for f in posters] == [
        f["name"].replace("Artwork", "X").replace("Logos", "Movies") for f in artwork
    ]


def test_discord_artwork_is_not_pluralised_to_artworks():
    from modules.upload import _discord_outcome_fields

    fields = _discord_outcome_fields(
        "Artwork", _outcome(scanned=5, already_current=5), dry_run=False, type_counts=[],
    )
    assert not any("Artworks" in f["name"] for f in fields)
    assert any(f["name"] == "Artwork already current" for f in fields)


def test_discord_hides_per_type_counts_when_nothing_uploaded():
    """A quiet run showed 'Movies 0 / Shows 0 / Seasons 0 / Collections 0' — pure noise."""
    from modules.upload import _discord_outcome_fields

    quiet = _discord_outcome_fields(
        "Posters", _outcome(scanned=10, already_current=10), dry_run=False,
        type_counts=[("Movies", 0), ("Shows", 0)],
    )
    assert not any(f["name"] in {"Movies", "Shows"} for f in quiet)

    busy = _discord_outcome_fields(
        "Posters", _outcome(scanned=10, uploaded_files=3, already_current=7), dry_run=False,
        type_counts=[("Movies", 3), ("Shows", 0)],
    )
    assert any(f["name"] == "Movies" and f["value"] == "3" for f in busy)


def test_discord_dry_run_says_would_upload():
    from modules.upload import _discord_outcome_fields

    fields = _discord_outcome_fields(
        "Posters", _outcome(scanned=4, uploaded_files=4), dry_run=True, type_counts=[],
    )
    assert fields[0]["name"] == "Posters would upload"


# ---------------------------------------------------------------------------
# Workflow parity: both steps must build from the same embed builder
# ---------------------------------------------------------------------------

def _upload_stats():
    return {
        "scanned": 5715, "uploaded_files": 4, "already_current": 5648, "awaiting_plex": 0, "errors": 0,
        "uploaded": 5, "would_upload": 0,
        "unmatched_reasons": {"not_downloaded": 63},
        "media_upload_counts": {"movies": 3, "shows": 1, "seasons": 1, "collections": 0},
        "artwork": {
            "scanned": 9249, "uploaded": 65, "would_upload": 0, "uploaded_files": 65,
            "already_current": 9020, "errors": 0,
            "unmatched_reasons": {"not_downloaded": 164},
            "by_type": {"logo": 40, "background": 20, "squareart": 5},
        },
    }


def test_plex_upload_embed_reports_both_halves_without_unmatched():
    """Unmatched files are expected and live in the log — the embed stays to what changed."""
    from modules.upload import build_plex_upload_embed

    embed = build_plex_upload_embed(_upload_stats(), dry_run=False)
    blob = " ".join(f'{f["name"]} {f["value"]}' for f in embed["fields"])

    # denominator counts files with a Plex target, so uploaded + current adds up to it
    assert "4 of 5,652" in blob            # 4 uploaded + 5,648 already current
    assert "65 of 9,085" in blob           # 65 uploaded + 9,020 already current
    assert "5,648" in blob and "9,020" in blob   # already current, both halves
    assert "5,715" not in blob and "9,249" not in blob, "raw scan totals include unmatched"
    assert "unmatched" not in blob.lower()
    assert embed["description"] == "5 poster(s) and 65 artwork file(s) uploaded (live)"


def test_plex_upload_embed_survives_stats_without_artwork():
    """Workflow reads persisted stats; artwork may be absent when the toggle is off."""
    from modules.upload import build_plex_upload_embed

    stats = _upload_stats()
    stats.pop("artwork")
    embed = build_plex_upload_embed(stats, dry_run=False)

    assert not any(f["name"].startswith("Artwork") for f in embed["fields"])
    assert "artwork" not in embed["description"]


def test_plex_upload_stats_are_committed_not_just_staged(test_db, monkeypatch):
    """upsert_setting only stages; without a commit the row died with the session and
    the workflow silently fell back to its bare embed."""
    import modules.upload as upload_module
    from models.job import Job
    from models.setting import get_setting_value

    job = Job(job_type="Plex Upload", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    stats = {
        "scanned": 3, "uploaded_files": 1, "already_current": 2, "awaiting_plex": 0, "errors": 0,
        "uploaded": 1, "would_upload": 0, "unmatched_reasons": {},
        "media_upload_counts": {"movies": 1, "shows": 0, "seasons": 0, "collections": 0},
        "library_totals": [{"instance": "Plex", "library": "Movies"}],
    }

    class _FakeService:
        def __init__(self, *a, **k):
            pass

        def run_full_upload(self, *a, **k):
            return {"success": True, "stats": stats, "message": "done"}

    monkeypatch.setattr(upload_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)
    monkeypatch.setattr(upload_module, "PlexUploadService", _FakeService)
    monkeypatch.setattr(upload_module, "send_discord_notification", lambda *a, **k: None)

    upload_module.run_plex_upload_background_job(job.id, dry_run=False, skip_discord=True)

    raw = get_setting_value(test_db, upload_module.SETTING_PLEX_UPLOAD_STATS)
    assert raw, "stats must be persisted even when the notification is suppressed"
    saved = json.loads(raw)
    assert saved["scanned"] == 3 and saved["uploaded_files"] == 1
    assert "library_totals" not in saved      # bulky and unused by the embed


def test_description_says_what_held_when_nothing_uploaded():
    """'0 poster(s) and 0 artwork file(s) uploaded' reads like a failed run; on a settled
    library uploading nothing is the normal outcome."""
    from modules.upload import build_plex_upload_embed

    stats = _upload_stats()
    stats["uploaded"] = 0
    stats["uploaded_files"] = 0
    stats["already_current"] = 5651
    stats["media_upload_counts"] = {"movies": 0, "shows": 0, "seasons": 0, "collections": 0}
    stats["artwork"].update({"uploaded": 0, "uploaded_files": 0, "already_current": 9082})

    embed = build_plex_upload_embed(stats, dry_run=False)
    assert embed["description"] == (
        "Nothing to upload — 5,651 poster(s) and 9,082 artwork file(s) already current (live)"
    )


def test_description_omits_a_half_that_uploaded_nothing():
    from modules.upload import build_plex_upload_embed

    stats = _upload_stats()
    stats["artwork"].update({"uploaded": 0, "uploaded_files": 0})

    embed = build_plex_upload_embed(stats, dry_run=False)
    assert embed["description"] == "5 poster(s) uploaded (live)"


def test_description_uses_would_upload_on_a_dry_run():
    from modules.upload import build_plex_upload_embed

    stats = _upload_stats()
    stats["would_upload"] = 4
    stats["artwork"]["would_upload"] = 0

    embed = build_plex_upload_embed(stats, dry_run=True)
    assert embed["description"] == "4 poster(s) would upload (dry run)"


def test_description_when_there_is_nothing_current_either():
    """Everything unmatched — don't claim files are 'already current' when none are."""
    from modules.upload import build_plex_upload_embed

    embed = build_plex_upload_embed(
        {"scanned": 5, "uploaded_files": 0, "already_current": 0, "errors": 0,
         "unmatched_reasons": {"not_downloaded": 5}},
        dry_run=False,
    )
    assert embed["description"] == "Nothing uploaded (live)"


def test_poster_and_artwork_groups_start_on_their_own_row():
    """Discord packs 3 inline fields per row. Unpadded, the artwork pair rode along on
    the posters' row; each group is padded out so it begins on a fresh one."""
    from modules.upload import build_plex_upload_embed

    stats = _upload_stats()
    stats.update(uploaded=0, uploaded_files=0, already_current=5651)
    stats["artwork"].update(uploaded=0, uploaded_files=0, already_current=9082)

    fields = build_plex_upload_embed(stats, dry_run=False)["fields"]
    names = [f["name"] for f in fields]

    # index // 3 is the row a field lands on
    poster_rows = {i // 3 for i, n in enumerate(names) if n.startswith("Posters ")}
    artwork_rows = {i // 3 for i, n in enumerate(names) if n.startswith("Artwork ")}
    assert poster_rows and artwork_rows
    assert not (poster_rows & artwork_rows), "artwork must not share a row with posters"


def test_per_type_counts_get_their_own_row():
    """Movies/Logos used to fill the third slot of the summary row above them."""
    from modules.upload import build_plex_upload_embed

    fields = build_plex_upload_embed(_upload_stats(), dry_run=False)["fields"]
    names = [f["name"] for f in fields]

    rows_of = lambda pred: {i // 3 for i, n in enumerate(names) if pred(n)}
    summary_rows = rows_of(lambda n: n.startswith(("Posters ", "Artwork ")))
    type_rows = rows_of(lambda n: n in {"Movies", "Shows", "Seasons", "Collections",
                                        "Logos", "Backgrounds", "Squareart"})
    assert type_rows
    assert not (summary_rows & type_rows), "per-type counts must not share a summary row"


def test_zero_per_type_counts_are_dropped():
    """'Seasons: 0, Collections: 0' fills a row and says nothing."""
    from modules.upload import build_plex_upload_embed

    stats = _upload_stats()
    stats["media_upload_counts"] = {"movies": 3, "shows": 0, "seasons": 0, "collections": 0}
    names = [f["name"] for f in build_plex_upload_embed(stats, dry_run=False)["fields"]]

    assert "Movies" in names
    assert "Shows" not in names and "Seasons" not in names and "Collections" not in names


def test_padding_uses_a_value_the_senders_keep():
    """Both senders coerce a falsy value to '-', which would render as a visible dash."""
    from modules.upload import build_plex_upload_embed

    stats = _upload_stats()
    stats.update(uploaded=0, uploaded_files=0)
    stats["artwork"].update(uploaded=0, uploaded_files=0)

    spacers = [f for f in build_plex_upload_embed(stats, dry_run=False)["fields"] if f["name"] == "​"]
    assert spacers
    assert all(f["value"] for f in spacers), "a falsy spacer value becomes a literal '-'"


def test_sender_keeps_every_field_of_a_full_upload_embed():
    """A run that uploaded builds 13 fields; the sender capped at 10 and silently
    dropped Squareart and Mode off the end."""
    from modules.upload import build_plex_upload_embed

    fields = build_plex_upload_embed(_upload_stats(), dry_run=False)["fields"]
    assert len(fields) > 10, "guard is meaningless if the embed is small"

    def mock_get_setting(session, key):
        s = MagicMock()
        s.value = {
            "discord_notifications_enabled": "true",
            "discord_notifications_webhook_url": "https://discord.com/api/webhooks/1/a",
            "discord_notifications_features": json.dumps(
                {"plex_upload": {"enabled": True, "include_summary": True, "include_details": True,
                                 "on_success": True}}
            ),
        }.get(key, "")
        return s

    with patch("services.discord_notifications.get_setting", side_effect=mock_get_setting), \
         patch("services.discord_notifications.requests.post") as post:
        post.return_value = MagicMock(status_code=204)
        send_discord_notification(
            MagicMock(), feature_key="plex_upload", event_type="success",
            title="Plex Upload Completed", fields=fields,
        )

    sent = post.call_args.kwargs["json"]["embeds"][0]["fields"]
    assert len(sent) == len(fields)
    assert sent[-1]["name"] == "Mode"
