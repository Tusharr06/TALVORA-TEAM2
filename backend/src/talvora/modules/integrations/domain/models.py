"""Domain Models for Integrations, Delegated Google OAuth Connections, and Transactions."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class IntegrationProvider(StrEnum):
    GOOGLE = "GOOGLE"


class ConnectionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class GoogleScope(StrEnum):
    OPENID = "openid"
    EMAIL = "https://www.googleapis.com/auth/userinfo.email"
    PROFILE = "https://www.googleapis.com/auth/userinfo.profile"
    CALENDAR_FREEBUSY = "https://www.googleapis.com/auth/calendar.events.freebusy"
    CALENDAR_EVENTS_OWNED = "https://www.googleapis.com/auth/calendar.events.owned"
    GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"


class IntegrationConnection(Base):
    """Stores tenant-isolated, encrypted OAuth connection credentials for a member."""

    __tablename__ = "integration_connection"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    owner_membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default=IntegrationProvider.GOOGLE.value
    )
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    verified_email: Mapped[str] = mapped_column(String(320), nullable=False)
    granted_scopes: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )

    encrypted_access_token: Mapped[str] = mapped_column(Text(), nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text(), nullable=True)
    encryption_key_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    token_expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    connection_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ConnectionStatus.ACTIVE.value
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_integration_connection"),
        UniqueConstraint("tenant_id", "id", name="uq_integration_connection_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "owner_membership_id",
            "provider",
            name="uq_integration_connection_owner_provider",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_integration_connection_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_integration_connection_membership",
            ondelete="CASCADE",
        ),
        Index(
            "ix_integration_conn_owner",
            "tenant_id",
            "owner_membership_id",
            "provider",
        ),
        Index("ix_integration_conn_status", "tenant_id", "connection_status"),
    )


class IntegrationOAuthTransaction(Base):
    """In-flight OAuth state tracking for replay protection and session binding."""

    __tablename__ = "integration_oauth_transaction"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default=IntegrationProvider.GOOGLE.value
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    session_binding: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    requested_scopes: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_integration_oauth_tx"),
        UniqueConstraint("tenant_id", "id", name="uq_integration_oauth_tx_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_integration_oauth_tx_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_integration_oauth_tx_membership",
            ondelete="CASCADE",
        ),
    )
