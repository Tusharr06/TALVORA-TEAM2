"""Identity and Authentication REST Endpoints."""

import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import cast

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from talvora.config import Settings, get_settings
from talvora.modules.identity.api.dependencies import (
    get_current_actor,
    get_current_actor_optional,
    get_db_session,
    get_session_service,
)
from talvora.modules.identity.api.dto import CsrfResponse, LoginResponse, MeResponse
from talvora.modules.identity.application.session_service import SessionService
from talvora.modules.identity.domain.models import LoginTransaction, User
from talvora.modules.identity.infrastructure.oidc import (
    CognitoOidcClient,
    MockOidcClient,
    OidcClient,
)
from talvora.modules.organizations.application.service import OrganizationService
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.security import (
    generate_csrf_token,
    generate_token_256,
    is_safe_redirect_url,
)
from talvora.shared.transport.problems import ProblemException

router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_oidc_client(request: Request, settings: Settings = Depends(get_settings)) -> OidcClient:
    """Dependency returning configured OIDC provider."""
    # Check if app state has an injected mock/test client
    custom_client = getattr(request.app.state, "oidc_client", None)
    if custom_client is not None:
        return cast(OidcClient, custom_client)

    if settings.allow_dev_identity_adapter:
        return MockOidcClient(issuer=settings.oidc_issuer, client_id=settings.oidc_client_id)
    return CognitoOidcClient(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
    )


