"""Integration tests for Worker Lease Fencing and Stale-Worker Protection."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from talvora.modules.operations.domain.models import OperationStatus
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.workers.task_handlers import (
    StaleFencingTokenError,
    TaskExecutionManager,
    execute_test_diagnostic_task,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:", execution_options={"schema_translate_map": {"identity": None}}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


@pytest.mark.integration
def test_worker_claim_increments_fencing_token(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    clock = FrozenClock()
    manager = TaskExecutionManager(clock=clock, lease_seconds=30)
    session = session_factory()

    with UnitOfWork(session=session, tenant_id=tenant_id, clock=clock) as uow:
        op = uow.admit_operation(operation_type="TEST_DIAGNOSTIC", payload={})

    # Worker 1 claims job
    claimed_op, token1 = manager.claim_job(session, tenant_id, op.id, worker_id="worker-node-1")
    session.commit()

    assert token1 == 1
    assert claimed_op.status == OperationStatus.RUNNING.value
    assert claimed_op.worker_id == "worker-node-1"
    assert claimed_op.lease_expires_at == clock.now() + timedelta(seconds=30)


@pytest.mark.integration
def test_stale_worker_cannot_finalize_after_lease_reclaimed(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    clock = FrozenClock()
    manager = TaskExecutionManager(clock=clock, lease_seconds=30)
    session = session_factory()

    with UnitOfWork(session=session, tenant_id=tenant_id, clock=clock) as uow:
        op = uow.admit_operation(operation_type="LONG_TASK", payload={})

    # 1. Worker 1 claims job (gets token = 1)
    _, token1 = manager.claim_job(session, tenant_id, op.id, worker_id="worker-1")
    session.commit()

    # 2. Worker 1 hangs/slows down. Lease expires. Worker 2 re-claims job (gets token = 2)
    clock.set_time(clock.now() + timedelta(seconds=45))
    _, token2 = manager.claim_job(session, tenant_id, op.id, worker_id="worker-2")
    session.commit()

    assert token1 == 1
    assert token2 == 2

    # 3. Worker 1 wakes up and attempts to finalize with stale token = 1.
    # INVARIANT: Database must reject stale worker and raise StaleFencingTokenError!
    with pytest.raises(StaleFencingTokenError):
        manager.finalize_job(
            session=session,
            tenant_id=tenant_id,
            job_id=op.id,
            fencing_token=token1,
            status=OperationStatus.SUCCEEDED,
            result={"output": "stale_data_do_not_commit"},
        )

    # 4. Worker 2 finalizes with current token = 2.
    final_op = manager.finalize_job(
        session=session,
        tenant_id=tenant_id,
        job_id=op.id,
        fencing_token=token2,
        status=OperationStatus.SUCCEEDED,
        result={"output": "correct_data_worker_2"},
    )
    session.commit()

    assert final_op.status == OperationStatus.SUCCEEDED.value
    assert final_op.result is not None
    assert final_op.result["output"] == "correct_data_worker_2"


@pytest.mark.integration
def test_execute_test_diagnostic_task_end_to_end(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    clock = FrozenClock()
    manager = TaskExecutionManager(clock=clock)
    session = session_factory()

    with UnitOfWork(session=session, tenant_id=tenant_id, clock=clock) as uow:
        op = uow.admit_operation(
            operation_type="TEST_DIAGNOSTIC", payload={"input": "hello_talvora"}
        )

    final_op = execute_test_diagnostic_task(
        session=session,
        manager=manager,
        tenant_id=tenant_id,
        job_id=op.id,
        worker_id="diagnostic-worker-01",
    )
    session.commit()

    assert final_op.status == OperationStatus.SUCCEEDED.value
    assert final_op.result is not None
    assert final_op.result["input_echo"] == "hello_talvora"
    assert final_op.result["processed"] is True
