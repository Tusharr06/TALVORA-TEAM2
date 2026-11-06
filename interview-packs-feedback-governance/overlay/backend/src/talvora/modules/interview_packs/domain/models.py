"""Domain Models and Pure Validators for Competency Frameworks and Interview Packs (P15).

Enforces observable job-relevant rubrics (1-4 + NOT_ASSESSED), rejects personality
and protected-attribute inference, binds every pack revision to immutable input
snapshots, distinguishes role-generic from candidate-specific premises, and ensures
timing fits within the scheduled interview duration.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

from talvora.modules.jobs.domain.models import sanitize_text, validate_no_protected_traits
from talvora.shared.infrastructure.models import Base
from talvora.shared.transport.problems import ProblemException


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ============================================================================
# Enums
# ============================================================================


class FrameworkStatus(StrEnum):
    """Lifecycle status of a competency framework container."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class FrameworkVersionStatus(StrEnum):
    """Lifecycle status of an immutable competency framework version."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class InterviewStageType(StrEnum):
    """Supported interview stage classifications."""

    SCREENING = "SCREENING"
    TECHNICAL = "TECHNICAL"
    SYSTEM_DESIGN = "SYSTEM_DESIGN"
    BEHAVIORAL = "BEHAVIORAL"
    HIRING_MANAGER = "HIRING_MANAGER"
    FINAL = "FINAL"


class RatingAnchorKey(StrEnum):
    """Allowed rubric anchor levels. Missing assessment is explicit, never zero."""

    LEVEL_1 = "1"
    LEVEL_2 = "2"
    LEVEL_3 = "3"
    LEVEL_4 = "4"
    NOT_ASSESSED = "NOT_ASSESSED"


REQUIRED_ANCHOR_KEYS: frozenset[str] = frozenset({"1", "2", "3", "4", "NOT_ASSESSED"})


class InterviewContextStatus(StrEnum):
    """Booked interview lifecycle states visible to pack and feedback workflows."""

    BOOKED = "BOOKED"
    RESCHEDULED = "RESCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"


class PackRevisionStatus(StrEnum):
    """Lifecycle of an interview pack revision."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


class PackContentScope(StrEnum):
    """Whether an item is generic to the role/stage or specific to the candidate."""

    ROLE_GENERIC = "ROLE_GENERIC"
    CANDIDATE_SPECIFIC = "CANDIDATE_SPECIFIC"


class PackClaimClassification(StrEnum):
    """Playbook Section 11.2 mandatory provenance classification for candidate statements."""

    RESUME_SUPPORTED = "RESUME_SUPPORTED"
    DERIVED_FROM_SUPPORTED_FACTS = "DERIVED_FROM_SUPPORTED_FACTS"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"
    HUMAN_ASSERTION_UNVERIFIED = "HUMAN_ASSERTION_UNVERIFIED"


class QuestionCategory(StrEnum):
    """Required interview question categories."""

    TECHNICAL = "TECHNICAL"
    BEHAVIORAL = "BEHAVIORAL"
    SCENARIO = "SCENARIO"
    PROJECT_SPECIFIC = "PROJECT_SPECIFIC"
    CLARIFICATION = "CLARIFICATION"


class PremiseClassification(StrEnum):
    """Provenance label for a question's underlying premise."""

    ROLE_GENERIC = "ROLE_GENERIC"
    RESUME_SUPPORTED = "RESUME_SUPPORTED"
    HYPOTHETICAL = "HYPOTHETICAL"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    HUMAN_ASSERTION_UNVERIFIED = "HUMAN_ASSERTION_UNVERIFIED"


class PackReviewDecision(StrEnum):
    """Human review decision on a draft interview pack revision."""

    APPROVED_FOR_PUBLISH = "APPROVED_FOR_PUBLISH"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


# ============================================================================
# Pure Domain Validators
# ============================================================================

