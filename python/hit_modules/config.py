"""Configuration helpers for the provisioner client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _read_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _read_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
        return parsed if parsed > 0 else default
    except ValueError:
        return default


@dataclass(frozen=True)
class ClientConfig:
    """Typed container for provisioner client configuration."""

    base_url: str
    module_token: str | None = None
    project_token: str | None = None
    timeout: float = 5.0
    verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> "ClientConfig":
        """Build config using environment variables."""

        base_url = os.environ.get("PROVISIONER_URL", "").strip()
        if not base_url:
            # Allow local fallback; the client can still use env secrets without remote calls.
            base_url = ""

        module_token = os.environ.get("HIT_MODULE_ID_TOKEN")
        project_token = os.environ.get("HIT_PROJECT_TOKEN")
        timeout = _read_float(os.environ.get("HIT_PROVISIONER_TIMEOUT"), 5.0)
        verify_ssl = _read_bool(os.environ.get("HIT_PROVISIONER_VERIFY_SSL"), True)

        return cls(
            base_url=base_url,
            module_token=module_token,
            project_token=project_token,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )

    def headers(self) -> dict[str, str]:
        """Build default headers for outbound requests."""

        headers: dict[str, str] = {
            "User-Agent": "hit-modules-client/0.1",
            "Accept": "application/json",
        }
        token = self.module_token or self.project_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def to_dict(self) -> dict[str, Any]:
        """Return a dict suitable for logging/debugging (tokens redacted)."""

        return {
            "base_url": self.base_url,
            "module_token_set": bool(self.module_token),
            "project_token_set": bool(self.project_token),
            "timeout": self.timeout,
            "verify_ssl": self.verify_ssl,
        }

