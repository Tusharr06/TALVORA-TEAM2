"""Domain Models for Candidate Identity, Contact Revisions, and Verifications.

A `Candidate` is a tenant-local identity aggregate, deliberately separate from the
job-specific `Application` aggregate owned by the `applications` module. Emails are
searchable tenant-local attributes, never a global identity key: two unrelated people
may legitimately share an email within (or across) tenants, and this module never
merges candidate identities automatically. Contact information changes are recorded as
an immutable, versioned revision log so that a human "contact verification" command can
be bound to an exact address and an exact version -- and so that later scheduling
consumers can detect that a previously verified recipient has changed.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
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
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base
from talvora.shared.transport.problems import ProblemException


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ============================================================================
# Enums
# ============================================================================


class CandidateSource(StrEnum):
    """How a candidate record came into existence."""

    MANUAL_ENTRY = "MANUAL_ENTRY"
    UPLOAD_INTAKE = "UPLOAD_INTAKE"


class ContactSource(StrEnum):
    """Provenance of a specific contact revision's values."""

    RECRUITER_ENTERED = "RECRUITER_ENTERED"
    CANDIDATE_SUBMITTED = "CANDIDATE_SUBMITTED"
    RESUME_EXTRACTED_UNVERIFIED = "RESUME_EXTRACTED_UNVERIFIED"
    SYSTEM_IMPORT = "SYSTEM_IMPORT"


class ContactVerificationSource(StrEnum):
    """How a human attested to an exact contact address.

    This is a record of a human command, not independent proof of mailbox ownership.
    Proof of ownership requires a separate verification flow (e.g. a confirmed reply or
    link click) that is not implemented in this phase.
    """

    RECRUITER_ATTESTATION = "RECRUITER_ATTESTATION"
    CANDIDATE_CONFIRMED_REPLY = "CANDIDATE_CONFIRMED_REPLY"
    THIRD_PARTY_REFERRAL = "THIRD_PARTY_REFERRAL"
    OTHER = "OTHER"


class ContactEventType(StrEnum):
    """Versioned contact lifecycle event types for downstream consumers."""

    CONTACT_REVISED = "CONTACT_REVISED"
    CONTACT_VERIFIED = "CONTACT_VERIFIED"


# ============================================================================
# Text Sanitization and Email Validation
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

# Conservative RFC-5322-lite email shape check. We deliberately do not accept anything
# "extracted" from free text without this exact-shape validation passing.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_text(text_value: str) -> str:
    """Sanitize candidate-entered text to prevent stored XSS and executable HTML tags."""
    if not text_value:
        return ""
    cleaned = _DANGEROUS_BLOCKS_RE.sub("", text_value)
    cleaned = _DANGEROUS_SELF_CLOSING_RE.sub("", cleaned)
    sanitized = _RAW_HTML_TAG_RE.sub("", cleaned).strip()
    return sanitized


def normalize_email(email: str) -> str:
    """Lowercase and trim an email for tenant-local search; never used as an identity key."""
    return email.strip().lower()


def normalize_name(name: str) -> str:
    """Collapse whitespace and lowercase a display name for tenant-local duplicate search."""
    return _WHITESPACE_RE.sub(" ", name.strip()).lower()


def validate_email_shape(email: str) -> str:
    """Reject malformed addresses. Returns the trimmed, original-case display value.

    This never trusts an address merely because it was present in resume text; the caller
    is responsible for only invoking this with a value a human explicitly entered or
    confirmed.
    """
    candidate = email.strip()
    if not candidate or not _EMAIL_RE.match(candidate):
        raise ProblemException(
            status_code=422,
            title="Invalid Contact Address",
            code="EVIDENCE_INVALID",
            detail=f"'{candidate}' is not a well-formed email address.",
        )
    return candidate


def mask_email(email: str) -> str:
    """Mask an email for minimum-exposure duplicate suggestion display."""
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    masked_local = "*" if len(local) <= 1 else local[0] + "*" * max(1, len(local) - 1)
    return f"{masked_local}@{domain}"


# ============================================================================
# SQLAlchemy Declarative Models
# ============================================================================


class Candidate(Base):
    """Tenant-local candidate identity aggregate root.

    Deliberately holds no job-specific state. Emails/names are searchable attributes
    only; they are never treated as a global or tenant-unique identity key, so duplicate
    emails are allowed and surfaced for human review rather than merged.
    """

    __tablename__ = "candidate"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    normalized_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default=CandidateSource.MANUAL_ENTRY.value
    )
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    current_contact_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    current_contact_revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified_contact_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    verified_contact_revision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate"),
        UniqueConstraint("tenant_id", "id", name="uq_candidate_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_candidate_tenant_email", "tenant_id", "normalized_email"),
        Index("ix_candidate_tenant_name", "tenant_id", "normalized_name"),
        Index("ix_candidate_tenant_created", "tenant_id", "created_at", "id"),
    )

    @property
    def is_contact_verified(self) -> bool:
        """True only when the currently active contact revision has a human verification."""
        return (
            self.verified_contact_revision_number is not None
            and self.verified_contact_revision_number == self.current_contact_revision_number
        )


class CandidateContactRevision(Base):
    """Immutable, append-only snapshot of a candidate's contact details.

    Keeps the normalized search fields separate from the original display values so
    that raw candidate-entered text is never silently rewritten.
    """

    __tablename__ = "candidate_contact_revision"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    normalized_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_contact_revision"),
        UniqueConstraint(
            "tenant_id",
            "candidate_id",
            "revision_number",
            name="uq_candidate_contact_revision_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_contact_revision_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_contact_revision_candidate",
            ondelete="CASCADE",
        ),
    )


class CandidateContactVerification(Base):
    """Immutable record of a human command attesting to an exact contact address.

    This is NOT proof of mailbox ownership -- it is a recorded human decision binding a
    source, actor, exact address, and contact revision version together for audit.
    """

    __tablename__ = "candidate_contact_verification"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    contact_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    contact_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)

    verified_address: Mapped[str] = mapped_column(String(320), nullable=False)
    verification_source: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_contact_verification"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_contact_verification_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_contact_verification_candidate",
            ondelete="CASCADE",
        ),
    )


class CandidateContactEvent(Base):
    """Versioned, immutable contact lifecycle event for later scheduling consumers.

    Emitted whenever a candidate's contact is revised or (re)verified, so that a future
    scheduling module can detect that a previously verified recipient has changed and
    invalidate downstream authorization built on the stale version.
    """

    __tablename__ = "candidate_contact_event"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    contact_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_verified_revision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_contact_event"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_contact_event_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_contact_event_candidate",
            ondelete="CASCADE",
        ),
        Index("ix_candidate_contact_event_candidate", "tenant_id", "candidate_id", "created_at"),
    )
