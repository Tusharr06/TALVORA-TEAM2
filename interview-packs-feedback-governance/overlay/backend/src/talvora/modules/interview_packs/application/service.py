"""Application Services for Competency Frameworks and Interview Packs (P15)."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.documents.domain.models import (
    ApplicationDocument,
    ApplicationDocumentStatus,
)
from talvora.modules.evidence.domain.models import EvidenceFragment, SourceSegment
from talvora.modules.facts.domain.models import (
    CandidateFactEvidence,
    CandidateFactPolarity,
    CandidateFactRevision,
    CandidateFactStatus,
)
from talvora.modules.facts.domain.validation import is_evidence_supported
from talvora.modules.identity.application.authorization import AuthorizationService
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.interview_packs.application.ports import (
    CompetencyInput,
    CriterionInput,
    DefaultInterviewContextAdapter,
    DeterministicEvidenceGroundedPackProvider,
    GeneratedClaimSpec,
    GeneratedQuestionSpec,
    InterviewContextPort,
    InterviewSnapshot,
    PackGenerationInput,
    StructuredPackAIProvider,
    SupportedFactInput,
)
from talvora.modules.interview_packs.domain.models import (
    Competency,
    CompetencyFramework,
    CompetencyFrameworkVersion,
    FrameworkStatus,
    FrameworkVersionStatus,
    InterviewContextStatus,
    InterviewPackContext,
    PackClaim,
    PackClaimClassification,
    PackContentScope,
    PackQuestion,
    PackReview,
    PackReviewDecision,
    PackRevision,
    PackRevisionStatus,
    PremiseClassification,
    QuestionCategory,
    compute_time_allocation,
    delimit_untrusted_text,
    validate_hypothetical_question_framing,
    validate_job_relevant_observable_text,
    validate_rubric_anchors,
)
from talvora.modules.jobs.domain.models import (
    Criterion,
    Job,
    JobAssignment,
    JobRole,
    RequirementVersion,
    sanitize_text,
)
from talvora.modules.screening.domain.models import ScreeningRun
from talvora.modules.summaries.domain.models import CandidateSummaryRevision
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CompetencyDraftSpec:
    """Input DTO for defining a competency inside a framework version."""

    code: str
    name: str
    definition: str
    observation_required: bool
    rubric_anchors: dict[str, str]
    display_order: int = 0


@dataclass(frozen=True)
class PackClaimEditSpec:
    """Input DTO for editing or adding a claim on a pack revision."""

    section: str
    content_scope: str
    statement: str
    classification: str | None = None
    fact_revision_id: UUID | None = None
    evidence_fragment_ids: list[UUID] | None = None
    source_note: str | None = None


@dataclass(frozen=True)
class PackQuestionEditSpec:
    """Input DTO for editing or replacing a question on a pack revision."""

    category: str
    content_scope: str
    premise_classification: str | None
    competency_id: UUID
    allocated_minutes: int
    prompt_text: str
    premise_text: str | None = None
    expected_signals: list[str] | None = None
    fact_revision_id: UUID | None = None
    evidence_fragment_ids: list[UUID] | None = None


@dataclass(frozen=True)
class PackDetailBundle:
    """Hydrated pack revision with claims, questions, reviews, and live staleness."""

    revision: PackRevision
    claims: list[PackClaim]
    questions: list[PackQuestion]
    reviews: list[PackReview]
    is_stale: bool
    stale_reasons: list[str]


def _require_tenant_actor(actor: ActorContext) -> tuple[UUID, UUID]:
    if not actor.is_authenticated or actor.tenant_id is None or actor.user_id is None:
        raise ProblemException(
            status_code=401,
            title="Unauthorized",
            code="AUTH_REQUIRED",
            detail="Authenticated organization session is required.",
        )
    return actor.tenant_id, actor.user_id


def _has_job_staff_authority(
    uow: UnitOfWork,
    actor: ActorContext,
    tenant_id: UUID,
    job_id: UUID,
    allowed_job_roles: set[str],
) -> bool:
    """Checks OWNER/ADMIN org authority or explicit job assignment."""
    if actor.membership and actor.membership.role in {Role.OWNER.value, Role.ADMIN.value}:
        return True
    assignments = list(
        uow.session.scalars(
            select(JobAssignment).where(
                JobAssignment.tenant_id == tenant_id,
                JobAssignment.job_id == job_id,
            )
        ).all()
    )
    return any(a.user_id == actor.user_id and a.role in allowed_job_roles for a in assignments)


def _check_tenant_ai_pack_policy(uow: UnitOfWork, tenant_id: UUID) -> tuple[bool, int]:
    """Reads `organization_policy` if present (P17+), returning `(enabled, remaining_tokens)`."""
    try:
        inspector = sa_inspect(uow.session.connection())
        tables = set(inspector.get_table_names())
    except Exception:
        return True, 500_000

    if "organization_policy" not in tables:
        return True, 500_000

    policy_row = (
        uow.session.execute(
            sa.text(
                "SELECT ai_interview_packs_enabled, monthly_ai_token_budget "
                "FROM organization_policy WHERE tenant_id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
        .mappings()
        .first()
    )
    if policy_row is None:
        return True, 500_000

    enabled = bool(policy_row["ai_interview_packs_enabled"])
    budget = int(policy_row["monthly_ai_token_budget"])
    used = 0
    if "usage_event" in tables:
        used_val = uow.session.execute(
            sa.text(
                "SELECT COALESCE(SUM(total_units), 0) FROM usage_event "
                "WHERE tenant_id = :tid AND category = 'AI_TOKENS'"
            ),
            {"tid": str(tenant_id)},
        ).scalar()
        used = int(used_val or 0)
    return enabled, max(0, budget - used)


def _record_ai_pack_usage_if_supported(
    uow: UnitOfWork,
    tenant_id: UUID,
    pack_revision_id: UUID,
    generator_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Records AI token and pack generation usage if `usage_event` table exists (P17+)."""
    try:
        tables = set(sa_inspect(uow.session.connection()).get_table_names())
    except Exception:
        return
    if "usage_event" not in tables:
        return

    now = uow.clock.now()
    uow.session.execute(
        sa.text(
            "INSERT INTO usage_event "
            "(id, tenant_id, category, model_or_engine, input_units, output_units, "
            "total_units, resource_type, resource_id, recorded_at) "
            "VALUES (:id, :tid, :cat, :eng, :in_u, :out_u, :tot_u, :rtype, :rid, :ts)"
        ),
        {
            "id": str(uuid4()),
            "tid": str(tenant_id),
            "cat": "AI_TOKENS",
            "eng": generator_id,
            "in_u": input_tokens,
            "out_u": output_tokens,
            "tot_u": input_tokens + output_tokens,
            "rtype": "pack_revision",
            "rid": str(pack_revision_id),
            "ts": now,
        },
    )


