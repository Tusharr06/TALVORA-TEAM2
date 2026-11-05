"""Audit Event Persistence Model."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base, TenantScopedMixin


class AuditEvent(Base, TenantScopedMixin):
    """Immutable, append-only operational audit event log."""

    __tablename__ = "audit_event"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_audit_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_audit_event_tenant",
            ondelete="CASCADE",
        ),
        sa.Index("ix_audit_tenant_resource", "tenant_id", "resource_type", "resource_id"),
        sa.Index("ix_audit_tenant_timestamp", "tenant_id", "timestamp"),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    actor_id: Mapped[UUID | None] = mapped_column(sa.UUID(as_uuid=True), nullable=True)
    actor_capability: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    resource_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(sa.UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    details: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
