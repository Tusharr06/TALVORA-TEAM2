"""Pydantic DTOs for Integrations Endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GoogleAuthorizeRequest(BaseModel):
    requested_capabilities: list[str] | None = Field(
        default=None,
        description="Optional specific capability keys: freebusy, calendar_events, gmail_send.",
    )
    redirect_uri: str | None = Field(
        default=None,
        description="Optional allowlisted redirect URI override.",
    )


class GoogleAuthorizeResponse(BaseModel):
    authorization_url: str = Field(description="Google OAuth 2.0 authorization URL.")
    state: str = Field(description="Cryptographic OAuth state string.")
    expires_at: datetime = Field(description="State expiration timestamp.")


class IntegrationConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    owner_membership_id: UUID
    provider: str
    provider_subject: str
    verified_email: str
    granted_scopes: list[str]
    connection_status: str
    token_expiry: datetime
    last_error_code: str | None = None
    connected_at: datetime
    disconnected_at: datetime | None = None


class IntegrationListResponse(BaseModel):
    items: list[IntegrationConnectionResponse]
    total_count: int
