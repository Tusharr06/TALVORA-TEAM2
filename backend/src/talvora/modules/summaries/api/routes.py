"""Evidence-cited summary generation and history routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.summaries.api.dto import (
    CandidateSummaryResponse,
    GenerateSummaryRequest,
    SummaryClaimResponse,
)
from talvora.modules.summaries.application.service import SummaryService
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1", tags=["Candidate Summaries"])


def _response(uow: UnitOfWork, summary_id: UUID, actor: ActorContext) -> CandidateSummaryResponse:
    summary, claims = SummaryService.get_summary(uow, actor, summary_id)
    return CandidateSummaryResponse(
        id=summary.id,
        application_id=summary.application_id,
        screening_run_id=summary.screening_run_id,
        revision_number=summary.revision_number,
        quality_status=summary.quality_status,
        summary_text=summary.summary_text,
        quality_issues=list(summary.quality_issues or []),
        created_by=summary.created_by,
        created_at=summary.created_at,
        claims=[
            SummaryClaimResponse(
                id=claim.id,
                fact_revision_id=claim.fact_revision_id,
                text=claim.text,
                evidence_fragment_ids=evidence_ids,
            )
            for claim, evidence_ids in claims
        ],
    )


@router.post(
    "/applications/{application_id}/summaries",
    response_model=CandidateSummaryResponse,
    status_code=201,
)
def generate_summary(
    payload: GenerateSummaryRequest,
    application_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> CandidateSummaryResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        draft_claims = (
            [
                (claim.text, claim.fact_revision_id, claim.evidence_fragment_ids)
                for claim in payload.draft_claims
            ]
            if payload.draft_claims is not None
            else None
        )
        summary = SummaryService.generate(
            uow,
            actor,
            application_id,
            payload.screening_run_id,
            draft_claims,
        )
        response = _response(uow, summary.id, actor)
        uow.commit()
        return response


@router.get(
    "/applications/{application_id}/summaries",
    response_model=list[CandidateSummaryResponse],
)
def list_summaries(
    application_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[CandidateSummaryResponse]:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        summaries = SummaryService.list_summaries(uow, actor, application_id)
        return [_response(uow, summary.id, actor) for summary in summaries]


@router.get("/summaries/{summary_id}", response_model=CandidateSummaryResponse)
def get_summary(
    summary_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> CandidateSummaryResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        return _response(uow, summary_id, actor)
