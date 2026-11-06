"""Data Transfer Objects for Evidence Extraction: Status, Source, Fragments."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExtractionRunSummaryResponse(BaseModel):
    """Status/quality summary for one extraction run -- no segment text included."""

    id: UUID
    tenant_id: UUID
    document_id: UUID
    document_version_id: UUID
    revision_number: int
    extractor_version: str
    normalization_version: str
    media_type: str
    status: str
    problem_code: str | None = None
    is_complete: bool
    warnings: list[str]
    error_detail: str | None = None
    total_pages: int | None = None
    processed_pages: int | None = None
    ocr_pages_used: int
    total_characters: int
    triggered_by: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    is_stale: bool = False

    model_config = ConfigDict(from_attributes=True)


class SourceSegmentResponse(BaseModel):
    id: UUID
    segment_index: int
    locator_type: str
    page_number: int | None = None
    docx_path: str | None = None
    extraction_method: str
    text: str
    char_count: int
    canonical_text_sha256: str
    is_low_confidence: bool
    ocr_mean_confidence: float | None = None
    warnings: list[str]

    model_config = ConfigDict(from_attributes=True)


class DocumentSourceResponse(BaseModel):
    """GET /documents/{id}/versions/{version}/source"""

    document_id: UUID
    document_version_id: UUID
    version_number: int
    extraction: ExtractionRunSummaryResponse | None = None
    segments: list[SourceSegmentResponse]


class EvidenceFragmentResponse(BaseModel):
    """GET /evidence/{id}"""

    id: UUID
    document_version_id: UUID
    source_segment_id: UUID
    extraction_run_id: UUID
    start_offset: int
    end_offset: int
    quote: str
    canonical_text_sha256: str
    extraction_method: str
    locator_type: str
    page_number: int | None = None
    docx_path: str | None = None
    is_low_confidence: bool
    ocr_mean_confidence: float | None = None
    segment_warnings: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReprocessDocumentRequest(BaseModel):
    """POST /documents/{id}/reprocess body: which version to reprocess."""

    version_number: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class ReprocessDocumentResponse(BaseModel):
    """POST /documents/{id}/reprocess"""

    extraction_run: ExtractionRunSummaryResponse


__all__ = [
    "DocumentSourceResponse",
    "EvidenceFragmentResponse",
    "ExtractionRunSummaryResponse",
    "ReprocessDocumentRequest",
    "ReprocessDocumentResponse",
    "SourceSegmentResponse",
]
