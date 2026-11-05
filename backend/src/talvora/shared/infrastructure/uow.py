"""Tenant-Aware Unit of Work and Transaction Context.

Guarantees PostgreSQL transaction-local context injection (app.current_tenant_id),
audit logging within transactional boundaries, and atomic operation admission
with the outbox pattern.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.audit.domain.models import AuditEvent
from talvora.modules.operations.domain.models import (
    JobOperation,
    OperationStatus,
    OutboxEvent,
    OutboxStatus,
)
from talvora.shared.domain.clock import Clock, SystemClock


class UnitOfWork:
    """Manages transactional scope, tenant context, audit recording, and outbox admission."""

    def __init__(
        self,
        session: Session,
        tenant_id: UUID | None = None,
        subject_id: UUID | None = None,
        clock: Clock | None = None,
        is_coordinator: bool = False,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.subject_id = subject_id
        self.clock = clock or SystemClock()
        self.is_coordinator = is_coordinator
        self._committed = False
        self._context_injected = False

    def _inject_transaction_context(self) -> None:
        """Sets transaction-local configuration settings on PostgreSQL connections."""
        if self._context_injected:
            return

        bind = self.session.get_bind()
        if bind and bind.dialect.name == "postgresql":
            # Set transaction-local context using third parameter `true` (is_local)
            self.session.execute(
                sa.text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(self.tenant_id) if self.tenant_id else ""},
            )
            self.session.execute(
                sa.text("SELECT set_config('app.current_subject_id', :subject_id, true)"),
                {"subject_id": str(self.subject_id) if self.subject_id else ""},
            )
            if self.is_coordinator:
                self.session.execute(
                    sa.text("SELECT set_config('app.is_coordinator', 'true', true)")
                )
            else:
                self.session.execute(
                    sa.text("SELECT set_config('app.is_coordinator', 'false', true)")
                )

        self._context_injected = True

    def __enter__(self) -> "UnitOfWork":
        self._inject_transaction_context()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        elif not self._committed:
            self.commit()

    def commit(self) -> None:
        """Commits the current transaction."""
        self.session.commit()
        self._committed = True
        self._context_injected = False  # Reset for next transaction on same connection

    def rollback(self) -> None:
        """Rolls back the current transaction."""
        self.session.rollback()
        self._committed = False
        self._context_injected = False

    def record_audit(
        self,
        resource_type: str,
        action: str,
        resource_id: UUID | None = None,
        actor_id: UUID | None = None,
        actor_capability: str | None = None,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Records an immutable audit event in the current transaction."""
        if not self.tenant_id:
            raise ValueError("Cannot record tenant audit event without active tenant_id.")

        audit_entry = AuditEvent(
            id=uuid4(),
            tenant_id=self.tenant_id,
            actor_id=actor_id or self.subject_id,
            actor_capability=actor_capability,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            correlation_id=correlation_id,
            timestamp=self.clock.now(),
            details=details or {},
        )
        self.session.add(audit_entry)
        return audit_entry

    def record_audit_event(
        self,
        action: str,
        resource_type: str,
        resource_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Alias for record_audit."""
        return self.record_audit(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=payload,
            correlation_id=correlation_id,
        )

    def admit_operation(
        self,
        operation_type: str,
        payload: dict[str, Any],
        resource_id: UUID | None = None,
        correlation_id: str | None = None,
        max_retries: int = 5,
    ) -> JobOperation:
        """Atomically admits a background task: writes JobOperation, OutboxEvent, and Audit."""
        if not self.tenant_id:
            raise ValueError("Cannot admit background task without active tenant_id.")

        op_id = uuid4()
        now_utc = self.clock.now()

        # 1. Insert JobOperation row
        operation = JobOperation(
            id=op_id,
            tenant_id=self.tenant_id,
            operation_type=operation_type,
            status=OperationStatus.PENDING.value,
            resource_id=resource_id,
            fencing_token=0,
            retry_count=0,
            max_retries=max_retries,
            payload=payload,
            version=1,
            created_at=now_utc,
            updated_at=now_utc,
        )
        self.session.add(operation)

        # 2. Insert OutboxEvent row
        outbox_entry = OutboxEvent(
            id=uuid4(),
            tenant_id=self.tenant_id,
            topic=operation_type,
            payload_ref=op_id,
            correlation_id=correlation_id,
            status=OutboxStatus.PENDING.value,
            created_at=now_utc,
        )
        self.session.add(outbox_entry)

        # 3. Mandatory audit logging of operation admission
        self.record_audit(
            resource_type="job_operation",
            action="OPERATION_ADMITTED",
            resource_id=op_id,
            actor_id=self.subject_id,
            correlation_id=correlation_id,
            details={
                "operation_type": operation_type,
                "resource_id": str(resource_id) if resource_id else None,
            },
        )

        return operation


@contextmanager
def unit_of_work(
    session_factory: Any,
    tenant_id: UUID | None = None,
    subject_id: UUID | None = None,
    clock: Clock | None = None,
    is_coordinator: bool = False,
) -> Generator[UnitOfWork]:
    """Context manager yielding a transaction-bound UnitOfWork."""
    session: Session = session_factory()
    uow = UnitOfWork(
        session=session,
        tenant_id=tenant_id,
        subject_id=subject_id,
        clock=clock,
        is_coordinator=is_coordinator,
    )
    try:
        with uow:
            yield uow
    finally:
        session.close()
