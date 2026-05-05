from unittest.mock import MagicMock, patch

import pytest

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
