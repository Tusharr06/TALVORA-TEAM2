"""FastAPI Routes for Minimized Append-Only Audit Queries (P17)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from talvora.modules.audit.api.dto import (
    AuditEventExportResponse,
    AuditEventPageResponse,
    AuditEventResponse,
)
from talvora.modules.audit.application.service import AuditQueryFilters, AuditQueryService
from talvora.modules.audit.domain.models import AUDIT_GOVERNANCE_DISCLOSURE
from talvora.modules.identity.api.dependencies import get_current_actor, get_db_session
from talvora.shared.application.actor import ActorContext
from talvora.shared.domain.clock import Clock
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1", tags=["Governance — Audit"])


def _get_clock(request: Request) -> Clock | None:
    return getattr(request.app.state, "clock", None)


@router.get(
    "/audit-events",
    response_model=AuditEventPageResponse,
    operation_id="listAuditEvents",
)
def list_audit_events(
    request: Request,
    resource_type: str | None = Query(default=None),
    resource_id: UUID | None = Query(default=None),
    actor_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    from_timestamp: datetime | None = Query(default=None),
    to_timestamp: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> AuditEventPageResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        page = AuditQueryService.query_audit_events(
            uow=uow,
            actor=actor,
            filters=AuditQueryFilters(
                resource_type=resource_type,
                resource_id=resource_id,
                actor_id=actor_id,
                action=action,
                correlation_id=correlation_id,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
            ),
            limit=limit,
            cursor=cursor,
        )
        return AuditEventPageResponse(
            items=[
                AuditEventResponse(
                    id=x.id,
                    tenant_id=x.tenant_id,
                    actor_id=x.actor_id,
                    actor_type=x.actor_type,
                    actor_capability=x.actor_capability,
                    resource_type=x.resource_type,
                    resource_id=x.resource_id,
                    action=x.action,
                    correlation_id=x.correlation_id,
                    timestamp=x.timestamp,
                    details=x.details,
                )
                for x in page.items
            ],
            next_cursor=page.next_cursor,
            has_more=page.has_more,
            governance_disclosure=page.governance_disclosure,
        )


@router.get(
    "/audit-events/export",
    response_model=AuditEventExportResponse,
    operation_id="exportAuditEvents",
)
def export_audit_events(
    request: Request,
    response: Response,
    resource_type: str | None = Query(default=None),
    resource_id: UUID | None = Query(default=None),
    actor_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    from_timestamp: datetime | None = Query(default=None),
    to_timestamp: datetime | None = Query(default=None),
    max_rows: int = Query(default=500, ge=1, le=1000),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> AuditEventExportResponse:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        items = AuditQueryService.export_audit_events(
            uow=uow,
            actor=actor,
            filters=AuditQueryFilters(
                resource_type=resource_type,
                resource_id=resource_id,
                actor_id=actor_id,
                action=action,
                correlation_id=correlation_id,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
            ),
            max_rows=max_rows,
        )
        now = uow.clock.now()
        uow.commit()
        return AuditEventExportResponse(
            exported_at=now,
            count=len(items),
            governance_disclosure=AUDIT_GOVERNANCE_DISCLOSURE,
            items=[
                AuditEventResponse(
                    id=x.id,
                    tenant_id=x.tenant_id,
                    actor_id=x.actor_id,
                    actor_type=x.actor_type,
                    actor_capability=x.actor_capability,
                    resource_type=x.resource_type,
                    resource_id=x.resource_id,
                    action=x.action,
                    correlation_id=x.correlation_id,
                    timestamp=x.timestamp,
                    details=x.details,
                )
                for x in items
            ],
        )
