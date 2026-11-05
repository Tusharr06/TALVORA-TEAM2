"""Main Application Entrypoint & Health Endpoints.

Implements unauthenticated /health/live and /health/ready outside /api/v1.
Distinguishes core relational database availability from optional integrations.
"""

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.shared.application.ports import HealthChecker

router = APIRouter()


class ComponentHealth(BaseModel):
    name: str = Field(description="Name of the component")
    healthy: bool = Field(description="Whether the component is operational")
    status: str = Field(description="Detailed status message")
    latency_ms: float = Field(description="Health check round-trip latency in milliseconds")


class HealthStatusResponse(BaseModel):
    status: str = Field(description="Overall service health: healthy, degraded, or unhealthy")
    service: str = Field(description="Service name")
    environment: str = Field(description="Active environment")
    components: list[ComponentHealth] = Field(default_factory=list)


class LivenessResponse(BaseModel):
    status: str = Field(default="alive")
    service: str = Field(description="Service name")


def get_current_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    tags=["Health"],
    operation_id="check_liveness",
    summary="Liveness Probe",
)
async def health_live(
    settings: Settings = Depends(get_current_settings),
) -> LivenessResponse:
    """Unauthenticated liveness probe.

    Indicates the FastAPI application process is alive and accepting connections.
    Does NOT depend on external database, cloud, or AI availability.
    """
    return LivenessResponse(
        status="alive",
        service=settings.service_name,
    )


@router.get(
    "/health/ready",
    response_model=HealthStatusResponse,
    tags=["Health"],
    operation_id="check_readiness",
    summary="Readiness Probe",
    responses={
        200: {"description": "Service is ready to handle traffic"},
        503: {"description": "Core dependencies are unavailable"},
    },
)
async def health_ready(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_current_settings),
) -> HealthStatusResponse:
    """Readiness probe checking core database and dependency availability.

    Returns HTTP 200 when core database is healthy.
    Returns HTTP 503 if core relational persistence is unreachable.
    Optional integrations appear as degraded capabilities rather than taking down the service.
    """
    checkers: list[HealthChecker] = getattr(request.app.state, "health_checkers", [])

    components: list[ComponentHealth] = []
    core_healthy = True

    for checker in checkers:
        is_healthy, msg, latency_s = await checker.check()
        components.append(
            ComponentHealth(
                name=checker.name,
                healthy=is_healthy,
                status=msg,
                latency_ms=round(latency_s * 1000.0, 2),
            )
        )
        # Database is the core critical dependency
        if checker.name == "database" and not is_healthy:
            core_healthy = False

    if not core_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        overall_status = "unhealthy"
    else:
        overall_status = "healthy"

    return HealthStatusResponse(
        status=overall_status,
        service=settings.service_name,
        environment=settings.environment,
        components=components,
    )


@router.get(
    "/internal/diagnostic",
    tags=["Internal"],
    operation_id="get_internal_diagnostic",
    summary="Internal Diagnostic Surface",
    include_in_schema=True,
)
async def internal_diagnostic(
    settings: Settings = Depends(get_current_settings),
) -> dict[str, Any]:
    """Non-production diagnostic route for runtime verification.

    Active strictly in local or test environments. Returns 404 in staging/production.
    """
    if not settings.internal_diagnostic_enabled or settings.environment in (
        "staging",
        "production",
    ):
        raise HTTPException(status_code=404, detail="Diagnostic endpoint not found.")

    return {
        "diagnostic": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
        "database_timeout": settings.db_timeout_seconds,
        "max_request_size": settings.max_request_size_bytes,
    }


# Composition root instance for ASGI servers (e.g. uvicorn talvora.main:app)
app = create_app()
