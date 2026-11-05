"""Organization and Membership Domain Models."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    VersionedMixin,
)


class Organization(Base, TimestampMixin, VersionedMixin):
    """Tenant organization entity."""

    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True, index=True)


class Membership(Base, TenantScopedMixin, TimestampMixin, VersionedMixin):
    """User membership within an organization."""

    __tablename__ = "memberships"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_memberships"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_memberships_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        sa.String(50),
        nullable=False,
        default="INTERVIEWER",
    )
    status: Mapped[str] = mapped_column(
        sa.String(50),
        nullable=False,
        default="ACTIVE",
    )
