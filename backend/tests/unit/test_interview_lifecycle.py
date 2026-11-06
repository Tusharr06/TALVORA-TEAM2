"""Unit Tests for P14: Interview Lifecycle, Candidate RSVP, Rescheduling, Cancellation, and Reminders."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.identity.domain.roles import Role, get_capabilities_for_role
from talvora.modules.integrations.application.ports import (
    CalendarEvent,
)
from talvora.modules.integrations.infrastructure.google.calendar import MockGoogleCalendarAdapter
from talvora.modules.integrations.infrastructure.google.gmail import MockGoogleGmailAdapter
from talvora.modules.jobs.domain.models import Job, JobStatus
from talvora.modules.organizations.domain.models import Membership, Organization
from talvora.modules.scheduling.application.booking_saga import (
    BookingSagaService,
)
from talvora.modules.scheduling.application.lifecycle_service import InterviewLifecycleService
from talvora.modules.scheduling.domain.errors import (
    CandidateLinkInvalidError,
)
from talvora.modules.scheduling.domain.models import (
    AvailabilitySnapshot,
    CandidateAvailabilityLink,
    CandidateConfirmationStatus,
    ExternalActionStatus,
    ExternalActionType,
    Interview,
    InterviewParticipant,
    InterviewRequest,
    InterviewRequestStatus,
    InterviewReservation,
    InterviewReservationStatus,
    InterviewRevision,
    InterviewStatus,
    Notification,
    ParticipantRole,
    ProposalStatus,
    ReminderKind,
    ReminderSchedule,
    ReminderStatus,
    SlotProposal,
)
from talvora.shared.application.actor import ActorContext, MembershipContext, Principal
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.security import generate_token_256, hash_token
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


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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


def _seed_booked_interview(
    session: Session,
    tenant_id: UUID,
    actor: ActorContext,
) -> tuple[Interview, InterviewRevision, Candidate, str, SlotProposal, Membership]:
    now = datetime.now(UTC)
    assert actor.membership is not None

    # 1. Org & Memberships
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

    interviewer_mem = Membership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=uuid4(),
        role=Role.INTERVIEWER.value,
        status="ACTIVE",
        created_at=now,
    )
    session.add(interviewer_mem)

    # 2. Candidate
    contact_rev_id = uuid4()
    candidate = Candidate(
        id=uuid4(),
        tenant_id=tenant_id,
        display_name="Alice Candidate",
        email="alice@candidate.test",
        created_by=actor.user_id,
        current_contact_revision_id=contact_rev_id,
        current_contact_revision_number=1,
        verified_contact_revision_id=contact_rev_id,
        verified_contact_revision_number=1,
        created_at=now,
    )
    session.add(candidate)

    # 3. Job & Application
    job = Job(
        id=uuid4(),
        tenant_id=tenant_id,
        title="Senior Site Reliability Engineer",
        description="Core infrastructure engineering.",
        creator_id=actor.user_id,
        status=JobStatus.OPEN.value,
        version=1,
        created_at=now,
    )
    session.add(job)

    app = Application(
        id=uuid4(),
        tenant_id=tenant_id,
        job_id=job.id,
        candidate_id=candidate.id,
        stage=ApplicationStage.INTERVIEW_APPROVED.value,
        created_by=actor.user_id,
        created_at=now,
    )
    session.add(app)

    # 4. Interview Request & Participant
    target_start = now + timedelta(days=2)
    target_end = now + timedelta(days=5)
    int_req = InterviewRequest(
        id=uuid4(),
        tenant_id=tenant_id,
        application_id=app.id,
        candidate_id=candidate.id,
        job_id=job.id,
        title="Technical Architectural Screen",
        duration_minutes=60,
        target_date_start=target_start,
        target_date_end=target_end,
        timezone="UTC",
        organizer_membership_id=actor.membership.membership_id,
        status=InterviewRequestStatus.SCHEDULED.value,
        created_by=actor.user_id,
        created_at=now,
    )
    session.add(int_req)

    part = InterviewParticipant(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        membership_id=interviewer_mem.id,
        role=ParticipantRole.INTERVIEWER.value,
        is_required=True,
        calendar_id="interviewer@calendar.test",
        created_at=now,
    )
    session.add(part)

    # 5. Snap & Proposals
    snap = AvailabilitySnapshot(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        interviewer_freebusy_snapshot={},
        candidate_availability_snapshot={},
        snapshot_hash="dummy_hash",
        created_at=now,
    )
    session.add(snap)

    slot_1_start = target_start + timedelta(hours=4)
    slot_1_end = slot_1_start + timedelta(minutes=60)
    prop_1 = SlotProposal(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        snapshot_id=snap.id,
        slot_start_utc=slot_1_start,
        slot_end_utc=slot_1_end,
        interviewer_membership_ids=[str(interviewer_mem.id)],
        status=ProposalStatus.SELECTED.value,
        expires_at=now + timedelta(hours=24),
        created_at=now,
    )
    session.add(prop_1)

    # Alternate proposal for rescheduling tests
    slot_2_start = target_start + timedelta(hours=8)
    slot_2_end = slot_2_start + timedelta(minutes=60)
    prop_2 = SlotProposal(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        snapshot_id=snap.id,
        slot_start_utc=slot_2_start,
        slot_end_utc=slot_2_end,
        interviewer_membership_ids=[str(interviewer_mem.id)],
        status=ProposalStatus.PENDING_REVIEW.value,
        expires_at=now + timedelta(hours=24),
        created_at=now,
    )
    session.add(prop_2)

    # 6. Interview & Revision 1
    interview_id = uuid4()
    rev_1_id = uuid4()
    interview = Interview(
        id=interview_id,
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        application_id=app.id,
        candidate_id=candidate.id,
        job_id=job.id,
        organizer_membership_id=actor.membership.membership_id,
        title=int_req.title,
        location_type="GOOGLE_MEET",
        status=InterviewStatus.SCHEDULED.value,
        google_calendar_event_id=f"talvora{interview_id.hex}",
        google_meet_link="https://meet.google.com/test-meet",
        external_event_etag="initial_etag_123",
        current_revision_id=rev_1_id,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(interview)

    rev_1 = InterviewRevision(
        id=rev_1_id,
        tenant_id=tenant_id,
        interview_id=interview.id,
        revision_number=1,
        slot_start_utc=slot_1_start,
        slot_end_utc=slot_1_end,
        timezone="UTC",
        provider_etag="initial_etag_123",
        external_event_status="confirmed",
        candidate_confirmation_status=CandidateConfirmationStatus.UNCONFIRMED.value,
        participant_rsvp_snapshot={"interviewer@calendar.test": "accepted"},
        created_at=now,
    )
    session.add(rev_1)

    # 7. Active Reservation
    res_1 = InterviewReservation(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_id=interview.id,
        interview_revision_id=rev_1.id,
        membership_id=interviewer_mem.id,
        status=InterviewReservationStatus.ACTIVE.value,
        slot_start_utc=slot_1_start - timedelta(minutes=10),
        slot_end_utc=slot_1_end + timedelta(minutes=10),
        interview_start_utc=slot_1_start,
        interview_end_utc=slot_1_end,
        created_at=now,
        updated_at=now,
    )
    session.add(res_1)

    # 8. Candidate Availability Link (Token for Public RSVP)
    raw_token = generate_token_256()
    token_digest = hash_token(raw_token)
    cand_link = CandidateAvailabilityLink(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_request_id=int_req.id,
        candidate_id=candidate.id,
        token_hash=token_digest,
        candidate_contact_revision_id=contact_rev_id,
        candidate_contact_revision_number=1,
        expires_at=now + timedelta(days=7),
        is_revoked=False,
        created_at=now,
    )
    session.add(cand_link)

    # 9. Reminders
    rem_24 = ReminderSchedule(
        id=uuid4(),
        tenant_id=tenant_id,
        interview_id=interview.id,
        interview_revision_id=rev_1.id,
        reminder_kind=ReminderKind.REMINDER_24H.value,
        scheduled_for=slot_1_start - timedelta(hours=24),
        recipient_email=candidate.email,
        status=ReminderStatus.SCHEDULED.value,
        created_at=now,
        updated_at=now,
    )
    session.add(rem_24)

    session.commit()
    return interview, rev_1, candidate, raw_token, prop_2, interviewer_mem


# ============================================================================
# Candidate Public Capability Tests
# ============================================================================


def test_candidate_confirm_attendance_success(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, candidate, raw_token, _, _ = _seed_booked_interview(
            session, tenant_id, actor
        )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=None) as uow:
            updated_rev = InterviewLifecycleService.public_confirm_attendance(
                uow=uow,
                token=raw_token,
                interview_id=interview.id,
                decision="CONFIRMED",
            )
            assert (
                updated_rev.candidate_confirmation_status
                == CandidateConfirmationStatus.CONFIRMED.value
            )
            assert updated_rev.candidate_confirmed_at is not None

        # Re-query
        re_int = session.query(Interview).filter_by(id=interview.id).one()
        assert re_int.status == InterviewStatus.SCHEDULED.value  # Active state preserved


def test_candidate_decline_attendance_moves_interview_to_change_pending(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, candidate, raw_token, _, _ = _seed_booked_interview(
            session, tenant_id, actor
        )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=None) as uow:
            updated_rev = InterviewLifecycleService.public_confirm_attendance(
                uow=uow,
                token=raw_token,
                interview_id=interview.id,
                decision="DECLINED",
            )
            assert (
                updated_rev.candidate_confirmation_status
                == CandidateConfirmationStatus.DECLINED.value
            )

        re_int = session.query(Interview).filter_by(id=interview.id).one()
        assert re_int.status == InterviewStatus.CHANGE_PENDING.value


def test_candidate_request_change_records_reason(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, candidate, raw_token, _, _ = _seed_booked_interview(
            session, tenant_id, actor
        )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=None) as uow:
            updated_rev = InterviewLifecycleService.public_request_change(
                uow=uow,
                token=raw_token,
                interview_id=interview.id,
                reason="Doctor appointment collision, need afternoon slot.",
            )
            assert (
                updated_rev.candidate_confirmation_status
                == CandidateConfirmationStatus.CHANGE_REQUESTED.value
            )
            assert (
                updated_rev.candidate_change_reason
                == "Doctor appointment collision, need afternoon slot."
            )

        re_int = session.query(Interview).filter_by(id=interview.id).one()
        assert re_int.status == InterviewStatus.CHANGE_PENDING.value


def test_candidate_public_action_fails_on_invalid_token(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, _, _, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=None) as uow:
            with pytest.raises(CandidateLinkInvalidError):
                InterviewLifecycleService.public_confirm_attendance(
                    uow=uow,
                    token="wrong_invalid_token",
                    interview_id=interview.id,
                    decision="CONFIRMED",
                )


# ============================================================================
# Recruiter Rescheduling Workflow Tests
# ============================================================================


@pytest.mark.asyncio
async def test_recruiter_reschedule_workflow_success(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, candidate, raw_token, prop_2, interviewer_mem = _seed_booked_interview(
            session, tenant_id, actor
        )

    # 1. Draft reschedule external action
    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = InterviewLifecycleService.request_reschedule(
                uow=uow,
                actor=actor,
                interview_id=interview.id,
                new_slot_proposal_id=prop_2.id,
                reason="Interviewer conflict with executive meeting.",
            )
            assert action.action_type == ExternalActionType.RESCHEDULE_INTERVIEW.value
            assert action.status == ExternalActionStatus.PENDING_APPROVAL.value
            action_id = action.id
            payload_hash = action.payload_hash

    # 2. Execute approved reschedule saga
    cal_provider = MockGoogleCalendarAdapter()
    # Add existing event to mock calendar so update succeeds
    cal_provider.events[f"talvora{interview.id.hex}"] = CalendarEvent(
        id=f"talvora{interview.id.hex}",
        summary=interview.title,
        start=rev_1.slot_start_utc,
        end=rev_1.slot_end_utc,
        attendees=[],
        etag="initial_etag_123",
    )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            (
                approval,
                approved_action,
                updated_int,
            ) = await BookingSagaService.approve_external_action(
                uow=uow,
                actor=actor,
                action_id=action_id,
                expected_payload_hash=payload_hash,
                expected_resource_version=1,
                calendar_provider=cal_provider,
            )
            assert approved_action.status == ExternalActionStatus.SUCCEEDED.value
            assert updated_int is not None
            assert updated_int.status == InterviewStatus.SCHEDULED.value

            # Check new revision 2 created
            revs = (
                session.query(InterviewRevision)
                .filter_by(interview_id=interview.id)
                .order_by(InterviewRevision.revision_number.desc())
                .all()
            )
            assert len(revs) == 2
            assert revs[0].revision_number == 2
            assert _ensure_utc(revs[0].slot_start_utc) == _ensure_utc(prop_2.slot_start_utc)

            # Check old reservation released and new reservation active
            reservations = (
                session.query(InterviewReservation).filter_by(interview_id=interview.id).all()
            )
            active_res = [
                r for r in reservations if r.status == InterviewReservationStatus.ACTIVE.value
            ]
            released_res = [
                r for r in reservations if r.status == InterviewReservationStatus.RELEASED.value
            ]
            assert len(active_res) == 1
            assert len(released_res) == 1
            assert released_res[0].release_reason == "RESCHEDULED"

            # Check old reminder cancelled and new reminder scheduled
            reminders = session.query(ReminderSchedule).filter_by(interview_id=interview.id).all()
            cancelled_rem = [r for r in reminders if r.status == ReminderStatus.CANCELLED.value]
            scheduled_rem = [r for r in reminders if r.status == ReminderStatus.SCHEDULED.value]
            assert len(cancelled_rem) >= 1
            assert len(scheduled_rem) >= 1


# ============================================================================
# Recruiter Cancellation Workflow Tests
# ============================================================================


@pytest.mark.asyncio
async def test_recruiter_cancellation_workflow_success(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, candidate, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            action = InterviewLifecycleService.request_cancellation(
                uow=uow,
                actor=actor,
                interview_id=interview.id,
                reason="Position put on freeze.",
            )
            assert action.action_type == ExternalActionType.CANCEL_INTERVIEW.value
            action_id = action.id
            payload_hash = action.payload_hash

    cal_provider = MockGoogleCalendarAdapter()
    cal_provider.events[f"talvora{interview.id.hex}"] = CalendarEvent(
        id=f"talvora{interview.id.hex}",
        summary=interview.title,
        start=rev_1.slot_start_utc,
        end=rev_1.slot_end_utc,
        attendees=[],
        etag="initial_etag_123",
    )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            (
                approval,
                approved_action,
                cancelled_int,
            ) = await BookingSagaService.approve_external_action(
                uow=uow,
                actor=actor,
                action_id=action_id,
                expected_payload_hash=payload_hash,
                expected_resource_version=1,
                calendar_provider=cal_provider,
            )
            assert approved_action.status == ExternalActionStatus.SUCCEEDED.value
            assert cancelled_int is not None
            assert cancelled_int.status == InterviewStatus.CANCELLED.value
            assert cancelled_int.completion_reason is not None
            assert "Position put on freeze." in cancelled_int.completion_reason

            # Check event removed from calendar
            assert f"talvora{interview.id.hex}" not in cal_provider.events

            # Check reservations released
            res_list = (
                session.query(InterviewReservation).filter_by(interview_id=interview.id).all()
            )
            for r in res_list:
                assert r.status == InterviewReservationStatus.RELEASED.value

            # Check reminders cancelled
            rem_list = session.query(ReminderSchedule).filter_by(interview_id=interview.id).all()
            for rem in rem_list:
                assert rem.status == ReminderStatus.CANCELLED.value


# ============================================================================
# Human Completion Tests (Strictly Non-Automated)
# ============================================================================


def test_human_completion_completed_success(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, _, _, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            completed_int = InterviewLifecycleService.record_completion(
                uow=uow,
                actor=actor,
                interview_id=interview.id,
                outcome=InterviewStatus.COMPLETED.value,
                reason="Candidate answered all technical questions satisfactorily.",
            )
            assert completed_int.status == InterviewStatus.COMPLETED.value
            assert (
                completed_int.completion_reason
                == "Candidate answered all technical questions satisfactorily."
            )

            # Reservations released
            reservations = (
                session.query(InterviewReservation).filter_by(interview_id=interview.id).all()
            )
            for r in reservations:
                assert r.status == InterviewReservationStatus.RELEASED.value


def test_human_completion_no_show_success(session_factory: sessionmaker[Session]) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, _, _, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            no_show_int = InterviewLifecycleService.record_completion(
                uow=uow,
                actor=actor,
                interview_id=interview.id,
                outcome=InterviewStatus.NO_SHOW.value,
                reason="Candidate did not connect to Google Meet after 15 minute wait.",
            )
            assert no_show_int.status == InterviewStatus.NO_SHOW.value
            assert no_show_int.completion_reason is not None
            assert "15 minute wait" in no_show_int.completion_reason


# ============================================================================
# External Calendar Reconciliation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_reconciliation_detects_external_deletion(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, _, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    cal_provider = MockGoogleCalendarAdapter()
    # Event cancelled by organizer externally
    cal_provider.events[f"talvora{interview.id.hex}"] = CalendarEvent(
        id=f"talvora{interview.id.hex}",
        summary=interview.title,
        start=rev_1.slot_start_utc,
        end=rev_1.slot_end_utc,
        attendees=[],
        status="cancelled",
    )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            reconciled = await InterviewLifecycleService.reconcile_calendar_event(
                uow=uow,
                interview_id=interview.id,
                calendar_provider=cal_provider,
            )
            assert reconciled.status == InterviewStatus.RECONCILIATION_REQUIRED.value
            assert reconciled.reconciliation_reason is not None
            assert "deleted by the organizer" in reconciled.reconciliation_reason


@pytest.mark.asyncio
async def test_reconciliation_detects_schedule_drift(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, _, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    cal_provider = MockGoogleCalendarAdapter()
    # Event moved 1 hour later by organizer outside Talvora
    cal_provider.events[f"talvora{interview.id.hex}"] = CalendarEvent(
        id=f"talvora{interview.id.hex}",
        summary=interview.title,
        start=rev_1.slot_start_utc + timedelta(hours=1),
        end=rev_1.slot_end_utc + timedelta(hours=1),
        attendees=[],
        status="confirmed",
    )

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            reconciled = await InterviewLifecycleService.reconcile_calendar_event(
                uow=uow,
                interview_id=interview.id,
                calendar_provider=cal_provider,
            )
            assert reconciled.status == InterviewStatus.RECONCILIATION_REQUIRED.value
            assert reconciled.reconciliation_reason is not None
            assert "External schedule drift detected" in reconciled.reconciliation_reason


# ============================================================================
# Durable Reminders Coordinator Tests
# ============================================================================


@pytest.mark.asyncio
async def test_durable_reminders_worker_dispatch_and_deduplication(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, candidate, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    gmail_provider = MockGoogleGmailAdapter()
    # 10 minutes past due time (slot_start - 24h + 10m), well within 2h window!
    eval_time = _ensure_utc(rev_1.slot_start_utc) - timedelta(hours=23, minutes=50)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            dispatched = await InterviewLifecycleService.process_due_reminders(
                uow=uow,
                gmail_provider=gmail_provider,
                evaluation_time=eval_time,
            )
            assert dispatched == 1
            assert len(gmail_provider.sent_messages) == 1
            assert gmail_provider.sent_messages[0].to_recipients == [candidate.email]

            reminders = session.query(ReminderSchedule).filter_by(interview_id=interview.id).all()
            assert reminders[0].status == ReminderStatus.SENT.value

            notifs = session.query(Notification).filter_by(interview_id=interview.id).all()
            assert len(notifs) == 1
            assert notifs[0].status == "ACCEPTED"

    # Second sweep at same time does not re-send (deduplication)
    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            dispatched_second = await InterviewLifecycleService.process_due_reminders(
                uow=uow,
                gmail_provider=gmail_provider,
                evaluation_time=eval_time,
            )
            assert dispatched_second == 0
            assert len(gmail_provider.sent_messages) == 1  # No additional send!


@pytest.mark.asyncio
async def test_durable_reminders_worker_skips_late_reminders(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_id = uuid4()
    actor = _create_actor(tenant_id)

    with session_factory() as session:
        interview, rev_1, candidate, _, _, _ = _seed_booked_interview(session, tenant_id, actor)

    gmail_provider = MockGoogleGmailAdapter()
    # Evaluated AFTER interview has already started!
    eval_time = rev_1.slot_start_utc + timedelta(minutes=30)

    with session_factory() as session:
        with UnitOfWork(session=session, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            dispatched = await InterviewLifecycleService.process_due_reminders(
                uow=uow,
                gmail_provider=gmail_provider,
                evaluation_time=eval_time,
            )
            assert dispatched == 0
            assert len(gmail_provider.sent_messages) == 0  # Late reminder skipped, NOT sent!

            rem = session.query(ReminderSchedule).filter_by(interview_id=interview.id).one()
            assert rem.status == ReminderStatus.SKIPPED.value
            assert rem.skip_reason == "LATE_WINDOW_EXPIRED"
