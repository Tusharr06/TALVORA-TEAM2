"""Summary generation and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SummaryClaimDraft(BaseModel):
    """Untrusted generated claim and its proposed fact/evidence citations."""

    text: str = Field(min_length=1, max_length=4000)
    fact_revision_id: UUID
    evidence_fragment_ids: list[UUID] = Field(min_length=1, max_length=20)

    model_config = ConfigDict(extra="forbid")


class GenerateSummaryRequest(BaseModel):
    screening_run_id: UUID
    draft_claims: list[SummaryClaimDraft] | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid")


class SummaryClaimResponse(BaseModel):
    id: UUID
    fact_revision_id: UUID
    text: str
    evidence_fragment_ids: list[UUID]


class CandidateSummaryResponse(BaseModel):
    id: UUID
    application_id: UUID
    screening_run_id: UUID
    revision_number: int
    quality_status: str
    summary_text: str
    quality_issues: list[str]
    created_by: UUID
    created_at: datetime
    claims: list[SummaryClaimResponse]

    model_config = ConfigDict(from_attributes=True)
