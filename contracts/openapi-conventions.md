# Talvora API Architecture & OpenAPI Conventions

## 1. Global API Conventions

### 1.1 Base Paths and Versioning
- All business APIs are versioned under the `/api/v1` prefix.
- The only unversioned endpoints are infrastructure health checks:
  - `GET /health/live`: Unauthenticated liveness probe (returns `{"status": "alive"}`).
  - `GET /health/ready`: Unauthenticated readiness probe checking database and queue connectivity.
- External webhook callbacks reside under `/api/v1/webhooks/*`.
- Candidate self-scheduling capability endpoints reside under `/api/v1/public/*`.

### 1.2 Headers and Concurrency Control
- **`Idempotency-Key` (Header, optional on safe requests, mandatory on side-effect commands):**
  - High-entropy UUID or client key.
  - Scoped by `(tenant_id, principal_id, route, idempotency_key)`.
  - Replays with identical payload return the cached original response.
  - Replays with different payloads return `409 Conflict`.
- **`If-Match` / `version` (Header or Payload):**
  - Optimistic concurrency control for mutable aggregates (Jobs, Applications, Packs).
  - If the database version does not match the provided version, the request returns `409 Conflict` (`code: "VERSION_CONFLICT"`).
- **`X-Correlation-ID` (Header, optional on entry, generated if missing):**
  - Tracing UUID propagated across HTTP requests, database transactions, outbox events, and Celery tasks.
- **`X-CSRF-Token` (Header, required for browser cookie mutations):**
  - Token verified against the user's active session.

### 1.3 Cursor-Based Pagination
All list endpoints return paginated collections with cursor pagination ordered deterministically by `(created_at DESC, id DESC)`. The maximum allowed page size is 100 items.

**Pagination Response Envelope:**
```json
{
  "items": [],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJjcmVhdGVkX2F0IjogIjIwMjYtMTEtMDVUMjI6MDA6MDBaIiwgImlkIjogIjExMTExMTExLTExMTEtNDExMS04MTExLTExMTExMTExMTExMSJ9",
    "page_size": 25,
    "total_count": 142
  }
}
```

---

## 2. Standard Problem Details (RFC 7807)

All non-2xx responses return `application/problem+json` matching RFC 7807:

```json
{
  "type": "https://talvora.com/problems/invalid-transition",
  "title": "Invalid Pipeline Transition",
  "status": 409,
  "code": "INVALID_TRANSITION",
  "detail": "Application cannot move from NEW to DECISION_PENDING without INTERVIEW_APPROVED.",
  "instance": "/api/v1/applications/22222222-2222-4222-8222-222222222222/stage-transitions",
  "correlation_id": "99999999-9999-4999-8999-999999999999",
  "invalid_params": []
}
```

### Standardized Error Codes:
- `AUTH_REQUIRED` (401): Missing or expired authentication session.
- `FORBIDDEN` (403): User lacks required capability within active tenant.
- `NOT_FOUND` (404): Resource does not exist (or belongs to another tenant).
- `VERSION_CONFLICT` (409): Resource version mismatch or concurrent edit collision.
- `INVALID_TRANSITION` (409): Forbidden state machine transition.
- `VALIDATION_ERROR` (422): Schema validation failure or malformed field values.
- `LIMIT_EXCEEDED` (429 / 413): Rate limit, batch limit, or file size ceiling exceeded.
- `UNSAFE_DOCUMENT` (415 / 422): Malware detected by ClamAV or malformed container format.
- `DOCUMENT_UNREADABLE` (422): Corrupted PDF/DOCX structure or unextractable text.
- `EVIDENCE_INVALID` (422): Fact citation points to invalid segment offset or mismatched quote.
- `AI_UNAVAILABLE` (503): External LLM provider timeout, refusal, or quota exhaustion.
- `INTEGRATION_REAUTH_REQUIRED` (403 / 409): Google OAuth refresh token expired or revoked.
- `AVAILABILITY_UNKNOWN` (409 / 503): One or more required interviewer calendars unreachable.
- `SLOT_CONFLICT` (409): Target calendar interval was booked concurrently.
- `APPROVAL_EXPIRED` (409): Recruiter approval validity window (15 mins) has lapsed.
- `APPROVAL_PAYLOAD_CHANGED` (409): Action parameters modified after approval signature.
- `PROVIDER_RESULT_UNKNOWN` (504): Ambiguous timeout during external side-effect execution.
- `PRIVACY_HOLD_ACTIVE` (409): Deletion or export blocked by active legal hold.

