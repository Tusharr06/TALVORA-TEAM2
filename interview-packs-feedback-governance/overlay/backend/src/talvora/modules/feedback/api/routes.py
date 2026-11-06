"""FastAPI Routes for Structured Feedback and Controlled DOCX/PDF Exports (P16)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.feedback.api.dto import (
    AmendMyFeedbackRequest,
    CompetencyRatingResponse,
    CreatePackExportRequest,
    ExportArtifactResponse,
    FeedbackResponse,
    FeedbackRevisionResponse,
    PutMyFeedbackDraftRequest,
    RevokeExportRequest,
    SubmitMyFeedbackRequest,
)
from talvora.modules.feedback.application.service import (
    CompetencyRatingInputSpec,
    FeedbackAggregateBundle,
    FeedbackService,
    PackExportService,
)
from talvora.modules.feedback.domain.models import EXPORT_REVOCATION_DISCLOSURE, ExportArtifact
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.interview_packs.application.ports import InterviewContextPort
from talvora.shared.application.actor import ActorContext
from talvora.shared.domain.clock import Clock
from talvora.shared.infrastructure.idempotency import IdempotencyService, compute_request_hash
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1", tags=["Feedback & Controlled Exports"])


def _to_feedback_response(bundle: FeedbackAggregateBundle) -> FeedbackResponse:
    fb = bundle.feedback
    return FeedbackResponse(
        id=fb.id,
        interview_id=fb.interview_id,
        application_id=fb.application_id,
        job_id=fb.job_id,
        candidate_id=fb.candidate_id,
        interviewer_user_id=fb.interviewer_user_id,
        pack_revision_id=fb.pack_revision_id,
        framework_version_id=fb.framework_version_id,
        status=fb.status,
        draft_overall_observations=(
            fb.draft_overall_observations if bundle.include_draft_fields else None
        ),
        draft_human_recommendation=(
            fb.draft_human_recommendation if bundle.include_draft_fields else None
        ),
        draft_ratings=list(fb.draft_ratings or []) if bundle.include_draft_fields else None,
        latest_submitted_revision=fb.latest_submitted_revision,
        first_submitted_at=fb.first_submitted_at,
        last_submitted_at=fb.last_submitted_at,
        version=fb.version,
        created_at=fb.created_at,
        updated_at=fb.updated_at,
        revisions=[
            FeedbackRevisionResponse(
                id=rd.revision.id,
                revision_number=rd.revision.revision_number,
                pack_revision_id=rd.revision.pack_revision_id,
                framework_version_id=rd.revision.framework_version_id,
                overall_observations=rd.revision.overall_observations,
                human_recommendation=rd.revision.human_recommendation,
                amendment_reason=rd.revision.amendment_reason,
                prior_revision_id=rd.revision.prior_revision_id,
                is_late_submission=rd.revision.is_late_submission,
                submitted_by=rd.revision.submitted_by,
                submitted_at=rd.revision.submitted_at,
                ratings=[
                    CompetencyRatingResponse(
                        id=rt.id,
                        competency_id=rt.competency_id,
                        rating=rt.rating,
                        observation=rt.observation,
                        question_notes=rt.question_notes,
                    )
                    for rt in rd.ratings
                ],
            )
            for rd in bundle.revisions
        ],
    )


def _to_export_response(art: ExportArtifact) -> ExportArtifactResponse:
    return ExportArtifactResponse(
        id=art.id,
        pack_revision_id=art.pack_revision_id,
        interview_id=art.interview_id,
        application_id=art.application_id,
        job_id=art.job_id,
        candidate_id=art.candidate_id,
        format=art.format,
        content_scope=art.content_scope,
        status=art.status,
        sha256_checksum=art.sha256_checksum,
        byte_size=art.byte_size,
        media_type=art.media_type,
        requested_by=art.requested_by,
        expires_at=art.expires_at,
        revoked_at=art.revoked_at,
        revoked_reason=art.revoked_reason,
        created_at=art.created_at,
        download_url=f"/api/v1/exports/{art.id}/download",
        revocation_disclosure=EXPORT_REVOCATION_DISCLOSURE,
    )


def _get_interview_port(request: Request) -> InterviewContextPort | None:
    return getattr(request.app.state, "interview_port", None)


def _get_storage(request: Request) -> ObjectStoragePort:
    storage: ObjectStoragePort = request.app.state.object_storage
    return storage


def _get_clock(request: Request) -> Clock | None:
    return getattr(request.app.state, "clock", None)


@router.get(
    "/interviews/{interview_id}/feedback/me",
    response_model=FeedbackResponse,
    operation_id="getMyInterviewFeedback",
)
def get_my_interview_feedback(
    request: Request,
    interview_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> FeedbackResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        bundle = FeedbackService.get_my_feedback(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            interview_port=_get_interview_port(request),
        )
        resp = _to_feedback_response(bundle)
        uow.commit()
        return resp


@router.put(
    "/interviews/{interview_id}/feedback/me",
    response_model=FeedbackResponse,
    operation_id="saveMyInterviewFeedbackDraft",
)
def put_my_interview_feedback_draft(
    request: Request,
    payload: PutMyFeedbackDraftRequest,
    interview_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> FeedbackResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        bundle = FeedbackService.save_my_draft(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            expected_version=payload.expected_version,
            overall_observations=payload.overall_observations,
            human_recommendation=payload.human_recommendation,
            ratings=[
                CompetencyRatingInputSpec(
                    competency_id=r.competency_id,
                    rating=r.rating,
                    observation=r.observation,
                    question_notes=r.question_notes,
                )
                for r in payload.ratings
            ],
            interview_port=_get_interview_port(request),
        )
        resp = _to_feedback_response(bundle)
        uow.commit()
        return resp


@router.post(
    "/interviews/{interview_id}/feedback/me/submit",
    response_model=FeedbackResponse,
    operation_id="submitMyInterviewFeedback",
)
def submit_my_interview_feedback(
    request: Request,
    payload: SubmitMyFeedbackRequest,
    interview_id: UUID = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        idem = IdempotencyService(clock=uow.clock)
        route_key = f"POST /api/v1/interviews/{interview_id}/feedback/me/submit"
        req_hash = compute_request_hash(payload.model_dump(mode="json"))
        if idempotency_key and actor.tenant_id:
            cached = idem.check_idempotency(
                uow.session, actor.tenant_id, route_key, idempotency_key, req_hash
            )
            if cached is not None:
                return JSONResponse(
                    status_code=cached.response_status, content=cached.response_body
                )

        bundle = FeedbackService.submit_my_feedback(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            expected_version=payload.expected_version,
            overall_observations=payload.overall_observations,
            human_recommendation=payload.human_recommendation,
            ratings=(
                [
                    CompetencyRatingInputSpec(
                        competency_id=r.competency_id,
                        rating=r.rating,
                        observation=r.observation,
                        question_notes=r.question_notes,
                    )
                    for r in payload.ratings
                ]
                if payload.ratings is not None
                else None
            ),
            interview_port=_get_interview_port(request),
        )
        resp = _to_feedback_response(bundle)
        if idempotency_key and actor.tenant_id:
            idem.save_idempotency_record(
                uow.session,
                actor.tenant_id,
                route_key,
                idempotency_key,
                req_hash,
                200,
                resp.model_dump(mode="json"),
                principal_id=actor.user_id,
            )
        uow.commit()
        return resp


@router.post(
    "/interviews/{interview_id}/feedback/me/amend",
    response_model=FeedbackResponse,
    operation_id="amendMyInterviewFeedback",
)
def amend_my_interview_feedback(
    request: Request,
    payload: AmendMyFeedbackRequest,
    interview_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> FeedbackResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        bundle = FeedbackService.amend_my_feedback(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            expected_version=payload.expected_version,
            amendment_reason=payload.amendment_reason,
            overall_observations=payload.overall_observations,
            human_recommendation=payload.human_recommendation,
            ratings=[
                CompetencyRatingInputSpec(
                    competency_id=r.competency_id,
                    rating=r.rating,
                    observation=r.observation,
                    question_notes=r.question_notes,
                )
                for r in payload.ratings
            ],
            interview_port=_get_interview_port(request),
        )
        resp = _to_feedback_response(bundle)
        uow.commit()
        return resp


@router.get(
    "/interviews/{interview_id}/feedback",
    response_model=list[FeedbackResponse],
    operation_id="listInterviewFeedback",
)
def list_interview_feedback(
    request: Request,
    interview_id: UUID = Path(...),
    blind_override: bool = Query(default=False),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[FeedbackResponse]:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        bundles = FeedbackService.list_interview_feedback(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            use_blind_override=blind_override,
            interview_port=_get_interview_port(request),
        )
        resp = [_to_feedback_response(b) for b in bundles]
        uow.commit()
        return resp


@router.post(
    "/pack-revisions/{pack_revision_id}/exports",
    response_model=ExportArtifactResponse,
    status_code=201,
    operation_id="createPackExport",
)
def create_pack_export(
    request: Request,
    payload: CreatePackExportRequest,
    pack_revision_id: UUID = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        idem = IdempotencyService(clock=uow.clock)
        route_key = f"POST /api/v1/pack-revisions/{pack_revision_id}/exports"
        req_hash = compute_request_hash(payload.model_dump(mode="json"))
        if idempotency_key and actor.tenant_id:
            cached = idem.check_idempotency(
                uow.session, actor.tenant_id, route_key, idempotency_key, req_hash
            )
            if cached is not None:
                return JSONResponse(
                    status_code=cached.response_status, content=cached.response_body
                )

        max_render_sec = float(getattr(request.app.state, "export_max_render_seconds", 15.0))
        artifact = PackExportService.create_pack_export(
            uow=uow,
            actor=actor,
            pack_revision_id=pack_revision_id,
            export_format=payload.format,
            content_scope=payload.content_scope,
            storage=_get_storage(request),
            bucket_name=request.app.state.settings.s3_clean_bucket_name,
            interview_port=_get_interview_port(request),
            max_render_seconds=max_render_sec,
        )
        resp = _to_export_response(artifact)
        if idempotency_key and actor.tenant_id:
            idem.save_idempotency_record(
                uow.session,
                actor.tenant_id,
                route_key,
                idempotency_key,
                req_hash,
                201,
                resp.model_dump(mode="json"),
                principal_id=actor.user_id,
            )
        uow.commit()
        return resp


@router.get(
    "/exports/{export_id}",
    response_model=ExportArtifactResponse,
    operation_id="getExportMetadata",
)
def get_export_metadata(
    request: Request,
    export_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> ExportArtifactResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        art = PackExportService.get_export_metadata(
            uow=uow,
            actor=actor,
            export_id=export_id,
            interview_port=_get_interview_port(request),
        )
        return _to_export_response(art)


@router.get(
    "/exports/{export_id}/download",
    operation_id="downloadExportArtifact",
)
def download_export_artifact(
    request: Request,
    export_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> Response:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        artifact, body = PackExportService.download_export(
            uow=uow,
            actor=actor,
            export_id=export_id,
            storage=_get_storage(request),
            interview_port=_get_interview_port(request),
        )
        ext = "docx" if artifact.format == "DOCX" else "pdf"
        filename = f"talvora-pack-rev{artifact.pack_revision_id}.{ext}"
        uow.commit()
        return Response(
            content=body,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "private, no-store, max-age=0",
                "X-Content-SHA256": artifact.sha256_checksum,
                "X-Export-Disclosure": EXPORT_REVOCATION_DISCLOSURE,
            },
        )


@router.post(
    "/exports/{export_id}/revoke",
    response_model=ExportArtifactResponse,
    operation_id="revokeExportArtifact",
)
def revoke_export_artifact(
    request: Request,
    payload: RevokeExportRequest,
    export_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> ExportArtifactResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        art = PackExportService.revoke_export(
            uow=uow,
            actor=actor,
            export_id=export_id,
            reason=payload.reason,
            storage=_get_storage(request),
        )
        resp = _to_export_response(art)
        uow.commit()
        return resp
