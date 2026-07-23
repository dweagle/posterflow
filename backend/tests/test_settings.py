import json

from core.config import settings as app_settings
from models.setting import Setting
from api.settings import MASKED_VALUE

def test_save_bulk_settings(client, test_db):
    """Test saving multiple settings at once"""
    settings_data = {
        "google_client_id": "test-client-id",
        "google_client_secret": "test-secret",
        "poster_destination": "remote:posters"
    }
    
    response = client.post("/api/settings/bulk", json=settings_data)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 3
    
    # Verify settings were saved
    saved_setting = test_db.query(Setting).filter(Setting.key == "poster_destination").first()
    assert saved_setting is not None
    assert saved_setting.value == "remote:posters"

def test_save_bulk_border_style_settings_persist(client, test_db):
    """Border style / gradient / inner-effect / season-style keys must be allowlisted
    so the Border tab actually saves (regression: they were silently dropped)."""
    border_settings = {
        "border_replacer_style": "gradient",
        "border_replacer_gradient_colors": json.dumps(["#ffffff", "#000000"]),
        "border_replacer_gradient_direction": "diagonal",
        "border_replacer_overlay_image": "frame.png",
        "border_replacer_overlay_remove_existing": "true",
        "border_replacer_inner_effect": "glow",
        "border_replacer_inner_color": "#123456",
        "border_replacer_inner_opacity": "80",
        "border_replacer_inner_width": "10",
        "border_replacer_fade_width": "12",
        "border_replacer_season_style": "gradient",
        "border_replacer_season_gradient_colors": json.dumps(["#abcdef"]),
        "border_replacer_season_gradient_direction": "horizontal",
        "border_replacer_season_overlay_image": "season.png",
        "border_replacer_season_overlay_remove_existing": "true",
        "border_replacer_season_inner_effect": "fade",
        "border_replacer_season_inner_color": "#654321",
        "border_replacer_season_inner_opacity": "60",
        "border_replacer_season_inner_width": "9",
        "border_replacer_season_fade_width": "7",
    }

    response = client.post("/api/settings/bulk", json=border_settings)
    assert response.status_code == 200
    assert response.json()["count"] == len(border_settings)

    for key, expected in border_settings.items():
        saved = test_db.query(Setting).filter(Setting.key == key).first()
        assert saved is not None, f"{key} was dropped instead of saved"
        assert saved.value == expected


def test_update_existing_setting(client, test_db):
    """Test updating an existing setting"""
    # Create initial setting with an allowlisted key
    setting = Setting(key="poster_destination", value="old:path")
    test_db.add(setting)
    test_db.commit()
    
    # Update it
    response = client.post("/api/settings/bulk", json={"poster_destination": "new:path"})
    assert response.status_code == 200
    
    # Verify update
    test_db.refresh(setting)
    assert setting.value == "new:path"


def test_get_settings_masks_sensitive_values(client, test_db):
    """Sensitive setting keys must be masked in the GET /api/settings/ response."""
    test_db.add_all([
        Setting(key="google_client_secret", value="super-secret"),
        Setting(key="plex_token", value="plex-token-value"),
        Setting(key="custom_api_key", value="custom-key-value"),
        Setting(key="plex_url", value="http://localhost:32400"),
    ])
    test_db.commit()

    response = client.get("/api/settings/")
    assert response.status_code == 200
    data = response.json()
    # Sensitive keys must be replaced with the placeholder
    assert data["google_client_secret"] == MASKED_VALUE
    assert data["plex_token"] == MASKED_VALUE
    # Non-sensitive keys must be returned as-is
    assert data["custom_api_key"] == "custom-key-value"
    assert data["plex_url"] == "http://localhost:32400"


def test_save_bulk_with_masked_placeholder_preserves_original_secret(client, test_db):
    """Posting the masked placeholder for a sensitive key must not overwrite the real value."""
    test_db.add(Setting(key="google_client_secret", value="real-secret"))
    test_db.commit()

    response = client.post(
        "/api/settings/bulk",
        json={
            "google_client_secret": MASKED_VALUE,  # placeholder – must be ignored
            "poster_destination": "remote:posters",
        },
    )
    assert response.status_code == 200

    # The real secret must still be in the DB
    saved_secret = test_db.query(Setting).filter(Setting.key == "google_client_secret").first()
    assert saved_secret is not None
    assert saved_secret.value == "real-secret"

    saved_dest = test_db.query(Setting).filter(Setting.key == "poster_destination").first()
    assert saved_dest is not None
    assert saved_dest.value == "remote:posters"


