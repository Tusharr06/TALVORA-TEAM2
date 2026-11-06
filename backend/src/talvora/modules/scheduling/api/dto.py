"""Pydantic DTOs for Interview Scheduling and Candidate Availability APIs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateInterviewRequestPayload(BaseModel):
    application_id: UUID = Field(
        description="Eligible candidate application ID (must be INTERVIEW_APPROVED)."
    )
    title: str = Field(description="Title of interview round e.g. Technical Screen.")
    target_date_start: datetime = Field(description="Scheduling window opening timestamp.")
    target_date_end: datetime = Field(description="Scheduling window closing timestamp.")
    duration_minutes: int = Field(
        default=45, ge=15, le=240, description="Interview duration in minutes."
    )
    location_type: str = Field(
        default="GOOGLE_MEET", description="GOOGLE_MEET, PHONE, or IN_PERSON."
    )
    timezone: str = Field(
        default="UTC", description="IANA timezone identifier e.g. America/New_York."
    )
    working_hours_start: int = Field(default=9, ge=0, le=23)
    working_hours_end: int = Field(default=17, ge=1, le=24)
    working_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    buffer_before_minutes: int = Field(default=10, ge=0, le=60)
    buffer_after_minutes: int = Field(default=10, ge=0, le=60)
    minimum_lead_time_hours: int = Field(default=24, ge=0, le=168)
    interviewer_membership_ids: list[UUID] | None = Field(default=None)


class InterviewParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    membership_id: UUID
    role: str
    is_required: bool
    calendar_id: str


class InterviewRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    application_id: UUID
    candidate_id: UUID
    job_id: UUID
    title: str
    duration_minutes: int
    status: str
    location_type: str
    organizer_membership_id: UUID
    target_date_start: datetime
    target_date_end: datetime
    timezone: str
    created_at: datetime
    version: int


class IssueAvailabilityLinkResponse(BaseModel):
    availability_url: str = Field(description="Single-use public scheduling link for candidate.")
    link_id: UUID
    expires_at: datetime


class PublicInterviewDetailsResponse(BaseModel):
    title: str
    company_name: str
    duration_minutes: int
    target_date_start: datetime
    target_date_end: datetime
    timezone: str


class AvailabilityIntervalItem(BaseModel):
    start: str = Field(description="ISO 8601 interval start timestamp.")
    end: str = Field(description="ISO 8601 interval end timestamp.")


class SubmitAvailabilityRequest(BaseModel):
    candidate_timezone: str = Field(default="UTC", description="Candidate local timezone.")
    available_intervals: list[AvailabilityIntervalItem] = Field(min_length=1)


class GenerateProposalsRequest(BaseModel):
    step_minutes: int = Field(default=15, ge=5, le=60)
    max_proposals: int = Field(default=5, ge=1, le=20)


class SlotProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interview_request_id: UUID
    slot_start_utc: datetime
    slot_end_utc: datetime
    interviewer_membership_ids: list[str]
    status: str
    invalidation_reason: str | None = None
    expires_at: datetime
    created_at: datetime


# ============================================================================
# P13 — Human-Approved External Actions DTOs
# ============================================================================


class CreateExternalActionRequest(BaseModel):
    action_type: str = Field(
        description="Action to execute e.g. CREATE_INTERVIEW or SEND_AVAILABILITY_REQUEST."
    )
    resource_type: str = Field(default="interview_request")
    resource_id: UUID = Field(description="Target aggregate ID e.g. interview_request ID.")
    resource_version: int = Field(default=1)
    proposal_id: UUID | None = Field(default=None, description="Required for CREATE_INTERVIEW.")
    connection_id: UUID | None = Field(default=None)
    custom_notes: str | None = Field(default=None)


class ApproveExternalActionRequest(BaseModel):
    expected_payload_hash: str = Field(
        description="SHA-256 canonical hash of the action payload binding approval."
    )
    expected_resource_version: int = Field(description="Expected version of target resource.")


class ResolveExternalActionRequest(BaseModel):
    decision: str = Field(description="REJECT, CANCEL, or FORCE_RELEASE.")
    reason: str = Field(description="Operational rationale for resolution.")


class ExternalActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    action_type: str
    status: str
    resource_type: str
    resource_id: UUID
    resource_version: int
    payload: dict[str, Any]
    payload_hash: str
    verified_recipient_email: str
    expires_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    external_action_id: UUID
    decision: str
    approved_payload_hash: str
    approved_resource_version: int
    auth_time: datetime
    valid_until: datetime
    created_at: datetime


class ProviderAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    attempt_number: int
    provider: str
    provider_event_id: str | None = None
    provider_message_id: str | None = None
    status: str
    error_code: str | None = None
    error_detail: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


# ============================================================================
# P14 — Interview Lifecycle, RSVP, Notifications, and Reminders DTOs
# ============================================================================


class InterviewRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interview_id: UUID
    revision_number: int
    slot_start_utc: datetime
    slot_end_utc: datetime
    timezone: str
    provider_etag: str | None = None
    external_event_status: str
    candidate_confirmation_status: str
    candidate_confirmed_at: datetime | None = None
    candidate_change_reason: str | None = None
    participant_rsvp_snapshot: dict[str, Any]
    created_at: datetime


class InterviewReservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interview_id: UUID
    membership_id: UUID
    status: str
    slot_start_utc: datetime
    slot_end_utc: datetime
    interview_start_utc: datetime
    interview_end_utc: datetime
    release_reason: str | None = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interview_id: UUID
    recipient_email: str
    notification_type: str
    channel: str
    status: str
    provider_message_id: str | None = None
    sent_at: datetime | None = None
    created_at: datetime


class ReminderScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interview_id: UUID
    interview_revision_id: UUID
    reminder_kind: str
    scheduled_for: datetime
    recipient_email: str
    status: str
    skip_reason: str | None = None
    created_at: datetime


class InterviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    interview_request_id: UUID
    application_id: UUID
    candidate_id: UUID
    job_id: UUID
    organizer_membership_id: UUID
    title: str
    location_type: str
    status: str
    google_calendar_event_id: str | None = None
    google_meet_link: str | None = None
    google_calendar_html_link: str | None = None
    external_event_etag: str | None = None
    current_revision_id: UUID | None = None
    completion_reason: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class InterviewDetailResponse(BaseModel):
    interview: InterviewResponse
    current_revision: InterviewRevisionResponse
    all_revisions: list[InterviewRevisionResponse]
    reservations: list[InterviewReservationResponse]
    notifications: list[NotificationResponse]
    reminders: list[ReminderScheduleResponse]


class RequestReschedulePayload(BaseModel):
    new_slot_proposal_id: UUID = Field(description="Approved replacement SlotProposal ID.")
    reason: str = Field(min_length=3, description="Operational reason for reschedule.")


class RequestCancellationPayload(BaseModel):
    reason: str = Field(min_length=3, description="Operational reason for cancellation.")
    send_updates: bool = Field(
        default=True, description="Whether to dispatch provider updates to attendees."
    )


class CompleteInterviewPayload(BaseModel):
    outcome: str = Field(description="COMPLETED or NO_SHOW.")
    reason: str = Field(
        min_length=3, description="Mandatory recruiter observation or policy explanation."
    )


class PublicConfirmInterviewRequest(BaseModel):
    token: str = Field(description="Opaque candidate link token.")
    interview_id: UUID
    decision: str = Field(description="CONFIRMED or DECLINED.")


class PublicChangeRequest(BaseModel):
    token: str = Field(description="Opaque candidate link token.")
    interview_id: UUID
    reason: str = Field(min_length=3, description="Reason for requesting schedule change.")
