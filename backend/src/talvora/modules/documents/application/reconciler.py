"""Object Receipt Reconciler: Orphaned Quarantine Object Cleanup.

Bridges the object-store/database dual-write gap documented on `ObjectReceipt`. A
receipt stays `PENDING` only for the short window between the quarantine-bucket write
and the owning database transaction's commit; if that owning transaction never commits
(a crash, a dropped connection, a batch-byte-limit rejection), the receipt is left
behind as the only durable record that bytes were retained under that key. This sweep
is the documented, idempotent process that finds such orphans and deletes the
underlying object, keeping storage from leaking an unaccounted-for file.

Run this periodically out-of-band (a scheduled job/cron invocation of
`ObjectReceiptReconciler.sweep`) -- never automatically inside a request path, since the
grace period exists specifically to avoid racing an in-flight admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.documents.domain.models import (
    DocumentVersion,
    ObjectReceipt,
    ObjectReceiptStatus,
)
from talvora.shared.domain.clock import Clock, SystemClock


@dataclass
class ReconcilerReport:
    scanned: int = 0
    cleaned_up: int = 0
    already_committed: int = 0


class ObjectReceiptReconciler:
    """Sweeps stale `PENDING` object receipts and deletes their orphaned objects."""

    def __init__(self, storage: ObjectStoragePort, clock: Clock | None = None) -> None:
        self.storage = storage
        self.clock = clock or SystemClock()

    def sweep(
        self,
        session: Session,
        tenant_id: UUID | None = None,
        grace_period: timedelta = timedelta(minutes=30),
        batch_size: int = 100,
    ) -> ReconcilerReport:
        report = ReconcilerReport()
        cutoff = self.clock.now() - grace_period
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

        stmt = (
            sa.select(ObjectReceipt)
            .where(
                ObjectReceipt.status == ObjectReceiptStatus.PENDING.value,
                ObjectReceipt.created_at < cutoff,
            )
            .order_by(ObjectReceipt.created_at.asc())
            .limit(batch_size)
        )
        if tenant_id is not None:
            stmt = stmt.where(ObjectReceipt.tenant_id == tenant_id)

        receipts = list(session.scalars(stmt).all())
        for receipt in receipts:
            report.scanned += 1

            # Defense in depth: a receipt may have been committed (DocumentVersion now
            # exists) without the receipt row itself being updated due to a prior
            # partial failure -- never delete an object a committed version still owns.
            owning_version = session.scalar(
                sa.select(DocumentVersion).where(
                    DocumentVersion.tenant_id == receipt.tenant_id, DocumentVersion.id == receipt.id
                )
            )
            if owning_version is not None and owning_version.storage_key == receipt.storage_key:
                receipt.status = ObjectReceiptStatus.COMMITTED.value
                receipt.updated_at = datetime.now(UTC)
                report.already_committed += 1
                continue

            self.storage.delete_object(receipt.bucket, receipt.storage_key)
            receipt.status = ObjectReceiptStatus.CLEANED_UP.value
            receipt.updated_at = datetime.now(UTC)
            report.cleaned_up += 1

        session.flush()
        return report


__all__ = ["ObjectReceiptReconciler", "ReconcilerReport"]
