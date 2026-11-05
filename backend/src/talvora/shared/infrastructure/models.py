"""Declarative Base and Shared Column Mixins for SQLAlchemy 2.0."""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarative class for all Talvora database entities."""

    pass


class TimestampMixin:
    """Provides timezone-aware UTC created_at and updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class TenantScopedMixin:
    """Enforces non-null tenant_id on all tenant-owned entities."""

    tenant_id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )


class VersionedMixin:
    """Optimistic concurrency control version column."""

    version: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=1,
    )
