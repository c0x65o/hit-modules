"""FastAPI middleware for injecting config and secrets from provisioner."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, status

from .client import ProvisionerClient
from .errors import ProvisionerConfigError, ProvisionerError
from .logger import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_provisioner_client() -> ProvisionerClient:
    """Get cached provisioner client."""
    return ProvisionerClient()


def _get_module_name() -> str:
    """Get module name from environment (required)."""
    module_name = os.getenv("HIT_MODULE_NAME")
    if not module_name:
        raise RuntimeError(
            "HIT_MODULE_NAME environment variable is required. "
            "Set it to the module name (e.g., 'ping-pong')."
        )
    return module_name


@lru_cache(maxsize=1)
def _load_module_config(module_name: str) -> dict[str, Any]:
    """Load module config from provisioner (cached)."""
    try:
        client = _get_provisioner_client()
        config = client.get_module_config(module_name)
        if not config:
            logger.warning("Provisioner returned empty config for module %s", module_name)
            return {}
        logger.info("Loaded config for module %s from provisioner", module_name)
        return config
    except ProvisionerConfigError as exc:
        raise RuntimeError(
            f"Provisioner misconfigured for module {module_name}: {exc}"
        ) from exc
    except ProvisionerError as exc:
        raise RuntimeError(
            f"Failed to load config from provisioner for module {module_name}: {exc}"
        ) from exc


def get_module_config() -> dict[str, Any]:
    """FastAPI dependency that provides module config from hit.yaml via provisioner.
    
    This dependency:
    - Fetches config from provisioner (cached per request)
    - Returns the full module config dict from hit.yaml
    - Fails hard if provisioner is unavailable
    
    Usage:
        @app.get("/endpoint")
        def my_endpoint(config: dict[str, Any] = Depends(get_module_config)):
            increment = config.get("settings", {}).get("increment", 1)
            ...
    """
    module_name = _get_module_name()
    return _load_module_config(module_name)


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

