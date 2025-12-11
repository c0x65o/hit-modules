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
        require_token: bool = True,
    ):
        """Initialize the provisioner client.

        Args:
            config: Client configuration. If None, loads from environment.
            session: Optional requests session to use.
            require_token: If True, requires a token for authentication.
                          Set to False for shared modules that only validate incoming tokens.
        """
        self._config = config or ClientConfig.from_env(require_token=require_token)
        if not self._config.base_url:
            raise ProvisionerConfigError(
                "Provisioner base URL missing. Did you forget to set PROVISIONER_URL?"
            )
        # Only require token if explicitly requested
        if require_token and not self._config.module_token:
            raise ProvisionerConfigError(
                "Provisioner authentication requires HIT_MODULE_ID_TOKEN."
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
            raise ProvisionerAuthError(
                "Provisioner authentication failed", status_code=401
            )

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

    def verify_token_with_acl(
        self,
        token: str,
        module_name: str,
        method_name: str | None = None,
    ) -> dict[str, Any]:
        """Validate a token and check if module/method access is allowed.

        Args:
            token: JWT token to validate
            module_name: Module name to check access for (e.g., "ping-pong")
            method_name: Optional method name to check (e.g., "increment")

        Returns:
            Dict with:
            - valid: bool - token is valid
            - claims: dict - decoded token claims
            - module_allowed: bool - module access allowed
            - method_allowed: bool - method access allowed (if method_name provided)
            - reason: str - explanation if denied
        """
        payload = {
            "token": token,
            "moduleName": module_name,
        }
        if method_name:
            payload["methodName"] = method_name

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
