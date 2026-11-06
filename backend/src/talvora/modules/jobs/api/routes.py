"""Jobs REST API Routes."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.jobs.api.dto import (
    AssignMemberRequest,
    CreateJobRequest,
    JobAssignmentResponse,
    JobListResponse,
    JobResponse,
    PublishRequirementsRequest,
    RequirementVersionResponse,
    StatusTransitionRequest,
    UpdateDraftRequirementsRequest,
    UpdateJobRequest,
)
from talvora.modules.jobs.application.service import JobService
from talvora.modules.jobs.domain.models import JobRole, JobStatus
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.idempotency import IdempotencyService, compute_request_hash
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])
_idempotency = IdempotencyService()


def _parse_if_match(header_val: str | None, body_val: int | None) -> int | None:
    """Extract expected version from If-Match header or body payload."""
    if header_val is not None:
        clean = header_val.strip().strip('"')
        try:
            return int(clean)
        except ValueError:
            pass
    return body_val


# ============================================================================
# Vacancy Management Routes
# ============================================================================


@router.get("", response_model=JobListResponse)
def list_jobs(
    team_id: UUID | None = Query(default=None),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> JobListResponse:
    """List job vacancies with filtering, cursor pagination, and allowlisted sorting."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        jobs, next_cursor, total_count = JobService.list_jobs(
            uow=uow,
            actor=actor,
            team_id=team_id,
            status=job_status,
            cursor=cursor,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return JobListResponse(
            items=[JobResponse.model_validate(j) for j in jobs],
            next_cursor=next_cursor,
            total_count=total_count,
        )


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: CreateJobRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    """Create a new vacancy in DRAFT status. Grants creator appropriate job assignment."""
    req_hash = compute_request_hash(payload.model_dump(mode="json"))
    route = "/api/v1/jobs"

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        assert actor.tenant_id is not None
        if idempotency_key:
            cached = _idempotency.check_idempotency(
                session=uow.session,
                tenant_id=actor.tenant_id,
                route=route,
                idempotency_key=idempotency_key,
                request_hash=req_hash,
            )
            if cached is not None:
                return JSONResponse(
                    status_code=cached.response_status, content=cached.response_body
                )

        job = JobService.create_job(
            uow=uow,
            actor=actor,
            title=payload.title,
            description=payload.description,
            department=payload.department,
            location=payload.location,
            employment_type=payload.employment_type,
            team_id=payload.team_id,
            hiring_manager_id=payload.hiring_manager_id,
            initial_criteria=payload.draft_requirements,
        )
        uow.commit()

        resp_dto = JobResponse.model_validate(job)
        if idempotency_key:
            _idempotency.save_idempotency_record(
                session=uow.session,
                tenant_id=actor.tenant_id,
                route=route,
                idempotency_key=idempotency_key,
                request_hash=req_hash,
                response_status=201,
                response_body=resp_dto.model_dump(mode="json"),
                principal_id=actor.user_id,
            )
            uow.commit()

        return resp_dto


