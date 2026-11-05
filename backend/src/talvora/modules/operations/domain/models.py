"""Operations, Outbox, and Idempotency Persistence Models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

import talvora.modules.organizations.domain.models  # noqa: F401
from talvora.shared.infrastructure.models import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    VersionedMixin,
)


class OperationStatus(StrEnum):
    """Lifecycle states of durable background tasks."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class OutboxStatus(StrEnum):
    """Lifecycle states of transactional outbox events."""

    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class JobOperation(Base, TenantScopedMixin, TimestampMixin, VersionedMixin):
    """Durable background operation tracking ledger."""

    __tablename__ = "job_operation"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_job_operation"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_job_operation_tenant",
            ondelete="CASCADE",
        ),
        sa.Index("ix_job_op_tenant_status_next", "tenant_id", "status", "next_attempt_at"),
        sa.Index("ix_job_op_tenant_status_lease", "tenant_id", "status", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    operation_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(50),
        nullable=False,
        default=OperationStatus.PENDING.value,
        index=True,
    )
    resource_id: Mapped[UUID | None] = mapped_column(sa.UUID(as_uuid=True), nullable=True)
    fencing_token: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    retry_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)


class OutboxEvent(Base, TenantScopedMixin):
    """Transactional outbox item admitted alongside domain operations."""

    __tablename__ = "outbox_event"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_outbox_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_outbox_event_tenant",
            ondelete="CASCADE",
        ),
        sa.Index("ix_outbox_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    topic: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    payload_ref: Mapped[UUID] = mapped_column(sa.UUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        sa.String(50),
        nullable=False,
        default=OutboxStatus.PENDING.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class IdempotencyRecord(Base, TenantScopedMixin):
    """Guarantees idempotent command execution per route and client key."""

    __tablename__ = "idempotency_record"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_idempotency_record"),
        sa.UniqueConstraint(
            "tenant_id", "route", "idempotency_key", name="uq_idempotency_tenant_route_key"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_idempotency_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    principal_id: Mapped[UUID | None] = mapped_column(sa.UUID(as_uuid=True), nullable=True)
    route: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
