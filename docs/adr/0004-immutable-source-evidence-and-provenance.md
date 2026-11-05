# ADR-0004: Immutable Source Evidence and Provenance Model

## Status
Accepted

## Context
Recruitment AI products routinely suffer from hallucination, where models fabricate past employers, degrees, or years of experience. To ensure recruiter trust and regulatory defensibility (such as EU AI Act compliance), every assertion made by the system about a candidate must be inspectable, verifiable, and tied to an immutable source document.

## Decision
Talvora implements an **Immutable Source Evidence and Provenance Architecture**:

1. **Deconstruction into Source Segments:**
   - Raw extracted document text is segmented into immutable `SourceSegment` entities:
     - PDF documents: Segments correspond to structural layout blocks on specific 1-based page numbers.
     - DOCX documents: Segments correspond to structural XML body paragraphs and table-cell paths. (DOCX files do not have intrinsic page layouts; page numbers are never invented).
2. **Canonical Code-Point Indexing:**
   - Text offsets are recorded as Unicode code-point offsets (`start_offset`, `end_offset`), UTF-8 safe, accompanied by the SHA-256 hash of the canonicalized source segment text.
3. **Structured Candidate Facts with Direct Citations:**
   - Fact extraction produces atomic `CandidateFact` records linked to `EvidenceFragment` spans.
   - Every fact records:
     - `fact_type` (e.g., `EMPLOYMENT_INTERVAL`, `SKILL_ACQUISITION`, `DEGREE_ATTAINMENT`).
     - `exact_quote` (exact substring matching source span).
     - `evidence_fragment_id`.
     - `provenance_status`:
       - `RESUME_SUPPORTED`: Directly cited against source segment.
       - `DERIVED_FROM_SUPPORTED_FACTS`: Computed deterministically (e.g., duration calculation).
       - `UNKNOWN`: Missing from the document.
       - `CONFLICT`: Conflicting information documented.
       - `HUMAN_ASSERTION_UNVERIFIED`: Manual recruiter addition or correction.
4. **Immutability of Extractions:**
   - Once persisted, `SourceSegment`, `EvidenceFragment`, and `CandidateFact` records are write-once.
   - If a new resume is uploaded, a new document version is created. Existing candidate profiles and interview packs retain their original evidence binding snapshots unless explicitly rebound by an authorized recruiter.

## Consequences
- **Positive:**
  - Complete evidentiary audit trail: recruiters can click any claim to highlight the exact sentence in the original resume.
  - Zero tolerance for hallucinated qualifications; claims without citations fail validation.
- **Negative / Trade-offs:**
  - Additional database storage for granular segment and fragment records.
  - Normalization algorithm changes require re-extracting documents into new revision sets.
