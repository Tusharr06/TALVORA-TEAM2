"""Operations Application Service.

Manages durable background operation querying, retry scheduling, and cancellation.
"""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.operations.domain.models import (
    JobOperation,
    OperationStatus,
    OutboxEvent,
    OutboxStatus,
)
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.shared.domain.errors import EntityNotFoundError
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


class OperationService:
    """Application use cases for inspecting and controlling background operations."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()

    def get_operation(self, session: Session, tenant_id: UUID, operation_id: UUID) -> JobOperation:
        """Retrieves a background operation scoped strictly by tenant."""
        stmt = sa.select(JobOperation).where(
            JobOperation.tenant_id == tenant_id,
            JobOperation.id == operation_id,
        )
        op = session.scalar(stmt)
        if op is None:
            raise EntityNotFoundError("JobOperation", str(operation_id))
        return op

    def retry_operation(self, uow: UnitOfWork, operation_id: UUID) -> JobOperation:
        """Re-schedules a failed or retryable operation."""
        if not uow.tenant_id:
            raise ValueError("Tenant context required.")

        op = self.get_operation(uow.session, uow.tenant_id, operation_id)

        # Disallow retrying terminal success or actively running tasks
        if op.status == OperationStatus.SUCCEEDED.value:
            raise ProblemException(
                status_code=409,
                title="Invalid Operation State",
                code="INVALID_TRANSITION",
                detail="Cannot retry an operation that has already succeeded.",
            )
        if op.status == OperationStatus.RUNNING.value:
            raise ProblemException(
                status_code=409,
                title="Operation In Flight",
                code="OPERATION_IN_PROGRESS",
                detail="Cannot retry an actively running operation.",
            )
        if op.status == OperationStatus.CANCELED.value:
            raise ProblemException(
                status_code=409,
                title="Operation Canceled",
                code="INVALID_TRANSITION",
                detail="Cannot retry an operation that was explicitly canceled.",
            )

        now = self.clock.now()
        op.status = OperationStatus.PENDING.value
        op.next_attempt_at = None
        op.updated_at = now
        op.version += 1

        # Re-admit outbox event for worker pickup
        outbox_entry = OutboxEvent(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            topic=op.operation_type,
            payload_ref=op.id,
            status=OutboxStatus.PENDING.value,
            created_at=now,
        )
        uow.session.add(outbox_entry)

        uow.record_audit(
            resource_type="job_operation",
            action="OPERATION_RETRY_SCHEDULED",
            resource_id=op.id,
            details={"retry_count": op.retry_count, "operation_type": op.operation_type},
        )

        return op

    def cancel_operation(self, uow: UnitOfWork, operation_id: UUID) -> JobOperation:
        """Cancels a pending or retry-waiting operation."""
        if not uow.tenant_id:
            raise ValueError("Tenant context required.")

        op = self.get_operation(uow.session, uow.tenant_id, operation_id)

        if op.status == OperationStatus.SUCCEEDED.value:
            raise ProblemException(
                status_code=409,
                title="Operation Already Succeeded",
                code="INVALID_TRANSITION",
                detail="Cannot cancel an operation that has already finished successfully.",
            )
        if op.status == OperationStatus.RUNNING.value:
            raise ProblemException(
                status_code=409,
                title="Operation Actively Executing",
                code="OPERATION_IN_PROGRESS",
                detail="Cannot cancel an operation currently being executed by a worker.",
            )
        if op.status == OperationStatus.CANCELED.value:
            return op  # Idempotent cancel

        now = self.clock.now()
        op.status = OperationStatus.CANCELED.value
        op.updated_at = now
        op.version += 1

        uow.record_audit(
            resource_type="job_operation",
            action="OPERATION_CANCELED",
            resource_id=op.id,
            details={"operation_type": op.operation_type},
        )

        return op
