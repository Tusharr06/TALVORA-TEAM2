"""Data Transfer Objects for Applications and Stage Transitions."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from talvora.modules.applications.domain.models import ApplicationSource, ApplicationStage


class CreateApplicationRequest(BaseModel):
    """Payload to create an application for an existing or a newly created candidate.

    Supply either `candidate_id` for an existing candidate, or new candidate fields
    (which may all be omitted for an unverified draft upload-intake candidate).
    """

    job_id: UUID
    candidate_id: UUID | None = None
    new_candidate_display_name: str | None = Field(default=None, max_length=255)
    new_candidate_email: str | None = Field(default=None, max_length=320)
    new_candidate_phone: str | None = Field(default=None, max_length=50)
    new_candidate_is_draft: bool = Field(default=False)
    source: ApplicationSource = Field(default=ApplicationSource.MANUAL_ENTRY)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_candidate_selection(self) -> "CreateApplicationRequest":
        has_existing = self.candidate_id is not None
        has_new = bool(
            self.new_candidate_display_name
            or self.new_candidate_email
            or self.new_candidate_is_draft
        )
        if not has_existing and not has_new:
            raise ValueError(
                "Supply either candidate_id or new candidate fields (new_candidate_is_draft=true "
                "is sufficient for an unverified draft)."
            )
        if has_existing and (self.new_candidate_display_name or self.new_candidate_email):
            raise ValueError("Supply either candidate_id or new candidate fields, not both.")
        return self


class UpdateApplicationRequest(BaseModel):
    """Payload to update non-stage application metadata."""

    notes: str | None = Field(default=None, max_length=4000)
    expected_version: int | None = None

    model_config = ConfigDict(extra="forbid")


class StageTransitionRequest(BaseModel):
    """Payload for a human-authorized pipeline stage transition.

    `expected_version` is mandatory: it is the version the actor observed before issuing
    this command, and guards against acting on stale/concurrent state.
    """

    target_stage: ApplicationStage
    reason: str | None = Field(default=None, max_length=2000)
    expected_version: int

    model_config = ConfigDict(extra="forbid")


class ApplicationResponse(BaseModel):
    """Detail response for a single application."""

    id: UUID
    tenant_id: UUID
    job_id: UUID
    candidate_id: UUID
    stage: ApplicationStage
    intake_cycle: int
    source: ApplicationSource
    notes: str | None = None
    created_by: UUID
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationListResponse(BaseModel):
    """Cursor-paginated, job-scoped list of applications."""

    items: list[ApplicationResponse]
    next_cursor: str | None = None
    total_count: int


class StageEventResponse(BaseModel):
    """A single immutable pipeline stage transition record."""

    id: UUID
    application_id: UUID
    from_stage: ApplicationStage | None = None
    to_stage: ApplicationStage
    reason: str | None = None
    actor_id: UUID
    actor_role: str
    expected_version: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InterviewEligibilityResponse(BaseModel):
    """Deterministic interview eligibility query result for later scheduling modules."""

    application_id: UUID
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
