"""Unit tests for JobService business logic, invariant enforcement, and state transitions."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from talvora.modules.identity.domain.roles import Role
from talvora.modules.jobs.application.service import JobService
from talvora.modules.jobs.domain.models import (
    CriterionDraft,
    CriterionType,
    JobAssignment,
    JobRole,
    JobStatus,
    ManualRequirementConfig,
    MinExperienceMonthsConfig,
    RequirementVersion,
    SkillPresentConfig,
)
from talvora.modules.operations.domain.models import OutboxEvent
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
    )
    actor = ActorContext(
        principal=principal,
        membership=membership,
    )
    return actor, uid


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


def test_create_job_assigns_creator_as_recruiter(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)

    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job = JobService.create_job(
            uow=uow,
            actor=actor,
            title="Senior Staff Distributed Systems Engineer",
            description="Leading architecture for high-throughput distributed transaction engines.",
            department="Core Infrastructure",
            location="Remote",
        )

    assert job.status == JobStatus.DRAFT.value
    assert job.version == 1
    assert job.creator_id == user_id

    # Check creator assignment
    assignments = session.query(JobAssignment).filter_by(job_id=job.id).all()
    assert len(assignments) == 1
    assert assignments[0].user_id == user_id
    assert assignments[0].role == JobRole.RECRUITER.value


def test_empty_publication_rejected_with_422(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)

    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job = JobService.create_job(
            uow=uow,
            actor=actor,
            title="Empty Job Post",
            description="No criteria specified.",
        )

    with uow, pytest.raises(ProblemException) as exc:
        JobService.publish_requirements(uow=uow, actor=actor, job_id=job.id)

    assert exc.value.status_code == 422
    assert exc.value.code == "EMPTY_REQUIREMENTS"


def test_publishing_validates_criteria_creates_immutable_version_and_emits_outbox(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)

    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    skill_criterion = CriterionDraft(
        criterion_type=CriterionType.SKILL_PRESENT,
        is_required=True,
        source_requirement_text="Proficiency in Python and async programming",
        relevance_rationale="Core platform services are written in Python and FastAPI.",
        configuration=SkillPresentConfig(
            skill_name="Python",
            canonical_name="python",
            synonyms=["python3", "py"],
        ),
    )
    manual_criterion = CriterionDraft(
        criterion_type=CriterionType.MANUAL_REQUIREMENT,
        is_required=True,
        source_requirement_text="Must possess active US Top Secret security clearance",
        relevance_rationale="Department of Defense client mandate.",
        requires_human_review=True,
        configuration=ManualRequirementConfig(
            human_verification_prompt="Confirm active clearance level via JPAS/DISS lookup.",
            category="SECURITY_CLEARANCE",
        ),
    )

    with uow:
        job = JobService.create_job(
            uow=uow,
            actor=actor,
            title="Lead Security Systems Architect",
            description="Hardening defense mission systems.",
            initial_criteria=[skill_criterion, manual_criterion],
        )

    # Publish requirements
    with uow:
        published_job, req_ver = JobService.publish_requirements(
            uow=uow,
            actor=actor,
            job_id=job.id,
            change_summary="Initial baseline publication",
            expected_version=1,
        )

    assert published_job.status == JobStatus.OPEN.value
    assert published_job.current_published_version_number == 1
    assert published_job.version == 2
    assert req_ver.version_number == 1
    assert req_ver.published_by == user_id
    assert len(req_ver.criteria) == 2

    # Verify Outbox event was emitted
    outbox_events = session.query(OutboxEvent).filter_by(tenant_id=tenant_id).all()
    assert len(outbox_events) >= 1
    assert any(
        e.topic == "jobs.requirements.published" and e.payload_ref == req_ver.id
        for e in outbox_events
    )


def test_draft_versus_published_mutation_isolation(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)

    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    criterion_v1 = CriterionDraft(
        criterion_type=CriterionType.MIN_EXPERIENCE_MONTHS,
        is_required=True,
        source_requirement_text="Minimum 3 years of software engineering experience",
        relevance_rationale="Requires independent delivery capability.",
        configuration=MinExperienceMonthsConfig(
            min_months=36,
            relevant_titles=["Software Engineer"],
        ),
    )

    with uow:
        job = JobService.create_job(
            uow=uow,
            actor=actor,
            title="Software Engineer II",
            description="Developing web APIs.",
            initial_criteria=[criterion_v1],
        )

    with uow:
        _, req_v1 = JobService.publish_requirements(
            uow=uow,
            actor=actor,
            job_id=job.id,
            change_summary="V1 publication",
            expected_version=1,
        )

    # Modify draft requirements
    criterion_v2 = CriterionDraft(
        criterion_type=CriterionType.MIN_EXPERIENCE_MONTHS,
        is_required=True,
        source_requirement_text="Minimum 5 years of software engineering experience",
        relevance_rationale="Elevated role scope.",
        configuration=MinExperienceMonthsConfig(
            min_months=60,
            relevant_titles=["Senior Software Engineer"],
        ),
    )

    with uow:
        updated_job = JobService.update_draft_requirements(
            uow=uow,
            actor=actor,
            job_id=job.id,
            criteria=[criterion_v2],
            expected_version=2,
        )

    # Historical requirement version remains untouched
    persisted_v1 = session.query(RequirementVersion).filter_by(id=req_v1.id).one()
    assert persisted_v1.version_number == 1
    assert persisted_v1.criteria[0].configuration["min_months"] == 36

    # Publish Version 2
    with uow:
        _, req_v2 = JobService.publish_requirements(
            uow=uow,
            actor=actor,
            job_id=job.id,
            change_summary="V2 increased experience requirement",
            expected_version=3,
        )

    assert req_v2.version_number == 2
    assert req_v2.criteria[0].configuration["min_months"] == 60
    assert updated_job.current_published_version_number == 2


def test_assignment_denials_unassigned_admin_cannot_publish_or_transition(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    recruiter_actor, recruiter_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    admin_actor, admin_id = _create_test_actor(tenant_id, role=Role.ADMIN.value)

    session = session_factory()
    _seed_org_and_membership(session, tenant_id, recruiter_id, Role.RECRUITER.value)
    _seed_org_and_membership(session, tenant_id, admin_id, Role.ADMIN.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    criterion = CriterionDraft(
        criterion_type=CriterionType.SKILL_PRESENT,
        is_required=True,
        source_requirement_text="Knowledge of Kubernetes",
        relevance_rationale="Deployment target.",
        configuration=SkillPresentConfig(skill_name="Kubernetes"),
    )

    with uow:
        job = JobService.create_job(
            uow=uow,
            actor=recruiter_actor,
            title="DevOps Specialist",
            description="Kube cluster ops.",
            initial_criteria=[criterion],
        )

    # Unassigned admin attempts to publish requirements -> 403 Forbidden
    with uow, pytest.raises(ProblemException) as exc:
        JobService.publish_requirements(uow=uow, actor=admin_actor, job_id=job.id)

    assert exc.value.status_code == 403
    assert exc.value.code == "CAPABILITY_DENIED"


def test_job_state_machine_rules_and_reopening(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)

    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    crit = CriterionDraft(
        criterion_type=CriterionType.SKILL_PRESENT,
        is_required=True,
        source_requirement_text="Go programming",
        relevance_rationale="Service rewrite.",
        configuration=SkillPresentConfig(skill_name="Go"),
    )

    with uow:
        job = JobService.create_job(
            uow=uow,
            actor=actor,
            title="Systems Engineer",
            description="Go development.",
            initial_criteria=[crit],
        )

    # 1. DRAFT -> OPEN without publish is rejected
    with uow, pytest.raises(ProblemException) as exc1:
        JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=job.id,
            target_status=JobStatus.OPEN,
            expected_version=1,
        )
    assert exc1.value.status_code == 409
    assert exc1.value.code == "INVALID_TRANSITION"

    # Publish requirements -> automatically opens job
    with uow:
        JobService.publish_requirements(uow=uow, actor=actor, job_id=job.id, expected_version=1)

    assert job.status == JobStatus.OPEN.value

    # 2. OPEN -> PAUSED
    with uow:
        JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=job.id,
            target_status=JobStatus.PAUSED,
            expected_version=2,
        )
    assert job.status == JobStatus.PAUSED.value

    # 3. PAUSED -> CLOSED without reason is rejected
    with uow, pytest.raises(ProblemException) as exc2:
        JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=job.id,
            target_status=JobStatus.CLOSED,
            reason="",
            expected_version=3,
        )
    assert exc2.value.status_code == 422

    # 4. PAUSED -> CLOSED with reason
    with uow:
        JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=job.id,
            target_status=JobStatus.CLOSED,
            reason="Headcount reprioritization for FY26.",
            expected_version=3,
        )
    assert job.status == JobStatus.CLOSED.value
    assert job.closed_reason == "Headcount reprioritization for FY26."

    # 5. CLOSED -> OPEN requires explicit reason
    with uow, pytest.raises(ProblemException) as exc3:
        JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=job.id,
            target_status=JobStatus.OPEN,
            reason=None,
            expected_version=4,
        )
    assert exc3.value.status_code == 422

    # 6. Reopen with reason succeeds
    with uow:
        JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=job.id,
            target_status=JobStatus.OPEN,
            reason="Headcount budget restored.",
            expected_version=4,
        )
    assert job.status == JobStatus.OPEN.value
    assert job.closed_reason is None


def test_optimistic_concurrency_version_conflict(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)

    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job = JobService.create_job(
            uow=uow,
            actor=actor,
            title="Concurrency Target",
            description="Testing race conditions.",
        )

    # Calling update with wrong expected_version -> 409 VERSION_CONFLICT
    with uow, pytest.raises(ProblemException) as exc:
        JobService.update_job(
            uow=uow,
            actor=actor,
            job_id=job.id,
            title="Updated Title",
            expected_version=99,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "VERSION_CONFLICT"
