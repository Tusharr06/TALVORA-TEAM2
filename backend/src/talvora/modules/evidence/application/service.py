"""Evidence Application Services: Extraction Admission, Source Text, and Fragments.

`ExtractionAdmissionService.request_extraction` is the one place that creates a new
`ExtractionRun` revision plus its durable `JobOperation`/`OutboxEvent`/audit trail. It
intentionally takes a raw SQLAlchemy `Session` (not a `UnitOfWork`) so it can be
called equally from `DocumentScanWorker` (P06, which has no `UnitOfWork` of its own
at that call site) and from the `UnitOfWork`-based reprocess API endpoint below --
mirroring exactly how `talvora.shared.infrastructure.uow.UnitOfWork.admit_operation`
builds a `JobOperation` + `OutboxEvent` + audit entry atomically, just without
requiring a full `UnitOfWork` wrapper.

This module deliberately does NOT import `talvora.modules.documents.application.
service` (the `documents` module's own services, in turn, call back into
`ExtractionAdmissionService.request_extraction` to trigger extraction right after a
clean scan -- importing `DocumentService` here would create an import cycle between
the two modules). Instead, the small tenant-scoped `document_version` lookup these
services need is implemented directly against `talvora.modules.documents.domain.
models` (a pure-data import, not a cycle) -- duplicating a few lines of the lookup
already in `DocumentService.get_document_version`, in exchange for keeping both
modules' *service* layers acyclic.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from talvora.modules.audit.domain.models import AuditEvent
from talvora.modules.documents.domain.models import (
    DOCUMENT_VERSION_DOWNLOADABLE_STATES,
    DocumentVersion,
)
from talvora.modules.evidence.application.extractor import extractor_version_for_media_type
from talvora.modules.evidence.application.normalization import NORMALIZATION_VERSION
from talvora.modules.evidence.domain.models import (
    EXTRACTION_RUN_READABLE_STATUSES,
    EvidenceFragment,
    ExtractionRun,
    ExtractionRunStatus,
    ExtractionTriggerKind,
    SourceSegment,
)
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.operations.domain.models import (
    JobOperation,
    OperationStatus,
    OutboxEvent,
    OutboxStatus,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

_EXTRACTION_TOPIC = "documents.extraction_requested"


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


def _require_evidence_read_scope(actor: ActorContext) -> None:
    """Source text/evidence excerpts require application/job document-read scope.

    Mirrors `DocumentService._require_document_read_scope`: interviewers never get raw
    source text or fragment excerpts in this phase (only an assigned pack excerpt, a
    later phase, would authorize that narrowly).
    """
    _require_capability(actor, Capability.READ_DOCUMENTS)
    if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="CAPABILITY_DENIED",
            detail="Interviewers may not access raw source text or evidence excerpts in this phase.",
        )


def _get_tenant_scoped_document_version(
    session: Session, tenant_id: UUID, document_id: UUID, version_number: int
) -> DocumentVersion:
    """Tenant-scoped lookup mirroring `DocumentService.get_document_version`'s 404
    semantics, duplicated here to keep the `evidence`/`documents` service layers
    acyclic (see module docstring)."""
    doc_version = session.scalar(
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


class ExtractionInProgressError(ProblemException):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            title="Extraction In Progress",
            code="EXTRACTION_IN_PROGRESS",
            detail="A previous extraction attempt for this document version has not finished yet.",
        )


class ExtractionAdmissionService:
    """Admits a new, versioned extraction attempt for a clean document version."""

    @classmethod
    def request_extraction(
        cls,
        session: Session,
        tenant_id: UUID,
        document_id: UUID,
        document_version: DocumentVersion,
        triggered_by: ExtractionTriggerKind,
        requested_by_actor_id: UUID | None = None,
        clock: Clock | None = None,
        raise_if_in_progress: bool = False,
    ) -> ExtractionRun:
        """Create the next immutable extraction revision for `document_version`.

        If a prior run for this exact document version is still PENDING/RUNNING,
        either silently returns it unchanged (the default, used by the automatic
        scan-clean/content-reuse triggers, which cannot otherwise race themselves)
        or raises `ExtractionInProgressError` when `raise_if_in_progress=True` (used
        by the explicit human-initiated reprocess command, so a duplicate request
        gets an actionable conflict instead of being silently dropped).
        """
        clock = clock or SystemClock()
        now = clock.now()

        latest = session.scalar(
            select(ExtractionRun)
            .where(
                ExtractionRun.tenant_id == tenant_id,
                ExtractionRun.document_version_id == document_version.id,
            )
            .order_by(ExtractionRun.revision_number.desc())
            .limit(1)
        )
        if latest is not None and latest.status in (
            ExtractionRunStatus.PENDING.value,
            ExtractionRunStatus.RUNNING.value,
        ):
            if raise_if_in_progress:
                raise ExtractionInProgressError()
            return latest

        next_revision = (latest.revision_number if latest is not None else 0) + 1
        run = ExtractionRun(
            id=uuid4(),
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=document_version.id,
            revision_number=next_revision,
            extractor_version=extractor_version_for_media_type(document_version.media_type),
            normalization_version=NORMALIZATION_VERSION,
            media_type=document_version.media_type,
            status=ExtractionRunStatus.PENDING.value,
            is_complete=False,
            warnings=[],
            triggered_by=triggered_by.value,
            requested_by_actor_id=requested_by_actor_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.flush()

        op_id = uuid4()
        job_operation = JobOperation(
            id=op_id,
            tenant_id=tenant_id,
            operation_type=_EXTRACTION_TOPIC,
            status=OperationStatus.PENDING.value,
            resource_id=run.id,
            fencing_token=0,
            retry_count=0,
            max_retries=5,
            payload={
                "document_version_id": str(document_version.id),
                "extraction_run_id": str(run.id),
            },
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(job_operation)

        session.add(
            OutboxEvent(
                id=uuid4(),
                tenant_id=tenant_id,
                topic=_EXTRACTION_TOPIC,
                payload_ref=op_id,
                status=OutboxStatus.PENDING.value,
                created_at=now,
            )
        )
        session.add(
            AuditEvent(
                id=uuid4(),
                tenant_id=tenant_id,
                actor_id=requested_by_actor_id,
                actor_capability=None,
                resource_type="job_operation",
                resource_id=op_id,
                action="OPERATION_ADMITTED",
                correlation_id=None,
                timestamp=now,
                details={"operation_type": _EXTRACTION_TOPIC, "resource_id": str(run.id)},
            )
        )

        run.job_operation_id = op_id
        session.flush()
        return run

    @classmethod
    def reprocess_document_version(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        document_id: UUID,
        version_number: int,
    ) -> ExtractionRun:
        """Explicit, human-initiated POST /documents/{id}/reprocess command."""
        tenant_id = _verify_tenant_actor(actor)
        _require_capability(actor, Capability.MANAGE_DOCUMENT_INTAKE)
        if not actor.is_human:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Automated, worker, or AI-model principals cannot request a reprocess.",
            )

        doc_version = _get_tenant_scoped_document_version(
            session=uow.session,
            tenant_id=tenant_id,
            document_id=document_id,
            version_number=version_number,
        )
        if doc_version.storage_state not in DOCUMENT_VERSION_DOWNLOADABLE_STATES:
            raise ProblemException(
                status_code=409,
                title="Document Not Available",
                code="DOCUMENT_NOT_CLEAN",
                detail=(
                    f"Document version is in storage state '{doc_version.storage_state}' "
                    "and cannot be (re)processed until it has passed malware scanning."
                ),
            )

        run = cls.request_extraction(
            session=uow.session,
            tenant_id=tenant_id,
            document_id=doc_version.document_id,
            document_version=doc_version,
            triggered_by=ExtractionTriggerKind.MANUAL_REPROCESS,
            requested_by_actor_id=actor.user_id,
            raise_if_in_progress=True,
        )
        uow.record_audit_event(
            actor_id=actor.user_id,
            action="DOCUMENT_REPROCESS_REQUESTED",
            resource_type="extraction_run",
            resource_id=run.id,
            payload={
                "document_version_id": str(doc_version.id),
                "revision_number": run.revision_number,
            },
        )
        return run


class SourceService:
    """Authorized, tenant-scoped read access to one extraction run's source text."""

    @classmethod
    def get_source(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        document_id: UUID,
        version_number: int,
        revision_number: int | None = None,
    ) -> tuple[DocumentVersion, ExtractionRun | None, list[SourceSegment]]:
        tenant_id = _verify_tenant_actor(actor)
        _require_evidence_read_scope(actor)

        doc_version = _get_tenant_scoped_document_version(
            session=uow.session,
            tenant_id=tenant_id,
            document_id=document_id,
            version_number=version_number,
        )

        stmt = select(ExtractionRun).where(
            ExtractionRun.tenant_id == tenant_id,
            ExtractionRun.document_version_id == doc_version.id,
        )
        if revision_number is not None:
            stmt = stmt.where(ExtractionRun.revision_number == revision_number)
        else:
            stmt = stmt.order_by(ExtractionRun.revision_number.desc())
        run = uow.session.scalar(stmt.limit(1))

        if run is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="EXTRACTION_NOT_FOUND",
                detail="No extraction has been requested for this document version yet.",
            )

        segments: list[SourceSegment] = []
        if run.status in EXTRACTION_RUN_READABLE_STATUSES:
            segments = list(
                uow.session.scalars(
                    select(SourceSegment)
                    .where(
                        SourceSegment.tenant_id == tenant_id,
                        SourceSegment.extraction_run_id == run.id,
                    )
                    .order_by(SourceSegment.segment_index.asc())
                ).all()
            )
        return doc_version, run, segments

    @classmethod
    def get_latest_extraction_status(
        cls, uow: UnitOfWork, actor: ActorContext, document_id: UUID, version_number: int
    ) -> tuple[DocumentVersion, ExtractionRun | None]:
        tenant_id = _verify_tenant_actor(actor)
        _require_evidence_read_scope(actor)

        doc_version = _get_tenant_scoped_document_version(
            session=uow.session,
            tenant_id=tenant_id,
            document_id=document_id,
            version_number=version_number,
        )
        run = uow.session.scalar(
            select(ExtractionRun)
            .where(
                ExtractionRun.tenant_id == tenant_id,
                ExtractionRun.document_version_id == doc_version.id,
            )
            .order_by(ExtractionRun.revision_number.desc())
            .limit(1)
        )
        return doc_version, run


