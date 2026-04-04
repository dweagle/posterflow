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


