"""FastAPI Routes for Pipeline Analytics, Operational Health, and AI/Storage Usage (P17)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from talvora.modules.analytics.api.dto import (
    OperationsAnalyticsResponse,
    OrganizationUsageResponse,
    PipelineAnalyticsResponse,
)
from talvora.modules.analytics.application.service import AnalyticsService
from talvora.modules.identity.api.dependencies import get_current_actor, get_db_session
from talvora.shared.application.actor import ActorContext
from talvora.shared.domain.clock import Clock
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1", tags=["Governance — Analytics & Usage"])


def _get_clock(request: Request) -> Clock | None:
    return getattr(request.app.state, "clock", None)


@router.get(
    "/analytics/pipeline",
    response_model=PipelineAnalyticsResponse,
    operation_id="getPipelineAnalytics",
)
def get_pipeline_analytics(
    request: Request,
    job_id: UUID | None = Query(default=None),
    team_id: UUID | None = Query(default=None),
    from_utc: datetime | None = Query(default=None),
    to_utc: datetime | None = Query(default=None),
    timezone: str = Query(default="UTC"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> PipelineAnalyticsResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        data = AnalyticsService.get_pipeline_analytics(
            uow=uow,
            actor=actor,
            job_id=job_id,
            team_id=team_id,
            from_utc=from_utc,
            to_utc=to_utc,
            timezone_name=timezone,
        )
        return PipelineAnalyticsResponse.model_validate(data)


@router.get(
    "/analytics/operations",
    response_model=OperationsAnalyticsResponse,
    operation_id="getOperationsAnalytics",
)
def get_operations_analytics(
    request: Request,
    job_id: UUID | None = Query(default=None),
    team_id: UUID | None = Query(default=None),
    from_utc: datetime | None = Query(default=None),
    to_utc: datetime | None = Query(default=None),
    timezone: str = Query(default="UTC"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> OperationsAnalyticsResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        data = AnalyticsService.get_operations_analytics(
            uow=uow,
            actor=actor,
            job_id=job_id,
            team_id=team_id,
            from_utc=from_utc,
            to_utc=to_utc,
            timezone_name=timezone,
        )
        return OperationsAnalyticsResponse.model_validate(data)


@router.get(
    "/usage",
    response_model=OrganizationUsageResponse,
    operation_id="getOrganizationUsage",
)
def get_organization_usage(
    request: Request,
    from_utc: datetime | None = Query(default=None),
    to_utc: datetime | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> OrganizationUsageResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        data = AnalyticsService.get_organization_usage(
            uow=uow,
            actor=actor,
            from_utc=from_utc,
            to_utc=to_utc,
        )
        return OrganizationUsageResponse.model_validate(data)
