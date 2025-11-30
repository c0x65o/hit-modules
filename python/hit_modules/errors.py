"""Custom exceptions for the provisioner client."""

from __future__ import annotations


class ProvisionerError(RuntimeError):
    """Base error for provisioner interactions."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProvisionerAuthError(ProvisionerError):
    """Raised when authentication with the provisioner fails."""


class ProvisionerRequestError(ProvisionerError):
    """Raised for non-auth HTTP errors."""


class SecretNotFoundError(ProvisionerError):
    """Raised when the requested secret cannot be located."""


class ProvisionerConfigError(ProvisionerError):
    """Raised when module environment/configuration is invalid."""


class DatabaseConnectionError(RuntimeError):
    """Raised when the database manager cannot create an engine/connection."""

