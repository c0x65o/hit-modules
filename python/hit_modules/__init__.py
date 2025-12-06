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
from .events import (
    EventMessage,
    EventPublisher,
    EventSubscriber,
    get_event_publisher,
    publish_event,
    event_publisher_context,
)
from .db_events import (
    EventEmittingBase,
    EventModelConfig,
    emit_events,
    setup_pg_notify_triggers,
    start_db_event_listener,
)
from .auto_events import (
    install_auto_events,
    install_auto_events_from_config,
    auto_publish_all,
    AutoEventsConfig,
)
from .fastapi import create_hit_app, install_hit_modules
from .middleware import get_module_config, get_module_config_from_request, get_module_secrets, get_module_settings
from .auth import require_provisioned_token
from .version import get_module_version, log_module_startup

__all__ = [
    # Client utilities
    "ClientConfig",
    "ProvisionerClient",
    "DatabaseConnectionManager",
    # Errors
    "ProvisionerError",
    "ProvisionerConfigError",
    "ProvisionerAuthError",
    "ProvisionerRequestError",
    "SecretNotFoundError",
    # Events
    "EventMessage",
    "EventPublisher",
    "EventSubscriber",
    "get_event_publisher",
    "publish_event",
    "event_publisher_context",
    # DB Events (PostgreSQL trigger-based, legacy)
    "EventEmittingBase",
    "EventModelConfig",
    "emit_events",
    "setup_pg_notify_triggers",
    "start_db_event_listener",
    # Auto Events (SQLAlchemy hook-based, recommended)
    "install_auto_events",
    "install_auto_events_from_config",
    "auto_publish_all",
    "AutoEventsConfig",
    # Auth
    "require_provisioned_token",
    # FastAPI integration
    "create_hit_app",
    "install_hit_modules",
    # Config helpers
    "get_module_config",
    "get_module_config_from_request",
    "get_module_secrets",
    "get_module_settings",
    # Version
    "get_module_version",
    "log_module_startup",
]

