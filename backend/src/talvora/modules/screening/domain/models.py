"""Immutable screening decisions, criterion results, and recruiter overrides."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ScreeningOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ScreeningRun(Base):
    """Snapshot of deterministic screening against one published requirement version."""

    __tablename__ = "screening_run"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_document_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    document_version_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    requirement_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_run"),
        CheckConstraint(
            "outcome IN ('PASS', 'FAIL', 'UNKNOWN')", name="ck_screening_run_outcome"
        ),
        CheckConstraint("length(trim(rule_version)) > 0", name="ck_screening_run_rule_version"),
        CheckConstraint(
            "(application_document_id IS NULL) = (document_version_id IS NULL)",
            name="ck_screening_run_binding_pair",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_screening_run_tenant", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_screening_run_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_screening_run_candidate",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_document_id"],
            ["application_document.tenant_id", "application_document.id"],
            name="fk_screening_run_application_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_screening_run_document_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requirement_version_id"],
            ["requirement_version.tenant_id", "requirement_version.id"],
            name="fk_screening_run_requirement_version",
            ondelete="RESTRICT",
        ),
        Index("ix_screening_run_application", "tenant_id", "application_id", "created_at"),
    )


class ScreeningResult(Base):
    """One deterministic result and explanation for a criterion in a screening run."""

    __tablename__ = "screening_result"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    screening_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    criterion_id: Mapped[UUID] = mapped_column(nullable=False)
    criterion_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_required: Mapped[bool] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    explanation: Mapped[str] = mapped_column(Text(), nullable=False)
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_result"),
        CheckConstraint(
            "outcome IN ('PASS', 'FAIL', 'UNKNOWN')", name="ck_screening_result_outcome"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_screening_result_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_screening_result_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "screening_run_id"],
            ["screening_run.tenant_id", "screening_run.id"],
            name="fk_screening_result_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "criterion_id"],
            ["criterion.tenant_id", "criterion.id"],
            name="fk_screening_result_criterion",
            ondelete="RESTRICT",
        ),
        Index("ix_screening_result_run", "tenant_id", "screening_run_id"),
    )


class ScreeningResultFact(Base):
    """Fact revisions that directly support one criterion result."""

    __tablename__ = "screening_result_fact"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    screening_result_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    fact_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_result_fact"),
        UniqueConstraint(
            "tenant_id",
            "screening_result_id",
            "fact_revision_id",
            name="uq_screening_result_fact",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_screening_result_fact_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "screening_result_id"],
            ["screening_result.tenant_id", "screening_result.id"],
            name="fk_screening_result_fact_result",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "fact_revision_id"],
            ["candidate_fact_revision.tenant_id", "candidate_fact_revision.id"],
            name="fk_screening_result_fact_fact",
            ondelete="RESTRICT",
        ),
    )


class ScreeningOverride(Base):
    """Append-only recruiter decision override; never mutates its screening run."""

    __tablename__ = "screening_override"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    screening_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_override"),
        CheckConstraint(
            "outcome IN ('PASS', 'FAIL', 'UNKNOWN')", name="ck_screening_override_outcome"
        ),
        CheckConstraint("revision_number >= 1", name="ck_screening_override_revision_positive"),
        CheckConstraint("length(trim(reason)) > 0", name="ck_screening_override_reason_nonempty"),
        UniqueConstraint(
            "tenant_id",
            "screening_run_id",
            "revision_number",
            name="uq_screening_override_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_screening_override_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "screening_run_id"],
            ["screening_run.tenant_id", "screening_run.id"],
            name="fk_screening_override_run",
            ondelete="CASCADE",
        ),
        Index("ix_screening_override_run", "tenant_id", "screening_run_id", "created_at"),
    )
