"""FastAPI REST Endpoints for Interview Requests, Public Candidate Links, and Slot Solver."""

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from talvora.config import Settings, get_settings
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.identity.application.authorization import AuthorizationService
from talvora.modules.identity.domain.roles import Capability
from talvora.modules.integrations.application.ports import CalendarProvider, GmailProvider
from talvora.modules.integrations.infrastructure.google.calendar import (
    GoogleCalendarAdapter,
    MockGoogleCalendarAdapter,
)
from talvora.modules.integrations.infrastructure.google.gmail import (
    GoogleGmailAdapter,
    MockGoogleGmailAdapter,
)
from talvora.modules.organizations.domain.models import Organization
from talvora.modules.scheduling.api.dto import (
    ApproveExternalActionRequest,
    CompleteInterviewPayload,
    CreateExternalActionRequest,
    CreateInterviewRequestPayload,
    ExternalActionResponse,
    GenerateProposalsRequest,
    InterviewDetailResponse,
    InterviewRequestResponse,
    InterviewReservationResponse,
    InterviewResponse,
    InterviewRevisionResponse,
    IssueAvailabilityLinkResponse,
    NotificationResponse,
    PublicChangeRequest,
    PublicConfirmInterviewRequest,
    PublicInterviewDetailsResponse,
    ReminderScheduleResponse,
    RequestCancellationPayload,
    RequestReschedulePayload,
    ResolveExternalActionRequest,
    SlotProposalResponse,
    SubmitAvailabilityRequest,
)
from talvora.modules.scheduling.application.booking_saga import BookingSagaService
from talvora.modules.scheduling.application.lifecycle_service import InterviewLifecycleService
from talvora.modules.scheduling.application.service import SchedulingService
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(tags=["Scheduling"])


