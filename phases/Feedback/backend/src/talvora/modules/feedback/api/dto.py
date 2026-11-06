"""Typed Pydantic v2 Request and Response Schemas for Feedback and Exports (P16)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompetencyRatingInputDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competency_id: UUID
    rating: str = Field(min_length=1, max_length=20)
    observation: str = Field(default="", max_length=4000)
    question_notes: str | None = Field(default=None, max_length=4000)


class PutMyFeedbackDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)
    overall_observations: str | None = Field(default=None, max_length=8000)
    human_recommendation: str | None = Field(default=None, max_length=40)
    ratings: list[CompetencyRatingInputDTO] = Field(default_factory=list, max_length=25)


class SubmitMyFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)
    overall_observations: str | None = Field(default=None, max_length=8000)
    human_recommendation: str | None = Field(default=None, max_length=40)
    ratings: list[CompetencyRatingInputDTO] | None = Field(default=None, max_length=25)


class AmendMyFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    amendment_reason: str = Field(min_length=5, max_length=2000)
    overall_observations: str = Field(min_length=1, max_length=8000)
    human_recommendation: str = Field(default="NONE", min_length=1, max_length=40)
    ratings: list[CompetencyRatingInputDTO] = Field(min_length=1, max_length=25)


class CompetencyRatingResponse(BaseModel):
    id: UUID
    competency_id: UUID
    rating: str
    observation: str
    question_notes: str | None


class FeedbackRevisionResponse(BaseModel):
    id: UUID
    revision_number: int
    pack_revision_id: UUID
    framework_version_id: UUID
    overall_observations: str
    human_recommendation: str
    amendment_reason: str | None
    prior_revision_id: UUID | None
    is_late_submission: bool
    submitted_by: UUID
    submitted_at: datetime
    ratings: list[CompetencyRatingResponse]


class FeedbackResponse(BaseModel):
    id: UUID
    interview_id: UUID
    application_id: UUID
    job_id: UUID
    candidate_id: UUID
    interviewer_user_id: UUID
    pack_revision_id: UUID
    framework_version_id: UUID
    status: str
    draft_overall_observations: str | None = None
    draft_human_recommendation: str | None = None
    draft_ratings: list[dict[str, Any]] | None = None
    latest_submitted_revision: int
    first_submitted_at: datetime | None
    last_submitted_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    revisions: list[FeedbackRevisionResponse]


class CreatePackExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = Field(min_length=3, max_length=10)
    content_scope: str = Field(default="INTERVIEWER_PACK", min_length=5, max_length=40)


class RevokeExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=1000)


class ExportArtifactResponse(BaseModel):
    id: UUID
    pack_revision_id: UUID
    interview_id: UUID
    application_id: UUID
    job_id: UUID
    candidate_id: UUID
    format: str
    content_scope: str
    status: str
    sha256_checksum: str
    byte_size: int
    media_type: str
    requested_by: UUID
    expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None
    created_at: datetime
    download_url: str
    revocation_disclosure: str
