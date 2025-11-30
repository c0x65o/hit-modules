"""Automatic module configuration bootstrap for HIT modules."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .client import ProvisionerClient
from .errors import ProvisionerConfigError, ProvisionerError
from .logger import get_logger

logger = get_logger(__name__)

_module_config_cache: dict[str, dict[str, Any]] = {}
_module_config_loaded = False


def _get_module_name() -> str:
    """Detect module name from environment or best-effort heuristics."""

    module_name = (
        os.getenv("HIT_MODULE_NAME")
        or os.getenv("MODULE_NAME")
        or os.getenv("SERVICE_NAME", "").replace("_", "-")
    )
    if module_name:
        return module_name

    script = Path(sys.argv[0]).stem if sys.argv else ""
    return script or "unknown"


def _inject_env_vars(module_name: str, config: dict[str, Any]) -> None:
    """Inject config/env values so modules can read them like normal env vars."""

    env_block = config.get("env", {})
    if isinstance(env_block, dict):
        for key, value in env_block.items():
            os.environ.setdefault(key, str(value))

    settings = config.get("settings", {})
    if isinstance(settings, dict):
        module_slug = module_name.upper().replace("-", "_")
        for key, value in settings.items():
            env_key = f"HIT_{module_slug}_{key.upper()}"
            os.environ.setdefault(env_key, str(value))


def _load_module_config(module_name: str) -> dict[str, Any]:
    """Fetch module config from the provisioner."""
    client = ProvisionerClient()
    config = client.get_module_config(module_name)
    return config or {}


def ensure_module_config_loaded(force: bool = False) -> None:
    """Ensure config has been pulled from provisioner and injected into env."""

    global _module_config_loaded
    if _module_config_loaded and not force:
        return

    module_name = _get_module_name()
    if module_name == "unknown":
        logger.debug("Module name unknown; set HIT_MODULE_NAME to enable auto config")
        _module_config_loaded = True
        return
    try:
        config = _load_module_config(module_name)
    except ProvisionerConfigError as exc:
        raise RuntimeError(
            f"Provisioner configuration missing for module {module_name}: {exc}"
        ) from exc
    except ProvisionerError as exc:
        raise RuntimeError(
            "Provisioner is required but unreachable; refusing to start module"
        ) from exc

    if config:
        _module_config_cache[module_name] = config
        _inject_env_vars(module_name, config)
        logger.info("Module config loaded for %s", module_name)
    else:
        logger.warning("Provisioner returned no config for module %s", module_name)

    _module_config_loaded = True


def _cast_value(value: str, default: Any) -> Any:
    if isinstance(default, bool):
        return value.lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except ValueError:
            return default
    return value


def get_config_value(key: str, default: Any = None, module_name: str | None = None) -> Any:
    """Read a config value after ensuring module config is loaded."""

    module_name = module_name or _get_module_name()
    env_key = key.upper()
    module_slug = module_name.upper().replace("-", "_")
    module_env_key = f"HIT_{module_slug}_{env_key}" if module_slug else env_key

    if env_key in os.environ:
        return _cast_value(os.environ[env_key], default)
    if module_env_key in os.environ:
        return _cast_value(os.environ[module_env_key], default)

    ensure_module_config_loaded()
    config = _module_config_cache.get(module_name, {})
    settings = config.get("settings", {})
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    return default


def get_module_config(module_name: str | None = None) -> dict[str, Any]:
    """Return the full cached module config (ensuring it's loaded first)."""

    module_name = module_name or _get_module_name()
    ensure_module_config_loaded()
    return _module_config_cache.get(module_name, {}).copy()

