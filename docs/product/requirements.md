# Talvora Product Requirements & Scope Boundaries

## 1. Scope Overview: Release 1 Controlled Pilot

The initial release of Talvora is scoped as an invite-only, enterprise-ready controlled pilot. It demonstrates the complete end-to-end vertical recruitment journey while enforcing tenant isolation, role-based governance, and deterministic verifiability.

---

## 2. Detailed Functional Requirements by Area

### 2.1 Organizations & Tenancy
- **Multi-Tenant Foundation:** All resources are strictly bound to an Organization (`tenant_id`). Cross-tenant sharing or querying is disallowed at all layers.
- **Tenancy Lifecycle:** Organization creation establishes the founding `OWNER`. Organization members hold exactly one base role (`OWNER`, `ADMIN`, `RECRUITER`, `HIRING_MANAGER`, `INTERVIEWER`).
- **Teams & Assignments:** Organizations contain functional Teams. Members are assigned to Teams and given explicit capabilities on specific Vacancies.
- **Invitations & Offboarding:** Email-based membership invitation with cryptographic token redemption. Offboarding immediately revokes active sessions, pending approvals, and integration grants while preserving historical authorship and audit logs.
- *Release 1 Boundary:* Invite-only organizations; self-service billing and public signup are excluded.

### 2.2 Vacancies & Requirement Frameworks
- **Vacancy Lifecycle:** `DRAFT` → `OPEN` ↔ `PAUSED` → `CLOSED`.
- **Requirement Versioning:** Vacancies possess mutable drafts and immutable published `RequirementVersion` instances. Modifying criteria produces a new version; active candidate screenings retain their original requirement version snapshot.
- **Deterministic Criterion Types:**
  - `SKILL_PRESENT`: Exact or rule-mapped canonical skill name.
  - `MIN_EXPERIENCE_MONTHS`: Minimum calendar months computed via interval arithmetic over documented employment intervals.
  - `EDUCATION_LEVEL`: Discrete levels (e.g., `BACHELORS`, `MASTERS`, `DOCTORATE`) evaluated against explicit equivalence rules.
  - `CERTIFICATION_PRESENT`: Named certification with issuance and expiration checks.
  - `MANUAL_REQUIREMENT`: Explicit human verification item.
- *Release 1 Boundary:* Fixed criterion types; no arbitrary executable code or dynamic scripting rules.

### 2.3 Candidate Records & Applications
- **Candidate Identity:** Tenant-local identity records with verified primary email and phone. No cross-tenant candidate directory.
- **Intake Cycles & Applications:** A Candidate submits an Application for a specific Job. An application snapshot links exactly one active resume version.
- **Human Pipeline Stages:**
  - `NEW` → `IN_REVIEW` → `INTERVIEW_APPROVED` → `INTERVIEWING` → `DECISION_PENDING` → `HIRED` / `REJECTED`.
  - `WITHDRAWN` is allowed from any non-terminal stage.
  - Reopening a terminal application requires an authorized recruiter command, an explicit audit reason, and an immutable stage event.
- *Release 1 Boundary:* One active resume per application snapshot; rebinding to a newer resume version is an explicit human action.

### 2.4 Intake & Upload Operations
- **Quarantined Batch Upload:** Supports multipart streaming upload of up to 100 files per batch, 10 MiB per file, 500 MiB total batch limit.
- **Quarantine Lifecycle:** Uploaded files stream directly to private S3 quarantine storage. Immediate SHA-256 calculation and ClamAV antivirus scanning.
- **Container Verification:** Magic byte validation, MIME-type enforcement (`application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`).
- **Error Isolation:** A malformed or infected file fails individually without aborting the rest of the batch.
- *Release 1 Boundary:* PDF and DOCX only; archive extraction (.zip, .tar) and macro-enabled documents (.docm) are rejected.

### 2.5 Document Processing & Provenance
- **Extraction Budgets:** Up to 30 PDF pages per document; 200,000 extracted characters; OCR bounded to at most 10 pages per document.
- **DOCX Hardening:** ZIP expansion limits (max 50 MiB uncompressed, max 2,000 entries, bounded compression ratio); XML entity expansion protections (no external entity resolution).
- **Evidence Provenance:** Deconstructs extracted text into immutable `SourceSegment` records. PDF segments track 1-based page numbers; DOCX segments track structural paragraph/table-cell paths.
- **Canonical Code-Point Indexing:** Offsets are computed using Unicode code points (UTF-8 safe) alongside segment text hashes.
- *Release 1 Boundary:* English-language resume parsing initially. Scanned documents requiring more than 10 OCR pages require human manual review.

