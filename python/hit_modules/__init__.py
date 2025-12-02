"""Shared client utilities for HIT modules."""

from .client import ProvisionerClient
from .config import ClientConfig
from .database import DatabaseConnectionManager
from .errors import (
    ProvisionerAuthError,
    ProvisionerConfigError,
    ProvisionerError,
    ProvisionerRequestError,
    SecretNotFoundError,
)
from .fastapi import create_hit_app, install_hit_modules
from .middleware import get_module_config, get_module_config_from_request, get_module_secrets, get_module_settings
from .auth import require_provisioned_token
from .version import get_module_version, log_module_startup

__all__ = [
    "ClientConfig",
    "ProvisionerClient",
    "DatabaseConnectionManager",
    "ProvisionerError",
    "ProvisionerConfigError",
    "ProvisionerAuthError",
    "ProvisionerRequestError",
    "SecretNotFoundError",
    "require_provisioned_token",
    "create_hit_app",
    "install_hit_modules",
    "get_module_config",
    "get_module_config_from_request",
    "get_module_secrets",
    "get_module_settings",
    "get_module_version",
    "log_module_startup",
]

