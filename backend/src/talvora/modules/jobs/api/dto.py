"""Data Transfer Objects for Jobs, Requirement Versions, Criteria, and Assignments."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from talvora.modules.jobs.domain.models import (
    CriterionDraft,
    CriterionType,
    JobRole,
    JobStatus,
)


class CreateJobRequest(BaseModel):
    """Payload to create a new job vacancy."""

    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    department: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=255)
    employment_type: str = Field(default="FULL_TIME", max_length=50)
    team_id: UUID | None = None
    hiring_manager_id: UUID | None = None
    draft_requirements: list[CriterionDraft] | None = None

    model_config = ConfigDict(extra="forbid")


class UpdateJobRequest(BaseModel):
    """Payload to update mutable draft and metadata on a job."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1)
    department: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=255)
    employment_type: str | None = Field(default=None, max_length=50)
    team_id: UUID | None = None
    draft_requirements: list[CriterionDraft] | None = None
    expected_version: int | None = None

    model_config = ConfigDict(extra="forbid")


class UpdateDraftRequirementsRequest(BaseModel):
    """Payload to update draft criteria for a job."""

    criteria: list[CriterionDraft] = Field(min_length=1)
    expected_version: int | None = None

    model_config = ConfigDict(extra="forbid")


class PublishRequirementsRequest(BaseModel):
    """Payload to snapshot and publish draft requirements."""

    change_summary: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = None

    model_config = ConfigDict(extra="forbid")


class StatusTransitionRequest(BaseModel):
    """Payload to transition job lifecycle status."""

    target_status: JobStatus
    reason: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = None

    model_config = ConfigDict(extra="forbid")


class AssignMemberRequest(BaseModel):
    """Payload to assign a member to a vacancy role."""

    user_id: UUID
    role: JobRole

    model_config = ConfigDict(extra="forbid")


class JobAssignmentResponse(BaseModel):
    """Details of a member assignment on a job."""

    id: UUID
    tenant_id: UUID
    job_id: UUID
    user_id: UUID
    role: JobRole
    assigned_by: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CriterionResponse(BaseModel):
    """Individual criterion in an immutable requirement version."""

    id: UUID
    criterion_type: CriterionType
    is_required: bool
    source_requirement_text: str
    relevance_rationale: str
    requires_human_review: bool
    competency_reference: str | None = None
    configuration: dict[str, Any]
    equivalence_rules: dict[str, Any] | None = None
    order_index: int

    model_config = ConfigDict(from_attributes=True)


class RequirementVersionResponse(BaseModel):
    """Snapshot of a published requirement version."""

    id: UUID
    tenant_id: UUID
    job_id: UUID
    version_number: int
    title: str
    description: str
    published_by: UUID
    published_at: datetime
    change_summary: str | None = None
    criteria: list[CriterionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class JobResponse(BaseModel):
    """Complete detail response for a job vacancy."""

    id: UUID
    tenant_id: UUID
    title: str
    description: str
    department: str | None = None
    location: str | None = None
    employment_type: str
    team_id: UUID | None = None
    creator_id: UUID
    hiring_manager_id: UUID | None = None
    status: JobStatus
    closed_reason: str | None = None
    current_published_version_id: UUID | None = None
    current_published_version_number: int | None = None
    draft_requirements: list[dict[str, Any]] | None = None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobListResponse(BaseModel):
    """Cursor-paginated list of job vacancies."""

    items: list[JobResponse]
    next_cursor: str | None = None
    total_count: int
