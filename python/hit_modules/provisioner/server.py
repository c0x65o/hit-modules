"""FastAPI application for the provisioner service."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from .auth import build_auth_dependency
from .config import ProvisionerSettings
from .secret_store import SecretStore


class DatabaseSecretRequest(BaseModel):
    namespace: str = Field(..., description="Logical namespace (e.g., shared)")
    secretKey: str = Field(default="auth-db", description="Secret key name")
    role: str | None = Field(
        default=None,
        description="Optional role hint (writer, reader, etc.)",
    )


def create_app(settings: ProvisionerSettings | None = None) -> FastAPI:
    settings = settings or ProvisionerSettings.from_env()
    store = SecretStore(settings)
    require_token = build_auth_dependency(settings)

    app = FastAPI(
        title="HIT Provisioner",
        version="0.1.0",
        description="Brokers secrets/tokens for shared HIT modules.",
    )

    @app.get("/healthz")
    def healthcheck():
        return {
            "status": "ok",
            "allowAnonymous": settings.allow_anonymous,
            "secretsPath": str(settings.secrets_path) if settings.secrets_path else None,
        }

    @app.post("/api/v1/secrets/database")
    def fetch_database_secret(
        request: DatabaseSecretRequest,
        claims: dict = Depends(require_token),
    ):
        del claims  # reserved for future use
        secret = store.get_database_secret(
            namespace=request.namespace,
            secret_key=request.secretKey,
            role=request.role,
        )
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Database secret not found",
            )
        return secret

    return app

