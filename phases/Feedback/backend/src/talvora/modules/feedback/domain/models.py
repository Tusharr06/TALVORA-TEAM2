"""Domain Models and Pure Validators for Structured Feedback and Controlled Exports (P16).

Enforces:
- One draft per interviewer/interview; immutable append-only submitted revisions.
- Rubric ratings strictly in {'1', '2', '3', '4', 'NOT_ASSESSED'} (missing != zero).
- Human-authored observations distinct from resume claims; no LLM grading or hiring verdicts.
- Blind-feedback server-side visibility rules and 24-hour private export expiry.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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

from talvora.modules.jobs.domain.models import validate_no_protected_traits
from talvora.shared.infrastructure.models import Base
from talvora.shared.transport.problems import ProblemException


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FeedbackStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    AMENDED = "AMENDED"


class HumanRecommendation(StrEnum):
    """Optional human-authored discussion stance; never auto-applied to pipeline stage."""

    ADVANCE_DISCUSSION = "ADVANCE_DISCUSSION"
    HOLD_NEEDS_MORE_SIGNAL = "HOLD_NEEDS_MORE_SIGNAL"
    DO_NOT_ADVANCE = "DO_NOT_ADVANCE"
    NONE = "NONE"


ALLOWED_RATING_VALUES: frozenset[str] = frozenset({"1", "2", "3", "4", "NOT_ASSESSED"})


class ExportFormat(StrEnum):
    DOCX = "DOCX"
    PDF = "PDF"


class ExportContentScope(StrEnum):
    INTERVIEWER_PACK = "INTERVIEWER_PACK"
    REVIEWER_PACK_WITH_FEEDBACK = "REVIEWER_PACK_WITH_FEEDBACK"


class ExportStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


EXPORT_REVOCATION_DISCLOSURE = (
    "CONFIDENTIAL: Downloaded or printed copies are outside platform revocation control. "
    "Do not forward or retain beyond active authorized recruitment review."
)

_EXTERNAL_RESOURCE_INJECTION_RE = re.compile(
    r"("
    r"<\s*(script|iframe|object|embed|link|img|meta|svg|style)\b|"
    r"@import\b|"
    r"url\s*\(\s*['\"]?\s*(https?|file|ftp|gopher|data|javascript):|"
    r"\b(file|gopher|javascript)://|"
    r"^\s*[=+\-@]\s*(cmd|powershell|hyperlink|importxml|webservice)\b"
    r")",
    re.IGNORECASE,
)

_XML_ILLEGAL_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_rating_value(rating: str) -> str:
    """Ensures ratings use rubric anchors 1-4 or NOT_ASSESSED; numeric 0 is rejected."""
    cleaned = rating.strip().upper()
    if cleaned not in ALLOWED_RATING_VALUES:
        raise ProblemException(
            status_code=422,
            title="Invalid Competency Rating",
            code="VALIDATION_ERROR",
            detail=(
                f"Rating '{rating}' is invalid. Allowed values are 1, 2, 3, 4, or NOT_ASSESSED "
                "(unassessed competencies must never be encoded as 0)."
            ),
        )
    return cleaned


def validate_interview_observation_text(text: str, field_label: str) -> str:
    """Validates human interview observations, blocking resource-fetch injection and protected traits."""
    cleaned = _XML_ILLEGAL_CONTROL_CHARS_RE.sub("", text).strip()
    if _EXTERNAL_RESOURCE_INJECTION_RE.search(cleaned):
        raise ProblemException(
            status_code=422,
            title="Unsafe Content Rejected",
            code="VALIDATION_ERROR",
            detail=f"External URL/file/script/formula injection pattern detected in {field_label}.",
        )
    validate_no_protected_traits(cleaned, context_label=field_label)
    return cleaned


def assert_safe_for_renderer(text: str) -> str:
    """Final defense-in-depth check inside the DOCX/PDF renderer against URL/file-fetch payloads."""
    cleaned = _XML_ILLEGAL_CONTROL_CHARS_RE.sub("", text or "")
    if _EXTERNAL_RESOURCE_INJECTION_RE.search(cleaned):
        raise ProblemException(
            status_code=422,
            title="Unsafe Export Content",
            code="UNSAFE_DOCUMENT",
            detail="Renderer blocked external URL, file-fetch, script, or formula injection payload.",
        )
    # Neutralize spreadsheet/Word leading formula triggers if present
    if cleaned.startswith(("=", "+", "@")) and len(cleaned) > 1:
        cleaned = "'" + cleaned
    return cleaned


# ============================================================================
# SQLAlchemy Persistence Models
# ============================================================================


class Feedback(Base):
    """Aggregate owning one interviewer's draft and submitted revision history for an interview."""

    __tablename__ = "feedback"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interviewer_user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    pack_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    framework_version_id: Mapped[UUID] = mapped_column(nullable=False)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=FeedbackStatus.DRAFT.value
    )
    draft_overall_observations: Mapped[str | None] = mapped_column(Text(), nullable=True)
    draft_human_recommendation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    draft_ratings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )

    latest_submitted_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_feedback"),
        UniqueConstraint("tenant_id", "id", name="uq_feedback_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "interview_id",
            "interviewer_user_id",
            name="uq_feedback_interview_interviewer",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'AMENDED')",
            name="ck_feedback_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_feedback_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_feedback_interview", "tenant_id", "interview_id"),
    )


