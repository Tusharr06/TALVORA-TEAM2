"""Evidence REST API Routes: Source Text, Extraction Status, Fragments, Reprocess."""

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from talvora.modules.evidence.api.dto import (
    DocumentSourceResponse,
    EvidenceFragmentResponse,
    ExtractionRunSummaryResponse,
    ReprocessDocumentRequest,
    ReprocessDocumentResponse,
    SourceSegmentResponse,
)
from talvora.modules.evidence.application.extractor import extractor_version_for_media_type
from talvora.modules.evidence.application.normalization import NORMALIZATION_VERSION
from talvora.modules.evidence.application.service import (
    EvidenceService,
    ExtractionAdmissionService,
    SourceService,
)
from talvora.modules.evidence.domain.models import ExtractionRun as ExtractionRunModel
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork

evidence_documents_router = APIRouter(prefix="/api/v1/documents", tags=["Evidence"])
evidence_router = APIRouter(prefix="/api/v1/evidence", tags=["Evidence"])


def _is_stale(run: ExtractionRunModel) -> bool:
    """True when the current code's extractor/normalization version differs from the
    one that produced this run -- an actionable signal to call POST /reprocess, never
    an automatic silent reprocess."""
    try:
        current_extractor_version = extractor_version_for_media_type(run.media_type)
    except ValueError:
        return True
    return (
        run.extractor_version != current_extractor_version
        or run.normalization_version != NORMALIZATION_VERSION
    )


def _run_summary(run: ExtractionRunModel) -> ExtractionRunSummaryResponse:
    return ExtractionRunSummaryResponse(
        id=run.id,
        tenant_id=run.tenant_id,
        document_id=run.document_id,
        document_version_id=run.document_version_id,
        revision_number=run.revision_number,
        extractor_version=run.extractor_version,
        normalization_version=run.normalization_version,
        media_type=run.media_type,
        status=run.status,
        problem_code=run.problem_code,
        is_complete=run.is_complete,
        warnings=list(run.warnings or []),
        error_detail=run.error_detail,
        total_pages=run.total_pages,
        processed_pages=run.processed_pages,
        ocr_pages_used=run.ocr_pages_used,
        total_characters=run.total_characters,
        triggered_by=run.triggered_by,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        is_stale=_is_stale(run),
    )


@evidence_documents_router.get(
    "/{id}/versions/{version_number}/source", response_model=DocumentSourceResponse
)
def get_document_source(
    document_id: UUID = Path(..., alias="id"),
    version_number: int = Path(...),
    revision: int | None = Query(default=None, ge=1),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> DocumentSourceResponse:
    """Source-addressable text for one immutable document version.

    Only a `SUCCEEDED`/`PARTIAL` run's segments are ever included; a `PENDING`/
    `RUNNING`/`NEEDS_REVIEW`/`FAILED` run is reported via `extraction` with an empty
    `segments` list -- a bounded partial extraction is never presented as a full
    candidate history, and an incomplete/unreadable one is never presented as text.
    """
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        doc_version, run, segments = SourceService.get_source(
            uow=uow,
            actor=actor,
            document_id=document_id,
            version_number=version_number,
            revision_number=revision,
        )
        return DocumentSourceResponse(
            document_id=doc_version.document_id,
            document_version_id=doc_version.id,
            version_number=doc_version.version_number,
            extraction=_run_summary(run) if run else None,
            segments=[SourceSegmentResponse.model_validate(s) for s in segments],
        )


@evidence_documents_router.get(
    "/{id}/versions/{version_number}/extraction", response_model=ExtractionRunSummaryResponse | None
)
def get_extraction_status(
    document_id: UUID = Path(..., alias="id"),
    version_number: int = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ExtractionRunSummaryResponse | None:
    """Latest extraction run's status/quality summary -- no source text."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        _doc_version, run = SourceService.get_latest_extraction_status(
            uow=uow, actor=actor, document_id=document_id, version_number=version_number
        )
        return _run_summary(run) if run else None


@evidence_documents_router.post(
    "/{id}/reprocess", response_model=ReprocessDocumentResponse, status_code=202
)
def reprocess_document(
    payload: ReprocessDocumentRequest,
    document_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ReprocessDocumentResponse:
    """Explicitly request a new extraction revision for a clean document version.

    Always creates a new, independent `ExtractionRun` revision -- it never mutates an
    existing run's segments/fragments, and it never changes which version is bound to
    any application (that remains a separate, explicit `document-bindings` command).
    """
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        run = ExtractionAdmissionService.reprocess_document_version(
            uow=uow,
            actor=actor,
            document_id=document_id,
            version_number=payload.version_number,
        )
        uow.commit()
        return ReprocessDocumentResponse(extraction_run=_run_summary(run))


@evidence_router.get("/{id}", response_model=EvidenceFragmentResponse)
def get_evidence_fragment(
    fragment_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> EvidenceFragmentResponse:
    """One immutable, hash-and-substring-verified evidence fragment.

    Tenant-scoped lookup: a fragment ID from another tenant always 404s, never
    revealing cross-tenant existence.
    """
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        fragment, segment = EvidenceService.get_fragment(
            uow=uow, actor=actor, fragment_id=fragment_id
        )
        return EvidenceFragmentResponse(
            id=fragment.id,
            document_version_id=fragment.document_version_id,
            source_segment_id=fragment.source_segment_id,
            extraction_run_id=fragment.extraction_run_id,
            start_offset=fragment.start_offset,
            end_offset=fragment.end_offset,
            quote=fragment.quote,
            canonical_text_sha256=segment.canonical_text_sha256,
            extraction_method=segment.extraction_method,
            locator_type=segment.locator_type,
            page_number=segment.page_number,
            docx_path=segment.docx_path,
            is_low_confidence=segment.is_low_confidence,
            ocr_mean_confidence=segment.ocr_mean_confidence,
            segment_warnings=list(segment.warnings or []),
            created_at=fragment.created_at,
        )
