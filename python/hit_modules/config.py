"""Configuration helpers for the provisioner client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .errors import ProvisionerConfigError


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
    # Allow creating client without token (for shared modules that validate incoming tokens)
    require_token: bool = True

    @classmethod
    def from_env(cls, *, require_token: bool = True) -> "ClientConfig":
        """Build config using environment variables.
        
        Args:
            require_token: If True (default), requires HIT_PROJECT_TOKEN or HIT_MODULE_ID_TOKEN.
                          Set to False for shared modules that only need to validate incoming tokens.
        """

        base_url = os.environ.get("PROVISIONER_URL", "").strip()
        if not base_url:
            raise ProvisionerConfigError(
                "PROVISIONER_URL is required for hit-modules clients. "
                "Set it to the provisioner service base URL (e.g., https://provisioner.dev.svc)."
            )

        module_token = (os.environ.get("HIT_MODULE_ID_TOKEN") or "").strip() or None
        project_token = (os.environ.get("HIT_PROJECT_TOKEN") or "").strip() or None
        
        # Only require token if explicitly requested (project-specific modules)
        # Shared modules may not have their own token - they validate incoming tokens
        if require_token and not (module_token or project_token):
            raise ProvisionerConfigError(
                "HIT_PROJECT_TOKEN is required for module provisioning. "
                "Ensure your pod is injected with HIT_PROJECT_TOKEN (and optionally HIT_MODULE_ID_TOKEN)."
            )

        timeout = _read_float(os.environ.get("HIT_PROVISIONER_TIMEOUT"), 5.0)
        verify_ssl = _read_bool(os.environ.get("HIT_PROVISIONER_VERIFY_SSL"), True)

        return cls(
            base_url=base_url,
            module_token=module_token,
            project_token=project_token,
            timeout=timeout,
            verify_ssl=verify_ssl,
            require_token=require_token,
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

