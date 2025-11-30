import os

from fastapi.testclient import TestClient

from hit_modules.provisioner import ProvisionerSettings, create_app


def test_database_secret_fallback(monkeypatch):
    monkeypatch.setenv("PROVISIONER_ALLOW_ANONYMOUS", "1")
    monkeypatch.setenv("HIT_PROVISIONER_DEFAULT_DB_URL", "postgresql://test-user@localhost/db")
    settings = ProvisionerSettings.from_env()
    app = create_app(settings)
    client = TestClient(app)

    response = client.post(
        "/api/v1/secrets/database",
        json={"namespace": "shared", "secretKey": "auth-db"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["url"].startswith("postgresql://")
    assert data["namespace"] == "shared"

