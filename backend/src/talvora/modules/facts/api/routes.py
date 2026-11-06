"""Candidate fact derivation and human review routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from talvora.modules.facts.api.dto import (
    CandidateFactResponse,
    DeriveCandidateFactRequest,
    ReviewCandidateFactRequest,
)
from talvora.modules.facts.application.service import CandidateFactService
from talvora.modules.facts.domain.models import CandidateFactEvidence, CandidateFactRevision
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1/candidates", tags=["Candidate Facts"])


def _response(
    uow: UnitOfWork,
    fact: CandidateFactRevision,
) -> CandidateFactResponse:
    evidence_ids = list(
        uow.session.scalars(
            select(CandidateFactEvidence.evidence_fragment_id).where(
                CandidateFactEvidence.tenant_id == fact.tenant_id,
                CandidateFactEvidence.fact_revision_id == fact.id,
            )
        ).all()
    )
    return CandidateFactResponse(
        id=fact.id,
        candidate_id=fact.candidate_id,
        fact_key=fact.fact_key,
        revision_number=fact.revision_number,
        fact_type=fact.fact_type,
        value=fact.value,
        extracted_value=fact.extracted_value,
        polarity=fact.polarity,
        extracted_polarity=fact.extracted_polarity,
        status=fact.status,
        review_note=fact.review_note,
        extraction_run_id=fact.extraction_run_id,
        created_by=fact.created_by,
        created_at=fact.created_at,
        evidence_fragment_ids=evidence_ids,
    )


@router.get("/{candidate_id}/facts", response_model=list[CandidateFactResponse])
def list_candidate_facts(
    candidate_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[CandidateFactResponse]:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        rows = CandidateFactService.list_facts(uow, actor, candidate_id)
        return [
            CandidateFactResponse(
                id=fact.id,
                candidate_id=fact.candidate_id,
                fact_key=fact.fact_key,
                revision_number=fact.revision_number,
                fact_type=fact.fact_type,
                value=fact.value,
                extracted_value=fact.extracted_value,
                polarity=fact.polarity,
                extracted_polarity=fact.extracted_polarity,
                status=fact.status,
                review_note=fact.review_note,
                extraction_run_id=fact.extraction_run_id,
                created_by=fact.created_by,
                created_at=fact.created_at,
                evidence_fragment_ids=evidence_ids,
            )
            for fact, evidence_ids in rows
        ]


@router.post("/{candidate_id}/facts", response_model=CandidateFactResponse, status_code=201)
def derive_candidate_fact(
    payload: DeriveCandidateFactRequest,
    candidate_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> CandidateFactResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        fact = CandidateFactService.derive_fact(
            uow,
            actor,
            candidate_id,
            payload.fact_type,
            payload.value,
            payload.polarity,
            payload.evidence_fragment_ids,
        )
        response = _response(uow, fact)
        uow.commit()
        return response


@router.post(
    "/{candidate_id}/facts/{fact_key}/review",
    response_model=CandidateFactResponse,
)
def review_candidate_fact(
    payload: ReviewCandidateFactRequest,
    candidate_id: UUID = Path(...),
    fact_key: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> CandidateFactResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        fact = CandidateFactService.review_fact(
            uow,
            actor,
            candidate_id,
            fact_key,
            payload.expected_revision,
            payload.status,
            payload.corrected_value,
            payload.polarity,
            payload.review_note,
        )
        response = _response(uow, fact)
        uow.commit()
        return response


@router.get(
    "/{candidate_id}/facts/{fact_key}/revisions",
    response_model=list[CandidateFactResponse],
)
def list_candidate_fact_revisions(
    candidate_id: UUID = Path(...),
    fact_key: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[CandidateFactResponse]:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        revisions = CandidateFactService.list_revisions(uow, actor, candidate_id, fact_key)
        return [_response(uow, fact) for fact in revisions]
