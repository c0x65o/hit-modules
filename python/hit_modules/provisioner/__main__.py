"""CLI entrypoint for running the provisioner service."""

from __future__ import annotations

import uvicorn

from .config import ProvisionerSettings
from .server import create_app


def main() -> None:
    settings = ProvisionerSettings.from_env()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()