def test_save_bulk_masked_json_blob_preserves_api_keys(client, test_db):
    """Posting a JSON blob with masked api_key fields must preserve the originals."""
    import json
    original_instances = json.dumps([
        {"name": "Radarr", "url": "http://radarr:7878", "api_key": "real-api-key"},
    ])
    test_db.add(Setting(key="radarr_instances", value=original_instances))
    test_db.commit()

    masked_instances = json.dumps([
        {"name": "Radarr", "url": "http://radarr:7878", "api_key": MASKED_VALUE},
    ])
    response = client.post(
        "/api/settings/bulk",
        json={"radarr_instances": masked_instances},
    )
    assert response.status_code == 200

    saved = test_db.query(Setting).filter(Setting.key == "radarr_instances").first()
    assert saved is not None
    saved_data = json.loads(saved.value)
    assert saved_data[0]["api_key"] == "real-api-key"


def test_save_bulk_settings_updates_secret_value(client, test_db):
    test_db.add(Setting(key="google_client_secret", value="persisted-secret"))
    test_db.commit()

    response = client.post(
        "/api/settings/bulk",
        json={
            "google_client_secret": "updated-secret",
            "poster_destination": "my-drive:posters",
        },
    )
    assert response.status_code == 200

    saved_secret = test_db.query(Setting).filter(Setting.key == "google_client_secret").first()
    assert saved_secret is not None
    assert saved_secret.value == "updated-secret"

    saved_dest = test_db.query(Setting).filter(Setting.key == "poster_destination").first()
    assert saved_dest is not None
    assert saved_dest.value == "my-drive:posters"


