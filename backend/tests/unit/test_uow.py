"""Unit tests for Tenant-Aware Unit of Work and Outbox Admission."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from talvora.modules.audit.domain.models import AuditEvent
from talvora.modules.operations.domain.models import JobOperation, OutboxEvent
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork


@pytest.fixture
def sync_session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:", execution_options={"schema_translate_map": {"identity": None}}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


def test_uow_commit_persists_changes(sync_session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    clock = FrozenClock()

    session = sync_session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor_id, clock=clock)

    with uow:
        op = uow.admit_operation(
            operation_type="TEST_TASK",
            payload={"test": "data"},
            correlation_id="corr-123",
        )
        assert op.id is not None

    # Verify persistence in a separate session
    verify_session = sync_session_factory()
    saved_op = verify_session.get(JobOperation, (tenant_id, op.id))
    assert saved_op is not None
    assert saved_op.operation_type == "TEST_TASK"

    # Verify outbox event persisted
    outbox = verify_session.query(OutboxEvent).filter_by(payload_ref=op.id).first()
    assert outbox is not None
    assert outbox.status == "PENDING"

    # Verify mandatory audit event was recorded
    audit = verify_session.query(AuditEvent).filter_by(resource_id=op.id).first()
    assert audit is not None
    assert audit.action == "OPERATION_ADMITTED"


def test_uow_rollback_on_exception(sync_session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    session = sync_session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with pytest.raises(RuntimeError):
        with uow:
            uow.admit_operation(operation_type="FAILING_TASK", payload={})
            raise RuntimeError("Forced transaction failure")

    # Verify nothing was persisted
    verify_session = sync_session_factory()
    ops = verify_session.query(JobOperation).all()
    assert len(ops) == 0
    outbox_entries = verify_session.query(OutboxEvent).all()
    assert len(outbox_entries) == 0


def test_uow_requires_tenant_id_for_admission(sync_session_factory: sessionmaker[Session]) -> None:
    session = sync_session_factory()
    uow = UnitOfWork(session=session, tenant_id=None)

    with pytest.raises(ValueError, match="Cannot admit background task without active tenant_id"):
        with uow:
            uow.admit_operation(operation_type="TASK_WITHOUT_TENANT", payload={})
