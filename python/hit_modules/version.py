"""Version detection and logging utilities for HIT modules."""

from __future__ import annotations

import os
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


def get_module_version(
    module_name: str | None = None, package_name: str | None = None
) -> str:
    """Get module version from package metadata or pyproject.toml.

    This function:
    1. Tries to read from installed package metadata (production)
    2. Falls back to reading from pyproject.toml (development)
    3. Returns "0.0.0" if version cannot be determined

    Args:
        module_name: Module name (e.g., "ping-pong"). If None, tries to detect from HIT_MODULE_NAME.
        package_name: Package name in pyproject.toml (e.g., "hit-ping-pong").
                     If None, tries to infer from module_name.

    Returns:
        Version string (e.g., "1.2.14")
    """
    # Try to detect module name if not provided
    if not module_name:
        module_name = os.getenv("HIT_MODULE_NAME")

    # Infer package name from module name if not provided
    if not package_name and module_name:
        # Convert module name to package name (e.g., "ping-pong" -> "hit-ping-pong")
        package_name = f"hit-{module_name.replace('_', '-')}"

    # Try reading from installed package metadata first (production)
    if package_name:
        try:
            from importlib.metadata import version as get_package_version

            return get_package_version(package_name)
        except Exception:
            pass

    # Fallback to reading from pyproject.toml (development)
    try:
        import tomllib

        # Strategy: try common locations where pyproject.toml might be
        # 1. Current working directory (most common - module root)
        # 2. Parent of current working directory
        # 3. Walk up from the calling module's file location

        search_paths = [Path.cwd()]

        # Try to find pyproject.toml relative to the caller's file
        import inspect

        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_file = Path(frame.f_back.f_code.co_filename)
            # Walk up from caller's file location
            # e.g., app/main.py -> app/ -> project root/
            for parent in [caller_file.parent, caller_file.parent.parent]:
                if parent not in search_paths:
                    search_paths.append(parent)

        for base_path in search_paths:
            pyproject_path = base_path / "pyproject.toml"
            if pyproject_path.exists():
                try:
                    with open(pyproject_path, "rb") as f:
                        data = tomllib.load(f)
                    project = data.get("project", {})
                    if "version" in project:
                        return str(project["version"])
                except Exception as e:
                    logger.debug(
                        "Could not read version from %s: %s", pyproject_path, e
                    )
                    continue
    except ImportError:
        # tomllib not available (Python < 3.11)
        logger.debug("tomllib not available, cannot read pyproject.toml")
    except Exception as e:
        logger.debug("Error reading version from pyproject.toml: %s", e)

    logger.warning("Could not determine module version, using default '0.0.0'")
    return "0.0.0"


def log_module_startup(
    module_name: str | None = None, version: str | None = None
) -> None:
    """Log module startup message with version.

    This is a convenience function that logs a standardized startup message
    for HIT modules. It detects the module name and version automatically
    if not provided.

    Args:
        module_name: Module name (e.g., "ping-pong"). If None, tries to detect.
        version: Module version. If None, tries to detect automatically.
    """
    if not module_name:
        module_name = os.getenv("HIT_MODULE_NAME", "unknown")

    if not version:
        version = get_module_version(module_name)

    service_name = module_name.replace("-", " ").replace("_", " ").title()
    message = f"Starting Hit {service_name} Service v{version}"

    # Print to stdout for uvicorn/gunicorn logs
    print(f"INFO:     {message}", flush=True)
    # Also log via Python logging
    logger.info(message)