class CompetencyFrameworkService:
    """Manages versioned, job/stage-scoped competency frameworks and observable rubrics."""

    @classmethod
    def create_framework(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        name: str,
        slug: str,
        stage: str,
        summary: str,
        behavioral_scale_guide: str,
        competencies: list[CompetencyDraftSpec],
        job_id: UUID | None = None,
        seniority_band: str = "MID_SENIOR",
    ) -> tuple[CompetencyFramework, CompetencyFrameworkVersion, list[Competency]]:
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.EDIT_JOB)

        if job_id is not None:
            job = uow.session.scalar(
                select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id)
            )
            if job is None:
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="NOT_FOUND",
                    detail="Target job vacancy was not found.",
                )
            if not _has_job_staff_authority(
                uow,
                actor,
                tenant_id,
                job_id,
                {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
            ):
                raise ProblemException(
                    status_code=403,
                    title="Forbidden",
                    code="FORBIDDEN",
                    detail="Actor is not assigned to manage competency frameworks for this job.",
                )

        existing_slug = uow.session.scalar(
            select(CompetencyFramework).where(
                CompetencyFramework.tenant_id == tenant_id,
                CompetencyFramework.slug == slug.strip().lower(),
            )
        )
        if existing_slug is not None:
            raise ProblemException(
                status_code=409,
                title="Framework Slug Conflict",
                code="VERSION_CONFLICT",
                detail=f"A competency framework with slug '{slug}' already exists.",
            )

        if not competencies:
            raise ProblemException(
                status_code=422,
                title="Missing Competencies",
                code="VALIDATION_ERROR",
                detail="At least one observable competency is required.",
            )

        clean_name = validate_job_relevant_observable_text(name, "framework name")
        clean_summary = validate_job_relevant_observable_text(summary, "framework summary")
        clean_guide = validate_job_relevant_observable_text(
            behavioral_scale_guide, "behavioral scale guide"
        )

        fw = CompetencyFramework(
            id=uuid4(),
            tenant_id=tenant_id,
            name=clean_name,
            slug=slug.strip().lower(),
            job_id=job_id,
            stage=stage.strip().upper(),
            seniority_band=seniority_band.strip().upper(),
            status=FrameworkStatus.DRAFT.value,
            latest_version_number=1,
            version=1,
            created_by=user_id,
        )
        uow.session.add(fw)
        uow.session.flush()

        fw_ver, comp_rows = cls._insert_version_and_competencies(
            uow=uow,
            tenant_id=tenant_id,
            framework_id=fw.id,
            version_number=1,
            summary=clean_summary,
            behavioral_scale_guide=clean_guide,
            competencies=competencies,
            created_by=user_id,
        )

        uow.record_audit(
            resource_type="competency_framework",
            action="FRAMEWORK_CREATED",
            resource_id=fw.id,
            actor_id=user_id,
            actor_capability=Capability.EDIT_JOB.value,
            details={
                "slug": fw.slug,
                "stage": fw.stage,
                "job_id": str(job_id) if job_id else None,
                "version_number": 1,
                "competency_count": len(comp_rows),
            },
        )
        return fw, fw_ver, comp_rows

    @classmethod
    def _insert_version_and_competencies(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        framework_id: UUID,
        version_number: int,
        summary: str,
        behavioral_scale_guide: str,
        competencies: list[CompetencyDraftSpec],
        created_by: UUID,
    ) -> tuple[CompetencyFrameworkVersion, list[Competency]]:
        seen_codes: set[str] = set()
        fw_ver = CompetencyFrameworkVersion(
            id=uuid4(),
            tenant_id=tenant_id,
            framework_id=framework_id,
            version_number=version_number,
            status=FrameworkVersionStatus.DRAFT.value,
            summary=summary,
            behavioral_scale_guide=behavioral_scale_guide,
            created_by=created_by,
        )
        uow.session.add(fw_ver)
        uow.session.flush()

        comp_rows: list[Competency] = []
        for idx, spec in enumerate(competencies):
            norm_code = spec.code.strip().upper()
            if not norm_code or norm_code in seen_codes:
                raise ProblemException(
                    status_code=422,
                    title="Duplicate or Empty Competency Code",
                    code="VALIDATION_ERROR",
                    detail=f"Competency code '{spec.code}' must be non-empty and unique within the version.",
                )
            seen_codes.add(norm_code)
            clean_cname = validate_job_relevant_observable_text(
                spec.name, f"competency '{norm_code}' name"
            )
            clean_cdef = validate_job_relevant_observable_text(
                spec.definition, f"competency '{norm_code}' definition"
            )
            clean_anchors = validate_rubric_anchors(spec.rubric_anchors, clean_cname)

            row = Competency(
                id=uuid4(),
                tenant_id=tenant_id,
                framework_version_id=fw_ver.id,
                code=norm_code,
                name=clean_cname,
                definition=clean_cdef,
                display_order=spec.display_order if spec.display_order else idx,
                observation_required=spec.observation_required,
                rubric_anchors=clean_anchors,
            )
            uow.session.add(row)
            comp_rows.append(row)

        uow.session.flush()
        return fw_ver, comp_rows

    @classmethod
    def create_new_version(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        framework_id: UUID,
        expected_framework_version: int,
        summary: str,
        behavioral_scale_guide: str,
        competencies: list[CompetencyDraftSpec],
    ) -> tuple[CompetencyFramework, CompetencyFrameworkVersion, list[Competency]]:
        """Appends a new DRAFT framework version without mutating any published version."""
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.EDIT_JOB)

        fw = uow.session.scalar(
            select(CompetencyFramework).where(
                CompetencyFramework.tenant_id == tenant_id,
                CompetencyFramework.id == framework_id,
            )
        )
        if fw is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Competency framework was not found.",
            )
        if fw.version != expected_framework_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Expected framework lock version {expected_framework_version}, found {fw.version}.",
            )
        if not competencies:
            raise ProblemException(
                status_code=422,
                title="Missing Competencies",
                code="VALIDATION_ERROR",
                detail="At least one competency is required.",
            )

        clean_summary = validate_job_relevant_observable_text(summary, "framework summary")
        clean_guide = validate_job_relevant_observable_text(
            behavioral_scale_guide, "behavioral scale guide"
        )
        next_ver_num = fw.latest_version_number + 1
        fw.latest_version_number = next_ver_num
        fw.version += 1

        fw_ver, comp_rows = cls._insert_version_and_competencies(
            uow=uow,
            tenant_id=tenant_id,
            framework_id=fw.id,
            version_number=next_ver_num,
            summary=clean_summary,
            behavioral_scale_guide=clean_guide,
            competencies=competencies,
            created_by=user_id,
        )
        uow.record_audit(
            resource_type="competency_framework_version",
            action="FRAMEWORK_VERSION_CREATED",
            resource_id=fw_ver.id,
            actor_id=user_id,
            actor_capability=Capability.EDIT_JOB.value,
            details={"framework_id": str(fw.id), "version_number": next_ver_num},
        )
        return fw, fw_ver, comp_rows

    @classmethod
    def publish_version(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        framework_id: UUID,
        version_number: int,
    ) -> tuple[CompetencyFramework, CompetencyFrameworkVersion, list[Competency]]:
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.PUBLISH_JOB_REQUIREMENTS)

        fw = uow.session.scalar(
            select(CompetencyFramework).where(
                CompetencyFramework.tenant_id == tenant_id,
                CompetencyFramework.id == framework_id,
            )
        )
        if fw is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Competency framework was not found.",
            )
        if fw.job_id is not None and not _has_job_staff_authority(
            uow,
            actor,
            tenant_id,
            fw.job_id,
            {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Actor is not assigned to publish competency frameworks for this job.",
            )

        fw_ver = uow.session.scalar(
            select(CompetencyFrameworkVersion).where(
                CompetencyFrameworkVersion.tenant_id == tenant_id,
                CompetencyFrameworkVersion.framework_id == framework_id,
                CompetencyFrameworkVersion.version_number == version_number,
            )
        )
        if fw_ver is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Requested framework version was not found.",
            )
        if fw_ver.status == FrameworkVersionStatus.PUBLISHED.value:
            raise ProblemException(
                status_code=409,
                title="Already Published",
                code="INVALID_TRANSITION",
                detail="Framework version is already published and immutable.",
            )

        comps = cls._list_competencies(uow, tenant_id, fw_ver.id)
        if not comps:
            raise ProblemException(
                status_code=422,
                title="Empty Framework Version",
                code="VALIDATION_ERROR",
                detail="Cannot publish a framework version with zero competencies.",
            )

        now = uow.clock.now()
        fw_ver.status = FrameworkVersionStatus.PUBLISHED.value
        fw_ver.published_by = user_id
        fw_ver.published_at = now
        fw.status = FrameworkStatus.ACTIVE.value
        fw.latest_published_version_id = fw_ver.id
        fw.latest_published_version_number = fw_ver.version_number
        fw.version += 1
        uow.session.flush()

        uow.record_audit(
            resource_type="competency_framework_version",
            action="FRAMEWORK_VERSION_PUBLISHED",
            resource_id=fw_ver.id,
            actor_id=user_id,
            actor_capability=Capability.PUBLISH_JOB_REQUIREMENTS.value,
            details={
                "framework_id": str(fw.id),
                "version_number": fw_ver.version_number,
            },
        )
        return fw, fw_ver, comps

    @classmethod
    def list_frameworks(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID | None = None,
        stage: str | None = None,
    ) -> list[CompetencyFramework]:
        tenant_id, _ = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.VIEW_JOBS)

        stmt = select(CompetencyFramework).where(CompetencyFramework.tenant_id == tenant_id)
        if job_id is not None:
            stmt = stmt.where(
                sa.or_(
                    CompetencyFramework.job_id == job_id,
                    CompetencyFramework.job_id.is_(None),
                )
            )
        if stage is not None:
            stmt = stmt.where(CompetencyFramework.stage == stage.strip().upper())
        stmt = stmt.order_by(CompetencyFramework.created_at.desc())
        return list(uow.session.scalars(stmt).all())

    @classmethod
    def get_framework_version(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        framework_id: UUID,
        version_number: int | None = None,
    ) -> tuple[CompetencyFramework, CompetencyFrameworkVersion, list[Competency]]:
        tenant_id, _ = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.VIEW_JOBS)

        fw = uow.session.scalar(
            select(CompetencyFramework).where(
                CompetencyFramework.tenant_id == tenant_id,
                CompetencyFramework.id == framework_id,
            )
        )
        if fw is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Competency framework was not found.",
            )

        target_ver = (
            version_number or fw.latest_published_version_number or fw.latest_version_number
        )
        fw_ver = uow.session.scalar(
            select(CompetencyFrameworkVersion).where(
                CompetencyFrameworkVersion.tenant_id == tenant_id,
                CompetencyFrameworkVersion.framework_id == fw.id,
                CompetencyFrameworkVersion.version_number == target_ver,
            )
        )
        if fw_ver is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Competency framework version was not found.",
            )
        comps = cls._list_competencies(uow, tenant_id, fw_ver.id)
        return fw, fw_ver, comps

    @staticmethod
    def _list_competencies(
        uow: UnitOfWork, tenant_id: UUID, framework_version_id: UUID
    ) -> list[Competency]:
        return list(
            uow.session.scalars(
                select(Competency)
                .where(
                    Competency.tenant_id == tenant_id,
                    Competency.framework_version_id == framework_version_id,
                )
                .order_by(Competency.display_order.asc(), Competency.code.asc())
            ).all()
        )


