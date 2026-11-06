"""Candidates REST API Routes."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from talvora.modules.candidates.api.dto import (
    CandidateListResponse,
    CandidateResponse,
    ContactVerificationRequest,
    ContactVerificationResponse,
    CreateCandidateRequest,
    CreateCandidateResponse,
    DuplicateSuggestionResponse,
    UpdateCandidateRequest,
)
from talvora.modules.candidates.application.service import CandidateService
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.idempotency import IdempotencyService, compute_request_hash
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1/candidates", tags=["Candidates"])
_idempotency = IdempotencyService()


def _parse_if_match(header_val: str | None, body_val: int | None) -> int | None:
    if header_val is not None:
        clean = header_val.strip().strip('"')
        try:
            return int(clean)
        except ValueError:
            pass
    return body_val


@router.get("", response_model=CandidateListResponse)
def list_candidates(
    email: str | None = Query(default=None),
    name: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> CandidateListResponse:
    """Search/list candidates by tenant-local normalized email/name."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        candidates, next_cursor, total_count = CandidateService.list_candidates(
            uow=uow, actor=actor, email=email, name=name, cursor=cursor, limit=limit
        )
        return CandidateListResponse(
            items=[CandidateResponse.model_validate(c) for c in candidates],
            next_cursor=next_cursor,
            total_count=total_count,
        )


@router.get("/duplicates", response_model=list[DuplicateSuggestionResponse])
def find_duplicates(
    email: str | None = Query(default=None),
    name: str | None = Query(default=None),
    exclude_candidate_id: UUID | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[DuplicateSuggestionResponse]:
    """Conservative, minimum-exposure duplicate candidate suggestions. Never merges identities."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        suggestions = CandidateService.find_duplicate_suggestions(
            uow=uow,
            actor=actor,
            email=email,
            name=name,
            exclude_candidate_id=exclude_candidate_id,
        )
        return [DuplicateSuggestionResponse.model_validate(s) for s in suggestions]


@router.post("", response_model=CreateCandidateResponse, status_code=status.HTTP_201_CREATED)
def create_candidate(
    payload: CreateCandidateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    """Create a candidate identity. Surfaces conservative duplicate suggestions; never merges."""
    req_hash = compute_request_hash(payload.model_dump(mode="json"))
    route = "/api/v1/candidates"

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

        candidate = CandidateService.create_candidate(
            uow=uow,
            actor=actor,
            display_name=payload.display_name,
            email=payload.email,
            phone=payload.phone,
            source=payload.source,
            is_draft=payload.is_draft,
        )
        suggestions = CandidateService.find_duplicate_suggestions(
            uow=uow,
            actor=actor,
            email=payload.email,
            name=payload.display_name,
            exclude_candidate_id=candidate.id,
        )
        uow.commit()

        resp_dto = CreateCandidateResponse(
            candidate=CandidateResponse.model_validate(candidate),
            duplicate_suggestions=[
                DuplicateSuggestionResponse.model_validate(s) for s in suggestions
            ],
        )
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


@router.get("/{id}", response_model=CandidateResponse)
def get_candidate(
    candidate_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> CandidateResponse:
    """Fetch details of a single candidate."""
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        candidate = CandidateService.get_candidate(uow=uow, actor=actor, candidate_id=candidate_id)
        return CandidateResponse.model_validate(candidate)


@router.patch("/{id}", response_model=CandidateResponse)
def update_candidate(
    payload: UpdateCandidateRequest,
    candidate_id: UUID = Path(..., alias="id"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> CandidateResponse:
    """Update candidate contact/display details with optimistic locking."""
    expected_version = _parse_if_match(if_match, payload.expected_version)

    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        candidate = CandidateService.update_candidate(
            uow=uow,
            actor=actor,
            candidate_id=candidate_id,
            display_name=payload.display_name,
            email=payload.email,
            phone=payload.phone,
            expected_version=expected_version,
        )
        uow.commit()
        return CandidateResponse.model_validate(candidate)


@router.post(
    "/{id}/contact-verifications",
    response_model=ContactVerificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_contact_verification(
    payload: ContactVerificationRequest,
    candidate_id: UUID = Path(..., alias="id"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    """Record a human command attesting to an exact contact address and version.

    This is NOT proof of mailbox ownership unless a separate verification flow exists.
    """
    expected_version = _parse_if_match(if_match, payload.expected_version)
    req_hash = compute_request_hash(payload.model_dump(mode="json"))
    route = f"/api/v1/candidates/{candidate_id}/contact-verifications"

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

        verification = CandidateService.verify_contact(
            uow=uow,
            actor=actor,
            candidate_id=candidate_id,
            contact_revision_number=payload.contact_revision_number,
            verified_address=payload.verified_address,
            verification_source=payload.verification_source,
            notes=payload.notes,
            expected_version=expected_version,
        )
        uow.commit()

        resp_dto = ContactVerificationResponse.model_validate(verification)
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
