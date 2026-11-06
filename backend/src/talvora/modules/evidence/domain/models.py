"""Domain Models for Bounded Document Extraction and Immutable Evidence (P07).

An `ExtractionRun` is one immutable attempt to extract source-addressable text from
a single, already-clean `DocumentVersion` (see `talvora.modules.documents`). A
document version may accumulate several extraction runs over time -- each one a new
`revision_number` -- but a run itself, and everything it produces, is never mutated
once it reaches a terminal status: changing the extractor or normalization pipeline
always creates a new revision rather than rewriting a published one. This is what
"do not mutate text beneath existing citations" means in practice: a
`SourceSegment`/`EvidenceFragment` row's `text`/`quote` is written exactly once, at
insert time, inside the same transaction as the run's terminal status.

A `SourceSegment` is one page (PDF) or one paragraph/table-cell (DOCX) worth of
already-normalized text, addressed by a stable locator (`page_number` for PDF,
`docx_path` for DOCX -- never both, and a DOCX segment's `page_number` is always
NULL because DOCX has no stable page layout in this phase). An `EvidenceFragment` is
a bounded, contiguous, hash-and-substring-verified excerpt of exactly one segment's
text -- the smallest unit a later citation (P08+) or a human reviewer can point at.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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


# ============================================================================
# Enums
# ============================================================================


class ExtractionRunStatus(StrEnum):
    """Lifecycle of one extraction attempt. Distinct from document storage_state."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


# Terminal states from which no further work occurs for this specific run.
EXTRACTION_RUN_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        ExtractionRunStatus.SUCCEEDED.value,
        ExtractionRunStatus.PARTIAL.value,
        ExtractionRunStatus.NEEDS_REVIEW.value,
        ExtractionRunStatus.FAILED.value,
        ExtractionRunStatus.CANCELED.value,
    }
)

# A run in one of these states produced source-addressable text usable for citation.
EXTRACTION_RUN_READABLE_STATUSES: frozenset[str] = frozenset(
    {ExtractionRunStatus.SUCCEEDED.value, ExtractionRunStatus.PARTIAL.value}
)


class ExtractionTriggerKind(StrEnum):
    """What caused this extraction run to be admitted."""

    SYSTEM_SCAN_CLEAN = "SYSTEM_SCAN_CLEAN"
    CONTENT_REUSE = "CONTENT_REUSE"
    MANUAL_REPROCESS = "MANUAL_REPROCESS"


class SourceLocatorType(StrEnum):
    """Mirrors `evidence.application.ports.SourceLocatorType` for persistence."""

    PDF_PAGE = "PDF_PAGE"
    DOCX_BODY_PARAGRAPH = "DOCX_BODY_PARAGRAPH"
    DOCX_TABLE_CELL = "DOCX_TABLE_CELL"
    DOCX_HEADER = "DOCX_HEADER"
    DOCX_FOOTER = "DOCX_FOOTER"


class SegmentExtractionMethod(StrEnum):
    """Mirrors `evidence.application.ports.SegmentExtractionMethod` for persistence."""

    DIGITAL_TEXT = "DIGITAL_TEXT"
    OCR = "OCR"
    DOCX_STRUCTURED = "DOCX_STRUCTURED"


# ============================================================================
# SQLAlchemy Declarative Models
# ============================================================================


class ExtractionRun(Base):
    """One immutable, versioned attempt to extract text from a clean document version."""

    __tablename__ = "extraction_run"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)

    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(50), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ExtractionRunStatus.PENDING.value
    )
    problem_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_complete: Mapped[bool] = mapped_column(nullable=False, default=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)

    total_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_pages_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_characters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    triggered_by: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_by_actor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    job_operation_id: Mapped[UUID | None] = mapped_column(nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_extraction_run"),
        UniqueConstraint("tenant_id", "id", name="uq_extraction_run_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "document_version_id",
            "revision_number",
            name="uq_extraction_run_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_extraction_run_tenant", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["document.tenant_id", "document.id"],
            name="fk_extraction_run_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_extraction_run_document_version",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_extraction_run_tenant_version",
            "tenant_id",
            "document_version_id",
            "revision_number",
        ),
    )


class SourceSegment(Base):
    """One immutable, normalized unit of source text produced by one extraction run."""

    __tablename__ = "source_segment"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    extraction_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)

    locator_type: Mapped[str] = mapped_column(String(50), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    docx_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    extraction_method: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    is_low_confidence: Mapped[bool] = mapped_column(nullable=False, default=False)
    ocr_mean_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_source_segment"),
        UniqueConstraint("tenant_id", "id", name="uq_source_segment_tenant_id"),
        UniqueConstraint(
            "tenant_id", "extraction_run_id", "segment_index", name="uq_source_segment_index"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_source_segment_tenant", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_run_id"],
            ["extraction_run.tenant_id", "extraction_run.id"],
            name="fk_source_segment_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_source_segment_document_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("char_count >= 0", name="ck_source_segment_char_count_nonneg"),
        Index("ix_source_segment_tenant_run", "tenant_id", "extraction_run_id"),
        Index("ix_source_segment_tenant_docversion", "tenant_id", "document_version_id"),
    )


class EvidenceFragment(Base):
    """A bounded, contiguous, hash-and-substring-verified excerpt of one segment."""

    __tablename__ = "evidence_fragment"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_segment_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    extraction_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text(), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_evidence_fragment"),
        UniqueConstraint("tenant_id", "id", name="uq_evidence_fragment_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_segment_id",
            "start_offset",
            "end_offset",
            name="uq_evidence_fragment_span",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_evidence_fragment_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_segment_id"],
            ["source_segment.tenant_id", "source_segment.id"],
            name="fk_evidence_fragment_segment",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_run_id"],
            ["extraction_run.tenant_id", "extraction_run.id"],
            name="fk_evidence_fragment_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_evidence_fragment_document_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("start_offset >= 0", name="ck_evidence_fragment_start_nonneg"),
        CheckConstraint("end_offset > start_offset", name="ck_evidence_fragment_end_after_start"),
        Index("ix_evidence_fragment_tenant_segment", "tenant_id", "source_segment_id"),
        Index("ix_evidence_fragment_tenant_docversion", "tenant_id", "document_version_id"),
    )


__all__ = [
    "EXTRACTION_RUN_READABLE_STATUSES",
    "EXTRACTION_RUN_TERMINAL_STATUSES",
    "EvidenceFragment",
    "ExtractionRun",
    "ExtractionRunStatus",
    "ExtractionTriggerKind",
    "SegmentExtractionMethod",
    "SourceLocatorType",
    "SourceSegment",
]
