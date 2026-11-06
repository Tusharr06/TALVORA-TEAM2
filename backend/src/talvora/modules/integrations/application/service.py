"""Application Service for Integration Connection Lifecycle and Token Management."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.identity.domain.roles import Role
from talvora.modules.integrations.application.ports import (
    GoogleOAuthClient,
    GoogleTokens,
    GoogleUserInfo,
)
from talvora.modules.integrations.domain.errors import (
    IntegrationError,
    IntegrationReauthRequiredError,
    OAuthConsentDeniedError,
    OAuthStateError,
)
from talvora.modules.integrations.domain.models import (
    ConnectionStatus,
    GoogleScope,
    IntegrationConnection,
    IntegrationOAuthTransaction,
    IntegrationProvider,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.security import (
    decrypt_token,
    encrypt_token,
    generate_token_256,
    hash_token,
)
from talvora.shared.infrastructure.uow import UnitOfWork


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _with_for_update(stmt: sa.sql.Select[Any], session: Session) -> sa.sql.Select[Any]:
    bind = session.get_bind()
    if bind and bind.dialect.name == "postgresql":
        return stmt.with_for_update()
    return stmt


DEFAULT_GOOGLE_SCOPES = [
    GoogleScope.OPENID.value,
    GoogleScope.EMAIL.value,
    GoogleScope.PROFILE.value,
    GoogleScope.CALENDAR_FREEBUSY.value,
    GoogleScope.CALENDAR_EVENTS_OWNED.value,
    GoogleScope.GMAIL_SEND.value,
]

CAPABILITY_TO_SCOPES: dict[str, list[str]] = {
    "freebusy": [GoogleScope.CALENDAR_FREEBUSY.value],
    "calendar_events": [GoogleScope.CALENDAR_EVENTS_OWNED.value],
    "gmail_send": [GoogleScope.GMAIL_SEND.value],
}


class IntegrationService:
    """Orchestrates secure OAuth handshakes, envelope encryption, and token lifecycle."""

    @staticmethod
    def initiate_google_authorization(
        uow: UnitOfWork,
        actor: ActorContext,
        oauth_client: GoogleOAuthClient,
        requested_capabilities: list[str] | None,
        redirect_uri: str,
        prompt: str = "consent",
        session_binding: str = "default_session",
    ) -> tuple[str, str, datetime]:
        """Generates an OAuth authorization URL and records a cryptographically bound, replay-safe state."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise IntegrationError(status_code=401, detail="Active authenticated session required.")

        # Determine scopes
        requested_scopes: list[str] = [GoogleScope.OPENID.value, GoogleScope.EMAIL.value]
        if requested_capabilities:
            for cap in requested_capabilities:
                scopes = CAPABILITY_TO_SCOPES.get(cap)
                if scopes:
                    for s in scopes:
                        if s not in requested_scopes:
                            requested_scopes.append(s)
        else:
            requested_scopes = list(DEFAULT_GOOGLE_SCOPES)

        raw_state = generate_token_256()
        state_digest = hash_token(raw_state)

        now = _utc_now()
        expires_at = now + timedelta(minutes=15)

        tx = IntegrationOAuthTransaction(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            actor_membership_id=actor.membership.membership_id,
            provider=IntegrationProvider.GOOGLE.value,
            state_hash=state_digest,
            session_binding=session_binding,
            redirect_uri=redirect_uri,
            requested_scopes=requested_scopes,
            expires_at=expires_at,
            consumed_at=None,
            created_at=now,
        )
        uow.session.add(tx)
        uow.session.flush()

        auth_url = oauth_client.get_authorization_url(
            redirect_uri=redirect_uri,
            state=raw_state,
            requested_scopes=requested_scopes,
            prompt=prompt,
            access_type="offline",
        )

        return auth_url, raw_state, expires_at

    @staticmethod
    async def handle_google_callback(
        uow: UnitOfWork,
        actor: ActorContext,
        oauth_client: GoogleOAuthClient,
        encryption_key: str,
        raw_state: str,
        code: str | None,
        error: str | None = None,
        error_description: str | None = None,
        session_binding: str = "default_session",
    ) -> IntegrationConnection:
        """Validates OAuth state, exchanges code for credentials, and encrypts tokens before persistence."""
        if error:
            detail_msg = f"Google authorization denied ({error})"
            if error_description:
                detail_msg = f"{detail_msg}: {error_description}"
            raise OAuthConsentDeniedError(detail_msg)

        if not raw_state or not code:
            raise OAuthStateError("Missing required OAuth callback parameters.")

        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise IntegrationError(status_code=401, detail="Active authenticated session required.")

        member_id = actor.membership.membership_id
        state_digest = hash_token(raw_state)
        now = _utc_now()

        # Atomically find and consume transaction
        stmt = _with_for_update(
            sa.select(IntegrationOAuthTransaction).where(
                IntegrationOAuthTransaction.state_hash == state_digest
            ),
            uow.session,
        )
        tx = uow.session.scalar(stmt)

        if tx is None:
            raise OAuthStateError("Invalid or unrecognized OAuth state.")

        if tx.consumed_at is not None:
            raise OAuthStateError("OAuth state has already been consumed (replay detected).")

        if now >= _ensure_utc(tx.expires_at):
            raise OAuthStateError("OAuth state has expired.")

        if tx.tenant_id != uow.tenant_id:
            raise OAuthStateError("Cross-tenant OAuth callback rejection.")

        if tx.session_binding != session_binding:
            raise OAuthStateError("OAuth transaction session mismatch.")

        if tx.actor_membership_id != member_id:
            raise OAuthStateError("OAuth transaction membership mismatch.")

        # Mark transaction consumed immediately
        tx.consumed_at = now
        uow.session.flush()

        # Exchange authorization code for tokens
        tokens: GoogleTokens = await oauth_client.exchange_code(code, tx.redirect_uri)

        # Retrieve verified identity
        user_info: GoogleUserInfo = await oauth_client.get_user_info(tokens.access_token)
        if not user_info.email_verified:
            raise IntegrationError(
                status_code=403, detail="Connected Google account email must be verified."
            )

        # Encrypt tokens server-side using AES-256-GCM
        enc_access = encrypt_token(tokens.access_token, encryption_key)
        enc_refresh: str | None = None
        if tokens.refresh_token:
            enc_refresh = encrypt_token(tokens.refresh_token, encryption_key)

        token_expiry = now + timedelta(seconds=tokens.expires_in)

        # Look up existing connection for this member in this tenant
        conn_stmt = _with_for_update(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == uow.tenant_id,
                IntegrationConnection.owner_membership_id == member_id,
                IntegrationConnection.provider == IntegrationProvider.GOOGLE.value,
            ),
            uow.session,
        )
        existing_conn: IntegrationConnection | None = uow.session.scalar(conn_stmt)

        connection: IntegrationConnection
        if existing_conn:
            # Reconnection: If Google omitted refresh token, PRESERVE the existing valid refresh token
            if not enc_refresh and existing_conn.encrypted_refresh_token:
                enc_refresh = existing_conn.encrypted_refresh_token

            existing_conn.provider_subject = user_info.sub
            existing_conn.verified_email = user_info.email
            existing_conn.granted_scopes = tokens.granted_scopes
            existing_conn.encrypted_access_token = enc_access
            existing_conn.encrypted_refresh_token = enc_refresh
            existing_conn.encryption_key_version = "v1"
            existing_conn.token_expiry = token_expiry
            existing_conn.connection_status = ConnectionStatus.ACTIVE.value
            existing_conn.last_error_code = None
            existing_conn.disconnected_at = None
            existing_conn.updated_at = now
            existing_conn.version += 1
            connection = existing_conn
        else:
            connection = IntegrationConnection(
                id=uuid4(),
                tenant_id=uow.tenant_id,
                owner_membership_id=member_id,
                provider=IntegrationProvider.GOOGLE.value,
                provider_subject=user_info.sub,
                verified_email=user_info.email,
                granted_scopes=tokens.granted_scopes,
                encrypted_access_token=enc_access,
                encrypted_refresh_token=enc_refresh,
                encryption_key_version="v1",
                token_expiry=token_expiry,
                connection_status=ConnectionStatus.ACTIVE.value,
                last_error_code=None,
                connected_at=now,
                updated_at=now,
            )
            uow.session.add(connection)

        uow.session.flush()

        uow.record_audit(
            resource_type="integration_connection",
            action="INTEGRATION_CONNECTED",
            resource_id=connection.id,
            actor_id=actor.user_id,
            details={
                "provider": IntegrationProvider.GOOGLE.value,
                "verified_email": user_info.email,
                "granted_scopes": tokens.granted_scopes,
            },
        )

        return connection

    @staticmethod
    def list_integrations(
        uow: UnitOfWork,
        actor: ActorContext,
    ) -> list[IntegrationConnection]:
        """Lists active and configured integration connections within tenant boundary."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise IntegrationError(status_code=401, detail="Active authenticated session required.")

        stmt = sa.select(IntegrationConnection).where(
            IntegrationConnection.tenant_id == uow.tenant_id,
        )

        # Non-admins can only see their own connections
        if actor.membership.role != Role.ADMIN.value:
            stmt = stmt.where(
                IntegrationConnection.owner_membership_id == actor.membership.membership_id
            )

        stmt = stmt.order_by(IntegrationConnection.created_at.desc())
        return list(uow.session.scalars(stmt).all())

    @staticmethod
    async def disconnect_integration(
        uow: UnitOfWork,
        actor: ActorContext,
        oauth_client: GoogleOAuthClient,
        encryption_key: str,
        connection_id: UUID,
    ) -> IntegrationConnection:
        """Zeroes out stored credentials, revokes OAuth grant, and sets status to DISCONNECTED."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise IntegrationError(status_code=401, detail="Active authenticated session required.")

        stmt = _with_for_update(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == uow.tenant_id,
                IntegrationConnection.id == connection_id,
            ),
            uow.session,
        )
        conn: IntegrationConnection | None = uow.session.scalar(stmt)
        if conn is None:
            raise IntegrationError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                detail="Integration connection not found.",
            )

        # Non-admins can only disconnect their own connections
        if (
            actor.membership.role != Role.ADMIN.value
            and conn.owner_membership_id != actor.membership.membership_id
        ):
            raise IntegrationError(
                status_code=403, detail="Forbidden: cannot disconnect other members' integrations."
            )

        # Attempt to revoke refresh token with Google
        if conn.encrypted_refresh_token:
            try:
                decrypted_refresh = decrypt_token(conn.encrypted_refresh_token, encryption_key)
                await oauth_client.revoke_token(decrypted_refresh)
            except Exception:
                pass

        now = _utc_now()
        conn.encrypted_access_token = ""
        conn.encrypted_refresh_token = None
        conn.connection_status = ConnectionStatus.DISCONNECTED.value
        conn.disconnected_at = now
        conn.updated_at = now
        conn.version += 1

        uow.session.flush()

        uow.record_audit(
            resource_type="integration_connection",
            action="INTEGRATION_DISCONNECTED",
            resource_id=conn.id,
            actor_id=actor.user_id,
            details={"provider": conn.provider, "verified_email": conn.verified_email},
        )

        return conn

    @staticmethod
    async def get_valid_access_token(
        uow: UnitOfWork,
        actor: ActorContext,
        oauth_client: GoogleOAuthClient,
        encryption_key: str,
        membership_id: UUID,
        required_scope: str | None = None,
    ) -> tuple[str, str]:
        """Retrieves a valid, unexpired access token with concurrency-safe refresh."""
        now = _utc_now()

        # Row lock connection record to prevent concurrent refresh races
        stmt = _with_for_update(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == uow.tenant_id,
                IntegrationConnection.owner_membership_id == membership_id,
                IntegrationConnection.provider == IntegrationProvider.GOOGLE.value,
            ),
            uow.session,
        )
        conn = uow.session.scalar(stmt)

        if conn is None:
            raise IntegrationError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                detail="Integration connection not found.",
            )

        if (
            conn.connection_status != ConnectionStatus.ACTIVE.value
            or conn.disconnected_at is not None
        ):
            raise IntegrationReauthRequiredError(
                f"Integration connection is not active (status: {conn.connection_status})."
            )

        # Check if current access token is valid (with 5-minute buffer)
        is_valid = conn.token_expiry and _ensure_utc(conn.token_expiry) > (
            now + timedelta(seconds=300)
        )

        if is_valid and conn.encrypted_access_token:
            try:
                decrypted_access = decrypt_token(conn.encrypted_access_token, encryption_key)
                return decrypted_access, conn.verified_email
            except Exception as exc:
                raise IntegrationError(
                    status_code=500, detail="Failed to decrypt stored access token."
                ) from exc

        # Token is expired or expiring; refresh required
        if not conn.encrypted_refresh_token:
            conn.connection_status = ConnectionStatus.ERROR.value
            conn.last_error_code = "MISSING_REFRESH_TOKEN"
            uow.session.flush()
            raise IntegrationReauthRequiredError("Refresh token missing; reauthorization required.")

        try:
            decrypted_refresh = decrypt_token(conn.encrypted_refresh_token, encryption_key)
        except Exception as exc:
            raise IntegrationError(
                status_code=500, detail="Failed to decrypt stored refresh token."
            ) from exc

        try:
            new_tokens = await oauth_client.refresh_access_token(decrypted_refresh)
        except IntegrationReauthRequiredError:
            conn.connection_status = ConnectionStatus.REVOKED.value
            conn.last_error_code = "REFRESH_TOKEN_REVOKED"
            uow.session.flush()
            raise

        conn.encrypted_access_token = encrypt_token(new_tokens.access_token, encryption_key)
        if new_tokens.refresh_token:
            conn.encrypted_refresh_token = encrypt_token(new_tokens.refresh_token, encryption_key)
        conn.token_expiry = now + timedelta(seconds=new_tokens.expires_in)
        conn.connection_status = ConnectionStatus.ACTIVE.value
        conn.last_error_code = None
        conn.updated_at = now
        conn.version += 1

        uow.session.flush()

        return new_tokens.access_token, conn.verified_email
