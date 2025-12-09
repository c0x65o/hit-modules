"""FastAPI middleware for injecting config and secrets from provisioner."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Request

from .client import ProvisionerClient
from .errors import ProvisionerConfigError, ProvisionerError
from .logger import get_logger

logger = get_logger(__name__)

# Cache for module configs keyed by project_slug
_config_cache: dict[str, dict[str, Any]] = {}


def _get_provisioner_client(token: str | None = None) -> ProvisionerClient:
    """Get provisioner client, optionally with a specific token.

    Args:
        token: Optional token to use for authentication.
               If None, creates client without token (for anonymous calls).
    """
    from .config import ClientConfig

    # Build config from environment
    base_url = os.environ.get("PROVISIONER_URL", "").strip()
    if not base_url:
        raise ProvisionerConfigError(
            "PROVISIONER_URL is required. Set it to the provisioner service URL."
        )

    config = ClientConfig(
        base_url=base_url,
        project_token=token,
        module_token=None,
        require_token=False,  # Don't require token - we're a shared module
    )

    return ProvisionerClient(config=config, require_token=False)


def _get_module_name() -> str:
    """Get module name from environment (required)."""
    module_name = os.getenv("HIT_MODULE_NAME")
    if not module_name:
        raise RuntimeError(
            "HIT_MODULE_NAME environment variable is required. "
            "Set it to the module name (e.g., 'ping-pong')."
        )
    return module_name


def _load_module_config(
    module_name: str,
    project_slug: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Load module config from provisioner.

    Args:
        module_name: The module name (e.g., "ping-pong")
        project_slug: Optional project slug for caching (from token claims)
        token: Optional token to pass to provisioner for K8s dynamic lookup

    Returns:
        Module configuration dict from hit.yaml
        Includes _request_token for database credential lookups
    """
    # Log when no project_slug is found (potential issue)
    if not project_slug:
        logger.warning(
            f"No project_slug found for module {module_name}. "
            f"Token present: {bool(token)}. "
            "Requests without project_slug will share config cache."
        )

    # Check cache first if we have a project_slug
    cache_key = f"{module_name}:{project_slug or 'default'}"
    if cache_key in _config_cache:
        logger.debug(f"Using cached config for {cache_key}")
        cached = _config_cache[cache_key].copy()
        # Always include the current request's token for database lookups
        if token:
            cached["_request_token"] = token
        return cached

    try:
        client = _get_provisioner_client(token=token)
        config = client.get_module_config(module_name)
        if not config:
            logger.warning(
                f"Provisioner returned empty config for module {module_name}"
            )
            config = {}
        else:
            logger.info(
                f"Loaded config for module {module_name} (project: {project_slug or 'none'})"
            )

        # Cache it (without token - token is added per-request)
        _config_cache[cache_key] = config

        # Add token and project_slug to returned config for database credential lookups
        # and debugging when config is empty
        result = config.copy()
        if token:
            result["_request_token"] = token
        if project_slug:
            result["_project_slug"] = project_slug
        return result
    except ProvisionerConfigError as exc:
        raise RuntimeError(
            f"Provisioner misconfigured for module {module_name}: {exc}"
        ) from exc
    except ProvisionerError as exc:
        raise RuntimeError(
            f"Failed to load config from provisioner for module {module_name}: {exc}"
        ) from exc


def clear_config_cache() -> None:
    """Clear the module config cache, forcing a reload from provisioner on next request.

    Call this after the provisioner has reloaded its config to pick up changes.
    """
    _config_cache.clear()
    logger.info("Module config cache cleared")


def _extract_bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header or X-HIT-Service-Token.

    Service tokens can come from:
    1. Authorization: Bearer <token> - when services call modules directly
    2. X-HIT-Service-Token - when frontend proxies calls for end users

    For config lookup (project identification), we prefer X-HIT-Service-Token
    because it contains the service token with the 'prj' (project slug) claim.
    The Authorization header may contain a user's JWT which doesn't have 'prj'.
    """
    # Check X-HIT-Service-Token first (frontend proxy adds this for project identification)
    service_token = request.headers.get("X-HIT-Service-Token")
    if service_token:
        return service_token

    # Fall back to Authorization header (direct service-to-module calls)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]  # Remove "Bearer " prefix

    return None


def _decode_project_slug(token: str) -> str | None:
    """Decode project_slug from token without full validation.

    We just need the 'prj' claim for caching and provisioner lookup.
    Full validation happens in require_provisioned_token().
    """
    try:
        import base64
        import json

        # JWT format: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return None

        # Decode payload (add padding if needed)
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload_json = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_json)

        return payload.get("prj")
    except Exception:
        return None


async def get_module_config_from_request(request: Request) -> dict[str, Any]:
    """FastAPI dependency that provides module config using the request's token.

    This dependency:
    - Extracts the token from the Authorization header (if present)
    - Passes the token to provisioner for K8s dynamic ConfigMap lookup
    - Caches config per-project for efficiency
    - Falls back to default config if no token provided

    Usage:
        @app.get("/endpoint")
        async def my_endpoint(config: dict[str, Any] = Depends(get_module_config_from_request)):
            increment = config.get("settings", {}).get("increment", 1)
            ...
    """
    module_name = _get_module_name()

    # Try to extract token from request
    token = _extract_bearer_token(request)
    project_slug = _decode_project_slug(token) if token else None

    return _load_module_config(
        module_name=module_name,
        project_slug=project_slug,
        token=token,
    )


def get_module_config() -> dict[str, Any]:
    """Get module config (synchronous version, for backward compatibility).

    WARNING: This function does NOT have access to the request token.
    It's kept for backward compatibility with startup code that loads config
    before any requests arrive. For request-time config, use get_module_config_from_request.

    In environment-wide provisioner mode, this will return empty config unless
    hit.yaml is mounted at /etc/config/hit.yaml (local dev mode).
    """
    module_name = _get_module_name()
    return _load_module_config(module_name=module_name)


def get_module_secrets() -> dict[str, Any]:
    """FastAPI dependency that provides module secrets from hit.yaml via provisioner.

    This dependency:
    - Extracts secrets from module config
    - Returns secrets dict
    - Fails hard if provisioner is unavailable

    Usage:
        @app.get("/endpoint")
        def my_endpoint(
            config: dict[str, Any] = Depends(get_module_config),
            secrets: dict[str, Any] = Depends(get_module_secrets),
        ):
            jwt_secret = secrets.get("JWT_SECRET")
            ...
    """
    config = get_module_config()
    secrets = config.get("secrets", {})
    if not isinstance(secrets, dict):
        return {}
    return secrets


def get_module_settings() -> dict[str, Any]:
    """FastAPI dependency that provides module settings from hit.yaml via provisioner.

    Convenience dependency that extracts just the settings block.

    Usage:
        @app.get("/endpoint")
        def my_endpoint(settings: dict[str, Any] = Depends(get_module_settings)):
            increment = settings.get("increment", 1)
            ...
    """
    config = get_module_config()
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        return {}
    return settings
