"""Document Intake REST API Routes: Batches, Items, Documents, and Bindings."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from talvora.config import Settings, get_settings
from talvora.modules.documents.api.dto import (
    ApplicationDocumentBindingRequest,
    ApplicationDocumentResponse,
    CreateUploadBatchRequest,
    DocumentResponse,
    DocumentVersionResponse,
    UploadBatchDetailResponse,
    UploadBatchResponse,
    UploadItemResponse,
)
from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.documents.application.service import (
    DocumentService,
    UploadBatchService,
    UploadItemService,
)
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.idempotency import compute_request_hash
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1/upload-batches", tags=["Document Intake"])
documents_router = APIRouter(prefix="/api/v1/documents", tags=["Document Intake"])
application_bindings_router = APIRouter(prefix="/api/v1/applications", tags=["Document Intake"])


def get_object_storage(request: Request) -> ObjectStoragePort:
    """Resolve the ObjectStoragePort adapter registered on app.state at startup."""
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage is not configured.")
    return cast(ObjectStoragePort, storage)


@router.post("", response_model=UploadBatchResponse, status_code=status.HTTP_201_CREATED)
def create_upload_batch(
    payload: CreateUploadBatchRequest,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> UploadBatchResponse:
    """Open a batch scoped to one eligible open job, requiring intake permission."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        batch = UploadBatchService.create_batch(
            uow=uow,
            actor=actor,
            job_id=payload.job_id,
            max_items=payload.max_items or settings.upload_max_items_per_batch,
            max_file_bytes=payload.max_file_bytes or settings.upload_max_file_bytes,
            max_total_bytes=payload.max_total_bytes or settings.upload_max_batch_bytes,
        )
        uow.commit()
        return UploadBatchResponse.model_validate(batch)


