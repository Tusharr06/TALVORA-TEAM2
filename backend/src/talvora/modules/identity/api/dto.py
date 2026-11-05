"""Identity and Authentication Data Transfer Objects."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class LoginResponse(BaseModel):
    """OIDC authorization redirect response."""

    authorization_url: str = Field(
        ..., description="OIDC Provider authorization endpoint URL with PKCE challenge."
    )


class MeResponse(BaseModel):
    """Authenticated user profile and active tenant context."""

    user_id: UUID
    email: str
    active_organization_id: UUID | None = None
    role: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    organizations: list[dict[str, Any]] = Field(default_factory=list)


class CsrfResponse(BaseModel):
    """Session anti-CSRF token response."""

    csrf_token: str