---

## 3. Asynchronous Operation Envelope (HTTP 202 Accepted)

Long-running operations (batch upload processing, OCR, AI profile extraction, screening runs, calendar booking sagas) return HTTP `202 Accepted` with a durable operation tracking envelope and a `Location` header pointing to the operation status endpoint:

```json
{
  "operation_id": "11111111-1111-4111-8111-111111111111",
  "status": "PENDING",
  "resource_id": "22222222-2222-4222-8222-222222222222",
  "status_url": "/api/v1/operations/11111111-1111-4111-8111-111111111111"
}
```

### Operation Status Lifecycle:
- `PENDING`: Admitted into PostgreSQL outbox; awaiting worker lease acquisition.
- `RUNNING`: Worker actively executing task; lease heartbeats active.
- `SUCCEEDED`: Operation completed successfully; output artifact ready.
- `RETRY_WAIT`: Transient error encountered; awaiting exponential backoff retry.
- `NEEDS_REVIEW`: Human intervention required to resolve ambiguity.
- `FAILED`: Terminal failure; error details recorded in problem format.
- `CANCELED`: Canceled by authorized user before execution.

---

## 4. Phase-to-API Ownership Map

The table below defines the complete inventory of canonical REST endpoints and their owning implementation phases:

| Endpoint Group | HTTP Verb & Path | Owning Phase | Description & Authorization |
| --- | --- | :---: | --- |
| **Health** | `GET /health/live`<br>`GET /health/ready` | **P01** | Unauthenticated infrastructure probes |
| **Session** | `GET /api/v1/auth/login`<br>`GET /api/v1/auth/callback`<br>`GET /api/v1/auth/me`<br>`GET /api/v1/auth/csrf`<br>`POST /api/v1/auth/logout` | **P03** | OIDC authentication, session lookup, and logout |
| **Tenancy** | `GET /api/v1/organizations`<br>`POST /api/v1/organizations`<br>`GET /api/v1/organizations/{id}/members`<br>`GET /api/v1/teams`<br>`POST /api/v1/membership-invitations`<br>`POST /api/v1/membership-invitations/{id}/accept` | **P03** | Tenant management, membership invitations, and team roles |
| **Vacancies** | `GET /api/v1/jobs`<br>`POST /api/v1/jobs`<br>`GET /api/v1/jobs/{id}`<br>`PATCH /api/v1/jobs/{id}`<br>`POST /api/v1/jobs/{id}/requirement-versions`<br>`POST /api/v1/jobs/{id}/publish`<br>`POST /api/v1/jobs/{id}/status-transitions` | **P04** | Vacancy CRUD, criteria versioning, publishing, and lifecycle transitions |
| **Candidates & Pipeline** | `GET /api/v1/candidates`<br>`POST /api/v1/candidates`<br>`PATCH /api/v1/candidates/{id}`<br>`POST /api/v1/candidates/{id}/contact-verifications`<br>`GET /api/v1/applications`<br>`POST /api/v1/applications`<br>`POST /api/v1/applications/{id}/stage-transitions` | **P05** | Candidate records, contact verification, and human pipeline movement |
| **Intake & Reprocessing** | `POST /api/v1/upload-batches`<br>`POST /api/v1/upload-batches/{id}/items`<br>`GET /api/v1/upload-batches/{id}`<br>`POST /api/v1/documents/{id}/reprocess`<br>`POST /api/v1/applications/{id}/document-bindings` | **P06–P07**| Multipart batch upload, quarantine ingestion, and document rebinding |
| **Evidence & Facts** | `GET /api/v1/documents/{id}/versions/{version}/source`<br>`GET /api/v1/evidence/{id}`<br>`GET /api/v1/applications/{id}/profile`<br>`POST /api/v1/applications/{id}/profile-extractions`<br>`POST /api/v1/profile-revisions/{id}/reviews` | **P07–P08**| Source segment viewer, extracted facts, and recruiter fact revisions |
| **Screening** | `POST /api/v1/applications/{id}/screening-runs`<br>`GET /api/v1/screening-runs/{id}`<br>`POST /api/v1/screening-runs/{id}/reviews` | **P09** | Deterministic screening execution, inspection, and overrides |
| **Evidence Summaries**| `POST /api/v1/applications/{id}/summary-runs`<br>`GET /api/v1/applications/{id}/summaries`<br>`POST /api/v1/summary-revisions/{id}/reviews` | **P10** | Cited summary generation and recruiter publication review |
| **Google Integrations** | `POST /api/v1/integrations/google/authorize`<br>`GET /api/v1/integrations/google/callback`<br>`GET /api/v1/integrations`<br>`DELETE /api/v1/integrations/{id}` | **P11** | Delegated Google OAuth authorization, connection status, and disconnect |
| **Availability & Slots**| `POST /api/v1/interview-requests`<br>`POST /api/v1/interview-requests/{id}/availability-links`<br>`POST /api/v1/interview-requests/{id}/slot-searches`<br>`GET /api/v1/interview-requests/{id}/proposals` | **P12** | Interview requests, link generation, free/busy queries, and slot proposals |
| **Candidate Portal** | `POST /api/v1/public/link-sessions`<br>`GET /api/v1/public/interview`<br>`POST /api/v1/public/availability`<br>`POST /api/v1/public/confirmation`<br>`POST /api/v1/public/change-requests` | **P12–P14**| Dedicated candidate capability endpoints (scoped token authentication) |
| **External Approvals**| `POST /api/v1/external-actions`<br>`GET /api/v1/external-actions/{id}`<br>`POST /api/v1/external-actions/{id}/approve`<br>`POST /api/v1/external-actions/{id}/resolve` | **P13** | Approval state machine, cryptographic signing, and manual conflict resolution |
| **Interview Lifecycle**| `GET /api/v1/interviews/{id}`<br>`POST /api/v1/interviews/{id}/reschedule-requests`<br>`POST /api/v1/interviews/{id}/cancel-requests`<br>`POST /api/v1/interviews/{id}/completion`<br>`POST /api/v1/webhooks/google/calendar` | **P14** | Confirmed interview inspection, change requests, and Google push webhook |
| **Interview Packs** | `GET /api/v1/competency-frameworks`<br>`POST /api/v1/competency-frameworks`<br>`POST /api/v1/interviews/{id}/pack-runs`<br>`GET /api/v1/interviews/{id}/packs`<br>`PATCH /api/v1/pack-revisions/{id}`<br>`POST /api/v1/pack-revisions/{id}/publish` | **P15** | Competency frameworks, interview pack generation, and rubric review |
| **Feedback & Exports** | `GET /api/v1/interviews/{id}/feedback/me`<br>`PUT /api/v1/interviews/{id}/feedback/me`<br>`POST /api/v1/interviews/{id}/feedback/me/submit`<br>`POST /api/v1/pack-revisions/{id}/exports`<br>`GET /api/v1/exports/{id}/download` | **P16** | Blind interviewer feedback, DOCX/PDF export generation, and controlled downloads |
| **Governance & Audit** | `GET /api/v1/audit-events`<br>`GET /api/v1/analytics/pipeline`<br>`GET /api/v1/analytics/operations`<br>`GET /api/v1/usage`<br>`POST /api/v1/privacy-requests`<br>`PATCH /api/v1/organization-policy` | **P17** | Audit query, operational analytics, privacy deletion, and retention policy |
| **Operations Engine** | `GET /api/v1/operations/{id}`<br>`POST /api/v1/operations/{id}/retry`<br>`POST /api/v1/operations/{id}/cancel` | **P02 onward**| Universal background task tracking, retry execution, and cancellation |
