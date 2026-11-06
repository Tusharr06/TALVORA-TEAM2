"""Extraction Worker: Claims, Runs, and Finalizes a `documents.extraction_requested` Job.

Uses the exact same P02 `TaskExecutionManager` fencing-token lease protocol as
`talvora.modules.documents.application.scan_worker.DocumentScanWorker` -- a crashed or
restarted worker never silently duplicates work, because:

- An `ExtractionRun` already in a terminal status (`SUCCEEDED`/`PARTIAL`/
  `NEEDS_REVIEW`/`FAILED`/`CANCELED`) is never re-extracted; the idempotency guard
  below returns success immediately, mirroring `DocumentScanWorker`'s
  `storage_state != QUARANTINED` check.
- `SourceSegment`/`EvidenceFragment` rows for one run are only ever written in a
  single flush at the very end of a successful extraction attempt (`_persist_outcome`
  below) -- so a crash mid-extraction leaves zero partial segments behind, and a
  restarted attempt against the same `extraction_run_id` simply redoes the
  (idempotent, side-effect-free) extraction and flushes once. The unique
  `(tenant_id, extraction_run_id, segment_index)` constraint is a second line of
  defense against any accidental double-insert.

A non-`SUCCEEDED`/`PARTIAL`/`NEEDS_REVIEW`/`FAILED` extraction *outcome* still counts
as a *successfully completed worker task* (the `JobOperation` finalizes as
`SUCCEEDED`) -- exactly as `DocumentScanWorker` finalizes `SUCCEEDED` even for an
infected-verdict scan. Only a genuine worker-level fault (the object store is
unreachable) is treated as transient/retryable, never a fabricated success.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.config import Settings
from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.documents.domain.models import DocumentVersion, DocumentVersionStorageState
from talvora.modules.evidence.application.extractor import default_extraction_limits
from talvora.modules.evidence.application.normalization import canonical_text_sha256
from talvora.modules.evidence.application.ports import DocumentExtractor, ExtractionOutcome
from talvora.modules.evidence.domain.models import (
    EXTRACTION_RUN_TERMINAL_STATUSES,
    EvidenceFragment,
    ExtractionRun,
    ExtractionRunStatus,
    SourceSegment,
)
from talvora.modules.operations.domain.models import JobOperation, OperationStatus
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.workers.task_handlers import StaleFencingTokenError, TaskExecutionManager


class ExtractionWorker:
    """Executes one `documents.extraction_requested` JobOperation."""

    def __init__(
        self,
        storage: ObjectStoragePort,
        extractor: DocumentExtractor,
        settings: Settings,
        clock: Clock | None = None,
        lease_seconds: int = 120,
    ) -> None:
        self.storage = storage
        self.extractor = extractor
        self.settings = settings
        self.clock = clock or SystemClock()
        self.manager = TaskExecutionManager(clock=self.clock, lease_seconds=lease_seconds)

    def run_once(
        self,
        session: Session,
        tenant_id: UUID,
        job_id: UUID,
        worker_id: str,
    ) -> JobOperation:
        op, token = self.manager.claim_job(session, tenant_id, job_id, worker_id)
        if op.status != OperationStatus.RUNNING.value:
            return op  # already terminal (SUCCEEDED/CANCELED); nothing to do.

        extraction_run_id = UUID(op.payload["extraction_run_id"])
        document_version_id = UUID(op.payload["document_version_id"])

        run = session.scalar(
            sa.select(ExtractionRun).where(
                ExtractionRun.tenant_id == tenant_id, ExtractionRun.id == extraction_run_id
            )
        )
        doc_version = session.scalar(
            sa.select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == document_version_id
            )
        )
        if run is None or doc_version is None:
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.FAILED,
                error_detail={
                    "code": "RESOURCE_MISSING",
                    "detail": "Extraction run or document version not found.",
                },
            )

        # Idempotency guard: a worker restart must not re-extract a run already given
        # a terminal outcome, nor create duplicate segments.
        if run.status in EXTRACTION_RUN_TERMINAL_STATUSES:
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.SUCCEEDED,
                result={"already_finalized": True, "extraction_status": run.status},
            )

        if doc_version.storage_state != DocumentVersionStorageState.CLEAN.value:
            run.status = ExtractionRunStatus.FAILED.value
            run.problem_code = "DOCUMENT_UNREADABLE"
            run.error_detail = (
                f"Document version is in storage state '{doc_version.storage_state}', "
                "not CLEAN; cannot be extracted."
            )
            run.completed_at = self.clock.now()
            run.version += 1
            session.flush()
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.SUCCEEDED,
                result={"extraction_status": run.status},
            )

        now = self.clock.now()
        run.status = ExtractionRunStatus.RUNNING.value
        run.started_at = run.started_at or now
        run.version += 1
        session.flush()

        try:
            stream = self.storage.get_object_stream(
                doc_version.storage_bucket, doc_version.storage_key
            )
            data = stream.read()
        except Exception as exc:
            return self._handle_storage_unavailable(
                session, tenant_id, job_id, token, run, str(exc)
            )

        limits = default_extraction_limits(self.settings)
        outcome = self.extractor.extract(data, doc_version.media_type, limits)

        self._persist_outcome(session, tenant_id, run, outcome)

        try:
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.SUCCEEDED,
                result={"extraction_status": run.status},
            )
        except StaleFencingTokenError:
            raise

    def _persist_outcome(
        self,
        session: Session,
        tenant_id: UUID,
        run: ExtractionRun,
        outcome: ExtractionOutcome,
    ) -> None:
        segments: list[SourceSegment] = []
        for index, extracted in enumerate(outcome.segments):
            text = extracted.text
            text_hash = canonical_text_sha256(text)
            # Defense-in-depth: the hash we are about to persist must match the exact
            # bytes we are about to persist as `text` -- never trust the adapter blindly.
            assert hashlib.sha256(text.encode("utf-8")).hexdigest() == text_hash

            segment = SourceSegment(
                tenant_id=tenant_id,
                extraction_run_id=run.id,
                document_version_id=run.document_version_id,
                segment_index=index,
                locator_type=extracted.locator_type.value,
                page_number=extracted.page_number,
                docx_path=extracted.docx_path,
                extraction_method=extracted.extraction_method.value,
                text=text,
                char_count=len(text),
                canonical_text_sha256=text_hash,
                is_low_confidence=extracted.is_low_confidence,
                ocr_mean_confidence=extracted.ocr_mean_confidence,
                warnings=list(extracted.warnings),
            )
            session.add(segment)
            segments.append(segment)

        # Segments must have real primary keys (assigned on flush/insert) before any
        # fragment can reference one via `source_segment_id` -- so flush all segments
        # first, then build fragments in a second pass.
        session.flush()

        for segment in segments:
            text = segment.text
            if not text:
                continue
            # One bounded, contiguous, whole-segment evidence fragment per non-empty
            # segment: the base citation anchor future fact-extraction (P08+) narrows
            # into sub-spans of. Verify the exact substring invariant before ever
            # constructing the row.
            start_offset, end_offset = 0, len(text)
            quote = text[start_offset:end_offset]
            assert quote == text and len(quote) == end_offset - start_offset
            session.add(
                EvidenceFragment(
                    tenant_id=tenant_id,
                    source_segment_id=segment.id,
                    extraction_run_id=run.id,
                    document_version_id=run.document_version_id,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    quote=quote,
                )
            )

        run.status = outcome.status.value
        run.problem_code = outcome.problem_code
        run.is_complete = outcome.is_complete
        run.warnings = list(outcome.warnings)
        run.error_detail = outcome.error_detail
        run.total_pages = outcome.total_pages
        run.processed_pages = outcome.processed_pages
        run.ocr_pages_used = outcome.ocr_pages_used
        run.total_characters = sum(len(s.text) for s in outcome.segments)
        run.completed_at = self.clock.now()
        run.version += 1
        session.flush()

    def _handle_storage_unavailable(
        self,
        session: Session,
        tenant_id: UUID,
        job_id: UUID,
        token: int,
        run: ExtractionRun,
        reason: str,
    ) -> JobOperation:
        """Object store unreachable: a worker-level fault, never a document verdict."""
        op = session.scalar(
            sa.select(JobOperation)
            .where(JobOperation.tenant_id == tenant_id, JobOperation.id == job_id)
            .with_for_update()
        )
        assert op is not None
        if op.fencing_token != token:
            raise StaleFencingTokenError(f"Stale fencing token for operation {job_id}.")

        now = self.clock.now()
        op.retry_count += 1
        if op.retry_count >= op.max_retries:
            op.status = OperationStatus.NEEDS_REVIEW.value
            op.error_detail = {"code": "STORAGE_UNAVAILABLE_MAX_RETRIES", "detail": reason}
            op.lease_expires_at = None
            run.status = ExtractionRunStatus.NEEDS_REVIEW.value
            run.problem_code = "DOCUMENT_UNREADABLE"
            run.error_detail = f"Object storage unavailable after repeated attempts: {reason}"
            run.completed_at = now
            run.version += 1
        else:
            backoff_seconds = min(300, 2**op.retry_count * 5)
            op.status = OperationStatus.RETRY_WAIT.value
            op.next_attempt_at = now + timedelta(seconds=backoff_seconds)
            op.error_detail = {"code": "STORAGE_UNAVAILABLE", "detail": reason}
            op.lease_expires_at = None
            # Run stays RUNNING -- pending/retryable, never marked terminal.
        op.updated_at = now
        op.version += 1
        session.flush()
        return op


__all__ = ["ExtractionWorker"]
