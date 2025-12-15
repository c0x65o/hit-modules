"""Centralized logger configuration for all HIT modules.

This module provides a standardized logging setup with timestamps and
consistent formatting across all HIT modules.
"""

import logging
import os
import sys
from typing import Optional

# Track if root logger has been configured
_root_logger_configured = False


def configure_root_logger(level: Optional[str] = None) -> None:
    """Configure the root logger with standard formatting.

    This should be called once at application startup. Subsequent calls
    are idempotent.

    Also configures Uvicorn loggers to use the same format for consistent
    log output across all HIT modules.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               If None, reads from HIT_MODULES_LOG_LEVEL env var or defaults to INFO.
    """
    global _root_logger_configured

    if _root_logger_configured:
        return

    if level is None:
        level = os.environ.get("HIT_MODULES_LOG_LEVEL", "INFO").upper()

    # Format with timestamp (including milliseconds), level, logger name, and message
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Create console handler with detailed formatting
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Configure Uvicorn loggers to use the same format
    # This ensures consistent timestamps across all log output
    uvicorn_loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for logger_name in uvicorn_loggers:
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(handler)
        uvicorn_logger.setLevel(level)
        uvicorn_logger.propagate = False

    _root_logger_configured = True


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return a configured logger with standardized formatting.

    This function ensures all loggers use consistent formatting with timestamps.
    The root logger is configured automatically on first call.

    Args:
        name: Logger name (typically __name__ of the calling module)
        level: Optional log level override. If None, uses root logger level.

    Returns:
        Configured logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Module started")
        2024-01-15 10:30:45.123 | INFO     | my_module.main | Module started
    """
    # Configure root logger if not already done
    if not _root_logger_configured:
        configure_root_logger(level)

    logger = logging.getLogger(name)

    # Set level if provided, otherwise inherit from root
    if level is not None:
        logger.setLevel(level.upper())

    return logger
