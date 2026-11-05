# Talvora Dependency Baseline & Technology Support Inventory

**Verification Date:** 5 November 2026  
**Status:** Approved Architectural Baseline  
**License Policy:** Permissive only (MIT, Apache-2.0, BSD-2/3-Clause, PSF). Zero AGPL/GPL copyleft in application runtime artifacts.

---

## 1. Backend Runtime & Core Frameworks

| Package / Technology | Version Pinned | Release Status | License | Verified Source / Rationale |
| --- | --- | --- | --- | --- |
| **Python** | `3.13.1` | Stable | PSF | Verified on host (`3.13.1`). Modern performance improvements, typing enhancements, and security fixes. |
| **FastAPI** | `0.115.8` | Stable | MIT | High-performance ASGI framework with native Pydantic 2 integration and OpenAPI 3.1 generation. |
| **Pydantic** | `2.10.6` | Stable | MIT | Core data validation and serialization; strict schema validation for LLM outputs. |
| **Pydantic Settings** | `2.7.1` | Stable | MIT | Strongly typed, environment-driven configuration management. |
| **Uvicorn** | `0.34.0` | Stable | BSD-3-Clause | Production-grade ASGI web server with `uvloop` and `httptools`. |
| **SQLAlchemy** | `2.0.38` | Stable | MIT | Typed ORM with explicit transaction control and direct support for PostgreSQL RLS `set_config`. |
| **psycopg (Version 3)** | `3.2.4` | Stable | LGPL with C exception / BSD | Modern PostgreSQL driver supporting binary protocol, prepared statements, and pipeline mode. |
| **Alembic** | `1.14.1` | Stable | MIT | Database schema migrations with revision tracking and autogenerate drift detection. |

---

## 2. Distributed Workload, Messaging & Cloud Adapters

| Package / Technology | Version Pinned | Release Status | License | Verified Source / Rationale |
| --- | --- | --- | --- | --- |
| **PostgreSQL** | `17.2` | Stable | PostgreSQL | Target database engine on AWS RDS and local Docker container (`postgres:17-alpine`). Range exclusion support (`tstzrange`, `btree_gist`). |
| **Celery** | `5.4.0` | Stable | BSD-3-Clause | Distributed asynchronous task queue for document processing and external side effect execution. |
| **Kombu** | `5.4.2` | Stable | BSD-3-Clause | Celery messaging abstraction with Amazon SQS transport (`kombu[sqs]`). |
| **Boto3** | `1.36.10` | Stable | Apache-2.0 | AWS SDK for Python (Amazon S3, SQS, SES, Secrets Manager, KMS). |
| **Botocore** | `1.36.10` | Stable | Apache-2.0 | Low-level AWS service interfaces and request signing. |
| **Moto (Server Mode)**| `5.0.28` | Stable | Apache-2.0 | Local AWS service emulator for isolated development and testing of S3 and SQS. |

---

## 3. Document Extraction, Parsing & Rendering

| Package / Technology | Version Pinned | Release Status | License | Verified Source / Rationale |
| --- | --- | --- | --- | --- |
| **pypdf** | `5.3.0` | Stable | BSD-3-Clause | Pure-Python PDF extraction library with page-level text extraction and metadata inspection. |
| **python-docx** | `1.1.2` | Stable | MIT | Word document reader/writer; traverses XML paragraphs and table cells without Word runtime. |
| **defusedxml** | `0.7.1` | Stable | Python-2.0 | XML bomb and XXE injection defense wrapper for all document and SVG parsing. |
| **pypdfium2** | `4.30.1` | Stable | Apache-2.0 / BSD | High-performance PDFium bindings for bounded PDF rendering prior to OCR. |
| **pytesseract** | `0.3.13` | Stable | Apache-2.0 | Wrapper for system Tesseract OCR 5.4+ engine for scanned PDF page fallback. |
| **WeasyPrint** | `64.0` | Stable | BSD-3-Clause | Visual HTML/CSS to PDF document converter for interview packs and feedback summaries. |
| **ClamAV (Container)** | `1.4.1` | Stable | GPL-2.0 (Isolated daemon) | Network-isolated antivirus scanning daemon communicating over TCP socket (`clamd`). |

