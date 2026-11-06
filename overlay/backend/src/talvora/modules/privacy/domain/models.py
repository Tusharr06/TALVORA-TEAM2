"""Domain Models for Organization Policy, Retention Holds, Privacy Requests & Deletion Ledger (P17)."""

import hashlib
import hmac
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
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
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.facts.domain.models import CandidateFactRevision
from talvora.modules.interview_packs.domain.models import PackRevision
from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


RETENTION_JURISDICTION_DISCLOSURE = (
    "Retention windows are configurable operational defaults and must be reviewed by legal "
    "counsel for each deployment jurisdiction prior to production onboarding."
)

RESIDUAL_EXTERNAL_ARTIFACTS_DISCLOSURE = (
    "Delivered external emails and previously downloaded/printed offline exports cannot be "
    "recalled by platform purge; external calendar events require separate authorized "
    "cancellation or redaction review."
)


class HoldScope(StrEnum):
    CANDIDATE = "CANDIDATE"
    APPLICATION = "APPLICATION"


class HoldStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class PrivacyRequestType(StrEnum):
    ERASURE = "ERASURE"
    EXPORT_PORTABILITY = "EXPORT_PORTABILITY"
    RETENTION_SWEEP = "RETENTION_SWEEP"


class PrivacyVerificationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    HUMAN_CONTACT_VERIFIED = "HUMAN_CONTACT_VERIFIED"
    HUMAN_IDENTITY_ATTESTED = "HUMAN_IDENTITY_ATTESTED"
    INTERNAL_RETENTION_POLICY = "INTERNAL_RETENTION_POLICY"


class PrivacyRequestStatus(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"


class CandidateTombstonedError(RuntimeError):
    """Raised when a stale worker or command attempts to resurrect data for a purged candidate."""


def compute_opaque_subject_hash(tenant_id: UUID, candidate_id: UUID) -> str:
    """Derives a non-PII opaque subject hash scoped to `(tenant_id, candidate_id)`."""
    return hmac.new(
        str(tenant_id).encode("utf-8"),
        str(candidate_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class OrganizationPolicy(Base):
    """Mutable, versioned governance policy per tenant organization."""

    __tablename__ = "organization_policy"

    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    ai_processing_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    ai_interview_packs_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )
    approved_ai_models: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
        server_default=sa.text("'[]'"),
    )
    monthly_ai_token_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_000_000, server_default="1000000"
    )
    outbound_communications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    reveal_peer_feedback_after_submit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    allow_blind_feedback_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    export_ttl_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24, server_default="24"
    )
    candidate_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=365, server_default="365"
    )
    audit_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=730, server_default="730"
    )
    backup_expiry_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=35, server_default="35"
    )
    min_analytics_cohort_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_by: Mapped[UUID] = mapped_column(
        nullable=False,
        default=lambda: UUID("00000000-0000-0000-0000-000000000000"),
        server_default=sa.text("'00000000-0000-0000-0000-000000000000'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=sa.func.now(),
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", name="pk_organization_policy"),
        CheckConstraint(
            "export_ttl_hours >= 1 AND export_ttl_hours <= 168",
            name="ck_org_policy_export_ttl",
        ),
        CheckConstraint(
            "candidate_retention_days >= 1 AND candidate_retention_days <= 3650",
            name="ck_org_policy_cand_retention",
        ),
        CheckConstraint(
            "backup_expiry_days >= 1 AND backup_expiry_days <= 365",
            name="ck_org_policy_backup_expiry",
        ),
        CheckConstraint(
            "min_analytics_cohort_size >= 3 AND min_analytics_cohort_size <= 100",
            name="ck_org_policy_min_cohort",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_organization_policy_tenant",
            ondelete="CASCADE",
        ),
    )


class OrganizationPolicyRevision(Base):
    """Immutable append-only revision log of organization governance policy updates."""

    __tablename__ = "organization_policy_revision"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    changed_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_organization_policy_revision"),
        UniqueConstraint("tenant_id", "revision_number", name="uq_org_policy_revision_number"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_org_policy_revision_tenant",
            ondelete="CASCADE",
        ),
    )


