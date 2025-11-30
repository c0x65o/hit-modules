"""Database connection helpers backed by the provisioner client."""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import Engine
except ImportError:  # pragma: no cover - optional dependency
    Engine: Any = Any  # type: ignore[misc,assignment]
    create_engine: Any = None  # type: ignore[assignment]

from .client import ProvisionerClient
from .errors import DatabaseConnectionError, ProvisionerError
from .logger import get_logger

logger = get_logger(__name__)


class DatabaseConnectionManager:
    """Provides cached SQLAlchemy engines for module databases."""

    def __init__(
        self,
        client: ProvisionerClient | None = None,
        *,
        default_env_keys: tuple[str, ...] = (
            "HIT_AUTH_DATABASE_URL",
            "DATABASE_URL",
        ),
    ):
        self._client = client or ProvisionerClient()
        self._engines: Dict[str, Engine] = {}
        self._default_env_keys = default_env_keys

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
        """Resolve the database URL via provisioner or environment fallback."""

        try:
            secret = self._client.get_database_secret(
                namespace=namespace,
                secret_key=secret_key,
                role=role,
            )
            db_url = secret.get("url") or secret.get("DATABASE_URL")
            if db_url:
                logger.debug(
                    "Resolved database URL via provisioner",
                    extra={"namespace": namespace, "secret_key": secret_key},
                )
                return db_url
        except ProvisionerError as exc:
            logger.warning(
                "Provisioner lookup failed (%s). Falling back to environment.",
                exc,
            )

        for key in self._default_env_keys:
            env_value = os.environ.get(key)
            if env_value:
                logger.info(
                    "Using %s from environment for namespace %s", key, namespace
                )
                return env_value

        raise DatabaseConnectionError(
            "Unable to determine database URL. Provisioner lookup failed and no "
            "environment fallback was found."
        )

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
