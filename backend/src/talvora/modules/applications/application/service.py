"""Applications Application Service: Job-Specific Pipeline and Human-Controlled Stages.

Stage transitions are human commands only. They require an authenticated human
principal, a granted capability, an explicit job assignment, and the expected current
version; a model or generic background/worker principal can never invoke a hiring
transition, and no screening outcome field writes the stage.
"""

import base64
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import false, func, select
from sqlalchemy.exc import IntegrityError

from talvora.modules.applications.domain.models import (
    ALLOWED_STAGE_TRANSITIONS,
    INTERVIEW_ELIGIBLE_STAGES,
    REASON_REQUIRED_TARGET_STAGES,
    Application,
    ApplicationSource,
    ApplicationStage,
    StageEvent,
    is_reopen_transition,
    reason_required,
    sanitize_text,
)
from talvora.modules.candidates.application.service import CandidateService
from talvora.modules.candidates.domain.models import Candidate, CandidateSource
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.jobs.application.service import JobService
from talvora.modules.jobs.domain.models import Job, JobRole, JobStatus
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

_PIPELINE_AUTHORITY_ROLES: set[str] = {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value}


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ApplicationService:
    """Manages job-specific applications and the audited, human-controlled pipeline."""

    # ------------------------------------------------------------------
    # Shared guards
    # ------------------------------------------------------------------

    @classmethod
    def _verify_tenant_actor(cls, actor: ActorContext) -> UUID:
        if not actor.is_authenticated or actor.tenant_id is None or actor.user_id is None:
            raise ProblemException(
                status_code=401,
                title="Unauthorized",
                code="UNAUTHENTICATED",
                detail="Valid session authentication within an organization is required.",
            )
        return actor.tenant_id

    @classmethod
    def _require_capability(cls, actor: ActorContext, capability: Capability) -> None:
        if not actor.has_capability(capability.value):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail=f"Actor lacks capability '{capability.value}' required for this operation.",
            )

    @classmethod
    def _get_application_or_404(
        cls, uow: UnitOfWork, tenant_id: UUID, application_id: UUID, for_update: bool = False
    ) -> Application:
        stmt = select(Application).where(
            Application.tenant_id == tenant_id, Application.id == application_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        application = uow.session.scalar(stmt)
        if not application:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested application was not found.",
            )
        return application

    @classmethod
    def _enforce_read_scope(
        cls, uow: UnitOfWork, actor: ActorContext, application: Application
    ) -> None:
        """INTERVIEWERs may only read applications for jobs they are explicitly assigned to."""
        is_interviewer = actor.membership and actor.membership.role == Role.INTERVIEWER.value
        if is_interviewer and not JobService.is_actor_assigned_to_job(
            uow, actor, application.job_id
        ):
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested application was not found.",
            )

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @classmethod
    def create_application(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        candidate_id: UUID | None = None,
        new_candidate_display_name: str | None = None,
        new_candidate_email: str | None = None,
        new_candidate_phone: str | None = None,
        new_candidate_is_draft: bool = False,
        source: ApplicationSource = ApplicationSource.MANUAL_ENTRY,
    ) -> Application:
        """Create an application against a job, either for an existing or a new candidate."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.CREATE_APPLICATION)

        job = JobService.get_job(uow=uow, actor=actor, job_id=job_id)
        if job.status != JobStatus.OPEN.value:
            raise ProblemException(
                status_code=409,
                title="Job Not Accepting Intake",
                code="JOB_NOT_ACCEPTING_INTAKE",
                detail=(
                    f"Job is in status '{job.status}' and is not currently accepting new "
                    "applications. Only OPEN jobs accept new intake."
                ),
            )

        if candidate_id is not None and (new_candidate_display_name or new_candidate_email):
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail="Supply either an existing candidate_id or new candidate fields, not both.",
            )

        candidate: Candidate
        if candidate_id is not None:
            if source == ApplicationSource.UPLOAD_INTAKE:
                is_recruiter_or_admin = actor.membership and actor.membership.role in {
                    Role.RECRUITER.value,
                    Role.OWNER.value,
                    Role.ADMIN.value,
                }
                if not is_recruiter_or_admin:
                    raise ProblemException(
                        status_code=403,
                        title="Forbidden",
                        code="CAPABILITY_DENIED",
                        detail=(
                            "Only a recruiter may select an existing candidate when "
                            "resolving an upload-intake item."
                        ),
                    )
            candidate = CandidateService.get_candidate(
                uow=uow, actor=actor, candidate_id=candidate_id
            )
        elif new_candidate_display_name or new_candidate_email or new_candidate_is_draft:
            candidate_source = (
                CandidateSource.UPLOAD_INTAKE
                if source == ApplicationSource.UPLOAD_INTAKE
                else CandidateSource.MANUAL_ENTRY
            )
            candidate = CandidateService.create_candidate(
                uow=uow,
                actor=actor,
                display_name=new_candidate_display_name,
                email=new_candidate_email,
                phone=new_candidate_phone,
                source=candidate_source,
                is_draft=new_candidate_is_draft,
            )
        else:
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail="An application requires either candidate_id or new candidate fields.",
            )

        existing_active = uow.session.scalar(
            select(Application).where(
                Application.tenant_id == tenant_id,
                Application.job_id == job_id,
                Application.candidate_id == candidate.id,
                Application.stage.notin_(
                    [ApplicationStage.WITHDRAWN.value, ApplicationStage.REJECTED.value]
                ),
            )
        )
        if existing_active is not None:
            raise ProblemException(
                status_code=409,
                title="Duplicate Active Application",
                code="DUPLICATE_ACTIVE_APPLICATION",
                detail="This candidate already holds an active application for this job.",
            )

        prior_cycles = (
            uow.session.scalar(
                select(func.max(Application.intake_cycle)).where(
                    Application.tenant_id == tenant_id,
                    Application.job_id == job_id,
                    Application.candidate_id == candidate.id,
                )
            )
            or 0
        )

        now = _utc_now()
        application = Application(
            id=uuid4(),
            tenant_id=tenant_id,
            job_id=job_id,
            candidate_id=candidate.id,
            stage=ApplicationStage.NEW.value,
            intake_cycle=prior_cycles + 1,
            source=source.value,
            created_by=actor.user_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(application)

        try:
            uow.session.flush()
        except IntegrityError as exc:
            raise ProblemException(
                status_code=409,
                title="Duplicate Active Application",
                code="DUPLICATE_ACTIVE_APPLICATION",
                detail="This candidate already holds an active application for this job.",
            ) from exc

        actor_role = actor.membership.role if actor.membership else "UNKNOWN"
        stage_event = StageEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            application_id=application.id,
            from_stage=None,
            to_stage=ApplicationStage.NEW.value,
            reason=None,
            actor_id=actor.user_id,
            actor_role=actor_role,
            expected_version=1,
            created_at=now,
        )
        uow.session.add(stage_event)

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="APPLICATION_CREATED",
            resource_type="application",
            resource_id=application.id,
            payload={"job_id": str(job_id), "candidate_id": str(candidate.id)},
        )

        return application

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @classmethod
    def get_application(
        cls, uow: UnitOfWork, actor: ActorContext, application_id: UUID
    ) -> Application:
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.READ_APPLICATION)

        application = cls._get_application_or_404(uow, tenant_id, application_id)
        cls._enforce_read_scope(uow, actor, application)
        return application

    @classmethod
    def list_applications(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID | None = None,
        candidate_id: UUID | None = None,
        stage: ApplicationStage | None = None,
        interview_eligible: bool | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[Application], str | None, int]:
        """Job-scoped application listing with filters for a future pipeline UI."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.READ_APPLICATION)

        limit = max(1, min(limit, 100))
        stmt = select(Application).where(Application.tenant_id == tenant_id)

        if job_id is not None:
            stmt = stmt.where(Application.job_id == job_id)
        if candidate_id is not None:
            stmt = stmt.where(Application.candidate_id == candidate_id)
        if stage is not None:
            stmt = stmt.where(Application.stage == stage.value)

        assigned_job_ids = JobService.get_assigned_job_ids(uow, actor)
        if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
            ids = assigned_job_ids or []
            stmt = stmt.where(Application.job_id.in_(ids)) if ids else stmt.where(false())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = uow.session.scalar(count_stmt) or 0

        if cursor:
            try:
                decoded = base64.b64decode(cursor.encode()).decode()
                raw_val, raw_id = decoded.split("|", 1)
                cursor_id = UUID(raw_id)
                cursor_dt = datetime.fromisoformat(raw_val)
                stmt = stmt.where(
                    (Application.created_at < cursor_dt)
                    | ((Application.created_at == cursor_dt) & (Application.id < cursor_id))
                )
            except Exception:
                pass

        stmt = stmt.order_by(Application.created_at.desc(), Application.id.desc()).limit(limit + 1)
        results = list(uow.session.scalars(stmt).all())

        if interview_eligible is not None:
            results = [
                a
                for a in results
                if cls._compute_eligibility(uow, actor, a)[0] == interview_eligible
            ]

        next_cursor = None
        if len(results) > limit:
            results = results[:limit]
            last_item = results[-1]
            next_cursor = base64.b64encode(
                f"{last_item.created_at.isoformat()}|{last_item.id}".encode()
            ).decode()

        return results, next_cursor, total_count

    @classmethod
    def list_for_candidate(
        cls, uow: UnitOfWork, actor: ActorContext, candidate_id: UUID
    ) -> list[Application]:
        """Candidate application history across all jobs the actor may view."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.READ_APPLICATION)

        # Ensure the candidate exists in this tenant (resource concealment).
        CandidateService.get_candidate(uow=uow, actor=actor, candidate_id=candidate_id)

        stmt = select(Application).where(
            Application.tenant_id == tenant_id, Application.candidate_id == candidate_id
        )
        if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
            ids = JobService.get_assigned_job_ids(uow, actor) or []
            stmt = stmt.where(Application.job_id.in_(ids)) if ids else stmt.where(false())

        stmt = stmt.order_by(Application.created_at.desc())
        return list(uow.session.scalars(stmt).all())

    @classmethod
    def list_stage_history(
        cls, uow: UnitOfWork, actor: ActorContext, application_id: UUID
    ) -> list[StageEvent]:
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.READ_APPLICATION)

        application = cls._get_application_or_404(uow, tenant_id, application_id)
        cls._enforce_read_scope(uow, actor, application)

        stmt = (
            select(StageEvent)
            .where(StageEvent.tenant_id == tenant_id, StageEvent.application_id == application_id)
            .order_by(StageEvent.created_at.asc())
        )
        return list(uow.session.scalars(stmt).all())

    @classmethod
    def _compute_eligibility(
        cls, uow: UnitOfWork, actor: ActorContext, application: Application
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if application.stage not in INTERVIEW_ELIGIBLE_STAGES:
            reasons.append(f"Application stage '{application.stage}' is not interview-eligible.")

        job_map = JobService.get_jobs_by_ids(uow, actor, [application.job_id])
        job: Job | None = job_map.get(application.job_id)
        if job is None:
            reasons.append("Job could not be resolved.")
        elif job.status == JobStatus.CLOSED.value:
            reasons.append("Job is CLOSED and no longer accepts interview scheduling.")

        candidate_map = CandidateService.get_candidates_by_ids(
            uow, actor, [application.candidate_id]
        )
        candidate = candidate_map.get(application.candidate_id)
        if candidate is None:
            reasons.append("Candidate could not be resolved.")
        elif not candidate.is_contact_verified:
            reasons.append("Candidate's current contact has not been human-verified.")

        return (len(reasons) == 0, reasons)

    @classmethod
    def get_interview_eligibility(
        cls, uow: UnitOfWork, actor: ActorContext, application_id: UUID
    ) -> dict[str, object]:
        """Deterministic interview eligibility query for later scheduling modules."""
        application = cls.get_application(uow, actor, application_id)
        eligible, reasons = cls._compute_eligibility(uow, actor, application)
        return {"application_id": application.id, "eligible": eligible, "reasons": reasons}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @classmethod
    def update_application(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        application_id: UUID,
        notes: str | None = None,
        expected_version: int | None = None,
    ) -> Application:
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.TRANSITION_STAGE)

        application = cls._get_application_or_404(uow, tenant_id, application_id, for_update=True)
        JobService.get_job_with_authority(
            uow=uow, actor=actor, job_id=application.job_id, allowed_roles=_PIPELINE_AUTHORITY_ROLES
        )

        if expected_version is not None and application.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=(
                    f"Application was modified concurrently. Expected version "
                    f"{expected_version}, but current version is {application.version}."
                ),
            )

        if notes is not None:
            application.notes = sanitize_text(notes)
        application.version += 1
        application.updated_at = _utc_now()

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="APPLICATION_UPDATED",
            resource_type="application",
            resource_id=application.id,
            payload={"version": application.version},
        )

        return application

    @classmethod
    def transition_stage(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        application_id: UUID,
        target_stage: ApplicationStage,
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> Application:
        """Execute a human-authorized pipeline stage transition.

        Human actor, capability, job assignment, and expected version are all mandatory.
        A model or generic background/worker principal can never invoke this.
        """
        tenant_id = cls._verify_tenant_actor(actor)

        if not actor.is_human:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Automated, worker, or AI-model principals cannot invoke pipeline transitions.",
            )

        cls._require_capability(actor, Capability.TRANSITION_STAGE)

        application = cls._get_application_or_404(uow, tenant_id, application_id, for_update=True)

        job = JobService.get_job_with_authority(
            uow=uow, actor=actor, job_id=application.job_id, allowed_roles=_PIPELINE_AUTHORITY_ROLES
        )

        if expected_version is None:
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail="expected_version is mandatory for a stage transition.",
            )
        if application.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=(
                    f"Application was modified concurrently. Expected version "
                    f"{expected_version}, but current version is {application.version}."
                ),
            )

        current = application.stage
        target = target_stage.value

        if current == target:
            raise ProblemException(
                status_code=409,
                title="Invalid Transition",
                code="INVALID_TRANSITION",
                detail=f"Application is already in stage '{target}'.",
            )

        allowed = ALLOWED_STAGE_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise ProblemException(
                status_code=409,
                title="Invalid Transition",
                code="INVALID_TRANSITION",
                detail=f"Cannot transition application from stage '{current}' to '{target}'.",
            )

        if (
            target == ApplicationStage.INTERVIEW_APPROVED.value
            and job.status != JobStatus.OPEN.value
        ):
            raise ProblemException(
                status_code=409,
                title="Job Not Open For Scheduling",
                code="JOB_NOT_ACCEPTING_INTAKE",
                detail=(
                    f"Job is in status '{job.status}'. New interview approvals require "
                    "the job to be OPEN."
                ),
            )

        clean_reason = sanitize_text(reason) if reason else None
        if reason_required(current, target) and not clean_reason:
            field = (
                "REQUIRED_REASON" if target in REASON_REQUIRED_TARGET_STAGES else "REOPEN_REASON"
            )
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail=f"A non-empty reason is required for this transition ({field}).",
            )

        now = _utc_now()
        application.stage = target
        application.version += 1
        application.updated_at = now

        actor_role = actor.membership.role if actor.membership else "UNKNOWN"
        stage_event = StageEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            application_id=application.id,
            from_stage=current,
            to_stage=target,
            reason=clean_reason,
            actor_id=actor.user_id,
            actor_role=actor_role,
            expected_version=expected_version,
            created_at=now,
        )
        uow.session.add(stage_event)

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="APPLICATION_STAGE_TRANSITIONED",
            resource_type="application",
            resource_id=application.id,
            payload={"from_stage": current, "to_stage": target, "reason": clean_reason},
        )

        if target in (ApplicationStage.WITHDRAWN.value, ApplicationStage.REJECTED.value):
            # Prepares future scheduling/interview consumers: pending scheduling approvals
            # must be invalidated and any existing interview flagged for coordinator
            # review rather than silently canceled. No scheduling/interview module exists
            # yet in this phase, so this only emits the versioned event those future
            # consumers will subscribe to.
            uow.record_outbox_event(
                topic="applications.eligibility_invalidated", payload_ref=application.id
            )

        if is_reopen_transition(current, target):
            uow.record_outbox_event(topic="applications.stage_reopened", payload_ref=application.id)

        return application
