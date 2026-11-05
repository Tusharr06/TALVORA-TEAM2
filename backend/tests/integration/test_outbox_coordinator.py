"""Integration tests for Outbox Coordinator and Lease Reaping."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from talvora.modules.operations.domain.models import (
    JobOperation,
    OperationStatus,
    OutboxEvent,
    OutboxStatus,
)
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.workers.coordinator import InMemoryQueuePublisher, OutboxCoordinator


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:", execution_options={"schema_translate_map": {"identity": None}}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


@pytest.mark.integration
def test_outbox_sweep_publishes_minimal_references(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    clock = FrozenClock()
    publisher = InMemoryQueuePublisher()
    coordinator = OutboxCoordinator(publisher=publisher, clock=clock)

    session = session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id, clock=clock)

    with uow:
        op = uow.admit_operation(
            operation_type="SCREEN_APPLICATION",
            payload={"sensitive_resume_text": "Do not put in SQS!"},
            correlation_id="corr-trace-888",
        )

    # Execute coordinator outbox sweep
    count = coordinator.sweep_outbox(session)
    session.commit()

    assert count == 1
    assert len(publisher.published_messages) == 1

    msg = publisher.published_messages[0]
    assert msg["topic"] == "SCREEN_APPLICATION"
    assert msg["tenant_id"] == str(tenant_id)
    assert msg["job_id"] == str(op.id)
    assert msg["correlation_id"] == "corr-trace-888"
    # Invariant check: zero resume or payload data in message
    assert "sensitive_resume_text" not in str(msg)

    # Outbox event status updated to PUBLISHED
    outbox = session.query(OutboxEvent).filter_by(payload_ref=op.id).first()
    assert outbox is not None
    assert outbox.status == OutboxStatus.PUBLISHED.value


@pytest.mark.integration
def test_lease_recovery_requeues_crashed_worker_job(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    clock = FrozenClock()
    publisher = InMemoryQueuePublisher()
    coordinator = OutboxCoordinator(publisher=publisher, clock=clock)

    session = session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id, clock=clock)

    with uow:
        op = uow.admit_operation(operation_type="PARSE_PDF", payload={})
        op.status = OperationStatus.RUNNING.value
        # Worker crashed; lease expired 10 seconds ago
        op.lease_expires_at = clock.now() - timedelta(seconds=10)
        op.retry_count = 0

    recovered = coordinator.recover_expired_leases(session)
    session.commit()

    assert recovered == 1
    refreshed_op = session.get(JobOperation, (tenant_id, op.id))
    assert refreshed_op is not None
    assert refreshed_op.status == OperationStatus.RETRY_WAIT.value
    assert refreshed_op.retry_count == 1
    assert refreshed_op.next_attempt_at is not None
    assert refreshed_op.error_detail is not None
    assert refreshed_op.error_detail["code"] == "LEASE_EXPIRED"


@pytest.mark.integration
def test_due_jobs_sweeper_readmits_retry_wait_tasks(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    clock = FrozenClock()
    publisher = InMemoryQueuePublisher()
    coordinator = OutboxCoordinator(publisher=publisher, clock=clock)

    session = session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id, clock=clock)

    with uow:
        op = uow.admit_operation(operation_type="PARSE_PDF", payload={})
        op.status = OperationStatus.RETRY_WAIT.value
        # Due timestamp has arrived
        op.next_attempt_at = clock.now() - timedelta(seconds=1)

    requeued = coordinator.sweep_due_jobs(session)
    session.commit()

    assert requeued == 1
    refreshed_op = session.get(JobOperation, (tenant_id, op.id))
    assert refreshed_op is not None
    assert refreshed_op.status == OperationStatus.PENDING.value
    assert refreshed_op.next_attempt_at is None
