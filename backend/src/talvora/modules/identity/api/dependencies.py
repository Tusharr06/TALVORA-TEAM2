"""FastAPI Authentication and CSRF Dependencies."""

import urllib.parse
from typing import cast
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from talvora.config import Settings, get_settings
from talvora.modules.identity.application.session_service import SessionService
from talvora.modules.identity.domain.roles import get_capabilities_for_role
from talvora.shared.application.actor import ActorContext, MembershipContext, Principal
from talvora.shared.infrastructure.security import verify_csrf_token
from talvora.shared.transport.problems import ProblemException


def get_db_session(request: Request) -> Session:
    """Yield a synchronous DB session from the application DatabaseManager."""
    db_manager = request.app.state.db_manager
    session = db_manager.session_maker()
    try:
        return cast(Session, session)
    except Exception:
        session.close()
        raise


def get_session_service() -> SessionService:
    """SessionService dependency."""
    return SessionService()


def get_current_actor_optional(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ActorContext | None:
    """Extract authenticated actor context from session cookie if present."""
    cookie_name = settings.session_cookie_name
    raw_token = request.cookies.get(cookie_name)
    if not raw_token:
        # Fallback to __Host- prefixed variant
        raw_token = request.cookies.get(f"__Host-{cookie_name}")
    if not raw_token:
        # Also check Authorization: Bearer <token> for test convenience
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            raw_token = auth_hdr[7:].strip()

    if not raw_token:
        return None

    db_manager = request.app.state.db_manager
    db_session: Session = db_manager.session_maker()
    try:
        service = SessionService(
            idle_timeout_minutes=settings.session_idle_timeout_minutes,
            absolute_timeout_hours=settings.session_absolute_timeout_hours,
        )
        resolved = service.resolve_session(db_session, raw_token)
        if resolved is None:
            db_session.rollback()
            return None

        auth_session, user, membership = resolved
        db_session.commit()

        principal = Principal(
            user_id=user.id,
            email=user.email,
            issuer=user.issuer,
            subject=user.subject,
        )

        mem_ctx: MembershipContext | None = None
        if membership is not None:
            caps = get_capabilities_for_role(membership.role)
            mem_ctx = MembershipContext(
                membership_id=membership.id,
                organization_id=membership.tenant_id,
                role=membership.role,  # type: ignore[arg-type]
                capabilities=caps,
            )

        actor = ActorContext(principal=principal, membership=mem_ctx)
        # Store on request state
        request.state.actor = actor
        request.state.auth_session = auth_session
        if actor.tenant_id is not None:
            request.state.tenant_id = actor.tenant_id
        return actor
    finally:
        db_session.close()


def get_current_actor(
    actor: ActorContext | None = Depends(get_current_actor_optional),
) -> ActorContext:
    """Enforce that request holds an authenticated session."""
    if actor is None or not actor.is_authenticated:
        raise ProblemException(
            status_code=401,
            title="Unauthorized",
            code="UNAUTHENTICATED",
            detail="Valid authentication credentials were not provided or session has expired.",
        )
    return actor


def get_active_tenant_id(actor: ActorContext = Depends(get_current_actor)) -> UUID:
    """Enforce that actor is authenticated and has an active tenant selected."""
    if actor.tenant_id is None:
        raise ProblemException(
            status_code=400,
            title="No Active Organization",
            code="NO_ACTIVE_ORGANIZATION",
            detail="Session has no active organization selected. Please select an organization.",
        )
    return actor.tenant_id


def enforce_csrf_and_origin(
    request: Request,
    settings: Settings = Depends(get_settings),
    actor: ActorContext | None = Depends(get_current_actor_optional),
) -> None:
    """Validate Origin header and Anti-CSRF token for state-mutating requests."""
    # Only validate mutating methods
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return

    # Skip CSRF check for public callback/login/accept routes
    path = request.url.path
    if path.startswith("/api/v1/auth/login") or path.startswith("/api/v1/auth/callback"):
        return

    # 1. Origin Header Validation
    origin = request.headers.get("origin")
    if origin:
        parsed_origin = urllib.parse.urlparse(origin)
        origin_host = parsed_origin.hostname
        if origin_host and origin_host not in settings.allowed_hosts:
            raise ProblemException(
                status_code=403,
                title="Forbidden Origin",
                code="ORIGIN_FORBIDDEN",
                detail=f"Origin '{origin}' is not permitted to mutate resources.",
            )

    # 2. CSRF Token Verification for authenticated sessions
    actor = getattr(request.state, "actor", None)
    auth_session = getattr(request.state, "auth_session", None)
    if actor is not None and auth_session is not None:
        csrf_token = request.headers.get("X-CSRF-Token")
        if not csrf_token:
            csrf_token = request.headers.get("x-csrf-token")

        if not csrf_token:
            raise ProblemException(
                status_code=403,
                title="Missing CSRF Token",
                code="CSRF_TOKEN_MISSING",
                detail="State-mutating request requires a valid X-CSRF-Token header.",
            )

        if not verify_csrf_token(auth_session.csrf_secret, str(auth_session.id), csrf_token):
            raise ProblemException(
                status_code=403,
                title="Invalid CSRF Token",
                code="CSRF_TOKEN_INVALID",
                detail="Anti-CSRF token verification failed.",
            )
