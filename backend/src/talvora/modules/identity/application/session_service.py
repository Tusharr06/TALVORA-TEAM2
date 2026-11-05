"""Session Lifecycle, Rotation, Idle Expiration, and Invalidation Service."""

from datetime import UTC, timedelta
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from talvora.modules.identity.domain.models import AuthSession, User
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.shared.infrastructure.security import (
    generate_token_256,
    hash_token,
)
from talvora.shared.transport.problems import ProblemException


class SessionService:
    """Manages secure server-side opaque sessions and CSRF secrets."""

    def __init__(
        self,
        clock: Clock | None = None,
        idle_timeout_minutes: int = 30,
        absolute_timeout_hours: int = 8,
    ) -> None:
        self.clock = clock or SystemClock()
        self.idle_timeout_minutes = idle_timeout_minutes
        self.absolute_timeout_hours = absolute_timeout_hours

    def create_session(
        self,
        db_session: Session,
        user_id: UUID,
        tenant_id: UUID | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[AuthSession, str]:
        """Creates a new opaque session, returning the persisted record and raw secret."""
        raw_token = generate_token_256()
        token_hash = hash_token(raw_token)
        csrf_secret = generate_token_256()
        now = self.clock.now()

        session = AuthSession(
            session_token_hash=token_hash,
            user_id=user_id,
            tenant_id=tenant_id,
            csrf_secret=csrf_secret,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=now + timedelta(hours=self.absolute_timeout_hours),
            idle_expires_at=now + timedelta(minutes=self.idle_timeout_minutes),
        )
        db_session.add(session)
        db_session.flush()
        return session, raw_token

    def resolve_session(
        self,
        db_session: Session,
        raw_token: str,
    ) -> tuple[AuthSession, User, Membership | None] | None:
        """Resolves an active session from raw bearer token, sliding the idle window."""
        token_hash = hash_token(raw_token)
        now = self.clock.now()

        stmt = sa.select(AuthSession).where(AuthSession.session_token_hash == token_hash)
        auth_session = db_session.scalar(stmt)
        if auth_session is None or auth_session.revoked_at is not None:
            return None

        # Check absolute expiry
        exp = (
            auth_session.expires_at
            if auth_session.expires_at.tzinfo
            else auth_session.expires_at.replace(tzinfo=UTC)
        )
        if exp <= now:
            return None

        # Check idle expiry
        idle = (
            auth_session.idle_expires_at
            if auth_session.idle_expires_at.tzinfo
            else auth_session.idle_expires_at.replace(tzinfo=UTC)
        )
        if idle <= now:
            return None

        # Slide idle window
        auth_session.idle_expires_at = now + timedelta(minutes=self.idle_timeout_minutes)

        # Load user
        user = db_session.get(User, auth_session.user_id)
        if user is None:
            return None

        # Load membership if tenant selected
        membership: Membership | None = None
        if auth_session.tenant_id is not None:
            m_stmt = sa.select(Membership).where(
                Membership.tenant_id == auth_session.tenant_id,
                Membership.user_id == user.id,
                Membership.status == "ACTIVE",
            )
            membership = db_session.scalar(m_stmt)
            # If membership was revoked, clear active tenant on this session
            if membership is None:
                auth_session.tenant_id = None

        db_session.flush()
        return auth_session, user, membership

    def rotate_session(
        self,
        db_session: Session,
        old_session_id: UUID,
        new_tenant_id: UUID | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[AuthSession, str]:
        """Rotates a session: revokes the old session and issues a fresh token and CSRF secret."""
        old = db_session.get(AuthSession, old_session_id)
        if old is None:
            raise ProblemException(
                status_code=401,
                title="Invalid Session",
                code="SESSION_INVALID",
                detail="Cannot rotate nonexistent session.",
            )

        now = self.clock.now()
        old.revoked_at = now

        tenant_id = new_tenant_id if new_tenant_id is not None else old.tenant_id
        return self.create_session(
            db_session=db_session,
            user_id=old.user_id,
            tenant_id=tenant_id,
            user_agent=user_agent or old.user_agent,
            ip_address=ip_address or old.ip_address,
        )

    def revoke_session(self, db_session: Session, raw_token: str) -> None:
        """Revokes an active session immediately."""
        token_hash = hash_token(raw_token)
        stmt = sa.select(AuthSession).where(AuthSession.session_token_hash == token_hash)
        session = db_session.scalar(stmt)
        if session is not None and session.revoked_at is None:
            session.revoked_at = self.clock.now()
            db_session.flush()

    def revoke_all_user_sessions(self, db_session: Session, user_id: UUID) -> int:
        """Revokes all active sessions for a user (used during offboarding)."""
        now = self.clock.now()
        stmt = (
            sa.update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        result = db_session.execute(stmt)
        db_session.flush()
        return int(cast(CursorResult[Any], result).rowcount)
