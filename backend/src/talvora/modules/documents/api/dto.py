"""Data Transfer Objects for Document Intake: Batches, Items, Documents, Bindings."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateUploadBatchRequest(BaseModel):
    """Payload to open a new upload batch scoped to one eligible open job."""

    job_id: UUID
    max_items: int | None = Field(default=None, gt=0, le=1000)
    max_file_bytes: int | None = Field(default=None, gt=0, le=200 * 1024 * 1024)
    max_total_bytes: int | None = Field(default=None, gt=0, le=2 * 1024 * 1024 * 1024)

    model_config = ConfigDict(extra="forbid")


class UploadBatchResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    job_id: UUID
    status: str
    max_items: int
    max_file_bytes: int
    max_total_bytes: int
    item_count: int
    reserved_total_bytes: int
    created_by: UUID
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UploadItemResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    batch_id: UUID
    sequence_number: int
    status: str
    candidate_id: UUID | None = None
    application_id: UUID | None = None
    created_draft_candidate: bool
    declared_filename: str | None = None
    declared_content_type: str | None = None
    reserved_bytes: int
    document_id: UUID | None = None
    document_version_id: UUID | None = None
    problem_code: str | None = None
    problem_detail: str | None = None
    next_action: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UploadBatchDetailResponse(BaseModel):
    batch: UploadBatchResponse
    items: list[UploadItemResponse]


class DocumentResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    candidate_id: UUID
    latest_version_number: int
    created_by: UUID
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentVersionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    document_id: UUID
    version_number: int
    sha256_checksum: str
    byte_size: int
    media_type: str
    storage_state: str
    scan_result: dict[str, object] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationDocumentBindingRequest(BaseModel):
    """Payload to explicitly bind a clean document version to an application.

    `expected_binding_generation` is mandatory optimistic-locking input: 0 means "no
    active binding exists yet"; otherwise it must match the application's current
    active binding generation, guarding against rebinding over a concurrently-created
    newer binding.
    """

    document_version_id: UUID
    expected_binding_generation: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class ApplicationDocumentResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    application_id: UUID
    document_id: UUID
    document_version_id: UUID
    binding_generation: int
    status: str
    bound_by: UUID
    bound_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
