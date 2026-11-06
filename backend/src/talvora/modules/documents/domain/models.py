"""Domain Models for Secure Batch Resume Ingestion and Quarantine (P06).

A recruiter-initiated `UploadBatch` is linked to exactly one eligible open job. Each
`UploadItem` within the batch streams a single file and resolves to either an
explicitly selected existing candidate or an idempotently created draft
candidate/application for that item -- this module never infers or merges a candidate
identity on its own.

A `Document` is a tenant-local, candidate-scoped container whose binary content is
represented by one or more immutable `DocumentVersion` rows. A version never mutates
once its bytes are durably received: a replacement resume always creates a new version,
never an edit of an existing one. Binding a specific version to an application is a
separate, explicit, version-checked act recorded by `ApplicationDocument` -- so an
application's active resume is never silently swapped by a later upload.

Storage keys are opaque (`tenant_id/<uuid4>`) and never derived from a candidate's name,
email, or the client-declared filename, so a storage key alone never discloses PII. The
`ObjectReceipt` row records the durable *intent* to have written an object to the
quarantine bucket before the slower network write happens, so that a crash between the
S3 PUT succeeding and the owning database transaction committing never silently loses
track of retained bytes -- a documented reconciler (`application/reconciler.py`) sweeps
stale `PENDING` receipts and deletes the orphaned object.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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


# ============================================================================
# Enums
# ============================================================================


class BatchStatus(StrEnum):
    """Lifecycle of an upload batch."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class UploadItemStatus(StrEnum):
    """Per-item processing lifecycle. Distinct from application pipeline stage."""

    RESERVED = "RESERVED"
    QUARANTINED = "QUARANTINED"
    SCANNING = "SCANNING"
    CLEAN = "CLEAN"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


# Terminal states from which no further processing occurs without an explicit retry.
UPLOAD_ITEM_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        UploadItemStatus.CLEAN.value,
        UploadItemStatus.BLOCKED.value,
        UploadItemStatus.CANCELED.value,
    }
)

# States from which a retry control is meaningful (processing stalled or failed, but the
# underlying object/reservation may still be recoverable).
UPLOAD_ITEM_RETRYABLE_STATUSES: frozenset[str] = frozenset({UploadItemStatus.FAILED.value})

# States from which a cancel control is permitted (not yet a final clean/blocked verdict).
UPLOAD_ITEM_CANCELABLE_STATUSES: frozenset[str] = frozenset(
    {
        UploadItemStatus.RESERVED.value,
        UploadItemStatus.QUARANTINED.value,
        UploadItemStatus.SCANNING.value,
        UploadItemStatus.FAILED.value,
    }
)


class DocumentVersionStorageState(StrEnum):
    """Storage-side lifecycle of one immutable document version's bytes."""

    PENDING_RECEIPT = "PENDING_RECEIPT"
    QUARANTINED = "QUARANTINED"
    CLEAN = "CLEAN"
    BLOCKED_INFECTED = "BLOCKED_INFECTED"
    BLOCKED_INVALID = "BLOCKED_INVALID"
    ORPHAN_PENDING_CLEANUP = "ORPHAN_PENDING_CLEANUP"


# A document version is safe to serve for download only in these states.
DOCUMENT_VERSION_DOWNLOADABLE_STATES: frozenset[str] = frozenset(
    {DocumentVersionStorageState.CLEAN.value}
)


class ObjectReceiptStatus(StrEnum):
    """Durable record of object-store write intent, bridging the S3/DB dual-write gap."""

    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    ORPHANED = "ORPHANED"
    CLEANED_UP = "CLEANED_UP"