_PERSONALITY_OR_CULTURE_FIT_RE = re.compile(
    r"\b("
    r"culture\s+fit|cultural\s+fit|vibe\s+check|personality\s+type|personality\s+trait|"
    r"introvert|extrovert|mbti|myers[-\s]?briggs|enneagram|astrology|zodiac|"
    r"beer\s+test|sunday\s+test|likeability\s+score|charisma\s+score"
    r")\b",
    re.IGNORECASE,
)

_PROMPT_INJECTION_RE = re.compile(
    r"("
    r"ignore\s+(all\s+)?previous\s+instructions|"
    r"disregard\s+(all\s+)?system\s+prompts?|"
    r"call\s+tool\s*:|"
    r"invoke\s+(calendar|gmail|send_mail|create_event|book_calendar)|"
    r"override\s+hiring\s+decision|"
    r"automatically\s+(hire|reject)\s+candidate"
    r")",
    re.IGNORECASE,
)

_IMPLIED_PAST_WORK_IN_HYPOTHETICAL_RE = re.compile(
    r"\b("
    r"on\s+your\s+resume\s+you\s+(built|led|designed|migrated|architected|managed)|"
    r"in\s+your\s+previous\s+role\s+at\s+\w+\s+you\s+(built|led|designed)|"
    r"since\s+you\s+already\s+built|"
    r"walk\s+us\s+through\s+the\s+exact\s+project\s+where\s+you"
    r")\b",
    re.IGNORECASE,
)


def validate_job_relevant_observable_text(text: str, context_label: str) -> str:
    """Sanitizes text and blocks protected traits as well as personality/culture-fit inference."""
    cleaned = sanitize_text(text)
    if not cleaned:
        raise ProblemException(
            status_code=422,
            title="Validation Error",
            code="VALIDATION_ERROR",
            detail=f"{context_label} cannot be empty after sanitization.",
        )
    validate_no_protected_traits(cleaned, context_label=context_label)
    match = _PERSONALITY_OR_CULTURE_FIT_RE.search(cleaned)
    if match:
        raise ProblemException(
            status_code=422,
            title="Disallowed Rubric Criterion",
            code="DISALLOWED_PERSONALITY_INFERENCE",
            detail=(
                f"Disallowed non-observable personality or cultural-fit construct "
                f"'{match.group(0)}' in {context_label}. Rubrics must evaluate observable "
                "job-relevant evidence only."
            ),
        )
    return cleaned


def validate_rubric_anchors(anchors: dict[str, str], competency_name: str) -> dict[str, str]:
    """Validates that a competency defines observable anchors for 1, 2, 3, 4, and NOT_ASSESSED."""
    keys = set(anchors.keys())
    if keys != REQUIRED_ANCHOR_KEYS:
        missing = sorted(REQUIRED_ANCHOR_KEYS - keys)
        extra = sorted(keys - REQUIRED_ANCHOR_KEYS)
        raise ProblemException(
            status_code=422,
            title="Invalid Rubric Anchors",
            code="VALIDATION_ERROR",
            detail=(
                f"Competency '{competency_name}' must define exact anchors "
                f"{sorted(REQUIRED_ANCHOR_KEYS)}; missing={missing}, unexpected={extra}."
            ),
        )
    cleaned_anchors: dict[str, str] = {}
    for key in ("1", "2", "3", "4", "NOT_ASSESSED"):
        raw_val = anchors.get(key, "")
        cleaned_anchors[key] = validate_job_relevant_observable_text(
            raw_val, context_label=f"competency '{competency_name}' anchor {key}"
        )
    return cleaned_anchors


def detect_prompt_injection(text: str) -> list[str]:
    """Returns detected prompt-injection or unauthorized tool-invocation markers in untrusted text."""
    if not text:
        return []
    return [m.group(0) for m in _PROMPT_INJECTION_RE.finditer(text)]


def delimit_untrusted_text(label: str, raw_text: str) -> str:
    """Wraps untrusted resume/job/recruiter text in explicit non-executable data delimiters."""
    safe_body = raw_text.replace("</untrusted_data>", "[/untrusted_data]")
    return f'<untrusted_data source="{label}">\n{safe_body}\n</untrusted_data>'


