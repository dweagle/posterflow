import json
from unittest.mock import MagicMock, patch

from apprise.result import AppriseResultStatus

from api.settings import MASKED_VALUE
from models.setting import Setting, get_setting
from services import apprise_notifications as apn
from services.apprise_notifications import (
    _build_body,
    invalid_apprise_url_lines,
    parse_apprise_entries,
    parse_apprise_urls,
    send_apprise_notification,
    send_apprise_workflow_summary,
)
from services.discord_notifications import DISCORD_NOTIFICATION_FEATURES
from services.notifications import send_major_error_notification, send_notification


# ---------------------------------------------------------------------------
# URL parsing and validation
# ---------------------------------------------------------------------------


def test_parse_apprise_urls_skips_blank_lines_and_comments():
    raw = "\n# home\n json://a/x \n\nntfy://topic\n"
    assert parse_apprise_urls(raw) == ["json://a/x", "ntfy://topic"]


def test_invalid_apprise_url_lines_reports_real_line_numbers():
    raw = "# comment\njson://a/x\nnot a url\n\nntfy://topic\njson://a/x json://b/y"
    assert invalid_apprise_url_lines(raw) == [3, 6]


def test_parse_apprise_entries_reads_tag_prefixes():
    raw = "home, urgent = pover://u@t\njson://a/x?x=1\nwork=mailto://user:p=ss@host"
    assert parse_apprise_entries(raw) == [
        ("pover://u@t", ["home", "urgent"]),
        ("json://a/x?x=1", []),
        ("mailto://user:p=ss@host", ["work"]),
    ]
    assert parse_apprise_urls(raw) == ["pover://u@t", "json://a/x?x=1", "mailto://user:p=ss@host"]


def test_invalid_apprise_url_lines_validates_the_url_behind_a_tag():
    assert invalid_apprise_url_lines("home = nope\nhome = json://a/x") == [1]


def test_invalid_apprise_url_lines_accepts_a_native_discord_webhook():
    assert invalid_apprise_url_lines("https://discord.com/api/webhooks/123456789/abcdefghijklmnop") == []


# ---------------------------------------------------------------------------
# body rendering
# ---------------------------------------------------------------------------


def test_build_body_joins_inline_fields_on_one_line():
    body = _build_body(
        "MyDrive",
        [
            {"name": "New", "value": "3", "inline": True},
            {"name": "Replaced", "value": "1", "inline": True},
            {"name": "Error", "value": "boom"},
        ],
        include_summary=True,
        include_details=True,
    )
    assert body == "MyDrive\n\nNew: 3 | Replaced: 1\nError: boom"


def test_build_body_puts_multiline_values_under_their_name():
    body = _build_body("", [{"name": "Movies", "value": "A\nB", "inline": True}], include_summary=True, include_details=True)
    assert body == "Movies:\nA\nB"


def test_build_body_honours_include_flags():
    fields = [{"name": "New", "value": "3", "inline": True}]
    assert _build_body("MyDrive", fields, include_summary=False, include_details=True) == "New: 3"
    assert _build_body("MyDrive", fields, include_summary=True, include_details=False) == "MyDrive"


# ---------------------------------------------------------------------------
# send_apprise_notification gating
# ---------------------------------------------------------------------------


def _settings(enabled="true", urls="json://global/x", features=None):
    def mock_get_setting(session, key):
        s = MagicMock()
        if key == "apprise_notifications_enabled":
            s.value = enabled
        elif key == "apprise_notifications_urls":
            s.value = urls
        elif key == "apprise_notifications_features":
            s.value = json.dumps(features or {})
        else:
            return None
        return s

    return mock_get_setting


def test_send_apprise_notification_returns_false_when_disabled():
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(enabled="false", features={"sync": {"enabled": True}})), \
         patch("services.apprise_notifications._notify") as notify:
        assert send_apprise_notification(MagicMock(), feature_key="sync", event_type="success", title="Sync done") is False
    notify.assert_not_called()


def test_send_apprise_notification_returns_false_when_feature_disabled():
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(features={"sync": {"enabled": False}})), \
         patch("services.apprise_notifications._notify") as notify:
        assert send_apprise_notification(MagicMock(), feature_key="sync", event_type="success", title="Sync done") is False
    notify.assert_not_called()


