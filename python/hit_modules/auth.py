"""Authentication helpers for HIT modules (FastAPI integration)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .client import ProvisionerClient
from .errors import ProvisionerConfigError, ProvisionerError
from .logger import get_logger

logger = get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _client() -> ProvisionerClient:
    return ProvisionerClient()


def require_provisioned_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """FastAPI dependency that enforces CAC-issued bearer tokens."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    token = credentials.credentials
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    try:
        result = _client().verify_project_token(token)
    except ProvisionerConfigError as exc:
        logger.error("Provisioner misconfigured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ProvisionerError as exc:
        logger.warning("Provisioner token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid project token",
        ) from exc

    claims = result.get("claims")
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid project token",
        )

    return claims


def _enforce_fastapi_auth(app: FastAPI) -> None:
    """Internal: Attach the provisioner auth dependency to all routes in a FastAPI app.
    
    This is used internally by install_hit_modules(). Use install_hit_modules() or
    create_hit_app() instead of calling this directly.
    """

    dependency = Depends(require_provisioned_token)
    app.router.dependencies.append(dependency)
    logger.info("Provisioner bearer token enforcement enabled for FastAPI app")

