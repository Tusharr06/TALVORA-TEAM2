"""Candidate fact request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from talvora.modules.facts.domain.models import (
    CandidateFactPolarity,
    CandidateFactStatus,
    CandidateFactType,
)


class DeriveCandidateFactRequest(BaseModel):
    fact_type: CandidateFactType
    value: str = Field(min_length=1, max_length=2000)
    polarity: CandidateFactPolarity = CandidateFactPolarity.AFFIRMED
    evidence_fragment_ids: list[UUID] = Field(min_length=1, max_length=20)

    model_config = ConfigDict(extra="forbid")


class ReviewCandidateFactRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    status: CandidateFactStatus
    corrected_value: str | None = Field(default=None, max_length=2000)
    polarity: CandidateFactPolarity | None = None
    review_note: str = Field(default="", max_length=4000)

    model_config = ConfigDict(extra="forbid")


class CandidateFactResponse(BaseModel):
    id: UUID
    candidate_id: UUID
    fact_key: UUID
    revision_number: int
    fact_type: str
    value: str
    extracted_value: str
    polarity: str
    extracted_polarity: str
    status: str
    review_note: str | None
    extraction_run_id: UUID
    created_by: UUID
    created_at: datetime
    evidence_fragment_ids: list[UUID]

    model_config = ConfigDict(from_attributes=True)
