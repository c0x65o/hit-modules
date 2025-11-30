"""Simple module-level logger helper."""

import logging
import os


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger with sane defaults."""

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    level = os.environ.get("HIT_MODULES_LOG_LEVEL", "INFO").upper()
    logger.setLevel(level)
    return logger

