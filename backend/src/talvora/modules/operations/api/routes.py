"""Operations REST API Routes.

Exposes inspectable background task status, controlled retry, and cancellation.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from talvora.config import Settings
from talvora.modules.operations.api.dto import OperationResponse
from talvora.modules.operations.application.service import OperationService
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import unit_of_work
from talvora.shared.transport.problems import ProblemException

router = APIRouter(prefix="/api/v1/operations", tags=["Operations"])


def get_operation_service() -> OperationService:
    return OperationService()


def get_current_actor(
    request: Request,
    x_tenant_id: UUID | None = Header(default=None, alias="X-Tenant-ID"),
    x_user_id: UUID | None = Header(default=None, alias="X-User-ID"),
) -> ActorContext:
    """Extracts actor context from session or verified headers in dev/test."""
    # In P02 foundation, tenant is resolved via session or authenticated header
    tenant_id = x_tenant_id
    if not tenant_id:
        raise ProblemException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Authentication Required",
            code="AUTH_REQUIRED",
            detail="Active tenant context is required to query operations.",
        )
    return ActorContext(
        principal=None,
        membership=None,  # Full membership RBAC will be plugged in P03
    )


@router.get(
    "/{operation_id}",
    response_model=OperationResponse,
    operation_id="get_operation_status",
    summary="Get Operation Status",
)
async def get_operation(
    operation_id: UUID,
    request: Request,
    x_tenant_id: UUID = Header(..., alias="X-Tenant-ID"),
    service: OperationService = Depends(get_operation_service),
) -> OperationResponse:
    """Retrieves current execution status, timings, result, and errors for a task."""
    db_manager = request.app.state.db_manager
    with unit_of_work(db_manager.session_maker, tenant_id=x_tenant_id) as uow:
        op = service.get_operation(uow.session, x_tenant_id, operation_id)
        return OperationResponse(
            operation_id=op.id,
            tenant_id=op.tenant_id,
            operation_type=op.operation_type,
            status=op.status,
            resource_id=op.resource_id,
            retry_count=op.retry_count,
            max_retries=op.max_retries,
            created_at=op.created_at,
            updated_at=op.updated_at,
            result=op.result,
            error_detail=op.error_detail,
        )


@router.post(
    "/{operation_id}/retry",
    response_model=OperationResponse,
    operation_id="retry_operation",
    summary="Retry Failed Operation",
)
async def retry_operation(
    operation_id: UUID,
    request: Request,
    x_tenant_id: UUID = Header(..., alias="X-Tenant-ID"),
    x_user_id: UUID | None = Header(default=None, alias="X-User-ID"),
    service: OperationService = Depends(get_operation_service),
) -> OperationResponse:
    """Re-enqueues a failed or retry-waiting operation for execution."""
    db_manager = request.app.state.db_manager
    with unit_of_work(db_manager.session_maker, tenant_id=x_tenant_id, subject_id=x_user_id) as uow:
        op = service.retry_operation(uow, operation_id)
        return OperationResponse(
            operation_id=op.id,
            tenant_id=op.tenant_id,
            operation_type=op.operation_type,
            status=op.status,
            resource_id=op.resource_id,
            retry_count=op.retry_count,
            max_retries=op.max_retries,
            created_at=op.created_at,
            updated_at=op.updated_at,
            result=op.result,
            error_detail=op.error_detail,
        )


@router.post(
    "/{operation_id}/cancel",
    response_model=OperationResponse,
    operation_id="cancel_operation",
    summary="Cancel Pending Operation",
)
async def cancel_operation(
    operation_id: UUID,
    request: Request,
    x_tenant_id: UUID = Header(..., alias="X-Tenant-ID"),
    x_user_id: UUID | None = Header(default=None, alias="X-User-ID"),
    service: OperationService = Depends(get_operation_service),
) -> OperationResponse:
    """Cancels a pending or retry-waiting operation before execution."""
    db_manager = request.app.state.db_manager
    with unit_of_work(db_manager.session_maker, tenant_id=x_tenant_id, subject_id=x_user_id) as uow:
        op = service.cancel_operation(uow, operation_id)
        return OperationResponse(
            operation_id=op.id,
            tenant_id=op.tenant_id,
            operation_type=op.operation_type,
            status=op.status,
            resource_id=op.resource_id,
            retry_count=op.retry_count,
            max_retries=op.max_retries,
            created_at=op.created_at,
            updated_at=op.updated_at,
            result=op.result,
            error_detail=op.error_detail,
        )


@router.post(
    "/test-diagnostic",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="admit_test_diagnostic_operation",
    summary="Admit Test Diagnostic Operation",
    include_in_schema=True,
)
async def admit_test_operation(
    request: Request,
    payload: dict[str, Any],
    x_tenant_id: UUID = Header(..., alias="X-Tenant-ID"),
    x_user_id: UUID | None = Header(default=None, alias="X-User-ID"),
) -> dict[str, Any]:
    """Internal test admission endpoint demonstrating transactional outbox.

    Active strictly in local and test environments.
    """
    settings: Settings = request.app.state.settings
    if settings.environment in ("staging", "production"):
        raise HTTPException(status_code=404, detail="Endpoint not found.")

    db_manager = request.app.state.db_manager
    with unit_of_work(db_manager.session_maker, tenant_id=x_tenant_id, subject_id=x_user_id) as uow:
        op = uow.admit_operation(
            operation_type="TEST_DIAGNOSTIC",
            payload=payload,
            correlation_id=request.headers.get("X-Correlation-ID"),
        )
        return {
            "operation_id": str(op.id),
            "status": op.status,
            "status_url": f"/api/v1/operations/{op.id}",
        }