### 2.6 Deterministic Screening Engine
- **Tri-State Verdicts per Criterion:**
  - `PASS`: Proven by cited candidate facts.
  - `FAIL`: Explicit, verified contradiction against complete history.
  - `NEEDS_REVIEW`: Missing facts, partial history, preferred skill missing, or manual check.
- **Interval Arithmetic for Experience:** Codified month-level date math taking the union of dated intervals to ensure overlapping concurrent roles are not double-counted. Incomplete history cannot trigger automatic `FAIL`.
- **Immutable Runs & Overrides:** A screening run is write-once. Recruiter overrides record a new override revision with rationale; `original_result` and `effective_review_result` remain visible side-by-side.
- *Release 1 Boundary:* No automated pipeline advancement or rejection based on screening verdicts.

### 2.7 Evidence Summaries
- **Job-Grounded Synthesis:** AI-assisted factual summary mapping candidate facts directly to vacancy requirements.
- **Strict Evidence Taxonomy:**
  - `RESUME_SUPPORTED`: Directly cited against source segment.
  - `DERIVED_FROM_SUPPORTED_FACTS`: Computed deterministically from supported facts.
  - `UNKNOWN`: Information absent from resume.
  - `CONFLICT`: Contradictory information detected.
  - `HUMAN_ASSERTION_UNVERIFIED`: Manual human addition.
- **Review & Publication:** Recruiter reviews, edits, and explicitly publishes summaries before use.
- *Release 1 Boundary:* No external enrichment (e.g., scraping LinkedIn or GitHub); claims are restricted to uploaded documents.

### 2.8 Governed Scheduling & Availability
- **Interview Request Creation:** Configured by recruiter with required interviewers, stage, duration, horizon, buffer times, organizer, and timezone.
- **Candidate Link Bootstrap:** Secure capability link issued to verified candidate contact. Candidate submits preferred weekly availability windows and native IANA timezone.
- **Deterministic Slot Solver:** Intersects candidate windows, interviewer configured working hours, Google Calendar free/busy responses, internal reservations, and transit buffers.
- **Proposal Expiry:** Slot proposals carry explicit timestamps and expire after 15 minutes.
- *Release 1 Boundary:* One interview event and one panel per request; automated multi-round panel rotation is deferred.

### 2.9 Calendar Booking & Side Effect Saga
- **Human Approval Gate:** Recruiter explicitly reviews and approves the exact booking payload (attendees, start/end, calendar organizer, invitation text, notification flags).
- **Two-Phase Reservation:** PostgreSQL reservation using range exclusions (`tstzrange` with `btree_gist`) prevents internal double-booking.
- **Google Calendar Execution:** Dedicated background worker claims approved action, verifies payload hash and actor authority, executes calendar event insertion with deterministic ID, and reconciles state.
- **Uncertainty Recovery:** Network timeouts during creation enter `UNKNOWN` state for explicit reconciliation; no blind resends.
- *Release 1 Boundary:* Google Calendar and Gmail supported; Microsoft Graph deferred.

### 2.10 Interview Preparation Packs & Structured Feedback
- **Pack Generation:** Stage- and duration-aware pack combining candidate evidence overview, role requirements, technical/behavioral questions, anchored rubrics, and time budgets.
- **Premise Citations:** Candidate-specific question premises must cite evidence fragments; hypothetical questions are explicitly marked.
- **Anchored Feedback:** Interviewers evaluate candidates on a 1–4 competency scale plus `NOT_ASSESSED`.
- **Peer-Blind Isolation:** Peer feedback is completely inaccessible to an interviewer until their own feedback is submitted.
- *Release 1 Boundary:* Human interviewers record ratings; LLMs never score candidates or render hiring verdicts.

### 2.11 Document Exports
- **Format Support:** Editable DOCX (via `python-docx`) and read-only PDF (via `WeasyPrint`).
- **Sandboxed Rendering:** Export renderers operate with zero network access and disabled local file inclusion.
- **Export Governance:** Controlled downloads using short-lived signed capability tokens.
- *Release 1 Boundary:* Controlled manual downloads only; no automatic public links or unauthenticated attachments.

