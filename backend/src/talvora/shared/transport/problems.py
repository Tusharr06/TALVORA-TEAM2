"""RFC 7807 Problem Details and Exception Mapping.

Standardizes all error responses across Talvora APIs to application/problem+json
envelopes, ensuring zero stack-trace or credential leakage to clients.
"""

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from talvora.shared.domain.errors import (
    AuthorizationDeniedError,
    DomainValidationError,
    EntityNotFoundError,
    InvalidTransitionError,
    VersionConflictError,
)
from talvora.shared.infrastructure.logging import correlation_id_ctx

logger = logging.getLogger(__name__)


class InvalidParam(BaseModel):
    """Details on invalid parameter or field validation error."""

    name: str = Field(description="Name of the field or query parameter")
    reason: str = Field(description="Description of the validation failure")


class ProblemDetail(BaseModel):
    """RFC 7807 compliant problem response envelope."""

    type: str = Field(
        default="about:blank",
        description="URI reference that identifies the problem type",
    )
    title: str = Field(description="Short human-readable summary of problem")
    status: int = Field(description="HTTP status code")
    code: str = Field(description="Application-specific stable error code")
    detail: str = Field(description="Human-readable explanation specific to this occurrence")
    instance: str | None = Field(
        default=None, description="URI reference that identifies the specific occurrence"
    )
    correlation_id: str | None = Field(
        default=None, description="Correlation identifier for tracing"
    )
    invalid_params: list[InvalidParam] = Field(
        default_factory=list, description="List of invalid fields for validation failures"
    )


class ProblemException(Exception):
    """Exception explicitly carrying ProblemDetail information."""

    def __init__(
        self,
        status_code: int,
        title: str,
        code: str,
        detail: str,
        problem_type: str = "about:blank",
        invalid_params: list[InvalidParam] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.title = title
        self.code = code
        self.detail = detail
        self.problem_type = problem_type
        self.invalid_params = invalid_params or []


def problem_response(
    status_code: int,
    title: str,
    code: str,
    detail: str,
    request: Request,
    problem_type: str = "about:blank",
    invalid_params: list[InvalidParam] | None = None,
) -> JSONResponse:
    """Construct an application/problem+json response."""
    correlation_id = correlation_id_ctx.get() or request.headers.get("X-Correlation-ID")

    problem = ProblemDetail(
        type=problem_type,
        title=title,
        status=status_code,
        code=code,
        detail=detail,
        instance=str(request.url.path),
        correlation_id=correlation_id,
        invalid_params=invalid_params or [],
    )

    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(),
        media_type="application/problem+json",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Registers unified RFC 7807 error handlers with FastAPI."""

    @app.exception_handler(ProblemException)
    async def problem_exception_handler(request: Request, exc: ProblemException) -> JSONResponse:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            code=exc.code,
            detail=exc.detail,
            request=request,
            problem_type=exc.problem_type,
            invalid_params=exc.invalid_params,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        invalid_params = [
            InvalidParam(
                name=".".join(str(loc) for loc in err["loc"] if loc != "body"),
                reason=err["msg"],
            )
            for err in exc.errors()
        ]

        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Request Validation Error",
            code="VALIDATION_ERROR",
            detail="One or more fields in the request failed validation.",
            request=request,
            invalid_params=invalid_params,
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        status_code = exc.status_code
        code_map = {
            401: "AUTH_REQUIRED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "VERSION_CONFLICT",
            413: "LIMIT_EXCEEDED",
            415: "UNSUPPORTED_MEDIA_TYPE",
            429: "LIMIT_EXCEEDED",
            503: "SERVICE_UNAVAILABLE",
        }
        code = code_map.get(status_code, "HTTP_ERROR")
        title = exc.detail if isinstance(exc.detail, str) and len(exc.detail) < 40 else "HTTP Error"

        return problem_response(
            status_code=status_code,
            title=title,
            code=code,
            detail=str(exc.detail),
            request=request,
        )

    @app.exception_handler(EntityNotFoundError)
    async def not_found_handler(request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Entity Not Found",
            code=exc.code,
            detail=exc.message,
            request=request,
        )

    @app.exception_handler(VersionConflictError)
    async def version_conflict_handler(request: Request, exc: VersionConflictError) -> JSONResponse:
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="Version Conflict",
            code=exc.code,
            detail=exc.message,
            request=request,
        )

    @app.exception_handler(InvalidTransitionError)
    async def invalid_transition_handler(
        request: Request, exc: InvalidTransitionError
    ) -> JSONResponse:
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="Invalid State Transition",
            code=exc.code,
            detail=exc.message,
            request=request,
        )

    @app.exception_handler(AuthorizationDeniedError)
    async def authz_denied_handler(request: Request, exc: AuthorizationDeniedError) -> JSONResponse:
        return problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            title="Access Denied",
            code=exc.code,
            detail=exc.message,
            request=request,
        )

    @app.exception_handler(DomainValidationError)
    async def domain_validation_handler(
        request: Request, exc: DomainValidationError
    ) -> JSONResponse:
        invalid_params = [InvalidParam(name=k, reason=str(v)) for k, v in exc.field_errors.items()]
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Domain Validation Failed",
            code=exc.code,
            detail=exc.message,
            request=request,
            invalid_params=invalid_params,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log unhandled exception internally with traceback and correlation ID
        logger.exception("Unhandled server exception: %s", exc)

        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal Server Error",
            code="INTERNAL_SERVER_ERROR",
            detail="An unexpected internal error occurred. Tracing metadata has been recorded.",
            request=request,
        )
