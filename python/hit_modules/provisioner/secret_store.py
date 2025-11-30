"""Simple JSON-backed secret store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .config import ProvisionerSettings
from ..logger import get_logger

logger = get_logger(__name__)


class SecretStore:
    """Loads secrets from a JSON file and/or environment fallbacks."""

    def __init__(self, settings: ProvisionerSettings):
        self._settings = settings
        self._data: Dict[str, Any] = {}
        if settings.secrets_path:
            self._data = self._load_file(settings.secrets_path)

    def _load_file(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            logger.warning("Secrets file %s not found, continuing with defaults", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
                logger.info("Loaded secrets file %s", path)
                return payload
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in secrets file %s: %s", path, exc)
        except OSError as exc:
            logger.error("Could not read secrets file %s: %s", path, exc)
        return {}

    def get_database_secret(
        self,
        namespace: str,
        secret_key: str,
        role: str | None = None,
    ) -> dict[str, Any] | None:
        namespaces = self._data.get("namespaces", {})
        namespace_entry = namespaces.get(namespace, {})
        db_entry = (
            namespace_entry.get("database", {}).get(secret_key)
            if isinstance(namespace_entry, dict)
            else None
        )

        if db_entry:
            payload = dict(db_entry)
            payload.setdefault("role", role)
            payload.setdefault("namespace", namespace)
            payload.setdefault("key", secret_key)
            return payload

        if self._settings.default_db_url:
            logger.info(
                "Using default DB URL fallback for namespace=%s secret=%s",
                namespace,
                secret_key,
            )
            return {
                "url": self._settings.default_db_url,
                "namespace": namespace,
                "key": secret_key,
                "role": role,
            }

        return None

