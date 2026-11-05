# ADR-0010: Constrained Parser Execution and Document Sandbox

## Status
Accepted

## Context
Resume intake requires processing untrusted binary files (PDF and DOCX) uploaded by external applicants. Document parsers (e.g., C/C++ rendering libraries inside PDF utilities, XML parsers in DOCX processors, OCR engines) have a long history of critical security vulnerabilities: memory corruption, ZIP bombs, XML External Entity (XXE) attacks, and infinite parsing loops causing CPU exhaustion.

## Decision
Talvora isolates document parsing and OCR workloads through a **Multi-Layered Sandboxing and Defense-in-Depth Architecture**:

1. **Quarantined Upload Storage:**
   - Uploaded files stream directly to an isolated S3 quarantine prefix (`s3://talvora-quarantine/...`).
   - Objects in quarantine are not accessible via standard application URLs.
2. **Mandatory Antivirus Scanning (ClamAV):**
   - Every file is scanned by a ClamAV scanning daemon prior to admission.
   - Files containing detected malware signatures are flagged `UNSAFE_DOCUMENT`, quarantined permanently, and blocked from further processing.
3. **Container-Level Sandbox Isolation:**
   - Document extraction and OCR run in a dedicated Celery worker profile (`celery_doc`).
   - The worker container operates with:
     - Rootless execution (`USER talvora`).
     - Read-only root filesystem (`read_only: true`) with bounded `tmpfs` for temporary file writes.
     - Strict resource ceilings: 1 CPU core, 1 GB RAM, 120-second hard execution watchdog.
     - Zero network egress: Container network is completely severed or blocked from external internet access via security groups.
4. **Defensive Parsing Budgets:**
   - **PDF:** Bounded to 30 pages maximum and 200,000 characters. Scanned pages requiring OCR are bounded to 10 pages maximum per document.
   - **DOCX:** Bounded to 50 MiB uncompressed size, 2,000 internal ZIP entries, and compression ratio ceilings to defeat ZIP bombs.
   - **XML:** All XML parsing is executed via `defusedxml` with external entity resolution, DTD expansion, and external stylesheet loading strictly disabled.
5. **Quality Warnings vs Failures:**
   - Documents with degraded layouts, corrupted fonts, or OCR ambiguity emit actionable `QualityWarning` records rather than crashing silently.

## Consequences
- **Positive:**
  - Robust containment: a compromised parser cannot access the internet, read credentials, or attack the database.
  - Predictable resource utilization preventing Denial of Service.
- **Negative / Trade-offs:**
  - Documents exceeding 30 pages or 10 OCR pages cannot be processed automatically and require manual recruiter handling.