class EvidenceService:
    """Authorized, tenant-scoped read access to one immutable evidence fragment."""

    @classmethod
    def get_fragment(
        cls, uow: UnitOfWork, actor: ActorContext, fragment_id: UUID
    ) -> tuple[EvidenceFragment, SourceSegment]:
        tenant_id = _verify_tenant_actor(actor)
        _require_evidence_read_scope(actor)

        fragment = uow.session.scalar(
            select(EvidenceFragment).where(
                EvidenceFragment.tenant_id == tenant_id, EvidenceFragment.id == fragment_id
            )
        )
        if fragment is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested evidence fragment was not found.",
            )

        segment = uow.session.scalar(
            select(SourceSegment).where(
                SourceSegment.tenant_id == tenant_id, SourceSegment.id == fragment.source_segment_id
            )
        )
        assert segment is not None, "evidence_fragment FK guarantees its segment exists"

        # Defense-in-depth re-verification (not just at write time): the quote must
        # still be an exact substring of the segment's immutable, hash-verified text.
        if segment.text[fragment.start_offset : fragment.end_offset] != fragment.quote:
            raise ProblemException(
                status_code=500,
                title="Evidence Invalid",
                code="EVIDENCE_INVALID",
                detail="Stored evidence fragment no longer matches its source segment text.",
            )

        return fragment, segment


__all__ = [
    "EvidenceService",
    "ExtractionAdmissionService",
    "ExtractionInProgressError",
    "SourceService",
]
