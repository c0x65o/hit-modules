"""Authentication helpers for HIT modules (FastAPI integration).

Validates incoming service/project tokens issued by CAC.
Service tokens (HIT_SERVICE_TOKEN) include module/database ACL claims.
Project tokens (HIT_PROJECT_TOKEN) are legacy and grant full access.

Method-level ACL:
    Service tokens include a `module_uses` claim that maps module names to
    allowed methods. Modules can use `require_method_acl()` to enforce
    method-level access control.

    Example:
        Token claims: {"module_uses": {"ping-pong": ["get_count"]}}

        @router.post("/increment")
        async def increment(claims: dict = Depends(require_method_acl("ping-pong", "increment"))):
            # Only services with "increment" in their uses list can call this
            ...
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .client import ProvisionerClient
from .errors import ProvisionerConfigError, ProvisionerError
from .logger import get_logger

logger = get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)

# Module name for ACL checks - set via HIT_MODULE_NAME env var
_module_name: str | None = os.getenv("HIT_MODULE_NAME")


def set_module_name(name: str) -> None:
    """Set the module name for ACL checks.

    This is typically called during module initialization or can be set
    via the HIT_MODULE_NAME environment variable.
    """
    global _module_name
    _module_name = name
    logger.info(f"Module name set to '{name}' for ACL checks")


def get_module_name() -> str | None:
    """Get the configured module name."""
    return _module_name


@lru_cache(maxsize=1)
def _client() -> ProvisionerClient:
    # Create client without requiring own token - shared modules validate incoming tokens
    return ProvisionerClient(require_token=False)


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
        client = _client()
        if client is None:
            logger.error("Provisioner client is None - cannot verify token")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Provisioner client not initialized",
            )

        result = client.verify_project_token(token)
        if result is None:
            logger.error("Provisioner verify_project_token returned None")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Provisioner returned invalid response",
            )
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
        logger.debug(f"Token verification result missing claims: result={result}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid project token",
        )

    return claims


def require_method_acl(module_name: str | None = None, method_name: str | None = None):
    """Create a FastAPI dependency that validates method-level ACL.

    This checks if the calling service's token grants access to a specific
    method on this module. If access is denied, returns 403 Forbidden.

    Args:
        module_name: Module name to check. If None, uses HIT_MODULE_NAME env var.
        method_name: Method name to check. If None, uses the endpoint function name.

    Returns:
        A FastAPI Depends() that returns claims if authorized.

    Example:
        @router.post("/increment")
        async def increment(claims: dict = Depends(require_method_acl("ping-pong", "increment"))):
            # Only services with "increment" in their uses list can call this
            return {"count": current_count}

        # Or use automatic method name detection:
        @router.post("/increment")
        async def increment(claims: dict = Depends(require_method_acl("ping-pong"))):
            # method_name defaults to "increment" (function name)
            return {"count": current_count}
    """

    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        request: Request = None,
    ) -> dict[str, Any]:
        # Resolve module name
        mod_name = module_name or _module_name
        if not mod_name:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Module name not configured. Set HIT_MODULE_NAME or pass module_name to require_method_acl().",
            )

        # Resolve method name (from arg or endpoint path)
        meth_name = method_name
        if not meth_name and request:
            # Try to get from endpoint path (e.g., /hit/increment -> increment)
            path = request.url.path
            meth_name = path.rstrip("/").split("/")[-1]

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
            client = _client()
            if client is None:
                logger.error(
                    "Provisioner client is None - cannot verify token with ACL"
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Provisioner client not initialized",
                )

            result = client.verify_token_with_acl(
                token=token,
                module_name=mod_name,
                method_name=meth_name,
            )
            if result is None:
                logger.error("Provisioner verify_token_with_acl returned None")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Provisioner returned invalid response",
                )
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
                detail="Invalid token",
            ) from exc

        valid = result.get("valid")
        if valid is None:
            logger.debug(
                f"ACL verification result missing 'valid' field: result={result}"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Provisioner returned invalid response format",
            )

        if not valid:
            logger.debug(f"Token ACL verification failed: result={result}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        # Check module access
        if not result.get("module_allowed", True):
            reason = result.get("reason", f"Access to module '{mod_name}' denied")
            logger.warning(f"Module ACL denied: {reason}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )

        # Check method access
        if meth_name and not result.get("method_allowed", True):
            reason = result.get(
                "reason",
                f"Access to method '{meth_name}' on module '{mod_name}' denied",
            )
            logger.warning(f"Method ACL denied: {reason}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )

        return result.get("claims", {})

    return dependency


def _enforce_fastapi_auth(app: FastAPI) -> None:
    """Internal: Attach the provisioner auth dependency to all routes in a FastAPI app.

    This is used internally by install_hit_modules(). Use install_hit_modules() or
    create_hit_app() instead of calling this directly.
    """

    dependency = Depends(require_provisioned_token)
    app.router.dependencies.append(dependency)
    logger.info("Provisioner bearer token enforcement enabled for FastAPI app")
