"""Application Bootstrap and Composition Root.

Initializes FastAPI application instance, registers lifespan event managers,
attaches dependency health checkers, and configures defense-in-depth middleware.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from talvora.config import Settings, get_settings
from talvora.shared.infrastructure.db import DatabaseHealthChecker, DatabaseManager
from talvora.shared.infrastructure.logging import configure_logging
from talvora.shared.transport.middleware import (
    CorrelationIdMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from talvora.shared.transport.problems import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Manages application lifecycle: resource initialization and graceful shutdown."""
    settings: Settings = app.state.settings

    # Initialize structured JSON logging
    configure_logging(
        log_level=settings.log_level,
        service_name=settings.service_name,
    )

    # Initialize Database manager if not already present
    db_manager: DatabaseManager | None = getattr(app.state, "db_manager", None)
    if db_manager is None:
        db_manager = DatabaseManager(settings)
        app.state.db_manager = db_manager

    # Register health checkers
    app.state.health_checkers = [
        DatabaseHealthChecker(db_manager, timeout=settings.db_timeout_seconds)
    ]

    try:
        yield
    finally:
        # Graceful shutdown: close connection pools and active clients
        await db_manager.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory for Talvora Backend."""
    effective_settings = settings or get_settings()

    app = FastAPI(
        title="Talvora Talent Operations API",
        description="Enterprise Talent Operations Workspace backend for intelligent recruitment workflows.",
        version="0.1.0",
        docs_url="/docs" if effective_settings.environment in ("local", "test") else None,
        redoc_url="/redoc" if effective_settings.environment in ("local", "test") else None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Attach settings and db manager to application state
    app.state.settings = effective_settings
    app.state.db_manager = DatabaseManager(effective_settings)

    # 1. Register RFC 7807 problem exception handlers
    register_exception_handlers(app)

    # 2. Register Middleware (Order matters: outer first)
    app.add_middleware(
        SecurityHeadersMiddleware,
        is_production=effective_settings.environment in ("staging", "production"),
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=effective_settings.max_request_size_bytes,
    )

    if effective_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=effective_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["X-Correlation-ID"],
        )

    if effective_settings.allowed_hosts and "*" not in effective_settings.allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=effective_settings.allowed_hosts,
        )

    # 3. Register Core Routers
    from talvora.main import router as core_router
    from talvora.modules.operations.api.routes import router as operations_router

    app.include_router(core_router)
    app.include_router(operations_router)

    return app
