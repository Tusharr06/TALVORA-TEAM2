"""Document Intake Application Services: Batches, Items, Documents, and Bindings.

Unlike most other Talvora module services, `UploadItemService.create_item` deliberately
manages *several* short-lived top-level database transactions itself (rather than
running entirely inside one caller-supplied `UnitOfWork`). This is required, not
incidental: the batch byte/item quota must be reserved and finalized atomically around
a slow network upload that must never happen while holding a row lock, and a failure in
one phase (e.g. a duplicate-active-application conflict, or an interrupted upload) must
not abort progress already durably recorded in an earlier phase. Each phase is commented
with which part of the P06 contract it satisfies.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from talvora.modules.applications.application.service import ApplicationService
from talvora.modules.applications.domain.models import Application, ApplicationSource
from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.documents.application.validation import (
    ContainerValidationError,
    sniff_media_type,
    validate_container,
    validate_declared_type_matches_sniffed,
)
from talvora.modules.documents.domain.models import (
    DOCUMENT_VERSION_DOWNLOADABLE_STATES,
    UPLOAD_ITEM_CANCELABLE_STATUSES,
    UPLOAD_ITEM_RETRYABLE_STATUSES,
    ApplicationDocument,
    ApplicationDocumentStatus,
    BatchStatus,
    Document,
    DocumentVersion,
    DocumentVersionStorageState,
    IntakeProblemCode,
    ObjectReceipt,
    ObjectReceiptStatus,
    UploadBatch,
    UploadItem,
    UploadItemStatus,
    generate_opaque_storage_key,
    sanitize_display_filename,
)
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.jobs.application.service import JobService
from talvora.modules.jobs.domain.models import JobRole, JobStatus
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

_INTAKE_AUTHORITY_ROLES: set[str] = {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _verify_tenant_actor(actor: ActorContext) -> UUID:
    if not actor.is_authenticated or actor.tenant_id is None or actor.user_id is None:
        raise ProblemException(
            status_code=401,
            title="Unauthorized",
            code="UNAUTHENTICATED",
            detail="Valid session authentication within an organization is required.",
        )
    return actor.tenant_id


def _require_capability(actor: ActorContext, capability: Capability) -> None:
    if not actor.has_capability(capability.value):
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="CAPABILITY_DENIED",
            detail=f"Actor lacks capability '{capability.value}' required for this operation.",
        )


# ============================================================================
# Upload Batches
# ============================================================================


class UploadBatchService:
    """Creates and reads batches scoped to one eligible open job."""

    @classmethod
    def create_batch(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        max_items: int,
        max_file_bytes: int,
        max_total_bytes: int,
    ) -> UploadBatch:
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)

        job = JobService.get_job_with_authority(
            uow=uow, actor=actor, job_id=job_id, allowed_roles=_INTAKE_AUTHORITY_ROLES
        )
        if job.status != JobStatus.OPEN.value:
            raise ProblemException(
                status_code=409,
                title="Job Not Accepting Intake",
                code="JOB_NOT_ACCEPTING_INTAKE",
                detail=(
                    f"Job is in status '{job.status}'. Only an OPEN job accepts a new upload batch."
                ),
            )

        now = _utc_now()
        batch = UploadBatch(
            id=uuid4(),
            tenant_id=tenant_id,
            job_id=job_id,
            status=BatchStatus.OPEN.value,
            max_items=max_items,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            item_count=0,
            reserved_total_bytes=0,
            created_by=actor.user_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(batch)
        uow.session.flush()

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="UPLOAD_BATCH_CREATED",
            resource_type="upload_batch",
            resource_id=batch.id,
            payload={"job_id": str(job_id)},
        )
        return batch

    @classmethod
    def get_batch(
        cls, uow: UnitOfWork, actor: ActorContext, batch_id: UUID
    ) -> tuple[UploadBatch, list[UploadItem]]:
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)

        batch = uow.session.scalar(
            select(UploadBatch).where(
                UploadBatch.tenant_id == tenant_id, UploadBatch.id == batch_id
            )
        )
        if batch is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested upload batch was not found.",
            )

        # Resource concealment plus object-level authority: only an actor assigned (or
        # the organization OWNER) on the batch's job may view its progress.
        JobService.get_job_with_authority(
            uow=uow, actor=actor, job_id=batch.job_id, allowed_roles=_INTAKE_AUTHORITY_ROLES
        )

        items = list(
            uow.session.scalars(
                select(UploadItem)
                .where(UploadItem.tenant_id == tenant_id, UploadItem.batch_id == batch_id)
                .order_by(UploadItem.sequence_number.asc())
            ).all()
        )
        return batch, items

    @classmethod
    def close_batch(cls, uow: UnitOfWork, actor: ActorContext, batch_id: UUID) -> UploadBatch:
        """Explicitly close a batch to new items (idempotent)."""
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)

        batch = uow.session.scalar(
            select(UploadBatch)
            .where(UploadBatch.tenant_id == tenant_id, UploadBatch.id == batch_id)
            .with_for_update()
        )
        if batch is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested upload batch was not found.",
            )
        JobService.get_job_with_authority(
            uow=uow, actor=actor, job_id=batch.job_id, allowed_roles=_INTAKE_AUTHORITY_ROLES
        )
        if batch.status != BatchStatus.CLOSED.value:
            batch.status = BatchStatus.CLOSED.value
            batch.version += 1
            batch.updated_at = _utc_now()
            uow.record_audit_event(
                actor_id=actor.user_id,
                action="UPLOAD_BATCH_CLOSED",
                resource_type="upload_batch",
                resource_id=batch.id,
            )
        return batch


# ============================================================================
# Upload Items: the multi-phase, quota-safe admission pipeline
# ============================================================================


class UploadItemService:
    """Admits one streamed file per item under atomic batch quota reservation.

    `create_item` is the only public entry point and runs all phases in sequence. Each
    phase below is its own top-level DB transaction so that a failure partway through
    (a duplicate-application conflict, an interrupted upload, a batch-byte overrun)
    neither corrupts an already-durable earlier phase nor aborts the rest of the batch.
    """

    # ------------------------------------------------------------------
    # Phase 1: atomic slot reservation (bounds item_count under concurrency)
    # ------------------------------------------------------------------

    @classmethod
    def _reserve_slot(
        cls,
        db: Session,
        actor: ActorContext,
        batch_id: UUID,
        declared_filename: str | None,
        declared_content_type: str | None,
        idempotency_key: str | None,
        request_hash: str | None,
    ) -> tuple[UploadItem, bool]:
        """Returns (item, is_idempotent_replay)."""
        with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
            tenant_id = _verify_tenant_actor(actor)
            _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)

            batch = uow.session.scalar(
                select(UploadBatch)
                .where(UploadBatch.tenant_id == tenant_id, UploadBatch.id == batch_id)
                .with_for_update()
            )
            if batch is None:
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="RESOURCE_NOT_FOUND",
                    detail="The requested upload batch was not found.",
                )
            JobService.get_job_with_authority(
                uow=uow, actor=actor, job_id=batch.job_id, allowed_roles=_INTAKE_AUTHORITY_ROLES
            )

            if batch.status != BatchStatus.OPEN.value:
                raise ProblemException(
                    status_code=409,
                    title="Batch Closed",
                    code="BATCH_CLOSED",
                    detail="This upload batch is closed and no longer accepts new items.",
                )

            if idempotency_key:
                existing = uow.session.scalar(
                    select(UploadItem).where(
                        UploadItem.tenant_id == tenant_id,
                        UploadItem.batch_id == batch_id,
                        UploadItem.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise ProblemException(
                            status_code=409,
                            title="Idempotency Conflict",
                            code="IDEMPOTENCY_CONFLICT",
                            detail=(
                                f"Idempotency key '{idempotency_key}' was already used "
                                "for a different request on this batch."
                            ),
                        )
                    uow.commit()
                    return existing, True

            if batch.item_count >= batch.max_items:
                raise ProblemException(
                    status_code=409,
                    title="Batch Item Limit Exceeded",
                    code=IntakeProblemCode.BATCH_ITEM_LIMIT_EXCEEDED.value,
                    detail=f"This batch already holds the maximum of {batch.max_items} items.",
                )

            batch.item_count += 1
            batch.version += 1
            batch.updated_at = _utc_now()
            seq = batch.item_count

            now = _utc_now()
            item = UploadItem(
                id=uuid4(),
                tenant_id=tenant_id,
                batch_id=batch_id,
                sequence_number=seq,
                status=UploadItemStatus.RESERVED.value,
                declared_filename=sanitize_display_filename(declared_filename),
                declared_content_type=declared_content_type,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                created_by=actor.user_id,
                version=1,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(item)
            uow.session.flush()
            uow.commit()
            return item, False

    # ------------------------------------------------------------------
    # Phase 2: candidate/application resolution (idempotent; isolated failure)
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_candidate(
        cls,
        db: Session,
        actor: ActorContext,
        item_id: UUID,
        batch_job_id: UUID,
        candidate_id: UUID | None,
        new_candidate_display_name: str | None,
        new_candidate_email: str | None,
        new_candidate_phone: str | None,
        new_candidate_is_draft: bool,
    ) -> UploadItem:
        tenant_id = actor.tenant_id
        assert tenant_id is not None

        resolved_candidate_id: UUID | None = None
        resolved_application_id: UUID | None = None
        problem: ProblemException | None = None

        try:
            with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
                application = ApplicationService.create_application(
                    uow=uow,
                    actor=actor,
                    job_id=batch_job_id,
                    candidate_id=candidate_id,
                    new_candidate_display_name=new_candidate_display_name,
                    new_candidate_email=new_candidate_email,
                    new_candidate_phone=new_candidate_phone,
                    new_candidate_is_draft=new_candidate_is_draft or candidate_id is None,
                    source=ApplicationSource.UPLOAD_INTAKE,
                )
                resolved_candidate_id = application.candidate_id
                resolved_application_id = application.id
        except ProblemException as exc:
            problem = exc

        with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            item = uow.session.scalar(
                select(UploadItem)
                .where(UploadItem.tenant_id == tenant_id, UploadItem.id == item_id)
                .with_for_update()
            )
            assert item is not None

            if problem is not None:
                item.status = UploadItemStatus.FAILED.value
                item.problem_code = problem.code
                item.problem_detail = problem.detail
                item.next_action = "choose_different_candidate_or_cancel_item"
            else:
                item.candidate_id = resolved_candidate_id
                item.application_id = resolved_application_id
                item.created_draft_candidate = candidate_id is None
            item.version += 1
            item.updated_at = _utc_now()
            uow.record_audit_event(
                actor_id=actor.user_id,
                action="UPLOAD_ITEM_CANDIDATE_RESOLVED"
                if problem is None
                else "UPLOAD_ITEM_CANDIDATE_RESOLUTION_FAILED",
                resource_type="upload_item",
                resource_id=item.id,
                payload={"problem_code": problem.code if problem else None},
            )
            uow.commit()
            return item

    # ------------------------------------------------------------------
    # Phase 3: bounded streaming read, signature sniff, structural validation
    # ------------------------------------------------------------------

    @staticmethod
    def _read_bounded(file_obj: BinaryIO, max_bytes: int, chunk_size: int) -> bytes:
        """Read at most `max_bytes` without ever buffering more than one chunk over it.

        Raises ContainerValidationError(FILE_TOO_LARGE) the instant the real byte count
        exceeds the limit -- the declared Content-Length/size is never trusted.
        """
        buf = bytearray()
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ContainerValidationError(
                    IntakeProblemCode.FILE_TOO_LARGE,
                    f"File exceeds the per-file limit of {max_bytes} bytes.",
                )
        return bytes(buf)

    @classmethod
    def _validate_content(
        cls,
        data: bytes,
        declared_content_type: str | None,
        declared_filename: str | None,
    ) -> tuple[str, str]:
        """Returns (media_type, sha256_checksum) or raises ContainerValidationError."""
        if not data:
            raise ContainerValidationError(IntakeProblemCode.EMPTY_FILE, "Uploaded file is empty.")

        sniffed = sniff_media_type(data[:16])
        if sniffed is None:
            raise ContainerValidationError(
                IntakeProblemCode.UNSUPPORTED_MEDIA_TYPE,
                "File signature does not match a supported PDF or DOCX container.",
            )
        validate_declared_type_matches_sniffed(declared_content_type, declared_filename, sniffed)
        report = validate_container(sniffed, data)
        checksum = hashlib.sha256(data).hexdigest()
        return report.media_type, checksum

    # ------------------------------------------------------------------
    # Phase 4/5/6: durable receipt, quarantine write, atomic metadata admission
    # ------------------------------------------------------------------

    @classmethod
    def _admit_content(
        cls,
        db: Session,
        actor: ActorContext,
        item_id: UUID,
        storage: ObjectStoragePort,
        quarantine_bucket: str,
        data: bytes,
        media_type: str,
        checksum: str,
    ) -> UploadItem:
        tenant_id = actor.tenant_id
        assert tenant_id is not None
        byte_size = len(data)
        document_version_id = uuid4()
        storage_key = generate_opaque_storage_key(tenant_id)

        # Tenant content reuse: only after another version's scan already produced CLEAN.
        with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            reusable = uow.session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.tenant_id == tenant_id,
                    DocumentVersion.sha256_checksum == checksum,
                    DocumentVersion.storage_state == DocumentVersionStorageState.CLEAN.value,
                )
                .limit(1)
            )
            reuse_bucket = reusable.storage_bucket if reusable else None
            reuse_key = reusable.storage_key if reusable else None

        if reuse_bucket is None:
            # Phase 4: durable intent record BEFORE the slow network write. This is the
            # explicit temporary object state bridging the S3/DB dual-write gap.
            with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
                receipt = ObjectReceipt(
                    id=document_version_id,
                    tenant_id=tenant_id,
                    upload_item_id=item_id,
                    bucket=quarantine_bucket,
                    storage_key=storage_key,
                    byte_size=byte_size,
                    status=ObjectReceiptStatus.PENDING.value,
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                uow.session.add(receipt)
                uow.commit()

            # Phase 5: the actual network write. Deliberately outside any DB transaction
            # -- this is explicitly NOT an atomic S3/PostgreSQL write.
            try:
                storage.put_object(
                    bucket=quarantine_bucket,
                    key=storage_key,
                    body=data,
                    content_type=media_type,
                    sha256_checksum=checksum,
                )
            except Exception as exc:
                with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
                    item = uow.session.scalar(
                        select(UploadItem)
                        .where(UploadItem.tenant_id == tenant_id, UploadItem.id == item_id)
                        .with_for_update()
                    )
                    assert item is not None
                    item.status = UploadItemStatus.FAILED.value
                    item.problem_code = IntakeProblemCode.UPLOAD_INTERRUPTED.value
                    item.problem_detail = f"The upload to quarantine storage failed: {exc}"
                    item.next_action = "retry_upload"
                    item.version += 1
                    item.updated_at = _utc_now()
                    rcpt = uow.session.scalar(
                        select(ObjectReceipt).where(
                            ObjectReceipt.tenant_id == tenant_id,
                            ObjectReceipt.id == document_version_id,
                        )
                    )
                    if rcpt is not None:
                        rcpt.status = ObjectReceiptStatus.ORPHANED.value
                        rcpt.updated_at = _utc_now()
                    uow.commit()
                    return item
        else:
            assert reuse_key is not None
            storage_key = reuse_key
            quarantine_bucket = reuse_bucket  # may already be the CLEAN bucket

        # Phase 6: atomic metadata + quota finalization + durable scan admission.
        with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
            item = uow.session.scalar(
                select(UploadItem)
                .where(UploadItem.tenant_id == tenant_id, UploadItem.id == item_id)
                .with_for_update()
            )
            assert item is not None

            batch = uow.session.scalar(
                select(UploadBatch)
                .where(UploadBatch.tenant_id == tenant_id, UploadBatch.id == item.batch_id)
                .with_for_update()
            )
            assert batch is not None

            if batch.reserved_total_bytes + byte_size > batch.max_total_bytes:
                item.status = UploadItemStatus.FAILED.value
                item.problem_code = IntakeProblemCode.BATCH_BYTE_LIMIT_EXCEEDED.value
                item.problem_detail = (
                    f"Admitting this {byte_size}-byte file would exceed the batch's "
                    f"{batch.max_total_bytes}-byte total limit."
                )
                item.next_action = "remove_item_or_start_new_batch"
                item.version += 1
                item.updated_at = _utc_now()

                if reuse_bucket is None:
                    rcpt = uow.session.scalar(
                        select(ObjectReceipt).where(
                            ObjectReceipt.tenant_id == tenant_id,
                            ObjectReceipt.id == document_version_id,
                        )
                    )
                    if rcpt is not None:
                        rcpt.status = ObjectReceiptStatus.ORPHANED.value
                        rcpt.updated_at = _utc_now()
                uow.commit()
                return item

            batch.reserved_total_bytes += byte_size
            batch.version += 1
            batch.updated_at = _utc_now()

            assert item.candidate_id is not None
            document = uow.session.scalar(
                select(Document).where(
                    Document.tenant_id == tenant_id, Document.candidate_id == item.candidate_id
                )
            )
            if document is None:
                document = Document(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    candidate_id=item.candidate_id,
                    latest_version_number=0,
                    created_by=actor.user_id,
                    version=1,
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                uow.session.add(document)
                uow.session.flush()

            next_version_number = document.latest_version_number + 1
            is_reused_clean = reuse_bucket is not None
            doc_version = DocumentVersion(
                id=document_version_id,
                tenant_id=tenant_id,
                document_id=document.id,
                version_number=next_version_number,
                sha256_checksum=checksum,
                byte_size=byte_size,
                media_type=media_type,
                storage_bucket=quarantine_bucket,
                storage_key=storage_key,
                storage_state=(
                    DocumentVersionStorageState.CLEAN.value
                    if is_reused_clean
                    else DocumentVersionStorageState.QUARANTINED.value
                ),
                scan_result=(
                    {"engine": "tenant_content_reuse", "infected": False}
                    if is_reused_clean
                    else None
                ),
                source_upload_item_id=item.id,
                source_actor_id=actor.user_id,
                created_at=_utc_now(),
            )
            uow.session.add(doc_version)

            document.latest_version_number = next_version_number
            document.version += 1
            document.updated_at = _utc_now()

            if reuse_bucket is None:
                rcpt = uow.session.scalar(
                    select(ObjectReceipt).where(
                        ObjectReceipt.tenant_id == tenant_id,
                        ObjectReceipt.id == document_version_id,
                    )
                )
                if rcpt is not None:
                    rcpt.status = ObjectReceiptStatus.COMMITTED.value
                    rcpt.updated_at = _utc_now()

            item.document_id = document.id
            item.document_version_id = doc_version.id
            item.reserved_bytes = byte_size
            item.version += 1
            item.updated_at = _utc_now()

            if is_reused_clean:
                item.status = UploadItemStatus.CLEAN.value
                uow.record_audit_event(
                    actor_id=actor.user_id,
                    action="UPLOAD_ITEM_CONTENT_REUSED_CLEAN",
                    resource_type="document_version",
                    resource_id=doc_version.id,
                )
                # Publish the same next-phase signal a fresh clean scan would, without
                # pretending extraction already happened.
                uow.record_outbox_event(
                    topic="documents.extraction_requested", payload_ref=doc_version.id
                )
            else:
                item.status = UploadItemStatus.SCANNING.value
                uow.admit_operation(
                    operation_type="documents.scan_requested",
                    payload={
                        "document_version_id": str(doc_version.id),
                        "upload_item_id": str(item.id),
                        "bucket": quarantine_bucket,
                        "storage_key": storage_key,
                    },
                    resource_id=doc_version.id,
                )
                uow.record_audit_event(
                    actor_id=actor.user_id,
                    action="UPLOAD_ITEM_QUARANTINED",
                    resource_type="document_version",
                    resource_id=doc_version.id,
                )

            uow.commit()
            return item

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @classmethod
    def create_item(
        cls,
        db: Session,
        actor: ActorContext,
        batch_id: UUID,
        storage: ObjectStoragePort,
        quarantine_bucket: str,
        file_obj: BinaryIO,
        declared_filename: str | None,
        declared_content_type: str | None,
        idempotency_key: str | None,
        request_hash: str | None,
        candidate_id: UUID | None = None,
        new_candidate_display_name: str | None = None,
        new_candidate_email: str | None = None,
        new_candidate_phone: str | None = None,
        new_candidate_is_draft: bool = False,
    ) -> UploadItem:
        item, is_replay = cls._reserve_slot(
            db=db,
            actor=actor,
            batch_id=batch_id,
            declared_filename=declared_filename,
            declared_content_type=declared_content_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if is_replay:
            return item

        batch_job_id = cls._get_batch_job_id(db, actor, batch_id)

        item = cls._resolve_candidate(
            db=db,
            actor=actor,
            item_id=item.id,
            batch_job_id=batch_job_id,
            candidate_id=candidate_id,
            new_candidate_display_name=new_candidate_display_name,
            new_candidate_email=new_candidate_email,
            new_candidate_phone=new_candidate_phone,
            new_candidate_is_draft=new_candidate_is_draft,
        )
        if item.status == UploadItemStatus.FAILED.value:
            return item

        max_file_bytes = cls._get_batch_max_file_bytes(db, actor, batch_id)
        try:
            data = cls._read_bounded(file_obj, max_bytes=max_file_bytes, chunk_size=256 * 1024)
            media_type, checksum = cls._validate_content(
                data, declared_content_type, declared_filename
            )
        except ContainerValidationError as exc:
            with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
                tenant_id = actor.tenant_id
                assert tenant_id is not None
                refetched = uow.session.scalar(
                    select(UploadItem)
                    .where(UploadItem.tenant_id == tenant_id, UploadItem.id == item.id)
                    .with_for_update()
                )
                assert refetched is not None
                refetched.status = UploadItemStatus.FAILED.value
                refetched.problem_code = exc.problem_code.value
                refetched.problem_detail = exc.detail
                refetched.next_action = "replace_with_a_valid_pdf_or_docx"
                refetched.version += 1
                refetched.updated_at = _utc_now()
                uow.record_audit_event(
                    actor_id=actor.user_id,
                    action="UPLOAD_ITEM_VALIDATION_FAILED",
                    resource_type="upload_item",
                    resource_id=refetched.id,
                    payload={"problem_code": exc.problem_code.value},
                )
                uow.commit()
                return refetched

        return cls._admit_content(
            db=db,
            actor=actor,
            item_id=item.id,
            storage=storage,
            quarantine_bucket=quarantine_bucket,
            data=data,
            media_type=media_type,
            checksum=checksum,
        )

    @classmethod
    def _get_batch_job_id(cls, db: Session, actor: ActorContext, batch_id: UUID) -> UUID:
        with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
            tenant_id = actor.tenant_id
            assert tenant_id is not None
            batch = uow.session.scalar(
                select(UploadBatch).where(
                    UploadBatch.tenant_id == tenant_id, UploadBatch.id == batch_id
                )
            )
            assert batch is not None
            uow.commit()
            return batch.job_id

    @classmethod
    def _get_batch_max_file_bytes(cls, db: Session, actor: ActorContext, batch_id: UUID) -> int:
        with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
            tenant_id = actor.tenant_id
            assert tenant_id is not None
            batch = uow.session.scalar(
                select(UploadBatch).where(
                    UploadBatch.tenant_id == tenant_id, UploadBatch.id == batch_id
                )
            )
            assert batch is not None
            uow.commit()
            return batch.max_file_bytes

    # ------------------------------------------------------------------
    # Retry / cancel controls (allowed processing states only)
    # ------------------------------------------------------------------

    @classmethod
    def cancel_item(
        cls, uow: UnitOfWork, actor: ActorContext, item_id: UUID, batch_id: UUID | None = None
    ) -> UploadItem:
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)

        item = uow.session.scalar(
            select(UploadItem)
            .where(UploadItem.tenant_id == tenant_id, UploadItem.id == item_id)
            .with_for_update()
        )
        if item is None or (batch_id is not None and item.batch_id != batch_id):
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested upload item was not found.",
            )
        if item.status not in UPLOAD_ITEM_CANCELABLE_STATUSES:
            raise ProblemException(
                status_code=409,
                title="Invalid Transition",
                code="INVALID_TRANSITION",
                detail=f"Item in status '{item.status}' cannot be canceled.",
            )

        item.status = UploadItemStatus.CANCELED.value
        item.next_action = None
        item.version += 1
        item.updated_at = _utc_now()
        uow.record_audit_event(
            actor_id=actor.user_id,
            action="UPLOAD_ITEM_CANCELED",
            resource_type="upload_item",
            resource_id=item.id,
        )
        return item

    @classmethod
    def retry_item(
        cls, uow: UnitOfWork, actor: ActorContext, item_id: UUID, batch_id: UUID | None = None
    ) -> UploadItem:
        """Re-admit a scan for a FAILED item whose object/metadata are still intact.

        Only permitted when the failure was scanner unavailability, not for a
        structurally invalid file or a detected infection (those require a genuinely
        different file, not a blind retry).
        """
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)

        item = uow.session.scalar(
            select(UploadItem)
            .where(UploadItem.tenant_id == tenant_id, UploadItem.id == item_id)
            .with_for_update()
        )
        if item is None or (batch_id is not None and item.batch_id != batch_id):
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested upload item was not found.",
            )
        if item.status not in UPLOAD_ITEM_RETRYABLE_STATUSES:
            raise ProblemException(
                status_code=409,
                title="Invalid Transition",
                code="INVALID_TRANSITION",
                detail=f"Item in status '{item.status}' is not retryable.",
            )
        if item.problem_code != IntakeProblemCode.SCANNER_UNAVAILABLE.value:
            raise ProblemException(
                status_code=409,
                title="Not Retryable",
                code="NOT_RETRYABLE",
                detail=(
                    "Only an item that failed due to scanner unavailability can be "
                    "retried; a structurally invalid or infected file requires a new "
                    "upload."
                ),
            )
        if item.document_version_id is None:
            raise ProblemException(
                status_code=409,
                title="Not Retryable",
                code="NOT_RETRYABLE",
                detail="Item has no admitted document version to re-scan.",
            )

        doc_version = uow.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.id == item.document_version_id,
            )
        )
        assert doc_version is not None

        item.status = UploadItemStatus.SCANNING.value
        item.problem_code = None
        item.problem_detail = None
        item.next_action = None
        item.version += 1
        item.updated_at = _utc_now()

        uow.admit_operation(
            operation_type="documents.scan_requested",
            payload={
                "document_version_id": str(doc_version.id),
                "upload_item_id": str(item.id),
                "bucket": doc_version.storage_bucket,
                "storage_key": doc_version.storage_key,
            },
            resource_id=doc_version.id,
        )
        uow.record_audit_event(
            actor_id=actor.user_id,
            action="UPLOAD_ITEM_RETRY_REQUESTED",
            resource_type="upload_item",
            resource_id=item.id,
        )
        return item


# ============================================================================
# Document metadata, authorized download, and application bindings
# ============================================================================


class DocumentService:
    """Authorized document/version metadata, proxied download, and binding commands."""

    @classmethod
    def _require_document_read_scope(cls, actor: ActorContext) -> None:
        _require_capability(actor, Capability.READ_DOCUMENTS)
        if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
            # Interviewers never receive raw resume access in this phase; only assigned
            # pack excerpts (a later phase) are in scope for them.
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Interviewers may not access raw document content in this phase.",
            )

    @classmethod
    def get_document(cls, uow: UnitOfWork, actor: ActorContext, document_id: UUID) -> Document:
        tenant_id = _verify_tenant_actor(actor)
        cls._require_document_read_scope(actor)

        document = uow.session.scalar(
            select(Document).where(Document.tenant_id == tenant_id, Document.id == document_id)
        )
        if document is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested document was not found.",
            )
        return document

    @classmethod
    def get_document_version(
        cls, uow: UnitOfWork, actor: ActorContext, document_id: UUID, version_number: int
    ) -> DocumentVersion:
        tenant_id = _verify_tenant_actor(actor)
        cls._require_document_read_scope(actor)

        cls.get_document(uow, actor, document_id)
        doc_version = uow.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_number == version_number,
            )
        )
        if doc_version is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail=f"Document version {version_number} was not found.",
            )
        return doc_version

    @classmethod
    def get_downloadable_version(
        cls, uow: UnitOfWork, actor: ActorContext, document_id: UUID, version_number: int
    ) -> DocumentVersion:
        doc_version = cls.get_document_version(uow, actor, document_id, version_number)
        if doc_version.storage_state not in DOCUMENT_VERSION_DOWNLOADABLE_STATES:
            raise ProblemException(
                status_code=409,
                title="Document Not Available",
                code="DOCUMENT_NOT_CLEAN",
                detail=(
                    f"Document version is in storage state '{doc_version.storage_state}' "
                    "and cannot be downloaded until it has passed malware scanning."
                ),
            )
        return doc_version

    # ------------------------------------------------------------------
    # Application <-> document version bindings
    # ------------------------------------------------------------------

    @classmethod
    def bind_document_to_application(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        application_id: UUID,
        document_version_id: UUID,
        expected_binding_generation: int,
    ) -> ApplicationDocument:
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)

        if not actor.is_human:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Automated, worker, or AI-model principals cannot bind resume versions.",
            )

        application = uow.session.scalar(
            select(Application)
            .where(Application.tenant_id == tenant_id, Application.id == application_id)
            .with_for_update()
        )
        if application is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested application was not found.",
            )

        JobService.get_job_with_authority(
            uow=uow, actor=actor, job_id=application.job_id, allowed_roles=_INTAKE_AUTHORITY_ROLES
        )

        doc_version = uow.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == document_version_id
            )
        )
        if doc_version is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested document version was not found.",
            )
        if doc_version.storage_state not in DOCUMENT_VERSION_DOWNLOADABLE_STATES:
            raise ProblemException(
                status_code=409,
                title="Document Not Available",
                code="DOCUMENT_NOT_CLEAN",
                detail="Only a CLEAN document version may be bound to an application.",
            )

        document = uow.session.scalar(
            select(Document).where(
                Document.tenant_id == tenant_id, Document.id == doc_version.document_id
            )
        )
        assert document is not None
        if document.candidate_id != application.candidate_id:
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="EVIDENCE_INVALID",
                detail="This document version belongs to a different candidate's record.",
            )

        current_active = uow.session.scalar(
            select(ApplicationDocument).where(
                ApplicationDocument.tenant_id == tenant_id,
                ApplicationDocument.application_id == application_id,
                ApplicationDocument.status == ApplicationDocumentStatus.ACTIVE.value,
            )
        )
        current_generation = current_active.binding_generation if current_active else 0
        if expected_binding_generation != current_generation:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=(
                    f"Binding was modified concurrently. Expected generation "
                    f"{expected_binding_generation}, but current generation is "
                    f"{current_generation}."
                ),
            )

        if current_active is not None:
            if current_active.document_version_id == document_version_id:
                raise ProblemException(
                    status_code=409,
                    title="No-op Binding",
                    code="INVALID_TRANSITION",
                    detail="This document version is already the active binding.",
                )
            current_active.status = ApplicationDocumentStatus.SUPERSEDED.value

        now = _utc_now()
        binding = ApplicationDocument(
            id=uuid4(),
            tenant_id=tenant_id,
            application_id=application_id,
            document_id=document.id,
            document_version_id=document_version_id,
            binding_generation=current_generation + 1,
            status=ApplicationDocumentStatus.ACTIVE.value,
            bound_by=actor.user_id,
            bound_at=now,
            created_at=now,
        )
        uow.session.add(binding)
        uow.session.flush()

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="APPLICATION_DOCUMENT_BOUND",
            resource_type="application_document",
            resource_id=binding.id,
            payload={
                "application_id": str(application_id),
                "document_version_id": str(document_version_id),
                "binding_generation": binding.binding_generation,
            },
        )

        # Downstream summaries/screening/packs (later phases) must treat their cached
        # inputs as stale once the bound resume version changes.
        uow.record_outbox_event(topic="applications.input_version_changed", payload_ref=binding.id)

        return binding

    @classmethod
    def get_active_binding(
        cls, uow: UnitOfWork, actor: ActorContext, application_id: UUID
    ) -> ApplicationDocument | None:
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.READ_APPLICATION)

        return uow.session.scalar(
            select(ApplicationDocument).where(
                ApplicationDocument.tenant_id == tenant_id,
                ApplicationDocument.application_id == application_id,
                ApplicationDocument.status == ApplicationDocumentStatus.ACTIVE.value,
            )
        )


__all__ = [
    "DocumentService",
    "UploadBatchService",
    "UploadItemService",
]
