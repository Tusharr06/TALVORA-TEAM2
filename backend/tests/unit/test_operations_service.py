"""Unit tests for OperationService logic, retries, and cancellation."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from talvora.modules.operations.application.service import OperationService
from talvora.modules.operations.domain.models import OperationStatus
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:", execution_options={"schema_translate_map": {"identity": None}}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


def test_retry_failed_operation_schedules_pending(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    service = OperationService()
    sync_session = session_factory()
    uow = UnitOfWork(session=sync_session, tenant_id=tenant_id)

    with uow:
        op = uow.admit_operation(operation_type="EXTRACT_TEXT", payload={})
        op.status = OperationStatus.FAILED.value
        op.retry_count = 1

    # Execute retry
    with uow:
        retried = service.retry_operation(uow, op.id)
        assert retried.status == OperationStatus.PENDING.value
        assert retried.next_attempt_at is None


def test_retry_succeeded_operation_raises_409(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    service = OperationService()
    session = session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        op = uow.admit_operation(operation_type="EXTRACT_TEXT", payload={})
        op.status = OperationStatus.SUCCEEDED.value

    with pytest.raises(ProblemException) as exc_info:
        with uow:
            service.retry_operation(uow, op.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "INVALID_TRANSITION"


def test_cancel_pending_operation_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    service = OperationService()
    session = session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        op = uow.admit_operation(operation_type="GENERATE_PACK", payload={})
        assert op.status == OperationStatus.PENDING.value

    with uow:
        canceled = service.cancel_operation(uow, op.id)
        assert canceled.status == OperationStatus.CANCELED.value


def test_cancel_actively_running_operation_raises_409(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    service = OperationService()
    session = session_factory()
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        op = uow.admit_operation(operation_type="BOOK_CALENDAR", payload={})
        op.status = OperationStatus.RUNNING.value

    with pytest.raises(ProblemException) as exc_info:
        with uow:
            service.cancel_operation(uow, op.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "OPERATION_IN_PROGRESS"
