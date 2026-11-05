"""Organization, Membership, Team, and Invitation Domain Models."""

from datetime import UTC, datetime
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


class MembershipInvitation(Base, TenantScopedMixin, TimestampMixin):
    """Invitation for a user to join an organization with a specific role."""

    __tablename__ = "membership_invitation"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_membership_invitation"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_membership_invitation_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["identity.users.id"],
            name="fk_membership_invitation_invited_by",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    invited_by_user_id: Mapped[UUID] = mapped_column(sa.UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class Team(Base, TenantScopedMixin, TimestampMixin):
    """Departmental or functional hiring team within an organization."""

    __tablename__ = "team"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_team"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_team_tenant_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_team_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class TeamMember(Base, TenantScopedMixin):
    """Assignment of a membership to a team."""

    __tablename__ = "team_member"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_team_member"),
        sa.UniqueConstraint("tenant_id", "team_id", "membership_id", name="uq_team_members_unique"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_team_member_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            ["team.tenant_id", "team.id"],
            name="fk_team_member_team",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_team_member_membership",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    team_id: Mapped[UUID] = mapped_column(sa.UUID(as_uuid=True), nullable=False, index=True)
    membership_id: Mapped[UUID] = mapped_column(sa.UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