def get_calendar_provider(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CalendarProvider:
    """Resolves active CalendarProvider (test double or production Google Calendar adapter)."""
    custom_provider: CalendarProvider | None = getattr(request.app.state, "calendar_provider", None)
    if custom_provider is not None:
        return custom_provider

    if settings.allow_mock_google_adapter and settings.environment in ("local", "test"):
        mock_adapter = MockGoogleCalendarAdapter()
        request.app.state.calendar_provider = mock_adapter
        return mock_adapter

    return GoogleCalendarAdapter()


def get_gmail_provider(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> GmailProvider:
    """Resolves active GmailProvider (test double or production Google Gmail adapter)."""
    custom_provider: GmailProvider | None = getattr(request.app.state, "gmail_provider", None)
    if custom_provider is not None:
        return custom_provider

    if settings.allow_mock_google_adapter and settings.environment in ("local", "test"):
        mock_adapter = MockGoogleGmailAdapter()
        request.app.state.gmail_provider = mock_adapter
        return mock_adapter

    return GoogleGmailAdapter()


# ============================================================================
# Recruiter / Staff Interview Request Endpoints
# ============================================================================


@router.post(
    "/api/v1/interview-requests",
    response_model=InterviewRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an Interview Request for an Approved Candidate",
    operation_id="create_interview_request",
)
def create_interview_request(
    payload: CreateInterviewRequestPayload,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> InterviewRequestResponse:
    """Creates a new interview request ensuring candidate contact is verified and stage is INTERVIEW_APPROVED."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        req = SchedulingService.create_interview_request(
            uow=uow,
            actor=actor,
            application_id=payload.application_id,
            title=payload.title,
            target_date_start=payload.target_date_start,
            target_date_end=payload.target_date_end,
            duration_minutes=payload.duration_minutes,
            location_type=payload.location_type,
            timezone=payload.timezone,
            working_hours_start=payload.working_hours_start,
            working_hours_end=payload.working_hours_end,
            working_days=payload.working_days,
            buffer_before_minutes=payload.buffer_before_minutes,
            buffer_after_minutes=payload.buffer_after_minutes,
            minimum_lead_time_hours=payload.minimum_lead_time_hours,
            interviewer_membership_ids=payload.interviewer_membership_ids,
        )
        return InterviewRequestResponse.model_validate(req)


@router.get(
    "/api/v1/interview-requests/{id}",
    response_model=InterviewRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Interview Request Details",
    operation_id="get_interview_request",
)
def get_interview_request(
    request_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> InterviewRequestResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        req = SchedulingService.get_interview_request(uow=uow, actor=actor, request_id=request_id)
        return InterviewRequestResponse.model_validate(req)


@router.post(
    "/api/v1/interview-requests/{id}/availability-links",
    response_model=IssueAvailabilityLinkResponse,
    status_code=status.HTTP_200_OK,
    summary="Issue Candidate Availability Link",
    operation_id="issue_candidate_link",
)
def issue_candidate_link(
    request: Request,
    request_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> IssueAvailabilityLinkResponse:
    """Generates an opaque single-use link for candidate availability collection."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        raw_token, link = SchedulingService.issue_candidate_link(
            uow=uow,
            actor=actor,
            request_id=request_id,
        )
        base_url = str(request.base_url).rstrip("/")
        avail_url = f"{base_url}/public/scheduling/{raw_token}"
        return IssueAvailabilityLinkResponse(
            availability_url=avail_url,
            link_id=link.id,
            expires_at=link.expires_at,
        )


@router.post(
    "/api/v1/interview-requests/{id}/proposals/generate",
    response_model=list[SlotProposalResponse],
    status_code=status.HTTP_200_OK,
    summary="Generate Deterministic Interview Proposals",
    operation_id="generate_slot_proposals",
)
async def generate_slot_proposals(
    payload: GenerateProposalsRequest,
    request_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> list[SlotProposalResponse]:
    """Evaluates candidate availability + FreeBusy schedules and generates conflict-free slot proposals."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        proposals = await SchedulingService.generate_slot_proposals(
            uow=uow,
            actor=actor,
            calendar_provider=calendar_provider,
            request_id=request_id,
            step_minutes=payload.step_minutes,
            max_proposals=payload.max_proposals,
        )
        return [SlotProposalResponse.model_validate(p) for p in proposals]


@router.get(
    "/api/v1/interview-requests/{id}/proposals",
    response_model=list[SlotProposalResponse],
    status_code=status.HTTP_200_OK,
    summary="List Generated Slot Proposals",
    operation_id="list_slot_proposals",
)
def list_slot_proposals(
    request_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[SlotProposalResponse]:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        proposals = SchedulingService.list_slot_proposals(
            uow=uow,
            actor=actor,
            request_id=request_id,
        )
        return [SlotProposalResponse.model_validate(p) for p in proposals]


# ============================================================================
# Public Candidate-Facing Endpoints (Scanner-Safe & Zero PII Leakage)
# ============================================================================


@router.get(
    "/api/v1/public/availability/{token}",
    response_model=PublicInterviewDetailsResponse,
    status_code=status.HTTP_200_OK,
    summary="Candidate View: Get Scheduling Window and Details",
    operation_id="public_get_interview_details",
)
def public_get_interview_details(
    token: str = Path(...),
    db: Session = Depends(get_db_session),
) -> PublicInterviewDetailsResponse:
    """Safe unauthenticated endpoint allowing candidates to inspect their scheduling window."""
    with UnitOfWork(session=db, tenant_id=None) as uow:
        link, req, _ = SchedulingService.validate_candidate_link(uow=uow, raw_token=token)

        org_stmt = uow.session.query(Organization).filter_by(id=link.tenant_id)
        org = uow.session.scalar(org_stmt)
        company_name = org.name if org else "Talvora Partner"

        return PublicInterviewDetailsResponse(
            title=req.title,
            company_name=company_name,
            duration_minutes=req.duration_minutes,
            target_date_start=req.target_date_start,
            target_date_end=req.target_date_end,
            timezone=req.timezone,
        )


@router.post(
    "/api/v1/public/availability/{token}",
    status_code=status.HTTP_200_OK,
    summary="Candidate View: Submit Availability Intervals",
    operation_id="public_submit_availability",
)
def public_submit_availability(
    payload: SubmitAvailabilityRequest,
    token: str = Path(...),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Candidate submits their available time windows."""
    with UnitOfWork(session=db, tenant_id=None) as uow:
        submission = SchedulingService.submit_candidate_availability(
            uow=uow,
            raw_token=token,
            candidate_timezone=payload.candidate_timezone,
            available_intervals=[i.model_dump() for i in payload.available_intervals],
        )
        return {"status": "submitted", "submission_id": str(submission.id)}


# ============================================================================
# P13 — Human-Approved External Actions & Booking Saga Endpoints
# ============================================================================


@router.post(
    "/api/v1/external-actions",
    response_model=ExternalActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft Human-Approved External Action",
    operation_id="create_external_action",
)
def create_external_action(
    payload: CreateExternalActionRequest,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ExternalActionResponse:
    """Drafts an external action (CREATE_INTERVIEW or SEND_AVAILABILITY_REQUEST) requiring human approval."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        action = BookingSagaService.create_external_action(
            uow=uow,
            actor=actor,
            action_type=payload.action_type,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            resource_version=payload.resource_version,
            proposal_id=payload.proposal_id,
            connection_id=payload.connection_id,
            custom_notes=payload.custom_notes,
        )
        return ExternalActionResponse.model_validate(action)


@router.get(
    "/api/v1/external-actions",
    response_model=list[ExternalActionResponse],
    status_code=status.HTTP_200_OK,
    summary="List External Actions",
    operation_id="list_external_actions",
)
def list_external_actions(
    status_filter: str | None = Query(default=None, alias="status"),
    action_type: str | None = Query(default=None),
    resource_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[ExternalActionResponse]:
    """Lists drafted and executed external actions within the tenant."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        actions = BookingSagaService.list_external_actions(
            uow=uow,
            actor=actor,
            status=status_filter,
            action_type=action_type,
            resource_id=resource_id,
            limit=limit,
        )
        return [ExternalActionResponse.model_validate(a) for a in actions]


@router.get(
    "/api/v1/external-actions/{id}",
    response_model=ExternalActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get External Action Details",
    operation_id="get_external_action",
)
def get_external_action(
    action_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ExternalActionResponse:
    """Fetches details of an external action within tenant boundary."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        action, _, _ = BookingSagaService.get_external_action(
            uow=uow,
            actor=actor,
            action_id=action_id,
        )
        return ExternalActionResponse.model_validate(action)


@router.post(
    "/api/v1/external-actions/{id}/approve",
    response_model=ExternalActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve and Execute External Action",
    operation_id="approve_external_action",
)
async def approve_external_action(
    payload: ApproveExternalActionRequest,
    action_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    gmail_provider: GmailProvider = Depends(get_gmail_provider),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ExternalActionResponse:
    """Binds exact reviewed payload hash and version, then safely dispatches the external saga."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        _, action, _ = await BookingSagaService.approve_external_action(
            uow=uow,
            actor=actor,
            action_id=action_id,
            expected_payload_hash=payload.expected_payload_hash,
            expected_resource_version=payload.expected_resource_version,
            calendar_provider=calendar_provider,
            gmail_provider=gmail_provider,
        )
        return ExternalActionResponse.model_validate(action)


@router.post(
    "/api/v1/external-actions/{id}/resolve",
    response_model=ExternalActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Resolve or Reject External Action",
    operation_id="resolve_external_action",
)
def resolve_external_action(
    payload: ResolveExternalActionRequest,
    action_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ExternalActionResponse:
    """Manually rejects or force-releases an ambiguous or pending external action."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        action = BookingSagaService.resolve_external_action(
            uow=uow,
            actor=actor,
            action_id=action_id,
            decision=payload.decision,
            reason=payload.reason,
        )
        return ExternalActionResponse.model_validate(action)


# ============================================================================
# P14 — Interview Lifecycle, RSVP, Notifications, and Reminders Endpoints
# ============================================================================


@router.get(
    "/api/v1/interviews",
    response_model=list[InterviewResponse],
    status_code=status.HTTP_200_OK,
    summary="List Interviews",
    operation_id="list_interviews",
)
def list_interviews(
    application_id: UUID | None = Query(default=None),
    candidate_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[InterviewResponse]:
    """Lists interviews for tenant with optional filtering by application, candidate, or status."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        interviews = InterviewLifecycleService.list_interviews(
            uow=uow,
            actor=actor,
            application_id=application_id,
            candidate_id=candidate_id,
            status=status_filter,
            limit=limit,
        )
        return [InterviewResponse.model_validate(i) for i in interviews]


@router.get(
    "/api/v1/interviews/{id}",
    response_model=InterviewDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Interview Details and Ledgers",
    operation_id="get_interview_detail",
)
def get_interview_detail(
    interview_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> InterviewDetailResponse:
    """Fetches full interview details including current revision, reservations, and reminders."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        (
            interview,
            current_revision,
            all_revisions,
            reservations,
            notifications,
            reminders,
        ) = InterviewLifecycleService.get_interview(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
        )
        return InterviewDetailResponse(
            interview=InterviewResponse.model_validate(interview),
            current_revision=InterviewRevisionResponse.model_validate(current_revision),
            all_revisions=[InterviewRevisionResponse.model_validate(r) for r in all_revisions],
            reservations=[InterviewReservationResponse.model_validate(res) for res in reservations],
            notifications=[NotificationResponse.model_validate(n) for n in notifications],
            reminders=[ReminderScheduleResponse.model_validate(rem) for rem in reminders],
        )


@router.post(
    "/api/v1/interviews/{id}/reschedule-requests",
    response_model=ExternalActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft Human-Approved Reschedule Request",
    operation_id="request_interview_reschedule",
)
def request_interview_reschedule(
    payload: RequestReschedulePayload,
    interview_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ExternalActionResponse:
    """Drafts an approved external action to reschedule an interview to a new proposal slot."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        action = InterviewLifecycleService.request_reschedule(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            new_slot_proposal_id=payload.new_slot_proposal_id,
            reason=payload.reason,
        )
        return ExternalActionResponse.model_validate(action)


@router.post(
    "/api/v1/interviews/{id}/cancel-requests",
    response_model=ExternalActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft Human-Approved Cancellation Request",
    operation_id="request_interview_cancellation",
)
def request_interview_cancellation(
    payload: RequestCancellationPayload,
    interview_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ExternalActionResponse:
    """Drafts an approved external action to cancel an interview on Google Calendar."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        action = InterviewLifecycleService.request_cancellation(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            reason=payload.reason,
            send_updates=payload.send_updates,
        )
        return ExternalActionResponse.model_validate(action)


@router.post(
    "/api/v1/interviews/{id}/completion",
    response_model=InterviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Record Human Interview Completion (COMPLETED / NO_SHOW)",
    operation_id="record_interview_completion",
)
def record_interview_completion(
    payload: CompleteInterviewPayload,
    interview_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> InterviewResponse:
    """Explicit human recording of COMPLETED or NO_SHOW status with mandatory justification."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        interview = InterviewLifecycleService.record_completion(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            outcome=payload.outcome,
            reason=payload.reason,
        )
        return InterviewResponse.model_validate(interview)


@router.get(
    "/api/v1/interviews/{id}/reminders",
    response_model=list[ReminderScheduleResponse],
    status_code=status.HTTP_200_OK,
    summary="List Scheduled and Historical Reminders",
    operation_id="list_interview_reminders",
)
def list_interview_reminders(
    interview_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[ReminderScheduleResponse]:
    """Lists reminder schedule records associated with the interview."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        _, _, _, _, _, reminders = InterviewLifecycleService.get_interview(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
        )
        return [ReminderScheduleResponse.model_validate(r) for r in reminders]


@router.get(
    "/api/v1/interviews/{id}/notifications",
    response_model=list[NotificationResponse],
    status_code=status.HTTP_200_OK,
    summary="List Dispatch Notifications Ledger",
    operation_id="list_interview_notifications",
)
def list_interview_notifications(
    interview_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[NotificationResponse]:
    """Lists delivery attempts and status history for interview notifications."""
    AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        _, _, _, _, notifications, _ = InterviewLifecycleService.get_interview(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
        )
        return [NotificationResponse.model_validate(n) for n in notifications]


@router.post(
    "/api/v1/public/confirmation",
    response_model=InterviewRevisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Candidate Public Confirmation or Decline",
    operation_id="public_confirm_interview",
)
def public_confirm_interview(
    payload: PublicConfirmInterviewRequest,
    db: Session = Depends(get_db_session),
) -> InterviewRevisionResponse:
    """Candidate confirms or declines attendance via their validated candidate session token."""
    with UnitOfWork(session=db, tenant_id=None) as uow:
        revision = InterviewLifecycleService.public_confirm_attendance(
            uow=uow,
            token=payload.token,
            interview_id=payload.interview_id,
            decision=payload.decision,
        )
        return InterviewRevisionResponse.model_validate(revision)


@router.post(
    "/api/v1/public/change-requests",
    response_model=InterviewRevisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Candidate Public Change Request",
    operation_id="public_request_interview_change",
)
def public_request_interview_change(
    payload: PublicChangeRequest,
    db: Session = Depends(get_db_session),
) -> InterviewRevisionResponse:
    """Candidate requests a scheduling change without directly modifying the calendar."""
    with UnitOfWork(session=db, tenant_id=None) as uow:
        revision = InterviewLifecycleService.public_request_change(
            uow=uow,
            token=payload.token,
            interview_id=payload.interview_id,
            reason=payload.reason,
        )
        return InterviewRevisionResponse.model_validate(revision)


@router.post(
    "/api/v1/webhooks/google/calendar",
    status_code=status.HTTP_200_OK,
    summary="Google Calendar Push Notification Webhook",
    operation_id="google_calendar_webhook",
)
async def google_calendar_webhook(
    request: Request,
    db: Session = Depends(get_db_session),
    calendar_provider: CalendarProvider = Depends(get_calendar_provider),
) -> dict[str, str]:
    """Receives Google Calendar push notification headers and acknowledges webhook."""
    channel_id = request.headers.get("X-Goog-Channel-ID")
    resource_id = request.headers.get("X-Goog-Resource-ID")
    resource_state = request.headers.get("X-Goog-Resource-State")

    if not channel_id or not resource_id:
        return {"status": "ignored", "reason": "missing_required_headers"}

    return {"status": "received", "channel_id": channel_id, "state": resource_state or "exists"}
