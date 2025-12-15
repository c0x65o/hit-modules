"""FastAPI integration for HIT modules with automatic auth and shared routes."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from .auth import _enforce_fastapi_auth, require_provisioned_token
from .client import ProvisionerClient
from .errors import ProvisionerConfigError, ProvisionerError
from .logger import get_logger
from .middleware import clear_config_cache, get_module_config, get_module_config_from_request, get_module_secrets
from .version import get_module_version, log_module_startup

logger = get_logger(__name__)


class _HitFormatter(logging.Formatter):
    """Custom formatter that cleans up confusing logger names.
    
    Renames 'uvicorn.error' to 'uvicorn' since the '.error' doesn't mean
    error-level logs - it's just Uvicorn's confusing naming for the stderr stream.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        # Clean up confusing uvicorn logger names
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return super().format(record)


def _configure_uvicorn_logging() -> None:
    """Configure Uvicorn loggers to use HIT standard format.
    
    This must be called during FastAPI startup event, after Uvicorn has
    configured its own loggers. Otherwise Uvicorn will overwrite our config.
    """
    level = os.environ.get("HIT_MODULES_LOG_LEVEL", "INFO").upper()
    
    formatter = _HitFormatter(
        fmt="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    
    uvicorn_loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for logger_name in uvicorn_loggers:
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(handler)
        uvicorn_logger.setLevel(level)
        uvicorn_logger.propagate = False

# Public router for routes that don't require authentication (K8s probes, monitoring)
_public_router = APIRouter(prefix="/hit", tags=["hit"])

# Authenticated router for routes that require bearer tokens
_auth_router = APIRouter(prefix="/hit", tags=["hit"])


def _health_check_response() -> dict[str, Any]:
    """Shared health check response."""
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    return {
        "status": "healthy",
        "module": module_name,
    }

@_public_router.get("/healthz")
def hit_health_check() -> dict[str, Any]:
    """Health check endpoint that verifies basic module status.
    
    This endpoint:
    - Returns module name and basic status
    - Does NOT require authentication (for K8s probes)
    - Does NOT verify provisioner connectivity (to avoid probe failures)
    """
    return _health_check_response()


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
async def hit_config(
    request: Request,
    claims: dict[str, Any] = Depends(require_provisioned_token),
) -> dict[str, Any]:
    """Get module configuration (requires authentication).
    
    Uses the request's token to fetch project-specific config from provisioner.
    
    Returns:
    - Full module config from hit.yaml (name, version, namespace, settings, etc.)
    - Config source (provisioner)
    - Does NOT expose secrets (only indicates if present)
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    
    # Get config using request token for K8s dynamic lookup
    config = await get_module_config_from_request(request)
    secrets = config.get("secrets", {})
    
    # Return full module config, not just settings
    # Filter out secrets block from the config for security
    config_without_secrets = {k: v for k, v in config.items() if k != "secrets"}
    
    # Build authenticated_as: show "project/service" if service token, else just project
    project = claims.get("prj")
    service = claims.get("svc")
    if project and service:
        authenticated_as = f"{project}/{service}"
    else:
        authenticated_as = project
    
    return {
        "module": module_name,
        "config_source": "provisioner",
        "settings": config_without_secrets.get("settings", {}),
        "has_secrets": bool(secrets),
        "authenticated_as": authenticated_as,
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
        # Use require_token=False since shared modules don't have their own token
        client = ProvisionerClient(require_token=False)
        # Verify connectivity with a health check
        provisioner_healthy = client.ping()
        status_info = {
            "module": module_name,
            "provisioner_configured": True,
            "provisioner_healthy": provisioner_healthy,
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


@_auth_router.post("/reload")
async def hit_reload_config(
    request: Request,
    claims: dict[str, Any] = Depends(require_provisioned_token),
) -> dict[str, Any]:
    """Reload module configuration from provisioner.
    
    Clears the local config cache and fetches fresh config from the provisioner.
    Call this after updating hit.yaml and reloading the provisioner.
    
    Requires authentication.
    """
    module_name = os.getenv("HIT_MODULE_NAME", "unknown")
    
    # Clear the config cache
    clear_config_cache()
    
    # Fetch fresh config using request token
    try:
        config = await get_module_config_from_request(request)
        settings = config.get("settings", {})
        return {
            "status": "ok",
            "module": module_name,
            "message": "Configuration reloaded",
            "settings": settings,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to reload config: {exc}",
        ) from exc


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
    - Adds shared HIT routes (/hit/healthz, /hit/version, /hit/config, /hit/provisioner)
    - Configures CORS (optional)
    - Logs module startup
    - Configures Uvicorn loggers to use standard HIT format
    
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
    
    # Add startup event to configure Uvicorn logging after Uvicorn has set up its loggers
    @app.on_event("startup")
    async def configure_logging_on_startup() -> None:
        _configure_uvicorn_logging()
    
    # Log startup
    log_module_startup(module_name, version)
    
    # Mount shared routes BEFORE enforcing auth (so public routes don't inherit auth requirement)
    if include_routes:
        # Add root-level /healthz for K8s probes (standardized health endpoint)
        @app.get("/healthz")
        def root_healthz() -> dict[str, Any]:
            """Root-level health check endpoint for Kubernetes probes."""
            return _health_check_response()
        
        # Mount public routes first (these won't require auth)
        app.include_router(_public_router)
        logger.info("Public HIT routes mounted: /healthz, /hit/healthz, /hit/version")
    
    # Enforce authentication (unless disabled)
    # This adds a dependency to app.router, which affects routes registered AFTER this point
    if enforce_auth:
        _enforce_fastapi_auth(app)
        logger.info("Bearer token authentication enforced for all routes")
    
    # Mount authenticated routes AFTER enforcing auth (so they inherit the auth requirement)
    if include_routes:
        app.include_router(_auth_router)
        logger.info("Authenticated HIT routes mounted: /hit/config, /hit/provisioner, /hit/reload")
    
    # Configure CORS if requested
    if cors_origins is not None:
        # When specific origins are provided, allow credentials
        # When empty list (allow all), credentials must be false per CORS spec
        allow_creds = bool(cors_origins)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins if cors_origins else ["*"],
            allow_credentials=allow_creds,
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

