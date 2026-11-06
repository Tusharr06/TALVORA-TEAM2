"""FastAPI Endpoints for Google Integrations and Delegated OAuth Connections."""

import urllib.parse
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from talvora.config import Settings, get_settings
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.integrations.api.dto import (
    GoogleAuthorizeRequest,
    GoogleAuthorizeResponse,
    IntegrationConnectionResponse,
    IntegrationListResponse,
)
from talvora.modules.integrations.application.ports import GoogleOAuthClient
from talvora.modules.integrations.application.service import IntegrationService
from talvora.modules.integrations.domain.errors import IntegrationError
from talvora.modules.integrations.domain.models import IntegrationConnection
from talvora.modules.integrations.infrastructure.google.oauth_client import (
    GoogleOAuthHttpClient,
    MockGoogleOAuthClient,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])


def get_google_oauth_client(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> GoogleOAuthClient:
    """Dependency resolving active GoogleOAuthClient (configured mock in dev/test or production HTTP client)."""
    custom_client: GoogleOAuthClient | None = getattr(
        request.app.state, "google_oauth_client", None
    )
    if custom_client is not None:
        return custom_client

    if settings.allow_mock_google_adapter and settings.environment in ("local", "test"):
        mock_client = MockGoogleOAuthClient(
            client_id=settings.google_client_id or "mock-google-client-id",
            client_secret=settings.google_client_secret or "mock-google-client-secret",
        )
        request.app.state.google_oauth_client = mock_client
        return mock_client

    if not settings.google_client_id or not settings.google_client_secret:
        raise IntegrationError(
            status_code=500,
            detail="Google OAuth client credentials are not configured.",
        )

    return GoogleOAuthHttpClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        auth_endpoint=settings.google_auth_endpoint,
        token_endpoint=settings.google_token_endpoint,
        userinfo_endpoint=settings.google_userinfo_endpoint,
    )


@router.post(
    "/google/authorize",
    response_model=GoogleAuthorizeResponse,
    status_code=status.HTTP_200_OK,
    summary="Initiate Google OAuth Authorization",
    operation_id="initiate_google_authorize",
)
def google_authorize(
    payload: GoogleAuthorizeRequest,
    request: Request,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    oauth_client: GoogleOAuthClient = Depends(get_google_oauth_client),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> GoogleAuthorizeResponse:
    """Creates a cryptographically bound OAuth transaction and returns the Google consent URL."""
    # Validate redirect URI against allowlisted redirect hosts
    redirect_uri = payload.redirect_uri or settings.google_redirect_uri
    parsed = urllib.parse.urlparse(redirect_uri)
    if (
        parsed.hostname not in settings.allowed_redirect_hosts
        and parsed.hostname not in settings.allowed_hosts
    ):
        raise IntegrationError(
            status_code=400,
            detail=f"Redirect URI host '{parsed.hostname}' is not in the configured allowlist.",
        )

    auth_session = getattr(request.state, "auth_session", None)
    session_binding = (
        str(auth_session.id) if auth_session and hasattr(auth_session, "id") else "default_session"
    )

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        auth_url, state, expires_at = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=payload.requested_capabilities,
            redirect_uri=redirect_uri,
            session_binding=session_binding,
        )

    return GoogleAuthorizeResponse(
        authorization_url=auth_url,
        state=state,
        expires_at=expires_at,
    )


@router.get(
    "/google/callback",
    response_model=IntegrationConnectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Google OAuth Callback Handler",
    operation_id="google_oauth_callback",
)
async def google_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    oauth_client: GoogleOAuthClient = Depends(get_google_oauth_client),
) -> IntegrationConnectionResponse:
    """Validates returned state, exchanges code for credentials, and encrypts tokens before persistence."""
    auth_session = getattr(request.state, "auth_session", None)
    session_binding = (
        str(auth_session.id) if auth_session and hasattr(auth_session, "id") else "default_session"
    )

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        conn = await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=settings.encryption_key,
            raw_state=state,
            code=code,
            error=error,
            error_description=error_description,
            session_binding=session_binding,
        )
        return IntegrationConnectionResponse.model_validate(conn)


@router.get(
    "",
    response_model=IntegrationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Organization Integrations",
    operation_id="list_integrations",
)
def list_integrations(
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> IntegrationListResponse:
    """Returns safe connection metadata. Admins see tenant-wide connections; members see their own."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        connections = IntegrationService.list_integrations(uow=uow, actor=actor)
        return IntegrationListResponse(
            items=[IntegrationConnectionResponse.model_validate(c) for c in connections],
            total_count=len(connections),
        )


@router.delete(
    "/{id}",
    response_model=IntegrationConnectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Disconnect an Integration",
    operation_id="disconnect_integration",
)
async def disconnect_integration(
    connection_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    oauth_client: GoogleOAuthClient = Depends(get_google_oauth_client),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> IntegrationConnectionResponse:
    """Revokes Google credentials where possible and zeroes out stored token material."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        conn = await IntegrationService.disconnect_integration(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=settings.encryption_key,
            connection_id=connection_id,
        )
        return IntegrationConnectionResponse.model_validate(conn)


@router.post(
    "/{id}/reconnect",
    response_model=GoogleAuthorizeResponse,
    status_code=status.HTTP_200_OK,
    summary="Initiate Reconnection for an Existing Integration",
    operation_id="reconnect_integration",
)
def reconnect_integration(
    request: Request,
    connection_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    oauth_client: GoogleOAuthClient = Depends(get_google_oauth_client),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> GoogleAuthorizeResponse:
    """Generates an explicit consent OAuth URL to refresh scopes and force a new refresh token."""
    redirect_uri = settings.google_redirect_uri
    auth_session = getattr(request.state, "auth_session", None)
    session_binding = (
        str(auth_session.id) if auth_session and hasattr(auth_session, "id") else "default_session"
    )

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        # Verify connection belongs to user/tenant
        conn = uow.session.get(
            IntegrationConnection,
            {"tenant_id": uow.tenant_id, "id": connection_id},
        )
        if conn is None:
            raise IntegrationError(
                status_code=404,
                title="Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="Integration connection not found.",
            )

        auth_url, state, expires_at = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri=redirect_uri,
            prompt="consent",
            session_binding=session_binding,
        )

    return GoogleAuthorizeResponse(
        authorization_url=auth_url,
        state=state,
        expires_at=expires_at,
    )
