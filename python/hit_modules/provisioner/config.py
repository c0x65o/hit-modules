"""Environment-driven configuration for the provisioner service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except ValueError:
        return default


@dataclass(frozen=True)
class ProvisionerSettings:
    """Configuration container."""

    host: str = "0.0.0.0"
    port: int = 8700
    token_secret: str = "local-dev-secret"
    token_algorithm: str = "HS256"
    expected_audience: str = "hit-provisioner"
    allow_anonymous: bool = False
    secrets_path: Path | None = None
    default_db_url: str | None = None

    @classmethod
    def from_env(cls) -> "ProvisionerSettings":
        host = os.environ.get("PROVISIONER_HOST", cls.host)
        port = _read_int(os.environ.get("PROVISIONER_PORT"), cls.port)
        token_secret = os.environ.get("PROVISIONER_TOKEN_SECRET", cls.token_secret)
        token_algorithm = os.environ.get("PROVISIONER_TOKEN_ALG", cls.token_algorithm)
        expected_audience = os.environ.get(
            "PROVISIONER_TOKEN_AUDIENCE", cls.expected_audience
        )
        allow_anonymous = _read_bool(
            os.environ.get("PROVISIONER_ALLOW_ANONYMOUS"), cls.allow_anonymous
        )
        secrets_path_raw = os.environ.get("PROVISIONER_SECRETS_PATH")
        secrets_path = Path(secrets_path_raw).expanduser() if secrets_path_raw else None
        default_db_url = os.environ.get("HIT_PROVISIONER_DEFAULT_DB_URL")

        return cls(
            host=host,
            port=port,
            token_secret=token_secret,
            token_algorithm=token_algorithm,
            expected_audience=expected_audience,
            allow_anonymous=allow_anonymous,
            secrets_path=secrets_path,
            default_db_url=default_db_url,
        )

