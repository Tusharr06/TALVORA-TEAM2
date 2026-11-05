"""Unit tests for Idempotency Service and Hash Verification."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.idempotency import (
    IdempotencyService,
    compute_request_hash,
)
from talvora.shared.infrastructure.models import Base
from talvora.shared.transport.problems import ProblemException


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:", execution_options={"schema_translate_map": {"identity": None}}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


def test_compute_request_hash_canonicalization() -> None:
    # Keys in different order must yield identical SHA-256 hash
    payload1 = {"name": "Candidate", "role": "Engineer", "active": True}
    payload2 = {"active": True, "name": "Candidate", "role": "Engineer"}

    hash1 = compute_request_hash(payload1)
    hash2 = compute_request_hash(payload2)

    assert hash1 == hash2
    assert len(hash1) == 64


def test_idempotency_matching_replay_returns_cached_record(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    service = IdempotencyService()
    session = session_factory()

    payload = {"command": "book_interview", "slot": "2026-11-10T10:00:00Z"}
    req_hash = compute_request_hash(payload)

    # 1. First execution: save record
    service.save_idempotency_record(
        session=session,
        tenant_id=tenant_id,
        route="/api/v1/interviews/book",
        idempotency_key="idemp-key-1001",
        request_hash=req_hash,
        response_status=201,
        response_body={"booking_id": "book-123", "status": "CONFIRMED"},
    )
    session.commit()

    # 2. Replay with identical key & payload returns cached record
    replay_record = service.check_idempotency(
        session=session,
        tenant_id=tenant_id,
        route="/api/v1/interviews/book",
        idempotency_key="idemp-key-1001",
        request_hash=req_hash,
    )
    assert replay_record is not None
    assert replay_record.response_status == 201
    assert replay_record.response_body["booking_id"] == "book-123"


def test_idempotency_different_payload_raises_409_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    service = IdempotencyService()
    session = session_factory()

    initial_payload = {"slot": "10:00"}
    different_payload = {"slot": "14:00"}

    hash1 = compute_request_hash(initial_payload)
    hash2 = compute_request_hash(different_payload)

    service.save_idempotency_record(
        session=session,
        tenant_id=tenant_id,
        route="/api/v1/interviews/book",
        idempotency_key="idemp-conflict-key",
        request_hash=hash1,
        response_status=200,
        response_body={"status": "ok"},
    )
    session.commit()

    # Replay with different payload must fail with 409
    with pytest.raises(ProblemException) as exc_info:
        service.check_idempotency(
            session=session,
            tenant_id=tenant_id,
            route="/api/v1/interviews/book",
            idempotency_key="idemp-conflict-key",
            request_hash=hash2,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"


def test_expired_idempotency_record_is_cleaned_up(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    clock = FrozenClock()
    service = IdempotencyService(clock=clock)
    session = session_factory()

    req_hash = compute_request_hash({"test": "data"})
    service.save_idempotency_record(
        session=session,
        tenant_id=tenant_id,
        route="/api/v1/test",
        idempotency_key="expiring-key",
        request_hash=req_hash,
        response_status=200,
        response_body={},
        ttl_hours=1,
    )
    session.commit()

    # Advance clock past TTL (2 hours)
    clock.set_time(clock.now() + timedelta(hours=2))

    # Recheck: expired record must return None and be pruned
    record = service.check_idempotency(
        session=session,
        tenant_id=tenant_id,
        route="/api/v1/test",
        idempotency_key="expiring-key",
        request_hash=req_hash,
    )
    assert record is None
