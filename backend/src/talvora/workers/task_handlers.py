"""Task Handlers and Worker Lease Fencing Execution.

Implements lease acquisition via SELECT ... FOR UPDATE, fencing token incrementing,
periodic heartbeats, and completion updates conditioned on matching tokens.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.operations.domain.models import JobOperation, OperationStatus
from talvora.shared.domain.clock import Clock, SystemClock


class StaleFencingTokenError(Exception):
    """Raised when a worker attempts to finalize with an outdated fencing token."""

    pass


class TaskExecutionManager:
    """Manages worker job claiming, heartbeating, and fencing token validation."""

    def __init__(self, clock: Clock | None = None, lease_seconds: int = 60) -> None:
        self.clock = clock or SystemClock()
        self.lease_seconds = lease_seconds

    def claim_job(
        self,
        session: Session,
        tenant_id: UUID,
        job_id: UUID,
        worker_id: str,
    ) -> tuple[JobOperation, int]:
        """Atomically claims a job, increments the fencing token, and sets lease expiration.

        Returns (JobOperation, claimed_fencing_token).
        """
        now = self.clock.now()

        stmt = (
            sa.select(JobOperation)
            .where(
                JobOperation.tenant_id == tenant_id,
                JobOperation.id == job_id,
            )
            .with_for_update()
        )
        op = session.scalar(stmt)

        if op is None:
            raise ValueError(f"JobOperation {job_id} not found in tenant {tenant_id}.")

        # If already canceled or succeeded, cannot claim
        if op.status in (OperationStatus.SUCCEEDED.value, OperationStatus.CANCELED.value):
            return op, op.fencing_token

        # Increment fencing token and lease
        op.fencing_token += 1
        claimed_token = op.fencing_token
        op.status = OperationStatus.RUNNING.value
        op.worker_id = worker_id
        op.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        op.updated_at = now
        op.version += 1

        session.flush()
        return op, claimed_token

    def heartbeat(
        self,
        session: Session,
        tenant_id: UUID,
        job_id: UUID,
        fencing_token: int,
    ) -> bool:
        """Extends worker lease expiration if the fencing token is still current."""
        now = self.clock.now()

        stmt = (
            sa.select(JobOperation)
            .where(
                JobOperation.tenant_id == tenant_id,
                JobOperation.id == job_id,
                JobOperation.fencing_token == fencing_token,
                JobOperation.status == OperationStatus.RUNNING.value,
            )
            .with_for_update()
        )
        op = session.scalar(stmt)

        if op is None:
            return False  # Lease expired and was claimed by another worker

        op.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        op.updated_at = now
        session.flush()
        return True

    def finalize_job(
        self,
        session: Session,
        tenant_id: UUID,
        job_id: UUID,
        fencing_token: int,
        status: OperationStatus,
        result: dict[str, Any] | None = None,
        error_detail: dict[str, Any] | None = None,
    ) -> JobOperation:
        """Finalizes job outcome conditioned on matching fencing token."""
        now = self.clock.now()

        stmt = (
            sa.select(JobOperation)
            .where(
                JobOperation.tenant_id == tenant_id,
                JobOperation.id == job_id,
            )
            .with_for_update()
        )
        op = session.scalar(stmt)

        if op is None:
            raise ValueError(f"JobOperation {job_id} not found.")

        # If fencing token does not match, a stale worker is attempting to commit
        if op.fencing_token != fencing_token:
            raise StaleFencingTokenError(
                f"Cannot finalize JobOperation {job_id}: claimed token was {fencing_token}, "
                f"but current token is {op.fencing_token}."
            )

        op.status = status.value
        op.result = result
        op.error_detail = error_detail
        op.lease_expires_at = None
        op.updated_at = now
        op.version += 1

        session.flush()
        return op


def execute_test_diagnostic_task(
    session: Session,
    manager: TaskExecutionManager,
    tenant_id: UUID,
    job_id: UUID,
    worker_id: str,
) -> JobOperation:
    """Executes a real background test diagnostic calculation demonstrating worker fencing."""
    op, token = manager.claim_job(session, tenant_id, job_id, worker_id)

    if op.status != OperationStatus.RUNNING.value:
        return op

    # Heartbeat demonstration
    manager.heartbeat(session, tenant_id, job_id, token)

    # Perform task computation (e.g. payload inspection)
    input_val = op.payload.get("input", "default")
    computed_result = {
        "processed": True,
        "input_echo": input_val,
        "worker_id": worker_id,
        "fencing_token": token,
    }

    # Finalize with matching token
    final_op = manager.finalize_job(
        session=session,
        tenant_id=tenant_id,
        job_id=job_id,
        fencing_token=token,
        status=OperationStatus.SUCCEEDED,
        result=computed_result,
    )
    return final_op