### 2.12 Governance, Audit & Privacy
- **Immutable Audit Trail:** Append-only database ledger recording actor, tenant, capability, resource, correlation ID, timestamp, and action summary.
- **Privacy Lifecycle:** Candidate data retention management (configurable 180-day default). Controlled deletion cascade across S3 objects, database tables, and AI cache.
- **Deletion Ledger:** Separate audit ledger ensuring restored backups re-apply deletions before resuming traffic.
- *Release 1 Boundary:* Customer jurisdiction and formal compliance certifications are external operational gates.

---

## 3. Explicit Release Boundaries & Deferred Features

| Feature / Capability | Status in Release 1 | Entry Criteria for Future Introduction | Rationale for Deferral |
| --- | --- | --- | --- |
| **Microsoft Graph Integration** | Deferred | Customer contract requiring M365 + adapter test suite | Focus on Google Workspace to prove the end-to-end booking saga first |
| **ATS / HRIS Integrations (Workday, Greenhouse)** | Deferred | Signed enterprise pilot + mapping contracts | Avoid competing systems of record during initial operational validation |
| **SAML 2.0 / SCIM Provisioning** | Deferred | Enterprise procurement requirement | Managed OIDC (Cognito/Keycloak) with invite flow suffices for pilot |
| **LangGraph Orchestration** | Deferred | Non-linear, multi-agent workflows exceeding state machines | Fixed extract-validate-screen pipelines are clearer with explicit state machines |
| **Redis Caching Layer** | Deferred | Measured PostgreSQL load showing repeated read bottlenecks | Prevent distributed caching bugs; PostgreSQL handles pilot transactional volume |
| **Vector Database / Embeddings** | Deferred | Multi-document cross-corpus retrieval requirements | Single resume + job description fits cleanly within LLM context windows |
| **Candidate Deduplication UI** | Deferred | Duplicate candidate volume justifying interactive merge | Duplicate warnings displayed; manual recruiter selection enforced |
| **Talent Rediscovery & Marketing Campaigns** | Deferred | Product-market validation & GDPR/DPDP consent review | Separate product surface; distinct regulatory consent requirements |
| **Multi-Region Active-Active Deployment** | Deferred | Documented data residency or multi-region scale mandates | Unnecessary infrastructure complexity and split-brain risk for initial pilot |

---

## 4. Engineering Targets & Non-Functional Limits

| Metric / Dimension | Release 1 Target | Measurement Method |
| --- | --- | --- |
| **Target Scale** | 10 tenants, 100 concurrent staff, 100,000 applications | Synthetic load generator, 20 req/s sustained |
| **Intake Capacity** | 100 files/batch, 10 MiB/file, 500 MiB/batch | Streaming upload test with concurrent file uploads |
| **PDF Extraction Budget** | Up to 30 pages, 200,000 chars, max 10 OCR pages | Resource-constrained parser worker with timeout watchdog |
| **DOCX Extraction Budget**| Max 50 MiB uncompressed, 2,000 entries | Defused XML and zip bomb mitigation bounds |
| **API Latency (p95)** | < 500 ms for reads, < 800 ms for database writes | Excludes external provider I/O and file transfers |
| **Job Admission Latency** | < 1,000 ms to return 202 Accepted + operation ID | Transactional outbox pattern measurement |
| **Text Extraction Latency**| p95 < 60 s (digital 5-page PDF), < 180 s (OCR) | Worker execution duration under reference load |
| **AI Generation Latency** | p95 < 120 s (pack/summary generation) | Model invocation + schema validation latency |
| **Target Availability** | 99.9% monthly uptime for core workspace | Synthetics monitoring on `/health/ready` |
| **Data Recovery Limits** | RPO ≤ 15 minutes, RTO ≤ 4 hours | Rehearsed AWS RDS PITR and S3 bucket versioning recovery |
| **Accessibility Standard**| WCAG 2.2 Level AA compliance | Automated Axe audits + manual keyboard navigation check |
| **Tenant Isolation** | 0% cross-tenant data leakage | Automated adversarial multi-tenant integration test suite |
