from __future__ import annotations

import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "python"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from hit_modules.config import ClientConfig
from hit_modules.database import DatabaseConnectionManager
from hit_modules.errors import DatabaseConnectionError, ProvisionerConfigError, ProvisionerError


def test_client_config_requires_provisioner_url(monkeypatch):
    monkeypatch.delenv("PROVISIONER_URL", raising=False)
    monkeypatch.delenv("HIT_PROJECT_TOKEN", raising=False)

    with pytest.raises(ProvisionerConfigError):
        ClientConfig.from_env()


def test_client_config_requires_project_token(monkeypatch):
    monkeypatch.setenv("PROVISIONER_URL", "https://provisioner.dev")
    monkeypatch.delenv("HIT_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("HIT_MODULE_ID_TOKEN", raising=False)

    with pytest.raises(ProvisionerConfigError):
        ClientConfig.from_env()


def test_client_config_happy_path(monkeypatch):
    monkeypatch.setenv("PROVISIONER_URL", "https://provisioner.dev")
    monkeypatch.setenv("HIT_PROJECT_TOKEN", "test-token")

    config = ClientConfig.from_env()
    assert config.base_url == "https://provisioner.dev"
    assert config.project_token == "test-token"


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_database_secret(self, **kwargs):
        self.calls.append(kwargs)
        value = self.payload
        if isinstance(value, Exception):
            raise value
        return value


def test_database_manager_returns_url():
    stub = _StubClient({"url": "postgres://user:pass@localhost/db"})
    manager = DatabaseConnectionManager(client=stub)

    url = manager.get_database_url(namespace="shared", secret_key="auth-db")

    assert url == "postgres://user:pass@localhost/db"
    assert stub.calls  # ensure provisioner was queried


def test_database_manager_raises_on_empty_secret():
    stub = _StubClient({})
    manager = DatabaseConnectionManager(client=stub)

    with pytest.raises(DatabaseConnectionError):
        manager.get_database_url(namespace="shared", secret_key="auth-db")


def test_database_manager_wraps_provisioner_errors():
    stub = _StubClient(ProvisionerError("boom"))
    manager = DatabaseConnectionManager(client=stub)

    with pytest.raises(DatabaseConnectionError):
        manager.get_database_url(namespace="shared", secret_key="auth-db")

