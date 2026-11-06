"""Unit tests for CandidateService: identity, contact revisions, and verifications."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from talvora.modules.candidates.application.service import CandidateService
from talvora.modules.candidates.domain.models import (
    Candidate,
    CandidateContactEvent,
    CandidateContactRevision,
    CandidateSource,
    ContactEventType,
    ContactVerificationSource,
    mask_email,
)
from talvora.modules.identity.domain.roles import Role, get_capabilities_for_role
from talvora.modules.organizations.domain.models import Membership, Organization
from talvora.shared.application.actor import ActorContext, MembershipContext, Principal
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"identity": None}},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, autocommit=False, autoflush=False)


def _create_test_actor(
    tenant_id: UUID,
    role: str = Role.RECRUITER.value,
    user_id: UUID | None = None,
) -> tuple[ActorContext, UUID]:
    uid = user_id or uuid4()
    principal = Principal(
        user_id=uid, email=f"user-{uid}@example.com", issuer="iss", subject=f"sub-{uid}"
    )
    membership = MembershipContext(
        membership_id=uuid4(),
        organization_id=tenant_id,
        role=role,  # type: ignore[arg-type]
        capabilities=get_capabilities_for_role(role),
    )
    return ActorContext(principal=principal, membership=membership), uid


def _seed_org_and_membership(session: Session, tenant_id: UUID, user_id: UUID, role: str) -> None:
    now = datetime.now(UTC)
    org = session.get(Organization, tenant_id)
    if not org:
        org = Organization(id=tenant_id, name="Test Org", slug=f"org-{tenant_id}")
        session.add(org)
    mem = Membership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        status="ACTIVE",
        created_at=now,
        updated_at=now,
    )
    session.add(mem)
    session.flush()


def test_draft_candidate_requires_no_name_or_email(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        candidate = CandidateService.create_draft_for_intake(uow=uow, actor=actor)

    assert candidate.is_draft is True
    assert candidate.display_name is None
    assert candidate.email is None
    assert candidate.source == CandidateSource.UPLOAD_INTAKE.value


def test_non_draft_candidate_requires_name_or_email(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow, pytest.raises(ProblemException) as exc:
        CandidateService.create_candidate(uow=uow, actor=actor, is_draft=False)
    assert exc.value.status_code == 422


def test_malformed_email_rejected_with_422(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow, pytest.raises(ProblemException) as exc:
        CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="not-an-email"
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "EVIDENCE_INVALID"


def test_candidate_creation_sanitizes_html_and_normalizes_search_fields(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        candidate = CandidateService.create_candidate(
            uow=uow,
            actor=actor,
            display_name="<script>alert(1)</script>Jane Doe",
            email="  Jane.DOE@Example.COM  ",
        )

    assert "<script>" not in (candidate.display_name or "")
    assert candidate.email == "Jane.DOE@Example.COM"
    assert candidate.normalized_email == "jane.doe@example.com"
    assert candidate.normalized_name == "jane doe"
    assert candidate.current_contact_revision_number == 1


def test_duplicate_email_surfaces_suggestion_without_merging(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        first = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="shared@example.com"
        )

    with uow:
        second = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Impersonator", email="shared@example.com"
        )

    # Both candidates persist independently -- no automatic merge occurred.
    assert first.id != second.id
    all_candidates = session.query(Candidate).filter_by(tenant_id=tenant_id).all()
    assert len(all_candidates) == 2

    with uow:
        suggestions = CandidateService.find_duplicate_suggestions(
            uow=uow, actor=actor, email="shared@example.com", exclude_candidate_id=second.id
        )

    assert len(suggestions) == 1
    assert suggestions[0]["candidate_id"] == first.id
    # Minimum data exposure: the email is masked, not shown in full.
    assert suggestions[0]["masked_email"] == mask_email("shared@example.com")
    assert suggestions[0]["masked_email"] != "shared@example.com"


def test_update_candidate_appends_contact_revision_and_invalidates_verification(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        candidate = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="jane@example.com"
        )

    with uow:
        CandidateService.verify_contact(
            uow=uow,
            actor=actor,
            candidate_id=candidate.id,
            contact_revision_number=1,
            verified_address="jane@example.com",
            verification_source=ContactVerificationSource.RECRUITER_ATTESTATION,
            expected_version=1,
        )

    refreshed = session.get(Candidate, (tenant_id, candidate.id))
    assert refreshed is not None
    assert refreshed.is_contact_verified is True

    # Changing the email appends a new revision and invalidates verified status.
    with uow:
        CandidateService.update_candidate(
            uow=uow,
            actor=actor,
            candidate_id=candidate.id,
            email="jane.new@example.com",
            expected_version=2,
        )

    refreshed2 = session.get(Candidate, (tenant_id, candidate.id))
    assert refreshed2 is not None
    assert refreshed2.current_contact_revision_number == 2
    assert refreshed2.verified_contact_revision_number == 1
    assert refreshed2.is_contact_verified is False

    revisions = session.query(CandidateContactRevision).filter_by(candidate_id=candidate.id).all()
    assert len(revisions) == 2

    # A versioned contact-change event exists for each revision/verification.
    events = session.query(CandidateContactEvent).filter_by(candidate_id=candidate.id).all()
    event_types = {e.event_type for e in events}
    assert ContactEventType.CONTACT_REVISED.value in event_types
    assert ContactEventType.CONTACT_VERIFIED.value in event_types


def test_verification_wrong_revision_number_rejected_with_422(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        candidate = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="jane@example.com"
        )

    with uow, pytest.raises(ProblemException) as exc:
        CandidateService.verify_contact(
            uow=uow,
            actor=actor,
            candidate_id=candidate.id,
            contact_revision_number=99,
            verified_address="jane@example.com",
            verification_source=ContactVerificationSource.RECRUITER_ATTESTATION,
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "EVIDENCE_INVALID"


def test_verification_address_mismatch_rejected_with_422(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        candidate = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="jane@example.com"
        )

    with uow, pytest.raises(ProblemException) as exc:
        CandidateService.verify_contact(
            uow=uow,
            actor=actor,
            candidate_id=candidate.id,
            contact_revision_number=1,
            verified_address="someone-else@example.com",
            verification_source=ContactVerificationSource.RECRUITER_ATTESTATION,
        )
    assert exc.value.status_code == 422


def test_verification_is_never_proof_of_ownership_and_requires_human_actor(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        candidate = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="jane@example.com"
        )

    worker_actor = ActorContext(
        principal=actor.principal, membership=actor.membership, principal_kind="SYSTEM_WORKER"
    )
    with uow, pytest.raises(ProblemException) as exc:
        CandidateService.verify_contact(
            uow=uow,
            actor=worker_actor,
            candidate_id=candidate.id,
            contact_revision_number=1,
            verified_address="jane@example.com",
            verification_source=ContactVerificationSource.RECRUITER_ATTESTATION,
        )
    assert exc.value.status_code == 403


def test_interviewer_cannot_create_or_edit_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.INTERVIEWER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.INTERVIEWER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow, pytest.raises(ProblemException) as exc:
        CandidateService.create_candidate(uow=uow, actor=actor, display_name="Jane Doe")
    assert exc.value.status_code == 403
    assert exc.value.code == "CAPABILITY_DENIED"


def test_missing_actor_rejected_with_401(session_factory: sessionmaker[Session]) -> None:
    session = session_factory()
    uow = UnitOfWork(session=session, tenant_id=None)
    anonymous = ActorContext()

    with uow, pytest.raises(ProblemException) as exc:
        CandidateService.create_candidate(uow=uow, actor=anonymous, display_name="Jane Doe")
    assert exc.value.status_code == 401
