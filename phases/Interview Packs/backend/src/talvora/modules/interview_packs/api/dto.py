"""Typed Pydantic v2 Request and Response Schemas for Interview Packs (P15)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompetencyInputDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=5, max_length=2000)
    observation_required: bool = Field(default=True)
    rubric_anchors: dict[str, str]
    display_order: int = Field(default=0, ge=0)


class CreateCompetencyFrameworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=120)
    stage: str = Field(min_length=1, max_length=50)
    seniority_band: str = Field(default="MID_SENIOR", min_length=1, max_length=50)
    job_id: UUID | None = None
    summary: str = Field(min_length=5, max_length=4000)
    behavioral_scale_guide: str = Field(min_length=5, max_length=4000)
    competencies: list[CompetencyInputDTO] = Field(min_length=1, max_length=25)


class CreateFrameworkVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_framework_version: int = Field(ge=1)
    summary: str = Field(min_length=5, max_length=4000)
    behavioral_scale_guide: str = Field(min_length=5, max_length=4000)
    competencies: list[CompetencyInputDTO] = Field(min_length=1, max_length=25)


class CompetencyResponse(BaseModel):
    id: UUID
    code: str
    name: str
    definition: str
    display_order: int
    observation_required: bool
    rubric_anchors: dict[str, str]


class CompetencyFrameworkSummaryResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    job_id: UUID | None
    stage: str
    seniority_band: str
    status: str
    latest_version_number: int
    latest_published_version_id: UUID | None
    latest_published_version_number: int | None
    version: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class CompetencyFrameworkDetailResponse(BaseModel):
    framework: CompetencyFrameworkSummaryResponse
    version_id: UUID
    version_number: int
    version_status: str
    summary: str
    behavioral_scale_guide: str
    published_by: UUID | None
    published_at: datetime | None
    competencies: list[CompetencyResponse]


class SyncInterviewContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: UUID
    stage: str = Field(default="TECHNICAL", min_length=1, max_length=50)
    seniority_level: str = Field(default="SENIOR", min_length=1, max_length=50)
    duration_minutes: int = Field(default=60, ge=15, le=240)
    schedule_revision: int = Field(default=1, ge=1)
    status: str = Field(default="BOOKED", min_length=1, max_length=30)
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    participant_user_ids: list[UUID] = Field(default_factory=list)
    booking_event_key: str | None = Field(default=None, max_length=120)


class InterviewContextResponse(BaseModel):
    interview_id: UUID
    application_id: UUID
    job_id: UUID
    candidate_id: UUID
    stage: str
    seniority_level: str
    duration_minutes: int
    schedule_revision: int
    status: str
    timezone: str
    participant_user_ids: list[UUID]
    admitted_operation_id: UUID | None = None


class CreatePackRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="EXPLICIT_PACK_RUN", min_length=1, max_length=200)
    framework_version_id: UUID | None = None


class PackClaimEditDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str = Field(default="OVERVIEW", min_length=1, max_length=50)
    content_scope: str = Field(default="CANDIDATE_SPECIFIC", min_length=1, max_length=30)
    statement: str = Field(min_length=1, max_length=4000)
    classification: str | None = None
    fact_revision_id: UUID | None = None
    evidence_fragment_ids: list[UUID] | None = None
    source_note: str | None = Field(default=None, max_length=1000)


class PackQuestionEditDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=30)
    content_scope: str = Field(min_length=1, max_length=30)
    premise_classification: str | None = None
    competency_id: UUID
    allocated_minutes: int = Field(ge=1, le=120)
    prompt_text: str = Field(min_length=5, max_length=4000)
    premise_text: str | None = Field(default=None, max_length=2000)
    expected_signals: list[str] = Field(min_length=1, max_length=10)
    fact_revision_id: UUID | None = None
    evidence_fragment_ids: list[UUID] | None = None


class PatchPackRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    candidate_overview: str | None = Field(default=None, max_length=4000)
    interviewer_instructions: str | None = Field(default=None, max_length=4000)
    notes_guidance: str | None = Field(default=None, max_length=4000)
    claims: list[PackClaimEditDTO] | None = None
    questions: list[PackQuestionEditDTO] | None = None


class PublishPackRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    review_notes: str | None = Field(default=None, max_length=2000)


class PackClaimResponse(BaseModel):
    id: UUID
    claim_index: int
    section: str
    content_scope: str
    classification: str
    statement: str
    fact_revision_id: UUID | None
    evidence_fragment_ids: list[UUID]
    source_note: str | None


class PackQuestionResponse(BaseModel):
    id: UUID
    order_index: int
    category: str
    content_scope: str
    premise_classification: str
    competency_id: UUID
    allocated_minutes: int
    prompt_text: str
    premise_text: str | None
    expected_signals: list[str]
    fact_revision_id: UUID | None
    evidence_fragment_ids: list[UUID]


class PackReviewResponse(BaseModel):
    id: UUID
    reviewer_id: UUID
    decision: str
    notes: str | None
    created_at: datetime


class PackRevisionResponse(BaseModel):
    id: UUID
    interview_id: UUID
    application_id: UUID
    job_id: UUID
    candidate_id: UUID
    revision_number: int
    status: str
    is_stale: bool
    stale_reasons: list[str]
    requirement_version_id: UUID
    document_version_id: UUID | None
    screening_run_id: UUID | None
    summary_revision_id: UUID | None
    framework_version_id: UUID
    stage: str
    seniority_level: str
    duration_minutes: int
    schedule_revision: int
    fact_snapshot_hash: str
    generation_reason: str
    based_on_revision_id: UUID | None
    model_or_generator_id: str
    candidate_overview: str
    role_requirements: list[dict[str, Any]]
    candidate_evidence_summary: str
    relevant_responsibilities: list[dict[str, Any]]
    competency_mapping: list[dict[str, Any]]
    interviewer_instructions: str
    time_allocation: dict[str, Any]
    evaluation_rubric: list[dict[str, Any]]
    notes_guidance: str
    feedback_form_structure: dict[str, Any]
    quality_warnings: list[str]
    version: int
    author_id: UUID
    published_by: UUID | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    claims: list[PackClaimResponse]
    questions: list[PackQuestionResponse]
    reviews: list[PackReviewResponse]
