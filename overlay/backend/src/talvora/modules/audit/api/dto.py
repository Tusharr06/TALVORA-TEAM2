"""Typed DTOs for Audit Event Query and Restricted Export (P17)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AuditEventResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    actor_id: UUID | None
    actor_type: str
    actor_capability: str | None
    resource_type: str
    resource_id: UUID | None
    action: str
    correlation_id: str | None
    timestamp: datetime
    details: dict[str, Any]


class AuditEventPageResponse(BaseModel):
    items: list[AuditEventResponse]
    next_cursor: str | None
    has_more: bool
    governance_disclosure: str


class AuditEventExportResponse(BaseModel):
    exported_at: datetime
    count: int
    governance_disclosure: str
    items: list[AuditEventResponse]