@router.get("/login", response_model=LoginResponse)
def login(
    request: Request,
    redirect_target: str = Query(default="/", description="Safe relative target path after login."),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db_session),
    oidc: OidcClient = Depends(get_oidc_client),
) -> LoginResponse:
    """Initiates OIDC Authorization Code Flow with PKCE."""
    safe_target = redirect_target if is_safe_redirect_url(redirect_target) else "/"
    state = generate_token_256()
    nonce = generate_token_256()
    code_verifier = generate_token_256()
    now = datetime.now(UTC)

    # Persist login transaction with 10-minute expiry
    tx = LoginTransaction(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_target=safe_target,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(tx)
    db.commit()

    # Formulate redirect URI
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/v1/auth/callback"
    auth_url = oidc.get_authorization_url(
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
    )
    # The mock provider stores its one-time grant in memory and returns an issuer
    # that is not a reachable identity server. In local development, send the browser
    # back to this API with the same state and PKCE query so the callback can finish.
    if settings.allow_dev_identity_adapter and isinstance(oidc, MockOidcClient):
        parsed = urllib.parse.urlparse(auth_url)
        auth_url = f"{base_url}/api/v1/auth/dev-authorize?{parsed.query}"
    return LoginResponse(authorization_url=auth_url)


@router.get("/dev-authorize", include_in_schema=False)
def dev_authorize(
    request: Request,
    state: str = Query(..., description="OIDC state token from the local login transaction."),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Complete the local mock login by redirecting to the real callback.

    Disabled whenever the development identity adapter is off, including staging
    and production, where that adapter cannot be configured.
    """
    if not settings.allow_dev_identity_adapter:
        raise ProblemException(
            status_code=404,
            title="Not Found",
            code="NOT_FOUND",
            detail="Development authorization is disabled.",
        )
    code = f"mock_code_{state[:16]}"
    base_url = str(request.base_url).rstrip("/")
    target = (
        f"{base_url}/api/v1/auth/callback"
        f"?code={urllib.parse.quote(code)}&state={urllib.parse.quote(state)}"
    )
    return RedirectResponse(url=target, status_code=302)


@router.get("/callback")
def callback(
    request: Request,
    response: Response,
    code: str = Query(..., description="OIDC authorization code"),
    state: str = Query(..., description="OIDC state token"),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
    oidc: OidcClient = Depends(get_oidc_client),
) -> RedirectResponse:
    """Exchanges OIDC authorization code for tokens, provisions identity, and sets session cookie."""
    now = datetime.now(UTC)
    stmt = sa.select(LoginTransaction).where(LoginTransaction.state == state)
    tx = db.scalar(stmt)

    if tx is None:
        raise ProblemException(
            status_code=400,
            title="Invalid State",
            code="OIDC_STATE_INVALID",
            detail="OIDC state is invalid or has already been used.",
        )

    exp = tx.expires_at if tx.expires_at.tzinfo else tx.expires_at.replace(tzinfo=UTC)
    if exp <= now:
        db.delete(tx)
        db.commit()
        raise ProblemException(
            status_code=400,
            title="Login Expired",
            code="OIDC_TRANSACTION_EXPIRED",
            detail="Login transaction has expired. Please initiate login again.",
        )

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/v1/auth/callback"

    # Exchange code via OIDC provider
    tokens = oidc.exchange_code(
        code=code,
        code_verifier=tx.code_verifier,
        redirect_uri=redirect_uri,
        expected_nonce=tx.nonce,
    )

    # Provision or update physical User identity
    user_stmt = sa.select(User).where(User.issuer == tokens.issuer, User.subject == tokens.subject)
    user = db.scalar(user_stmt)
    if user is None:
        user = User(
            issuer=tokens.issuer,
            subject=tokens.subject,
            email=tokens.email,
            email_verified=tokens.email_verified,
        )
        db.add(user)
        db.flush()
    else:
        user.email = tokens.email
        user.email_verified = tokens.email_verified
        db.flush()

    # Determine default tenant if user has active memberships
    mem_stmt = (
        sa.select(Membership)
        .where(Membership.user_id == user.id, Membership.status == "ACTIVE")
        .order_by(Membership.created_at.asc())
    )
    first_mem = db.scalar(mem_stmt)
    default_tenant_id = first_mem.tenant_id if first_mem else None

    # Create server-managed opaque session (Session Fixation Defense: Always a fresh token)
    user_agent = request.headers.get("user-agent")
    client_ip = request.client.host if request.client else None
    auth_session, raw_token = session_service.create_session(
        db_session=db,
        user_id=user.id,
        tenant_id=default_tenant_id,
        user_agent=user_agent,
        ip_address=client_ip,
    )

    redirect_target = tx.redirect_target
    db.delete(tx)
    db.commit()

    # Issue secure HTTP-only session cookie
    cookie_name = settings.session_cookie_name
    redirect_resp = RedirectResponse(url=redirect_target, status_code=302)
    redirect_resp.set_cookie(
        key=cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_absolute_timeout_hours * 3600,
    )
    return redirect_resp


@router.get("/me", response_model=MeResponse)
def get_me(
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> MeResponse:
    """Returns authenticated user profile, active tenant, and granted capabilities."""
    org_service = OrganizationService()
    user_id = actor.user_id
    email = actor.principal.email if actor.principal else ""

    orgs = org_service.list_user_organizations(db, user_id)
    active_tenant_id = actor.tenant_id
    role = actor.membership.role if actor.membership else None
    capabilities = sorted(actor.membership.capabilities) if actor.membership else []

    return MeResponse(
        user_id=user_id,
        email=email,
        active_organization_id=active_tenant_id,
        role=role,
        capabilities=capabilities,
        organizations=orgs,
    )


@router.get("/csrf", response_model=CsrfResponse)
def get_csrf(
    request: Request,
    actor: ActorContext = Depends(get_current_actor),
) -> CsrfResponse:
    """Generates an anti-CSRF token cryptographically bound to the active session."""
    auth_session = getattr(request.state, "auth_session", None)
    if auth_session is None:
        raise ProblemException(
            status_code=401,
            title="Unauthorized",
            code="UNAUTHENTICATED",
            detail="Session record not found.",
        )

    token = generate_csrf_token(auth_session.csrf_secret, str(auth_session.id))
    return CsrfResponse(csrf_token=token)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
    actor: ActorContext | None = Depends(get_current_actor_optional),
) -> dict[str, str]:
    """Revokes the active session and clears the session cookie."""
    cookie_name = settings.session_cookie_name
    raw_token = request.cookies.get(cookie_name)
    if not raw_token:
        raw_token = request.cookies.get(f"__Host-{cookie_name}")
    if not raw_token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            raw_token = auth_hdr[7:].strip()

    if raw_token:
        session_service.revoke_session(db, raw_token)
        db.commit()

    response.delete_cookie(key=cookie_name, path="/")
    response.delete_cookie(key=f"__Host-{cookie_name}", path="/")
    return {"message": "Logged out successfully"}
