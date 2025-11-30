# hit-modules

Shared client-side utilities for HIT microservices. This repo contains:

- **Python client library** (`python/hit_modules`) for modules to:
  - Fetch secrets/tokens from the provisioner service
  - Get module configuration from `hit.yaml` via the provisioner
  - Connect to databases using provisioner-managed secrets
  - Enforce authentication via bearer tokens
  - Standardize version detection and logging
  - **Auto-register shared routes** (`/hit/health`, `/hit/version`, `/hit/config`, `/hit/provisioner`)

## Quick Start (Recommended)

Use `create_hit_app()` for zero-configuration HIT modules:

```python
from hit_modules.fastapi import create_hit_app
from hit_modules.middleware import get_module_config

# Creates FastAPI app with auth + shared routes pre-configured
app = create_hit_app(
    title="My Module",
    description="My awesome HIT module",
    cors_origins=[],  # Empty list = allow all origins
)

# Your routes here - auth is automatically enforced
@app.get("/my-endpoint")
def my_endpoint(config: dict[str, Any] = Depends(get_module_config)):
    increment = config.get("settings", {}).get("increment", 1)
    return {"increment": increment}
```

**What you get automatically:**
- ✅ Bearer token authentication enforced on all routes
- ✅ Shared routes: `/hit/health`, `/hit/version`, `/hit/config`, `/hit/provisioner`
- ✅ Module startup logging
- ✅ Version detection

## Alternative: Install on Existing App

If you already have a FastAPI app, use `install_hit_modules()`:

```python
from fastapi import FastAPI
from hit_modules.fastapi import install_hit_modules
from hit_modules.middleware import get_module_config

app = FastAPI(title="My Module")

# Install HIT modules middleware and routes
install_hit_modules(
    app,
    enforce_auth=True,      # Require bearer tokens (default: True)
    include_routes=True,     # Add shared routes (default: True)
    cors_origins=[],         # CORS config (None = no CORS, [] = allow all)
)

@app.get("/my-endpoint")
def my_endpoint(config: dict[str, Any] = Depends(get_module_config)):
    ...
```

## Legacy Pattern (Still Supported)

The old manual pattern still works for backward compatibility:

```python
from hit_modules.auth import enforce_fastapi_auth
from hit_modules.middleware import get_module_config
from hit_modules.version import get_module_version, log_module_startup

app = FastAPI(title="My Module")
enforce_fastapi_auth(app)

__version__ = get_module_version(module_name="my-module")
log_module_startup(module_name="my-module", version=__version__)

@app.get("/endpoint")
def my_endpoint(config: dict[str, Any] = Depends(get_module_config)):
    ...
```

## Shared Routes

All modules using `create_hit_app()` or `install_hit_modules()` automatically get these routes:

- **`GET /hit/health`** - Health check (no auth required, for K8s probes)
- **`GET /hit/version`** - Module version info (no auth required)
- **`GET /hit/config`** - Module configuration (requires auth)
- **`GET /hit/provisioner`** - Provisioner connectivity status (requires auth)

These routes are automatically available in all HIT modules without any code changes.

## Provisioner Service

The provisioner server is in a separate repository: [`hit-module-provisioner`](https://github.com/c0x65o/hit-module-provisioner)

## Tests

```
uv run --extra dev pytest
```