def compute_time_allocation(duration_minutes: int, question_minutes_sum: int) -> dict[str, int]:
    """Computes deterministic introduction, questions, candidate Q&A, and closing budgets.

    Guarantees total allocated time fits strictly within `duration_minutes`.
    For example:
      - 30-min interview: intro=3, candidate_questions=4, closing=2 => max 21 min for questions
      - 60-min interview: intro=5, candidate_questions=7, closing=3 => max 45 min for questions
    """
    if duration_minutes < 15 or duration_minutes > 240:
        raise ProblemException(
            status_code=422,
            title="Invalid Interview Duration",
            code="VALIDATION_ERROR",
            detail="Interview duration must be between 15 and 240 minutes.",
        )

    if duration_minutes <= 30:
        intro = 3
        candidate_qa = 4
        closing = 2
    elif duration_minutes <= 45:
        intro = 4
        candidate_qa = 5
        closing = 3
    else:
        intro = 5
        candidate_qa = 7
        closing = 3

    max_question_budget = duration_minutes - (intro + candidate_qa + closing)
    if question_minutes_sum <= 0 or question_minutes_sum > max_question_budget:
        raise ProblemException(
            status_code=422,
            title="Interview Time Allocation Exceeded",
            code="VALIDATION_ERROR",
            detail=(
                f"Allocated question time ({question_minutes_sum} min) plus reserved "
                f"intro/Q&A/closing ({intro + candidate_qa + closing} min) exceeds "
                f"the {duration_minutes}-minute interview duration (max question budget: "
                f"{max_question_budget} min)."
            ),
        )

    return {
        "duration_minutes": duration_minutes,
        "intro_minutes": intro,
        "questions_minutes": question_minutes_sum,
        "max_questions_budget_minutes": max_question_budget,
        "candidate_questions_minutes": candidate_qa,
        "closing_minutes": closing,
        "buffer_minutes": max_question_budget - question_minutes_sum,
        "total_allocated_minutes": intro + question_minutes_sum + candidate_qa + closing,
    }


def validate_hypothetical_question_framing(prompt_text: str, premise_text: str | None) -> None:
    """Ensures hypothetical/scenario questions do not falsely imply the candidate did that work."""
    combined = f"{prompt_text} {premise_text or ''}"
    match = _IMPLIED_PAST_WORK_IN_HYPOTHETICAL_RE.search(combined)
    if match:
        raise ProblemException(
            status_code=422,
            title="Invalid Hypothetical Framing",
            code="EVIDENCE_INVALID",
            detail=(
                f"Hypothetical question must not imply the candidate previously performed "
                f"unverified work ('{match.group(0)}'). Cite source evidence or frame as a scenario."
            ),
        )


# ============================================================================
# SQLAlchemy Persistence Models
# ============================================================================


class CompetencyFramework(Base):
    """Versioned competency framework scoped to a tenant and optional job/stage."""

    __tablename__ = "competency_framework"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    seniority_band: Mapped[str] = mapped_column(String(50), nullable=False, default="MID_SENIOR")
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=FrameworkStatus.DRAFT.value
    )
    latest_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latest_published_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    latest_published_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_competency_framework"),
        UniqueConstraint("tenant_id", "id", name="uq_competency_framework_tenant_id"),
        UniqueConstraint("tenant_id", "slug", name="uq_competency_framework_slug"),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')",
            name="ck_competency_framework_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_framework_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_competency_framework_job_stage", "tenant_id", "job_id", "stage"),
    )


