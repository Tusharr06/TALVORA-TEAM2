"""Versioned summary outputs, claims, and evidence citations."""

from datetime import UTC, datetime
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
from sqlalchemy.types import JSON

from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CandidateSummaryRevision(Base):
    """One immutable accepted or rejected evidence-summary generation attempt."""

    __tablename__ = "candidate_summary_revision"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    screening_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(30), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text(), nullable=False)
    quality_issues: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_summary_revision"),
        CheckConstraint(
            "quality_status IN ('ACCEPTED', 'REJECTED')",
            name="ck_candidate_summary_quality_status",
        ),
        CheckConstraint("revision_number >= 1", name="ck_candidate_summary_revision_positive"),
        UniqueConstraint(
            "tenant_id",
            "application_id",
            "revision_number",
            name="uq_candidate_summary_revision_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_summary_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_candidate_summary_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "screening_run_id"],
            ["screening_run.tenant_id", "screening_run.id"],
            name="fk_candidate_summary_screening",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_candidate_summary_application",
            "tenant_id",
            "application_id",
            "revision_number",
        ),
    )


class CandidateSummaryClaim(Base):
    """One factual claim in an accepted summary version."""

    __tablename__ = "candidate_summary_claim"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    summary_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    claim_index: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_summary_claim"),
        UniqueConstraint(
            "tenant_id",
            "summary_revision_id",
            "claim_index",
            name="uq_candidate_summary_claim_index",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_summary_claim_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "summary_revision_id"],
            ["candidate_summary_revision.tenant_id", "candidate_summary_revision.id"],
            name="fk_candidate_summary_claim_revision",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "fact_revision_id"],
            ["candidate_fact_revision.tenant_id", "candidate_fact_revision.id"],
            name="fk_candidate_summary_claim_fact",
            ondelete="RESTRICT",
        ),
    )


class CandidateSummaryCitation(Base):
    """Evidence citation for one accepted factual summary claim."""

    __tablename__ = "candidate_summary_citation"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    summary_claim_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    evidence_fragment_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_summary_citation"),
        UniqueConstraint(
            "tenant_id",
            "summary_claim_id",
            "evidence_fragment_id",
            name="uq_candidate_summary_citation",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_summary_citation_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "summary_claim_id"],
            ["candidate_summary_claim.tenant_id", "candidate_summary_claim.id"],
            name="fk_candidate_summary_citation_claim",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "evidence_fragment_id"],
            ["evidence_fragment.tenant_id", "evidence_fragment.id"],
            name="fk_candidate_summary_citation_fragment",
            ondelete="RESTRICT",
        ),
    )
