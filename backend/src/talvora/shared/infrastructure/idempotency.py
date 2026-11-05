"""Tenant-Scoped Idempotency Service.

Binds Idempotency-Key headers to canonical SHA-256 request hashes within
the active tenant. Enforces 409 conflicts on mismatched payloads and returns
cached responses on matching replays without bypassing authorization.
"""

import hashlib
import json
from datetime import UTC, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.operations.domain.models import IdempotencyRecord
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.shared.transport.problems import ProblemException


def compute_request_hash(payload: Any) -> str:
    """Computes a deterministic SHA-256 hash of canonical JSON request payload."""
    if payload is None:
        payload_bytes = b""
    elif isinstance(payload, bytes):
        payload_bytes = payload
    elif isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    else:
        # Sort keys for canonical representation
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    return hashlib.sha256(payload_bytes).hexdigest()


class IdempotencyService:
    """Manages idempotency verification and response caching."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()

    def check_idempotency(
        self,
        session: Session,
        tenant_id: UUID,
        route: str,
        idempotency_key: str,
        request_hash: str,
    ) -> IdempotencyRecord | None:
        """Checks for existing idempotency record.

        Returns existing record if found with matching hash.
        Raises 409 ProblemException if key exists with different hash.
        Returns None if key has not been used.
        """
        now = self.clock.now()

        stmt = sa.select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.route == route,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        record = session.scalar(stmt)

        if record is None:
            return None

        # Check if record is expired
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if expires_at < now:
            session.delete(record)
            session.flush()
            return None

        # If payload hash differs, raise 409 Conflict
        if record.request_hash != request_hash:
            raise ProblemException(
                status_code=409,
                title="Idempotency Conflict",
                code="IDEMPOTENCY_CONFLICT",
                detail=(
                    f"Idempotency key '{idempotency_key}' has already been used "
                    "for a different request payload on this route."
                ),
            )

        return record

    def save_idempotency_record(
        self,
        session: Session,
        tenant_id: UUID,
        route: str,
        idempotency_key: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
        principal_id: UUID | None = None,
        ttl_hours: int = 24,
    ) -> IdempotencyRecord:
        """Stores response in idempotency ledger upon successful execution."""
        now = self.clock.now()
        expires_at = now + timedelta(hours=ttl_hours)

        record = IdempotencyRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            principal_id=principal_id,
            route=route,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_status=response_status,
            response_body=response_body,
            created_at=now,
            expires_at=expires_at,
        )
        session.add(record)
        return record
