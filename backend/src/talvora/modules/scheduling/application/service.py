"""Application Service for Interview Scheduling, Candidate Availability, and Slot Proposals."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.identity.domain.models import User
from talvora.modules.integrations.application.ports import (
    CalendarAvailabilityStatus,
    CalendarProvider,
    CalendarSchedule,
    FreeBusyResponse,
    TimeInterval,
)
from talvora.modules.organizations.domain.models import Membership
from talvora.modules.scheduling.application.ports import (
    SolverInput,
    SolverResult,
)
from talvora.modules.scheduling.application.slot_solver import DeterministicSlotSolver
from talvora.modules.scheduling.domain.errors import (
    CandidateContactNotVerifiedError,
    CandidateLinkExpiredError,
    CandidateLinkInvalidError,
    CandidateNotEligibleForInterviewError,
    SchedulingError,
)
from talvora.modules.scheduling.domain.models import (
    AvailabilitySnapshot,
    AvailabilitySubmission,
    CandidateAvailabilityLink,
    InternalReservation,
    InterviewLocationType,
    InterviewParticipant,
    InterviewRequest,
    InterviewRequestStatus,
    ParticipantRole,
    ProposalStatus,
    ReservationStatus,
    SlotProposal,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.security import generate_token_256, hash_token
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


class SchedulingService:
    """Coordinates interview requests, public candidate link sessions, FreeBusy checks, and slot generation."""

    @staticmethod
    def create_interview_request(
        uow: UnitOfWork,
        actor: ActorContext,
        application_id: UUID,
        title: str,
        target_date_start: datetime,
        target_date_end: datetime,
        duration_minutes: int = 45,
        location_type: str = InterviewLocationType.GOOGLE_MEET.value,
        timezone: str = "UTC",
        working_hours_start: int = 9,
        working_hours_end: int = 17,
        working_days: list[int] | None = None,
        buffer_before_minutes: int = 10,
        buffer_after_minutes: int = 10,
        minimum_lead_time_hours: int = 24,
        interviewer_membership_ids: list[UUID] | None = None,
    ) -> InterviewRequest:
        """Creates an interview request enforcing P05 eligibility and contact verification."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        now = _utc_now()

        # 1. Fetch Application and verify stage == INTERVIEW_APPROVED
        app_stmt = sa.select(Application).where(
            Application.tenant_id == uow.tenant_id,
            Application.id == application_id,
        )
        app = uow.session.scalar(app_stmt)
        if app is None:
            raise SchedulingError(status_code=404, detail="Application not found.")

        if app.stage != ApplicationStage.INTERVIEW_APPROVED.value:
            raise CandidateNotEligibleForInterviewError(
                f"Application must be in INTERVIEW_APPROVED stage (current: {app.stage})."
            )

        # 2. Fetch Candidate and verify verified contact details
        cand_stmt = sa.select(Candidate).where(
            Candidate.tenant_id == uow.tenant_id,
            Candidate.id == app.candidate_id,
        )
        cand = uow.session.scalar(cand_stmt)
        if cand is None:
            raise SchedulingError(status_code=404, detail="Candidate not found.")

        if not cand.is_contact_verified or not cand.verified_contact_revision_id:
            raise CandidateContactNotVerifiedError(
                "Candidate contact information must be human-verified before creating an interview request."
            )

        # Verify contact has not drifted since verification
        if cand.current_contact_revision_number != cand.verified_contact_revision_number:
            raise CandidateContactNotVerifiedError(
                "Candidate contact information was modified after verification; re-verification required."
            )

        # 3. Create InterviewRequest
        req = InterviewRequest(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            application_id=app.id,
            candidate_id=cand.id,
            job_id=app.job_id,
            title=title,
            duration_minutes=duration_minutes,
            status=InterviewRequestStatus.DRAFT.value,
            location_type=location_type,
            organizer_membership_id=actor.membership.membership_id,
            target_date_start=_ensure_utc(target_date_start),
            target_date_end=_ensure_utc(target_date_end),
            working_hours_start=working_hours_start,
            working_hours_end=working_hours_end,
            working_days=working_days if working_days is not None else [0, 1, 2, 3, 4],
            buffer_before_minutes=buffer_before_minutes,
            buffer_after_minutes=buffer_after_minutes,
            minimum_lead_time_hours=minimum_lead_time_hours,
            timezone=timezone,
            created_by=actor.user_id,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(req)
        uow.session.flush()

        # 4. Attach Participants
        participant_memberships = interviewer_membership_ids or [actor.membership.membership_id]
        for mem_id in participant_memberships:
            # Look up membership and user email for calendar_id
            mem_stmt = (
                sa.select(Membership, User)
                .join(User, Membership.user_id == User.id)
                .where(
                    Membership.tenant_id == uow.tenant_id,
                    Membership.id == mem_id,
                )
            )
            result = uow.session.execute(mem_stmt).first()
            if result:
                _, user = result
                calendar_id = user.email
            else:
                calendar_id = "primary"

            participant = InterviewParticipant(
                id=uuid4(),
                tenant_id=uow.tenant_id,
                interview_request_id=req.id,
                membership_id=mem_id,
                role=ParticipantRole.INTERVIEWER.value,
                is_required=True,
                calendar_id=calendar_id,
                created_at=now,
            )
            uow.session.add(participant)

        uow.session.flush()

        uow.record_audit(
            resource_type="interview_request",
            action="INTERVIEW_REQUEST_CREATED",
            resource_id=req.id,
            actor_id=actor.user_id,
            details={"application_id": str(app.id), "candidate_id": str(cand.id), "title": title},
        )

        return req

    @staticmethod
    def get_interview_request(
        uow: UnitOfWork,
        actor: ActorContext,
        request_id: UUID,
    ) -> InterviewRequest:
        """Fetches an interview request within the tenant boundary."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        stmt = sa.select(InterviewRequest).where(
            InterviewRequest.tenant_id == uow.tenant_id,
            InterviewRequest.id == request_id,
        )
        req = uow.session.scalar(stmt)
        if req is None:
            raise SchedulingError(status_code=404, detail="Interview request not found.")
        return req

    @staticmethod
    def issue_candidate_link(
        uow: UnitOfWork,
        actor: ActorContext,
        request_id: UUID,
        validity_hours: int = 72,
    ) -> tuple[str, CandidateAvailabilityLink]:
        """Generates a cryptographically secure, single-use candidate availability link."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        req = SchedulingService.get_interview_request(uow=uow, actor=actor, request_id=request_id)

        # Check candidate contact status hasn't drifted
        cand_stmt = sa.select(Candidate).where(
            Candidate.tenant_id == uow.tenant_id,
            Candidate.id == req.candidate_id,
        )
        cand = uow.session.scalar(cand_stmt)
        if not cand or not cand.is_contact_verified or not cand.verified_contact_revision_id:
            raise CandidateContactNotVerifiedError(
                "Candidate contact must be verified before generating availability link."
            )

        if cand.current_contact_revision_number != cand.verified_contact_revision_number:
            raise CandidateContactNotVerifiedError(
                "Candidate contact changed since verification; re-verification required."
            )

        raw_token = generate_token_256()
        token_digest = hash_token(raw_token)
        now = _utc_now()
        expires_at = now + timedelta(hours=validity_hours)

        link = CandidateAvailabilityLink(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            interview_request_id=req.id,
            candidate_id=cand.id,
            token_hash=token_digest,
            candidate_contact_revision_id=cand.verified_contact_revision_id,
            candidate_contact_revision_number=cand.verified_contact_revision_number,
            expires_at=expires_at,
            consumed_at=None,
            is_revoked=False,
            created_at=now,
        )
        uow.session.add(link)

        # Update request status to AWAITING_AVAILABILITY if currently DRAFT
        if req.status == InterviewRequestStatus.DRAFT.value:
            req.status = InterviewRequestStatus.AWAITING_AVAILABILITY.value
            req.updated_at = now
            req.version += 1

        uow.session.flush()

        uow.record_audit(
            resource_type="candidate_availability_link",
            action="CANDIDATE_LINK_ISSUED",
            resource_id=link.id,
            actor_id=actor.user_id,
            details={"interview_request_id": str(req.id), "expires_at": expires_at.isoformat()},
        )

        return raw_token, link

    @staticmethod
    def validate_candidate_link(
        uow: UnitOfWork,
        raw_token: str,
    ) -> tuple[CandidateAvailabilityLink, InterviewRequest, Candidate]:
        """Validates public candidate link token and verifies contact revision freshness."""
        token_digest = hash_token(raw_token)
        now = _utc_now()

        stmt = sa.select(CandidateAvailabilityLink).where(
            CandidateAvailabilityLink.token_hash == token_digest
        )
        link = uow.session.scalar(stmt)
        if link is None or link.is_revoked:
            raise CandidateLinkInvalidError("Availability link is invalid or has been revoked.")

        if now >= _ensure_utc(link.expires_at):
            raise CandidateLinkExpiredError("Availability link has expired.")

        req_stmt = sa.select(InterviewRequest).where(
            InterviewRequest.tenant_id == link.tenant_id,
            InterviewRequest.id == link.interview_request_id,
        )
        req = uow.session.scalar(req_stmt)
        if req is None or req.status in (
            InterviewRequestStatus.CANCELLED.value,
            InterviewRequestStatus.EXPIRED.value,
        ):
            raise CandidateLinkInvalidError("Associated interview request is no longer active.")

        cand_stmt = sa.select(Candidate).where(
            Candidate.tenant_id == link.tenant_id,
            Candidate.id == link.candidate_id,
        )
        cand = uow.session.scalar(cand_stmt)
        if cand is None:
            raise CandidateLinkInvalidError("Candidate record not found.")

        # Invalidation check: if candidate contact changed after link generation
        if cand.current_contact_revision_number != link.candidate_contact_revision_number:
            raise CandidateContactNotVerifiedError(
                "Candidate contact information was modified after link issuance."
            )

        return link, req, cand

    @staticmethod
    def submit_candidate_availability(
        uow: UnitOfWork,
        raw_token: str,
        candidate_timezone: str,
        available_intervals: list[dict[str, str]],
    ) -> AvailabilitySubmission:
        """Records candidate-submitted availability windows via public link."""
        link, req, _ = SchedulingService.validate_candidate_link(uow=uow, raw_token=raw_token)
        uow.tenant_id = link.tenant_id

        now = _utc_now()
        parsed_intervals: list[dict[str, str]] = []

        for item in available_intervals:
            start_str = item.get("start")
            end_str = item.get("end")
            if not start_str or not end_str:
                raise SchedulingError(
                    status_code=422, detail="Interval start and end are required."
                )

            dt_start = _ensure_utc(datetime.fromisoformat(start_str.replace("Z", "+00:00")))
            dt_end = _ensure_utc(datetime.fromisoformat(end_str.replace("Z", "+00:00")))

            if dt_start >= dt_end:
                raise SchedulingError(
                    status_code=422,
                    detail=f"Start time ({dt_start}) must precede end time ({dt_end}).",
                )

            parsed_intervals.append({"start": dt_start.isoformat(), "end": dt_end.isoformat()})

        if not parsed_intervals:
            raise SchedulingError(
                status_code=422, detail="At least one availability interval is required."
            )

        submission = AvailabilitySubmission(
            id=uuid4(),
            tenant_id=link.tenant_id,
            interview_request_id=req.id,
            link_id=link.id,
            candidate_timezone=candidate_timezone,
            available_intervals=parsed_intervals,
            submitted_at=now,
        )
        uow.session.add(submission)

        link.consumed_at = now
        req.updated_at = now
        req.version += 1
        uow.session.flush()

        uow.record_audit(
            resource_type="availability_submission",
            action="CANDIDATE_AVAILABILITY_SUBMITTED",
            resource_id=submission.id,
            details={"interview_request_id": str(req.id), "intervals_count": len(parsed_intervals)},
        )

        return submission

    @staticmethod
    async def generate_slot_proposals(
        uow: UnitOfWork,
        actor: ActorContext,
        calendar_provider: CalendarProvider,
        request_id: UUID,
        step_minutes: int = 15,
        max_proposals: int = 5,
        access_token_override: str | None = None,
    ) -> list[SlotProposal]:
        """Queries interviewer FreeBusy, executes DeterministicSlotSolver, and records proposed slots."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        req = SchedulingService.get_interview_request(uow=uow, actor=actor, request_id=request_id)

        # 1. Fetch latest candidate availability submission
        sub_stmt = (
            sa.select(AvailabilitySubmission)
            .where(
                AvailabilitySubmission.tenant_id == uow.tenant_id,
                AvailabilitySubmission.interview_request_id == req.id,
            )
            .order_by(AvailabilitySubmission.submitted_at.desc())
        )
        submission = uow.session.scalar(sub_stmt)
        if submission is None:
            raise SchedulingError(
                status_code=422,
                detail="No candidate availability has been submitted for this interview request.",
            )

        # 2. Fetch Participants
        part_stmt = sa.select(InterviewParticipant).where(
            InterviewParticipant.tenant_id == uow.tenant_id,
            InterviewParticipant.interview_request_id == req.id,
        )
        participants = list(uow.session.scalars(part_stmt).all())
        if not participants:
            raise SchedulingError(
                status_code=422, detail="Interview request has no assigned participants."
            )

        # 3. Query Google Calendar FreeBusy via CalendarProvider
        calendar_ids = [p.calendar_id for p in participants]
        token = access_token_override or "system_internal_token"

        freebusy_resp: FreeBusyResponse = await calendar_provider.query_freebusy(
            access_token=token,
            calendar_ids=calendar_ids,
            time_min=req.target_date_start,
            time_max=req.target_date_end,
        )

        # Map schedules to membership IDs
        interviewer_schedules: dict[UUID, CalendarSchedule] = {}
        for p in participants:
            sched = freebusy_resp.schedules.get(
                p.calendar_id,
                CalendarSchedule(
                    calendar_id=p.calendar_id, status=CalendarAvailabilityStatus.UNKNOWN
                ),
            )
            interviewer_schedules[p.membership_id] = sched

        # 4. Fetch internal active reservations
        res_stmt = sa.select(InternalReservation).where(
            InternalReservation.tenant_id == uow.tenant_id,
            InternalReservation.membership_id.in_([p.membership_id for p in participants]),
            InternalReservation.status == ReservationStatus.ACTIVE.value,
            InternalReservation.slot_end_utc > req.target_date_start,
            InternalReservation.slot_start_utc < req.target_date_end,
        )
        internal_reservations = [
            TimeInterval(start=_ensure_utc(r.slot_start_utc), end=_ensure_utc(r.slot_end_utc))
            for r in uow.session.scalars(res_stmt).all()
        ]

        # 5. Convert candidate intervals to TimeInterval objects
        cand_intervals = [
            TimeInterval(
                start=_ensure_utc(datetime.fromisoformat(item["start"])),
                end=_ensure_utc(datetime.fromisoformat(item["end"])),
            )
            for item in submission.available_intervals
        ]

        # 6. Execute Deterministic Slot Solver
        solver_input = SolverInput(
            request_id=req.id,
            duration_minutes=req.duration_minutes,
            buffer_before_minutes=req.buffer_before_minutes,
            buffer_after_minutes=req.buffer_after_minutes,
            minimum_lead_time_hours=req.minimum_lead_time_hours,
            working_hours_start=req.working_hours_start,
            working_hours_end=req.working_hours_end,
            working_days=req.working_days,
            timezone=req.timezone,
            target_date_start=req.target_date_start,
            target_date_end=req.target_date_end,
            candidate_intervals=cand_intervals,
            interviewer_schedules=interviewer_schedules,
            internal_reservations=internal_reservations,
            evaluation_time=_utc_now(),
            step_minutes=step_minutes,
            max_proposals=max_proposals,
        )
        result: SolverResult = DeterministicSlotSolver.solve(solver_input)

        # 7. Record Availability Snapshot
        now = _utc_now()
        freebusy_dict = {
            p.calendar_id: {
                "status": sched.status.value,
                "busy": [
                    {"start": i.start.isoformat(), "end": i.end.isoformat()}
                    for i in sched.busy_intervals
                ],
            }
            for p, sched in [(p, interviewer_schedules[p.membership_id]) for p in participants]
        }
        cand_dict = {
            "intervals": submission.available_intervals,
            "timezone": submission.candidate_timezone,
        }

        snapshot_raw = json.dumps(
            {"freebusy": freebusy_dict, "candidate": cand_dict}, sort_keys=True
        )
        snapshot_hash = hashlib.sha256(snapshot_raw.encode("utf-8")).hexdigest()

        snapshot = AvailabilitySnapshot(
            id=uuid4(),
            tenant_id=uow.tenant_id,
            interview_request_id=req.id,
            interviewer_freebusy_snapshot=freebusy_dict,
            candidate_availability_snapshot=cand_dict,
            snapshot_hash=snapshot_hash,
            created_at=now,
        )
        uow.session.add(snapshot)
        uow.session.flush()

        # Invalidate any previously pending proposals for this request
        old_props_stmt = sa.select(SlotProposal).where(
            SlotProposal.tenant_id == uow.tenant_id,
            SlotProposal.interview_request_id == req.id,
            SlotProposal.status == ProposalStatus.PENDING_REVIEW.value,
        )
        for old_p in uow.session.scalars(old_props_stmt).all():
            old_p.status = ProposalStatus.INVALIDATED.value
            old_p.invalidation_reason = "SUPERSEDED_BY_NEW_SEARCH"

        # 8. Create SlotProposal entities
        proposals: list[SlotProposal] = []
        proposal_expiry = now + timedelta(hours=48)

        for slot in result.proposed_slots:
            prop = SlotProposal(
                id=uuid4(),
                tenant_id=uow.tenant_id,
                interview_request_id=req.id,
                snapshot_id=snapshot.id,
                slot_start_utc=slot.start_utc,
                slot_end_utc=slot.end_utc,
                interviewer_membership_ids=[str(mid) for mid in slot.interviewer_membership_ids],
                status=ProposalStatus.PENDING_REVIEW.value,
                invalidation_reason=None,
                expires_at=proposal_expiry,
                created_at=now,
            )
            uow.session.add(prop)
            proposals.append(prop)

        if proposals:
            req.status = InterviewRequestStatus.SLOTS_PROPOSED.value
            req.updated_at = now
            req.version += 1

        uow.session.flush()

        uow.record_audit(
            resource_type="interview_request",
            action="SLOT_PROPOSALS_GENERATED",
            resource_id=req.id,
            actor_id=actor.user_id,
            details={"proposals_count": len(proposals), "snapshot_id": str(snapshot.id)},
        )

        return proposals

    @staticmethod
    def list_slot_proposals(
        uow: UnitOfWork,
        actor: ActorContext,
        request_id: UUID,
    ) -> list[SlotProposal]:
        """Lists all slot proposals for an interview request."""
        if not actor.is_authenticated or not actor.tenant_id or not actor.membership:
            raise SchedulingError(status_code=401, detail="Active authenticated session required.")

        stmt = (
            sa.select(SlotProposal)
            .where(
                SlotProposal.tenant_id == uow.tenant_id,
                SlotProposal.interview_request_id == request_id,
            )
            .order_by(SlotProposal.slot_start_utc.asc())
        )
        return list(uow.session.scalars(stmt).all())