class FeedbackRevision(Base):
    """Immutable append-only submission or authorized amendment of interviewer feedback."""

    __tablename__ = "feedback_revision"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    feedback_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pack_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    framework_version_id: Mapped[UUID] = mapped_column(nullable=False)

    overall_observations: Mapped[str] = mapped_column(Text(), nullable=False)
    human_recommendation: Mapped[str] = mapped_column(
        String(40), nullable=False, default=HumanRecommendation.NONE.value
    )
    amendment_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    prior_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    is_late_submission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    submitted_by: Mapped[UUID] = mapped_column(nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_feedback_revision"),
        UniqueConstraint("tenant_id", "id", name="uq_feedback_revision_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "feedback_id",
            "revision_number",
            name="uq_feedback_revision_num",
        ),
        CheckConstraint("revision_number >= 1", name="ck_feedback_revision_pos"),
        CheckConstraint(
            "human_recommendation IN ('ADVANCE_DISCUSSION', 'HOLD_NEEDS_MORE_SIGNAL', "
            "'DO_NOT_ADVANCE', 'NONE')",
            name="ck_feedback_revision_recommendation",
        ),
        CheckConstraint(
            "(revision_number = 1) OR (amendment_reason IS NOT NULL AND length(trim(amendment_reason)) > 0)",
            name="ck_feedback_amendment_reason_required",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_feedback_revision_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "feedback_id"],
            ["feedback.tenant_id", "feedback.id"],
            name="fk_feedback_revision_parent",
            ondelete="CASCADE",
        ),
    )


class CompetencyRating(Base):
    """Per-competency rating (1-4 or NOT_ASSESSED) and human interview observation."""

    __tablename__ = "competency_rating"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    feedback_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    competency_id: Mapped[UUID] = mapped_column(nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    observation: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    question_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_competency_rating"),
        UniqueConstraint(
            "tenant_id",
            "feedback_revision_id",
            "competency_id",
            name="uq_competency_rating_rev_comp",
        ),
        CheckConstraint(
            "rating IN ('1', '2', '3', '4', 'NOT_ASSESSED')",
            name="ck_competency_rating_scale",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_rating_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "feedback_revision_id"],
            ["feedback_revision.tenant_id", "feedback_revision.id"],
            name="fk_competency_rating_revision",
            ondelete="CASCADE",
        ),
    )


class ExportArtifact(Base):
    """Private, checksummed, time-limited DOCX/PDF export artifact."""

    __tablename__ = "export_artifact"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    pack_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    interview_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    format: Mapped[str] = mapped_column(String(10), nullable=False)
    content_scope: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExportStatus.READY.value
    )
    operation_id: Mapped[UUID | None] = mapped_column(nullable=True)

    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)

    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_export_artifact"),
        UniqueConstraint("tenant_id", "id", name="uq_export_artifact_tenant_id"),
        CheckConstraint("format IN ('DOCX', 'PDF')", name="ck_export_artifact_format"),
        CheckConstraint(
            "content_scope IN ('INTERVIEWER_PACK', 'REVIEWER_PACK_WITH_FEEDBACK')",
            name="ck_export_artifact_scope",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'READY', 'FAILED', 'REVOKED', 'EXPIRED')",
            name="ck_export_artifact_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_export_artifact_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_export_artifact_candidate", "tenant_id", "candidate_id"),
    )
