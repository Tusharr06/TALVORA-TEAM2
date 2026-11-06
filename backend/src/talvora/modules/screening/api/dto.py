"""Screening API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from talvora.modules.screening.domain.models import ScreeningOutcome


class ScreeningOverrideRequest(BaseModel):
    outcome: ScreeningOutcome
    reason: str = Field(min_length=1, max_length=4000)

    model_config = ConfigDict(extra="forbid")


class ScreeningFactCitationResponse(BaseModel):
    fact_revision_id: UUID
    evidence_fragment_ids: list[UUID]


class ScreeningResultResponse(BaseModel):
    criterion_id: UUID
    criterion_type: str
    is_required: bool
    outcome: ScreeningOutcome
    explanation: str
    citations: list[ScreeningFactCitationResponse]


class ScreeningOverrideResponse(BaseModel):
    id: UUID
    revision_number: int
    outcome: ScreeningOutcome
    reason: str
    actor_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScreeningRunResponse(BaseModel):
    id: UUID
    application_id: UUID
    candidate_id: UUID
    application_document_id: UUID | None
    document_version_id: UUID | None
    requirement_version_id: UUID
    outcome: ScreeningOutcome
    effective_outcome: ScreeningOutcome
    rule_version: str
    created_at: datetime
    results: list[ScreeningResultResponse]
    latest_override: ScreeningOverrideResponse | None


class ScreeningOverridesResponse(BaseModel):
    screening_run_id: UUID
    original_outcome: ScreeningOutcome
    overrides: list[ScreeningOverrideResponse]
