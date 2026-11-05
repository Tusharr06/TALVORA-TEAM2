"""HTTP Middleware Suite.

Includes Correlation ID propagation, Security Headers, Request Size Limits,
and RFC 7807 problem formatting for transport-level rejections.
"""

from typing import Any
from uuid import uuid4

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from talvora.shared.infrastructure.logging import correlation_id_ctx


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagates X-Correlation-ID from request or generates a new UUID v4."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        header_correlation = request.headers.get("X-Correlation-ID")
        correlation_id = header_correlation if header_correlation else str(uuid4())

        token = correlation_id_ctx.set(correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            correlation_id_ctx.reset(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces standard defensive HTTP security headers."""

    def __init__(self, app: Any, is_production: bool = False) -> None:
        super().__init__(app)
        self.is_production = is_production

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; object-src 'none';"
        )

        if self.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects incoming requests exceeding maximum allowed body byte size."""

    def __init__(self, app: Any, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("Content-Length")
        if content_length:
            try:
                length = int(content_length)
                if length > self.max_bytes:
                    correlation_id = correlation_id_ctx.get()
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={
                            "type": "https://talvora.com/problems/limit-exceeded",
                            "title": "Payload Too Large",
                            "status": 413,
                            "code": "LIMIT_EXCEEDED",
                            "detail": f"Request body exceeds maximum size of {self.max_bytes} bytes.",
                            "instance": str(request.url.path),
                            "correlation_id": correlation_id,
                        },
                        media_type="application/problem+json",
                    )
            except ValueError:
                pass

        return await call_next(request)
