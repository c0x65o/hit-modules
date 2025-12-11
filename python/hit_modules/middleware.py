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
               If provided, this token will be used for provisioner requests.
               If None, creates client without token (for anonymous calls).
    """
    from .config import ClientConfig

    # Build config from environment
    base_url = os.environ.get("PROVISIONER_URL", "").strip()
    if not base_url:
        raise ProvisionerConfigError(
            "PROVISIONER_URL is required. Set it to the provisioner service URL."
        )

    token_preview = token[:30] + "..." if token and len(token) > 30 else token or "None"
    logger.debug(
        f"Creating provisioner client: base_url={base_url}, "
        f"has_token={bool(token)}, token_preview={token_preview}"
    )

    config = ClientConfig(
        base_url=base_url,
        module_token=token,  # Use the passed token (service token from request)
        require_token=False,  # Don't require token - we're a shared module
    )

    # Verify the token is actually set in the config
    if token and not config.module_token:
        logger.error(
            f"Token was provided but not set in ClientConfig! "
            f"Provided token: {token_preview}"
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
    service_name: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Load module config from provisioner.

    Args:
        module_name: The module name (e.g., "ping-pong")
        project_slug: Project slug from token claims (required for service tokens)
        service_name: Service name from token claims (required for service tokens)
        token: Service token to pass to provisioner for K8s dynamic lookup

    Returns:
        Module configuration dict from hit.yaml
        Includes _request_token, _project_slug, and _service_name for database credential lookups

    Raises:
        RuntimeError: If service token is missing or invalid (missing prj or svc claims)
    """
    # Require both project_slug and service_name for service tokens
    # Legacy project-only tokens are no longer supported
    if token and (not project_slug or not service_name):
        claims = _decode_token_claims(token) if token else None
        has_prj = bool(claims.get("prj") if claims else None)
        has_svc = bool(claims.get("svc") if claims else None)

        raise RuntimeError(
            f"Invalid service token for module {module_name}. "
            f"Service tokens must have both 'prj' (project) and 'svc' (service) claims. "
            f"Token has prj: {has_prj}, svc: {has_svc}. "
            f"Legacy project-only tokens are no longer supported. "
            f"Ensure the calling service sends a valid service token with both claims."
        )

    # Log when no project_slug/service is found (should not happen with service tokens)
    if not project_slug or not service_name:
        logger.warning(
            f"No project_slug or service_name found for module {module_name}. "
            f"Token present: {bool(token)}. "
            "This may indicate a missing or invalid service token."
        )

    # Check cache first if we have both project_slug and service_name
    # Cache key includes service because different services can have different configs
    cache_key = f"{module_name}:{project_slug or 'default'}:{service_name or 'default'}"
    if cache_key in _config_cache:
        logger.debug(f"Using cached config for {cache_key}")
        cached = _config_cache[cache_key].copy()
        # Always include the current request's token for database lookups
        if token:
            cached["_request_token"] = token
        if project_slug:
            cached["_project_slug"] = project_slug
        if service_name:
            cached["_service_name"] = service_name
        return cached

    try:
        client = _get_provisioner_client(token=token)
        logger.info(
            f"Fetching config for module {module_name} from provisioner "
            f"(project: {project_slug or 'none'}, service: {service_name or 'none'}, "
            f"has_token={bool(token)}, token_preview={token[:20] + '...' if token and len(token) > 20 else token or 'None'})"
        )
        if not token:
            logger.warning(
                f"No token provided for config request to module {module_name}. "
                f"Provisioner will return empty config without a valid service token. "
                f"This is expected during startup, but requests should include X-HIT-Service-Token header."
            )
        config = client.get_module_config(module_name)

        # Log detailed info about what was received
        has_settings = bool(config.get("settings"))
        has_features = bool(config.get("features"))
        has_secrets = bool(config.get("secrets"))
        config_keys = list(config.keys())

        if not config:
            logger.warning(
                f"Provisioner returned empty config for module {module_name} "
                f"(project: {project_slug or 'none'}, service: {service_name or 'none'})"
            )
            config = {}
        else:
            logger.info(
                f"Loaded config for module {module_name} "
                f"(project: {project_slug or 'none'}, service: {service_name or 'none'}): "
                f"keys={config_keys}, has_settings={has_settings}, "
                f"has_features={has_features}, has_secrets={has_secrets}"
            )
            if has_settings:
                settings_keys = list(config.get("settings", {}).keys())
                logger.debug(f"Module {module_name} settings keys: {settings_keys}")

        # Cache it (without token - token is added per-request)
        _config_cache[cache_key] = config

        # Add token, project_slug, and service_name to returned config for database credential lookups
        # and debugging when config is empty
        result = config.copy()
        if token:
            result["_request_token"] = token
        if project_slug:
            result["_project_slug"] = project_slug
        if service_name:
            result["_service_name"] = service_name
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


def _decode_token_claims(token: str) -> dict[str, Any] | None:
    """Decode token claims without full validation.

    We just need the 'prj' and 'svc' claims for caching and provisioner lookup.
    Full validation happens in require_provisioned_token().

    Returns:
        Dict with 'prj' and 'svc' claims, or None if token is invalid
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

        return payload
    except Exception:
        return None


def _decode_project_slug(token: str) -> str | None:
    """Decode project_slug from token (legacy - use _decode_token_claims instead)."""
    claims = _decode_token_claims(token)
    return claims.get("prj") if claims else None


async def get_module_config_from_request(request: Request) -> dict[str, Any]:
    """FastAPI dependency that provides module config using the request's service token.

    This dependency:
    - Extracts the service token from X-HIT-Service-Token or Authorization header
    - Validates that the token has both 'prj' (project) and 'svc' (service) claims
    - Passes the token to provisioner for K8s dynamic ConfigMap lookup
    - Caches config per-project+service for efficiency
    - Requires a valid service token (legacy project-only tokens not supported)

    Usage:
        @app.get("/endpoint")
        async def my_endpoint(config: dict[str, Any] = Depends(get_module_config_from_request)):
            increment = config.get("settings", {}).get("increment", 1)
            ...
    """
    module_name = _get_module_name()

    # Try to extract service token from request
    token = _extract_bearer_token(request)

    if not token:
        raise RuntimeError(
            f"Service token required for module {module_name}. "
            f"Requests must include X-HIT-Service-Token header or Authorization: Bearer <service_token>. "
            f"Service tokens must have both 'prj' (project) and 'svc' (service) claims."
        )

    # Decode token claims to extract project_slug and service_name
    claims = _decode_token_claims(token)
    if not claims:
        raise RuntimeError(
            f"Invalid token format for module {module_name}. "
            f"Token must be a valid JWT with 'prj' and 'svc' claims."
        )

    project_slug = claims.get("prj")
    service_name = claims.get("svc")

    # Require both project_slug and service_name - service tokens always have both
    if not project_slug or not service_name:
        raise RuntimeError(
            f"Service token missing required claims for module {module_name}. "
            f"Service tokens must have both 'prj' (project slug) and 'svc' (service name) claims. "
            f"Token has prj: {bool(project_slug)}, svc: {bool(service_name)}. "
            f"Legacy project-only tokens are no longer supported."
        )

    return _load_module_config(
        module_name=module_name,
        project_slug=project_slug,
        service_name=service_name,
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
