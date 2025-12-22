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
    """Provides cached SQLAlchemy engines for module databases.

    For shared modules (auth, email, etc.), you MUST provide either:
    - A pre-configured ProvisionerClient (from get_provisioner_client dependency)
    - A service token (from the request)

    The client/token is used to authenticate with the provisioner to fetch
    database credentials. Without it, the provisioner returns 401 Unauthorized.

    Example usage in shared modules:
        # Preferred: Use the request-scoped client
        client = await get_provisioner_client(request)
        db_manager = DatabaseConnectionManager(client=client)

        # Alternative: Pass the token directly
        token = await get_service_token(request)
        db_manager = DatabaseConnectionManager(token=token)
    """

    def __init__(
        self,
        client: ProvisionerClient | None = None,
        token: str | None = None,
    ):
        """Initialize database connection manager.

        Args:
            client: Pre-configured provisioner client (preferred).
                    Use get_provisioner_client(request) to get one.
            token: Service token from the request.
                   Required if client is not provided.

        Raises:
            DatabaseConnectionError: If neither client nor token is provided,
                                    or if PROVISIONER_URL is not set.
        """
        import os
        from .config import ClientConfig

        self._token = token

        if client:
            self._client = client
            logger.debug("DatabaseConnectionManager: using provided ProvisionerClient")
        elif token:
            # Create client with the provided token
            base_url = os.environ.get("PROVISIONER_URL", "").strip()
            if not base_url:
                raise DatabaseConnectionError(
                    "PROVISIONER_URL is required for database connections. "
                    "Set PROVISIONER_URL to the provisioner service URL."
                )

            token_preview = token[:30] + "..." if len(token) > 30 else token
            logger.debug(
                f"DatabaseConnectionManager: creating ProvisionerClient with token "
                f"(preview: {token_preview})"
            )

            config = ClientConfig(
                base_url=base_url,
                module_token=token,
                require_token=False,
            )
            self._client = ProvisionerClient(config=config, require_token=False)
        else:
            raise DatabaseConnectionError(
                "DatabaseConnectionManager requires either a ProvisionerClient or a service token. "
                "For shared modules, use:\n"
                "  client = await get_provisioner_client(request)\n"
                "  db_manager = DatabaseConnectionManager(client=client)\n"
                "Or:\n"
                "  token = await get_service_token(request)\n"
                "  db_manager = DatabaseConnectionManager(token=token)"
            )

        self._engines: Dict[str, Engine] = {}

    def _ensure_sqlalchemy(self) -> None:
        if create_engine is None:
            raise DatabaseConnectionError(
                "SQLAlchemy is required for DatabaseConnectionManager but is not installed. "
                "Install hit-modules with the `sqlalchemy` extra or add SQLAlchemy to your project."
            )
        # Ensure psycopg3 is imported so SQLAlchemy recognizes postgresql+psycopg:// URLs
        # This prevents SQLAlchemy from falling back to psycopg2
        try:
            import psycopg  # psycopg3 - import to register with SQLAlchemy
        except ImportError:
            # psycopg3 not available - will use psycopg2 if URL specifies it
            pass

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
                f"Provisioner returned empty secret for namespace '{namespace}' ({secret_key}). "
                f"This usually means:\n"
                f"  1. The database hasn't been provisioned yet - run 'hit db provision' or deploy the project\n"
                f"  2. The provisioner secrets weren't regenerated after provisioning\n"
                f"  3. The database name or namespace in hit.yaml doesn't match what was provisioned\n"
                f"Check that the database '{secret_key}' exists in namespace '{namespace}' and that "
                f"provisioner.secrets.json has been updated."
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

        # Ensure PostgreSQL URLs use psycopg3 (psycopg) driver, not psycopg2
        # SQLAlchemy may fall back to psycopg2 if psycopg3 isn't properly detected
        if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
            # Check if psycopg3 is available
            try:
                import psycopg  # psycopg3

                # Normalize to use psycopg3 driver
                if (
                    db_url.startswith("postgresql://")
                    and "+" not in db_url.split("://")[0]
                ):
                    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
                elif db_url.startswith("postgres://"):
                    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
            except ImportError:
                # psycopg3 not available, but URL should already specify driver
                pass

        # Also normalize if URL has psycopg2 to use psycopg3 instead
        if "postgresql+psycopg2://" in db_url:
            db_url = db_url.replace(
                "postgresql+psycopg2://", "postgresql+psycopg://", 1
            )

        logger.info("Creating SQLAlchemy engine for %s", key)
        # Configure reasonable pool limits for shared databases
        # Default pool_size=5 and max_overflow=10 is too aggressive for shared clusters
        pool_defaults = {
            "pool_size": 2,  # Only 2 connections in the main pool
            "max_overflow": 3,  # Allow 3 temporary connections (5 total max)
            "pool_timeout": 30,  # Wait up to 30s for a connection
            "pool_recycle": 1800,  # Recycle connections after 30 minutes
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

    # ------------------------------------------------------------------
    # Service-targeted database resolution (for task runners, multi-DB apps, etc.)
    # ------------------------------------------------------------------

    def get_service_database_url(
        self,
        *,
        databases: list[dict[str, Any]],
        service_name: str,
        env_key: str = "DATABASE_URL",
    ) -> str:
        """Resolve the *primary* database URL for a specific service from hit.yaml `databases:`.

        Why:
        - Shared modules (auth, tasks, etc.) usually connect to THEIR OWN persistence DB via
          module settings (settings.database.*).
        - Some workflows (notably task execution) need to connect to the *target service's*
          application database, which is defined in the project's top-level `databases:` section.
        - We want a single place to implement this mapping so modules don't re-implement it.

        Selection rules:
        - Find a database role where:
          - role.primary == True
          - service_name is in role.services
          - role.env == env_key (defaults to DATABASE_URL, which most app scripts expect)
        - Use db_config.namespace + db_config.database as the provisioner secret lookup inputs.
        - Pass role.name as the role hint when available.
        """
        if not service_name:
            raise DatabaseConnectionError("service_name is required")

        chosen: dict[str, Any] | None = None
        for db_cfg in databases or []:
            if not isinstance(db_cfg, dict):
                continue
            roles = db_cfg.get("roles", [])
            if not isinstance(roles, list):
                continue
            for role_cfg in roles:
                if not isinstance(role_cfg, dict):
                    continue
                services = role_cfg.get("services", [])
                if not isinstance(services, list):
                    services = []
                if service_name not in services:
                    continue
                if not role_cfg.get("primary"):
                    continue
                role_env = role_cfg.get("env") or "DATABASE_URL"
                if role_env != env_key:
                    continue
                chosen = {
                    "namespace": db_cfg.get("namespace"),
                    "database": db_cfg.get("database"),
                    "role": role_cfg.get("name"),
                }
                break
            if chosen:
                break

        if not chosen or not chosen.get("namespace") or not chosen.get("database"):
            raise DatabaseConnectionError(
                "No primary database mapping found for "
                f"service='{service_name}' env_key='{env_key}'. "
                "Ensure hit.yaml databases roles include primary: true, services: [<service>], "
                f"and env: {env_key}."
            )

        return self.get_database_url(
            namespace=str(chosen["namespace"]),
            secret_key=str(chosen["database"]),
            role=str(chosen.get("role") or "") or None,
        )