@router.get("/{id}", response_model=UploadBatchDetailResponse)
def get_upload_batch(
    batch_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> UploadBatchDetailResponse:
    """Authorized batch progress: never reveals another tenant's items or object URLs."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        batch, items = UploadBatchService.get_batch(uow=uow, actor=actor, batch_id=batch_id)
        return UploadBatchDetailResponse(
            batch=UploadBatchResponse.model_validate(batch),
            items=[UploadItemResponse.model_validate(i) for i in items],
        )


@router.post("/{id}/close", response_model=UploadBatchResponse)
def close_upload_batch(
    batch_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> UploadBatchResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        batch = UploadBatchService.close_batch(uow=uow, actor=actor, batch_id=batch_id)
        uow.commit()
        return UploadBatchResponse.model_validate(batch)


@router.post("/{id}/items", response_model=UploadItemResponse, status_code=status.HTTP_201_CREATED)
def create_upload_item(
    batch_id: UUID = Path(..., alias="id"),
    file: UploadFile = File(...),
    candidate_id: Annotated[UUID | None, Form()] = None,
    new_candidate_display_name: Annotated[str | None, Form(max_length=255)] = None,
    new_candidate_email: Annotated[str | None, Form(max_length=320)] = None,
    new_candidate_phone: Annotated[str | None, Form(max_length=50)] = None,
    new_candidate_is_draft: Annotated[bool, Form()] = False,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStoragePort = Depends(get_object_storage),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> UploadItemResponse:
    """Stream exactly one file for this item under atomic batch quota reservation.

    A per-item failure (oversized file, spoofed type, malformed container, malware,
    etc.) returns 201 with a FAILED/BLOCKED item body carrying a safe problem code --
    it is not an HTTP error, so the rest of the batch can continue independently.
    """
    request_hash = (
        compute_request_hash(
            {
                "candidate_id": str(candidate_id) if candidate_id else None,
                "new_candidate_display_name": new_candidate_display_name,
                "new_candidate_email": new_candidate_email,
                "new_candidate_phone": new_candidate_phone,
                "new_candidate_is_draft": new_candidate_is_draft,
                "declared_filename": file.filename,
            }
        )
        if idempotency_key
        else None
    )

    item = UploadItemService.create_item(
        db=db,
        actor=actor,
        batch_id=batch_id,
        storage=storage,
        quarantine_bucket=settings.s3_bucket_name,
        file_obj=file.file,
        declared_filename=file.filename,
        declared_content_type=file.content_type,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        candidate_id=candidate_id,
        new_candidate_display_name=new_candidate_display_name,
        new_candidate_email=new_candidate_email,
        new_candidate_phone=new_candidate_phone,
        new_candidate_is_draft=new_candidate_is_draft,
    )
    return UploadItemResponse.model_validate(item)


@router.post("/{batch_id}/items/{item_id}/cancel", response_model=UploadItemResponse)
def cancel_upload_item(
    batch_id: UUID,
    item_id: UUID,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> UploadItemResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        item = UploadItemService.cancel_item(
            uow=uow, actor=actor, item_id=item_id, batch_id=batch_id
        )
        uow.commit()
        return UploadItemResponse.model_validate(item)


@router.post("/{batch_id}/items/{item_id}/retry", response_model=UploadItemResponse)
def retry_upload_item(
    batch_id: UUID,
    item_id: UUID,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> UploadItemResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        item = UploadItemService.retry_item(
            uow=uow, actor=actor, item_id=item_id, batch_id=batch_id
        )
        uow.commit()
        return UploadItemResponse.model_validate(item)


# ============================================================================
# Document metadata and authorized proxied download
# ============================================================================


@documents_router.get("/{id}", response_model=DocumentResponse)
def get_document(
    document_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> DocumentResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        document = DocumentService.get_document(uow=uow, actor=actor, document_id=document_id)
        return DocumentResponse.model_validate(document)


@documents_router.get("/{id}/versions/{version_number}", response_model=DocumentVersionResponse)
def get_document_version(
    document_id: UUID = Path(..., alias="id"),
    version_number: int = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> DocumentVersionResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        doc_version = DocumentService.get_document_version(
            uow=uow, actor=actor, document_id=document_id, version_number=version_number
        )
        return DocumentVersionResponse.model_validate(doc_version)


@documents_router.get("/{id}/versions/{version_number}/download")
def download_document_version(
    document_id: UUID = Path(..., alias="id"),
    version_number: int = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    storage: ObjectStoragePort = Depends(get_object_storage),
) -> StreamingResponse:
    """Authorized, proxied binary download. Never redirects to a presigned/public URL."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        doc_version = DocumentService.get_downloadable_version(
            uow=uow, actor=actor, document_id=document_id, version_number=version_number
        )
        bucket = doc_version.storage_bucket
        key = doc_version.storage_key
        media_type = doc_version.media_type

    stream = storage.get_object_stream(bucket, key)
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="document-v{version_number}"'},
    )


# ============================================================================
# Application <-> document version bindings
# ============================================================================


@application_bindings_router.post(
    "/{id}/document-bindings",
    response_model=ApplicationDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def bind_document_to_application(
    payload: ApplicationDocumentBindingRequest,
    application_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ApplicationDocumentResponse:
    """Explicit, version-checked binding of a clean resume version to an application."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        binding = DocumentService.bind_document_to_application(
            uow=uow,
            actor=actor,
            application_id=application_id,
            document_version_id=payload.document_version_id,
            expected_binding_generation=payload.expected_binding_generation,
        )
        uow.commit()
        return ApplicationDocumentResponse.model_validate(binding)


@application_bindings_router.get(
    "/{id}/document-bindings/active", response_model=ApplicationDocumentResponse | None
)
def get_active_document_binding(
    application_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ApplicationDocumentResponse | None:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        binding = DocumentService.get_active_binding(
            uow=uow, actor=actor, application_id=application_id
        )
        if binding is None:
            return None
        return ApplicationDocumentResponse.model_validate(binding)
