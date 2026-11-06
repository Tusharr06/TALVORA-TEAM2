"""Immutable candidate fact revisions and their source evidence links."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CandidateFactType(StrEnum):
    """Supported screening-relevant fact categories."""

    SKILL = "SKILL"
    EXPERIENCE_MONTHS = "EXPERIENCE_MONTHS"
    EDUCATION_LEVEL = "EDUCATION_LEVEL"
    CERTIFICATION = "CERTIFICATION"
    OTHER = "OTHER"


class CandidateFactStatus(StrEnum):
    """Review state of one immutable fact revision."""

    EXTRACTED = "EXTRACTED"
    VERIFIED = "VERIFIED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"


class CandidateFactPolarity(StrEnum):
    """Whether a structured fact affirms, negates, or cannot establish a claim."""

    AFFIRMED = "AFFIRMED"
    NEGATED = "NEGATED"
    UNKNOWN = "UNKNOWN"


class CandidateFactRevision(Base):
    """One immutable candidate fact revision; corrections append, never overwrite."""

    __tablename__ = "candidate_fact_revision"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    fact_key: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[str] = mapped_column(Text(), nullable=False)
    extracted_value: Mapped[str] = mapped_column(Text(), nullable=False)
    polarity: Mapped[str] = mapped_column(String(20), nullable=False)
    extracted_polarity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    extraction_run_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_fact_revision"),
        CheckConstraint(
            "fact_type IN ('SKILL', 'EXPERIENCE_MONTHS', 'EDUCATION_LEVEL', "
            "'CERTIFICATION', 'OTHER')",
            name="ck_candidate_fact_type",
        ),
        CheckConstraint(
            "status IN ('EXTRACTED', 'VERIFIED', 'CORRECTED', 'REJECTED')",
            name="ck_candidate_fact_status",
        ),
        CheckConstraint(
            "polarity IN ('AFFIRMED', 'NEGATED', 'UNKNOWN')",
            name="ck_candidate_fact_polarity",
        ),
        CheckConstraint(
            "extracted_polarity IN ('AFFIRMED', 'NEGATED', 'UNKNOWN')",
            name="ck_candidate_fact_extracted_polarity",
        ),
        CheckConstraint("revision_number >= 1", name="ck_candidate_fact_revision_positive"),
        CheckConstraint("length(trim(value)) > 0", name="ck_candidate_fact_value_nonempty"),
        UniqueConstraint(
            "tenant_id",
            "candidate_id",
            "fact_key",
            "revision_number",
            name="uq_candidate_fact_revision_number",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_candidate_fact_revision_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_fact_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_fact_candidate",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_run_id"],
            ["extraction_run.tenant_id", "extraction_run.id"],
            name="fk_candidate_fact_extraction_run",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_candidate_fact_latest",
            "tenant_id",
            "candidate_id",
            "fact_key",
            "revision_number",
        ),
    )


class CandidateFactEvidence(Base):
    """Traceable association between one fact revision and its source excerpt."""

    __tablename__ = "candidate_fact_evidence"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    fact_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    evidence_fragment_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_fact_evidence"),
        UniqueConstraint(
            "tenant_id",
            "fact_revision_id",
            "evidence_fragment_id",
            name="uq_candidate_fact_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_fact_evidence_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "fact_revision_id"],
            ["candidate_fact_revision.tenant_id", "candidate_fact_revision.id"],
            name="fk_candidate_fact_evidence_fact",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "evidence_fragment_id"],
            ["evidence_fragment.tenant_id", "evidence_fragment.id"],
            name="fk_candidate_fact_evidence_fragment",
            ondelete="RESTRICT",
        ),
    )


__all__ = [
    "CandidateFactEvidence",
    "CandidateFactPolarity",
    "CandidateFactRevision",
    "CandidateFactStatus",
    "CandidateFactType",
]
