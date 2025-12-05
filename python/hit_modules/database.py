"""Database connection helpers backed by the provisioner client."""

from __future__ import annotations

from typing import Any, Dict

try:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import Engine
except ImportError:  # pragma: no cover - optional dependency
    from typing import Any

    Engine: Any = Any  # type: ignore[misc,assignment]
    create_engine: Any = None  # type: ignore[assignment]

from .client import ProvisionerClient
from .errors import (
    DatabaseConnectionError,
    ProvisionerConfigError,
    ProvisionerError,
)
from .logger import get_logger

logger = get_logger(__name__)


class DatabaseConnectionManager:
    """Provides cached SQLAlchemy engines for module databases."""

    def __init__(
        self,
        client: ProvisionerClient | None = None,
        token: str | None = None,
    ):
        """Initialize database connection manager.
        
        Args:
            client: Optional pre-configured provisioner client
            token: Optional bearer token to use for provisioner requests.
                   For shared modules, this should be the calling app's token.
        """
        if client:
            self._client = client
        else:
            # Create client with optional token
            from .config import ClientConfig
            import os
            
            base_url = os.environ.get("PROVISIONER_URL", "").strip()
            if base_url:
                config = ClientConfig(
                    base_url=base_url,
                    project_token=token,
                    module_token=None,
                    require_token=False,
                )
                self._client = ProvisionerClient(config=config, require_token=False)
            else:
                # Fall back to default client (may fail if PROVISIONER_URL not set)
                self._client = ProvisionerClient(require_token=False)
        self._engines: Dict[str, Engine] = {}

    def _ensure_sqlalchemy(self) -> None:
        if create_engine is None:
            raise DatabaseConnectionError(
                "SQLAlchemy is required for DatabaseConnectionManager but is not installed. "
                "Install hit-modules with the `sqlalchemy` extra or add SQLAlchemy to your project."
            )

    def get_database_url(
        self,
        *,
        namespace: str,
        secret_key: str = "auth-db",
        role: str | None = None,
    ) -> str:
        """Resolve the database URL via provisioner (no environment fallback)."""

        try:
            secret = self._client.get_database_secret(
                namespace=namespace,
                secret_key=secret_key,
                role=role,
            )
        except ProvisionerConfigError as exc:
            raise DatabaseConnectionError(
                f"Provisioner configuration invalid while fetching database secret: {exc}"
            ) from exc
        except ProvisionerError as exc:
            raise DatabaseConnectionError(
                f"Provisioner lookup failed for namespace '{namespace}': {exc}"
            ) from exc

        if not secret:
            raise DatabaseConnectionError(
                f"Provisioner returned empty secret for namespace '{namespace}' ({secret_key})."
            )

        db_url = secret.get("url") or secret.get("DATABASE_URL")
        if not db_url:
            raise DatabaseConnectionError(
                f"Provisioner secret missing database URL for namespace '{namespace}' ({secret_key})."
            )

        logger.debug(
            "Resolved database URL via provisioner",
            extra={"namespace": namespace, "secret_key": secret_key},
        )
        return db_url

    def get_engine(
        self,
        *,
        namespace: str,
        secret_key: str = "auth-db",
        role: str | None = None,
        engine_key: str | None = None,
        **engine_kwargs: Any,
    ) -> Engine:
        """Return a cached SQLAlchemy engine for the requested namespace."""

        self._ensure_sqlalchemy()

        key = engine_key or f"{namespace}:{secret_key}:{role or 'default'}"
        if key in self._engines:
            return self._engines[key]

        db_url = self.get_database_url(
            namespace=namespace, secret_key=secret_key, role=role
        )
        logger.info("Creating SQLAlchemy engine for %s", key)
        # Configure reasonable pool limits for shared databases
        # Default pool_size=5 and max_overflow=10 is too aggressive for shared clusters
        pool_defaults = {
            "pool_size": 2,       # Only 2 connections in the main pool
            "max_overflow": 3,    # Allow 3 temporary connections (5 total max)
            "pool_timeout": 30,   # Wait up to 30s for a connection
            "pool_recycle": 1800, # Recycle connections after 30 minutes
        }
        # Allow overrides from caller, but apply sane defaults
        for pool_key, pool_value in pool_defaults.items():
            engine_kwargs.setdefault(pool_key, pool_value)
        
        engine = create_engine(db_url, pool_pre_ping=True, **engine_kwargs)  # type: ignore[arg-type]
        self._engines[key] = engine
        return engine

    def dispose(self) -> None:
        """Dispose all cached engines (useful for graceful shutdown)."""

        for key, engine in list(self._engines.items()):
            try:
                engine.dispose()
                logger.info("Disposed engine %s", key)
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to dispose engine %s: %s", key, exc)
        self._engines.clear()
