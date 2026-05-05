from models.setting import Setting


def test_setup_status_returns_false_when_no_setting(client):
    """When no setup_complete setting exists, returns setup_complete=False."""
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json() == {"setup_complete": False}


def test_setup_status_returns_true_when_setting_is_true(client, test_db):
    """When setup_complete is set to 'true', returns setup_complete=True."""
    test_db.add(Setting(key="setup_complete", value="true"))
    test_db.commit()

    response = client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json() == {"setup_complete": True}


def test_setup_status_returns_false_when_setting_is_not_true(client, test_db):
    """When setup_complete is set to a non-true value, returns setup_complete=False."""
    test_db.add(Setting(key="setup_complete", value="false"))
    test_db.commit()

    response = client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json() == {"setup_complete": False}
