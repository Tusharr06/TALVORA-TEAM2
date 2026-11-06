"""Unit Tests for P13: Human-Approved External Actions and Booking Saga."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.identity.domain.roles import Role, get_capabilities_for_role
from talvora.modules.integrations.infrastructure.google.calendar import MockGoogleCalendarAdapter
from talvora.modules.integrations.infrastructure.google.gmail import MockGoogleGmailAdapter
from talvora.modules.jobs.domain.models import Job, JobStatus
from talvora.modules.organizations.domain.models import Membership, Organization
from talvora.modules.scheduling.application.booking_saga import (
    BookingSagaService,
    compute_canonical_payload_hash,
)
from talvora.modules.scheduling.domain.errors import (
    ApprovalPayloadChangedError,
    CandidateContactNotVerifiedError,
    CandidateNotEligibleForInterviewError,
    ProviderResultUnknownError,
    SlotConflictError,
)
from talvora.modules.scheduling.domain.models import (
    ApprovalDecision,
    AvailabilitySnapshot,
    ExternalAction,
    ExternalActionStatus,
    ExternalActionType,
    InterviewParticipant,
    InterviewRequest,
    InterviewRequestStatus,
    InterviewReservation,
    InterviewReservationStatus,
    InterviewStatus,
    ParticipantRole,
    ProposalStatus,
    ProviderAttempt,
    ProviderAttemptStatus,
    SlotProposal,
)
from talvora.shared.application.actor import ActorContext, MembershipContext, Principal
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork


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


def _create_actor(tenant_id: UUID, role: str = Role.RECRUITER.value) -> ActorContext:
    uid = uuid4()
    principal = Principal(
        user_id=uid, email=f"user-{uid}@example.com", issuer="iss", subject=f"sub-{uid}"
    )
    membership = MembershipContext(
        membership_id=uuid4(),
        organization_id=tenant_id,
        role=role,  # type: ignore[arg-type]
        capabilities=get_capabilities_for_role(role),
    )
    return ActorContext(principal=principal, membership=membership)


def _seed_prerequisites(
    session: Session,
    tenant_id: UUID,
    actor: ActorContext,
    candidate_verified: bool = True,
    app_stage: str = ApplicationStage.INTERVIEW_APPROVED.value,
    job_status: str = JobStatus.OPEN.value,
) -> tuple[Candidate, Job, Application, InterviewRequest, SlotProposal, Membership]:
    now = datetime.now(UTC)

    # 1. Org and Recruiter Membership
    org = Organization(
        id=tenant_id, name="Talvora Test", slug=f"org-{tenant_id.hex[:6]}", created_at=now
    )
    session.add(org)

    recruiter_mem = Membership(
        id=actor.membership.membership_id,
        tenant_id=tenant_id,
        user_id=actor.user_id,
        role=Role.RECRUITER.value,
        status="ACTIVE",
        created_at=now,
    )
    session.add(recruiter_mem)

    # 2. Interviewer Membership
    interviewer_mem = Membership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=uuid4(),
        role=Role.INTERVIEWER.value,
        status="ACTIVE",
        created_at=now,
    )
    session.add(interviewer_mem)

    # 3. Candidate
    contact_rev_id = uuid4()
    candidate = Candidate(
        id=uuid4(),
        tenant_id=tenant_id,
        display_name="Alice Smith",
        email="alice@example.com",
        created_by=actor.user_id,
        current_contact_revision_id=contact_rev_id,
        current_contact_revision_number=1,
        verified_contact_revision_id=contact_rev_id if candidate_verified else None,
        verified_contact_revision_number=1 if candidate_verified else None,
        created_at=now,
    )
    session.add(candidate)

    # 4. Job
    job = Job(
        id=uuid4(),
        tenant_id=tenant_id,
        title="Staff Backend Engineer",
        description="Responsible for building backend services.",
        creator_id=actor.user_id,
        status=job_status,
        version=1,
        created_at=now,
    )
    session.add(job)

    # 5. Application
    app = Application(
        id=uuid4(),
        tenant_id=tenant_id,
        job_id=job.id,
        candidate_id=candidate.id,
        stage=app_stage,
        created_by=actor.user_id,
        created_at=now,
    )
    session.add(app)

    # 6. Interview Request
    target_start = now + timedelta(days=2)
    target_end = now + timedelta(days=5)
    int_req = InterviewRequest(
        id=uuid4(),
        tenant_id=tenant_id,
        application_id=app.id,
        candidate_id=candidate.id,
        job_id=job.id,
        title="System Design Interview",
        duration_minutes=60,
        target_date_start=target_start,
        target_date_end=target_end,
        timezone="UTC",
        organizer_membership_id=actor.membership.membership_id,
        status=InterviewRequestStatus.SLOTS_PROPOSED.value,
        created_by=actor.user_id,
        created_at=now,
    )
    session.add(int_req)

    # 7. Participant
    part = InterviewParticipant(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        membership_id=interviewer_mem.id,
        role=ParticipantRole.INTERVIEWER.value,
        is_required=True,
        calendar_id="interviewer@example.com",
        created_at=now,
    )
    session.add(part)

    # 8. Snapshot
    snap = AvailabilitySnapshot(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        interviewer_freebusy_snapshot={},
        candidate_availability_snapshot={},
        snapshot_hash="dummy_snapshot_hash",
        created_at=now,
    )
    session.add(snap)

    # 9. Slot Proposal
    slot_start = target_start + timedelta(hours=4)
    slot_end = slot_start + timedelta(minutes=60)
    proposal = SlotProposal(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        snapshot_id=snap.id,
        slot_start_utc=slot_start,
        slot_end_utc=slot_end,
        interviewer_membership_ids=[str(interviewer_mem.id)],
        status=ProposalStatus.PENDING_REVIEW.value,
        expires_at=now + timedelta(hours=24),
        created_at=now,
    )
    session.add(proposal)
    session.commit()

    return candidate, job, app, int_req, proposal, interviewer_mem


@pytest.mark.asyncio
async def test_draft_external_action_create_interview_success(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.CREATE_INTERVIEW.value,
                resource_type="interview_request",
                resource_id=req.id,
                proposal_id=prop.id,
            )
            assert action.status == ExternalActionStatus.PENDING_APPROVAL.value
            assert action.payload_hash == compute_canonical_payload_hash(action.payload)
            assert action.verified_recipient_email == "alice@example.com"
            assert action.payload["title"] == "System Design Interview"


@pytest.mark.asyncio
async def test_draft_action_fails_if_candidate_unverified(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(
            session, tenant_id, actor, candidate_verified=False
        )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            with pytest.raises(CandidateContactNotVerifiedError):
                BookingSagaService.create_external_action(
                    uow=uow,
                    actor=actor,
                    action_type=ExternalActionType.CREATE_INTERVIEW.value,
                    resource_type="interview_request",
                    resource_id=req.id,
                    proposal_id=prop.id,
                )


@pytest.mark.asyncio
async def test_draft_action_fails_if_application_not_interview_approved(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(
            session, tenant_id, actor, app_stage=ApplicationStage.IN_REVIEW.value
        )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            with pytest.raises(CandidateNotEligibleForInterviewError):
                BookingSagaService.create_external_action(
                    uow=uow,
                    actor=actor,
                    action_type=ExternalActionType.CREATE_INTERVIEW.value,
                    resource_type="interview_request",
                    resource_id=req.id,
                    proposal_id=prop.id,
                )


@pytest.mark.asyncio
async def test_approve_external_action_and_execute_booking_saga_success(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, int_mem = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.CREATE_INTERVIEW.value,
                resource_type="interview_request",
                resource_id=req.id,
                proposal_id=prop.id,
            )
            action_id = action.id
            payload_hash = action.payload_hash

    # Approve and execute booking saga
    cal_provider = MockGoogleCalendarAdapter()

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            approval, approved_action, interview = await BookingSagaService.approve_external_action(
                uow=uow,
                actor=actor,
                action_id=action_id,
                expected_payload_hash=payload_hash,
                expected_resource_version=1,
                calendar_provider=cal_provider,
            )
            assert approval.decision == ApprovalDecision.APPROVED.value
            assert approved_action.status == ExternalActionStatus.SUCCEEDED.value
            assert interview is not None
            assert interview.status == InterviewStatus.SCHEDULED.value
            assert interview.google_calendar_event_id == f"talvora{approved_action.id.hex}"
            assert len(cal_provider.events) == 1
            assert f"talvora{approved_action.id.hex}" in cal_provider.events

            # Verify active reservations
            res_stmt = (
                session.query(InterviewReservation).filter_by(interview_id=interview.id).all()
            )
            assert len(res_stmt) == 1
            assert res_stmt[0].status == InterviewReservationStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_approve_fails_on_payload_tampering(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.CREATE_INTERVIEW.value,
                resource_type="interview_request",
                resource_id=req.id,
                proposal_id=prop.id,
            )
            action_id = action.id

    cal_provider = MockGoogleCalendarAdapter()

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            with pytest.raises(ApprovalPayloadChangedError):
                await BookingSagaService.approve_external_action(
                    uow=uow,
                    actor=actor,
                    action_id=action_id,
                    expected_payload_hash="invalid_hash_value_12345",
                    expected_resource_version=1,
                    calendar_provider=cal_provider,
                )


@pytest.mark.asyncio
async def test_booking_saga_blocks_on_fresh_freebusy_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, int_mem = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.CREATE_INTERVIEW.value,
                resource_type="interview_request",
                resource_id=req.id,
                proposal_id=prop.id,
            )
            action_id = action.id
            payload_hash = action.payload_hash

    # Simulate calendar becoming BUSY right before booking
    cal_provider = MockGoogleCalendarAdapter()
    cal_provider.add_busy_interval(
        "interviewer@example.com",
        prop.slot_start_utc,
        prop.slot_end_utc,
    )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            with pytest.raises(SlotConflictError):
                await BookingSagaService.approve_external_action(
                    uow=uow,
                    actor=actor,
                    action_id=action_id,
                    expected_payload_hash=payload_hash,
                    expected_resource_version=1,
                    calendar_provider=cal_provider,
                )

            # Ensure HELD reservations were released
            res_list = session.query(InterviewReservation).filter_by(tenant_id=tenant_id).all()
            for r in res_list:
                assert r.status == InterviewReservationStatus.RELEASED.value


@pytest.mark.asyncio
async def test_booking_saga_timeout_enters_unknown_and_preserves_reservations(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.CREATE_INTERVIEW.value,
                resource_type="interview_request",
                resource_id=req.id,
                proposal_id=prop.id,
            )
            action_id = action.id
            payload_hash = action.payload_hash

    cal_provider = MockGoogleCalendarAdapter()
    cal_provider.simulate_create_timeout = True  # Ambiguous event creation timeout!

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            with pytest.raises(ProviderResultUnknownError):
                await BookingSagaService.approve_external_action(
                    uow=uow,
                    actor=actor,
                    action_id=action_id,
                    expected_payload_hash=payload_hash,
                    expected_resource_version=1,
                    calendar_provider=cal_provider,
                )

            # Status must be UNKNOWN, reservations held in UNKNOWN to prevent double-booking
            act = session.query(ExternalAction).filter_by(id=action_id).one()
            assert act.status == ExternalActionStatus.UNKNOWN.value

            reservations = session.query(InterviewReservation).filter_by(tenant_id=tenant_id).all()
            assert len(reservations) == 1
            assert reservations[0].status == InterviewReservationStatus.UNKNOWN.value


@pytest.mark.asyncio
async def test_resolve_unknown_action_force_release(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, int_mem = _seed_prerequisites(session, tenant_id, actor)
        now = datetime.now(UTC)

        action = ExternalAction(
            id=uuid4(),
            tenant_id=tenant_id,
            action_type=ExternalActionType.CREATE_INTERVIEW.value,
            status=ExternalActionStatus.UNKNOWN.value,
            resource_type="interview_request",
            resource_id=req.id,
            resource_version=1,
            payload={},
            payload_hash="dummy",
            verified_recipient_email="test@test.com",
            organizer_membership_id=actor.membership.membership_id,
            expires_at=now + timedelta(hours=1),
            created_by=actor.user_id,
            created_by_membership_id=actor.membership.membership_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(action)

        res = InterviewReservation(
            id=uuid4(),
            tenant_id=tenant_id,
            membership_id=int_mem.id,
            status=InterviewReservationStatus.UNKNOWN.value,
            slot_start_utc=now,
            slot_end_utc=now + timedelta(hours=1),
            interview_start_utc=now,
            interview_end_utc=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
        )
        session.add(res)
        session.commit()
        action_id = action.id

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            resolved = BookingSagaService.resolve_external_action(
                uow=uow,
                actor=actor,
                action_id=action_id,
                decision="FORCE_RELEASE",
                reason="Confirmed no calendar event exists on Google side.",
            )
            assert resolved.status == ExternalActionStatus.RESOLVED.value

            updated_res = session.query(InterviewReservation).filter_by(id=res.id).one()
            assert updated_res.status == InterviewReservationStatus.RELEASED.value


@pytest.mark.asyncio
async def test_gmail_send_action_accepted(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.SEND_AVAILABILITY_REQUEST.value,
                resource_type="interview_request",
                resource_id=req.id,
            )
            action_id = action.id
            payload_hash = action.payload_hash

    gmail_provider = MockGoogleGmailAdapter()
    cal_provider = MockGoogleCalendarAdapter()

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            approval, approved_action, _ = await BookingSagaService.approve_external_action(
                uow=uow,
                actor=actor,
                action_id=action_id,
                expected_payload_hash=payload_hash,
                expected_resource_version=1,
                calendar_provider=cal_provider,
                gmail_provider=gmail_provider,
            )
            assert approved_action.status == ExternalActionStatus.SUCCEEDED.value
            assert len(gmail_provider.sent_messages) == 1
            assert gmail_provider.sent_messages[0].to_recipients == ["alice@example.com"]


@pytest.mark.asyncio
async def test_gmail_send_action_unknown_never_blindly_retried(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.SEND_AVAILABILITY_REQUEST.value,
                resource_type="interview_request",
                resource_id=req.id,
            )
            action_id = action.id
            payload_hash = action.payload_hash

    gmail_provider = MockGoogleGmailAdapter()
    gmail_provider.simulate_timeout = True  # Triggers UNKNOWN
    cal_provider = MockGoogleCalendarAdapter()

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            approval, approved_action, _ = await BookingSagaService.approve_external_action(
                uow=uow,
                actor=actor,
                action_id=action_id,
                expected_payload_hash=payload_hash,
                expected_resource_version=1,
                calendar_provider=cal_provider,
                gmail_provider=gmail_provider,
            )
            # Must be UNKNOWN and NEVER blindly retried
            assert approved_action.status == ExternalActionStatus.UNKNOWN.value

            attempts = session.query(ProviderAttempt).filter_by(external_action_id=action_id).all()
            assert len(attempts) == 1
            assert attempts[0].status == ProviderAttemptStatus.UNKNOWN.value


@pytest.mark.asyncio
async def test_gmail_send_action_definitive_failure(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        cand, job, app, req, prop, _ = _seed_prerequisites(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = BookingSagaService.create_external_action(
                uow=uow,
                actor=actor,
                action_type=ExternalActionType.SEND_AVAILABILITY_REQUEST.value,
                resource_type="interview_request",
                resource_id=req.id,
            )
            action_id = action.id
            payload_hash = action.payload_hash

    gmail_provider = MockGoogleGmailAdapter()
    gmail_provider.simulate_rejected = True  # Triggers DEFINITIVE_FAILURE
    cal_provider = MockGoogleCalendarAdapter()

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            approval, approved_action, _ = await BookingSagaService.approve_external_action(
                uow=uow,
                actor=actor,
                action_id=action_id,
                expected_payload_hash=payload_hash,
                expected_resource_version=1,
                calendar_provider=cal_provider,
                gmail_provider=gmail_provider,
            )
            assert approved_action.status == ExternalActionStatus.FAILED.value

            attempts = session.query(ProviderAttempt).filter_by(external_action_id=action_id).all()
            assert len(attempts) == 1
            assert attempts[0].status == ProviderAttemptStatus.FAILED.value
