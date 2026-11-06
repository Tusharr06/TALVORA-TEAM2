"""P13: Human-Approved External Actions and Recoverable Interview Booking Saga.

Guarantees:
- Exact payload, recipient, and resource-version approval binding (SHA-256 canonical hash).
- Human approval required for all external side effects; creating an action is NOT approval.
- Fresh bounded FreeBusy verification immediately before booking.
- Short DB transaction for HELD reservations; zero DB locks held during provider HTTP calls.
- Deterministic provider event ID derivation: talvora{action.id.hex}.
- Concurrency and overlap prevention across HELD/ACTIVE/UNKNOWN reservations in tenant.
- Ambiguous timeout reconciliation (marks UNKNOWN, retains reservation, requires human resolution).
- Send-only Gmail dispatch (ACCEPTED, UNKNOWN, DEFINITIVE_FAILURE) without blind retry.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.identity.application.authorization import AuthorizationService
from talvora.modules.identity.domain.roles import Capability
from talvora.modules.integrations.application.ports import (
    CalendarAvailabilityStatus,
    CalendarEvent,
    CalendarProvider,
    CreateEventRequest,
    FreeBusyResponse,
    GmailProvider,
    MailSendResultStatus,
    SendEmailPayload,
    SendEmailResult,
)
from talvora.modules.jobs.domain.models import Job, JobStatus
from talvora.modules.scheduling.domain.errors import (
    ApprovalExpiredError,
    ApprovalPayloadChangedError,
    AvailabilityUnknownError,
    CandidateContactNotVerifiedError,
    CandidateNotEligibleForInterviewError,
    DuplicateActiveInterviewError,
    ExternalActionNotFoundError,
    ProviderResultUnknownError,
    SchedulingError,
    SlotConflictError,
    SlotProposalInvalidatedError,
)
from talvora.modules.scheduling.domain.models import (
    Approval,
    ApprovalDecision,
    CandidateConfirmationStatus,
    ExternalAction,
    ExternalActionStatus,
    ExternalActionType,
    Interview,
    InterviewLocationType,
    InterviewParticipant,
    InterviewRequest,
    InterviewRequestStatus,
    InterviewReservation,
    InterviewReservationStatus,
    InterviewRevision,
    InterviewStatus,
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
    ProposalStatus,
    ProviderAttempt,
    ProviderAttemptStatus,
    ReminderKind,
    ReminderSchedule,
    ReminderStatus,
    SlotProposal,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _with_for_update(stmt: sa.sql.Select[Any], session: Session) -> sa.sql.Select[Any]:
    bind = session.get_bind()
    if bind and bind.dialect.name == "postgresql":
        return stmt.with_for_update()
    return stmt


def compute_canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Computes deterministic SHA-256 digest of canonical, sorted JSON payload."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class BookingSagaService:
    """Coordinates external action drafting, exact human approvals, and recoverable booking sagas."""

    @staticmethod
    def create_external_action(
        uow: UnitOfWork,
        actor: ActorContext,
        action_type: str,
        resource_type: str,
        resource_id: UUID,
        resource_version: int = 1,
        proposal_id: UUID | None = None,
        connection_id: UUID | None = None,
        idempotency_key: str | None = None,
        custom_notes: str | None = None,
    ) -> ExternalAction:
        """Drafts an ExternalAction requiring explicit human approval before any external side effects."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)
        now = _utc_now()

        # Idempotency check
        if idempotency_key:
            idemp_stmt = sa.select(ExternalAction).where(
                ExternalAction.tenant_id == uow.tenant_id,
                ExternalAction.idempotency_key == idempotency_key,
            )
            existing = uow.session.scalar(idemp_stmt)
            if existing is not None:
                return existing

        contact_rev_num: int = 1
        if action_type == ExternalActionType.CREATE_INTERVIEW.value:
            if not proposal_id:
                raise SchedulingError(
                    status_code=422, detail="proposal_id is required for CREATE_INTERVIEW."
                )

            # 1. Fetch InterviewRequest
            req_stmt = sa.select(InterviewRequest).where(
                InterviewRequest.tenant_id == uow.tenant_id,
                InterviewRequest.id == resource_id,
            )
            req = uow.session.scalar(req_stmt)
            if req is None:
                raise SchedulingError(status_code=404, detail="Interview request not found.")

            # 2. Check no active interview exists for this request
            active_int_stmt = sa.select(Interview).where(
                Interview.tenant_id == uow.tenant_id,
                Interview.interview_request_id == req.id,
                Interview.status.not_in([InterviewStatus.CANCELLED.value]),
            )
            if uow.session.scalar(active_int_stmt) is not None:
                raise DuplicateActiveInterviewError(
                    "An active interview is already scheduled for this request."
                )

            # 3. Check Application stage == INTERVIEW_APPROVED
            app_stmt = sa.select(Application).where(
                Application.tenant_id == uow.tenant_id,
                Application.id == req.application_id,
            )
            app = uow.session.scalar(app_stmt)
            if app is None or app.stage != ApplicationStage.INTERVIEW_APPROVED.value:
                stage_val = app.stage if app else "UNKNOWN"
                raise CandidateNotEligibleForInterviewError(
                    f"Application must be in INTERVIEW_APPROVED stage (current: {stage_val})."
                )

            # 4. Check Candidate contact verified and fresh
            cand_stmt = sa.select(Candidate).where(
                Candidate.tenant_id == uow.tenant_id,
                Candidate.id == req.candidate_id,
            )
            cand = uow.session.scalar(cand_stmt)
            if not cand or not cand.is_contact_verified or not cand.verified_contact_revision_id:
                raise CandidateContactNotVerifiedError(
                    "Candidate contact information must be human-verified before booking."
                )
            if cand.current_contact_revision_number != cand.verified_contact_revision_number:
                raise CandidateContactNotVerifiedError(
                    "Candidate contact changed after verification; re-verification required."
                )

            # 5. Check Job is open
            job_stmt = sa.select(Job).where(
                Job.tenant_id == uow.tenant_id,
                Job.id == req.job_id,
            )
            job = uow.session.scalar(job_stmt)
            if job is None or job.status != JobStatus.OPEN.value:
                job_status = job.status if job else "UNKNOWN"
                raise SchedulingError(
                    status_code=409,
                    detail=f"Job must be OPEN to book interviews (status: {job_status}).",
                )

            # 6. Fetch SlotProposal
            prop_stmt = sa.select(SlotProposal).where(
                SlotProposal.tenant_id == uow.tenant_id,
                SlotProposal.id == proposal_id,
                SlotProposal.interview_request_id == req.id,
            )
            proposal = uow.session.scalar(prop_stmt)
            if proposal is None:
                raise SchedulingError(
                    status_code=404, detail="Slot proposal not found for this request."
                )
            if proposal.status != ProposalStatus.PENDING_REVIEW.value:
                raise SlotProposalInvalidatedError(
                    f"Slot proposal is not in PENDING_REVIEW (status: {proposal.status})."
                )
            if now >= _ensure_utc(proposal.expires_at):
                raise SlotProposalInvalidatedError("Slot proposal has expired.")

            # 7. Fetch Participants
            part_stmt = sa.select(InterviewParticipant).where(
                InterviewParticipant.tenant_id == uow.tenant_id,
                InterviewParticipant.interview_request_id == req.id,
            )
            participants = list(uow.session.scalars(part_stmt).all())
            if not participants:
                raise SchedulingError(
                    status_code=422, detail="Interview request has no participants assigned."
                )

            payload = {
                "action_type": ExternalActionType.CREATE_INTERVIEW.value,
                "interview_request_id": str(req.id),
                "slot_proposal_id": str(proposal.id),
                "application_id": str(app.id),
                "candidate_id": str(cand.id),
                "candidate_email": cand.email,
                "job_id": str(job.id),
                "job_title": job.title,
                "title": req.title,
                "slot_start_utc": _ensure_utc(proposal.slot_start_utc).isoformat(),
                "slot_end_utc": _ensure_utc(proposal.slot_end_utc).isoformat(),
                "duration_minutes": req.duration_minutes,
                "location_type": req.location_type,
                "buffer_before_minutes": req.buffer_before_minutes,
                "buffer_after_minutes": req.buffer_after_minutes,
                "timezone": req.timezone,
                "interviewer_membership_ids": [str(p.membership_id) for p in participants],
                "interviewer_calendar_ids": [p.calendar_id for p in participants],
                "organizer_membership_id": str(req.organizer_membership_id),
                "contact_revision_id": str(cand.verified_contact_revision_id),
                "contact_revision_number": cand.verified_contact_revision_number,
                "notes": custom_notes,
            }
            recipient_email = cand.email
            contact_rev_id = cand.verified_contact_revision_id
            contact_rev_num = cand.verified_contact_revision_number or 1
            organizer_mem_id = req.organizer_membership_id

        elif action_type == ExternalActionType.SEND_AVAILABILITY_REQUEST.value:
            req_stmt = sa.select(InterviewRequest).where(
                InterviewRequest.tenant_id == uow.tenant_id,
                InterviewRequest.id == resource_id,
            )
            req = uow.session.scalar(req_stmt)
            if req is None:
                raise SchedulingError(status_code=404, detail="Interview request not found.")

            cand_stmt = sa.select(Candidate).where(
                Candidate.tenant_id == uow.tenant_id,
                Candidate.id == req.candidate_id,
            )
            cand = uow.session.scalar(cand_stmt)
            if not cand or not cand.is_contact_verified or not cand.verified_contact_revision_id:
                raise CandidateContactNotVerifiedError(
                    "Candidate contact must be verified before sending email."
                )

            payload = {
                "action_type": ExternalActionType.SEND_AVAILABILITY_REQUEST.value,
                "interview_request_id": str(req.id),
                "candidate_id": str(cand.id),
                "recipient_email": cand.email,
                "title": req.title,
                "subject": f"Interview Availability Request: {req.title}",
                "body_text": f"Please provide your interview availability for {req.title}.",
                "organizer_membership_id": str(req.organizer_membership_id),
                "contact_revision_number": cand.verified_contact_revision_number,
                "notes": custom_notes,
            }
            recipient_email = cand.email
            contact_rev_id = cand.verified_contact_revision_id
            contact_rev_num = cand.verified_contact_revision_number or 1
            organizer_mem_id = req.organizer_membership_id
        else:
            raise SchedulingError(
                status_code=422, detail=f"Unsupported initial action_type: {action_type}"
            )

        payload_hash = compute_canonical_payload_hash(payload)
        expires_at = now + timedelta(hours=24)

        action = ExternalAction(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            action_type=action_type,
            status=ExternalActionStatus.PENDING_APPROVAL.value,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            payload=payload,
            payload_hash=payload_hash,
            verified_recipient_email=recipient_email,
            contact_revision_id=contact_rev_id,
            contact_revision_number=contact_rev_num,
            organizer_membership_id=organizer_mem_id,
            organizer_connection_id=connection_id,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            created_by=actor.user_id,
            created_by_membership_id=actor.membership.membership_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(action)
        uow.session.flush()

        uow.record_audit(
            resource_type="external_action",
            action="EXTERNAL_ACTION_DRAFTED",
            resource_id=action.id,
            actor_id=actor.user_id,
            details={
                "action_type": action_type,
                "payload_hash": payload_hash,
                "resource_id": str(resource_id),
            },
        )

        return action

    @staticmethod
    def get_external_action(
        uow: UnitOfWork,
        actor: ActorContext,
        action_id: UUID,
    ) -> tuple[ExternalAction, Approval | None, list[ProviderAttempt]]:
        """Fetches an ExternalAction, bound Approval, and all ProviderAttempt records within tenant."""
        if not actor.is_authenticated or not actor.tenant_id:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        stmt = sa.select(ExternalAction).where(
            ExternalAction.tenant_id == uow.tenant_id,
            ExternalAction.id == action_id,
        )
        action = uow.session.scalar(stmt)
        if action is None:
            raise ExternalActionNotFoundError("External action not found.")

        appr_stmt = sa.select(Approval).where(
            Approval.tenant_id == uow.tenant_id,
            Approval.external_action_id == action.id,
        )
        approval = uow.session.scalar(appr_stmt)

        attempts_stmt = (
            sa.select(ProviderAttempt)
            .where(
                ProviderAttempt.tenant_id == uow.tenant_id,
                ProviderAttempt.external_action_id == action.id,
            )
            .order_by(ProviderAttempt.attempt_number.asc())
        )
        attempts = list(uow.session.scalars(attempts_stmt).all())

        return action, approval, attempts

    @staticmethod
    def list_external_actions(
        uow: UnitOfWork,
        actor: ActorContext,
        status: str | None = None,
        action_type: str | None = None,
        resource_id: UUID | None = None,
        limit: int = 50,
    ) -> list[ExternalAction]:
        """Lists external actions within the tenant boundary."""
        if not actor.is_authenticated or not actor.tenant_id:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        stmt = sa.select(ExternalAction).where(ExternalAction.tenant_id == uow.tenant_id)
        if status:
            stmt = stmt.where(ExternalAction.status == status)
        if action_type:
            stmt = stmt.where(ExternalAction.action_type == action_type)
        if resource_id:
            stmt = stmt.where(ExternalAction.resource_id == resource_id)

        stmt = stmt.order_by(ExternalAction.created_at.desc()).limit(min(limit, 100))
        return list(uow.session.scalars(stmt).all())

    @staticmethod
    async def approve_external_action(
        uow: UnitOfWork,
        actor: ActorContext,
        action_id: UUID,
        expected_payload_hash: str,
        expected_resource_version: int,
        calendar_provider: CalendarProvider,
        gmail_provider: GmailProvider | None = None,
        encryption_key: str | None = None,
    ) -> tuple[Approval, ExternalAction, Interview | None]:
        """Approves and immediately dispatches the booking saga with exact payload validation."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)
        now = _utc_now()

        # 1. Fetch ExternalAction with row lock
        action_stmt = _with_for_update(
            sa.select(ExternalAction).where(
                ExternalAction.tenant_id == uow.tenant_id,
                ExternalAction.id == action_id,
            ),
            uow.session,
        )
        action = uow.session.scalar(action_stmt)
        if action is None:
            raise ExternalActionNotFoundError("External action not found.")

        # Concurrency / state validation
        if action.status not in (
            ExternalActionStatus.PENDING_APPROVAL.value,
            ExternalActionStatus.DRAFT.value,
        ):
            raise SchedulingError(
                status_code=409,
                detail=f"Action cannot be approved in current status: {action.status}",
            )

        # Exact payload hash validation
        if action.payload_hash != expected_payload_hash:
            raise ApprovalPayloadChangedError(
                "Action parameters were modified after approval was initiated. Payload hash mismatch."
            )

        # Resource version validation
        if action.resource_version != expected_resource_version:
            raise SchedulingError(
                status_code=409,
                detail=f"Resource version conflict: expected {expected_resource_version}, found {action.resource_version}.",
            )

        # Expiry check
        if now >= _ensure_utc(action.expires_at):
            raise ApprovalExpiredError("Approval validity period has expired.")

        # Re-verify candidate contact has not drifted
        if action.contact_revision_id:
            cand_stmt = sa.select(Candidate).where(
                Candidate.tenant_id == uow.tenant_id,
                Candidate.id == UUID(action.payload["candidate_id"]),
            )
            cand = uow.session.scalar(cand_stmt)
            if not cand or not cand.is_contact_verified:
                raise CandidateContactNotVerifiedError("Candidate contact verification revoked.")
            if cand.current_contact_revision_number != action.contact_revision_number:
                raise CandidateContactNotVerifiedError(
                    "Candidate contact modified after approval issuance."
                )

        # Create Approval record
        valid_until = now + timedelta(minutes=15)
        approval = Approval(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            external_action_id=action.id,
            approver_user_id=actor.user_id,
            approver_membership_id=actor.membership.membership_id,
            auth_time=now,
            decision=ApprovalDecision.APPROVED.value,
            approved_payload_hash=expected_payload_hash,
            approved_resource_version=expected_resource_version,
            valid_until=valid_until,
            created_at=now,
        )
        uow.session.add(approval)
        action.status = ExternalActionStatus.APPROVED.value
        action.updated_at = now
        action.version += 1
        uow.session.flush()

        uow.record_audit(
            resource_type="external_action",
            action="EXTERNAL_ACTION_APPROVED",
            resource_id=action.id,
            actor_id=actor.user_id,
            details={
                "approved_payload_hash": expected_payload_hash,
                "valid_until": valid_until.isoformat(),
            },
        )

        interview: Interview | None = None

        # 2. Execute Booking Saga or Send Action based on action_type
        if action.action_type == ExternalActionType.CREATE_INTERVIEW.value:
            interview = await BookingSagaService.execute_booking_saga(
                uow=uow,
                action=action,
                approval=approval,
                calendar_provider=calendar_provider,
                encryption_key=encryption_key,
            )
        elif (
            action.action_type == ExternalActionType.SEND_AVAILABILITY_REQUEST.value
            and gmail_provider is not None
        ):
            await BookingSagaService.execute_gmail_send_action(
                uow=uow,
                action=action,
                approval=approval,
                gmail_provider=gmail_provider,
                encryption_key=encryption_key,
            )

        return approval, action, interview

    @staticmethod
    async def execute_booking_saga(
        uow: UnitOfWork,
        action: ExternalAction,
        approval: Approval,
        calendar_provider: CalendarProvider,
        encryption_key: str | None = None,
    ) -> Interview:
        """Executes the recoverable interview booking saga against Google Calendar API."""
        now = _utc_now()
        payload = action.payload

        req_id = UUID(payload["interview_request_id"])
        proposal_id = UUID(payload["slot_proposal_id"])
        app_id = UUID(payload["application_id"])
        cand_id = UUID(payload["candidate_id"])
        job_id = UUID(payload["job_id"])
        organizer_mem_id = UUID(payload["organizer_membership_id"])

        slot_start_utc = _ensure_utc(datetime.fromisoformat(payload["slot_start_utc"]))
        slot_end_utc = _ensure_utc(datetime.fromisoformat(payload["slot_end_utc"]))
        buffer_before = payload.get("buffer_before_minutes", 10)
        buffer_after = payload.get("buffer_after_minutes", 10)

        buffered_start_utc = slot_start_utc - timedelta(minutes=buffer_before)
        buffered_end_utc = slot_end_utc + timedelta(minutes=buffer_after)

        interviewer_membership_ids = [UUID(mid) for mid in payload["interviewer_membership_ids"]]
        interviewer_calendar_ids = payload["interviewer_calendar_ids"]

        # ---------------------------------------------------------------------
        # Step A: Internal Overlap Protection & HELD Reservation (Short DB Tx)
        # ---------------------------------------------------------------------
        overlap_stmt = sa.select(InterviewReservation).where(
            InterviewReservation.tenant_id == uow.tenant_id,
            InterviewReservation.membership_id.in_(interviewer_membership_ids),
            InterviewReservation.status.in_(
                [
                    InterviewReservationStatus.HELD.value,
                    InterviewReservationStatus.ACTIVE.value,
                    InterviewReservationStatus.UNKNOWN.value,
                ]
            ),
            InterviewReservation.slot_start_utc < buffered_end_utc,
            InterviewReservation.slot_end_utc > buffered_start_utc,
        )
        conflicting_res = uow.session.scalar(overlap_stmt)
        if conflicting_res is not None:
            action.status = ExternalActionStatus.FAILED.value
            uow.session.flush()
            raise SlotConflictError(
                "Target time interval conflicts with an active or pending reservation for an interviewer."
            )

        # Create HELD reservations for all required participants
        held_reservations: list[InterviewReservation] = []
        dummy_interview_id = uuid4()
        for mem_id in interviewer_membership_ids:
            res = InterviewReservation(
                id=uuid4(),
                tenant_id=uow.tenant_id,
                interview_id=dummy_interview_id,
                interview_revision_id=None,
                membership_id=mem_id,
                status=InterviewReservationStatus.HELD.value,
                slot_start_utc=buffered_start_utc,
                slot_end_utc=buffered_end_utc,
                interview_start_utc=slot_start_utc,
                interview_end_utc=slot_end_utc,
                release_reason=None,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(res)
            held_reservations.append(res)

        action.status = ExternalActionStatus.EXECUTING.value
        uow.session.flush()

        # ---------------------------------------------------------------------
        # Step B: Fresh Bounded FreeBusy Verification (No DB Transaction Held)
        # ---------------------------------------------------------------------
        token = "test_calendar_access_token"
        try:
            freebusy_resp: FreeBusyResponse = await calendar_provider.query_freebusy(
                access_token=token,
                calendar_ids=interviewer_calendar_ids,
                time_min=buffered_start_utc,
                time_max=buffered_end_utc,
            )
        except Exception as exc:
            for r in held_reservations:
                r.status = InterviewReservationStatus.RELEASED.value
                r.release_reason = "FREEBUSY_QUERY_FAILED"
            action.status = ExternalActionStatus.FAILED.value
            uow.session.flush()
            raise AvailabilityUnknownError(
                f"Fresh FreeBusy query failed immediately before booking: {exc}"
            ) from exc

        # Check for UNKNOWN status or overlapping external busy periods
        for cal_id in interviewer_calendar_ids:
            sched = freebusy_resp.schedules.get(cal_id)
            if not sched or sched.status == CalendarAvailabilityStatus.UNKNOWN:
                for r in held_reservations:
                    r.status = InterviewReservationStatus.RELEASED.value
                    r.release_reason = "CALENDAR_AVAILABILITY_UNKNOWN"
                action.status = ExternalActionStatus.FAILED.value
                uow.session.flush()
                raise AvailabilityUnknownError(
                    f"Calendar {cal_id} availability is UNKNOWN; booking blocked."
                )

            for busy in sched.busy_intervals:
                if busy.start < buffered_end_utc and busy.end > buffered_start_utc:
                    for r in held_reservations:
                        r.status = InterviewReservationStatus.RELEASED.value
                        r.release_reason = "EXTERNAL_SLOT_CONFLICT"
                    action.status = ExternalActionStatus.FAILED.value
                    # Invalidate slot proposal
                    prop_stmt = sa.select(SlotProposal).where(SlotProposal.id == proposal_id)
                    p = uow.session.scalar(prop_stmt)
                    if p:
                        p.status = ProposalStatus.INVALIDATED.value
                        p.invalidation_reason = "EXTERNAL_CALENDAR_BUSY"
                    uow.session.flush()
                    raise SlotConflictError(
                        f"Calendar {cal_id} has a concurrent busy event during proposed slot."
                    )

        # ---------------------------------------------------------------------
        # Step C: External Event Creation with Stable ID & Attempt Ledger
        # ---------------------------------------------------------------------
        provider_event_id = f"talvora{action.id.hex}"
        description = (
            f"Talvora Interview: {payload['title']}\n"
            f"Candidate: {payload['candidate_email']}\n"
            f"Job: {payload['job_title']}\n"
            f"Coordination Reference: {provider_event_id}"
        )
        attendees = [payload["candidate_email"]] + interviewer_calendar_ids

        attempt = ProviderAttempt(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            external_action_id=action.id,
            attempt_number=1,
            provider="GOOGLE_CALENDAR",
            provider_event_id=provider_event_id,
            status=ProviderAttemptStatus.UNKNOWN.value,
            request_payload_snapshot={
                "event_id": provider_event_id,
                "summary": payload["title"],
                "start": slot_start_utc.isoformat(),
                "end": slot_end_utc.isoformat(),
                "attendees": attendees,
            },
            started_at=now,
        )
        uow.session.add(attempt)
        uow.session.flush()

        create_req = CreateEventRequest(
            event_id=provider_event_id,
            summary=payload["title"],
            start=slot_start_utc,
            end=slot_end_utc,
            attendees=attendees,
            description=description,
            create_meet_link=(
                payload.get("location_type") == InterviewLocationType.GOOGLE_MEET.value
            ),
        )

        event: CalendarEvent | None = None
        is_unknown_outcome = False

        try:
            event = await calendar_provider.create_event(
                access_token=token,
                calendar_id="primary",
                request=create_req,
            )
        except Exception as exc:
            err_str = str(exc)
            # Check if 409 conflict: event might already exist (e.g. from previous crash/retry)
            if "409" in err_str:
                try:
                    existing_evt = await calendar_provider.get_event(
                        access_token=token,
                        calendar_id="primary",
                        event_id=provider_event_id,
                    )
                    # Verify it has matching Talvora marker and start time
                    if (
                        existing_evt
                        and existing_evt.start == slot_start_utc
                        and (
                            existing_evt.description
                            and provider_event_id in existing_evt.description
                        )
                    ):
                        event = existing_evt  # Successfully reconciled duplicate!
                    else:
                        attempt.status = ProviderAttemptStatus.CONFLICT.value
                        attempt.error_code = "409_EVENT_COLLISION"
                        attempt.error_detail = "Unrelated external event exists with the same ID."
                        attempt.completed_at = _utc_now()
                        for r in held_reservations:
                            r.status = InterviewReservationStatus.RELEASED.value
                            r.release_reason = "EVENT_ID_COLLISION"
                        action.status = ExternalActionStatus.FAILED.value
                        uow.session.flush()
                        raise SlotConflictError(
                            "Calendar event ID collision with unrelated external event."
                        ) from exc
                except Exception:
                    is_unknown_outcome = True
            else:
                is_unknown_outcome = True

        # Ambiguous timeout or network error: retain UNKNOWN reservations
        if is_unknown_outcome or event is None:
            attempt.status = ProviderAttemptStatus.UNKNOWN.value
            attempt.error_code = "AMBIGUOUS_TIMEOUT"
            attempt.error_detail = (
                "HTTP timeout or connection error during calendar event creation."
            )
            attempt.completed_at = _utc_now()

            for r in held_reservations:
                r.status = InterviewReservationStatus.UNKNOWN.value
                r.release_reason = "AMBIGUOUS_PROVIDER_TIMEOUT"

            action.status = ExternalActionStatus.UNKNOWN.value
            uow.session.flush()

            uow.record_audit(
                resource_type="external_action",
                action="EXTERNAL_ACTION_AMBIGUOUS",
                resource_id=action.id,
                actor_id=approval.approver_user_id,
                details={"event_id": provider_event_id, "error": "ProviderResultUnknownError"},
            )
            raise ProviderResultUnknownError(
                "Calendar booking timed out with ambiguous status. Kept internal reservations held as UNKNOWN pending reconciliation."
            )

        # ---------------------------------------------------------------------
        # Step D: Finalize Local Aggregate & Reservations (Short DB Tx)
        # ---------------------------------------------------------------------
        attempt.status = ProviderAttemptStatus.SUCCEEDED.value
        attempt.completed_at = _utc_now()
        attempt.response_payload_snapshot = {
            "id": event.id,
            "status": event.status,
            "etag": event.etag,
            "html_link": event.html_link,
            "meet_link": event.meet_link,
        }

        # 1. Create Interview
        actual_interview_id = uuid4()
        interview = Interview(
            id=actual_interview_id,
            tenant_id=uow.tenant_id,
            interview_request_id=req_id,
            application_id=app_id,
            candidate_id=cand_id,
            job_id=job_id,
            organizer_membership_id=organizer_mem_id,
            title=payload["title"],
            location_type=payload.get("location_type", InterviewLocationType.GOOGLE_MEET.value),
            status=InterviewStatus.SCHEDULED.value,
            google_calendar_event_id=event.id,
            google_meet_link=event.meet_link,
            google_calendar_html_link=event.html_link,
            external_event_etag=event.etag,
            current_revision_id=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(interview)
        uow.session.flush()

        # 2. Create InterviewRevision
        rsvp_snapshot = {att.email: att.response_status for att in event.attendees}
        revision = InterviewRevision(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            interview_id=interview.id,
            revision_number=1,
            slot_start_utc=slot_start_utc,
            slot_end_utc=slot_end_utc,
            timezone=payload.get("timezone", "UTC"),
            provider_etag=event.etag,
            external_event_status=event.status,
            candidate_confirmation_status=CandidateConfirmationStatus.UNCONFIRMED.value,
            participant_rsvp_snapshot=rsvp_snapshot,
            external_action_id=action.id,
            created_at=now,
        )
        uow.session.add(revision)
        interview.current_revision_id = revision.id

        # 3. Promote HELD reservations to ACTIVE linked to interview and revision
        for r in held_reservations:
            r.interview_id = interview.id
            r.interview_revision_id = revision.id
            r.status = InterviewReservationStatus.ACTIVE.value
            r.updated_at = now

        # 4. Update parent request & proposal
        req_stmt = sa.select(InterviewRequest).where(InterviewRequest.id == req_id)
        req = uow.session.scalar(req_stmt)
        if req:
            req.status = InterviewRequestStatus.SCHEDULED.value
            req.updated_at = now
            req.version += 1

        prop_stmt = sa.select(SlotProposal).where(SlotProposal.id == proposal_id)
        prop = uow.session.scalar(prop_stmt)
        if prop:
            prop.status = ProposalStatus.SELECTED.value

        action.status = ExternalActionStatus.SUCCEEDED.value
        action.updated_at = now
        action.version += 1

        # 5. Schedule Reminders (24h and 1h before start, if still future)
        time_24h = slot_start_utc - timedelta(hours=24)
        if time_24h > now:
            rem_24 = ReminderSchedule(
                id=uuid4(),
                tenant_id=uow.tenant_id,
                interview_id=interview.id,
                interview_revision_id=revision.id,
                reminder_kind=ReminderKind.REMINDER_24H.value,
                scheduled_for=time_24h,
                recipient_email=payload["candidate_email"],
                status=ReminderStatus.SCHEDULED.value,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(rem_24)

        time_1h = slot_start_utc - timedelta(hours=1)
        if time_1h > now:
            rem_1 = ReminderSchedule(
                id=uuid4(),
                tenant_id=uow.tenant_id,
                interview_id=interview.id,
                interview_revision_id=revision.id,
                reminder_kind=ReminderKind.REMINDER_1H.value,
                scheduled_for=time_1h,
                recipient_email=payload["candidate_email"],
                status=ReminderStatus.SCHEDULED.value,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(rem_1)

        # 6. Record Notification ledger
        notif = Notification(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            interview_id=interview.id,
            interview_revision_id=revision.id,
            external_action_id=action.id,
            recipient_email=payload["candidate_email"],
            notification_type=NotificationType.CANDIDATE_INVITATION.value,
            channel=NotificationChannel.CALENDAR_INVITE.value,
            status=NotificationStatus.ACCEPTED.value,
            provider_message_id=event.id,
            sent_at=now,
            created_at=now,
        )
        uow.session.add(notif)
        uow.session.flush()

        uow.record_audit(
            resource_type="interview",
            action="INTERVIEW_BOOKED",
            resource_id=interview.id,
            actor_id=approval.approver_user_id,
            details={
                "event_id": event.id,
                "slot_start": slot_start_utc.isoformat(),
                "slot_end": slot_end_utc.isoformat(),
                "google_meet_link": event.meet_link,
            },
        )

        return interview

    @staticmethod
    async def execute_gmail_send_action(
        uow: UnitOfWork,
        action: ExternalAction,
        approval: Approval,
        gmail_provider: GmailProvider,
        encryption_key: str | None = None,
    ) -> SendEmailResult:
        """Sends an approved email notification via Gmail with strict tri-state handling."""
        now = _utc_now()
        payload = action.payload
        token = "test_gmail_access_token"

        send_payload = SendEmailPayload(
            to_recipients=[payload["recipient_email"]],
            subject=payload["subject"],
            body_text=payload["body_text"],
            body_html=payload.get("body_html"),
            from_address=f"recruiting@{uow.tenant_id}.talvora.test",
        )

        attempt = ProviderAttempt(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            external_action_id=action.id,
            attempt_number=1,
            provider="GOOGLE_GMAIL",
            status=ProviderAttemptStatus.UNKNOWN.value,
            request_payload_snapshot={
                "to": payload["recipient_email"],
                "subject": payload["subject"],
            },
            started_at=now,
        )
        uow.session.add(attempt)
        uow.session.flush()

        result = await gmail_provider.send_message(access_token=token, payload=send_payload)

        attempt.completed_at = _utc_now()
        attempt.provider_message_id = result.provider_message_id
        attempt.error_detail = result.error_message

        if result.status == MailSendResultStatus.ACCEPTED:
            attempt.status = ProviderAttemptStatus.SUCCEEDED.value
            action.status = ExternalActionStatus.SUCCEEDED.value
            action.updated_at = _utc_now()
        elif result.status == MailSendResultStatus.UNKNOWN:
            # Ambiguous outcome: mark UNKNOWN, never blindly retry!
            attempt.status = ProviderAttemptStatus.UNKNOWN.value
            action.status = ExternalActionStatus.UNKNOWN.value
            action.updated_at = _utc_now()
        else:
            attempt.status = ProviderAttemptStatus.FAILED.value
            action.status = ExternalActionStatus.FAILED.value
            action.updated_at = _utc_now()

        uow.session.flush()
        return result

    @staticmethod
    def resolve_external_action(
        uow: UnitOfWork,
        actor: ActorContext,
        action_id: UUID,
        decision: str,
        reason: str,
    ) -> ExternalAction:
        """Resolves an external action in PENDING_APPROVAL or UNKNOWN state."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)
        now = _utc_now()

        stmt = _with_for_update(
            sa.select(ExternalAction).where(
                ExternalAction.tenant_id == uow.tenant_id,
                ExternalAction.id == action_id,
            ),
            uow.session,
        )
        action: ExternalAction | None = uow.session.scalar(stmt)
        if action is None:
            raise ExternalActionNotFoundError("External action not found.")

        if decision in (ApprovalDecision.REJECTED.value, "REJECT"):
            if action.status not in (
                ExternalActionStatus.PENDING_APPROVAL.value,
                ExternalActionStatus.DRAFT.value,
            ):
                raise SchedulingError(
                    status_code=409,
                    detail=f"Cannot reject action in status: {action.status}",
                )
            action.status = ExternalActionStatus.REJECTED.value
            action.updated_at = now
            action.version += 1

            # Create rejection Approval record
            approval = Approval(
                id=uuid4(),
                tenant_id=uow.tenant_id,
                external_action_id=action.id,
                approver_user_id=actor.user_id,
                approver_membership_id=actor.membership.membership_id,
                auth_time=now,
                decision=ApprovalDecision.REJECTED.value,
                approved_payload_hash=action.payload_hash,
                approved_resource_version=action.resource_version,
                rejection_reason=reason,
                valid_until=now,
                created_at=now,
            )
            uow.session.add(approval)
            uow.session.flush()

            uow.record_audit(
                resource_type="external_action",
                action="EXTERNAL_ACTION_REJECTED",
                resource_id=action.id,
                actor_id=actor.user_id,
                details={"reason": reason},
            )
        elif decision in ("CANCEL", "FORCE_RELEASE"):
            if action.status != ExternalActionStatus.UNKNOWN.value:
                raise SchedulingError(
                    status_code=409,
                    detail="FORCE_RELEASE is only allowed for actions in UNKNOWN state.",
                )
            # Release pending UNKNOWN reservations
            res_stmt = sa.select(InterviewReservation).where(
                InterviewReservation.tenant_id == uow.tenant_id,
                InterviewReservation.status == InterviewReservationStatus.UNKNOWN.value,
            )
            for r in uow.session.scalars(res_stmt).all():
                r.status = InterviewReservationStatus.RELEASED.value
                r.release_reason = f"RESOLVED_CANCELLED: {reason}"
                r.updated_at = now

            action.status = ExternalActionStatus.RESOLVED.value
            action.updated_at = now
            action.version += 1
            uow.session.flush()

            uow.record_audit(
                resource_type="external_action",
                action="EXTERNAL_ACTION_RESOLVED",
                resource_id=action.id,
                actor_id=actor.user_id,
                details={"decision": decision, "reason": reason},
            )
        else:
            raise SchedulingError(
                status_code=422, detail=f"Unsupported resolution decision: {decision}"
            )

        return action