def test_send_apprise_notification_respects_event_toggles():
    features = {"sync": {"enabled": True, "on_success": False}}
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(features=features)), \
         patch("services.apprise_notifications._notify", return_value=True) as notify:
        assert send_apprise_notification(MagicMock(), feature_key="sync", event_type="success", title="Sync done") is False
        assert send_apprise_notification(MagicMock(), feature_key="sync", event_type="error", title="Sync failed") is True
    notify.assert_called_once()
    assert notify.call_args.kwargs["event_type"] == "error"


def test_send_apprise_notification_prefers_feature_urls_over_global():
    features = {"sync": {"enabled": True, "urls": "json://feature/x"}}
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(features=features)), \
         patch("services.apprise_notifications._notify", return_value=True) as notify:
        sent = send_apprise_notification(
            MagicMock(),
            feature_key="sync",
            event_type="success",
            title="Sync done",
            description="MyDrive",
            fields=[{"name": "New", "value": "3", "inline": True}],
        )
    assert sent is True
    args, kwargs = notify.call_args
    assert args[0] == [("json://feature/x", [])]
    assert kwargs["title"] == "Sync done"
    assert kwargs["body"] == "MyDrive\n\nNew: 3"
    assert kwargs["context"] == "sync:success"
    assert kwargs["tags"] == []


def test_send_apprise_notification_passes_feature_tags():
    features = {"sync": {"enabled": True, "tags": "urgent, home"}}
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(urls="home = json://a/x", features=features)), \
         patch("services.apprise_notifications._notify", return_value=True) as notify:
        assert send_apprise_notification(MagicMock(), feature_key="sync", event_type="error", title="Sync failed") is True
    assert notify.call_args.args[0] == [("json://a/x", ["home"])]
    assert notify.call_args.kwargs["tags"] == ["urgent", "home"]


def test_send_apprise_notification_falls_back_to_global_urls():
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(features={"sync": {"enabled": True}})), \
         patch("services.apprise_notifications._notify", return_value=True) as notify:
        assert send_apprise_notification(MagicMock(), feature_key="sync", event_type="success", title="Sync done") is True
    assert notify.call_args.args[0] == [("json://global/x", [])]


def test_send_apprise_notification_returns_false_without_urls():
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(urls="", features={"sync": {"enabled": True}})), \
         patch("services.apprise_notifications._notify") as notify:
        assert send_apprise_notification(MagicMock(), feature_key="sync", event_type="success", title="Sync done") is False
    notify.assert_not_called()


# ---------------------------------------------------------------------------
# _notify against a stubbed Apprise result
# ---------------------------------------------------------------------------


class _FakeServiceResult:
    def __init__(self, name, ok):
        self.name = name
        self.url = f"{name.lower()}://masked"
        self._ok = ok

    def __bool__(self):
        return self._ok


class _FakeResult:
    def __init__(self, services, warnings=()):
        self._services = list(services)
        self._warnings = list(warnings)

    def __iter__(self):
        return iter(self._services)

    def __bool__(self):
        return all(bool(s) for s in self._services)

    @property
    def success_count(self):
        return sum(1 for s in self._services if s)

    @property
    def status(self):
        return AppriseResultStatus.SUCCESS if self else AppriseResultStatus.FAILURE

    def logs(self):
        for message in self._warnings:
            entry = MagicMock()
            entry.level = "WARNING"
            entry.message = message
            yield entry


def test_notify_reports_partial_success_and_relays_warnings():
    fake = _FakeResult([_FakeServiceResult("JSON", True), _FakeServiceResult("Discord", False)], warnings=["Discord said no"])
    with patch.object(apn.apprise.Apprise, "notify", return_value=fake) as notify, \
         patch("services.apprise_notifications.log_warning") as warn:
        assert apn._notify([("json://a/x", []), ("discord://123456789/abcdefghij", [])], title="t", body="b", event_type="error", context="sync:error") is True
    kwargs = notify.call_args.kwargs
    assert kwargs["notify_type"] == apn.apprise.NotifyType.FAILURE
    assert kwargs["body_format"] == apn.apprise.NotifyFormat.TEXT
    assert kwargs["timeout"] == apn.NOTIFY_TIMEOUT_SECONDS
    messages = [call.args[1] for call in warn.call_args_list]
    assert any("Discord said no" in m for m in messages)
    assert any("Discord failed" in m for m in messages)