class CompetencyFrameworkVersion(Base):
    """Immutable-once-published competency framework version.

    Later framework revisions never retroactively mutate rubrics already bound
    to published interview packs or submitted interviewer feedback.
    """

    __tablename__ = "competency_framework_version"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    framework_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=FrameworkVersionStatus.DRAFT.value
    )
    summary: Mapped[str] = mapped_column(Text(), nullable=False)
    behavioral_scale_guide: Mapped[str] = mapped_column(Text(), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    published_by: Mapped[UUID | None] = mapped_column(nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_competency_framework_version"),
        UniqueConstraint("tenant_id", "id", name="uq_competency_framework_version_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "framework_id",
            "version_number",
            name="uq_competency_framework_version_num",
        ),
        CheckConstraint("version_number >= 1", name="ck_competency_framework_version_pos"),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED')",
            name="ck_competency_framework_version_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_framework_version_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "framework_id"],
            ["competency_framework.tenant_id", "competency_framework.id"],
            name="fk_competency_framework_version_parent",
            ondelete="CASCADE",
        ),
    )


class Competency(Base):
    """Single observable competency with 1-4 behavioral anchors and NOT_ASSESSED."""

    __tablename__ = "competency"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    framework_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[str] = mapped_column(Text(), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observation_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rubric_anchors: Mapped[dict[str, str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_competency"),
        UniqueConstraint("tenant_id", "id", name="uq_competency_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "framework_version_id",
            "code",
            name="uq_competency_version_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "framework_version_id"],
            ["competency_framework_version.tenant_id", "competency_framework_version.id"],
            name="fk_competency_framework_version",
            ondelete="CASCADE",
        ),
    )


class InterviewPackContext(Base):
    """Pinned booked-interview context seam owned by P15 (`interview_packs`).

    Decouples P15-P17 from direct ORM imports of P11-P14 scheduling modules while
    preserving exact interview IDs, participant scopes, durations, schedule revisions,
    and idempotent booked-event keys. When P14's `interview` table is also present,
    `InterviewContextPort` resolves and syncs seamlessly across both.
    """

    __tablename__ = "interview_pack_context"

    id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    seniority_level: Mapped[str] = mapped_column(String(50), nullable=False, default="SENIOR")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=InterviewContextStatus.BOOKED.value
    )
    scheduled_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    participant_user_ids: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    processed_booking_keys: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_pack_context"),
        UniqueConstraint("tenant_id", "id", name="uq_interview_pack_context_tenant_id"),
        CheckConstraint(
            "duration_minutes >= 15 AND duration_minutes <= 240",
            name="ck_interview_pack_context_duration",
        ),
        CheckConstraint("schedule_revision >= 1", name="ck_interview_pack_context_sched_rev"),
        CheckConstraint(
            "status IN ('BOOKED', 'RESCHEDULED', 'COMPLETED', 'CANCELED')",
            name="ck_interview_pack_context_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_pack_context_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_interview_pack_context_app", "tenant_id", "application_id"),
    )


class PackRevision(Base):
    """Versioned interview preparation pack pinned to exact upstream input versions."""

    __tablename__ = "pack_revision"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PackRevisionStatus.DRAFT.value
    )

    # Pinned input snapshot versions
    requirement_version_id: Mapped[UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    screening_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    summary_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    framework_version_id: Mapped[UUID] = mapped_column(nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    seniority_level: Mapped[str] = mapped_column(String(50), nullable=False, default="SENIOR")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    generation_reason: Mapped[str] = mapped_column(String(200), nullable=False)
    based_on_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    model_or_generator_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default="deterministic-pack-v1"
    )

    # Structured pack sections (NO candidate score or hire/reject recommendation exists)
    candidate_overview: Mapped[str] = mapped_column(Text(), nullable=False)
    role_requirements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    candidate_evidence_summary: Mapped[str] = mapped_column(Text(), nullable=False)
    relevant_responsibilities: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    competency_mapping: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    interviewer_instructions: Mapped[str] = mapped_column(Text(), nullable=False)
    time_allocation: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    evaluation_rubric: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    notes_guidance: Mapped[str] = mapped_column(Text(), nullable=False)
    feedback_form_structure: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    quality_warnings: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    author_id: Mapped[UUID] = mapped_column(nullable=False)
    published_by: Mapped[UUID | None] = mapped_column(nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_revision"),
        UniqueConstraint("tenant_id", "id", name="uq_pack_revision_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "interview_id",
            "revision_number",
            name="uq_pack_revision_interview_rev",
        ),
        CheckConstraint("revision_number >= 1", name="ck_pack_revision_positive"),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED')",
            name="ck_pack_revision_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_revision_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "framework_version_id"],
            ["competency_framework_version.tenant_id", "competency_framework_version.id"],
            name="fk_pack_revision_framework_version",
            ondelete="RESTRICT",
        ),
        Index("ix_pack_revision_interview", "tenant_id", "interview_id", "revision_number"),
    )


