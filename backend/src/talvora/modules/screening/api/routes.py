"""Deterministic screening and recruiter override routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from talvora.modules.facts.domain.models import CandidateFactEvidence
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.screening.api.dto import (
    ScreeningFactCitationResponse,
    ScreeningOverrideRequest,
    ScreeningOverrideResponse,
    ScreeningOverridesResponse,
    ScreeningResultResponse,
    ScreeningRunResponse,
)
from talvora.modules.screening.application.service import ScreeningService
from talvora.modules.screening.domain.models import ScreeningOutcome, ScreeningResultFact
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1", tags=["Screening"])


def _run_response(
    uow: UnitOfWork,
    run_id: UUID,
    actor: ActorContext,
) -> ScreeningRunResponse:
    run, results, latest_override = ScreeningService.get_run(uow, actor, run_id)
    result_responses: list[ScreeningResultResponse] = []
    for result in results:
        fact_ids = list(
            uow.session.scalars(
                select(ScreeningResultFact.fact_revision_id).where(
                    ScreeningResultFact.tenant_id == run.tenant_id,
                    ScreeningResultFact.screening_result_id == result.id,
                )
            ).all()
        )
        links = (
            uow.session.execute(
                select(
                    CandidateFactEvidence.fact_revision_id,
                    CandidateFactEvidence.evidence_fragment_id,
                ).where(
                    CandidateFactEvidence.tenant_id == run.tenant_id,
                    CandidateFactEvidence.fact_revision_id.in_(fact_ids),
                )
            ).all()
            if fact_ids
            else []
        )
        citations_by_fact: dict[UUID, list[UUID]] = {}
        for fact_id, evidence_id in links:
            citations_by_fact.setdefault(fact_id, []).append(evidence_id)
        result_responses.append(
            ScreeningResultResponse(
                criterion_id=result.criterion_id,
                criterion_type=result.criterion_type,
                is_required=result.is_required,
                outcome=ScreeningOutcome(result.outcome),
                explanation=result.explanation,
                citations=[
                    ScreeningFactCitationResponse(
                        fact_revision_id=fact_id,
                        evidence_fragment_ids=evidence_ids,
                    )
                    for fact_id, evidence_ids in citations_by_fact.items()
                ],
            )
        )
    return ScreeningRunResponse(
        id=run.id,
        application_id=run.application_id,
        candidate_id=run.candidate_id,
        application_document_id=run.application_document_id,
        document_version_id=run.document_version_id,
        requirement_version_id=run.requirement_version_id,
        outcome=ScreeningOutcome(run.outcome),
        effective_outcome=(
            ScreeningOutcome(latest_override.outcome)
            if latest_override
            else ScreeningOutcome(run.outcome)
        ),
        rule_version=run.rule_version,
        created_at=run.created_at,
        results=result_responses,
        latest_override=(
            ScreeningOverrideResponse.model_validate(latest_override) if latest_override else None
        ),
    )


@router.post(
    "/applications/{application_id}/screening",
    response_model=ScreeningRunResponse,
    status_code=201,
)
def run_screening(
    application_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ScreeningRunResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        run = ScreeningService.run(uow, actor, application_id)
        response = _run_response(uow, run.id, actor)
        uow.commit()
        return response


@router.get("/screening/{screening_run_id}", response_model=ScreeningRunResponse)
def get_screening(
    screening_run_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ScreeningRunResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        return _run_response(uow, screening_run_id, actor)


@router.post(
    "/screening/{screening_run_id}/overrides",
    response_model=ScreeningRunResponse,
    status_code=201,
)
def override_screening(
    payload: ScreeningOverrideRequest,
    screening_run_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ScreeningRunResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        ScreeningService.override(uow, actor, screening_run_id, payload.outcome, payload.reason)
        response = _run_response(uow, screening_run_id, actor)
        uow.commit()
        return response


@router.get(
    "/screening/{screening_run_id}/overrides",
    response_model=ScreeningOverridesResponse,
)
def list_screening_overrides(
    screening_run_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ScreeningOverridesResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        run, _, _ = ScreeningService.get_run(uow, actor, screening_run_id)
        overrides = ScreeningService.list_overrides(uow, actor, screening_run_id)
        return ScreeningOverridesResponse(
            screening_run_id=run.id,
            original_outcome=ScreeningOutcome(run.outcome),
            overrides=[ScreeningOverrideResponse.model_validate(row) for row in overrides],
        )