def test_notify_uses_title_as_body_when_body_is_empty():
    fake = _FakeResult([_FakeServiceResult("JSON", True)])
    with patch.object(apn.apprise.Apprise, "notify", return_value=fake) as notify:
        assert apn._notify([("json://a/x", [])], title="Only title", body="", event_type="info", context="x") is True
    assert notify.call_args.kwargs["body"] == "Only title"
    assert notify.call_args.kwargs["notify_type"] == apn.apprise.NotifyType.INFO


def test_notify_returns_false_when_no_url_parses():
    with patch.object(apn.apprise.Apprise, "notify") as notify:
        assert apn._notify([("not a url", [])], title="t", body="b", event_type="info", context="x") is False
    notify.assert_not_called()


def test_notify_tags_services_and_filters_by_feature_tags():
    fake = _FakeResult([_FakeServiceResult("JSON", True)])
    with patch.object(apn.apprise.Apprise, "notify", autospec=True, return_value=fake) as notify:
        assert apn._notify([("json://a/x", ["home"]), ("json://b/y", [])], title="t", body="b", event_type="info", context="x", tags=["home"]) is True
    client = notify.call_args.args[0]
    assert [sorted(service.tags) for service in client] == [["home"], []]
    assert notify.call_args.kwargs["tag"] == ["home"]


def test_notify_reaches_every_service_without_feature_tags():
    fake = _FakeResult([_FakeServiceResult("JSON", True)])
    with patch.object(apn.apprise.Apprise, "notify", return_value=fake) as notify:
        assert apn._notify([("json://a/x", ["home"])], title="t", body="b", event_type="info", context="x") is True
    assert notify.call_args.kwargs["tag"] == "all"


def test_notify_warns_when_no_url_carries_the_feature_tags():
    with patch("services.apprise_notifications.log_warning") as warn:
        assert apn._notify([("json://localhost:1/x", ["home"])], title="t", body="b", event_type="info", context="x", tags=["other"]) is False
    assert any("carries the tags other" in call.args[1] for call in warn.call_args_list)


def test_notify_relays_apprise_log_lines_even_with_propagation_off():
    # the module silences the stdlib "apprise" logger; result.logs() must still carry the reason
    outcome = apn.send_apprise_test_notification(["json://localhost:1/x?cto=1&rto=1"], title="t", body="b")
    assert outcome["results"][0]["ok"] is False
    assert outcome["warnings"]


# ---------------------------------------------------------------------------
# workflow summary
# ---------------------------------------------------------------------------


def test_workflow_summary_builds_sections_and_flags_errors():
    features = {"workflow": {"enabled": True}}
    embeds = [
        {"event_type": "success", "title": "Poster Sync Completed", "description": "MyDrive", "fields": [{"name": "New", "value": "3", "inline": True}]},
        {"event_type": "error", "title": "Renamer Failed", "description": "boom"},
    ]
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(features=features)), \
         patch("services.apprise_notifications._notify", return_value=True) as notify:
        assert send_apprise_workflow_summary(MagicMock(), embeds=embeds) is True
    kwargs = notify.call_args.kwargs
    assert kwargs["event_type"] == "error"
    assert kwargs["title"] == "PosterFlow Workflow Summary"
    assert "Poster Sync Completed\nMyDrive\n\nNew: 3" in kwargs["body"]
    assert "Renamer Failed\nboom" in kwargs["body"]


def test_workflow_summary_skips_success_specs_when_disabled():
    features = {"workflow": {"enabled": True, "on_success": False}}
    with patch("services.apprise_notifications.get_setting", side_effect=_settings(features=features)), \
         patch("services.apprise_notifications._notify") as notify:
        assert send_apprise_workflow_summary(MagicMock(), embeds=[{"event_type": "success", "title": "A"}]) is False
    notify.assert_not_called()


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