class PackClaim(Base):
    """Atomic statement inside an interview pack with explicit provenance and citations."""

    __tablename__ = "pack_claim"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    pack_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    claim_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(50), nullable=False)
    content_scope: Mapped[str] = mapped_column(String(30), nullable=False)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    statement: Mapped[str] = mapped_column(Text(), nullable=False)
    fact_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    evidence_fragment_ids: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    source_note: Mapped[str | None] = mapped_column(Text(), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_claim"),
        UniqueConstraint(
            "tenant_id",
            "pack_revision_id",
            "claim_index",
            name="uq_pack_claim_index",
        ),
        CheckConstraint(
            "content_scope IN ('ROLE_GENERIC', 'CANDIDATE_SPECIFIC')",
            name="ck_pack_claim_scope",
        ),
        CheckConstraint(
            "classification IN ('RESUME_SUPPORTED', 'DERIVED_FROM_SUPPORTED_FACTS', "
            "'UNKNOWN', 'CONFLICT', 'HUMAN_ASSERTION_UNVERIFIED')",
            name="ck_pack_claim_classification",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_claim_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_pack_claim_revision",
            ondelete="CASCADE",
        ),
    )


class PackQuestion(Base):
    """Structured interview question mapped to a competency, timing, and premise provenance."""

    __tablename__ = "pack_question"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    pack_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    content_scope: Mapped[str] = mapped_column(String(30), nullable=False)
    premise_classification: Mapped[str] = mapped_column(String(50), nullable=False)
    competency_id: Mapped[UUID] = mapped_column(nullable=False)
    allocated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text(), nullable=False)
    premise_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    expected_signals: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    fact_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    evidence_fragment_ids: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_question"),
        UniqueConstraint("tenant_id", "id", name="uq_pack_question_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "pack_revision_id",
            "order_index",
            name="uq_pack_question_order",
        ),
        CheckConstraint("allocated_minutes >= 1", name="ck_pack_question_minutes_pos"),
        CheckConstraint(
            "category IN ('TECHNICAL', 'BEHAVIORAL', 'SCENARIO', 'PROJECT_SPECIFIC', 'CLARIFICATION')",
            name="ck_pack_question_category",
        ),
        CheckConstraint(
            "content_scope IN ('ROLE_GENERIC', 'CANDIDATE_SPECIFIC')",
            name="ck_pack_question_scope",
        ),
        CheckConstraint(
            "premise_classification IN ('ROLE_GENERIC', 'RESUME_SUPPORTED', 'HYPOTHETICAL', "
            "'CLARIFICATION_NEEDED', 'HUMAN_ASSERTION_UNVERIFIED')",
            name="ck_pack_question_premise_class",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_question_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_pack_question_revision",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "competency_id"],
            ["competency.tenant_id", "competency.id"],
            name="fk_pack_question_competency",
            ondelete="RESTRICT",
        ),
    )


class PackReview(Base):
    """Append-only human review and publication record for an interview pack revision."""

    __tablename__ = "pack_review"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    pack_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    reviewer_id: Mapped[UUID] = mapped_column(nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_review"),
        CheckConstraint(
            "decision IN ('APPROVED_FOR_PUBLISH', 'CHANGES_REQUESTED')",
            name="ck_pack_review_decision",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_review_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_pack_review_revision",
            ondelete="CASCADE",
        ),
    )
