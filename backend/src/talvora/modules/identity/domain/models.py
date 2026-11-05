"""Identity User Domain Model, Sessions, and Login Transactions."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base, TimestampMixin


class User(Base, TimestampMixin):
    """Physical user identity provisioned via OIDC."""

    __tablename__ = "users"
    __table_args__ = (
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    issuer: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    email_verified: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)


class AuthSession(Base, TimestampMixin):
    """Server-side opaque session record."""

    __tablename__ = "auth_session"

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    session_token_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        sa.ForeignKey("identity.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    csrf_secret: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        now = datetime.now(UTC)
        if self.revoked_at is not None:
            return False
        exp = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=UTC)
        idle = (
            self.idle_expires_at
            if self.idle_expires_at.tzinfo
            else self.idle_expires_at.replace(tzinfo=UTC)
        )
        return now < exp and now < idle


class LoginTransaction(Base):
    """Temporary OIDC login transaction holding PKCE verifier, state, and nonce."""

    __tablename__ = "login_transaction"

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    state: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True, index=True)
    nonce: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    redirect_target: Mapped[str] = mapped_column(sa.String(500), nullable=False, default="/")
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
