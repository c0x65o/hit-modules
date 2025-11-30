"""Authentication helpers for the provisioner service."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import ProvisionerSettings


def build_auth_dependency(settings: ProvisionerSettings):
    """Create a dependency that enforces Bearer auth (with optional anonymous mode)."""

    bearer_scheme = HTTPBearer(auto_error=not settings.allow_anonymous)

    async def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> dict:
        if credentials is None:
            if settings.allow_anonymous:
                return {"sub": "anonymous", "ns": "local"}
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
            )

        token = credentials.credentials
        try:
            payload = jwt.decode(
                token,
                settings.token_secret,
                algorithms=[settings.token_algorithm],
                audience=settings.expected_audience,
                options={"verify_signature": True, "verify_aud": True},
            )
            return payload
        except JWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {exc}",
            ) from exc

    return require_token

