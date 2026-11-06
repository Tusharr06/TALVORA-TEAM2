"""Jobs Application Service: Vacancies, Immutable Requirements, and Object-Level Assignments."""

import base64
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from talvora.modules.identity.domain.roles import Role
from talvora.modules.jobs.domain.models import (
    Criterion,
    CriterionDraft,
    CriterionType,
    Job,
    JobAssignment,
    JobRole,
    JobStatus,
    RequirementVersion,
    sanitize_text,
    validate_no_protected_traits,
)
from talvora.modules.organizations.domain.models import Membership, Team
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JobService:
    """Manages job vacancies, draft and immutable requirement versions, and assignments."""

    @classmethod
    def _verify_tenant_actor(cls, actor: ActorContext) -> UUID:
        """Verify actor is authenticated and bound to an active tenant."""
        if not actor.is_authenticated or actor.tenant_id is None or actor.user_id is None:
            raise ProblemException(
                status_code=401,
                title="Unauthorized",
                code="UNAUTHENTICATED",
                detail="Valid session authentication within an organization is required.",
            )
        return actor.tenant_id

    @classmethod
    def _get_job_assignments(
        cls, uow: UnitOfWork, tenant_id: UUID, job_id: UUID
    ) -> list[JobAssignment]:
        stmt = select(JobAssignment).where(
            JobAssignment.tenant_id == tenant_id,
            JobAssignment.job_id == job_id,
        )
        return list(uow.session.scalars(stmt).all())

    @classmethod
    def _has_job_authority(
        cls,
        actor: ActorContext,
        assignments: list[JobAssignment],
        allowed_roles: set[str],
    ) -> bool:
        """Check if actor has object-level authority on the job or organization-level owner authority."""
        # Organization OWNER always retains authority
        if actor.membership and actor.membership.role == Role.OWNER.value:
            return True

        # Check explicit job assignments
        for assignment in assignments:
            if assignment.user_id == actor.user_id and assignment.role in allowed_roles:
                return True

        return False

    @classmethod
    def create_job(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        title: str,
        description: str,
        department: str | None = None,
        location: str | None = None,
        employment_type: str = "FULL_TIME",
        team_id: UUID | None = None,
        hiring_manager_id: UUID | None = None,
        initial_criteria: list[CriterionDraft] | None = None,
    ) -> Job:
        """Create a new vacancy in DRAFT status and grant creator appropriate assignment."""
        tenant_id = cls._verify_tenant_actor(actor)

        # Enforce capability
        if not actor.membership or actor.membership.role not in {
            Role.OWNER.value,
            Role.ADMIN.value,
            Role.RECRUITER.value,
            Role.HIRING_MANAGER.value,
        }:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Actor lacks capability to create job vacancies in this organization.",
            )

        # Team validation (resource concealment if cross-tenant)
        if team_id is not None:
            team = uow.session.scalar(
                select(Team).where(Team.tenant_id == tenant_id, Team.id == team_id)
            )
            if not team:
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="RESOURCE_NOT_FOUND",
                    detail="The specified team was not found.",
                )

        # Sanitize text
        clean_title = sanitize_text(title)
        clean_desc = sanitize_text(description)
        if not clean_title:
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail="Job title cannot be empty or solely HTML tags.",
            )
        if not clean_desc:
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail="Job description cannot be empty or solely HTML tags.",
            )

        validate_no_protected_traits(clean_title, "job title")
        validate_no_protected_traits(clean_desc, "job description")

        # Validate initial draft criteria if present
        draft_list: list[dict[str, Any]] = []
        if initial_criteria:
            for idx, c in enumerate(initial_criteria):
                validate_no_protected_traits(
                    c.source_requirement_text, f"criterion {idx} source text"
                )
                validate_no_protected_traits(c.relevance_rationale, f"criterion {idx} rationale")
                if (
                    c.criterion_type == CriterionType.MANUAL_REQUIREMENT
                    and not c.requires_human_review
                ):
                    raise ProblemException(
                        status_code=422,
                        title="Invalid Criterion Configuration",
                        code="INVALID_CRITERION_CONFIG",
                        detail=f"Criterion at index {idx} is a MANUAL_REQUIREMENT and must have requires_human_review set to True.",
                    )
                c_dict = c.model_dump(mode="json")
                c_dict["order_index"] = idx
                draft_list.append(c_dict)

        # Validate hiring manager if designated
        if hiring_manager_id is not None:
            hm_membership = uow.session.scalar(
                select(Membership).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == hiring_manager_id,
                    Membership.status == "ACTIVE",
                )
            )
            if not hm_membership:
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="RESOURCE_NOT_FOUND",
                    detail="The designated hiring manager does not hold an active membership in this organization.",
                )

        now = _utc_now()
        job = Job(
            id=uuid4(),
            tenant_id=tenant_id,
            title=clean_title,
            description=clean_desc,
            department=department.strip() if department else None,
            location=location.strip() if location else None,
            employment_type=employment_type.strip().upper(),
            team_id=team_id,
            creator_id=actor.user_id,
            hiring_manager_id=hiring_manager_id,
            status=JobStatus.DRAFT.value,
            draft_requirements=draft_list if draft_list else None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(job)

        # Assignment Policy:
        # Creator receives appropriate job assignment based on their role
        actor_role = actor.membership.role
        creator_assignment_role: JobRole
        if actor_role == Role.RECRUITER.value:
            creator_assignment_role = JobRole.RECRUITER
        elif actor_role == Role.HIRING_MANAGER.value:
            creator_assignment_role = JobRole.HIRING_MANAGER
        else:
            creator_assignment_role = JobRole.HIRING_MANAGER

        creator_assignment = JobAssignment(
            id=uuid4(),
            tenant_id=tenant_id,
            job_id=job.id,
            user_id=actor.user_id,
            role=creator_assignment_role.value,
            assigned_by=actor.user_id,
            created_at=now,
        )
        uow.session.add(creator_assignment)

        # If a different user was designated as hiring manager, assign them too
        if hiring_manager_id is not None and hiring_manager_id != actor.user_id:
            hm_assignment = JobAssignment(
                id=uuid4(),
                tenant_id=tenant_id,
                job_id=job.id,
                user_id=hiring_manager_id,
                role=JobRole.HIRING_MANAGER.value,
                assigned_by=actor.user_id,
                created_at=now,
            )
            uow.session.add(hm_assignment)

        # Audit
        uow.record_audit_event(
            actor_id=actor.user_id,
            action="JOB_CREATED",
            resource_type="job",
            resource_id=job.id,
            payload={"title": job.title, "creator_assignment": creator_assignment_role.value},
        )

        return job

    @classmethod
    def get_job(cls, uow: UnitOfWork, actor: ActorContext, job_id: UUID) -> Job:
        """Fetch job details with multi-tenant resource concealment."""
        tenant_id = cls._verify_tenant_actor(actor)

        job = uow.session.scalar(select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id))
        if not job:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested job vacancy was not found.",
            )

        # If actor is an INTERVIEWER, they must have an explicit assignment on this job
        if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
            assignments = cls._get_job_assignments(uow, tenant_id, job_id)
            if not any(a.user_id == actor.user_id for a in assignments):
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="RESOURCE_NOT_FOUND",
                    detail="The requested job vacancy was not found.",
                )

        return job

    @classmethod
    def list_jobs(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        team_id: UUID | None = None,
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[Job], str | None, int]:
        """List vacancies with filtering, allowlisted sorting, and cursor pagination."""
        tenant_id = cls._verify_tenant_actor(actor)

        limit = max(1, min(limit, 100))
        allowlisted_sorts = {"created_at", "title", "status", "updated_at"}
        if sort_by not in allowlisted_sorts:
            sort_by = "created_at"
        is_desc = sort_order.lower() == "desc"

        stmt = select(Job).where(Job.tenant_id == tenant_id)

        if team_id is not None:
            stmt = stmt.where(Job.team_id == team_id)
        if status is not None:
            stmt = stmt.where(Job.status == status.value)

        # If interviewer, only show assigned jobs
        if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
            assigned_job_ids = select(JobAssignment.job_id).where(
                JobAssignment.tenant_id == tenant_id,
                JobAssignment.user_id == actor.user_id,
            )
            stmt = stmt.where(Job.id.in_(assigned_job_ids))

        # Total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = uow.session.scalar(count_stmt) or 0

        # Cursor parsing
        if cursor:
            try:
                decoded = base64.b64decode(cursor.encode()).decode()
                raw_val, raw_id = decoded.split("|", 1)
                cursor_id = UUID(raw_id)
                if sort_by in {"created_at", "updated_at"}:
                    cursor_dt = datetime.fromisoformat(raw_val)
                    col = getattr(Job, sort_by)
                    if is_desc:
                        stmt = stmt.where(
                            (col < cursor_dt) | ((col == cursor_dt) & (Job.id < cursor_id))
                        )
                    else:
                        stmt = stmt.where(
                            (col > cursor_dt) | ((col == cursor_dt) & (Job.id > cursor_id))
                        )
                else:
                    col = getattr(Job, sort_by)
                    if is_desc:
                        stmt = stmt.where(
                            (col < raw_val) | ((col == raw_val) & (Job.id < cursor_id))
                        )
                    else:
                        stmt = stmt.where(
                            (col > raw_val) | ((col == raw_val) & (Job.id > cursor_id))
                        )
            except Exception:
                pass  # Ignore invalid cursor and start from beginning

        # Ordering
        order_col = getattr(Job, sort_by)
        if is_desc:
            stmt = stmt.order_by(order_col.desc(), Job.id.desc())
        else:
            stmt = stmt.order_by(order_col.asc(), Job.id.asc())

        stmt = stmt.limit(limit + 1)
        results = list(uow.session.scalars(stmt).all())

        next_cursor = None
        if len(results) > limit:
            results = results[:limit]
            last_item = results[-1]
            last_val = getattr(last_item, sort_by)
            val_str = last_val.isoformat() if isinstance(last_val, datetime) else str(last_val)
            next_cursor = base64.b64encode(f"{val_str}|{last_item.id}".encode()).decode()

        return results, next_cursor, total_count

    @classmethod
    def update_job(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        title: str | None = None,
        description: str | None = None,
        department: str | None = None,
        location: str | None = None,
        employment_type: str | None = None,
        team_id: UUID | None = None,
        draft_requirements: list[CriterionDraft] | None = None,
        expected_version: int | None = None,
    ) -> Job:
        """Update mutable draft and safe job metadata with optimistic concurrency control."""
        tenant_id = cls._verify_tenant_actor(actor)

        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id).with_for_update()
        )
        if not job:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested job vacancy was not found.",
            )

        # Enforce authority: OWNER or assigned HIRING_MANAGER or assigned RECRUITER
        assignments = cls._get_job_assignments(uow, tenant_id, job_id)
        if not cls._has_job_authority(
            actor, assignments, {JobRole.HIRING_MANAGER.value, JobRole.RECRUITER.value}
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Actor is not assigned as recruiter or hiring manager on this job vacancy.",
            )

        # Optimistic concurrency check
        if expected_version is not None and job.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Job was modified concurrently. Expected version {expected_version}, but current version is {job.version}.",
            )

        # Team validation if updated
        if team_id is not None and team_id != job.team_id:
            team = uow.session.scalar(
                select(Team).where(Team.tenant_id == tenant_id, Team.id == team_id)
            )
            if not team:
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="RESOURCE_NOT_FOUND",
                    detail="The specified team was not found.",
                )
            job.team_id = team_id

        # Update metadata
        if title is not None:
            clean_title = sanitize_text(title)
            if not clean_title:
                raise ProblemException(
                    status_code=422,
                    title="Validation Error",
                    code="VALIDATION_ERROR",
                    detail="Job title cannot be empty or solely HTML tags.",
                )
            validate_no_protected_traits(clean_title, "job title")
            job.title = clean_title

        if description is not None:
            clean_desc = sanitize_text(description)
            if not clean_desc:
                raise ProblemException(
                    status_code=422,
                    title="Validation Error",
                    code="VALIDATION_ERROR",
                    detail="Job description cannot be empty or solely HTML tags.",
                )
            validate_no_protected_traits(clean_desc, "job description")
            job.description = clean_desc

        if department is not None:
            job.department = department.strip() if department else None
        if location is not None:
            job.location = location.strip() if location else None
        if employment_type is not None:
            job.employment_type = employment_type.strip().upper()

        if draft_requirements is not None:
            draft_list: list[dict[str, Any]] = []
            for idx, c in enumerate(draft_requirements):
                validate_no_protected_traits(
                    c.source_requirement_text, f"criterion {idx} source text"
                )
                validate_no_protected_traits(c.relevance_rationale, f"criterion {idx} rationale")
                if (
                    c.criterion_type == CriterionType.MANUAL_REQUIREMENT
                    and not c.requires_human_review
                ):
                    raise ProblemException(
                        status_code=422,
                        title="Invalid Criterion Configuration",
                        code="INVALID_CRITERION_CONFIG",
                        detail=f"Criterion at index {idx} is a MANUAL_REQUIREMENT and must have requires_human_review set to True.",
                    )
                c_dict = c.model_dump(mode="json")
                c_dict["order_index"] = idx
                draft_list.append(c_dict)
            job.draft_requirements = draft_list

        job.version += 1
        job.updated_at = _utc_now()

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="JOB_UPDATED",
            resource_type="job",
            resource_id=job.id,
            payload={"version": job.version, "title": job.title},
        )

        return job

    @classmethod
    def update_draft_requirements(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        criteria: list[CriterionDraft],
        expected_version: int | None = None,
    ) -> Job:
        """Update the mutable draft requirements for a job without publishing."""
        return cls.update_job(
            uow=uow,
            actor=actor,
            job_id=job_id,
            draft_requirements=criteria,
            expected_version=expected_version,
        )

    @classmethod
    def publish_requirements(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        change_summary: str | None = None,
        expected_version: int | None = None,
    ) -> tuple[Job, RequirementVersion]:
        """Publish draft requirements into an immutable RequirementVersion snapshot."""
        tenant_id = cls._verify_tenant_actor(actor)

        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id).with_for_update()
        )
        if not job:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested job vacancy was not found.",
            )

        # Enforce authority
        assignments = cls._get_job_assignments(uow, tenant_id, job_id)
        if not cls._has_job_authority(
            actor, assignments, {JobRole.HIRING_MANAGER.value, JobRole.RECRUITER.value}
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Actor is not assigned as recruiter or hiring manager on this job vacancy.",
            )

        # Optimistic concurrency
        if expected_version is not None and job.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Job was modified concurrently. Expected version {expected_version}, but current version is {job.version}.",
            )

        # Publishing validation
        if not job.title or not job.title.strip():
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail="Job must have a non-empty title before publishing requirements.",
            )
        if not job.description or not job.description.strip():
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail="Job must have a non-empty description before publishing requirements.",
            )

        raw_draft = job.draft_requirements or []
        if not raw_draft:
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="EMPTY_REQUIREMENTS",
                detail="Cannot publish requirements for a job with zero criteria defined.",
            )

        # Validate each criterion in draft strictly
        validated_criteria: list[CriterionDraft] = []
        for idx, item in enumerate(raw_draft):
            try:
                c = CriterionDraft.model_validate(item)
            except Exception as e:
                raise ProblemException(
                    status_code=422,
                    title="Invalid Criterion Configuration",
                    code="INVALID_CRITERION_CONFIG",
                    detail=f"Criterion at index {idx} failed schema validation: {e}",
                ) from e

            # Check text completeness
            if not c.source_requirement_text or not c.source_requirement_text.strip():
                raise ProblemException(
                    status_code=422,
                    title="Invalid Criterion Configuration",
                    code="INVALID_CRITERION_CONFIG",
                    detail=f"Criterion at index {idx} must have non-empty source_requirement_text.",
                )
            if not c.relevance_rationale or not c.relevance_rationale.strip():
                raise ProblemException(
                    status_code=422,
                    title="Invalid Criterion Configuration",
                    code="INVALID_CRITERION_CONFIG",
                    detail=f"Criterion at index {idx} must have non-empty relevance_rationale.",
                )

            validate_no_protected_traits(c.source_requirement_text, f"criterion {idx} source text")
            validate_no_protected_traits(c.relevance_rationale, f"criterion {idx} rationale")

            # Check manual criterion invariant
            if c.criterion_type == CriterionType.MANUAL_REQUIREMENT and not c.requires_human_review:
                raise ProblemException(
                    status_code=422,
                    title="Invalid Criterion Configuration",
                    code="INVALID_CRITERION_CONFIG",
                    detail=(
                        f"Criterion at index {idx} is a MANUAL_REQUIREMENT. "
                        "A manual criterion is allowed only when visibly marked as requiring human verification (requires_human_review=True)."
                    ),
                )

            validated_criteria.append(c)

        now = _utc_now()
        next_ver = (job.current_published_version_number or 0) + 1

        # Create immutable RequirementVersion
        req_version = RequirementVersion(
            id=uuid4(),
            tenant_id=tenant_id,
            job_id=job.id,
            version_number=next_ver,
            title=job.title,
            description=job.description,
            published_by=actor.user_id,
            published_at=now,
            change_summary=change_summary.strip() if change_summary else None,
            created_at=now,
        )
        uow.session.add(req_version)

        # Create Criterion rows
        for idx, c in enumerate(validated_criteria):
            crit = Criterion(
                id=uuid4(),
                tenant_id=tenant_id,
                requirement_version_id=req_version.id,
                criterion_type=c.criterion_type.value,
                is_required=c.is_required,
                source_requirement_text=c.source_requirement_text,
                relevance_rationale=c.relevance_rationale,
                requires_human_review=c.requires_human_review,
                competency_reference=c.competency_reference,
                configuration=c.configuration.model_dump(mode="json"),
                equivalence_rules=c.equivalence_rules,
                order_index=idx,
                created_at=now,
            )
            req_version.criteria.append(crit)

        # Update Job pointers and status
        job.current_published_version_id = req_version.id
        job.current_published_version_number = next_ver
        if job.status == JobStatus.DRAFT.value:
            job.status = JobStatus.OPEN.value
        job.version += 1
        job.updated_at = now

        # Emit outbox event for staleness marking in downstream modules
        uow.record_outbox_event(
            topic="jobs.requirements.published",
            payload_ref=req_version.id,
        )

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="JOB_REQUIREMENTS_PUBLISHED",
            resource_type="requirement_version",
            resource_id=req_version.id,
            payload={
                "job_id": str(job.id),
                "version_number": next_ver,
                "criteria_count": len(validated_criteria),
            },
        )

        return job, req_version

    @classmethod
    def get_requirement_versions(
        cls, uow: UnitOfWork, actor: ActorContext, job_id: UUID
    ) -> list[RequirementVersion]:
        """Fetch all published requirement versions for a job."""
        tenant_id = cls._verify_tenant_actor(actor)

        # Ensure job exists in tenant
        cls.get_job(uow, actor, job_id)

        stmt = (
            select(RequirementVersion)
            .where(
                RequirementVersion.tenant_id == tenant_id,
                RequirementVersion.job_id == job_id,
            )
            .order_by(RequirementVersion.version_number.desc())
        )
        return list(uow.session.scalars(stmt).all())

    @classmethod
    def get_requirement_version(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        version_number: int,
    ) -> RequirementVersion:
        """Fetch a specific published requirement version snapshot and its immutable criteria."""
        tenant_id = cls._verify_tenant_actor(actor)

        cls.get_job(uow, actor, job_id)

        stmt = select(RequirementVersion).where(
            RequirementVersion.tenant_id == tenant_id,
            RequirementVersion.job_id == job_id,
            RequirementVersion.version_number == version_number,
        )
        req_ver = uow.session.scalar(stmt)
        if not req_ver:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail=f"Requirement version {version_number} was not found for this job.",
            )
        return req_ver

    @classmethod
    def transition_status(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        target_status: JobStatus,
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> Job:
        """Transition job lifecycle status with strict state machine verification."""
        tenant_id = cls._verify_tenant_actor(actor)

        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id).with_for_update()
        )
        if not job:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested job vacancy was not found.",
            )

        # Enforce authority: OWNER or assigned HIRING_MANAGER or assigned RECRUITER
        assignments = cls._get_job_assignments(uow, tenant_id, job_id)
        if not cls._has_job_authority(
            actor, assignments, {JobRole.HIRING_MANAGER.value, JobRole.RECRUITER.value}
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Actor is not assigned as recruiter or hiring manager on this job vacancy.",
            )

        # Optimistic concurrency check
        if expected_version is not None and job.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Job was modified concurrently. Expected version {expected_version}, but current version is {job.version}.",
            )

        current = job.status
        target = target_status.value

        if current == target:
            raise ProblemException(
                status_code=409,
                title="Invalid Transition",
                code="INVALID_TRANSITION",
                detail=f"Job is already in status '{target}'.",
            )

        # State machine transition matrix
        allowed_transitions = {
            JobStatus.DRAFT.value: {JobStatus.OPEN.value},
            JobStatus.OPEN.value: {JobStatus.PAUSED.value, JobStatus.CLOSED.value},
            JobStatus.PAUSED.value: {JobStatus.OPEN.value, JobStatus.CLOSED.value},
            JobStatus.CLOSED.value: {JobStatus.OPEN.value},
        }

        if target not in allowed_transitions.get(current, set()):
            raise ProblemException(
                status_code=409,
                title="Invalid Transition",
                code="INVALID_TRANSITION",
                detail=f"Cannot transition job from status '{current}' to '{target}'.",
            )

        # DRAFT -> OPEN requires at least one published requirement version
        if (
            current == JobStatus.DRAFT.value
            and target == JobStatus.OPEN.value
            and not job.current_published_version_id
        ):
            raise ProblemException(
                status_code=409,
                title="Invalid Transition",
                code="INVALID_TRANSITION",
                detail="Cannot transition DRAFT job to OPEN without a published requirement version. Publish requirements first.",
            )

        # Closing a job requires a non-empty reason
        if target == JobStatus.CLOSED.value:
            if not reason or not reason.strip():
                raise ProblemException(
                    status_code=422,
                    title="Validation Error",
                    code="VALIDATION_ERROR",
                    detail="A non-empty reason is required to close a job vacancy.",
                )
            job.closed_reason = reason.strip()

        # Reopening a closed job requires an explicit non-empty reason
        if current == JobStatus.CLOSED.value and target == JobStatus.OPEN.value:
            if not reason or not reason.strip():
                raise ProblemException(
                    status_code=422,
                    title="Validation Error",
                    code="VALIDATION_ERROR",
                    detail="A non-empty reason is required to reopen a closed job vacancy.",
                )
            job.closed_reason = None

        job.status = target
        job.version += 1
        job.updated_at = _utc_now()

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="JOB_STATUS_TRANSITIONED",
            resource_type="job",
            resource_id=job.id,
            payload={"from_status": current, "to_status": target, "reason": reason},
        )

        return job

    @classmethod
    def assign_member(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        user_id: UUID,
        role: JobRole,
    ) -> JobAssignment:
        """Assign an organization member to a specific vacancy role."""
        tenant_id = cls._verify_tenant_actor(actor)

        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id).with_for_update()
        )
        if not job:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested job vacancy was not found.",
            )

        # Assignment management capability: OWNER, ADMIN, or assigned HIRING_MANAGER
        assignments = cls._get_job_assignments(uow, tenant_id, job_id)
        is_owner = actor.membership and actor.membership.role == Role.OWNER.value
        is_admin = actor.membership and actor.membership.role == Role.ADMIN.value
        is_job_hm = any(
            a.user_id == actor.user_id and a.role == JobRole.HIRING_MANAGER.value
            for a in assignments
        )

        if not (is_owner or is_admin or is_job_hm):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Actor lacks capability to manage assignments on this job.",
            )

        # Target user must be active member in tenant
        target_membership = uow.session.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
                Membership.status == "ACTIVE",
            )
        )
        if not target_membership:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="Target user does not hold an active membership in this organization.",
            )

        # Check duplicate
        existing = uow.session.scalar(
            select(JobAssignment).where(
                JobAssignment.tenant_id == tenant_id,
                JobAssignment.job_id == job_id,
                JobAssignment.user_id == user_id,
                JobAssignment.role == role.value,
            )
        )
        if existing:
            raise ProblemException(
                status_code=409,
                title="Duplicate Assignment",
                code="DUPLICATE_ASSIGNMENT",
                detail=f"User already holds assignment '{role.value}' on this job.",
            )

        now = _utc_now()
        assignment = JobAssignment(
            id=uuid4(),
            tenant_id=tenant_id,
            job_id=job_id,
            user_id=user_id,
            role=role.value,
            assigned_by=actor.user_id,
            created_at=now,
        )
        uow.session.add(assignment)

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="JOB_MEMBER_ASSIGNED",
            resource_type="job_assignment",
            resource_id=assignment.id,
            payload={"job_id": str(job_id), "user_id": str(user_id), "role": role.value},
        )

        return assignment

    @classmethod
    def remove_assignment(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID,
        user_id: UUID,
        role: JobRole | None = None,
    ) -> None:
        """Remove a user's assignment from a vacancy."""
        tenant_id = cls._verify_tenant_actor(actor)

        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id).with_for_update()
        )
        if not job:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested job vacancy was not found.",
            )

        assignments = cls._get_job_assignments(uow, tenant_id, job_id)
        is_owner = actor.membership and actor.membership.role == Role.OWNER.value
        is_admin = actor.membership and actor.membership.role == Role.ADMIN.value
        is_job_hm = any(
            a.user_id == actor.user_id and a.role == JobRole.HIRING_MANAGER.value
            for a in assignments
        )

        if not (is_owner or is_admin or is_job_hm):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Actor lacks capability to manage assignments on this job.",
            )

        stmt = select(JobAssignment).where(
            JobAssignment.tenant_id == tenant_id,
            JobAssignment.job_id == job_id,
            JobAssignment.user_id == user_id,
        )
        if role is not None:
            stmt = stmt.where(JobAssignment.role == role.value)

        matching = list(uow.session.scalars(stmt).all())
        if not matching:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="Assignment was not found.",
            )

        for a in matching:
            uow.session.delete(a)
            uow.record_audit_event(
                actor_id=actor.user_id,
                action="JOB_MEMBER_UNASSIGNED",
                resource_type="job_assignment",
                resource_id=a.id,
                payload={"job_id": str(job_id), "user_id": str(user_id), "role": a.role},
            )

    @classmethod
    def list_assignments(
        cls, uow: UnitOfWork, actor: ActorContext, job_id: UUID
    ) -> list[JobAssignment]:
        """List all member assignments on a job vacancy."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls.get_job(uow, actor, job_id)
        return cls._get_job_assignments(uow, tenant_id, job_id)