def test_send_notification_fans_out_to_both_channels():
    db = MagicMock()
    with patch("services.notifications.send_discord_notification", return_value=False) as discord, \
         patch("services.notifications.send_apprise_notification", return_value=True) as apprise_send:
        assert send_notification(db, feature_key="sync", event_type="success", title="t", description="d", fields=[], color=1) is True
    discord.assert_called_once()
    apprise_send.assert_called_once()
    assert discord.call_args.kwargs["color"] == 1
    assert apprise_send.call_args.kwargs["feature_key"] == "sync"
    assert "color" not in apprise_send.call_args.kwargs


def test_send_major_error_notification_uses_system_errors_feature():
    with patch("services.notifications.send_discord_notification", return_value=True) as discord, \
         patch("services.notifications.send_apprise_notification", return_value=False):
        assert send_major_error_notification(MagicMock(), source="sync.one", message="boom", job_id=7) is True
    kwargs = discord.call_args.kwargs
    assert kwargs["feature_key"] == "system_errors"
    assert kwargs["event_type"] == "error"
    assert kwargs["description"] == "boom"
    assert {"name": "Job ID", "value": "7", "inline": True} in kwargs["fields"]


# ---------------------------------------------------------------------------
# settings API
# ---------------------------------------------------------------------------


def _features_payload(**overrides):
    return {
        key: {
            "enabled": False,
            "on_success": True,
            "on_error": True,
            "include_summary": True,
            "include_details": True,
            "urls": "",
            "tags": "",
            **overrides.get(key, {}),
        }
        for key in DISCORD_NOTIFICATION_FEATURES
    }


def test_get_apprise_config_masks_every_url_list(client, test_db):
    test_db.add_all([
        Setting(key="apprise_notifications_enabled", value="true"),
        Setting(key="apprise_notifications_urls", value="json://a/x"),
        Setting(key="apprise_notifications_features", value=json.dumps({"sync": {"enabled": True, "urls": "json://b/y"}})),
    ])
    test_db.commit()

    data = client.get("/api/settings/notifications/apprise").json()
    assert data["enabled"] is True
    assert data["urls"] == MASKED_VALUE
    assert data["features"]["sync"]["urls"] == MASKED_VALUE
    assert data["features"]["sync"]["enabled"] is True
    assert data["features"]["workflow"]["urls"] == ""


def test_save_apprise_config_persists_and_keeps_comments(client, test_db):
    payload = {"enabled": True, "urls": "# home\nhome, urgent = json://a/x\n", "features": _features_payload(sync={"enabled": True, "urls": "urgent = ntfy://topic", "tags": " urgent "})}
    response = client.post("/api/settings/notifications/apprise", json=payload)
    assert response.status_code == 200, response.text
    assert get_setting(test_db, "apprise_notifications_enabled").value == "true"
    assert get_setting(test_db, "apprise_notifications_urls").value == "# home\nhome, urgent = json://a/x"
    stored = json.loads(get_setting(test_db, "apprise_notifications_features").value)
    assert stored["sync"]["urls"] == "urgent = ntfy://topic"
    assert stored["sync"]["tags"] == "urgent"
    assert stored["sync"]["enabled"] is True
    assert client.get("/api/settings/notifications/apprise").json()["features"]["sync"]["tags"] == "urgent"


def test_save_apprise_config_rejects_bad_url_with_line_number(client):
    payload = {"enabled": True, "urls": "json://a/x\nnope", "features": _features_payload()}
    response = client.post("/api/settings/notifications/apprise", json=payload)
    assert response.status_code == 400
    assert "line 2" in response.json()["detail"]


def test_save_apprise_config_rejects_bad_feature_url(client):
    payload = {"enabled": False, "urls": "", "features": _features_payload(idarr={"urls": "nope"})}
    response = client.post("/api/settings/notifications/apprise", json=payload)
    assert response.status_code == 400
    assert "idarr" in response.json()["detail"]


def test_save_apprise_config_rejects_tags_no_url_carries(client):
    payload = {"enabled": True, "urls": "home = json://a/x", "features": _features_payload(sync={"enabled": True, "tags": "urgent"})}
    response = client.post("/api/settings/notifications/apprise", json=payload)
    assert response.status_code == 400
    assert "sync" in response.json()["detail"]
    assert "urgent" in response.json()["detail"]