class ApplicationDocumentStatus(StrEnum):
    """Lifecycle of a single application<->document-version binding record."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class IntakeProblemCode(StrEnum):
    """Safe, stable problem codes surfaced to clients for a failed/blocked item."""

    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    BATCH_ITEM_LIMIT_EXCEEDED = "BATCH_ITEM_LIMIT_EXCEEDED"
    BATCH_BYTE_LIMIT_EXCEEDED = "BATCH_BYTE_LIMIT_EXCEEDED"
    EMPTY_FILE = "EMPTY_FILE"
    SPOOFED_MEDIA_TYPE = "SPOOFED_MEDIA_TYPE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    MALFORMED_CONTAINER = "MALFORMED_CONTAINER"
    DECOMPRESSION_BOMB_SUSPECTED = "DECOMPRESSION_BOMB_SUSPECTED"
    ENCRYPTED_DOCUMENT = "ENCRYPTED_DOCUMENT"
    UNSAFE_CONTAINER_ENTRY = "UNSAFE_CONTAINER_ENTRY"
    MACRO_CONTENT_BLOCKED = "MACRO_CONTENT_BLOCKED"
    EXTERNAL_CONTENT_BLOCKED = "EXTERNAL_CONTENT_BLOCKED"
    EMBEDDED_EXECUTABLE_BLOCKED = "EMBEDDED_EXECUTABLE_BLOCKED"
    MALWARE_DETECTED = "MALWARE_DETECTED"
    SCANNER_UNAVAILABLE = "SCANNER_UNAVAILABLE"
    ADMISSION_FAILED = "ADMISSION_FAILED"
    UPLOAD_INTERRUPTED = "UPLOAD_INTERRUPTED"


# ============================================================================
# Filename / storage key helpers
# ============================================================================

_UNSAFE_FILENAME_RE = re.compile(r"[\x00-\x1f\x7f/\\]")


def sanitize_display_filename(raw: str | None) -> str | None:
    """Produce a safe, bounded display label from a client-declared filename.

    This value is for human display only -- it is never used to derive a storage path,
    and it is never trusted as evidence of the actual file type.
    """
    if not raw:
        return None
    cleaned = _UNSAFE_FILENAME_RE.sub("_", raw.strip())
    cleaned = cleaned.replace("..", "_")
    return cleaned[:255] or None


def generate_opaque_storage_key(tenant_id: UUID) -> str:
    """Generate an opaque object-store key carrying no candidate PII whatsoever."""
    return f"{tenant_id}/{uuid4().hex}"


# ============================================================================
# SQLAlchemy Declarative Models
# ============================================================================


class UploadBatch(Base):
    """A recruiter-initiated batch of resume uploads scoped to one eligible open job."""

    __tablename__ = "upload_batch"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default=BatchStatus.OPEN.value)
    max_items: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_file_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=10 * 1024 * 1024
    )
    max_total_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=500 * 1024 * 1024
    )

    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_by: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_upload_batch"),
        UniqueConstraint("tenant_id", "id", name="uq_upload_batch_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_upload_batch_tenant", ondelete="CASCADE"
        ),
        Index("ix_upload_batch_tenant_job", "tenant_id", "job_id"),
    )


class UploadItem(Base):
    """One file-level unit of work within an upload batch."""

    __tablename__ = "upload_item"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    batch_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=UploadItemStatus.RESERVED.value
    )

    candidate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    application_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_draft_candidate: Mapped[bool] = mapped_column(nullable=False, default=False)

    declared_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    declared_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    document_id: Mapped[UUID | None] = mapped_column(nullable=True)
    document_version_id: Mapped[UUID | None] = mapped_column(nullable=True)

    problem_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    problem_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(100), nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_by: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_upload_item"),
        UniqueConstraint("tenant_id", "id", name="uq_upload_item_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_upload_item_tenant", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            ["upload_batch.tenant_id", "upload_batch.id"],
            name="fk_upload_item_batch",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_upload_item_candidate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_upload_item_application",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "batch_id", "sequence_number", name="uq_upload_item_sequence"
        ),
        Index("ix_upload_item_tenant_batch", "tenant_id", "batch_id"),
        Index("ix_upload_item_tenant_idem", "tenant_id", "batch_id", "idempotency_key"),
    )


class Document(Base):
    """Tenant-local, candidate-scoped container for immutable resume versions."""

    __tablename__ = "document"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    latest_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_document"),
        UniqueConstraint("tenant_id", "id", name="uq_document_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_document_tenant", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_document_candidate",
            ondelete="RESTRICT",
        ),
        Index("ix_document_tenant_candidate", "tenant_id", "candidate_id"),
    )


class DocumentVersion(Base):
    """Immutable binary resume version. Never mutated once bytes are durably received."""

    __tablename__ = "document_version"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)

    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_state: Mapped[str] = mapped_column(
        String(50), nullable=False, default=DocumentVersionStorageState.PENDING_RECEIPT.value
    )

    scan_result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    source_upload_item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_actor_id: Mapped[UUID] = mapped_column(nullable=False)
    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_document_version"),
        UniqueConstraint("tenant_id", "id", name="uq_document_version_tenant_id"),
        UniqueConstraint(
            "tenant_id", "document_id", "version_number", name="uq_document_version_number"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_document_version_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["document.tenant_id", "document.id"],
            name="fk_document_version_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_upload_item_id"],
            ["upload_item.tenant_id", "upload_item.id"],
            name="fk_document_version_upload_item",
            ondelete="SET NULL",
        ),
        Index("ix_document_version_tenant_document", "tenant_id", "document_id"),
        Index("ix_document_version_tenant_checksum", "tenant_id", "sha256_checksum"),
    )


class ObjectReceipt(Base):
    """Durable record that an object write to the quarantine bucket was intended/attempted.

    Written in its own fast transaction *before* the slower network upload, so that a
    crash between a successful S3 PUT and the owning DB transaction's commit never
    silently loses track of retained bytes. The reconciler sweeps receipts that remain
    `PENDING` past a grace period with no corresponding committed `DocumentVersion`.
    """

    __tablename__ = "object_receipt"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    upload_item_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ObjectReceiptStatus.PENDING.value
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_object_receipt"),
        ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_object_receipt_tenant", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "upload_item_id"],
            ["upload_item.tenant_id", "upload_item.id"],
            name="fk_object_receipt_upload_item",
            ondelete="CASCADE",
        ),
        Index("ix_object_receipt_tenant_status_created", "tenant_id", "status", "created_at"),
    )


class ApplicationDocument(Base):
    """Explicit, version-checked binding of one document version to an application.

    Append-only: binding a replacement version creates a new row with the next
    `binding_generation` and marks the prior active row `SUPERSEDED`. Superseded rows
    remain auditable until retention deletion; they are never deleted on rebind.
    """

    __tablename__ = "application_document"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    binding_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ApplicationDocumentStatus.ACTIVE.value
    )

    bound_by: Mapped[UUID] = mapped_column(nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_application_document"),
        UniqueConstraint(
            "tenant_id",
            "application_id",
            "binding_generation",
            name="uq_application_document_generation",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_application_document_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_application_document_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["document.tenant_id", "document.id"],
            name="fk_application_document_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_application_document_version",
            ondelete="RESTRICT",
        ),
        Index("ix_application_document_tenant_app", "tenant_id", "application_id", "status"),
    )


__all__ = [
    "ApplicationDocument",
    "ApplicationDocumentStatus",
    "BatchStatus",
    "Document",
    "DocumentVersion",
    "DocumentVersionStorageState",
    "DOCUMENT_VERSION_DOWNLOADABLE_STATES",
    "IntakeProblemCode",
    "ObjectReceipt",
    "ObjectReceiptStatus",
    "UPLOAD_ITEM_CANCELABLE_STATUSES",
    "UPLOAD_ITEM_RETRYABLE_STATUSES",
    "UPLOAD_ITEM_TERMINAL_STATUSES",
    "UploadBatch",
    "UploadItem",
    "UploadItemStatus",
    "generate_opaque_storage_key",
    "sanitize_display_filename",
]