@router.get("/{id}", response_model=JobResponse)
def get_job(
    job_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> JobResponse:
    """Fetch details of a single job vacancy."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        job = JobService.get_job(uow=uow, actor=actor, job_id=job_id)
        return JobResponse.model_validate(job)


@router.patch("/{id}", response_model=JobResponse)
def update_job(
    payload: UpdateJobRequest,
    job_id: UUID = Path(..., alias="id"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> JobResponse:
    """Update mutable draft criteria or safe vacancy metadata with optimistic locking."""
    expected_version = _parse_if_match(if_match, payload.expected_version)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        job = JobService.update_job(
            uow=uow,
            actor=actor,
            job_id=job_id,
            title=payload.title,
            description=payload.description,
            department=payload.department,
            location=payload.location,
            employment_type=payload.employment_type,
            team_id=payload.team_id,
            draft_requirements=payload.draft_requirements,
            expected_version=expected_version,
        )
        uow.commit()
        return JobResponse.model_validate(job)


# ============================================================================
# Requirement Versioning and Publication Routes
# ============================================================================


@router.post("/{id}/requirement-versions", response_model=JobResponse)
def update_draft_requirements(
    payload: UpdateDraftRequirementsRequest,
    job_id: UUID = Path(..., alias="id"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> JobResponse:
    """Save or update draft requirement criteria on a vacancy."""
    expected_version = _parse_if_match(if_match, payload.expected_version)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        job = JobService.update_draft_requirements(
            uow=uow,
            actor=actor,
            job_id=job_id,
            criteria=payload.criteria,
            expected_version=expected_version,
        )
        uow.commit()
        return JobResponse.model_validate(job)


@router.get("/{id}/requirement-versions", response_model=list[RequirementVersionResponse])
def get_requirement_versions(
    job_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[RequirementVersionResponse]:
    """List all published immutable requirement versions for a job."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        versions = JobService.get_requirement_versions(uow=uow, actor=actor, job_id=job_id)
        return [RequirementVersionResponse.model_validate(v) for v in versions]


@router.get(
    "/{id}/requirement-versions/{version_number}", response_model=RequirementVersionResponse
)
def get_requirement_version(
    version_number: int,
    job_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> RequirementVersionResponse:
    """Fetch an exact published requirement version snapshot and its immutable criteria."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        version = JobService.get_requirement_version(
            uow=uow,
            actor=actor,
            job_id=job_id,
            version_number=version_number,
        )
        return RequirementVersionResponse.model_validate(version)


@router.post("/{id}/publish", response_model=RequirementVersionResponse)
def publish_requirements(
    payload: PublishRequirementsRequest,
    job_id: UUID = Path(..., alias="id"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    """Snapshot and publish draft criteria into a new immutable requirement version."""
    expected_version = _parse_if_match(if_match, payload.expected_version)
    req_hash = compute_request_hash(payload.model_dump(mode="json"))
    route = f"/api/v1/jobs/{job_id}/publish"

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        assert actor.tenant_id is not None
        if idempotency_key:
            cached = _idempotency.check_idempotency(
                session=uow.session,
                tenant_id=actor.tenant_id,
                route=route,
                idempotency_key=idempotency_key,
                request_hash=req_hash,
            )
            if cached is not None:
                return JSONResponse(
                    status_code=cached.response_status, content=cached.response_body
                )

        job, req_ver = JobService.publish_requirements(
            uow=uow,
            actor=actor,
            job_id=job_id,
            change_summary=payload.change_summary,
            expected_version=expected_version,
        )
        uow.commit()

        resp_dto = RequirementVersionResponse.model_validate(req_ver)
        if idempotency_key:
            _idempotency.save_idempotency_record(
                session=uow.session,
                tenant_id=actor.tenant_id,
                route=route,
                idempotency_key=idempotency_key,
                request_hash=req_hash,
                response_status=200,
                response_body=resp_dto.model_dump(mode="json"),
                principal_id=actor.user_id,
            )
            uow.commit()

        return resp_dto


@router.post("/{id}/status-transitions", response_model=JobResponse)
def transition_job_status(
    payload: StatusTransitionRequest,
    job_id: UUID = Path(..., alias="id"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> JobResponse:
    """Transition job lifecycle status (DRAFT, OPEN, PAUSED, CLOSED)."""
    expected_version = _parse_if_match(if_match, payload.expected_version)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        job = JobService.transition_status(
            uow=uow,
            actor=actor,
            job_id=job_id,
            target_status=payload.target_status,
            reason=payload.reason,
            expected_version=expected_version,
        )
        uow.commit()
        return JobResponse.model_validate(job)


# ============================================================================
# Job Assignments Routes
# ============================================================================


@router.get("/{id}/assignments", response_model=list[JobAssignmentResponse])
def list_job_assignments(
    job_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[JobAssignmentResponse]:
    """List all staff assignments on a job vacancy."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        assignments = JobService.list_assignments(uow=uow, actor=actor, job_id=job_id)
        return [JobAssignmentResponse.model_validate(a) for a in assignments]


@router.post(
    "/{id}/assignments", response_model=JobAssignmentResponse, status_code=status.HTTP_201_CREATED
)
def assign_job_member(
    payload: AssignMemberRequest,
    job_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> JobAssignmentResponse:
    """Assign a team member to a vacancy role (HIRING_MANAGER, RECRUITER, INTERVIEWER)."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        assignment = JobService.assign_member(
            uow=uow,
            actor=actor,
            job_id=job_id,
            user_id=payload.user_id,
            role=payload.role,
        )
        uow.commit()
        return JobAssignmentResponse.model_validate(assignment)


@router.delete("/{id}/assignments/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_job_assignment(
    user_id: UUID,
    job_id: UUID = Path(..., alias="id"),
    role: JobRole | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Response:
    """Remove a member's assignment from a vacancy."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        JobService.remove_assignment(
            uow=uow,
            actor=actor,
            job_id=job_id,
            user_id=user_id,
            role=role,
        )
        uow.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
