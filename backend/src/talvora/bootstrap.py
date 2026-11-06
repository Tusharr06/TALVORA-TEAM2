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

    # One mock identity client for the process. A new client per request forgets the
    # PKCE grant before the browser can call the callback. Tests replace this.
    if effective_settings.allow_dev_identity_adapter and not hasattr(app.state, "oidc_client"):
        from talvora.modules.identity.infrastructure.oidc import MockOidcClient

        app.state.oidc_client = MockOidcClient(
            issuer=effective_settings.oidc_issuer,
            client_id=effective_settings.oidc_client_id,
        )

    # Object storage: a real S3/Moto-backed adapter unless the host application has
    # already injected a test double (e.g. InMemoryObjectStorageAdapter) before this
    # call. Constructing a boto3 client performs no network I/O; bucket existence is
    # the caller's responsibility (e.g. infra-as-code in staging/production, or a test
    # fixture calling `ensure_bucket` explicitly against Moto), never auto-provisioned
    # silently on every application boot.
    if not hasattr(app.state, "object_storage"):
        from talvora.modules.documents.application.storage import S3ObjectStorageAdapter

        app.state.object_storage = S3ObjectStorageAdapter(effective_settings)

    if not hasattr(app.state, "virus_scanner"):
        from talvora.modules.documents.application.scanner import ClamdScanner

        app.state.virus_scanner = ClamdScanner.from_settings(effective_settings)

    # Document extraction (P07): a real pypdf/python-docx + bounded pypdfium2/
    # Tesseract-OCR composite adapter unless the host application has already
    # injected a test double before this call.
    if not hasattr(app.state, "document_extractor"):
        from talvora.modules.evidence.application.extractor import CompositeDocumentExtractor

        app.state.document_extractor = CompositeDocumentExtractor.from_settings(effective_settings)

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
    from talvora.modules.applications.api.routes import candidate_history_router
    from talvora.modules.applications.api.routes import router as applications_router
    from talvora.modules.candidates.api.routes import router as candidates_router
    from talvora.modules.documents.api.routes import application_bindings_router, documents_router
    from talvora.modules.documents.api.routes import router as upload_batches_router
    from talvora.modules.evidence.api.routes import evidence_documents_router, evidence_router
    from talvora.modules.facts.api.routes import router as candidate_facts_router
    from talvora.modules.identity.api.routes import router as auth_router
    from talvora.modules.integrations.api.routes import router as integrations_router
    from talvora.modules.jobs.api.routes import router as jobs_router
    from talvora.modules.operations.api.routes import router as operations_router
    from talvora.modules.organizations.api.routes import router as organizations_router
    from talvora.modules.scheduling.api.routes import router as scheduling_router
    from talvora.modules.screening.api.routes import router as screening_router
    from talvora.modules.summaries.api.routes import router as summaries_router

    app.include_router(core_router)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(organizations_router, prefix="/api/v1")
    app.include_router(operations_router)
    app.include_router(jobs_router)
    app.include_router(candidates_router)
    app.include_router(applications_router)
    app.include_router(candidate_history_router)
    app.include_router(upload_batches_router)
    app.include_router(documents_router)
    app.include_router(application_bindings_router)
    app.include_router(evidence_documents_router)
    app.include_router(evidence_router)
    app.include_router(candidate_facts_router)
    app.include_router(screening_router)
    app.include_router(summaries_router)
    app.include_router(integrations_router)
    app.include_router(scheduling_router)

    return app
