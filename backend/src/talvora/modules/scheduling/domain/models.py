"""Domain Models for Interview Requests, Candidate Links, Availability, and Deterministic Slot Proposals."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class InterviewRequestStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_AVAILABILITY = "AWAITING_AVAILABILITY"
    SLOTS_PROPOSED = "SLOTS_PROPOSED"
    SCHEDULED = "SCHEDULED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class InterviewLocationType(StrEnum):
    GOOGLE_MEET = "GOOGLE_MEET"
    PHONE = "PHONE"
    IN_PERSON = "IN_PERSON"


class ParticipantRole(StrEnum):
    INTERVIEWER = "INTERVIEWER"
    ORGANIZER = "ORGANIZER"
    OBSERVER = "OBSERVER"


class ProposalStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    SELECTED = "SELECTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    CONVERTED = "CONVERTED"


class ExternalActionType(StrEnum):
    SEND_AVAILABILITY_REQUEST = "SEND_AVAILABILITY_REQUEST"
    CREATE_INTERVIEW = "CREATE_INTERVIEW"
    RESCHEDULE_INTERVIEW = "RESCHEDULE_INTERVIEW"
    CANCEL_INTERVIEW = "CANCEL_INTERVIEW"
    SEND_REVIEWED_NOTIFICATION = "SEND_REVIEWED_NOTIFICATION"


class ExternalActionStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RESOLVED = "RESOLVED"


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ProviderAttemptStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class InterviewStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    CHANGE_PENDING = "CHANGE_PENDING"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"


class CandidateConfirmationStatus(StrEnum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    CHANGE_REQUESTED = "CHANGE_REQUESTED"


class InterviewReservationStatus(StrEnum):
    HELD = "HELD"
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    UNKNOWN = "UNKNOWN"


class NotificationType(StrEnum):
    CANDIDATE_INVITATION = "CANDIDATE_INVITATION"
    INTERVIEWER_INVITATION = "INTERVIEWER_INVITATION"
    RESCHEDULE_NOTICE = "RESCHEDULE_NOTICE"
    CANCELLATION_NOTICE = "CANCELLATION_NOTICE"
    REMINDER_24H = "REMINDER_24H"
    REMINDER_1H = "REMINDER_1H"


class NotificationChannel(StrEnum):
    GMAIL = "GMAIL"
    CALENDAR_INVITE = "CALENDAR_INVITE"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ReminderKind(StrEnum):
    REMINDER_24H = "REMINDER_24H"
    REMINDER_1H = "REMINDER_1H"


class ReminderStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    CLAIMED = "CLAIMED"
    SENT = "SENT"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


# ============================================================================
# Declarative Models
# ============================================================================


class InterviewRequest(Base):
    """Aggregate root tracking interview preparation and deterministic scheduling."""

    __tablename__ = "interview_request"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=InterviewRequestStatus.DRAFT.value
    )
    location_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default=InterviewLocationType.GOOGLE_MEET.value
    )
    organizer_membership_id: Mapped[UUID] = mapped_column(nullable=False)

    target_date_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_date_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    working_hours_start: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    working_hours_end: Mapped[int] = mapped_column(Integer, nullable=False, default=17)
    working_days: Mapped[list[int]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=lambda: [0, 1, 2, 3, 4],  # Monday to Friday
    )
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    minimum_lead_time_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")

    created_by: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_request"),
        UniqueConstraint("tenant_id", "id", name="uq_interview_request_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_request_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_interview_request_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_interview_request_candidate",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_interview_request_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_request_organizer",
            ondelete="RESTRICT",
        ),
        Index("ix_interview_req_app", "tenant_id", "application_id"),
        Index("ix_interview_req_status", "tenant_id", "status"),
    )


class InterviewParticipant(Base):
    """Internal interviewer or organizer attached to an interview request."""

    __tablename__ = "interview_participant"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ParticipantRole.INTERVIEWER.value
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    calendar_id: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_participant"),
        UniqueConstraint("tenant_id", "id", name="uq_interview_participant_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "interview_request_id",
            "membership_id",
            name="uq_interview_participant_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_participant_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_interview_participant_request",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_participant_membership",
            ondelete="CASCADE",
        ),
    )


class CandidateAvailabilityLink(Base):
    """Opaque, single-use candidate link for availability collection."""

    __tablename__ = "candidate_availability_link"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    candidate_contact_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    candidate_contact_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_cand_avail_link"),
        UniqueConstraint("tenant_id", "id", name="uq_cand_avail_link_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_cand_avail_link_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_cand_avail_link_request",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_cand_avail_link_candidate",
            ondelete="CASCADE",
        ),
    )


class AvailabilitySubmission(Base):
    """Recorded candidate availability time windows submitted via link."""

    __tablename__ = "availability_submission"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    link_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    candidate_timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    available_intervals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_availability_submission"),
        UniqueConstraint("tenant_id", "id", name="uq_availability_submission_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_availability_submission_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_availability_submission_request",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "link_id"],
            ["candidate_availability_link.tenant_id", "candidate_availability_link.id"],
            name="fk_availability_submission_link",
            ondelete="CASCADE",
        ),
    )


class AvailabilitySnapshot(Base):
    """Immutable snapshot of interviewer FreeBusy + candidate windows at solver execution time."""

    __tablename__ = "availability_snapshot"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    interviewer_freebusy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    candidate_availability_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_availability_snapshot"),
        UniqueConstraint("tenant_id", "id", name="uq_availability_snapshot_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_availability_snapshot_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_availability_snapshot_request",
            ondelete="CASCADE",
        ),
    )


class SlotProposal(Base):
    """A deterministic slot proposal generated for recruiter review."""

    __tablename__ = "slot_proposal"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slot_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interviewer_membership_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )

    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ProposalStatus.PENDING_REVIEW.value
    )
    invalidation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_slot_proposal"),
        UniqueConstraint("tenant_id", "id", name="uq_slot_proposal_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_slot_proposal_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_slot_proposal_request",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["availability_snapshot.tenant_id", "availability_snapshot.id"],
            name="fk_slot_proposal_snapshot",
            ondelete="CASCADE",
        ),
        Index("ix_slot_proposal_req_status", "tenant_id", "interview_request_id", "status"),
    )


class InternalReservation(Base):
    """Internal provisional hold on interviewer time to avoid cross-candidate double-booking."""

    __tablename__ = "internal_reservation"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slot_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ReservationStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_internal_reservation"),
        UniqueConstraint("tenant_id", "id", name="uq_internal_reservation_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_internal_reservation_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_internal_reservation_membership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_internal_reservation_request",
            ondelete="CASCADE",
        ),
        Index(
            "ix_internal_res_membership_window",
            "tenant_id",
            "membership_id",
            "slot_start_utc",
            "slot_end_utc",
        ),
    )


# ============================================================================
# P13 — Human-Approved External Actions & Recoverable Booking Models
# ============================================================================


class ExternalAction(Base):
    """Auditable external action tracking exact reviewed parameters, human approval, and dispatch state."""

    __tablename__ = "external_action"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ExternalActionStatus.PENDING_APPROVAL.value
    )
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    verified_recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    contact_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    contact_revision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    organizer_membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    organizer_connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_by_membership_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_external_action"),
        UniqueConstraint("tenant_id", "id", name="uq_external_action_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_external_action_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_external_action_organizer_mem",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_external_action_creator_mem",
            ondelete="RESTRICT",
        ),
        Index("ix_external_action_status", "tenant_id", "status"),
        Index("ix_external_action_resource", "tenant_id", "resource_id", "action_type"),
        Index("ix_external_action_idemp", "tenant_id", "idempotency_key"),
    )


class Approval(Base):
    """Cryptographically bound, human-signed approval record binding exact payload and version."""

    __tablename__ = "approval"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    external_action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    approver_user_id: Mapped[UUID] = mapped_column(nullable=False)
    approver_membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    auth_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    decision: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ApprovalDecision.APPROVED.value
    )
    approved_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)

    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_approval"),
        UniqueConstraint("tenant_id", "id", name="uq_approval_tenant_id"),
        UniqueConstraint("tenant_id", "external_action_id", name="uq_approval_external_action"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_approval_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "external_action_id"],
            ["external_action.tenant_id", "external_action.id"],
            name="fk_approval_action",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approver_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_approval_membership",
            ondelete="RESTRICT",
        ),
    )


class ProviderAttempt(Base):
    """Historical ledger of provider HTTP write attempts and outcomes for reconciliation."""

    __tablename__ = "provider_attempt"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    external_action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    request_payload_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    response_payload_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_provider_attempt"),
        UniqueConstraint("tenant_id", "id", name="uq_provider_attempt_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_provider_attempt_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "external_action_id"],
            ["external_action.tenant_id", "external_action.id"],
            name="fk_provider_attempt_action",
            ondelete="CASCADE",
        ),
        Index(
            "ix_provider_attempt_action_seq", "tenant_id", "external_action_id", "attempt_number"
        ),
    )


class Interview(Base):
    """Aggregate root tracking a booked interview, external provider synchronization, and lifecycle."""

    __tablename__ = "interview"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    organizer_membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    location_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default=InterviewLocationType.GOOGLE_MEET.value
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=InterviewStatus.SCHEDULED.value
    )

    google_calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_meet_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    google_calendar_html_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    external_event_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)

    completion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconciliation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_interview"),
        UniqueConstraint("tenant_id", "id", name="uq_interview_tenant_id"),
        UniqueConstraint("tenant_id", "interview_request_id", name="uq_interview_request_active"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_interview_request",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_interview_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_interview_candidate",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_interview_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_organizer",
            ondelete="RESTRICT",
        ),
        Index("ix_interview_status", "tenant_id", "status"),
        Index("ix_interview_event_id", "tenant_id", "google_calendar_event_id"),
    )


class InterviewRevision(Base):
    """Immutable revision representing a schedule window, ETag, candidate confirmation, and participant RSVPs."""

    __tablename__ = "interview_revision"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slot_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")

    provider_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_event_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="confirmed"
    )

    candidate_confirmation_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=CandidateConfirmationStatus.UNCONFIRMED.value
    )
    candidate_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    candidate_change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    participant_rsvp_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    external_action_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_revision"),
        UniqueConstraint("tenant_id", "id", name="uq_interview_revision_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "interview_id",
            "revision_number",
            name="uq_interview_revision_num",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_revision_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_interview_revision_interview",
            ondelete="CASCADE",
        ),
        Index("ix_interview_rev_interview", "tenant_id", "interview_id"),
    )


class InterviewReservation(Base):
    """Internal schedule reservation protecting required interviewer time intervals across states."""

    __tablename__ = "interview_reservation"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    interview_revision_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=InterviewReservationStatus.ACTIVE.value
    )
    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slot_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interview_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interview_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    release_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_reservation"),
        UniqueConstraint("tenant_id", "id", name="uq_interview_reservation_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_reservation_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_interview_reservation_interview",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_reservation_membership",
            ondelete="CASCADE",
        ),
        Index(
            "ix_interview_res_mem_window",
            "tenant_id",
            "membership_id",
            "status",
            "slot_start_utc",
            "slot_end_utc",
        ),
    )


# ============================================================================
# P14 — Lifecycle, Notifications, Reminders & Sync Models
# ============================================================================


class Notification(Base):
    """Audit record for candidate invitations, reminder messages, and cancellation notices."""

    __tablename__ = "notification"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    interview_revision_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    external_action_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)

    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(50), nullable=False, default=NotificationChannel.GMAIL.value
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=NotificationStatus.PENDING.value
    )

    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_notification"),
        UniqueConstraint("tenant_id", "id", name="uq_notification_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_notification_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_notification_interview",
            ondelete="CASCADE",
        ),
        Index("ix_notification_interview", "tenant_id", "interview_id"),
        Index("ix_notification_status", "tenant_id", "status"),
    )


class ReminderSchedule(Base):
    """Persisted reminder execution ledger with strict revision deduplication and cancellation."""

    __tablename__ = "reminder_schedule"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    reminder_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ReminderStatus.SCHEDULED.value
    )

    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_action_id: Mapped[UUID | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_reminder_schedule"),
        UniqueConstraint("tenant_id", "id", name="uq_reminder_schedule_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "interview_revision_id",
            "recipient_email",
            "reminder_kind",
            name="uq_reminder_dedup",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_reminder_schedule_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_reminder_schedule_interview",
            ondelete="CASCADE",
        ),
        Index("ix_reminder_status_due", "tenant_id", "status", "scheduled_for"),
        Index("ix_reminder_interview", "tenant_id", "interview_id"),
    )


class ProviderSyncState(Base):
    """Synchronization checkpoint and Google webhook subscription tracking for organizer calendars."""

    __tablename__ = "provider_sync_state"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    organizer_membership_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    calendar_id: Mapped[str] = mapped_column(String(320), nullable=False)

    sync_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_expiration: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_provider_sync_state"),
        UniqueConstraint("tenant_id", "id", name="uq_provider_sync_state_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "organizer_membership_id",
            "calendar_id",
            name="uq_provider_sync_calendar",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_provider_sync_state_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_provider_sync_membership",
            ondelete="CASCADE",
        ),
        Index("ix_provider_sync_channel", "tenant_id", "channel_id"),
    )
