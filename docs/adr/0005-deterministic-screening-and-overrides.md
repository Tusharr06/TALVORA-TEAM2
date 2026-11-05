# ADR-0005: Deterministic Screening Semantics and Recruiter Overrides

## Status
Accepted

## Context
Many recruitment platforms use opaque machine-learning algorithms to rank candidates or generate single "match scores" (e.g., 78%). These scores obscure the reasons for qualification or disqualification, introduce uncontrolled algorithmic bias, and violate emerging employment regulations. Furthermore, naive date subtraction frequently miscalculates candidate experience when overlapping jobs are double-counted.

## Decision
Talvora replaces opaque candidate scoring with a **Deterministic, Rule-Based Screening Engine** producing inspectable tri-state verdicts per criterion:

1. **Tri-State Verdict Architecture:**
   - Every published criterion is evaluated independently to produce:
     - `PASS`: Proven by cited candidate facts.
     - `FAIL`: Explicit, verified contradiction against complete documented history.
     - `NEEDS_REVIEW`: Missing facts, partial history, preferred criteria absent, or manual verification needed.
   - Criteria marked "preferred" (optional) never yield `FAIL` when missing; they yield `NEEDS_REVIEW` or an explicit unknown finding.
2. **Union-of-Intervals Date Arithmetic:**
   - For `MIN_EXPERIENCE_MONTHS`, candidate employment intervals are represented as half-open date ranges `[start_date, end_date)`.
   - The engine computes the mathematical union of all relevant intervals before calculating total elapsed calendar months.
   - Concurrent or overlapping positions are never double-counted.
   - Incomplete histories (e.g., candidate documents only recent roles) cannot trigger `FAIL`; they evaluate strictly to `NEEDS_REVIEW`.
3. **Immutable Runs and Human Overrides:**
   - A `ScreeningRun` is an immutable, versioned artifact bound to exact `RequirementVersion` and `ProfileRevision` IDs.
   - Recruiters cannot alter the machine's historical run output. Instead, an authorized recruiter submits a `ScreeningOverride` recording:
     - Target criterion result ID.
     - New reviewed verdict (`PASS`, `FAIL`, `NEEDS_REVIEW`).
     - Mandatory human rationale string.
     - Reviewer user ID and timestamp.
   - The UI displays both `original_result` and `effective_review_result` side-by-side.
4. **No Automated Pipeline Advancement:**
   - Screening outcomes never automatically advance or reject applications. Application stage movement remains an explicit human command.

## Consequences
- **Positive:**
  - 100% deterministic, explainable, and reproducible evaluation logic.
  - Recruiter accountability and complete auditability of human decisions.
- **Negative / Trade-offs:**
  - Requires explicit criteria definitions in job setup; unstructured or vague job postings cannot be screened automatically.
