"""Domain Models for Job Applications and the Human-Controlled Hiring Pipeline.

An `Application` is a job-specific aggregate, deliberately independent from the
tenant-local `Candidate` identity aggregate owned by the `candidates` module: the same
candidate may hold independent applications against different jobs, each with its own
pipeline stage, assignment, and history. Stage transitions are human commands only --
never a side effect of AI output or a background/system principal -- and every
transition is recorded as an immutable `StageEvent` alongside an audit entry in the same
transaction.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ============================================================================
# Enums
# ============================================================================


class ApplicationSource(StrEnum):
    """How an application came into existence."""

    MANUAL_ENTRY = "MANUAL_ENTRY"
    UPLOAD_INTAKE = "UPLOAD_INTAKE"


class ApplicationStage(StrEnum):
    """Human-controlled hiring pipeline stages. No screening outcome writes this field."""

    NEW = "NEW"
    IN_REVIEW = "IN_REVIEW"
    INTERVIEW_APPROVED = "INTERVIEW_APPROVED"
    INTERVIEWING = "INTERVIEWING"
    DECISION_PENDING = "DECISION_PENDING"
    HIRED = "HIRED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


# Terminal stages that currently block further forward progress without an explicit,
# reasoned "reopen" transition.
TERMINAL_STAGES: frozenset[str] = frozenset(
    {
        ApplicationStage.HIRED.value,
        ApplicationStage.REJECTED.value,
        ApplicationStage.WITHDRAWN.value,
    }
)

# Terminal *negative* outcomes: these may be reopened (e.g. a recruiter error, or a
# candidate retracting a withdrawal) with a mandatory reason. HIRED is never reopened
# in this phase.
REOPENABLE_TERMINAL_STAGES: frozenset[str] = frozenset(
    {ApplicationStage.REJECTED.value, ApplicationStage.WITHDRAWN.value}
)

# Explicit allowed transition matrix. Human actor, capability, job assignment, and
# expected version are all mandatory and checked by the application service -- this
# table only encodes which (from, to) pairs are structurally legal.
ALLOWED_STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    ApplicationStage.NEW.value: frozenset(
        {
            ApplicationStage.IN_REVIEW.value,
            ApplicationStage.REJECTED.value,
            ApplicationStage.WITHDRAWN.value,
        }
    ),
    ApplicationStage.IN_REVIEW.value: frozenset(
        {
            ApplicationStage.INTERVIEW_APPROVED.value,
            ApplicationStage.REJECTED.value,
            ApplicationStage.WITHDRAWN.value,
        }
    ),
    ApplicationStage.INTERVIEW_APPROVED.value: frozenset(
        {
            ApplicationStage.INTERVIEWING.value,
            ApplicationStage.REJECTED.value,
            ApplicationStage.WITHDRAWN.value,
        }
    ),
    ApplicationStage.INTERVIEWING.value: frozenset(
        {
            ApplicationStage.DECISION_PENDING.value,
            ApplicationStage.REJECTED.value,
            ApplicationStage.WITHDRAWN.value,
        }
    ),
    ApplicationStage.DECISION_PENDING.value: frozenset(
        {
            ApplicationStage.HIRED.value,
            ApplicationStage.REJECTED.value,
            ApplicationStage.WITHDRAWN.value,
        }
    ),
    # HIRED is fully terminal in this phase: no reopen transition is defined.
    ApplicationStage.HIRED.value: frozenset(),
    # Reopening a negative terminal decision always requires a mandatory reason and
    # returns the application to active recruiter review, never directly to a later
    # stage, so the reopened case is re-screened by a human.
    ApplicationStage.REJECTED.value: frozenset({ApplicationStage.IN_REVIEW.value}),
    ApplicationStage.WITHDRAWN.value: frozenset({ApplicationStage.IN_REVIEW.value}),
}

# Stages whose entry mandates a non-empty human-authored reason.
REASON_REQUIRED_TARGET_STAGES: frozenset[str] = frozenset(
    {ApplicationStage.REJECTED.value, ApplicationStage.WITHDRAWN.value}
)


def is_reopen_transition(current_stage: str, target_stage: str) -> bool:
    """True when the transition reopens a previously terminal negative decision."""
    return current_stage in REOPENABLE_TERMINAL_STAGES and target_stage not in TERMINAL_STAGES


def reason_required(current_stage: str, target_stage: str) -> bool:
    """A reason is mandatory for entering a negative terminal stage or reopening one."""
    return target_stage in REASON_REQUIRED_TARGET_STAGES or is_reopen_transition(
        current_stage, target_stage
    )


# Stages eligible to be considered for interview scheduling by a future module.
INTERVIEW_ELIGIBLE_STAGES: frozenset[str] = frozenset(
    {ApplicationStage.INTERVIEW_APPROVED.value, ApplicationStage.INTERVIEWING.value}
)


# ============================================================================
# Text Sanitization
# ============================================================================

_DANGEROUS_BLOCKS_RE = re.compile(
    r"<(script|style|iframe|object|embed|applet)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_SELF_CLOSING_RE = re.compile(
    r"<(script|style|iframe|object|embed|applet|link|meta)[^>]*/>",
    re.IGNORECASE | re.DOTALL,
)
_RAW_HTML_TAG_RE = re.compile(r"<\s*/?\s*([a-zA-Z0-9]+)(\s+[^>]*)?>", re.DOTALL)


def sanitize_text(text_value: str | None) -> str | None:
    """Sanitize candidate/recruiter-entered free text to prevent stored XSS."""
    if not text_value:
        return text_value
    cleaned = _DANGEROUS_BLOCKS_RE.sub("", text_value)
    cleaned = _DANGEROUS_SELF_CLOSING_RE.sub("", cleaned)
    return _RAW_HTML_TAG_RE.sub("", cleaned).strip()


# ============================================================================
# SQLAlchemy Declarative Models
# ============================================================================


class Application(Base):
    """A candidate's independent application against a single job vacancy."""

    __tablename__ = "application"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    stage: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ApplicationStage.NEW.value
    )
    intake_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ApplicationSource.MANUAL_ENTRY.value
    )
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    created_by: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_application"),
        UniqueConstraint("tenant_id", "id", name="uq_application_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_application_tenant",
            ondelete="CASCADE",
        ),
        # NOTE: the composite FKs to job/candidate are added in the migration, since the
        # jobs/candidates tables live in sibling modules whose ORM classes this module
        # does not import (cross-module access stays at the application-service layer).
        Index("ix_application_tenant_job", "tenant_id", "job_id"),
        Index("ix_application_tenant_candidate", "tenant_id", "candidate_id"),
        Index("ix_application_tenant_stage", "tenant_id", "stage"),
        Index("ix_application_tenant_created", "tenant_id", "created_at", "id"),
        # Partial unique index: only one ACTIVE (non-withdrawn, non-rejected) application
        # per job/candidate pair. Mirrored verbatim in the migration for PostgreSQL/SQLite.
        Index(
            "uq_application_active_per_job_candidate",
            "tenant_id",
            "job_id",
            "candidate_id",
            unique=True,
            postgresql_where=text("stage NOT IN ('WITHDRAWN', 'REJECTED')"),
            sqlite_where=text("stage NOT IN ('WITHDRAWN', 'REJECTED')"),
        ),
    )


class StageEvent(Base):
    """Immutable record of a single pipeline stage transition command."""

    __tablename__ = "stage_event"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    from_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_stage_event"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_stage_event_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_stage_event_application",
            ondelete="CASCADE",
        ),
        Index("ix_stage_event_tenant_application", "tenant_id", "application_id", "created_at"),
    )