class InterviewPackService:
    """Manages booked-interview context binding, draft pack generation, edits, and publication."""

    @classmethod
    def upsert_interview_context(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        application_id: UUID,
        stage: str,
        duration_minutes: int,
        participant_user_ids: list[UUID],
        schedule_revision: int = 1,
        seniority_level: str = "SENIOR",
        status: str = InterviewContextStatus.BOOKED.value,
        scheduled_start_at: datetime | None = None,
        scheduled_end_at: datetime | None = None,
        timezone: str = "UTC",
        booking_event_key: str | None = None,
    ) -> tuple[InterviewPackContext, UUID | None]:
        """Registers or updates a booked interview context and optionally admits an AI pack job.

        Repeated `booking_event_key` values are deduplicated idempotently so duplicate
        booking events never admit duplicate draft-generation operations.
        """
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.APPROVE_SCHEDULING)

        app_row = uow.session.scalar(
            select(Application).where(
                Application.tenant_id == tenant_id,
                Application.id == application_id,
            )
        )
        if app_row is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Application was not found.",
            )

        if not _has_job_staff_authority(
            uow,
            actor,
            tenant_id,
            app_row.job_id,
            {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Actor is not assigned to coordinate interviews for this job.",
            )

        if duration_minutes < 15 or duration_minutes > 240:
            raise ProblemException(
                status_code=422,
                title="Invalid Duration",
                code="VALIDATION_ERROR",
                detail="Interview duration must be between 15 and 240 minutes.",
            )

        ctx = uow.session.scalar(
            select(InterviewPackContext).where(
                InterviewPackContext.tenant_id == tenant_id,
                InterviewPackContext.id == interview_id,
            )
        )
        dedup_hit = False
        if ctx is None:
            processed_keys = [booking_event_key] if booking_event_key else []
            ctx = InterviewPackContext(
                id=interview_id,
                tenant_id=tenant_id,
                application_id=application_id,
                job_id=app_row.job_id,
                candidate_id=app_row.candidate_id,
                stage=stage.strip().upper(),
                seniority_level=seniority_level.strip().upper(),
                duration_minutes=duration_minutes,
                schedule_revision=schedule_revision,
                status=status,
                scheduled_start_at=scheduled_start_at,
                scheduled_end_at=scheduled_end_at,
                timezone=timezone,
                participant_user_ids=[str(u) for u in participant_user_ids],
                processed_booking_keys=processed_keys,
            )
            uow.session.add(ctx)
        else:
            existing_keys = list(ctx.processed_booking_keys or [])
            if booking_event_key and booking_event_key in existing_keys:
                dedup_hit = True
            else:
                if booking_event_key:
                    existing_keys.append(booking_event_key)
                ctx.stage = stage.strip().upper()
                ctx.seniority_level = seniority_level.strip().upper()
                ctx.duration_minutes = duration_minutes
                ctx.schedule_revision = schedule_revision
                ctx.status = status
                ctx.scheduled_start_at = scheduled_start_at
                ctx.scheduled_end_at = scheduled_end_at
                ctx.timezone = timezone
                ctx.participant_user_ids = [str(u) for u in participant_user_ids]
                ctx.processed_booking_keys = existing_keys

        uow.session.flush()

        admitted_op_id: UUID | None = None
        if (
            booking_event_key
            and not dedup_hit
            and status
            in (
                InterviewContextStatus.BOOKED.value,
                InterviewContextStatus.RESCHEDULED.value,
            )
        ):
            ai_enabled, remaining_budget = _check_tenant_ai_pack_policy(uow, tenant_id)
            if ai_enabled and remaining_budget > 0:
                op = uow.admit_operation(
                    operation_type="interview_packs.generate_draft",
                    payload={
                        "interview_id": str(interview_id),
                        "reason": f"BOOKING_EVENT:{booking_event_key}",
                        "actor_id": str(user_id),
                    },
                    resource_id=interview_id,
                )
                admitted_op_id = op.id

        uow.record_audit(
            resource_type="interview_pack_context",
            action="INTERVIEW_CONTEXT_SYNCED",
            resource_id=interview_id,
            actor_id=user_id,
            actor_capability=Capability.APPROVE_SCHEDULING.value,
            details={
                "application_id": str(application_id),
                "schedule_revision": ctx.schedule_revision,
                "duration_minutes": ctx.duration_minutes,
                "deduplicated_booking_event": dedup_hit,
                "admitted_operation_id": str(admitted_op_id) if admitted_op_id else None,
            },
        )
        return ctx, admitted_op_id

    @classmethod
    def _resolve_interview(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        interview_id: UUID,
        interview_port: InterviewContextPort | None = None,
    ) -> InterviewSnapshot:
        port = interview_port or DefaultInterviewContextAdapter()
        snap = port.get_interview(uow.session, tenant_id, interview_id)
        if snap is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="The requested interview was not found.",
            )
        return snap

    @classmethod
    def _collect_current_inputs(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        interview: InterviewSnapshot,
        framework_version_id: UUID | None = None,
    ) -> tuple[
        Job,
        Application,
        RequirementVersion,
        list[Criterion],
        UUID | None,
        ScreeningRun | None,
        CandidateSummaryRevision | None,
        CompetencyFrameworkVersion,
        list[Competency],
        list[SupportedFactInput],
        str,
    ]:
        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == interview.job_id)
        )
        app_row = uow.session.scalar(
            select(Application).where(
                Application.tenant_id == tenant_id,
                Application.id == interview.application_id,
            )
        )
        if job is None or app_row is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview job or application could not be resolved.",
            )
        if app_row.stage == ApplicationStage.WITHDRAWN.value:
            raise ProblemException(
                status_code=409,
                title="Application Withdrawn",
                code="INVALID_TRANSITION",
                detail="Cannot generate or publish an interview pack for a withdrawn application.",
            )
        if job.current_published_version_id is None:
            raise ProblemException(
                status_code=409,
                title="Unpublished Job Requirements",
                code="INVALID_TRANSITION",
                detail="Job must have a published requirement version before generating interview packs.",
            )

        req_ver = uow.session.scalar(
            select(RequirementVersion).where(
                RequirementVersion.tenant_id == tenant_id,
                RequirementVersion.id == job.current_published_version_id,
            )
        )
        if req_ver is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Published requirement version was not found.",
            )
        criteria = list(
            uow.session.scalars(
                select(Criterion)
                .where(
                    Criterion.tenant_id == tenant_id,
                    Criterion.requirement_version_id == req_ver.id,
                )
                .order_by(Criterion.order_index.asc())
            ).all()
        )

        # Active bound resume version (if bound)
        binding = uow.session.scalar(
            select(ApplicationDocument).where(
                ApplicationDocument.tenant_id == tenant_id,
                ApplicationDocument.application_id == app_row.id,
                ApplicationDocument.status == ApplicationDocumentStatus.ACTIVE.value,
            )
        )
        doc_version_id = binding.document_version_id if binding is not None else None

        # Latest screening run & accepted summary
        latest_screening = uow.session.scalar(
            select(ScreeningRun)
            .where(
                ScreeningRun.tenant_id == tenant_id,
                ScreeningRun.application_id == app_row.id,
            )
            .order_by(ScreeningRun.created_at.desc())
            .limit(1)
        )
        latest_summary = uow.session.scalar(
            select(CandidateSummaryRevision)
            .where(
                CandidateSummaryRevision.tenant_id == tenant_id,
                CandidateSummaryRevision.application_id == app_row.id,
                CandidateSummaryRevision.quality_status == "ACCEPTED",
            )
            .order_by(CandidateSummaryRevision.revision_number.desc())
            .limit(1)
        )

        # Resolve published competency framework version
        if framework_version_id is not None:
            fw_ver = uow.session.scalar(
                select(CompetencyFrameworkVersion).where(
                    CompetencyFrameworkVersion.tenant_id == tenant_id,
                    CompetencyFrameworkVersion.id == framework_version_id,
                )
            )
        else:
            # Find active published framework matching job+stage or org+stage
            fw = uow.session.scalar(
                select(CompetencyFramework)
                .where(
                    CompetencyFramework.tenant_id == tenant_id,
                    CompetencyFramework.status == FrameworkStatus.ACTIVE.value,
                    CompetencyFramework.latest_published_version_id.is_not(None),
                    sa.or_(
                        CompetencyFramework.job_id == job.id,
                        CompetencyFramework.job_id.is_(None),
                    ),
                )
                .order_by(
                    sa.case((CompetencyFramework.job_id == job.id, 0), else_=1),
                    CompetencyFramework.updated_at.desc(),
                )
                .limit(1)
            )
            fw_ver = (
                uow.session.scalar(
                    select(CompetencyFrameworkVersion).where(
                        CompetencyFrameworkVersion.tenant_id == tenant_id,
                        CompetencyFrameworkVersion.id == fw.latest_published_version_id,
                    )
                )
                if fw and fw.latest_published_version_id
                else None
            )

        if fw_ver is None or fw_ver.status != FrameworkVersionStatus.PUBLISHED.value:
            raise ProblemException(
                status_code=409,
                title="Published Competency Framework Required",
                code="INVALID_TRANSITION",
                detail="A published competency framework version is required to generate an interview pack.",
            )

        comps = CompetencyFrameworkService._list_competencies(uow, tenant_id, fw_ver.id)
        if not comps:
            raise ProblemException(
                status_code=422,
                title="Empty Competency Framework",
                code="VALIDATION_ERROR",
                detail="Selected competency framework version has no competencies.",
            )

        # Collect latest non-rejected candidate facts whose citations belong to active `doc_version_id`
        supported_facts: list[SupportedFactInput] = []
        fact_ids_for_hash: list[str] = []
        all_fact_revs = list(
            uow.session.scalars(
                select(CandidateFactRevision)
                .where(
                    CandidateFactRevision.tenant_id == tenant_id,
                    CandidateFactRevision.candidate_id == app_row.candidate_id,
                )
                .order_by(
                    CandidateFactRevision.fact_key.asc(),
                    CandidateFactRevision.revision_number.desc(),
                )
            ).all()
        )
        latest_by_key: dict[UUID, CandidateFactRevision] = {}
        for rev in all_fact_revs:
            if rev.fact_key not in latest_by_key:
                latest_by_key[rev.fact_key] = rev

        for fact in sorted(latest_by_key.values(), key=lambda r: str(r.fact_key)):
            if fact.status == CandidateFactStatus.REJECTED.value:
                continue
            fact_ids_for_hash.append(f"{fact.id}:{fact.status}:{fact.value}")
            if doc_version_id is None:
                continue

            ev_ids = list(
                uow.session.scalars(
                    select(CandidateFactEvidence.evidence_fragment_id).where(
                        CandidateFactEvidence.tenant_id == tenant_id,
                        CandidateFactEvidence.fact_revision_id == fact.id,
                    )
                ).all()
            )
            valid_fids: list[UUID] = []
            valid_quotes: list[str] = []
            for fid in ev_ids:
                frag_seg = cls._load_bound_fragment(uow, tenant_id, doc_version_id, fid)
                if frag_seg is None:
                    continue
                frag, _seg = frag_seg
                human_corrected = fact.status == CandidateFactStatus.CORRECTED.value and bool(
                    fact.review_note and fact.review_note.strip()
                )
                if is_evidence_supported(fact.value, frag.quote) or human_corrected:
                    valid_fids.append(frag.id)
                    valid_quotes.append(frag.quote)

            if valid_fids and fact.polarity == CandidateFactPolarity.AFFIRMED.value:
                supported_facts.append(
                    SupportedFactInput(
                        fact_revision_id=fact.id,
                        fact_type=fact.fact_type,
                        value=fact.value,
                        status=fact.status,
                        polarity=fact.polarity,
                        evidence_fragment_ids=tuple(valid_fids),
                        evidence_quotes=tuple(valid_quotes),
                    )
                )

        fact_hash = hashlib.sha256(
            json.dumps(fact_ids_for_hash, sort_keys=True).encode("utf-8")
        ).hexdigest()

        return (
            job,
            app_row,
            req_ver,
            criteria,
            doc_version_id,
            latest_screening,
            latest_summary,
            fw_ver,
            comps,
            supported_facts,
            fact_hash,
        )

    @staticmethod
    def _load_bound_fragment(
        uow: UnitOfWork,
        tenant_id: UUID,
        document_version_id: UUID | None,
        fragment_id: UUID,
    ) -> tuple[EvidenceFragment, SourceSegment] | None:
        if document_version_id is None:
            return None
        row = uow.session.execute(
            select(EvidenceFragment, SourceSegment)
            .join(
                SourceSegment,
                sa.and_(
                    SourceSegment.tenant_id == EvidenceFragment.tenant_id,
                    SourceSegment.id == EvidenceFragment.source_segment_id,
                ),
            )
            .where(
                EvidenceFragment.tenant_id == tenant_id,
                EvidenceFragment.id == fragment_id,
                SourceSegment.document_version_id == document_version_id,
            )
        ).first()
        if row is None:
            return None
        return row[0], row[1]

    @classmethod
    def _validate_and_normalize_claims(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        document_version_id: UUID | None,
        raw_claims: list[GeneratedClaimSpec],
        strict_citation_on_resume_supported: bool = True,
    ) -> list[GeneratedClaimSpec]:
        validated: list[GeneratedClaimSpec] = []
        for idx, cl in enumerate(raw_claims):
            clean_stmt = validate_job_relevant_observable_text(
                cl.statement, f"pack claim #{idx + 1}"
            )
            if cl.classification == PackClaimClassification.RESUME_SUPPORTED.value:
                if (
                    not cl.evidence_fragment_ids or document_version_id is None
                ) and strict_citation_on_resume_supported:
                    raise ProblemException(
                        status_code=422,
                        title="Missing Evidence Citation",
                        code="EVIDENCE_INVALID",
                        detail=(
                            f"Claim #{idx + 1} is classified RESUME_SUPPORTED but lacks "
                            "valid bound evidence fragment citations."
                        ),
                    )
                supported_any = False
                for fid in cl.evidence_fragment_ids:
                    frag_seg = cls._load_bound_fragment(uow, tenant_id, document_version_id, fid)
                    if frag_seg is None:
                        continue
                    frag, _ = frag_seg
                    if is_evidence_supported(clean_stmt, frag.quote):
                        supported_any = True
                    elif cl.fact_revision_id is not None:
                        fact = uow.session.scalar(
                            select(CandidateFactRevision).where(
                                CandidateFactRevision.tenant_id == tenant_id,
                                CandidateFactRevision.id == cl.fact_revision_id,
                            )
                        )
                        if (
                            fact is not None
                            and fact.status == CandidateFactStatus.CORRECTED.value
                            and fact.value == clean_stmt
                            and bool(fact.review_note and fact.review_note.strip())
                        ):
                            supported_any = True
                if not supported_any:
                    if strict_citation_on_resume_supported:
                        raise ProblemException(
                            status_code=422,
                            title="Unsupported Candidate Claim",
                            code="EVIDENCE_INVALID",
                            detail=(
                                f"Claim #{idx + 1} ('{clean_stmt[:80]}') is not supported by its "
                                "cited bound resume excerpts."
                            ),
                        )
                    validated.append(
                        GeneratedClaimSpec(
                            section=cl.section,
                            content_scope=cl.content_scope,
                            classification=PackClaimClassification.HUMAN_ASSERTION_UNVERIFIED.value,
                            statement=clean_stmt,
                            fact_revision_id=None,
                            evidence_fragment_ids=(),
                            source_note="Citation invalidated after manual edit; downgraded to unverified human assertion.",
                        )
                    )
                    continue
            validated.append(
                GeneratedClaimSpec(
                    section=cl.section,
                    content_scope=cl.content_scope,
                    classification=cl.classification,
                    statement=clean_stmt,
                    fact_revision_id=cl.fact_revision_id,
                    evidence_fragment_ids=cl.evidence_fragment_ids,
                    source_note=cl.source_note,
                )
            )
        return validated

    @classmethod
    def _validate_and_normalize_questions(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        document_version_id: UUID | None,
        valid_competency_ids: set[UUID],
        duration_minutes: int,
        raw_questions: list[GeneratedQuestionSpec],
        strict_citation_on_resume_supported: bool = True,
    ) -> tuple[list[GeneratedQuestionSpec], dict[str, int]]:
        if not raw_questions:
            raise ProblemException(
                status_code=422,
                title="Empty Question List",
                code="VALIDATION_ERROR",
                detail="An interview pack must include at least one structured question.",
            )

        normalized: list[GeneratedQuestionSpec] = []
        for idx, q in enumerate(raw_questions):
            if q.competency_id not in valid_competency_ids:
                raise ProblemException(
                    status_code=422,
                    title="Invalid Competency Mapping",
                    code="VALIDATION_ERROR",
                    detail=f"Question #{idx + 1} references competency {q.competency_id} outside the pinned framework version.",
                )
            clean_prompt = validate_job_relevant_observable_text(
                q.prompt_text, f"question #{idx + 1} prompt"
            )
            clean_premise = (
                validate_job_relevant_observable_text(
                    q.premise_text, f"question #{idx + 1} premise"
                )
                if q.premise_text
                else None
            )
            clean_signals = tuple(
                validate_job_relevant_observable_text(sig, f"question #{idx + 1} expected signal")
                for sig in q.expected_signals
                if sig and sig.strip()
            )
            if not clean_signals:
                raise ProblemException(
                    status_code=422,
                    title="Missing Expected Signals",
                    code="VALIDATION_ERROR",
                    detail=f"Question #{idx + 1} must provide at least one observable guidance signal.",
                )

            # Enforce premise provenance rules
            premise_cls = q.premise_classification
            fids = q.evidence_fragment_ids
            fact_id = q.fact_revision_id

            if q.content_scope == PackContentScope.ROLE_GENERIC.value and fids:
                raise ProblemException(
                    status_code=422,
                    title="Invalid Role-Generic Citation",
                    code="EVIDENCE_INVALID",
                    detail=f"Role-generic question #{idx + 1} must not attach fake resume citations.",
                )

            if (
                q.category == QuestionCategory.SCENARIO.value
                or premise_cls == PremiseClassification.HYPOTHETICAL.value
            ):
                validate_hypothetical_question_framing(clean_prompt, clean_premise)

            if q.category == QuestionCategory.PROJECT_SPECIFIC.value and premise_cls not in {
                PremiseClassification.RESUME_SUPPORTED.value,
                PremiseClassification.HUMAN_ASSERTION_UNVERIFIED.value,
            }:
                raise ProblemException(
                    status_code=422,
                    title="Project Question Requires Premise Provenance",
                    code="EVIDENCE_INVALID",
                    detail=(
                        f"Project-specific question #{idx + 1} must cite RESUME_SUPPORTED evidence "
                        "or be explicitly marked HUMAN_ASSERTION_UNVERIFIED."
                    ),
                )

            if premise_cls == PremiseClassification.RESUME_SUPPORTED.value:
                anchor_text = clean_premise or clean_prompt
                if not fids or document_version_id is None:
                    if strict_citation_on_resume_supported:
                        raise ProblemException(
                            status_code=422,
                            title="Missing Premise Citation",
                            code="EVIDENCE_INVALID",
                            detail=(
                                f"Question #{idx + 1} premise is labeled RESUME_SUPPORTED "
                                "without valid bound resume citations."
                            ),
                        )
                    premise_cls = PremiseClassification.HUMAN_ASSERTION_UNVERIFIED.value
                    fids = ()
                    fact_id = None
                else:
                    supported = False
                    for fid in fids:
                        frag_seg = cls._load_bound_fragment(
                            uow, tenant_id, document_version_id, fid
                        )
                        if frag_seg is None:
                            continue
                        frag, _ = frag_seg
                        if is_evidence_supported(anchor_text, frag.quote):
                            supported = True
                        elif fact_id is not None:
                            fact = uow.session.scalar(
                                select(CandidateFactRevision).where(
                                    CandidateFactRevision.tenant_id == tenant_id,
                                    CandidateFactRevision.id == fact_id,
                                )
                            )
                            if (
                                fact is not None
                                and fact.status == CandidateFactStatus.CORRECTED.value
                                and fact.value == anchor_text
                                and bool(fact.review_note and fact.review_note.strip())
                            ):
                                supported = True
                    if not supported:
                        if strict_citation_on_resume_supported:
                            raise ProblemException(
                                status_code=422,
                                title="Unsupported Question Premise",
                                code="EVIDENCE_INVALID",
                                detail=(
                                    f"Question #{idx + 1} premise ('{anchor_text[:80]}') is not "
                                    "supported by the cited bound resume excerpt."
                                ),
                            )
                        premise_cls = PremiseClassification.HUMAN_ASSERTION_UNVERIFIED.value
                        fids = ()
                        fact_id = None

            normalized.append(
                GeneratedQuestionSpec(
                    category=q.category,
                    content_scope=q.content_scope,
                    premise_classification=premise_cls,
                    competency_id=q.competency_id,
                    allocated_minutes=q.allocated_minutes,
                    prompt_text=clean_prompt,
                    premise_text=clean_premise,
                    expected_signals=clean_signals,
                    fact_revision_id=fact_id,
                    evidence_fragment_ids=fids,
                )
            )

        time_alloc = compute_time_allocation(
            duration_minutes=duration_minutes,
            question_minutes_sum=sum(item.allocated_minutes for item in normalized),
        )
        return normalized, time_alloc

    @classmethod
    def generate_draft_pack(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        reason: str = "EXPLICIT_PACK_RUN",
        framework_version_id: UUID | None = None,
        ai_provider: StructuredPackAIProvider | None = None,
        interview_port: InterviewContextPort | None = None,
    ) -> PackDetailBundle:
        """Generates a new pinned DRAFT interview pack without overwriting any PUBLISHED pack."""
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.READ_APPLICATION)

        interview = cls._resolve_interview(uow, tenant_id, interview_id, interview_port)
        if not _has_job_staff_authority(
            uow,
            actor,
            tenant_id,
            interview.job_id,
            {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Actor is not assigned as recruiter or hiring manager for this job.",
            )

        ai_enabled, remaining_budget = _check_tenant_ai_pack_policy(uow, tenant_id)
        if not ai_enabled:
            raise ProblemException(
                status_code=403,
                title="AI Pack Generation Disabled",
                code="FORBIDDEN",
                detail="Organization policy currently disables automated interview pack generation.",
            )
        if remaining_budget <= 0:
            raise ProblemException(
                status_code=429,
                title="AI Token Budget Exceeded",
                code="LIMIT_EXCEEDED",
                detail="Monthly organization AI token budget has been reached.",
            )

        (
            job,
            app_row,
            req_ver,
            criteria,
            doc_version_id,
            latest_screening,
            latest_summary,
            fw_ver,
            comps,
            supported_facts,
            fact_hash,
        ) = cls._collect_current_inputs(uow, tenant_id, interview, framework_version_id)

        # Compute max question time budget upfront
        probe_alloc = compute_time_allocation(interview.duration_minutes, 1)
        max_q_budget = probe_alloc["max_questions_budget_minutes"]

        # Identify unknown / manual criteria for clarification prompts
        unknown_descriptions = [
            c.source_requirement_text
            for c in criteria
            if c.requires_human_review or c.criterion_type == "MANUAL_REQUIREMENT"
        ]
        if not unknown_descriptions and not supported_facts:
            unknown_descriptions = [c.source_requirement_text for c in criteria[:2]] or [
                "core role experience and project scope"
            ]

        provider = ai_provider or DeterministicEvidenceGroundedPackProvider()
        pack_input = PackGenerationInput(
            job_title=job.title,
            delimited_job_description=delimit_untrusted_text("job_description", job.description),
            stage=interview.stage,
            seniority_level=interview.seniority_level,
            duration_minutes=interview.duration_minutes,
            max_questions_budget_minutes=max_q_budget,
            criteria=tuple(
                CriterionInput(
                    criterion_id=c.id,
                    criterion_type=c.criterion_type,
                    is_required=c.is_required,
                    source_requirement_text=c.source_requirement_text,
                )
                for c in criteria
            ),
            competencies=tuple(
                CompetencyInput(
                    competency_id=comp.id,
                    code=comp.code,
                    name=comp.name,
                    definition=comp.definition,
                    observation_required=comp.observation_required,
                    rubric_anchors=dict(comp.rubric_anchors),
                )
                for comp in comps
            ),
            supported_facts=tuple(supported_facts),
            unknown_criterion_descriptions=tuple(unknown_descriptions),
        )

        try:
            generated = provider.generate_pack(pack_input)
        except ProblemException:
            raise
        except TimeoutError as exc:
            raise ProblemException(
                status_code=503,
                title="AI Provider Timeout",
                code="AI_UNAVAILABLE",
                detail="Structured interview pack model timed out before completing generation.",
            ) from exc
        except Exception as exc:
            raise ProblemException(
                status_code=503,
                title="AI Provider Unavailable",
                code="AI_UNAVAILABLE",
                detail=f"Structured interview pack generation failed safely: {type(exc).__name__}.",
            ) from exc

        comp_ids = {c.id for c in comps}
        validated_claims = cls._validate_and_normalize_claims(
            uow=uow,
            tenant_id=tenant_id,
            document_version_id=doc_version_id,
            raw_claims=list(generated.claims),
            strict_citation_on_resume_supported=True,
        )
        validated_questions, time_alloc = cls._validate_and_normalize_questions(
            uow=uow,
            tenant_id=tenant_id,
            document_version_id=doc_version_id,
            valid_competency_ids=comp_ids,
            duration_minutes=interview.duration_minutes,
            raw_questions=list(generated.questions),
            strict_citation_on_resume_supported=True,
        )

        clean_overview = validate_job_relevant_observable_text(
            generated.candidate_overview, "candidate overview"
        )
        clean_ev_summary = validate_job_relevant_observable_text(
            latest_summary.summary_text
            if latest_summary is not None and latest_summary.summary_text
            else generated.candidate_evidence_summary,
            "candidate evidence summary",
        )
        clean_instructions = validate_job_relevant_observable_text(
            generated.interviewer_instructions, "interviewer instructions"
        )
        clean_notes_guidance = validate_job_relevant_observable_text(
            generated.notes_guidance, "notes guidance"
        )

        next_rev = (
            uow.session.scalar(
                select(PackRevision.revision_number)
                .where(
                    PackRevision.tenant_id == tenant_id,
                    PackRevision.interview_id == interview.id,
                )
                .order_by(PackRevision.revision_number.desc())
                .limit(1)
            )
            or 0
        ) + 1

        role_reqs_json = [
            {
                "criterion_id": str(c.id),
                "criterion_type": c.criterion_type,
                "is_required": c.is_required,
                "requirement_text": c.source_requirement_text,
            }
            for c in criteria
        ]
        responsibilities_json = [
            {
                "statement": cl.statement,
                "classification": cl.classification,
                "evidence_fragment_ids": [str(fid) for fid in cl.evidence_fragment_ids],
            }
            for cl in validated_claims
        ]
        comp_by_id = {c.id: c for c in comps}
        comp_mapping_json = [
            {
                "question_order": idx,
                "category": q.category,
                "competency_id": str(q.competency_id),
                "competency_code": comp_by_id[q.competency_id].code,
                "competency_name": comp_by_id[q.competency_id].name,
                "allocated_minutes": q.allocated_minutes,
            }
            for idx, q in enumerate(validated_questions)
        ]
        rubric_json = [
            {
                "competency_id": str(c.id),
                "code": c.code,
                "name": c.name,
                "definition": c.definition,
                "observation_required": c.observation_required,
                "anchors": dict(c.rubric_anchors),
            }
            for c in comps
        ]
        feedback_structure_json = {
            "framework_version_id": str(fw_ver.id),
            "rating_scale": ["1", "2", "3", "4", "NOT_ASSESSED"],
            "missing_is_not_zero": True,
            "blind_until_submitted": True,
            "competency_fields": [
                {
                    "competency_id": str(c.id),
                    "code": c.code,
                    "name": c.name,
                    "observation_required": c.observation_required,
                }
                for c in comps
            ],
            "overall_observations_required": True,
            "allows_human_recommendation": True,
            "auto_pipeline_transition": False,
        }

        pack_rev = PackRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            interview_id=interview.id,
            application_id=app_row.id,
            job_id=job.id,
            candidate_id=app_row.candidate_id,
            revision_number=next_rev,
            status=PackRevisionStatus.DRAFT.value,
            requirement_version_id=req_ver.id,
            document_version_id=doc_version_id,
            screening_run_id=latest_screening.id if latest_screening else None,
            summary_revision_id=latest_summary.id if latest_summary else None,
            framework_version_id=fw_ver.id,
            stage=interview.stage,
            seniority_level=interview.seniority_level,
            duration_minutes=interview.duration_minutes,
            schedule_revision=interview.schedule_revision,
            fact_snapshot_hash=fact_hash,
            generation_reason=reason.strip() or "EXPLICIT_PACK_RUN",
            based_on_revision_id=None,
            model_or_generator_id=generated.generator_id,
            candidate_overview=clean_overview,
            role_requirements=role_reqs_json,
            candidate_evidence_summary=clean_ev_summary,
            relevant_responsibilities=responsibilities_json,
            competency_mapping=comp_mapping_json,
            interviewer_instructions=clean_instructions,
            time_allocation=time_alloc,
            evaluation_rubric=rubric_json,
            notes_guidance=clean_notes_guidance,
            feedback_form_structure=feedback_structure_json,
            quality_warnings=list(generated.quality_warnings),
            version=1,
            author_id=user_id,
        )
        uow.session.add(pack_rev)
        uow.session.flush()

        claim_rows = cls._persist_claims(uow, tenant_id, pack_rev.id, validated_claims)
        question_rows = cls._persist_questions(uow, tenant_id, pack_rev.id, validated_questions)

        _record_ai_pack_usage_if_supported(
            uow=uow,
            tenant_id=tenant_id,
            pack_revision_id=pack_rev.id,
            generator_id=generated.generator_id,
            input_tokens=generated.input_tokens,
            output_tokens=generated.output_tokens,
        )

        uow.record_audit(
            resource_type="pack_revision",
            action="PACK_DRAFT_GENERATED",
            resource_id=pack_rev.id,
            actor_id=user_id,
            actor_capability=Capability.READ_APPLICATION.value,
            details={
                "interview_id": str(interview.id),
                "revision_number": pack_rev.revision_number,
                "framework_version_id": str(fw_ver.id),
                "requirement_version_id": str(req_ver.id),
                "document_version_id": str(doc_version_id) if doc_version_id else None,
                "duration_minutes": interview.duration_minutes,
                "schedule_revision": interview.schedule_revision,
                "question_count": len(question_rows),
            },
        )
        return PackDetailBundle(
            revision=pack_rev,
            claims=claim_rows,
            questions=question_rows,
            reviews=[],
            is_stale=False,
            stale_reasons=[],
        )

    @staticmethod
    def _persist_claims(
        uow: UnitOfWork,
        tenant_id: UUID,
        pack_revision_id: UUID,
        claims: list[GeneratedClaimSpec],
    ) -> list[PackClaim]:
        rows: list[PackClaim] = []
        for idx, cl in enumerate(claims):
            row = PackClaim(
                id=uuid4(),
                tenant_id=tenant_id,
                pack_revision_id=pack_revision_id,
                claim_index=idx,
                section=cl.section,
                content_scope=cl.content_scope,
                classification=cl.classification,
                statement=cl.statement,
                fact_revision_id=cl.fact_revision_id,
                evidence_fragment_ids=[str(fid) for fid in cl.evidence_fragment_ids],
                source_note=cl.source_note,
            )
            uow.session.add(row)
            rows.append(row)
        uow.session.flush()
        return rows

    @staticmethod
    def _persist_questions(
        uow: UnitOfWork,
        tenant_id: UUID,
        pack_revision_id: UUID,
        questions: list[GeneratedQuestionSpec],
    ) -> list[PackQuestion]:
        rows: list[PackQuestion] = []
        for idx, q in enumerate(questions):
            row = PackQuestion(
                id=uuid4(),
                tenant_id=tenant_id,
                pack_revision_id=pack_revision_id,
                order_index=idx,
                category=q.category,
                content_scope=q.content_scope,
                premise_classification=q.premise_classification,
                competency_id=q.competency_id,
                allocated_minutes=q.allocated_minutes,
                prompt_text=q.prompt_text,
                premise_text=q.premise_text,
                expected_signals=list(q.expected_signals),
                fact_revision_id=q.fact_revision_id,
                evidence_fragment_ids=[str(fid) for fid in q.evidence_fragment_ids],
            )
            uow.session.add(row)
            rows.append(row)
        uow.session.flush()
        return rows

    @classmethod
    def update_pack_revision(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        pack_revision_id: UUID,
        expected_version: int,
        candidate_overview: str | None = None,
        interviewer_instructions: str | None = None,
        notes_guidance: str | None = None,
        claims: list[PackClaimEditSpec] | None = None,
        questions: list[PackQuestionEditSpec] | None = None,
        interview_port: InterviewContextPort | None = None,
    ) -> PackDetailBundle:
        """Updates a DRAFT pack revision, or forks a new DRAFT revision if target is PUBLISHED.

        Enforces citation integrity: manual factual additions without valid source citations
        become `HUMAN_ASSERTION_UNVERIFIED` and cannot be mislabeled `RESUME_SUPPORTED`.
        """
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.EDIT_JOB)

        target = uow.session.scalar(
            select(PackRevision).where(
                PackRevision.tenant_id == tenant_id,
                PackRevision.id == pack_revision_id,
            )
        )
        if target is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview pack revision was not found.",
            )
        if not _has_job_staff_authority(
            uow,
            actor,
            tenant_id,
            target.job_id,
            {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Actor is not authorized to edit interview packs for this job.",
            )
        if target.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Expected pack revision lock version {expected_version}, found {target.version}.",
            )

        comps = CompetencyFrameworkService._list_competencies(
            uow, tenant_id, target.framework_version_id
        )
        comp_ids = {c.id for c in comps}
        comp_by_id = {c.id: c for c in comps}

        existing_claims = cls._load_claims(uow, tenant_id, target.id)
        existing_questions = cls._load_questions(uow, tenant_id, target.id)

        # Build candidate claim specs
        if claims is not None:
            raw_claim_specs: list[GeneratedClaimSpec] = []
            strict_flags: list[bool] = []
            for cs in claims:
                fids = tuple(cs.evidence_fragment_ids or [])
                explicit_cls = cs.classification
                if explicit_cls is None:
                    inferred_cls = (
                        PackClaimClassification.RESUME_SUPPORTED.value
                        if fids
                        else PackClaimClassification.HUMAN_ASSERTION_UNVERIFIED.value
                    )
                    strict_flags.append(False)
                else:
                    inferred_cls = explicit_cls
                    strict_flags.append(
                        explicit_cls == PackClaimClassification.RESUME_SUPPORTED.value
                    )
                raw_claim_specs.append(
                    GeneratedClaimSpec(
                        section=cs.section,
                        content_scope=cs.content_scope,
                        classification=inferred_cls,
                        statement=cs.statement,
                        fact_revision_id=cs.fact_revision_id,
                        evidence_fragment_ids=fids,
                        source_note=cs.source_note,
                    )
                )
            validated_claims: list[GeneratedClaimSpec] = []
            for spec_item, is_strict in zip(raw_claim_specs, strict_flags, strict=True):
                validated_claims.extend(
                    cls._validate_and_normalize_claims(
                        uow=uow,
                        tenant_id=tenant_id,
                        document_version_id=target.document_version_id,
                        raw_claims=[spec_item],
                        strict_citation_on_resume_supported=is_strict,
                    )
                )
        else:
            validated_claims = [
                GeneratedClaimSpec(
                    section=c.section,
                    content_scope=c.content_scope,
                    classification=c.classification,
                    statement=c.statement,
                    fact_revision_id=c.fact_revision_id,
                    evidence_fragment_ids=tuple(UUID(x) for x in (c.evidence_fragment_ids or [])),
                    source_note=c.source_note,
                )
                for c in existing_claims
            ]

        if questions is not None:
            raw_q_specs: list[GeneratedQuestionSpec] = []
            q_strict_flags: list[bool] = []
            for qs in questions:
                qfids = tuple(qs.evidence_fragment_ids or [])
                if qs.premise_classification is None:
                    if qs.content_scope == PackContentScope.ROLE_GENERIC.value:
                        pcls = (
                            PremiseClassification.HYPOTHETICAL.value
                            if qs.category == QuestionCategory.SCENARIO.value
                            else PremiseClassification.ROLE_GENERIC.value
                        )
                    elif qfids:
                        pcls = PremiseClassification.RESUME_SUPPORTED.value
                    else:
                        pcls = PremiseClassification.HUMAN_ASSERTION_UNVERIFIED.value
                    q_strict_flags.append(False)
                else:
                    pcls = qs.premise_classification
                    q_strict_flags.append(
                        qs.premise_classification == PremiseClassification.RESUME_SUPPORTED.value
                    )
                raw_q_specs.append(
                    GeneratedQuestionSpec(
                        category=qs.category,
                        content_scope=qs.content_scope,
                        premise_classification=pcls,
                        competency_id=qs.competency_id,
                        allocated_minutes=qs.allocated_minutes,
                        prompt_text=qs.prompt_text,
                        premise_text=qs.premise_text,
                        expected_signals=tuple(qs.expected_signals or ()),
                        fact_revision_id=qs.fact_revision_id,
                        evidence_fragment_ids=qfids,
                    )
                )
            # Validate per-question strictness first, then total timing
            checked_questions: list[GeneratedQuestionSpec] = []
            for q_item, q_strict in zip(raw_q_specs, q_strict_flags, strict=True):
                q_norm, _ = cls._validate_and_normalize_questions(
                    uow=uow,
                    tenant_id=tenant_id,
                    document_version_id=target.document_version_id,
                    valid_competency_ids=comp_ids,
                    duration_minutes=max(target.duration_minutes, 240),
                    raw_questions=[q_item],
                    strict_citation_on_resume_supported=q_strict,
                )
                checked_questions.extend(q_norm)
            time_alloc = compute_time_allocation(
                duration_minutes=target.duration_minutes,
                question_minutes_sum=sum(q.allocated_minutes for q in checked_questions),
            )
            validated_questions = checked_questions
        else:
            validated_questions = [
                GeneratedQuestionSpec(
                    category=q.category,
                    content_scope=q.content_scope,
                    premise_classification=q.premise_classification,
                    competency_id=q.competency_id,
                    allocated_minutes=q.allocated_minutes,
                    prompt_text=q.prompt_text,
                    premise_text=q.premise_text,
                    expected_signals=tuple(q.expected_signals or ()),
                    fact_revision_id=q.fact_revision_id,
                    evidence_fragment_ids=tuple(UUID(x) for x in (q.evidence_fragment_ids or [])),
                )
                for q in existing_questions
            ]
            time_alloc = compute_time_allocation(
                duration_minutes=target.duration_minutes,
                question_minutes_sum=sum(q.allocated_minutes for q in validated_questions),
            )

        new_overview = (
            validate_job_relevant_observable_text(candidate_overview, "candidate overview")
            if candidate_overview is not None
            else target.candidate_overview
        )
        new_instructions = (
            validate_job_relevant_observable_text(
                interviewer_instructions, "interviewer instructions"
            )
            if interviewer_instructions is not None
            else target.interviewer_instructions
        )
        new_notes = (
            validate_job_relevant_observable_text(notes_guidance, "notes guidance")
            if notes_guidance is not None
            else target.notes_guidance
        )
        comp_mapping_json = [
            {
                "question_order": idx,
                "category": q.category,
                "competency_id": str(q.competency_id),
                "competency_code": comp_by_id[q.competency_id].code,
                "competency_name": comp_by_id[q.competency_id].name,
                "allocated_minutes": q.allocated_minutes,
            }
            for idx, q in enumerate(validated_questions)
        ]
        responsibilities_json = [
            {
                "statement": cl.statement,
                "classification": cl.classification,
                "evidence_fragment_ids": [str(fid) for fid in cl.evidence_fragment_ids],
            }
            for cl in validated_claims
        ]

        # CRITICAL INVARIANT: Editing a PUBLISHED/SUPERSEDED pack forks a new DRAFT revision!
        if target.status != PackRevisionStatus.DRAFT.value:
            next_rev = (
                uow.session.scalar(
                    select(PackRevision.revision_number)
                    .where(
                        PackRevision.tenant_id == tenant_id,
                        PackRevision.interview_id == target.interview_id,
                    )
                    .order_by(PackRevision.revision_number.desc())
                    .limit(1)
                )
                or target.revision_number
            ) + 1
            forked = PackRevision(
                id=uuid4(),
                tenant_id=tenant_id,
                interview_id=target.interview_id,
                application_id=target.application_id,
                job_id=target.job_id,
                candidate_id=target.candidate_id,
                revision_number=next_rev,
                status=PackRevisionStatus.DRAFT.value,
                requirement_version_id=target.requirement_version_id,
                document_version_id=target.document_version_id,
                screening_run_id=target.screening_run_id,
                summary_revision_id=target.summary_revision_id,
                framework_version_id=target.framework_version_id,
                stage=target.stage,
                seniority_level=target.seniority_level,
                duration_minutes=target.duration_minutes,
                schedule_revision=target.schedule_revision,
                fact_snapshot_hash=target.fact_snapshot_hash,
                generation_reason="MANUAL_EDIT_FORK_FROM_PUBLISHED",
                based_on_revision_id=target.id,
                model_or_generator_id=target.model_or_generator_id,
                candidate_overview=new_overview,
                role_requirements=list(target.role_requirements),
                candidate_evidence_summary=target.candidate_evidence_summary,
                relevant_responsibilities=responsibilities_json,
                competency_mapping=comp_mapping_json,
                interviewer_instructions=new_instructions,
                time_allocation=time_alloc,
                evaluation_rubric=list(target.evaluation_rubric),
                notes_guidance=new_notes,
                feedback_form_structure=dict(target.feedback_form_structure),
                quality_warnings=list(target.quality_warnings),
                version=1,
                author_id=user_id,
            )
            uow.session.add(forked)
            uow.session.flush()
            claim_rows = cls._persist_claims(uow, tenant_id, forked.id, validated_claims)
            question_rows = cls._persist_questions(uow, tenant_id, forked.id, validated_questions)
            uow.record_audit(
                resource_type="pack_revision",
                action="PACK_REVISION_FORKED_FROM_PUBLISHED",
                resource_id=forked.id,
                actor_id=user_id,
                actor_capability=Capability.EDIT_JOB.value,
                details={
                    "based_on_revision_id": str(target.id),
                    "new_revision_number": forked.revision_number,
                },
            )
            is_stale, stale_reasons = cls._compute_staleness(uow, tenant_id, forked, interview_port)
            return PackDetailBundle(
                revision=forked,
                claims=claim_rows,
                questions=question_rows,
                reviews=[],
                is_stale=is_stale,
                stale_reasons=stale_reasons,
            )

        # In-place update on DRAFT with optimistic lock increment
        target.candidate_overview = new_overview
        target.interviewer_instructions = new_instructions
        target.notes_guidance = new_notes
        target.relevant_responsibilities = responsibilities_json
        target.competency_mapping = comp_mapping_json
        target.time_allocation = time_alloc
        target.version += 1
        target.updated_at = uow.clock.now()

        uow.session.execute(
            sa.delete(PackClaim).where(
                PackClaim.tenant_id == tenant_id,
                PackClaim.pack_revision_id == target.id,
            )
        )
        uow.session.execute(
            sa.delete(PackQuestion).where(
                PackQuestion.tenant_id == tenant_id,
                PackQuestion.pack_revision_id == target.id,
            )
        )
        uow.session.flush()

        claim_rows = cls._persist_claims(uow, tenant_id, target.id, validated_claims)
        question_rows = cls._persist_questions(uow, tenant_id, target.id, validated_questions)
        reviews = cls._load_reviews(uow, tenant_id, target.id)

        uow.record_audit(
            resource_type="pack_revision",
            action="PACK_DRAFT_UPDATED",
            resource_id=target.id,
            actor_id=user_id,
            actor_capability=Capability.EDIT_JOB.value,
            details={"revision_number": target.revision_number, "version": target.version},
        )
        is_stale, stale_reasons = cls._compute_staleness(uow, tenant_id, target, interview_port)
        return PackDetailBundle(
            revision=target,
            claims=claim_rows,
            questions=question_rows,
            reviews=reviews,
            is_stale=is_stale,
            stale_reasons=stale_reasons,
        )

    @classmethod
    def publish_pack_revision(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        pack_revision_id: UUID,
        expected_version: int,
        review_notes: str | None = None,
        interview_port: InterviewContextPort | None = None,
    ) -> PackDetailBundle:
        """Validates citations, timing, rubric, and freshness before publishing a pack revision."""
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.PUBLISH_JOB_REQUIREMENTS)

        target = uow.session.scalar(
            select(PackRevision).where(
                PackRevision.tenant_id == tenant_id,
                PackRevision.id == pack_revision_id,
            )
        )
        if target is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview pack revision was not found.",
            )
        if not _has_job_staff_authority(
            uow,
            actor,
            tenant_id,
            target.job_id,
            {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Actor is not assigned to publish interview packs for this job.",
            )
        if target.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Expected pack revision lock version {expected_version}, found {target.version}.",
            )
        if target.status != PackRevisionStatus.DRAFT.value:
            raise ProblemException(
                status_code=409,
                title="Invalid Pack Transition",
                code="INVALID_TRANSITION",
                detail=f"Only a DRAFT pack revision can be published (current status: {target.status}).",
            )

        is_stale, stale_reasons = cls._compute_staleness(uow, tenant_id, target, interview_port)
        if is_stale:
            raise ProblemException(
                status_code=409,
                title="Pack Inputs Are Stale",
                code="VERSION_CONFLICT",
                detail=(
                    "Cannot publish draft pack because pinned upstream inputs changed: "
                    + "; ".join(stale_reasons)
                ),
            )

        claims = cls._load_claims(uow, tenant_id, target.id)
        questions = cls._load_questions(uow, tenant_id, target.id)
        comps = CompetencyFrameworkService._list_competencies(
            uow, tenant_id, target.framework_version_id
        )
        comp_ids = {c.id for c in comps}

        # Re-verify every claim and question citation & timing strictly before publication
        cls._validate_and_normalize_claims(
            uow=uow,
            tenant_id=tenant_id,
            document_version_id=target.document_version_id,
            raw_claims=[
                GeneratedClaimSpec(
                    section=c.section,
                    content_scope=c.content_scope,
                    classification=c.classification,
                    statement=c.statement,
                    fact_revision_id=c.fact_revision_id,
                    evidence_fragment_ids=tuple(UUID(x) for x in (c.evidence_fragment_ids or [])),
                    source_note=c.source_note,
                )
                for c in claims
            ],
            strict_citation_on_resume_supported=True,
        )
        cls._validate_and_normalize_questions(
            uow=uow,
            tenant_id=tenant_id,
            document_version_id=target.document_version_id,
            valid_competency_ids=comp_ids,
            duration_minutes=target.duration_minutes,
            raw_questions=[
                GeneratedQuestionSpec(
                    category=q.category,
                    content_scope=q.content_scope,
                    premise_classification=q.premise_classification,
                    competency_id=q.competency_id,
                    allocated_minutes=q.allocated_minutes,
                    prompt_text=q.prompt_text,
                    premise_text=q.premise_text,
                    expected_signals=tuple(q.expected_signals or ()),
                    fact_revision_id=q.fact_revision_id,
                    evidence_fragment_ids=tuple(UUID(x) for x in (q.evidence_fragment_ids or [])),
                )
                for q in questions
            ],
            strict_citation_on_resume_supported=True,
        )

        # Supersede any prior PUBLISHED pack on this interview
        prior_published = list(
            uow.session.scalars(
                select(PackRevision).where(
                    PackRevision.tenant_id == tenant_id,
                    PackRevision.interview_id == target.interview_id,
                    PackRevision.status == PackRevisionStatus.PUBLISHED.value,
                )
            ).all()
        )
        for old_pub in prior_published:
            old_pub.status = PackRevisionStatus.SUPERSEDED.value
            old_pub.version += 1

        now = uow.clock.now()
        target.status = PackRevisionStatus.PUBLISHED.value
        target.published_by = user_id
        target.published_at = now
        target.version += 1
        target.updated_at = now

        review = PackReview(
            id=uuid4(),
            tenant_id=tenant_id,
            pack_revision_id=target.id,
            reviewer_id=user_id,
            decision=PackReviewDecision.APPROVED_FOR_PUBLISH.value,
            notes=sanitize_text(review_notes) if review_notes else None,
            created_at=now,
        )
        uow.session.add(review)
        uow.session.flush()

        reviews = cls._load_reviews(uow, tenant_id, target.id)
        uow.record_audit(
            resource_type="pack_revision",
            action="PACK_REVISION_PUBLISHED",
            resource_id=target.id,
            actor_id=user_id,
            actor_capability=Capability.PUBLISH_JOB_REQUIREMENTS.value,
            details={
                "interview_id": str(target.interview_id),
                "revision_number": target.revision_number,
                "superseded_count": len(prior_published),
            },
        )
        return PackDetailBundle(
            revision=target,
            claims=claims,
            questions=questions,
            reviews=reviews,
            is_stale=False,
            stale_reasons=[],
        )

    @classmethod
    def list_packs_for_interview(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        interview_port: InterviewContextPort | None = None,
    ) -> list[PackDetailBundle]:
        """Lists interview packs. Interviewers see ONLY the assigned PUBLISHED pack."""
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.READ_APPLICATION)

        interview = cls._resolve_interview(uow, tenant_id, interview_id, interview_port)
        is_staff_reviewer = _has_job_staff_authority(
            uow,
            actor,
            tenant_id,
            interview.job_id,
            {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
        )

        if not is_staff_reviewer:
            # Must be an assigned participant interviewer on this specific interview
            if user_id not in interview.participant_user_ids:
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="NOT_FOUND",
                    detail="The requested interview was not found.",
                )
            revs = list(
                uow.session.scalars(
                    select(PackRevision)
                    .where(
                        PackRevision.tenant_id == tenant_id,
                        PackRevision.interview_id == interview_id,
                        PackRevision.status == PackRevisionStatus.PUBLISHED.value,
                    )
                    .order_by(PackRevision.revision_number.desc())
                ).all()
            )
        else:
            revs = list(
                uow.session.scalars(
                    select(PackRevision)
                    .where(
                        PackRevision.tenant_id == tenant_id,
                        PackRevision.interview_id == interview_id,
                    )
                    .order_by(PackRevision.revision_number.desc())
                ).all()
            )

        if not revs:
            return []

        rev_ids = [r.id for r in revs]
        claims_by_rev: dict[UUID, list[PackClaim]] = {rid: [] for rid in rev_ids}
        for cl in uow.session.scalars(
            select(PackClaim)
            .where(
                PackClaim.tenant_id == tenant_id,
                PackClaim.pack_revision_id.in_(rev_ids),
            )
            .order_by(PackClaim.pack_revision_id.asc(), PackClaim.claim_index.asc())
        ).all():
            claims_by_rev.setdefault(cl.pack_revision_id, []).append(cl)

        questions_by_rev: dict[UUID, list[PackQuestion]] = {rid: [] for rid in rev_ids}
        for q in uow.session.scalars(
            select(PackQuestion)
            .where(
                PackQuestion.tenant_id == tenant_id,
                PackQuestion.pack_revision_id.in_(rev_ids),
            )
            .order_by(PackQuestion.pack_revision_id.asc(), PackQuestion.order_index.asc())
        ).all():
            questions_by_rev.setdefault(q.pack_revision_id, []).append(q)

        reviews_by_rev: dict[UUID, list[PackReview]] = {rid: [] for rid in rev_ids}
        if is_staff_reviewer:
            for rv in uow.session.scalars(
                select(PackReview)
                .where(
                    PackReview.tenant_id == tenant_id,
                    PackReview.pack_revision_id.in_(rev_ids),
                )
                .order_by(PackReview.pack_revision_id.asc(), PackReview.created_at.asc())
            ).all():
                reviews_by_rev.setdefault(rv.pack_revision_id, []).append(rv)

        # Batch-load staleness dependencies once across all revisions for this interview
        first_rev = revs[0]
        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == first_rev.job_id)
        )
        binding = uow.session.scalar(
            select(ApplicationDocument).where(
                ApplicationDocument.tenant_id == tenant_id,
                ApplicationDocument.application_id == first_rev.application_id,
                ApplicationDocument.status == ApplicationDocumentStatus.ACTIVE.value,
            )
        )
        current_doc_ver = binding.document_version_id if binding is not None else None
        fw_ver_ids = list({r.framework_version_id for r in revs})
        fw_vers = list(
            uow.session.scalars(
                select(CompetencyFrameworkVersion).where(
                    CompetencyFrameworkVersion.tenant_id == tenant_id,
                    CompetencyFrameworkVersion.id.in_(fw_ver_ids),
                )
            ).all()
        )
        fw_ver_map = {fv.id: fv for fv in fw_vers}
        fw_ids = list({fv.framework_id for fv in fw_vers})
        fw_map: dict[UUID, CompetencyFramework] = {}
        if fw_ids:
            for fw in uow.session.scalars(
                select(CompetencyFramework).where(
                    CompetencyFramework.tenant_id == tenant_id,
                    CompetencyFramework.id.in_(fw_ids),
                )
            ).all():
                fw_map[fw.id] = fw

        bundles: list[PackDetailBundle] = []
        for rev in revs:
            reasons: list[str] = []
            if interview.schedule_revision != rev.schedule_revision:
                reasons.append(
                    f"interview schedule_revision changed ({rev.schedule_revision} -> {interview.schedule_revision})"
                )
            if interview.duration_minutes != rev.duration_minutes:
                reasons.append(
                    f"interview duration_minutes changed ({rev.duration_minutes} -> {interview.duration_minutes})"
                )
            if job is not None and job.current_published_version_id != rev.requirement_version_id:
                reasons.append("job published requirement_version_id changed")
            if current_doc_ver != rev.document_version_id:
                reasons.append("application bound document_version_id changed")
            fv_row = fw_ver_map.get(rev.framework_version_id)
            if fv_row is not None:
                fw_row = fw_map.get(fv_row.framework_id)
                if (
                    fw_row is not None
                    and fw_row.latest_published_version_id is not None
                    and fw_row.latest_published_version_id != rev.framework_version_id
                ):
                    reasons.append("competency framework published version advanced")

            bundles.append(
                PackDetailBundle(
                    revision=rev,
                    claims=claims_by_rev.get(rev.id, []),
                    questions=questions_by_rev.get(rev.id, []),
                    reviews=reviews_by_rev.get(rev.id, []) if is_staff_reviewer else [],
                    is_stale=bool(reasons),
                    stale_reasons=reasons,
                )
            )
        return bundles

    @classmethod
    def get_pack_revision(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        pack_revision_id: UUID,
        interview_port: InterviewContextPort | None = None,
    ) -> PackDetailBundle:
        tenant_id, user_id = _require_tenant_actor(actor)
        AuthorizationService.enforce_capability(actor, Capability.READ_APPLICATION)

        rev = uow.session.scalar(
            select(PackRevision).where(
                PackRevision.tenant_id == tenant_id,
                PackRevision.id == pack_revision_id,
            )
        )
        if rev is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview pack revision was not found.",
            )

        interview = cls._resolve_interview(uow, tenant_id, rev.interview_id, interview_port)
        is_staff_reviewer = _has_job_staff_authority(
            uow,
            actor,
            tenant_id,
            rev.job_id,
            {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value},
        )
        if not is_staff_reviewer and (
            user_id not in interview.participant_user_ids
            or rev.status != PackRevisionStatus.PUBLISHED.value
        ):
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview pack revision was not found.",
            )

        is_stale, reasons = cls._compute_staleness(uow, tenant_id, rev, interview_port)
        return PackDetailBundle(
            revision=rev,
            claims=cls._load_claims(uow, tenant_id, rev.id),
            questions=cls._load_questions(uow, tenant_id, rev.id),
            reviews=cls._load_reviews(uow, tenant_id, rev.id) if is_staff_reviewer else [],
            is_stale=is_stale,
            stale_reasons=reasons,
        )

    @classmethod
    def _compute_staleness(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        rev: PackRevision,
        interview_port: InterviewContextPort | None = None,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        port = interview_port or DefaultInterviewContextAdapter()
        interview = port.get_interview(uow.session, tenant_id, rev.interview_id)
        if interview is not None:
            if interview.schedule_revision != rev.schedule_revision:
                reasons.append(
                    f"interview schedule_revision changed ({rev.schedule_revision} -> {interview.schedule_revision})"
                )
            if interview.duration_minutes != rev.duration_minutes:
                reasons.append(
                    f"interview duration_minutes changed ({rev.duration_minutes} -> {interview.duration_minutes})"
                )

        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == rev.job_id)
        )
        if job is not None and job.current_published_version_id != rev.requirement_version_id:
            reasons.append("job published requirement_version_id changed")

        binding = uow.session.scalar(
            select(ApplicationDocument).where(
                ApplicationDocument.tenant_id == tenant_id,
                ApplicationDocument.application_id == rev.application_id,
                ApplicationDocument.status == ApplicationDocumentStatus.ACTIVE.value,
            )
        )
        current_doc_ver = binding.document_version_id if binding is not None else None
        if current_doc_ver != rev.document_version_id:
            reasons.append("application bound document_version_id changed")

        fw_ver = uow.session.scalar(
            select(CompetencyFrameworkVersion).where(
                CompetencyFrameworkVersion.tenant_id == tenant_id,
                CompetencyFrameworkVersion.id == rev.framework_version_id,
            )
        )
        if fw_ver is not None:
            fw = uow.session.scalar(
                select(CompetencyFramework).where(
                    CompetencyFramework.tenant_id == tenant_id,
                    CompetencyFramework.id == fw_ver.framework_id,
                )
            )
            if (
                fw is not None
                and fw.latest_published_version_id is not None
                and fw.latest_published_version_id != rev.framework_version_id
            ):
                reasons.append("competency framework published version advanced")

        return bool(reasons), reasons

    @staticmethod
    def _load_claims(uow: UnitOfWork, tenant_id: UUID, pack_revision_id: UUID) -> list[PackClaim]:
        return list(
            uow.session.scalars(
                select(PackClaim)
                .where(
                    PackClaim.tenant_id == tenant_id,
                    PackClaim.pack_revision_id == pack_revision_id,
                )
                .order_by(PackClaim.claim_index.asc())
            ).all()
        )

    @staticmethod
    def _load_questions(
        uow: UnitOfWork, tenant_id: UUID, pack_revision_id: UUID
    ) -> list[PackQuestion]:
        return list(
            uow.session.scalars(
                select(PackQuestion)
                .where(
                    PackQuestion.tenant_id == tenant_id,
                    PackQuestion.pack_revision_id == pack_revision_id,
                )
                .order_by(PackQuestion.order_index.asc())
            ).all()
        )

    @staticmethod
    def _load_reviews(uow: UnitOfWork, tenant_id: UUID, pack_revision_id: UUID) -> list[PackReview]:
        return list(
            uow.session.scalars(
                select(PackReview)
                .where(
                    PackReview.tenant_id == tenant_id,
                    PackReview.pack_revision_id == pack_revision_id,
                )
                .order_by(PackReview.created_at.asc())
            ).all()
        )