class RetentionHold(Base):
    """Legal, compliance, or dispute hold blocking candidate/application purge until release."""

    __tablename__ = "retention_hold"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    # Opaque UUIDs without CASCADE FK so released hold audit history survives subsequent purges
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    application_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    scope: Mapped[str] = mapped_column(
        String(30), nullable=False, default=HoldScope.CANDIDATE.value
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=HoldStatus.ACTIVE.value)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    issued_by: Mapped[UUID] = mapped_column(nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[UUID | None] = mapped_column(nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_retention_hold"),
        CheckConstraint("scope IN ('CANDIDATE', 'APPLICATION')", name="ck_retention_hold_scope"),
        CheckConstraint(
            "status IN ('ACTIVE', 'RELEASED', 'EXPIRED')", name="ck_retention_hold_status"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_retention_hold_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_retention_hold_candidate_status", "tenant_id", "candidate_id", "status"),
    )


class PrivacyRequest(Base):
    """Governed candidate privacy / retention request requiring verification before purge."""

    __tablename__ = "privacy_request"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    # Opaque UUID retained without PII so completed request state survives candidate row deletion
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    request_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=PrivacyRequestStatus.PENDING_VERIFICATION.value
    )
    verification_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=PrivacyVerificationStatus.UNVERIFIED.value
    )
    verified_contact_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    verified_by: Mapped[UUID | None] = mapped_column(nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    approved_by: Mapped[UUID | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    override_active_applications: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    retention_preview: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    residual_review_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)

    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    request_reason: Mapped[str] = mapped_column(Text(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_privacy_request"),
        UniqueConstraint("tenant_id", "id", name="uq_privacy_request_tenant_id"),
        CheckConstraint(
            "request_type IN ('ERASURE', 'EXPORT_PORTABILITY', 'RETENTION_SWEEP')",
            name="ck_privacy_request_type",
        ),
        CheckConstraint(
            "status IN ('PENDING_VERIFICATION', 'VERIFIED', 'APPROVED', "
            "'PARTIAL_FAILURE', 'COMPLETED', 'CANCELED')",
            name="ck_privacy_request_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_privacy_request_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_privacy_request_candidate", "tenant_id", "candidate_id", "status"),
    )


class DeletionLedger(Base):
    """Minimized database tombstone paired 1:1 with an independent encrypted S3 tombstone."""

    __tablename__ = "deletion_ledger"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    privacy_request_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    scope_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    s3_tombstone_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_tombstone_key: Mapped[str] = mapped_column(String(512), nullable=False)
    s3_tombstone_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    s3_tombstone_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)

    purged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    backup_window_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    replayed_after_restore: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    executed_by: Mapped[UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_deletion_ledger"),
        UniqueConstraint("tenant_id", "candidate_id", name="uq_deletion_ledger_candidate"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_deletion_ledger_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_deletion_ledger_purged_at", "tenant_id", "purged_at"),
    )


def _check_not_tombstoned_on_connection(
    connection: sa.Connection, tenant_id: UUID | None, candidate_id: UUID | None
) -> None:
    """Blocks stale background workers from resurrecting rows for a tombstoned candidate."""
    if tenant_id is None or candidate_id is None:
        return
    try:
        has_table = sa.inspect(connection).has_table("deletion_ledger")
    except Exception:
        return
    if not has_table:
        return

    found = connection.execute(
        sa.select(DeletionLedger.id).where(
            DeletionLedger.tenant_id == tenant_id,
            DeletionLedger.candidate_id == candidate_id,
        )
    ).first()
    if found is not None:
        raise CandidateTombstonedError(
            f"Candidate {candidate_id} in tenant {tenant_id} has been permanently purged; "
            "resurrection writes are blocked by deletion_ledger."
        )


@event.listens_for(Candidate, "before_insert")
def _guard_candidate_resurrection(
    _mapper: Any, connection: sa.Connection, target: Candidate
) -> None:
    _check_not_tombstoned_on_connection(connection, target.tenant_id, target.id)


@event.listens_for(CandidateFactRevision, "before_insert")
def _guard_fact_resurrection(
    _mapper: Any, connection: sa.Connection, target: CandidateFactRevision
) -> None:
    _check_not_tombstoned_on_connection(connection, target.tenant_id, target.candidate_id)


@event.listens_for(PackRevision, "before_insert")
def _guard_pack_resurrection(_mapper: Any, connection: sa.Connection, target: PackRevision) -> None:
    _check_not_tombstoned_on_connection(connection, target.tenant_id, target.candidate_id)
