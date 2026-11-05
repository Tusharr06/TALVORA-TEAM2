"""Outbox and Due-Work Coordinator.

Sweeps unpublished outbox items, recovers expired worker leases, and
schedules due retries using database fencing and transactions.
"""

from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.operations.domain.models import (
    JobOperation,
    OperationStatus,
    OutboxEvent,
    OutboxStatus,
)
from talvora.shared.domain.clock import Clock, SystemClock


class QueuePublisher(Protocol):
    """Protocol for dispatching task messages to the queue broker."""

    def publish_task(
        self,
        topic: str,
        tenant_id: UUID,
        job_id: UUID,
        schema_version: int,
        correlation_id: str | None,
    ) -> bool:
        """Publishes task message containing only minimal routing references."""
        ...


class InMemoryQueuePublisher:
    """In-memory queue publisher for tests and local development."""

    def __init__(self) -> None:
        self.published_messages: list[dict[str, Any]] = []
        self.should_fail = False

    def publish_task(
        self,
        topic: str,
        tenant_id: UUID,
        job_id: UUID,
        schema_version: int,
        correlation_id: str | None,
    ) -> bool:
        if self.should_fail:
            return False

        self.published_messages.append(
            {
                "topic": topic,
                "tenant_id": str(tenant_id),
                "job_id": str(job_id),
                "schema_version": schema_version,
                "correlation_id": correlation_id,
            }
        )
        return True


class OutboxCoordinator:
    """Coordinates outbox sweeping, lease reaping, and due task scheduling."""

    def __init__(
        self,
        publisher: QueuePublisher,
        clock: Clock | None = None,
        lease_duration_seconds: int = 60,
    ) -> None:
        self.publisher = publisher
        self.clock = clock or SystemClock()
        self.lease_duration_seconds = lease_duration_seconds

    def sweep_outbox(self, session: Session, batch_size: int = 50) -> int:
        """Sweeps unpublished outbox events and publishes minimal task references to SQS.

        Returns count of published events.
        """
        now = self.clock.now()

        # Query pending outbox events ordered by created_at
        stmt = (
            sa.select(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING.value)
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = session.scalars(stmt).all()
        published_count = 0

        for event in events:
            # Publish strictly: tenant_id, job_id, schema_version, correlation_id
            success = self.publisher.publish_task(
                topic=event.topic,
                tenant_id=event.tenant_id,
                job_id=event.payload_ref,
                schema_version=1,
                correlation_id=event.correlation_id,
            )

            if success:
                event.status = OutboxStatus.PUBLISHED.value
                event.published_at = now
                published_count += 1
            else:
                event.status = OutboxStatus.FAILED.value

        session.flush()
        return published_count

    def recover_expired_leases(self, session: Session, batch_size: int = 50) -> int:
        """Reaps operations where worker lease has expired without completion."""
        now = self.clock.now()

        stmt = (
            sa.select(JobOperation)
            .where(
                JobOperation.status == OperationStatus.RUNNING.value,
                JobOperation.lease_expires_at < now,
            )
            .order_by(JobOperation.lease_expires_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        expired_ops = session.scalars(stmt).all()
        recovered_count = 0

        for op in expired_ops:
            op.retry_count += 1
            if op.retry_count >= op.max_retries:
                op.status = OperationStatus.FAILED.value
                op.error_detail = {
                    "code": "LEASE_EXPIRED_MAX_RETRIES",
                    "detail": f"Worker lease expired after {op.retry_count} attempts.",
                }
            else:
                # Schedule retry with exponential backoff
                backoff_seconds = min(300, 2**op.retry_count * 5)
                op.status = OperationStatus.RETRY_WAIT.value
                op.next_attempt_at = now + timedelta(seconds=backoff_seconds)
                op.error_detail = {
                    "code": "LEASE_EXPIRED",
                    "detail": "Worker lease expired. Re-queued for retry.",
                }

            op.updated_at = now
            op.version += 1
            recovered_count += 1

        session.flush()
        return recovered_count

    def sweep_due_jobs(self, session: Session, batch_size: int = 50) -> int:
        """Sweeps RETRY_WAIT operations whose next_attempt_at has arrived and enqueues them."""
        now = self.clock.now()

        stmt = (
            sa.select(JobOperation)
            .where(
                JobOperation.status == OperationStatus.RETRY_WAIT.value,
                JobOperation.next_attempt_at <= now,
            )
            .order_by(JobOperation.next_attempt_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        due_ops = session.scalars(stmt).all()
        requeued_count = 0

        for op in due_ops:
            op.status = OperationStatus.PENDING.value
            op.next_attempt_at = None
            op.updated_at = now
            op.version += 1

            # Insert outbox entry
            outbox_entry = OutboxEvent(
                tenant_id=op.tenant_id,
                topic=op.operation_type,
                payload_ref=op.id,
                status=OutboxStatus.PENDING.value,
                created_at=now,
            )
            session.add(outbox_entry)
            requeued_count += 1

        session.flush()
        return requeued_count