def test_save_apprise_config_accepts_tags_carried_by_a_url_or_always(client):
    base = _features_payload(sync={"enabled": True, "tags": "urgent"}, idarr={"enabled": False, "tags": "stale"})
    for urls in ("urgent = json://a/x", "always = json://a/x", "home = json://a/x\nurgent, home = ntfy://topic"):
        response = client.post("/api/settings/notifications/apprise", json={"enabled": True, "urls": urls, "features": base})
        assert response.status_code == 200, (urls, response.text)


def test_save_apprise_config_requires_a_url_when_enabled(client):
    payload = {"enabled": True, "urls": "# nothing here\n", "features": _features_payload()}
    response = client.post("/api/settings/notifications/apprise", json=payload)
    assert response.status_code == 400
    assert "at least one" in response.json()["detail"]


def test_save_apprise_config_keeps_secrets_when_mask_is_echoed(client, test_db):
    test_db.add_all([
        Setting(key="apprise_notifications_urls", value="json://a/x"),
        Setting(key="apprise_notifications_features", value=json.dumps({"sync": {"enabled": True, "urls": "ntfy://topic"}})),
    ])
    test_db.commit()

    payload = {"enabled": True, "urls": MASKED_VALUE, "features": _features_payload(sync={"enabled": True, "urls": MASKED_VALUE})}
    response = client.post("/api/settings/notifications/apprise", json=payload)
    assert response.status_code == 200, response.text
    assert get_setting(test_db, "apprise_notifications_urls").value == "json://a/x"
    assert json.loads(get_setting(test_db, "apprise_notifications_features").value)["sync"]["urls"] == "ntfy://topic"


def test_get_settings_masks_nested_feature_secrets(client, test_db):
    test_db.add_all([
        Setting(key="apprise_notifications_features", value=json.dumps({"sync": {"urls": "json://b/y"}})),
        Setting(key="discord_notifications_features", value=json.dumps({"sync": {"webhook_url": "https://discord.com/api/webhooks/1/abc"}})),
    ])
    test_db.commit()

    data = client.get("/api/settings/").json()
    assert json.loads(data["apprise_notifications_features"])["sync"]["urls"] == MASKED_VALUE
    assert json.loads(data["discord_notifications_features"])["sync"]["webhook_url"] == MASKED_VALUE


def test_reveal_feature_apprise_urls(client, test_db):
    test_db.add(Setting(key="apprise_notifications_features", value=json.dumps({"sync": {"urls": "json://b/y"}})))
    test_db.commit()

    response = client.post(
        "/api/settings/reveal",
        json={"setting_key": "apprise_notifications_features", "field": "urls", "instance_name": "sync"},
    )
    assert response.status_code == 200
    assert response.json()["value"] == "json://b/y"


def test_apprise_test_endpoint_reports_partial_delivery(client):
    outcome = {
        "results": [
            {"name": "JSON", "url": "json://a/x", "ok": True},
            {"name": "Ntfy", "url": "ntfy://t", "ok": False},
        ],
        "warnings": ["ntfy refused the topic"],
    }
    with patch("api.settings.send_apprise_test_notification", return_value=outcome) as send:
        response = client.post(
            "/api/settings/notifications/apprise/test",
            json={"enabled": True, "urls": "json://a/x\nntfy://topic", "features": _features_payload()},
        )
    assert response.status_code == 200, response.text
    assert send.call_args.args[0] == ["json://a/x", "ntfy://topic"]
    message = response.json()["message"]
    assert "1 of 2" in message
    assert "Ntfy" in message
    assert "ntfy refused the topic" in message


def test_apprise_test_endpoint_fails_when_every_service_fails(client):
    outcome = {"results": [{"name": "JSON", "url": "json://a/x", "ok": False}], "warnings": ["connection refused"]}
    with patch("api.settings.send_apprise_test_notification", return_value=outcome):
        response = client.post(
            "/api/settings/notifications/apprise/test",
            json={"enabled": True, "urls": "json://a/x", "features": _features_payload()},
        )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "JSON" in detail
    assert "connection refused" in detail


def test_apprise_test_endpoint_requires_urls(client):
    response = client.post(
        "/api/settings/notifications/apprise/test",
        json={"enabled": True, "urls": "", "features": _features_payload()},
    )
    assert response.status_code == 400
