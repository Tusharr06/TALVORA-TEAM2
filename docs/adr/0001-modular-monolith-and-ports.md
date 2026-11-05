# ADR-0001: Modular Monolith and Ports Architecture

## Status
Accepted

## Context
Talvora requires high domain cohesion across vacancy management, resume intake, OCR/parsing, AI screening, calendar scheduling, and structured interviewer feedback. The engineering team evaluated whether to structure the system as microservices, a monolithic framework (like Django), or a TypeScript-only stack (NestJS).

Resume parsing, text extraction, OCR bounding, and model evaluation libraries (`pypdf`, `python-docx`, `pypdfium2`, `pytesseract`, `weasyprint`) are mature and heavily optimized in Python. Utilizing an all-TypeScript backend would require maintaining a secondary Python microservice solely for document and AI workloads, introducing distributed failure modes and deployment overhead for an initial pilot. Conversely, traditional monolithic frameworks like Django introduce opaque ORM magic, difficult RLS transaction-local bindings, and tight coupling between database tables.

## Decision
Talvora will be built as a **Modular Monolith** in Python 3.13 utilizing **FastAPI** and **Pydantic 2**, structured via **Ports and Adapters (Hexagonal Architecture)**:
1. **Single Repository & Artifact:** All domains reside in a single codebase with unified database migrations (Alembic) and shared domain value objects.
2. **Process Profile Separation:** The single artifact is deployed into three distinct runtime process roles:
   - `web`: Synchronous FastAPI HTTP handling.
   - `celery_doc`: Constrained, sandboxed worker for file parsing and OCR.
   - `celery_io`: External I/O worker for OpenAI, Google Calendar, and SES/Mailpit.
   - `coordinator`: Lightweight periodic outbox publisher and lease sweeper.
3. **Layer Separation:**
   - `domain/`: Pure business logic, zero framework dependencies.
   - `application/`: Use cases, transactional boundaries, port interfaces.
   - `infrastructure/`: Adapters for PostgreSQL/SQLAlchemy 2.0, S3, OpenAI, and Google.
   - `api/`: FastAPI transport, DTO schemas, and problem formatting.

## Consequences
- **Positive:**
  - One language, one dependency tree, and zero network hops for internal module collaboration.
  - Testable domain logic without database mocking.
  - High observability and unified Alembic migration history.
- **Negative / Trade-offs:**
  - Requires strict architectural discipline to prevent circular imports and leaky abstractions between modules.
  - Microservice-style autonomous deployments are not possible; scaling worker vs web occurs at the container task level.
