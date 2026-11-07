<div align="center">TALVORA

AI-POWERED RECRUITING OPERATIONS AGENT

From Resume Overload to Evidence-Backed Hiring Decisions

"📊 PowerPoint Presentation" (https://drive.google.com/drive/folders/1XyR2gt36STJ2VR9j43cCBvlb_isr1IeT) | "📄 Resume & Supporting Documents" (https://drive.google.com/drive/folders/1YsnTODCHhIZ5GraGnFBUFEHf0uFaggWA) | "🎥 Demo Video" (https://drive.google.com/drive/folders/1Q2Vbi5UilTW_ITDmILGdm78qyK03gvNT)

AI WHERE IT HELPS. HUMANS WHERE IT MATTERS.

</div>---

🚀 1. Project Overview

Recruiting teams deal with hundreds of resumes, repetitive screening, scheduling delays, inconsistent candidate evaluation, and scattered interview feedback.

Traditional recruitment processes often involve manually reviewing resumes, comparing candidate qualifications, coordinating interview schedules through emails, and preparing interview documents independently.

TALVORA is an AI-powered Recruiting Operations Agent designed to streamline the recruitment lifecycle through structured automation, evidence-backed candidate evaluation, and intelligent workflow orchestration.

It helps recruiters:

- Convert job descriptions into structured hiring requirements.
- Process and extract information from candidate resumes.
- Screen candidates against explicit job criteria.
- Generate evidence-backed candidate summaries.
- Coordinate interview scheduling.
- Generate structured interview questions and evaluation rubrics.
- Maintain audit trails and privacy-aware workflows.

TALVORA does not replace recruiter judgement. It automates repetitive operations while keeping important decisions and external actions under human control.

🎯 2. Problem Statement

Recruitment operations face several challenges:

Challenge| Impact
Resume overload| Recruiters spend significant time manually reviewing applications.
Inconsistent screening| Different recruiters may interpret the same requirements differently.
Scheduling delays| Interview coordination involves repetitive email communication.
Unstructured interviews| Feedback may depend on subjective judgement rather than consistent rubrics.
Lack of traceability| Candidate evaluations may not have clear supporting evidence.
Privacy concerns| Candidate documents require secure processing and controlled access.

Our Solution

TALVORA introduces an evidence-first recruiting workflow that combines AI-assisted information extraction, deterministic screening, structured interview operations, and human approval mechanisms.

---

🔄 3. End-to-End Workflow

flowchart TD
    A["Create Job Vacancy"] --> B["Publish Versioned Requisition"]
    B --> C["Define Must-Have & Preferred Criteria"]
    C --> D["Upload Candidate Resumes"]
    D --> E["Quarantine & Malware Scanning"]
    E --> F["Document Extraction"]
    F --> G["AI-Based Fact Extraction"]
    G --> H["Deterministic Screening"]
    H --> I{"Screening Result"}
    I -->|MEETS| J["Evidence-Backed Summary"]
    I -->|DOES NOT MEET| K["Show Unmet Criteria"]
    I -->|UNCLEAR| L["Recruiter Review Required"]
    J --> M["Human Review"]
    K --> M
    L --> M
    M --> N["Interview Availability Link"]
    N --> O["Calendar Slot Selection"]
    O --> P["Human Approval"]
    P --> Q["Interview Pack Generation"]
    Q --> R["Structured Feedback"]
    R --> S["Audit Trail & Export"]

Workflow Stages

1. Requisition Intake

- Create a vacancy and publish a versioned job requisition.
- Separate mandatory requirements from preferred qualifications.
- Preserve the criteria version used during screening.

2. Resume Processing

- Upload candidate resumes.
- Store uploaded documents in quarantine.
- Scan files using ClamAV.
- Process PDF/DOCX documents and OCR-derived text where applicable.

3. Evidence Extraction

- Extract structured candidate information.
- Generate typed facts using AI.
- Preserve page-level and character-offset provenance.
- Ensure extracted information can be traced back to the original document.

4. Candidate Screening

- Evaluate candidates against explicit job requirements.
- Use deterministic rules for screening decisions.
- Generate one of three possible outcomes:
  - "MEETS"
  - "DOESN'T MEET"
  - "UNCLEAR"

5. Candidate Summarization

- Generate evidence-backed candidate summaries.
- Attach citations to relevant resume content.
- Apply quality checks to identify unsupported claims.
- Mark outdated information for review when source documents change.

6. Interview Scheduling

- Share availability links with candidates.
- Retrieve calendar free/busy information.
- Identify suitable interview slots.
- Require human approval before confirming bookings.

7. Interview Preparation

- Generate competency-based interview questions.
- Create evaluation rubrics.
- Support structured feedback collection.
- Export interview documents in DOCX/PDF formats.

---

🧠 4. Intelligent Screening Architecture

TALVORA separates AI-assisted extraction from deterministic screening decisions.

flowchart TD
    A["Candidate Resume"] --> B["AI Fact Extraction"]
    B --> C["Typed Candidate Facts"]
    C --> D["Source Citations"]
    D --> E["Load Job Requirements"]
    E --> F["Evaluate Must-Have Criteria"]
    F --> G{"Evidence Evaluation"}
    G -->|Requirement Satisfied| H["MEETS"]
    G -->|Requirement Not Satisfied| I["DOESN'T MEET"]
    G -->|Insufficient Evidence| J["UNCLEAR"]
    H --> K["Recruiter Review"]
    I --> K
    J --> K
    K --> L{"Recruiter Decision"}
    L -->|Accept| M["Continue Workflow"]
    L -->|Override| N["Record Override & Reason"]
    N --> M

Three-State Screening Model

Result| Meaning
MEETS| Available evidence supports the requirement.
DOESN'T MEET| Available evidence indicates the requirement is not satisfied.
UNCLEAR| Evidence is missing, ambiguous, or insufficient.

The system avoids guessing when evidence is unavailable. Recruiters can review the supporting information and override screening results with an auditable reason.

---

🏗️ 5. System Architecture

flowchart TB
    USER["Recruiter / Hiring Manager"]

    USER --> FRONTEND["React 19 + TypeScript"]

    FRONTEND --> API["FastAPI Modular Monolith"]

    API --> CONTRACT["Pydantic v2 / OpenAPI 3.1"]

    subgraph MODULES["Application Modules"]
        JOBS["Jobs & Requisitions"]
        CANDIDATES["Candidates & Applications"]
        DOCUMENTS["Documents & Evidence"]
        SCREENING["Facts & Screening"]
        SUMMARIES["Candidate Summaries"]
        SCHEDULING["Interview Scheduling"]
        INTERVIEWS["Interview Packs & Feedback"]
        GOVERNANCE["Audit, Analytics & Privacy"]
    end

    API --> MODULES

    MODULES --> DB[("PostgreSQL 17 + RLS")]

    DOCUMENTS --> STORAGE[("S3-Compatible Storage")]

    API --> OUTBOX["Transactional Outbox"]

    OUTBOX --> WORKERS["Celery Workers"]

    WORKERS --> AI["Structured AI Provider"]
    WORKERS --> SCAN["ClamAV"]
    WORKERS --> EMAIL["Mail Adapter"]
    WORKERS --> CALENDAR["Calendar Adapter"]
    WORKERS --> STORAGE
    WORKERS --> DB

Technology Stack

Component| Technology
Frontend| React 19
Programming Language| TypeScript
Backend| FastAPI
API Contracts| Pydantic v2
API Specification| OpenAPI 3.1
Database| PostgreSQL 17
Database Migrations| Alembic
Background Processing| Celery
Object Storage| S3-Compatible Storage
Malware Scanning| ClamAV
AI Integration| StructuredAIProvider
Email Integration| Mailpit / SES adapter path
Calendar Integration| Google Calendar / Gmail adapter path
Reliability| Transactional Outbox, Job Ledger, Leases and Fencing Tokens

Backend Architecture

The backend follows a modular monolith architecture with clearly separated responsibilities.

Core modules include:

- Jobs
- Candidates
- Applications
- Documents
- Evidence
- Facts
- Screening
- Summaries
- Interviews
- Scheduling
- Interview Packs
- Feedback
- Audit
- Analytics
- Privacy
- Operations

Each module follows four architectural layers:

1. Domain
2. Application
3. Infrastructure
4. API

This separation improves maintainability, testability, and extensibility.

---

🤖 6. AI Engineering Principles

TALVORA uses AI for information extraction and summarization while retaining deterministic application logic for important decisions.

AI-Assisted Operations| Deterministic Operations
Resume fact extraction| Job requirement evaluation
Unstructured information processing| Screening result calculation
Candidate summarization| Workflow state transitions
Interview question suggestions| Approval requirements
Structured AI responses| Validation and audit recording

Evidence-First AI

Every important extracted fact should be supported by source information.

- Typed Pydantic outputs.
- Source citations and document provenance.
- Page and character-offset references.
- Validation of AI-generated information.
- Quality gates for unsupported summary claims.
- Human review for uncertain information.

Prompt Injection Protection

Candidate documents are treated as untrusted data rather than instructions.

Resume content cannot redefine system behavior, bypass application policies, or independently authorize external actions.

AI outputs are constrained through typed schemas, validation, and application-level business rules.

---

🔐 7. Security, Privacy & Human Oversight

Security and governance are fundamental parts of the TALVORA architecture.

Tenant Isolation

- PostgreSQL Row-Level Security.
- Tenant-level data boundaries.

Document Security

- Quarantine-to-clean document lifecycle.
- ClamAV malware scanning.
- Controlled access to private candidate documents.

Human Oversight

- Recruiter review of screening outcomes.
- Explicit override mechanisms.
- Human approval before interview booking.
- Auditable actions.

Privacy

- Privacy lifecycle workflows.
- Controlled access to candidate information.
- Secure handling of sensitive documents.

Production Safety

- Production startup checks.
- Rejection of development adapters and unsafe configurations.

---

⚙️ 8. Background Processing & Reliability

TALVORA uses asynchronous workers for long-running operations such as document processing, AI extraction, and workflow tasks.

flowchart LR
    A["API Request"] --> B["Database Transaction"]
    B --> C["Persist Business State"]
    B --> D["Write Outbox Event"]
    D --> E["Outbox Publisher"]
    E --> F["Message Queue"]
    F --> G["Worker Claims Lease"]
    G --> H["Execute Job Idempotently"]
    H --> I["Persist Result"]
    I --> J["Audit Completion"]
    G -->|Crash / Lease Expiry| K["Recovery Mechanism"]
    K --> G

Reliability Mechanisms

- Transactional outbox pattern.
- Durable job ledger.
- Worker leases.
- Fencing tokens.
- Idempotent consumers.
- Duplicate-delivery handling.
- Worker crash recovery.
- Structured error handling.

These mechanisms help maintain consistency across asynchronous operations.

---

🧪 9. Testing & Evaluation

The project emphasizes reliability, contract validation, and AI evaluation.

Testing Area| Coverage
Unit Testing| 205+ unit tests
Database Integration| PostgreSQL, RLS and triggers
Object Storage| S3-compatible testing using Moto
Malware Scanning| ClamAV integration
Email| SMTP capture / Mailpit-style testing
API Contracts| Frozen OpenAPI validation
Static Analysis| Linting and strict mypy
Worker Reliability| Crash, lease expiry and duplicate delivery scenarios
AI Evaluation| Golden-set screening and extraction coverage
OCR Evaluation| OCR fixture testing
AI Quality| Unsupported-claim validation
Performance| Query-count guards and p95 budgets
Release Validation| Synthetic end-to-end workflow

Quality Assurance

The CI workflow validates:

- Code quality.
- Type safety.
- Unit and integration tests.
- API contract compatibility.
- Worker reliability.
- AI evaluation checks.
- Performance constraints.

---

🎬 10. Demo Walkthrough

The demonstration covers a complete recruiting workflow.

1. Create a vacancy and publish its requisition version.
2. Upload three synthetic candidate resumes.
3. Demonstrate document quarantine and malware scanning.
4. Extract candidate information with source citations.
5. Display tri-state screening outcomes.
6. Override a screening result as a recruiter.
7. Generate a cited candidate summary.
8. Share an interview availability link.
9. Find suitable calendar slots and approve a booking.
10. Generate an interview pack.
11. Collect structured feedback.
12. Export the interview document.

The demo focuses on inspectable artifacts, evidence-backed outputs, safe failure handling, and human-controlled decisions.

---

📈 11. Business Impact

Recruiting Process| TALVORA's Intended Impact
Resume Screening| Reduce repetitive manual screening effort.
Candidate Evaluation| Improve consistency through shared criteria.
Interview Scheduling| Replace email ping-pong with availability links.
Interview Preparation| Provide structured questions and rubrics.
Candidate Summaries| Make important claims traceable to evidence.
Compliance| Improve auditability and privacy workflows.

Dayforce Alignment

TALVORA is designed as a recruiting operations layer that can complement the broader hire-to-pay lifecycle.

Its alignment includes:

- Connecting recruiting outcomes with downstream onboarding processes.
- Supporting structured talent evaluation.
- Maintaining evidence and audit trails.
- Prioritizing privacy and operational trust.
- Reducing manual recruiting coordination.

---

🔮 12. Future Enhancements

Potential future improvements include:

- Consent-aware talent rediscovery.
- Multilingual OCR and resume parsing.
- Offer and onboarding handoff.
- LLM evaluation calibration.
- Prompt regression testing.
- Outlook and Microsoft Teams integrations.
- Additional job-board integrations.
- Recruiter analytics.
- WCAG AA accessibility improvements.
- Internationalization.

---

📂 13. Project Resources

Resource| Access
PowerPoint Presentation| "Open Presentation" (https://drive.google.com/drive/folders/1XyR2gt36STJ2VR9j43cCBvlb_isr1IeT)
Resume & Supporting Documents| "Open Resume Folder" (https://drive.google.com/drive/folders/1YsnTODCHhIZ5GraGnFBUFEHf0uFaggWA)
Project Demo Video| "Watch Demo" (https://drive.google.com/drive/folders/1Q2Vbi5UilTW_ITDmILGdm78qyK03gvNT)

---

👥 14. Team Members

Name| USN
Ameena G| 1DS23CS024
Morusu Harshitha| 1DS23CS136
Piyush Kumar| 1DS23IS114
Aparajeeta Singh| 1DT23CS024
Vaishnavi Dubalgunde| 1DT23CS238
Abhinava P| 1DS23CS009
Tushar R| 1DS23IS173
G N Madhu| 1DT23CS063

---

💡 Our Philosophy

«Automate the repetitive. Explain the evidence. Escalate the uncertain. Keep humans accountable.»

TALVORA aims to make recruitment faster without making hiring decisions opaque.

AI should organize information, reduce operational friction, and support better decisions—but trustworthy hiring must always be grounded in evidence, clear rules, privacy, and human judgement.

<div align="center">TALVORA

Evidence-Led Recruiting. Human-Led Decisions.

</div>