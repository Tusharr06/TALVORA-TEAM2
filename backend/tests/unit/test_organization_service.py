"""Unit tests for OrganizationService, Last-Owner Protection, Teams, and Invitations."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from talvora.modules.identity.domain.models import User
from talvora.modules.organizations.application.service import OrganizationService
from talvora.modules.organizations.domain.models import Membership, Organization
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"identity": None}},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


def test_create_organization_assigns_creator_as_owner(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)

    creator = User(
        issuer="https://idp", subject="sub-owner", email="owner@test.com", email_verified=True
    )
    db.add(creator)
    db.commit()

    with UnitOfWork(session=db, tenant_id=None, subject_id=creator.id, clock=clock) as uow:
        org = service.create_organization(
            uow=uow,
            creator_user_id=creator.id,
            name="Talvora Corp",
            slug="talvora-corp",
        )

    # Verify organization and owner membership
    mem = db.query(Membership).filter_by(tenant_id=org.id, user_id=creator.id).first()
    assert mem is not None
    assert mem.role == "OWNER"
    assert mem.status == "ACTIVE"


def test_last_owner_demotion_rejected_with_409(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)

    creator = User(
        issuer="https://idp", subject="sub-1", email="owner1@test.com", email_verified=True
    )
    db.add(creator)
    org = Organization(name="Solo Corp", slug="solo-corp")
    db.add(org)
    db.flush()

    mem = Membership(tenant_id=org.id, user_id=creator.id, role="OWNER", status="ACTIVE")
    db.add(mem)
    db.commit()

    # Attempting to demote the sole active owner to ADMIN must raise 409 Conflict
    with pytest.raises(ProblemException) as exc:
        with UnitOfWork(session=db, tenant_id=org.id, subject_id=creator.id, clock=clock) as uow:
            service.update_member_role(
                uow=uow,
                tenant_id=org.id,
                target_user_id=creator.id,
                new_role="ADMIN",
            )

    assert exc.value.status_code == 409
    assert exc.value.code == "LAST_OWNER_PROTECTION"


def test_last_owner_removal_rejected_with_409(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)

    creator = User(
        issuer="https://idp", subject="sub-2", email="owner2@test.com", email_verified=True
    )
    db.add(creator)
    org = Organization(name="Single Owner Inc", slug="single-owner-inc")
    db.add(org)
    db.flush()

    mem = Membership(tenant_id=org.id, user_id=creator.id, role="OWNER", status="ACTIVE")
    db.add(mem)
    db.commit()

    # Attempting to remove/offboard the sole active owner must raise 409 Conflict
    with pytest.raises(ProblemException) as exc:
        with UnitOfWork(session=db, tenant_id=org.id, subject_id=creator.id, clock=clock) as uow:
            service.remove_member(
                uow=uow,
                tenant_id=org.id,
                target_user_id=creator.id,
            )

    assert exc.value.status_code == 409
    assert exc.value.code == "LAST_OWNER_PROTECTION"


def test_demotion_allowed_when_multiple_owners_exist(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)

    owner1 = User(issuer="https://idp", subject="sub-o1", email="o1@test.com", email_verified=True)
    owner2 = User(issuer="https://idp", subject="sub-o2", email="o2@test.com", email_verified=True)
    db.add_all([owner1, owner2])
    org = Organization(name="Multi Owner Corp", slug="multi-owner")
    db.add(org)
    db.flush()

    mem1 = Membership(tenant_id=org.id, user_id=owner1.id, role="OWNER", status="ACTIVE")
    mem2 = Membership(tenant_id=org.id, user_id=owner2.id, role="OWNER", status="ACTIVE")
    db.add_all([mem1, mem2])
    db.commit()

    # Owner 1 can be demoted because Owner 2 is also active owner
    with UnitOfWork(session=db, tenant_id=org.id, subject_id=owner2.id, clock=clock) as uow:
        updated = service.update_member_role(
            uow=uow,
            tenant_id=org.id,
            target_user_id=owner1.id,
            new_role="ADMIN",
        )

    assert updated.role == "ADMIN"


def test_ownership_transfer_atomic_handoff(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)

    current_owner = User(
        issuer="https://idp", subject="sub-co", email="current@test.com", email_verified=True
    )
    target_member = User(
        issuer="https://idp", subject="sub-tm", email="target@test.com", email_verified=True
    )
    db.add_all([current_owner, target_member])
    org = Organization(name="Handoff Corp", slug="handoff-corp")
    db.add(org)
    db.flush()

    mem_owner = Membership(
        tenant_id=org.id, user_id=current_owner.id, role="OWNER", status="ACTIVE"
    )
    mem_target = Membership(
        tenant_id=org.id, user_id=target_member.id, role="ADMIN", status="ACTIVE"
    )
    db.add_all([mem_owner, mem_target])
    db.commit()

    with UnitOfWork(session=db, tenant_id=org.id, subject_id=current_owner.id, clock=clock) as uow:
        service.transfer_ownership(
            uow=uow,
            tenant_id=org.id,
            current_owner_id=current_owner.id,
            target_user_id=target_member.id,
        )

    # Invariant: Current owner demoted to ADMIN, Target promoted to OWNER
    db.refresh(mem_owner)
    db.refresh(mem_target)
    assert mem_owner.role == "ADMIN"
    assert mem_target.role == "OWNER"


def test_invitation_lifecycle_hashed_token_and_acceptance(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)
    mail_sink = InMemorySystemMailProvider()

    inviter = User(
        issuer="https://idp", subject="sub-inv", email="admin@test.com", email_verified=True
    )
    invitee = User(
        issuer="https://idp", subject="sub-cand", email="invitee@test.com", email_verified=True
    )
    db.add_all([inviter, invitee])
    org = Organization(name="Invite Corp", slug="invite-corp")
    db.add(org)
    db.flush()

    with UnitOfWork(session=db, tenant_id=org.id, subject_id=inviter.id, clock=clock) as uow:
        inv, raw_token = service.create_invitation(
            uow=uow,
            tenant_id=org.id,
            invited_by_user_id=inviter.id,
            email="invitee@test.com",
            role="RECRUITER",
            mail_provider=mail_sink,
        )

    # Invariant: Database stores only SHA-256 token hash, never raw token!
    assert inv.token_hash != raw_token
    assert len(inv.token_hash) == 64
    assert len(mail_sink.outbox) == 1
    assert "RECRUITER" in mail_sink.outbox[0].body_text

    # Accept invitation
    with UnitOfWork(session=db, tenant_id=None, subject_id=invitee.id, clock=clock) as uow:
        new_membership = service.accept_invitation(
            uow=uow,
            token=raw_token,
            accepting_user_id=invitee.id,
        )

    assert new_membership.role == "RECRUITER"
    assert new_membership.tenant_id == org.id

    # Invariant: Invitation is single-use; subsequent redemption must be rejected!
    with pytest.raises(ProblemException) as exc:
        with UnitOfWork(session=db, tenant_id=None, subject_id=invitee.id, clock=clock) as uow:
            service.accept_invitation(
                uow=uow,
                token=raw_token,
                accepting_user_id=invitee.id,
            )
    assert exc.value.status_code == 400
    assert exc.value.code == "INVITATION_INVALID"


def test_invitation_email_mismatch_rejected_with_403(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)

    inviter = User(
        issuer="https://idp", subject="sub-inv2", email="admin2@test.com", email_verified=True
    )
    wrong_user = User(
        issuer="https://idp", subject="sub-wrong", email="wrong@test.com", email_verified=True
    )
    db.add_all([inviter, wrong_user])
    org = Organization(name="Mismatch Corp", slug="mismatch-corp")
    db.add(org)
    db.flush()

    with UnitOfWork(session=db, tenant_id=org.id, subject_id=inviter.id, clock=clock) as uow:
        _, raw_token = service.create_invitation(
            uow=uow,
            tenant_id=org.id,
            invited_by_user_id=inviter.id,
            email="intended@test.com",
            role="INTERVIEWER",
        )

    # Attempting to accept with wrong logged-in email -> 403 Forbidden
    with pytest.raises(ProblemException) as exc:
        with UnitOfWork(session=db, tenant_id=None, subject_id=wrong_user.id, clock=clock) as uow:
            service.accept_invitation(
                uow=uow,
                token=raw_token,
                accepting_user_id=wrong_user.id,
            )

    assert exc.value.status_code == 403
    assert exc.value.code == "INVITATION_EMAIL_MISMATCH"


def test_expired_invitation_rejected_with_400(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FrozenClock()
    db = session_factory()
    service = OrganizationService(clock=clock)

    inviter = User(
        issuer="https://idp", subject="sub-inv3", email="admin3@test.com", email_verified=True
    )
    invitee = User(
        issuer="https://idp", subject="sub-exp", email="exp@test.com", email_verified=True
    )
    db.add_all([inviter, invitee])
    org = Organization(name="Expiry Corp", slug="expiry-corp")
    db.add(org)
    db.flush()

    with UnitOfWork(session=db, tenant_id=org.id, subject_id=inviter.id, clock=clock) as uow:
        _, raw_token = service.create_invitation(
            uow=uow,
            tenant_id=org.id,
            invited_by_user_id=inviter.id,
            email="exp@test.com",
            role="INTERVIEWER",
        )

    # Advance clock past 7 days TTL
    clock.set_time(clock.now() + timedelta(days=8))

    with pytest.raises(ProblemException) as exc:
        with UnitOfWork(session=db, tenant_id=None, subject_id=invitee.id, clock=clock) as uow:
            service.accept_invitation(
                uow=uow,
                token=raw_token,
                accepting_user_id=invitee.id,
            )

    assert exc.value.status_code == 400
    assert exc.value.code == "INVITATION_EXPIRED"
