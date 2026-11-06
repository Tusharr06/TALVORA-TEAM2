"""Applications REST API Routes: Pipeline, Stage Transitions, and History."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from talvora.modules.applications.api.dto import (
    ApplicationListResponse,
    ApplicationResponse,
    CreateApplicationRequest,
    InterviewEligibilityResponse,
    StageEventResponse,
    StageTransitionRequest,
    UpdateApplicationRequest,
)
from talvora.modules.applications.application.service import ApplicationService
from talvora.modules.applications.domain.models import ApplicationStage
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.idempotency import IdempotencyService, compute_request_hash
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1/applications", tags=["Applications"])
candidate_history_router = APIRouter(prefix="/api/v1/candidates", tags=["Applications"])
_idempotency = IdempotencyService()


def _parse_if_match(header_val: str | None, body_val: int | None) -> int | None:
    if header_val is not None:
        clean = header_val.strip().strip('"')
        try:
            return int(clean)
        except ValueError:
            pass
    return body_val


@router.get("", response_model=ApplicationListResponse)
def list_applications(
    job_id: UUID | None = Query(default=None),
    candidate_id: UUID | None = Query(default=None),
    stage: ApplicationStage | None = Query(default=None),
    interview_eligible: bool | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ApplicationListResponse:
    """Job-scoped application listing with filters for a future pipeline UI."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        applications, next_cursor, total_count = ApplicationService.list_applications(
            uow=uow,
            actor=actor,
            job_id=job_id,
            candidate_id=candidate_id,
            stage=stage,
            interview_eligible=interview_eligible,
            cursor=cursor,
            limit=limit,
        )
        return ApplicationListResponse(
            items=[ApplicationResponse.model_validate(a) for a in applications],
            next_cursor=next_cursor,
            total_count=total_count,
        )


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: CreateApplicationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    """Create an application against a job for an existing or newly created candidate."""
    req_hash = compute_request_hash(payload.model_dump(mode="json"))
    route = "/api/v1/applications"

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

        application = ApplicationService.create_application(
            uow=uow,
            actor=actor,
            job_id=payload.job_id,
            candidate_id=payload.candidate_id,
            new_candidate_display_name=payload.new_candidate_display_name,
            new_candidate_email=payload.new_candidate_email,
            new_candidate_phone=payload.new_candidate_phone,
            new_candidate_is_draft=payload.new_candidate_is_draft,
            source=payload.source,
        )
        uow.commit()

        resp_dto = ApplicationResponse.model_validate(application)
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


@router.get("/{id}", response_model=ApplicationResponse)
def get_application(
    application_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ApplicationResponse:
    """Fetch details of a single application."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        application = ApplicationService.get_application(
            uow=uow, actor=actor, application_id=application_id
        )
        return ApplicationResponse.model_validate(application)


@router.patch("/{id}", response_model=ApplicationResponse)
def update_application(
    payload: UpdateApplicationRequest,
    application_id: UUID = Path(..., alias="id"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ApplicationResponse:
    """Update non-stage application metadata (e.g. recruiter notes) with optimistic locking."""
    expected_version = _parse_if_match(if_match, payload.expected_version)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        application = ApplicationService.update_application(
            uow=uow,
            actor=actor,
            application_id=application_id,
            notes=payload.notes,
            expected_version=expected_version,
        )
        uow.commit()
        return ApplicationResponse.model_validate(application)


@router.get("/{id}/stage-history", response_model=list[StageEventResponse])
def get_stage_history(
    application_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[StageEventResponse]:
    """List the immutable stage transition history for an application."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        events = ApplicationService.list_stage_history(
            uow=uow, actor=actor, application_id=application_id
        )
        return [StageEventResponse.model_validate(e) for e in events]


@router.get("/{id}/interview-eligibility", response_model=InterviewEligibilityResponse)
def get_interview_eligibility(
    application_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> InterviewEligibilityResponse:
    """Deterministic interview eligibility query for later scheduling modules."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        result = ApplicationService.get_interview_eligibility(
            uow=uow, actor=actor, application_id=application_id
        )
        return InterviewEligibilityResponse.model_validate(result)


@router.post("/{id}/stage-transitions", response_model=ApplicationResponse)
def create_stage_transition(
    payload: StageTransitionRequest,
    application_id: UUID = Path(..., alias="id"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    """Execute a human-authorized pipeline stage transition.

    Human actor, capability, job assignment, and expected version are mandatory. A
    model or generic background/worker principal is rejected with 403.
    """
    expected_version = _parse_if_match(if_match, payload.expected_version)
    req_hash = compute_request_hash(payload.model_dump(mode="json"))
    route = f"/api/v1/applications/{application_id}/stage-transitions"

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

        application = ApplicationService.transition_stage(
            uow=uow,
            actor=actor,
            application_id=application_id,
            target_stage=payload.target_stage,
            reason=payload.reason,
            expected_version=expected_version,
        )
        uow.commit()

        resp_dto = ApplicationResponse.model_validate(application)
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


@candidate_history_router.get("/{id}/applications", response_model=list[ApplicationResponse])
def get_candidate_application_history(
    candidate_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[ApplicationResponse]:
    """Candidate application history across all jobs the actor may view."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        applications = ApplicationService.list_for_candidate(
            uow=uow, actor=actor, candidate_id=candidate_id
        )
        return [ApplicationResponse.model_validate(a) for a in applications]
