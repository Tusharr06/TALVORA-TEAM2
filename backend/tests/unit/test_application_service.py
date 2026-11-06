"""Unit tests for ApplicationService: human-controlled hiring pipeline."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from talvora.modules.applications.application.service import ApplicationService
from talvora.modules.applications.domain.models import (
    Application,
    ApplicationSource,
    ApplicationStage,
    StageEvent,
)
from talvora.modules.candidates.application.service import CandidateService
from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.identity.domain.roles import Role, get_capabilities_for_role
from talvora.modules.jobs.application.service import JobService
from talvora.modules.jobs.domain.models import (
    CriterionDraft,
    CriterionType,
    JobRole,
    JobStatus,
    SkillPresentConfig,
)
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


def _create_open_job(
    uow: UnitOfWork, actor: ActorContext, title: str = "Software Engineer"
) -> UUID:
    """Create and publish a job so it reaches OPEN status, returning its ID."""
    criterion = CriterionDraft(
        criterion_type=CriterionType.SKILL_PRESENT,
        is_required=True,
        source_requirement_text="Proficiency in Python",
        relevance_rationale="Core stack.",
        configuration=SkillPresentConfig(skill_name="Python"),
    )
    job = JobService.create_job(
        uow=uow,
        actor=actor,
        title=title,
        description="Builds backend services.",
        initial_criteria=[criterion],
    )
    uow.session.flush()
    JobService.publish_requirements(uow=uow, actor=actor, job_id=job.id, expected_version=1)
    uow.session.flush()
    return job.id


# ============================================================================
# Creation and intake
# ============================================================================


def test_create_application_requires_open_job(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job = JobService.create_job(
            uow=uow, actor=actor, title="Draft Job", description="Not published yet."
        )

    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.create_application(
            uow=uow,
            actor=actor,
            job_id=job.id,
            new_candidate_display_name="Jane Doe",
            new_candidate_email="jane@example.com",
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "JOB_NOT_ACCEPTING_INTAKE"


def test_create_application_with_new_draft_candidate_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job_id = _create_open_job(uow, actor)

    with uow:
        application = ApplicationService.create_application(
            uow=uow,
            actor=actor,
            job_id=job_id,
            new_candidate_is_draft=True,
            source=ApplicationSource.UPLOAD_INTAKE,
        )

    assert application.stage == ApplicationStage.NEW.value
    assert application.intake_cycle == 1
    candidate = session.get(Candidate, (tenant_id, application.candidate_id))
    assert candidate is not None
    assert candidate.is_draft is True


def test_duplicate_active_application_rejected_with_409(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job_id = _create_open_job(uow, actor)
        candidate = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="jane@example.com"
        )

    with uow:
        ApplicationService.create_application(
            uow=uow, actor=actor, job_id=job_id, candidate_id=candidate.id
        )

    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.create_application(
            uow=uow, actor=actor, job_id=job_id, candidate_id=candidate.id
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "DUPLICATE_ACTIVE_APPLICATION"


def test_withdrawn_application_allows_a_new_intake_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job_id = _create_open_job(uow, actor)
        candidate = CandidateService.create_candidate(
            uow=uow, actor=actor, display_name="Jane Doe", email="jane@example.com"
        )

    with uow:
        first = ApplicationService.create_application(
            uow=uow, actor=actor, job_id=job_id, candidate_id=candidate.id
        )

    with uow:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=first.id,
            target_stage=ApplicationStage.WITHDRAWN,
            reason="Candidate withdrew interest.",
            expected_version=1,
        )

    with uow:
        second = ApplicationService.create_application(
            uow=uow, actor=actor, job_id=job_id, candidate_id=candidate.id
        )

    assert second.id != first.id
    assert second.intake_cycle == 2


def test_upload_intake_existing_candidate_selection_requires_recruiter(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    recruiter, recruiter_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    hiring_manager, hm_id = _create_test_actor(tenant_id, role=Role.HIRING_MANAGER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, recruiter_id, Role.RECRUITER.value)
    _seed_org_and_membership(session, tenant_id, hm_id, Role.HIRING_MANAGER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job_id = _create_open_job(uow, recruiter)
        candidate = CandidateService.create_candidate(
            uow=uow, actor=recruiter, display_name="Jane Doe", email="jane@example.com"
        )

    # The hiring manager role independently holds CREATE_APPLICATION capability, but the
    # upload-intake "select an existing candidate" action is restricted to recruiters.
    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.create_application(
            uow=uow,
            actor=hiring_manager,
            job_id=job_id,
            candidate_id=candidate.id,
            source=ApplicationSource.UPLOAD_INTAKE,
        )
    assert exc.value.status_code == 403

    with uow:
        application = ApplicationService.create_application(
            uow=uow,
            actor=recruiter,
            job_id=job_id,
            candidate_id=candidate.id,
            source=ApplicationSource.UPLOAD_INTAKE,
        )
    assert application.candidate_id == candidate.id


# ============================================================================
# Stage transitions: allowed/forbidden edges
# ============================================================================


def _bootstrap_application(uow: UnitOfWork, actor: ActorContext) -> Application:
    job_id = _create_open_job(uow, actor)
    candidate = CandidateService.create_candidate(
        uow=uow, actor=actor, display_name="Jane Doe", email="jane@example.com"
    )
    return ApplicationService.create_application(
        uow=uow, actor=actor, job_id=job_id, candidate_id=candidate.id
    )


@pytest.mark.parametrize(
    "target,reason",
    [
        (ApplicationStage.IN_REVIEW, None),
        (ApplicationStage.REJECTED, "Not a fit."),
        (ApplicationStage.WITHDRAWN, "Candidate withdrew."),
    ],
)
def test_allowed_transitions_from_new(
    session_factory: sessionmaker[Session], target: ApplicationStage, reason: str | None
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow:
        updated = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=target,
            reason=reason,
            expected_version=1,
        )
    assert updated.stage == target.value


@pytest.mark.parametrize(
    "target",
    [ApplicationStage.INTERVIEWING, ApplicationStage.DECISION_PENDING, ApplicationStage.HIRED],
)
def test_forbidden_skip_transitions_from_new(
    session_factory: sessionmaker[Session], target: ApplicationStage
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=target,
            reason="irrelevant",
            expected_version=1,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "INVALID_TRANSITION"


def test_full_pipeline_progression_and_hired_is_terminal(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    sequence = [
        ApplicationStage.IN_REVIEW,
        ApplicationStage.INTERVIEW_APPROVED,
        ApplicationStage.INTERVIEWING,
        ApplicationStage.DECISION_PENDING,
        ApplicationStage.HIRED,
    ]
    version = 1
    for target in sequence:
        with uow:
            application = ApplicationService.transition_stage(
                uow=uow,
                actor=actor,
                application_id=application.id,
                target_stage=target,
                expected_version=version,
            )
        version += 1

    assert application.stage == ApplicationStage.HIRED.value

    # HIRED is fully terminal: no reopen transition exists.
    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            reason="Reconsider",
            expected_version=version,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "INVALID_TRANSITION"


def test_reopen_terminal_decision_requires_reason(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow:
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.REJECTED,
            reason="Not qualified.",
            expected_version=1,
        )

    # Missing reason on reopen -> 422.
    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=2,
        )
    assert exc.value.status_code == 422

    # With a reason, reopen succeeds.
    with uow:
        reopened = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            reason="Hiring manager requested a second look.",
            expected_version=2,
        )
    assert reopened.stage == ApplicationStage.IN_REVIEW.value


def test_missing_reason_for_rejection_is_422(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.REJECTED,
            expected_version=1,
        )
    assert exc.value.status_code == 422


def test_job_closure_blocks_new_interview_approvals(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow:
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )

    with uow:
        JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=application.job_id,
            target_status=JobStatus.CLOSED,
            reason="Headcount frozen.",
            expected_version=2,
        )

    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.INTERVIEW_APPROVED,
            expected_version=2,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "JOB_NOT_ACCEPTING_INTAKE"

    # In-flight work is preserved: rejecting/withdrawing a closed job's application still works.
    with uow:
        rejected = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.REJECTED,
            reason="Role was closed.",
            expected_version=2,
        )
    assert rejected.stage == ApplicationStage.REJECTED.value


# ============================================================================
# Human authority, missing actor, worker denial
# ============================================================================


def test_missing_actor_rejected_with_401(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    anonymous = ActorContext()
    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=anonymous,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
    assert exc.value.status_code == 401


@pytest.mark.parametrize("principal_kind", ["SYSTEM_WORKER", "AI_MODEL"])
def test_worker_and_model_principals_cannot_invoke_hiring_transitions(
    session_factory: sessionmaker[Session], principal_kind: str
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    worker_actor = ActorContext(
        principal=actor.principal,
        membership=actor.membership,
        principal_kind=principal_kind,  # type: ignore[arg-type]
    )
    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=worker_actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "CAPABILITY_DENIED"

    # The application was not mutated by the denied worker attempt.
    refreshed = session.get(Application, (tenant_id, application.id))
    assert refreshed is not None
    assert refreshed.stage == ApplicationStage.NEW.value


def test_interviewer_cannot_transition_stage(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    recruiter, recruiter_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    interviewer, interviewer_id = _create_test_actor(tenant_id, role=Role.INTERVIEWER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, recruiter_id, Role.RECRUITER.value)
    _seed_org_and_membership(session, tenant_id, interviewer_id, Role.INTERVIEWER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, recruiter)

    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=interviewer,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
    assert exc.value.status_code == 403


# ============================================================================
# Concurrency and version conflicts ("simultaneous decisions")
# ============================================================================


def test_simultaneous_decisions_second_caller_gets_version_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow:
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.INTERVIEW_APPROVED,
            expected_version=2,
        )
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.INTERVIEWING,
            expected_version=3,
        )
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.DECISION_PENDING,
            expected_version=4,
        )

    # Two recruiters both observed version=5 (DECISION_PENDING) and raced to decide.
    with uow:
        winner = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.HIRED,
            expected_version=5,
        )
    assert winner.stage == ApplicationStage.HIRED.value

    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.REJECTED,
            reason="Too late, already decided.",
            expected_version=5,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "VERSION_CONFLICT"


def test_repeated_identical_transition_is_rejected_not_silently_reapplied(
    session_factory: sessionmaker[Session],
) -> None:
    """Calling the same transition twice without a fresh version is rejected, not re-run."""
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow:
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )

    # Same expected_version replayed again: stale version -> 409 (the HTTP layer's
    # Idempotency-Key handling is what makes an exact replay return the cached response
    # instead of re-invoking this service method).
    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
    assert exc.value.status_code in (409,)

    events = session.query(StageEvent).filter_by(application_id=application.id).all()
    # Exactly two events: the initial NEW creation event, and the single IN_REVIEW transition.
    assert len(events) == 2


# ============================================================================
# Eligibility, cross-tenant, shared candidate across jobs, role-scoped search
# ============================================================================


def test_withdrawn_application_is_not_interview_eligible(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow:
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.WITHDRAWN,
            reason="No longer interested.",
            expected_version=1,
        )

    with uow:
        result = ApplicationService.get_interview_eligibility(
            uow=uow, actor=actor, application_id=application.id
        )
    reasons = list(result["reasons"])  # type: ignore[call-overload]
    assert result["eligible"] is False
    assert any("not interview-eligible" in r for r in reasons)


def test_interview_approved_without_verified_contact_is_not_eligible(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor, user_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, user_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        application = _bootstrap_application(uow, actor)

    with uow:
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application.id,
            target_stage=ApplicationStage.INTERVIEW_APPROVED,
            expected_version=2,
        )

    with uow:
        result = ApplicationService.get_interview_eligibility(
            uow=uow, actor=actor, application_id=application.id
        )
    reasons = list(result["reasons"])  # type: ignore[call-overload]
    assert result["eligible"] is False
    assert any("not been human-verified" in r for r in reasons)


def test_cross_tenant_candidate_job_pairing_rejected_with_404(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    actor_a, user_a = _create_test_actor(tenant_a, role=Role.RECRUITER.value)
    actor_b, user_b = _create_test_actor(tenant_b, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_a, user_a, Role.RECRUITER.value)
    _seed_org_and_membership(session, tenant_b, user_b, Role.RECRUITER.value)

    uow_a = UnitOfWork(session=session, tenant_id=tenant_a)
    with uow_a:
        job_id_a = _create_open_job(uow_a, actor_a, title="Tenant A Job")

    uow_b = UnitOfWork(session=session, tenant_id=tenant_b)
    with uow_b:
        candidate_b = CandidateService.create_candidate(
            uow=uow_b, actor=actor_b, display_name="Cross Tenant Candidate", email="x@example.com"
        )

    # Tenant A actor tries to create an application against Tenant A's job but using
    # Tenant B's candidate ID -- must be concealed as not-found, never linked.
    with uow_a, pytest.raises(ProblemException) as exc:
        ApplicationService.create_application(
            uow=uow_a, actor=actor_a, job_id=job_id_a, candidate_id=candidate_b.id
        )
    assert exc.value.status_code == 404

    # And a Tenant B actor cannot reference Tenant A's job either.
    with uow_b, pytest.raises(ProblemException) as exc2:
        ApplicationService.create_application(
            uow=uow_b, actor=actor_b, job_id=job_id_a, candidate_id=candidate_b.id
        )
    assert exc2.value.status_code == 404


def test_shared_candidate_across_two_jobs_with_different_assignments(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    recruiter_a, recruiter_a_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    recruiter_b, recruiter_b_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, recruiter_a_id, Role.RECRUITER.value)
    _seed_org_and_membership(session, tenant_id, recruiter_b_id, Role.RECRUITER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job_a = _create_open_job(uow, recruiter_a, title="Job A")
        job_b = _create_open_job(uow, recruiter_b, title="Job B")
        candidate = CandidateService.create_candidate(
            uow=uow, actor=recruiter_a, display_name="Shared Candidate", email="shared@example.com"
        )

    with uow:
        app_a = ApplicationService.create_application(
            uow=uow, actor=recruiter_a, job_id=job_a, candidate_id=candidate.id
        )
        app_b = ApplicationService.create_application(
            uow=uow, actor=recruiter_b, job_id=job_b, candidate_id=candidate.id
        )

    # Recruiter A (assigned only to Job A) cannot transition the application on Job B.
    with uow, pytest.raises(ProblemException) as exc:
        ApplicationService.transition_stage(
            uow=uow,
            actor=recruiter_a,
            application_id=app_b.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
    assert exc.value.status_code == 403

    # But Recruiter A can transition their own Job A application.
    with uow:
        updated_a = ApplicationService.transition_stage(
            uow=uow,
            actor=recruiter_a,
            application_id=app_a.id,
            target_stage=ApplicationStage.IN_REVIEW,
            expected_version=1,
        )
    assert updated_a.stage == ApplicationStage.IN_REVIEW.value

    # Job B's application is untouched by Recruiter A's denied attempt.
    refreshed_b = session.get(Application, (tenant_id, app_b.id))
    assert refreshed_b is not None
    assert refreshed_b.stage == ApplicationStage.NEW.value


def test_role_scoped_search_interviewer_sees_only_assigned_jobs(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    owner, owner_id = _create_test_actor(tenant_id, role=Role.OWNER.value)
    recruiter, recruiter_id = _create_test_actor(tenant_id, role=Role.RECRUITER.value)
    interviewer, interviewer_id = _create_test_actor(tenant_id, role=Role.INTERVIEWER.value)
    session = session_factory()
    _seed_org_and_membership(session, tenant_id, owner_id, Role.OWNER.value)
    _seed_org_and_membership(session, tenant_id, recruiter_id, Role.RECRUITER.value)
    _seed_org_and_membership(session, tenant_id, interviewer_id, Role.INTERVIEWER.value)
    uow = UnitOfWork(session=session, tenant_id=tenant_id)

    with uow:
        job_a = _create_open_job(uow, recruiter, title="Job A")
        job_b = _create_open_job(uow, recruiter, title="Job B")
        candidate_1 = CandidateService.create_candidate(
            uow=uow, actor=recruiter, display_name="Candidate One", email="one@example.com"
        )
        candidate_2 = CandidateService.create_candidate(
            uow=uow, actor=recruiter, display_name="Candidate Two", email="two@example.com"
        )

    with uow:
        ApplicationService.create_application(
            uow=uow, actor=recruiter, job_id=job_a, candidate_id=candidate_1.id
        )
        ApplicationService.create_application(
            uow=uow, actor=recruiter, job_id=job_b, candidate_id=candidate_2.id
        )

    # Interviewer has no assignment yet: sees nothing.
    with uow:
        results, _, total = ApplicationService.list_applications(uow=uow, actor=interviewer)
    assert total == 0
    assert results == []

    # Assign the interviewer only to Job A (org OWNER holds assignment-management authority).
    with uow:
        JobService.assign_member(
            uow=uow,
            actor=owner,
            job_id=job_a,
            user_id=interviewer_id,
            role=JobRole.INTERVIEWER,
        )

    with uow:
        results, _, total = ApplicationService.list_applications(uow=uow, actor=interviewer)
    assert total == 1
    assert results[0].job_id == job_a

    # The recruiter (unrestricted) sees both.
    with uow:
        recruiter_results, _, recruiter_total = ApplicationService.list_applications(
            uow=uow, actor=recruiter
        )
    assert recruiter_total == 2