def test_reveal_sensitive_json_field_returns_value(client, test_db):
    test_db.add(
        Setting(
            key="plex_instances",
            value=json.dumps([
                {"name": "Plex", "url": "http://plex:32400", "api_key": "real-plex-token"},
            ]),
        )
    )
    test_db.commit()

    response = client.post(
        "/api/settings/reveal",
        json={
            "setting_key": "plex_instances",
            "field": "api_key",
            "instance_name": "Plex",
            "index": 0,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["setting_key"] == "plex_instances"
    assert data["field"] == "api_key"
    assert data["value"] == "real-plex-token"


def test_reveal_blocks_password_hash_key(client, test_db):
    test_db.add(Setting(key="app_password_hash", value="do-not-reveal"))
    test_db.commit()

    response = client.post(
        "/api/settings/reveal",
        json={"setting_key": "app_password_hash"},
    )

    assert response.status_code == 403
    assert "cannot be revealed" in response.json()["detail"]


def test_save_bulk_rejects_unknown_keys(client, test_db):
    """Keys not in BULK_SETTINGS_ALLOWLIST must be silently dropped (not persisted)."""
    response = client.post(
        "/api/settings/bulk",
        json={
            "poster_destination": "remote:posters",
            "totally_made_up_key": "evil-value",
        },
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1

    # Allowed key was saved
    saved = test_db.query(Setting).filter(Setting.key == "poster_destination").first()
    assert saved is not None and saved.value == "remote:posters"

    # Unknown key was NOT persisted
    rejected = test_db.query(Setting).filter(Setting.key == "totally_made_up_key").first()
    assert rejected is None


def test_save_bulk_blocks_password_hash_key(client, test_db):
    """app_password_hash must not be writable via the bulk endpoint."""
    response = client.post(
        "/api/settings/bulk",
        json={"app_password_hash": "hacked"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0

    not_saved = test_db.query(Setting).filter(Setting.key == "app_password_hash").first()
    assert not_saved is None


def test_upload_service_account_json_saves_file_and_setting(client, test_db, tmp_path, monkeypatch):
    """Uploading service account JSON should persist file and google_service_account_file setting."""
    monkeypatch.setattr(app_settings, "config_dir", tmp_path)

    response = client.post(
        "/api/settings/service-account/upload",
        files={"file": ("service-account.json", b'{"type":"service_account"}', "application/json")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Service account JSON uploaded successfully"
    assert payload["path"].endswith(".json")

    saved_path = tmp_path / "service_accounts"
    assert saved_path.exists()
    assert any(saved_path.glob("*.json"))

    setting = test_db.query(Setting).filter(Setting.key == "google_service_account_file").first()
    assert setting is not None
    assert setting.value == payload["path"]


def test_upload_service_account_json_rejects_invalid_json(client, tmp_path, monkeypatch):
    """Upload endpoint should reject non-JSON content."""
    monkeypatch.setattr(app_settings, "config_dir", tmp_path)

    response = client.post(
        "/api/settings/service-account/upload",
        files={"file": ("service-account.json", b"not-json", "application/json")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON file"

def test_setup_status_incomplete(client):
    """Test setup status when not complete"""
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    data = response.json()
    assert data["setup_complete"] is False

def test_setup_status_complete(client, test_db):
    """Test setup status when complete"""
    # Mark setup as complete
    setting = Setting(key="setup_complete", value="true")
    test_db.add(setting)
    test_db.commit()

    response = client.get("/api/setup/status")
    assert response.status_code == 200
    data = response.json()
    assert data["setup_complete"] is True


def _get_setting_json(test_db, key):
    saved = test_db.query(Setting).filter(Setting.key == key).first()
    assert saved is not None, f"{key} missing"
    return json.loads(saved.value)


def test_plex_rename_migrates_name_keyed_settings(client, test_db):
    """Renaming a Plex server (same URL, new name) must migrate every setting keyed by
    the instance name and preserve its masked api_key via the URL fallback."""
    test_db.add_all([
        Setting(key="plex_instances", value=json.dumps([
            {"name": "Old Plex", "url": "http://plex:32400", "api_key": "plex-token"},
        ])),
        Setting(key="plex_library_config", value=json.dumps([
            {"instance_name": "Old Plex", "libraries": [
                {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
                {"key": "2", "title": "TV", "type": "show", "enabled": False},
            ]},
        ])),
        Setting(key="plex_upload_library_override", value=json.dumps({
            "enabled": True,
            "configs": [{"instance_name": "Old Plex", "libraries": [
                {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
            ]}],
        })),
        Setting(key="plex_upload_instance_library_map", value=json.dumps({
            "Radarr": [{"plex_instance": "Old Plex", "library_key": "1"}],
        })),
        Setting(key="poster_renamer_libraries", value=json.dumps(["Old Plex:1", "Old Plex:2"])),
        Setting(key="border_replacer_rule_libraries", value=json.dumps(["Old Plex:1"])),
    ])
    test_db.commit()

    response = client.post("/api/settings/bulk", json={
        "plex_instances": json.dumps([
            {"name": "New Plex", "url": "http://plex:32400", "api_key": MASKED_VALUE},
        ]),
    })
    assert response.status_code == 200

    instances = _get_setting_json(test_db, "plex_instances")
    assert instances[0]["name"] == "New Plex"
    assert instances[0]["api_key"] == "plex-token"

    configs = _get_setting_json(test_db, "plex_library_config")
    assert [c["instance_name"] for c in configs] == ["New Plex"]
    # Selection preserved, not reset
    assert {lib["key"]: lib["enabled"] for lib in configs[0]["libraries"]} == {"1": True, "2": False}

    override = _get_setting_json(test_db, "plex_upload_library_override")
    assert override["enabled"] is True
    assert [c["instance_name"] for c in override["configs"]] == ["New Plex"]

    routing = _get_setting_json(test_db, "plex_upload_instance_library_map")
    assert routing == {"Radarr": [{"plex_instance": "New Plex", "library_key": "1"}]}

    assert _get_setting_json(test_db, "poster_renamer_libraries") == ["New Plex:1", "New Plex:2"]
    assert _get_setting_json(test_db, "border_replacer_rule_libraries") == ["New Plex:1"]


def test_plex_rename_keeps_config_already_saved_under_new_name(client, test_db):
    """If the user already re-saved libraries under the new name (the reported bug state:
    old + new entries side by side), the newer config wins and the stale one is dropped."""
    test_db.add_all([
        Setting(key="plex_instances", value=json.dumps([
            {"name": "Old Plex", "url": "http://plex:32400", "api_key": "plex-token"},
        ])),
        Setting(key="plex_library_config", value=json.dumps([
            {"instance_name": "Old Plex", "libraries": [
                {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
            ]},
            {"instance_name": "New Plex", "libraries": [
                {"key": "1", "title": "Movies", "type": "movie", "enabled": False},
            ]},
        ])),
    ])
    test_db.commit()

    response = client.post("/api/settings/bulk", json={
        "plex_instances": json.dumps([
            {"name": "New Plex", "url": "http://plex:32400", "api_key": "plex-token"},
        ]),
    })
    assert response.status_code == 200

    configs = _get_setting_json(test_db, "plex_library_config")
    assert [c["instance_name"] for c in configs] == ["New Plex"]
    assert configs[0]["libraries"][0]["enabled"] is False


def test_plex_instances_save_prunes_orphaned_library_configs(client, test_db):
    """Saving plex_instances drops library configs for servers that no longer exist,
    but leaves the inert 'name:key' selection lists alone so they revive on re-add."""
    test_db.add_all([
        Setting(key="plex_instances", value=json.dumps([
            {"name": "Plex", "url": "http://plex:32400", "api_key": "plex-token"},
        ])),
        Setting(key="plex_library_config", value=json.dumps([
            {"instance_name": "Plex", "libraries": [
                {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
            ]},
            {"instance_name": "Ghost", "libraries": [
                {"key": "9", "title": "Old", "type": "movie", "enabled": True},
            ]},
        ])),
        Setting(key="poster_renamer_libraries", value=json.dumps(["Ghost:9", "Plex:1"])),
    ])
    test_db.commit()

    response = client.post("/api/settings/bulk", json={
        "plex_instances": json.dumps([
            {"name": "Plex", "url": "http://plex:32400", "api_key": "plex-token"},
        ]),
    })
    assert response.status_code == 200

    configs = _get_setting_json(test_db, "plex_library_config")
    assert [c["instance_name"] for c in configs] == ["Plex"]
    assert _get_setting_json(test_db, "poster_renamer_libraries") == ["Ghost:9", "Plex:1"]


def test_arr_rename_migrates_routing_map_key(client, test_db):
    """Renaming a Radarr/Sonarr instance re-keys its Plex Upload routing row; rows for
    other instances (including the other arr type) are untouched."""
    test_db.add_all([
        Setting(key="radarr_instances", value=json.dumps([
            {"name": "Radarr HD", "url": "http://radarr:7878", "api_key": "radarr-key"},
        ])),
        Setting(key="sonarr_instances", value=json.dumps([
            {"name": "Sonarr", "url": "http://sonarr:8989", "api_key": "sonarr-key"},
        ])),
        Setting(key="plex_upload_instance_library_map", value=json.dumps({
            "Radarr HD": [{"plex_instance": "Plex", "library_key": "1"}],
            "Sonarr": [{"plex_instance": "Plex", "library_key": "2"}],
        })),
    ])
    test_db.commit()

    response = client.post("/api/settings/bulk", json={
        "radarr_instances": json.dumps([
            {"name": "Radarr 1080p", "url": "http://radarr:7878", "api_key": MASKED_VALUE},
        ]),
    })
    assert response.status_code == 200

    # Masked key preserved across the rename via the URL fallback
    instances = _get_setting_json(test_db, "radarr_instances")
    assert instances[0] == {"name": "Radarr 1080p", "url": "http://radarr:7878", "api_key": "radarr-key"}

    routing = _get_setting_json(test_db, "plex_upload_instance_library_map")
    assert routing == {
        "Radarr 1080p": [{"plex_instance": "Plex", "library_key": "1"}],
        "Sonarr": [{"plex_instance": "Plex", "library_key": "2"}],
    }


def test_arr_rename_keeps_row_already_saved_under_new_name(client, test_db):
    """If routing was already re-configured under the new arr name, that row wins."""
    test_db.add_all([
        Setting(key="sonarr_instances", value=json.dumps([
            {"name": "Old Sonarr", "url": "http://sonarr:8989", "api_key": "sonarr-key"},
        ])),
        Setting(key="plex_upload_instance_library_map", value=json.dumps({
            "Old Sonarr": [{"plex_instance": "Plex", "library_key": "1"}],
            "New Sonarr": [{"plex_instance": "Plex", "library_key": "2"}],
        })),
    ])
    test_db.commit()

    response = client.post("/api/settings/bulk", json={
        "sonarr_instances": json.dumps([
            {"name": "New Sonarr", "url": "http://sonarr:8989", "api_key": "sonarr-key"},
        ]),
    })
    assert response.status_code == 200

    routing = _get_setting_json(test_db, "plex_upload_instance_library_map")
    assert routing == {"New Sonarr": [{"plex_instance": "Plex", "library_key": "2"}]}


def test_plex_rename_round_trip_restores_original_state(client, test_db):
    """Rename to a temp name and back again — two saves — must land exactly where it
    started: selections, routing, and the stored token all intact, no leftover entries."""
    original_settings = {
        "plex_instances": [{"name": "MyPlex", "url": "http://plex:32400", "api_key": "plex-token"}],
        "plex_library_config": [{"instance_name": "MyPlex", "libraries": [
            {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
            {"key": "2", "title": "TV", "type": "show", "enabled": False},
        ]}],
        "plex_upload_instance_library_map": {"Radarr": [{"plex_instance": "MyPlex", "library_key": "1"}]},
        "poster_renamer_libraries": ["MyPlex:1"],
    }
    test_db.add_all([Setting(key=k, value=json.dumps(v)) for k, v in original_settings.items()])
    test_db.commit()

    for name in ("test", "MyPlex"):
        response = client.post("/api/settings/bulk", json={
            "plex_instances": json.dumps([
                {"name": name, "url": "http://plex:32400", "api_key": MASKED_VALUE},
            ]),
        })
        assert response.status_code == 200

    for key, expected in original_settings.items():
        assert _get_setting_json(test_db, key) == expected, f"{key} did not round-trip"


def test_startup_cleanup_prunes_orphans_without_a_save(test_db):
    """Installs already carrying orphaned rename leftovers are healed at startup,
    with no Media Servers save required."""
    from api.settings import cleanup_stale_plex_name_keys

    test_db.add_all([
        Setting(key="plex_instances", value=json.dumps([
            {"name": "New Plex", "url": "http://plex:32400", "api_key": "plex-token"},
        ])),
        Setting(key="plex_library_config", value=json.dumps([
            {"instance_name": "Old Plex", "libraries": [
                {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
            ]},
            {"instance_name": "New Plex", "libraries": [
                {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
            ]},
        ])),
        Setting(key="plex_upload_instance_library_map", value=json.dumps({
            "Radarr": [{"plex_instance": "Old Plex", "library_key": "1"}],
        })),
    ])
    test_db.commit()

    cleanup_stale_plex_name_keys(test_db)
    test_db.commit()

    configs = _get_setting_json(test_db, "plex_library_config")
    assert [c["instance_name"] for c in configs] == ["New Plex"]
    # Routing entries pointing at the vanished name are dropped too
    saved_map = _get_setting_json(test_db, "plex_upload_instance_library_map")
    assert saved_map == {}


def test_startup_cleanup_noop_without_configured_instances(test_db):
    """Before setup (no plex_instances), the cleanup must not touch existing configs."""
    from api.settings import cleanup_stale_plex_name_keys

    test_db.add(Setting(key="plex_library_config", value=json.dumps([
        {"instance_name": "Plex", "libraries": [
            {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
        ]},
    ])))
    test_db.commit()

    cleanup_stale_plex_name_keys(test_db)
    test_db.commit()

    configs = _get_setting_json(test_db, "plex_library_config")
    assert [c["instance_name"] for c in configs] == ["Plex"]


def test_plex_name_swap_is_not_treated_as_rename(client, test_db):
    """Two instances swapping names is ambiguous — selections must be left untouched."""
    test_db.add_all([
        Setting(key="plex_instances", value=json.dumps([
            {"name": "A", "url": "http://plex-a:32400", "api_key": "key-a"},
            {"name": "B", "url": "http://plex-b:32400", "api_key": "key-b"},
        ])),
        Setting(key="plex_library_config", value=json.dumps([
            {"instance_name": "A", "libraries": [{"key": "1", "title": "M", "type": "movie", "enabled": True}]},
            {"instance_name": "B", "libraries": [{"key": "2", "title": "T", "type": "show", "enabled": True}]},
        ])),
        Setting(key="poster_renamer_libraries", value=json.dumps(["A:1", "B:2"])),
    ])
    test_db.commit()

    response = client.post("/api/settings/bulk", json={
        "plex_instances": json.dumps([
            {"name": "B", "url": "http://plex-a:32400", "api_key": "key-a"},
            {"name": "A", "url": "http://plex-b:32400", "api_key": "key-b"},
        ]),
    })
    assert response.status_code == 200

    configs = _get_setting_json(test_db, "plex_library_config")
    assert sorted(c["instance_name"] for c in configs) == ["A", "B"]
    assert _get_setting_json(test_db, "poster_renamer_libraries") == ["A:1", "B:2"]


