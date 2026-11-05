"""Unit tests for SessionService: creation, idle/absolute expiry, rotation, and revocation."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from talvora.modules.identity.application.session_service import SessionService
from talvora.modules.identity.domain.models import User
from talvora.modules.organizations.domain.models import Membership, Organization
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.models import Base


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"identity": None}},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


def test_create_and_resolve_active_session(session_factory: sessionmaker[Session]) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = SessionService(clock=clock, idle_timeout_minutes=30, absolute_timeout_hours=8)

    user = User(issuer="https://idp", subject="sub-1", email="alice@test.com", email_verified=True)
    db.add(user)
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.flush()

    mem = Membership(tenant_id=org.id, user_id=user.id, role="OWNER", status="ACTIVE")
    db.add(mem)
    db.commit()

    # Create session
    auth_session, raw_token = service.create_session(db, user_id=user.id, tenant_id=org.id)
    db.commit()

    assert auth_session.session_token_hash is not None
    assert raw_token is not None

    # Resolve session
    resolved = service.resolve_session(db, raw_token)
    assert resolved is not None
    res_session, res_user, res_mem = resolved

    assert res_user.id == user.id
    assert res_mem is not None
    assert res_mem.role == "OWNER"
    assert res_session.tenant_id == org.id


def test_idle_timeout_expires_session(session_factory: sessionmaker[Session]) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = SessionService(clock=clock, idle_timeout_minutes=30, absolute_timeout_hours=8)

    user = User(issuer="https://idp", subject="sub-2", email="bob@test.com", email_verified=True)
    db.add(user)
    db.commit()

    _, raw_token = service.create_session(db, user_id=user.id)
    db.commit()

    # Advance clock 31 minutes (past 30-min idle limit)
    clock.set_time(clock.now() + timedelta(minutes=31))

    resolved = service.resolve_session(db, raw_token)
    assert resolved is None


def test_absolute_timeout_expires_session(session_factory: sessionmaker[Session]) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = SessionService(clock=clock, idle_timeout_minutes=30, absolute_timeout_hours=8)

    user = User(issuer="https://idp", subject="sub-3", email="carol@test.com", email_verified=True)
    db.add(user)
    db.commit()

    _, raw_token = service.create_session(db, user_id=user.id)
    db.commit()

    # Even with active usage, absolute lifetime must cut off at 8 hours
    clock.set_time(clock.now() + timedelta(hours=8, seconds=1))

    resolved = service.resolve_session(db, raw_token)
    assert resolved is None


def test_session_rotation_revokes_old_and_creates_fresh(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = SessionService(clock=clock)

    user = User(issuer="https://idp", subject="sub-4", email="dave@test.com", email_verified=True)
    db.add(user)
    db.commit()

    old_session, old_token = service.create_session(db, user_id=user.id)
    db.commit()

    # Rotate session
    new_session, new_token = service.rotate_session(db, old_session.id)
    db.commit()

    assert new_token != old_token
    assert new_session.id != old_session.id

    # Old token is now rejected
    assert service.resolve_session(db, old_token) is None
    # New token is active
    assert service.resolve_session(db, new_token) is not None


def test_revoke_session_and_revoke_all_user_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = SessionService(clock=clock)

    user = User(issuer="https://idp", subject="sub-5", email="eve@test.com", email_verified=True)
    db.add(user)
    db.commit()

    _, token1 = service.create_session(db, user_id=user.id)
    _, token2 = service.create_session(db, user_id=user.id)
    db.commit()

    # Revoke single session
    service.revoke_session(db, token1)
    db.commit()
    assert service.resolve_session(db, token1) is None
    assert service.resolve_session(db, token2) is not None

    # Offboarding: revoke all user sessions
    revoked_count = service.revoke_all_user_sessions(db, user.id)
    db.commit()
    assert revoked_count == 1
    assert service.resolve_session(db, token2) is None