---

## 4. AI & Integration Adapters

| Package / Technology | Version Pinned | Release Status | License | Verified Source / Rationale |
| --- | --- | --- | --- | --- |
| **OpenAI SDK** | `1.61.0` | Stable | Apache-2.0 | Client for OpenAI Structured Outputs API with `store=false` support. |
| **Google API Client** | `2.160.0` | Stable | Apache-2.0 | Client for Google Calendar (free/busy, events) and Gmail (`gmail.send`). |
| **Google Auth / Oauthlib** | `1.2.1` | Stable | Apache-2.0 | Delegated OAuth 2.0 token management, code exchange, and refresh handling. |
| **Authlib** | `1.4.1` | Stable | BSD-3-Clause | Comprehensive OpenID Connect (OIDC) client with PKCE and JWT verification. |
| **Cryptography** | `44.0.0` | Stable | Apache-2.0 / BSD | Low-level cryptographic primitives for HMAC-SHA256 approvals and AES-GCM token encryption. |

---

## 5. Development, Quality Assurance & Verification

| Package / Technology | Version Pinned | Release Status | License | Verified Source / Rationale |
| --- | --- | --- | --- | --- |
| **uv** | `0.11.2` | Stable | Apache-2.0 / MIT | Ultra-fast Python package installer and virtualenv manager (verified on host: `0.11.2`). |
| **Ruff** | `0.9.6` | Stable | MIT | High-performance linter and code formatter replacing Flake8, Black, and isort. |
| **mypy** | `1.15.0` | Stable | MIT | Static type checker run in strict mode (`--strict`). |
| **pytest** | `8.3.4` | Stable | MIT | Core testing framework. |
| **pytest-asyncio** | `0.25.3` | Stable | Apache-2.0 | Asynchronous test execution for FastAPI transport endpoints. |
| **Hypothesis** | `6.124.7` | Stable | MPL-2.0 | Property-based testing for interval date arithmetic and screening truth tables. |
| **HTTPX** | `0.28.1` | Stable | BSD-3-Clause | Full-featured HTTP client for ASGI integration tests. |

---

## 6. Future Frontend Baseline (Planned for Phase P19)

> **Important Boundary Rule:** No frontend packages or configurations may be installed before Phase P19. The table below represents the validated technology plan to be executed at P19.

| Technology / Tool | Target Version | Status | License | Purpose / Boundary |
| --- | --- | --- | --- | --- |
| **Node.js** | `22.12.0 LTS` | Active LTS | MIT | Target JavaScript runtime (verified on host: `v22.12.0`). |
| **pnpm** | `9.15.x` | Stable | MIT | Fast, disk-efficient package manager. |
| **React** | `19.0.0` | Stable | MIT | Core declarative UI component library. |
| **TypeScript** | `5.7.x` | Stable | Apache-2.0 | Strict static type checking across all client modules. |
| **Vite** | `6.1.x` | Stable | MIT | Modern frontend bundler and development server. |
| **TanStack Query** | `5.66.x` | Stable | MIT | Server-state management and synchronization; eliminates need for Redux. |
| **Tailwind CSS** | `3.4.17` | Stable | MIT | Utility-first styling framework with clean slate/indigo professional palette. |
| **Radix UI** | `1.x` | Stable | MIT | Headless, accessible UI primitives complying with WCAG 2.2 AA. |
| **React Hook Form** | `7.54.x` | Stable | MIT | Performant form management with Zod schema validation. |
| **Zod** | `3.24.x` | Stable | MIT | TypeScript-first client-side schema validation. |
| **OpenAPI TS Codegen**| `0.29.x` / `@hey-api` | Stable | MIT | Automated generation of typed API client from `contracts/openapi.json`. |
