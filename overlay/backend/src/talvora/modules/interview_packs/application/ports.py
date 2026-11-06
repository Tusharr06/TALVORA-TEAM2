"""Application Ports and Adapters for Interview Packs (P15).

1. `InterviewContextPort`: Hexagonal seam resolving booked interview metadata
   (duration, stage, schedule revision, assigned participants) without importing
   sibling P11-P14 ORM modules. Automatically bridges to P14's `interview` table
   when present in the database, falling back to `interview_pack_context`.
2. `StructuredPackAIProvider`: Task-oriented, tool-free structured output port
   that receives delimited untrusted inputs and emits a schema-constrained draft.
   It has zero access to calendar, mail, database, or pipeline-transition tools.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from talvora.modules.interview_packs.domain.models import (
    InterviewPackContext,
    PackClaimClassification,
    PackContentScope,
    PremiseClassification,
    QuestionCategory,
    compute_time_allocation,
    delimit_untrusted_text,
    detect_prompt_injection,
)
from talvora.shared.transport.problems import ProblemException


@dataclass(frozen=True)
class InterviewSnapshot:
    """Immutable view of a booked/scheduled interview required by P15-P17."""

    id: UUID
    tenant_id: UUID
    application_id: UUID
    job_id: UUID
    candidate_id: UUID
    stage: str
    seniority_level: str
    duration_minutes: int
    schedule_revision: int
    status: str
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    timezone: str
    participant_user_ids: tuple[UUID, ...]


class InterviewContextPort(Protocol):
    """Port for resolving booked interview state across P14 and P15-P17."""

    def get_interview(
        self, session: Session, tenant_id: UUID, interview_id: UUID
    ) -> InterviewSnapshot | None: ...

    def list_interviews_for_application(
        self, session: Session, tenant_id: UUID, application_id: UUID
    ) -> list[InterviewSnapshot]: ...

    def list_interviews_for_tenant(
        self, session: Session, tenant_id: UUID
    ) -> list[InterviewSnapshot]: ...


class DefaultInterviewContextAdapter:
    """Resolves interview context from `interview_pack_context` and dynamically
    bridges to P14's `interview` table when present after branch integration."""

    @staticmethod
    def _from_context_row(row: InterviewPackContext) -> InterviewSnapshot:
        participants: list[UUID] = []
        for raw_uid in row.participant_user_ids or []:
            try:
                participants.append(UUID(str(raw_uid)))
            except ValueError:
                continue
        return InterviewSnapshot(
            id=row.id,
            tenant_id=row.tenant_id,
            application_id=row.application_id,
            job_id=row.job_id,
            candidate_id=row.candidate_id,
            stage=row.stage,
            seniority_level=row.seniority_level,
            duration_minutes=row.duration_minutes,
            schedule_revision=row.schedule_revision,
            status=row.status,
            scheduled_start_at=row.scheduled_start_at,
            scheduled_end_at=row.scheduled_end_at,
            timezone=row.timezone,
            participant_user_ids=tuple(participants),
        )

    def _try_reflective_p14_lookup(
        self, session: Session, tenant_id: UUID, interview_id: UUID
    ) -> InterviewSnapshot | None:
        """If P14's `interview` table exists in the connected DB, reflectively read it."""
        try:
            inspector = sa_inspect(session.connection())
            table_names = set(inspector.get_table_names())
        except Exception:
            return None

        if "interview" not in table_names:
            return None

        cols = {c["name"] for c in inspector.get_columns("interview")}
        if not {"id", "tenant_id", "application_id"}.issubset(cols):
            return None

        row = (
            session.execute(
                sa.text(
                    "SELECT * FROM interview WHERE tenant_id = :tenant_id AND id = :interview_id"
                ),
                {"tenant_id": str(tenant_id), "interview_id": str(interview_id)},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None

        app_row = (
            session.execute(
                sa.text(
                    "SELECT job_id, candidate_id FROM application "
                    "WHERE tenant_id = :tenant_id AND id = :app_id"
                ),
                {"tenant_id": str(tenant_id), "app_id": str(row["application_id"])},
            )
            .mappings()
            .first()
        )
        if app_row is None:
            return None

        participants: list[UUID] = []
        if "interview_participant" in table_names:
            part_cols = {c["name"] for c in inspector.get_columns("interview_participant")}
            if {"tenant_id", "user_id"}.issubset(part_cols):
                fk_col = "interview_id" if "interview_id" in part_cols else None
                if fk_col:
                    part_rows = session.execute(
                        sa.text(
                            f"SELECT user_id FROM interview_participant "
                            f"WHERE tenant_id = :tenant_id AND {fk_col} = :iid"
                        ),
                        {"tenant_id": str(tenant_id), "iid": str(interview_id)},
                    ).fetchall()
                    for pr in part_rows:
                        if pr[0] is not None:
                            participants.append(UUID(str(pr[0])))

        return InterviewSnapshot(
            id=UUID(str(row["id"])),
            tenant_id=tenant_id,
            application_id=UUID(str(row["application_id"])),
            job_id=UUID(str(row.get("job_id") or app_row["job_id"])),
            candidate_id=UUID(str(row.get("candidate_id") or app_row["candidate_id"])),
            stage=str(row.get("stage") or "TECHNICAL"),
            seniority_level=str(row.get("seniority_level") or "SENIOR"),
            duration_minutes=int(row.get("duration_minutes") or 60),
            schedule_revision=int(row.get("schedule_revision") or row.get("version") or 1),
            status=str(row.get("status") or "BOOKED"),
            scheduled_start_at=None,
            scheduled_end_at=None,
            timezone=str(row.get("timezone") or "UTC"),
            participant_user_ids=tuple(participants),
        )

    def get_interview(
        self, session: Session, tenant_id: UUID, interview_id: UUID
    ) -> InterviewSnapshot | None:
        ctx = session.scalar(
            select(InterviewPackContext).where(
                InterviewPackContext.tenant_id == tenant_id,
                InterviewPackContext.id == interview_id,
            )
        )
        if ctx is not None:
            return self._from_context_row(ctx)
        return self._try_reflective_p14_lookup(session, tenant_id, interview_id)

    def list_interviews_for_application(
        self, session: Session, tenant_id: UUID, application_id: UUID
    ) -> list[InterviewSnapshot]:
        rows = session.scalars(
            select(InterviewPackContext)
            .where(
                InterviewPackContext.tenant_id == tenant_id,
                InterviewPackContext.application_id == application_id,
            )
            .order_by(InterviewPackContext.created_at.asc())
        ).all()
        return [self._from_context_row(r) for r in rows]

    def list_interviews_for_tenant(
        self, session: Session, tenant_id: UUID
    ) -> list[InterviewSnapshot]:
        rows = session.scalars(
            select(InterviewPackContext)
            .where(InterviewPackContext.tenant_id == tenant_id)
            .order_by(InterviewPackContext.created_at.asc())
        ).all()
        return [self._from_context_row(r) for r in rows]


# ============================================================================
# Structured Pack AI Provider Port & Default Grounded Implementation
# ============================================================================


@dataclass(frozen=True)
class SupportedFactInput:
    """Verified or extracted candidate fact with bound evidence quotes."""

    fact_revision_id: UUID
    fact_type: str
    value: str
    status: str
    polarity: str
    evidence_fragment_ids: tuple[UUID, ...]
    evidence_quotes: tuple[str, ...]


@dataclass(frozen=True)
class CompetencyInput:
    """Pinned competency definition from a published framework version."""

    competency_id: UUID
    code: str
    name: str
    definition: str
    observation_required: bool
    rubric_anchors: dict[str, str]


@dataclass(frozen=True)
class CriterionInput:
    """Pinned job requirement criterion."""

    criterion_id: UUID
    criterion_type: str
    is_required: bool
    source_requirement_text: str


@dataclass(frozen=True)
class PackGenerationInput:
    """Minimal approved, delimited input bundle passed to `StructuredPackAIProvider`."""

    job_title: str
    delimited_job_description: str
    stage: str
    seniority_level: str
    duration_minutes: int
    max_questions_budget_minutes: int
    criteria: tuple[CriterionInput, ...]
    competencies: tuple[CompetencyInput, ...]
    supported_facts: tuple[SupportedFactInput, ...]
    unknown_criterion_descriptions: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedClaimSpec:
    """Atomic statement emitted by a pack generator."""

    section: str
    content_scope: str
    classification: str
    statement: str
    fact_revision_id: UUID | None = None
    evidence_fragment_ids: tuple[UUID, ...] = ()
    source_note: str | None = None


@dataclass(frozen=True)
class GeneratedQuestionSpec:
    """Structured interview question emitted by a pack generator."""

    category: str
    content_scope: str
    premise_classification: str
    competency_id: UUID
    allocated_minutes: int
    prompt_text: str
    premise_text: str | None
    expected_signals: tuple[str, ...]
    fact_revision_id: UUID | None = None
    evidence_fragment_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class GeneratedPackOutput:
    """Schema-constrained output from `StructuredPackAIProvider`."""

    generator_id: str
    candidate_overview: str
    candidate_evidence_summary: str
    interviewer_instructions: str
    notes_guidance: str
    claims: tuple[GeneratedClaimSpec, ...]
    questions: tuple[GeneratedQuestionSpec, ...]
    input_tokens: int = 0
    output_tokens: int = 0
    quality_warnings: tuple[str, ...] = field(default_factory=tuple)


class StructuredPackAIProvider(Protocol):
    """Tool-free structured AI port for interview pack generation."""

    def generate_pack(self, pack_input: PackGenerationInput) -> GeneratedPackOutput: ...


class DeterministicEvidenceGroundedPackProvider:
    """Deterministic, zero-network pack synthesizer for local development and tests.

    Strictly grounds candidate-specific claims and project/skill premises in verified
    `SupportedFactInput` records, emits explicit `UNKNOWN` / `CLARIFICATION` items
    when education/project facts are absent, calibrates depth to seniority without
    personality inference, and allocates question durations within `duration_minutes`.
    """

    generator_id: str = "deterministic-evidence-grounded-v1"

    def generate_pack(self, pack_input: PackGenerationInput) -> GeneratedPackOutput:
        if not pack_input.competencies:
            raise ProblemException(
                status_code=422,
                title="Empty Competency Framework",
                code="VALIDATION_ERROR",
                detail="Cannot generate an interview pack without at least one competency.",
            )

        warnings: list[str] = []
        delimited_probe = delimit_untrusted_text(
            "job_description", pack_input.delimited_job_description
        )
        injections = detect_prompt_injection(delimited_probe)
        if injections:
            warnings.append(
                "Prompt-injection directive detected in untrusted job text and neutralized."
            )

        for fact in pack_input.supported_facts:
            if detect_prompt_injection(fact.value) or any(
                detect_prompt_injection(q) for q in fact.evidence_quotes
            ):
                warnings.append(
                    f"Prompt-injection marker in candidate fact {fact.fact_revision_id} ignored; "
                    "no external tools or hiring verdicts are available."
                )

        affirmed_facts = [
            f
            for f in pack_input.supported_facts
            if f.polarity == "AFFIRMED" and f.evidence_fragment_ids
        ]
        skill_facts = [f for f in affirmed_facts if f.fact_type == "SKILL"]
        exp_facts = [f for f in affirmed_facts if f.fact_type in ("EXPERIENCE_MONTHS", "OTHER")]
        edu_facts = [f for f in affirmed_facts if f.fact_type == "EDUCATION_LEVEL"]

        claims: list[GeneratedClaimSpec] = []
        for sf in affirmed_facts:
            clean_val = sf.value
            if detect_prompt_injection(clean_val):
                continue
            section = "PROJECT" if sf.fact_type == "OTHER" else "OVERVIEW"
            claims.append(
                GeneratedClaimSpec(
                    section=section,
                    content_scope=PackContentScope.CANDIDATE_SPECIFIC.value,
                    classification=PackClaimClassification.RESUME_SUPPORTED.value,
                    statement=clean_val,
                    fact_revision_id=sf.fact_revision_id,
                    evidence_fragment_ids=sf.evidence_fragment_ids,
                    source_note=f"Grounded in {sf.fact_type.lower()} fact ({sf.status}).",
                )
            )

        if not exp_facts:
            claims.append(
                GeneratedClaimSpec(
                    section="PROJECT",
                    content_scope=PackContentScope.CANDIDATE_SPECIFIC.value,
                    classification=PackClaimClassification.UNKNOWN.value,
                    statement="Project-level scope and production ownership details are not established in the bound resume.",
                    source_note="Absent project detail; verify live during interview.",
                )
            )
        if not edu_facts:
            claims.append(
                GeneratedClaimSpec(
                    section="OVERVIEW",
                    content_scope=PackContentScope.CANDIDATE_SPECIFIC.value,
                    classification=PackClaimClassification.UNKNOWN.value,
                    statement="Formal degree attainment is not established in the bound resume facts.",
                    source_note="Absent education fact; verify only if required by job criteria.",
                )
            )

        seniority_upper = pack_input.seniority_level.upper()
        is_junior = "JUNIOR" in seniority_upper or "ENTRY" in seniority_upper
        depth_descriptor = (
            "foundational implementation clarity, debugging approach, and learning from code review"
            if is_junior
            else "system trade-offs, operational reliability, failure recovery, and cross-team technical execution"
        )

        # Allocate minutes strictly within `max_questions_budget_minutes`
        budget = pack_input.max_questions_budget_minutes
        # Build up to 5 questions covering all categories; trim count/minutes for short interviews
        if budget <= 21:
            # e.g. 30-minute interview (21 min question budget): 4 concise questions (5+5+6+5 = 21)
            slot_minutes = [5, 5, 6, 5]
        else:
            # e.g. 60-minute interview (45 min question budget): 5 thorough questions (9+8+10+10+8 = 45)
            per_q = max(4, budget // 5)
            slot_minutes = [per_q, per_q, per_q, per_q, budget - 4 * per_q]

        comps = pack_input.competencies
        questions: list[GeneratedQuestionSpec] = []

        # 1. TECHNICAL (Role-generic or skill-grounded)
        c0 = comps[0 % len(comps)]
        if skill_facts and not detect_prompt_injection(skill_facts[0].value):
            sf0 = skill_facts[0]
            questions.append(
                GeneratedQuestionSpec(
                    category=QuestionCategory.TECHNICAL.value,
                    content_scope=PackContentScope.CANDIDATE_SPECIFIC.value,
                    premise_classification=PremiseClassification.RESUME_SUPPORTED.value,
                    competency_id=c0.competency_id,
                    allocated_minutes=slot_minutes[0],
                    prompt_text=(
                        f"Your resume cites experience with {sf0.value}. Walk through a concrete "
                        f"engineering problem where you applied {sf0.value}, focusing on {depth_descriptor}."
                    ),
                    premise_text=sf0.value,
                    expected_signals=(
                        "Explains concrete constraints, failure modes, and verification steps",
                        "Distinguishes personal technical contribution from team work",
                    ),
                    fact_revision_id=sf0.fact_revision_id,
                    evidence_fragment_ids=sf0.evidence_fragment_ids,
                )
            )
        else:
            questions.append(
                GeneratedQuestionSpec(
                    category=QuestionCategory.TECHNICAL.value,
                    content_scope=PackContentScope.ROLE_GENERIC.value,
                    premise_classification=PremiseClassification.ROLE_GENERIC.value,
                    competency_id=c0.competency_id,
                    allocated_minutes=slot_minutes[0],
                    prompt_text=(
                        f"For the {pack_input.job_title} role ({pack_input.stage} stage), describe how "
                        f"you approach {depth_descriptor} when implementing a core backend change."
                    ),
                    premise_text=None,
                    expected_signals=(
                        "Identifies correctness invariants and test verification strategy",
                        "Articulates concrete trade-offs relevant to role scope",
                    ),
                )
            )

        # 2. BEHAVIORAL (Observable job-relevant past action, zero personality inference)
        c1 = comps[1 % len(comps)]
        questions.append(
            GeneratedQuestionSpec(
                category=QuestionCategory.BEHAVIORAL.value,
                content_scope=PackContentScope.ROLE_GENERIC.value,
                premise_classification=PremiseClassification.ROLE_GENERIC.value,
                competency_id=c1.competency_id,
                allocated_minutes=slot_minutes[1],
                prompt_text=(
                    "Describe a time when requirements or technical assumptions changed mid-delivery. "
                    "What observable steps did you take to align stakeholders and verify the outcome?"
                ),
                premise_text=None,
                expected_signals=(
                    "Describes specific situation, actions taken, and measurable engineering outcome",
                    "Shows transparent risk communication grounded in evidence",
                ),
            )
        )

        # 3. SCENARIO (Explicitly labeled HYPOTHETICAL - never implies candidate did this work)
        c2 = comps[2 % len(comps)]
        questions.append(
            GeneratedQuestionSpec(
                category=QuestionCategory.SCENARIO.value,
                content_scope=PackContentScope.ROLE_GENERIC.value,
                premise_classification=PremiseClassification.HYPOTHETICAL.value,
                competency_id=c2.competency_id,
                allocated_minutes=slot_minutes[2],
                prompt_text=(
                    "Hypothetical scenario: suppose a multi-tenant API begins exhibiting intermittent "
                    "lock contention under peak batch uploads. How would you diagnose, isolate, and mitigate it?"
                ),
                premise_text="Hypothetical operational scenario for role assessment.",
                expected_signals=(
                    "Proposes systematic observability checks before speculative changes",
                    "Protects data integrity and tenant isolation during mitigation",
                ),
            )
        )

        # 4. PROJECT_SPECIFIC (if supported fact exists) OR CLARIFICATION (if absent / short budget)
        c3 = comps[3 % len(comps)]
        if len(slot_minutes) == 4:
            unknown_topic = (
                pack_input.unknown_criterion_descriptions[0]
                if pack_input.unknown_criterion_descriptions
                else "the scope, scale, and verification approach of your most recent technical project"
            )
            questions.append(
                GeneratedQuestionSpec(
                    category=QuestionCategory.CLARIFICATION.value,
                    content_scope=PackContentScope.CANDIDATE_SPECIFIC.value,
                    premise_classification=PremiseClassification.CLARIFICATION_NEEDED.value,
                    competency_id=c3.competency_id,
                    allocated_minutes=slot_minutes[3],
                    prompt_text=(
                        f"Clarification needed: the submitted resume does not fully detail {unknown_topic}. "
                        "Could you clarify your direct hands-on responsibilities and outcomes in this area?"
                    ),
                    premise_text=f"Unestablished or incomplete resume evidence regarding {unknown_topic}.",
                    expected_signals=(
                        "Provides specific, verifiable scope and concrete examples",
                        "Clarifies direct ownership vs. assisted work",
                    ),
                )
            )
        else:
            if affirmed_facts and not detect_prompt_injection(affirmed_facts[-1].value):
                pf = affirmed_facts[-1]
                questions.append(
                    GeneratedQuestionSpec(
                        category=QuestionCategory.PROJECT_SPECIFIC.value,
                        content_scope=PackContentScope.CANDIDATE_SPECIFIC.value,
                        premise_classification=PremiseClassification.RESUME_SUPPORTED.value,
                        competency_id=c3.competency_id,
                        allocated_minutes=slot_minutes[3],
                        prompt_text=(
                            f"Based on your documented background ({pf.value}), what was the hardest "
                            "verification or production trade-off you personally resolved, and how did you measure it?"
                        ),
                        premise_text=pf.value,
                        expected_signals=(
                            "Connects design choices to concrete constraints and outcomes",
                            "Explains trade-offs and lessons learned without overstating scope",
                        ),
                        fact_revision_id=pf.fact_revision_id,
                        evidence_fragment_ids=pf.evidence_fragment_ids,
                    )
                )
            else:
                questions.append(
                    GeneratedQuestionSpec(
                        category=QuestionCategory.SCENARIO.value,
                        content_scope=PackContentScope.ROLE_GENERIC.value,
                        premise_classification=PremiseClassification.HYPOTHETICAL.value,
                        competency_id=c3.competency_id,
                        allocated_minutes=slot_minutes[3],
                        prompt_text=(
                            "Hypothetical scenario: imagine inheriting a service lacking automated migration "
                            "checks ahead of a schema change. How would you de-risk the rollout?"
                        ),
                        premise_text="Hypothetical project scenario (no project facts established on resume).",
                        expected_signals=(
                            "Outlines backward-compatible migration steps and rollback plan",
                            "Defines concrete pre-release verification checks",
                        ),
                    )
                )

            c4 = comps[4 % len(comps)]
            unknown_topic = (
                pack_input.unknown_criterion_descriptions[0]
                if pack_input.unknown_criterion_descriptions
                else "specific production scale, team size, and verification metrics"
            )
            questions.append(
                GeneratedQuestionSpec(
                    category=QuestionCategory.CLARIFICATION.value,
                    content_scope=PackContentScope.CANDIDATE_SPECIFIC.value,
                    premise_classification=PremiseClassification.CLARIFICATION_NEEDED.value,
                    competency_id=c4.competency_id,
                    allocated_minutes=slot_minutes[4],
                    prompt_text=(
                        f"Clarification question: to complete evidence for {unknown_topic}, "
                        "please walk through your direct responsibilities and time horizon."
                    ),
                    premise_text=f"Clarify unestablished detail: {unknown_topic}.",
                    expected_signals=(
                        "Supplies clear timeline, direct scope, and concrete artifacts",
                        "Addresses open screening questions transparently",
                    ),
                )
            )

        # Double-check timing invariant before returning
        compute_time_allocation(
            pack_input.duration_minutes,
            sum(q.allocated_minutes for q in questions),
        )

        supported_preview = (
            "; ".join(f"{f.fact_type.lower()}: {f.value}" for f in affirmed_facts[:5])
            if affirmed_facts
            else "No resume-supported facts established yet; treat candidate background as requiring live verification."
        )

        overview = (
            f"Interview preparation guide for {pack_input.job_title} "
            f"({pack_input.stage}, {pack_input.seniority_level}, {pack_input.duration_minutes} min). "
            "Evaluate observable job-relevant evidence only; do not infer personality or protected traits."
        )
        evidence_summary = f"Pinned evidence summary: {supported_preview}"
        instructions = (
            "1. Follow the allocated time boxes so the candidate has time for questions.\n"
            "2. Use expected signals as editable guidance, not a single rigid answer key.\n"
            "3. Record concrete behavioral/technical observations per competency using anchors 1-4 or NOT_ASSESSED.\n"
            "4. Submit your independent feedback before discussing with peer interviewers."
        )
        notes_guidance = (
            "Document verbatim technical examples, trade-offs articulated, and direct ownership vs. team assistance. "
            "Do not record protected personal attributes or speculative personality judgments."
        )

        est_in_tokens = max(
            120, len(pack_input.delimited_job_description) // 4 + len(affirmed_facts) * 40
        )
        est_out_tokens = max(180, len(questions) * 75)

        return GeneratedPackOutput(
            generator_id=self.generator_id,
            candidate_overview=overview,
            candidate_evidence_summary=evidence_summary,
            interviewer_instructions=instructions,
            notes_guidance=notes_guidance,
            claims=tuple(claims),
            questions=tuple(questions),
            input_tokens=est_in_tokens,
            output_tokens=est_out_tokens,
            quality_warnings=tuple(warnings),
        )
