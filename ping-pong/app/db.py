"""Filesystem-based counter storage for ping-pong service."""

import json
import os
from pathlib import Path
from threading import Lock

# Storage directory for counter files
STORAGE_DIR = Path(os.getenv("PING_PONG_STORAGE_DIR", "/tmp/ping-pong-counters"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# lock
_file_lock = Lock()


def _get_counter_path(counter_id: str) -> Path:
    """Get the file path for a counter ID."""
    # Sanitize counter_id to prevent directory traversal
    safe_id = "".join(c for c in counter_id if c.isalnum() or c in "-_")
    return STORAGE_DIR / f"{safe_id}.json"


def get_counter(counter_id: str) -> int:
    """Get current counter value from filesystem.

    Args:
        counter_id: Counter identifier

    Returns:
        Counter value (0 if doesn't exist)
    """
    counter_path = _get_counter_path(counter_id)
    with _file_lock:
        if counter_path.exists():
            try:
                with open(counter_path, "r") as f:
                    data = json.load(f)
                    return data.get("value", 0)
            except (json.JSONDecodeError, IOError):
                return 0
        return 0


def set_counter(counter_id: str, value: int) -> None:
    """Set counter value in filesystem.

    Args:
        counter_id: Counter identifier
        value: Counter value to set
    """
    counter_path = _get_counter_path(counter_id)
    with _file_lock:
        with open(counter_path, "w") as f:
            json.dump({"id": counter_id, "value": value}, f)
