"""Data Transfer Objects for Candidates, Contact Revisions, and Verifications."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from talvora.modules.candidates.domain.models import CandidateSource, ContactVerificationSource


class CreateCandidateRequest(BaseModel):
    """Payload to create a candidate identity.

    A draft upload-intake candidate requires no invented name or address: set
    `is_draft=true` and omit display_name/email/phone entirely if unknown.
    """

    display_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    source: CandidateSource = Field(default=CandidateSource.MANUAL_ENTRY)
    is_draft: bool = Field(default=False)

    model_config = ConfigDict(extra="forbid")


class UpdateCandidateRequest(BaseModel):
    """Payload to update candidate contact/display details.

    Any contact field supplied appends a new immutable contact revision. If the
    candidate's current contact was previously verified, this invalidates that verified
    status (the verified revision version no longer matches the current one) until a
    recruiter verifies the new contact again.
    """

    display_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    expected_version: int | None = None

    model_config = ConfigDict(extra="forbid")


class ContactVerificationRequest(BaseModel):
    """Human command recording source, actor, exact address, and contact version.

    This endpoint is NOT proof of mailbox ownership; it records a human attestation
    bound to an exact contact revision version.
    """

    contact_revision_number: int = Field(ge=1)
    verified_address: str = Field(min_length=3, max_length=320)
    verification_source: ContactVerificationSource
    notes: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = None

    model_config = ConfigDict(extra="forbid")


class ContactVerificationResponse(BaseModel):
    """Details of a recorded contact verification command."""

    id: UUID
    candidate_id: UUID
    contact_revision_id: UUID
    contact_revision_number: int
    verified_address: str
    verification_source: ContactVerificationSource
    notes: str | None = None
    actor_id: UUID
    created_at: datetime
    is_proof_of_ownership: bool = Field(
        default=False,
        description=(
            "Always false in this phase: a verification is a recorded human attestation, "
            "not independent proof of mailbox ownership."
        ),
    )

    model_config = ConfigDict(from_attributes=True)


class DuplicateSuggestionResponse(BaseModel):
    """Minimum-exposure duplicate candidate suggestion. Never a merge action."""

    candidate_id: UUID
    display_name: str | None = None
    masked_email: str | None = None
    is_draft: bool
    created_at: datetime
    matched_on: str


class CandidateResponse(BaseModel):
    """Complete detail response for a candidate identity."""

    id: UUID
    tenant_id: UUID
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: CandidateSource
    is_draft: bool
    current_contact_revision_number: int
    verified_contact_revision_number: int | None = None
    is_contact_verified: bool
    created_by: UUID
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CandidateListResponse(BaseModel):
    """Cursor-paginated list of candidates."""

    items: list[CandidateResponse]
    next_cursor: str | None = None
    total_count: int


class CreateCandidateResponse(BaseModel):
    """Candidate creation result including conservative, minimum-exposure duplicates."""

    candidate: CandidateResponse
    duplicate_suggestions: list[DuplicateSuggestionResponse] = Field(default_factory=list)
