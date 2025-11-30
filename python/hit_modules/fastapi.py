"""FastAPI integration for HIT modules with automatic auth and shared routes."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .auth import enforce_fastapi_auth, require_provisioned_token
from .client import ProvisionerClient
from .errors import ProvisionerConfigError, ProvisionerError
from .logger import get_logger
from .middleware import get_module_config, get_module_secrets
from .version import get_module_version, log_module_startup

logger = get_logger(__name__)

# Public router for routes that don't require authentication (K8s probes, monitoring)
_public_router = APIRouter(prefix="/hit", tags=["hit"])

# Authenticated router for routes that require bearer tokens
_auth_router = APIRouter(prefix="/hit", tags=["hit"])


@_public_router.get("/health")
def hit_health_check() -> dict[str, Any]:
    """Health check endpoint that verifies basic module status.
    
    This endpoint:
    - Returns module name and basic status
    - Does NOT require authentication (for K8s probes)
    - Does NOT verify provisioner connectivity (to avoid probe failures)
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    return {
        "status": "healthy",
        "module": module_name,
    }


@_public_router.get("/version")
def hit_version() -> dict[str, Any]:
    """Get module version information.
    
    Does NOT require authentication (for monitoring/debugging).
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    version = get_module_version(module_name)
    return {
        "module": module_name,
        "version": version,
    }


@_auth_router.get("/config")
def hit_config(
    config: dict[str, Any] = Depends(get_module_config),
    secrets: dict[str, Any] = Depends(get_module_secrets),
    claims: dict[str, Any] = Depends(require_provisioned_token),
) -> dict[str, Any]:
    """Get module configuration (requires authentication).
    
    Returns:
    - Module settings (from hit.yaml)
    - Config source (provisioner)
    - Does NOT expose secrets (only indicates if present)
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    settings = config.get("settings", {})
    
    return {
        "module": module_name,
        "config_source": "provisioner",
        "settings": settings,
        "has_secrets": bool(secrets),
        "authenticated_as": claims.get("project_slug"),
    }


@_auth_router.get("/provisioner")
def hit_provisioner_status(
    claims: dict[str, Any] = Depends(require_provisioned_token),
) -> dict[str, Any]:
    """Check provisioner connectivity and authentication status.
    
    Requires authentication to verify token validity.
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    
    try:
        client = ProvisionerClient()
        # Try a simple operation to verify connectivity
        # We'll just verify the client can be created (it validates config on init)
        status_info = {
            "module": module_name,
            "provisioner_configured": True,
            "authenticated": True,
            "project_slug": claims.get("project_slug"),
            "environment": claims.get("environment"),
        }
    except ProvisionerConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Provisioner misconfigured: {exc}",
        ) from exc
    except ProvisionerError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Provisioner unreachable: {exc}",
        ) from exc
    
    return status_info


def install_hit_modules(
    app: FastAPI,
    *,
    enforce_auth: bool = True,
    include_routes: bool = True,
    cors_origins: list[str] | None = None,
) -> None:
    """Install HIT modules middleware and routes on a FastAPI app.
    
    This function:
    - Enforces bearer token authentication on all routes (unless disabled)
    - Adds shared HIT routes (/hit/health, /hit/version, /hit/config, /hit/provisioner)
    - Configures CORS (optional)
    - Logs module startup
    
    Args:
        app: FastAPI application instance
        enforce_auth: If True, require bearer token auth on all routes (default: True)
        include_routes: If True, mount shared HIT routes (default: True)
        cors_origins: List of allowed CORS origins. If None, CORS is not configured.
                     If empty list [], allows all origins.
    
    Usage:
        from fastapi import FastAPI
        from hit_modules.fastapi import install_hit_modules
        
        app = FastAPI(title="My Module")
        install_hit_modules(app)
        
        # Your routes here - auth is automatically enforced
        @app.get("/my-endpoint")
        def my_endpoint():
            return {"message": "Hello"}
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    version = get_module_version(module_name)
    
    # Log startup
    log_module_startup(module_name, version)
    
    # Mount shared routes BEFORE enforcing auth (so public routes don't inherit auth requirement)
    if include_routes:
        # Mount public routes first (these won't require auth)
        app.include_router(_public_router)
        logger.info("Public HIT routes mounted: /hit/health, /hit/version")
    
    # Enforce authentication (unless disabled)
    # This adds a dependency to app.router, which affects routes registered AFTER this point
    if enforce_auth:
        enforce_fastapi_auth(app)
        logger.info("Bearer token authentication enforced for all routes")
    
    # Mount authenticated routes AFTER enforcing auth (so they inherit the auth requirement)
    if include_routes:
        app.include_router(_auth_router)
        logger.info("Authenticated HIT routes mounted: /hit/config, /hit/provisioner")
    
    # Configure CORS if requested
    if cors_origins is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins if cors_origins else ["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        logger.info("CORS middleware configured")


def create_hit_app(
    title: str | None = None,
    description: str | None = None,
    version: str | None = None,
    *,
    enforce_auth: bool = True,
    include_routes: bool = True,
    cors_origins: list[str] | None = None,
    **fastapi_kwargs: Any,
) -> FastAPI:
    """Create a FastAPI app pre-configured with HIT modules middleware.
    
    This is a convenience factory that creates a FastAPI app and automatically
    calls install_hit_modules() on it. Use this instead of FastAPI() directly
    for zero-configuration HIT modules.
    
    Args:
        title: App title (defaults to module name)
        description: App description
        version: App version (defaults to detected module version)
        enforce_auth: If True, require bearer token auth (default: True)
        include_routes: If True, mount shared HIT routes (default: True)
        cors_origins: CORS origins (None = no CORS, [] = allow all)
        **fastapi_kwargs: Additional arguments passed to FastAPI()
    
    Returns:
        Configured FastAPI app instance
    
    Usage:
        from hit_modules.fastapi import create_hit_app
        
        app = create_hit_app(title="My Module")
        
        # Your routes here - auth and routes are already configured
        @app.get("/my-endpoint")
        def my_endpoint():
            return {"message": "Hello"}
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    detected_version = get_module_version(module_name)
    
    # Set defaults
    if title is None:
        title = f"HIT {module_name.replace('-', ' ').replace('_', ' ').title()} Service"
    if version is None:
        version = detected_version
    
    # Create app
    app = FastAPI(
        title=title,
        description=description,
        version=version,
        **fastapi_kwargs,
    )
    
    # Install HIT modules
    install_hit_modules(
        app,
        enforce_auth=enforce_auth,
        include_routes=include_routes,
        cors_origins=cors_origins,
    )
    
    return app

