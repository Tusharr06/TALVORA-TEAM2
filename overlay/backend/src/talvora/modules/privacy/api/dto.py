"""Typed Pydantic v2 Schemas for Organization Policy, Holds, and Privacy Requests (P17)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrganizationPolicyResponse(BaseModel):
    tenant_id: UUID
    ai_processing_enabled: bool
    ai_interview_packs_enabled: bool = True
    approved_ai_models: list[str]
    monthly_ai_token_budget: int
    outbound_communications_enabled: bool
    reveal_peer_feedback_after_submit: bool
    allow_blind_feedback_override: bool
    export_ttl_hours: int
    candidate_retention_days: int
    audit_retention_days: int
    backup_expiry_days: int
    min_analytics_cohort_size: int
    version: int
    updated_by: UUID
    updated_at: datetime
    canceled_queued_operations: int = 0
    jurisdiction_disclosure: str


class PatchOrganizationPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=2000)
    ai_processing_enabled: bool | None = None
    ai_interview_packs_enabled: bool | None = None
    approved_ai_models: list[str] | None = None
    monthly_ai_token_budget: int | None = Field(default=None, ge=0)
    outbound_communications_enabled: bool | None = None
    reveal_peer_feedback_after_submit: bool | None = None
    allow_blind_feedback_override: bool | None = None
    export_ttl_hours: int | None = Field(default=None, ge=1, le=168)
    candidate_retention_days: int | None = Field(default=None, ge=1, le=3650)
    audit_retention_days: int | None = Field(default=None, ge=30, le=3650)
    backup_expiry_days: int | None = Field(default=None, ge=1, le=365)
    min_analytics_cohort_size: int | None = Field(default=None, ge=3, le=100)


class CreateRetentionHoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID
    application_id: UUID | None = None
    scope: str = Field(default="CANDIDATE", min_length=5, max_length=30)
    reason: str = Field(min_length=5, max_length=2000)
    review_at: datetime
    expires_at: datetime | None = None


class ReleaseRetentionHoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    release_reason: str = Field(min_length=5, max_length=2000)


class RetentionHoldResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    candidate_id: UUID
    application_id: UUID | None
    scope: str
    status: str
    reason: str
    issued_by: UUID
    issued_at: datetime
    review_at: datetime
    expires_at: datetime | None
    released_by: UUID | None
    released_at: datetime | None
    release_reason: str | None
    version: int


class CreatePrivacyRequestDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID
    request_type: str = Field(default="ERASURE", min_length=5, max_length=40)
    request_reason: str = Field(min_length=5, max_length=2000)


class VerifyPrivacyRequestDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    verification_method: str = Field(min_length=5, max_length=40)
    verification_notes: str = Field(min_length=3, max_length=2000)


class ApprovePrivacyRequestDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    approval_notes: str = Field(min_length=5, max_length=2000)
    override_active_applications: bool = False


class DeletionLedgerResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    privacy_request_id: UUID | None
    candidate_id: UUID
    subject_hash: str
    scope_summary: dict[str, Any]
    s3_tombstone_bucket: str
    s3_tombstone_key: str
    s3_tombstone_sha256: str
    purged_at: datetime
    backup_window_expires_at: datetime
    replayed_after_restore: bool


class PrivacyRequestResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    candidate_id: UUID
    subject_hash: str
    request_type: str
    status: str
    verification_status: str
    verified_contact_revision_id: UUID | None
    verified_by: UUID | None
    verified_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    override_active_applications: bool
    retention_preview: dict[str, Any]
    residual_review_items: list[dict[str, Any]]
    last_error: str | None
    requested_by: UUID
    request_reason: str
    completed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    residual_disclosure: str
    deletion_ledger: DeletionLedgerResponse | None = None


class TombstoneReplayResponse(BaseModel):
    s3_tombstones_scanned: int
    replayed_resurrected_count: int
    already_applied_count: int
    replayed_at_utc: str
