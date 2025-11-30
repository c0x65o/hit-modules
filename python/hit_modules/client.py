"""HTTP client for the provisioning middleware."""

from __future__ import annotations

import json
from typing import Any

import requests

from .config import ClientConfig
from .errors import (
    ProvisionerAuthError,
    ProvisionerConfigError,
    ProvisionerError,
    ProvisionerRequestError,
    SecretNotFoundError,
)
from .logger import get_logger

logger = get_logger(__name__)


class ProvisionerClient:
    """Thin wrapper around the provisioner HTTP API."""

    def __init__(
        self,
        config: ClientConfig | None = None,
        *,
        session: requests.Session | None = None,
    ):
        self._config = config or ClientConfig.from_env()
        if not self._config.base_url:
            raise ProvisionerConfigError(
                "Provisioner base URL missing. Did you forget to set PROVISIONER_URL?"
            )
        if not (self._config.module_token or self._config.project_token):
            raise ProvisionerConfigError(
                "Provisioner authentication requires HIT_PROJECT_TOKEN (and optionally HIT_MODULE_ID_TOKEN)."
            )
        self._session = session or requests.Session()

    @property
    def base_url(self) -> str:
        return self._config.base_url.rstrip("/")

    def _build_url(self, path: str) -> str:
        if not self.base_url:
            raise ProvisionerConfigError(
                "Provisioner base URL is not configured. "
                "Set PROVISIONER_URL or supply a ClientConfig."
            )
        path = path.lstrip("/")
        return f"{self.base_url}/{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        url = self._build_url(path)
        headers = self._config.headers()

        logger.debug(
            "Provisioner request",
            extra={"method": method, "url": url, "payload": json_body},
        )

        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                headers=headers,
                json=json_body,
                timeout=self._config.timeout,
                verify=self._config.verify_ssl,
            )
        except requests.RequestException as exc:
            logger.error("Provisioner request failed: %s", exc)
            raise ProvisionerRequestError(str(exc)) from exc

        if response.status_code == expected_status:
            if not response.content:
                return {}
            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise ProvisionerRequestError(
                    f"Invalid JSON response from provisioner: {exc}"
                ) from exc

        if response.status_code == 401:
            raise ProvisionerAuthError("Provisioner authentication failed", status_code=401)

        if response.status_code == 404:
            raise SecretNotFoundError(
                "Requested secret not found",
                status_code=404,
            )

        detail = response.text or "Unknown error"
        raise ProvisionerRequestError(detail, status_code=response.status_code)

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    def get_database_secret(
        self,
        *,
        namespace: str,
        secret_key: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a database secret (connection string + metadata)."""

        payload = {
            "namespace": namespace,
            "secretKey": secret_key,
            "role": role,
        }
        return self._request(
            "POST",
            "/api/v1/secrets/database",
            json_body=payload,
            expected_status=200,
        )

    def get_secret(
        self,
        *,
        namespace: str,
        secret_type: str,
        selector: dict[str, Any],
    ) -> dict[str, Any]:
        """Generic secret accessor."""

        payload = {"namespace": namespace, "selector": selector}
        return self._request(
            "POST",
            f"/api/v1/secrets/{secret_type}",
            json_body=payload,
        )

    def ping(self) -> bool:
        """Check provisioning service health."""

        try:
            self._request("GET", "/healthz", expected_status=200)
            return True
        except ProvisionerError as exc:
            logger.warning("Provisioner health check failed: %s", exc)
            return False

    def verify_project_token(self, token: str) -> dict[str, Any]:
        """Ask the provisioner to validate an end-user/project token."""

        payload = {"token": token}
        return self._request(
            "POST",
            "/api/v1/tokens/validate",
            json_body=payload,
            expected_status=200,
        )

    def get_module_config(self, module_name: str) -> dict[str, Any]:
        """Fetch module-specific configuration from the provisioner."""

        payload = {"moduleName": module_name}
        return self._request(
            "POST",
            "/api/v1/config/module",
            json_body=payload,
            expected_status=200,
        )

