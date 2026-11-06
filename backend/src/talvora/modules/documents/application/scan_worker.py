"""Real ClamAV Scan Worker: Claims, Scans, and Finalizes a `documents.scan_requested` Job.

Uses the P02 `TaskExecutionManager` fencing-token lease protocol (`claim_job` /
`finalize_job`) exactly as `execute_test_diagnostic_task` does, so a scan operation is
subject to the same crash-safe lease recovery and retry/backoff machinery as any other
durable background operation. There is no code path here that marks a document CLEAN
without a real, successful clamd verdict: scanner-unavailable and timeout both leave the
document QUARANTINED and the operation PENDING/RETRY_WAIT (or NEEDS_REVIEW after
exhausting retries), never CLEAN.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.documents.application.ports import (
    ObjectStoragePort,
    ScannerUnavailableError,
    VirusScannerPort,
)
from talvora.modules.documents.domain.models import (
    DocumentVersion,
    DocumentVersionStorageState,
    IntakeProblemCode,
    UploadItem,
    UploadItemStatus,
)
from talvora.modules.evidence.application.service import ExtractionAdmissionService
from talvora.modules.evidence.domain.models import ExtractionTriggerKind
from talvora.modules.operations.domain.models import JobOperation, OperationStatus
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.workers.task_handlers import StaleFencingTokenError, TaskExecutionManager


class DocumentScanWorker:
    """Executes one `documents.scan_requested` JobOperation against real ClamAV."""

    def __init__(
        self,
        storage: ObjectStoragePort,
        scanner: VirusScannerPort,
        clean_bucket: str,
        clock: Clock | None = None,
        lease_seconds: int = 60,
    ) -> None:
        self.storage = storage
        self.scanner = scanner
        self.clean_bucket = clean_bucket
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

        document_version_id = UUID(op.payload["document_version_id"])
        upload_item_id = UUID(op.payload["upload_item_id"])
        bucket = op.payload["bucket"]
        storage_key = op.payload["storage_key"]

        doc_version = session.scalar(
            sa.select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == document_version_id
            )
        )
        item = session.scalar(
            sa.select(UploadItem).where(
                UploadItem.tenant_id == tenant_id, UploadItem.id == upload_item_id
            )
        )
        if doc_version is None or item is None:
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.FAILED,
                error_detail={
                    "code": "RESOURCE_MISSING",
                    "detail": "Document version or item not found.",
                },
            )

        # Idempotency guard: a worker restart must not re-scan or re-finalize a version
        # already given a verdict.
        if doc_version.storage_state != DocumentVersionStorageState.QUARANTINED.value:
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.SUCCEEDED,
                result={"already_finalized": True, "storage_state": doc_version.storage_state},
            )

        try:
            stream = self.storage.get_object_stream(bucket, storage_key)
            data = stream.read()
            verdict = self.scanner.scan_stream(data)
        except ScannerUnavailableError as exc:
            return self._handle_scanner_unavailable(
                session, tenant_id, job_id, token, item, str(exc)
            )

        if verdict.infected:
            doc_version.storage_state = DocumentVersionStorageState.BLOCKED_INFECTED.value
            doc_version.scan_result = {
                "engine": verdict.engine,
                "infected": True,
                "signature_name": verdict.signature_name,
                "scanned_at": verdict.scanned_at_iso,
            }
            item.status = UploadItemStatus.BLOCKED.value
            item.problem_code = IntakeProblemCode.MALWARE_DETECTED.value
            item.problem_detail = f"Malware signature '{verdict.signature_name}' detected."
            item.next_action = "remove_item_and_report"
            item.version += 1
            session.flush()
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.SUCCEEDED,
                result={"infected": True, "signature_name": verdict.signature_name},
            )

        # Clean verdict: promote quarantine -> clean bucket, never parse/extract here.
        self.storage.copy_object(bucket, storage_key, self.clean_bucket, storage_key)
        self.storage.delete_object(bucket, storage_key)

        doc_version.storage_bucket = self.clean_bucket
        doc_version.storage_state = DocumentVersionStorageState.CLEAN.value
        doc_version.scan_result = {
            "engine": verdict.engine,
            "infected": False,
            "scanned_at": verdict.scanned_at_iso,
        }
        item.status = UploadItemStatus.CLEAN.value
        item.problem_code = None
        item.problem_detail = None
        item.next_action = None
        item.version += 1
        session.flush()

        # Request the first extraction revision for this now-clean version (P07). This
        # creates the durable JobOperation an ExtractionWorker claims/fences exactly
        # like this scan operation was claimed/fenced -- never a bare, unclaimable
        # outbox signal.
        ExtractionAdmissionService.request_extraction(
            session=session,
            tenant_id=tenant_id,
            document_id=doc_version.document_id,
            document_version=doc_version,
            triggered_by=ExtractionTriggerKind.SYSTEM_SCAN_CLEAN,
            clock=self.clock,
        )

        try:
            return self.manager.finalize_job(
                session,
                tenant_id,
                job_id,
                token,
                OperationStatus.SUCCEEDED,
                result={"infected": False},
            )
        except StaleFencingTokenError:
            raise

    def _handle_scanner_unavailable(
        self,
        session: Session,
        tenant_id: UUID,
        job_id: UUID,
        token: int,
        item: UploadItem,
        reason: str,
    ) -> JobOperation:
        """Scanner unavailable/timeout: never clean, leaves work pending/retryable."""
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
            op.error_detail = {"code": "SCANNER_UNAVAILABLE_MAX_RETRIES", "detail": reason}
            op.lease_expires_at = None
            item.status = UploadItemStatus.FAILED.value
            item.problem_code = IntakeProblemCode.SCANNER_UNAVAILABLE.value
            item.problem_detail = (
                "The malware scanner was unavailable after repeated attempts. "
                "A reviewer may retry once the scanner is healthy."
            )
            item.next_action = "retry_once_scanner_is_healthy"
            item.version += 1
        else:
            backoff_seconds = min(300, 2**op.retry_count * 5)
            op.status = OperationStatus.RETRY_WAIT.value
            op.next_attempt_at = now + timedelta(seconds=backoff_seconds)
            op.error_detail = {"code": "SCANNER_UNAVAILABLE", "detail": reason}
            op.lease_expires_at = None
            # Item remains SCANNING -- pending/retryable, never marked clean or failed.
        op.updated_at = now
        op.version += 1
        session.flush()
        return op
