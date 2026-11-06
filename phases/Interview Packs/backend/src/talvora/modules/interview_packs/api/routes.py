"""FastAPI Routes for Competency Frameworks and Interview Packs (P15)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.interview_packs.api.dto import (
    CompetencyFrameworkDetailResponse,
    CompetencyFrameworkSummaryResponse,
    CompetencyResponse,
    CreateCompetencyFrameworkRequest,
    CreateFrameworkVersionRequest,
    CreatePackRunRequest,
    InterviewContextResponse,
    PackClaimResponse,
    PackQuestionResponse,
    PackReviewResponse,
    PackRevisionResponse,
    PatchPackRevisionRequest,
    PublishPackRevisionRequest,
    SyncInterviewContextRequest,
)
from talvora.modules.interview_packs.application.ports import (
    InterviewContextPort,
    StructuredPackAIProvider,
)
from talvora.modules.interview_packs.application.service import (
    CompetencyDraftSpec,
    CompetencyFrameworkService,
    InterviewPackService,
    PackClaimEditSpec,
    PackDetailBundle,
    PackQuestionEditSpec,
)
from talvora.modules.interview_packs.domain.models import (
    Competency,
    CompetencyFramework,
    CompetencyFrameworkVersion,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.idempotency import IdempotencyService, compute_request_hash
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(prefix="/api/v1", tags=["Interview Packs & Competency Frameworks"])


def _to_framework_summary(fw: CompetencyFramework) -> CompetencyFrameworkSummaryResponse:
    return CompetencyFrameworkSummaryResponse(
        id=fw.id,
        name=fw.name,
        slug=fw.slug,
        job_id=fw.job_id,
        stage=fw.stage,
        seniority_band=fw.seniority_band,
        status=fw.status,
        latest_version_number=fw.latest_version_number,
        latest_published_version_id=fw.latest_published_version_id,
        latest_published_version_number=fw.latest_published_version_number,
        version=fw.version,
        created_by=fw.created_by,
        created_at=fw.created_at,
        updated_at=fw.updated_at,
    )


def _to_framework_detail(
    fw: CompetencyFramework,
    fw_ver: CompetencyFrameworkVersion,
    comps: list[Competency],
) -> CompetencyFrameworkDetailResponse:
    return CompetencyFrameworkDetailResponse(
        framework=_to_framework_summary(fw),
        version_id=fw_ver.id,
        version_number=fw_ver.version_number,
        version_status=fw_ver.status,
        summary=fw_ver.summary,
        behavioral_scale_guide=fw_ver.behavioral_scale_guide,
        published_by=fw_ver.published_by,
        published_at=fw_ver.published_at,
        competencies=[
            CompetencyResponse(
                id=c.id,
                code=c.code,
                name=c.name,
                definition=c.definition,
                display_order=c.display_order,
                observation_required=c.observation_required,
                rubric_anchors=dict(c.rubric_anchors),
            )
            for c in comps
        ],
    )


def _to_pack_response(bundle: PackDetailBundle) -> PackRevisionResponse:
    rev = bundle.revision
    return PackRevisionResponse(
        id=rev.id,
        interview_id=rev.interview_id,
        application_id=rev.application_id,
        job_id=rev.job_id,
        candidate_id=rev.candidate_id,
        revision_number=rev.revision_number,
        status=rev.status,
        is_stale=bundle.is_stale,
        stale_reasons=bundle.stale_reasons,
        requirement_version_id=rev.requirement_version_id,
        document_version_id=rev.document_version_id,
        screening_run_id=rev.screening_run_id,
        summary_revision_id=rev.summary_revision_id,
        framework_version_id=rev.framework_version_id,
        stage=rev.stage,
        seniority_level=rev.seniority_level,
        duration_minutes=rev.duration_minutes,
        schedule_revision=rev.schedule_revision,
        fact_snapshot_hash=rev.fact_snapshot_hash,
        generation_reason=rev.generation_reason,
        based_on_revision_id=rev.based_on_revision_id,
        model_or_generator_id=rev.model_or_generator_id,
        candidate_overview=rev.candidate_overview,
        role_requirements=list(rev.role_requirements or []),
        candidate_evidence_summary=rev.candidate_evidence_summary,
        relevant_responsibilities=list(rev.relevant_responsibilities or []),
        competency_mapping=list(rev.competency_mapping or []),
        interviewer_instructions=rev.interviewer_instructions,
        time_allocation=dict(rev.time_allocation or {}),
        evaluation_rubric=list(rev.evaluation_rubric or []),
        notes_guidance=rev.notes_guidance,
        feedback_form_structure=dict(rev.feedback_form_structure or {}),
        quality_warnings=list(rev.quality_warnings or []),
        version=rev.version,
        author_id=rev.author_id,
        published_by=rev.published_by,
        published_at=rev.published_at,
        created_at=rev.created_at,
        updated_at=rev.updated_at,
        claims=[
            PackClaimResponse(
                id=cl.id,
                claim_index=cl.claim_index,
                section=cl.section,
                content_scope=cl.content_scope,
                classification=cl.classification,
                statement=cl.statement,
                fact_revision_id=cl.fact_revision_id,
                evidence_fragment_ids=[UUID(x) for x in (cl.evidence_fragment_ids or [])],
                source_note=cl.source_note,
            )
            for cl in bundle.claims
        ],
        questions=[
            PackQuestionResponse(
                id=q.id,
                order_index=q.order_index,
                category=q.category,
                content_scope=q.content_scope,
                premise_classification=q.premise_classification,
                competency_id=q.competency_id,
                allocated_minutes=q.allocated_minutes,
                prompt_text=q.prompt_text,
                premise_text=q.premise_text,
                expected_signals=list(q.expected_signals or []),
                fact_revision_id=q.fact_revision_id,
                evidence_fragment_ids=[UUID(x) for x in (q.evidence_fragment_ids or [])],
            )
            for q in bundle.questions
        ],
        reviews=[
            PackReviewResponse(
                id=r.id,
                reviewer_id=r.reviewer_id,
                decision=r.decision,
                notes=r.notes,
                created_at=r.created_at,
            )
            for r in bundle.reviews
        ],
    )


def _get_ai_provider(request: Request) -> StructuredPackAIProvider | None:
    return getattr(request.app.state, "pack_ai_provider", None)


def _get_interview_port(request: Request) -> InterviewContextPort | None:
    return getattr(request.app.state, "interview_port", None)


@router.get(
    "/competency-frameworks",
    response_model=list[CompetencyFrameworkSummaryResponse],
    operation_id="listCompetencyFrameworks",
)
def list_competency_frameworks(
    job_id: UUID | None = Query(default=None),
    stage: str | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[CompetencyFrameworkSummaryResponse]:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        rows = CompetencyFrameworkService.list_frameworks(uow, actor, job_id=job_id, stage=stage)
        return [_to_framework_summary(r) for r in rows]


@router.post(
    "/competency-frameworks",
    response_model=CompetencyFrameworkDetailResponse,
    status_code=201,
    operation_id="createCompetencyFramework",
)
def create_competency_framework(
    payload: CreateCompetencyFrameworkRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        idem = IdempotencyService()
        req_hash = compute_request_hash(payload.model_dump(mode="json"))
        if idempotency_key and actor.tenant_id:
            cached = idem.check_idempotency(
                uow.session,
                actor.tenant_id,
                "POST /api/v1/competency-frameworks",
                idempotency_key,
                req_hash,
            )
            if cached is not None:
                return JSONResponse(
                    status_code=cached.response_status, content=cached.response_body
                )

        fw, fw_ver, comps = CompetencyFrameworkService.create_framework(
            uow=uow,
            actor=actor,
            name=payload.name,
            slug=payload.slug,
            stage=payload.stage,
            seniority_band=payload.seniority_band,
            job_id=payload.job_id,
            summary=payload.summary,
            behavioral_scale_guide=payload.behavioral_scale_guide,
            competencies=[
                CompetencyDraftSpec(
                    code=c.code,
                    name=c.name,
                    definition=c.definition,
                    observation_required=c.observation_required,
                    rubric_anchors=c.rubric_anchors,
                    display_order=c.display_order,
                )
                for c in payload.competencies
            ],
        )
        resp = _to_framework_detail(fw, fw_ver, comps)
        if idempotency_key and actor.tenant_id:
            idem.save_idempotency_record(
                uow.session,
                actor.tenant_id,
                "POST /api/v1/competency-frameworks",
                idempotency_key,
                req_hash,
                201,
                resp.model_dump(mode="json"),
                principal_id=actor.user_id,
            )
        uow.commit()
        return resp


@router.get(
    "/competency-frameworks/{framework_id}",
    response_model=CompetencyFrameworkDetailResponse,
    operation_id="getCompetencyFramework",
)
def get_competency_framework(
    framework_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> CompetencyFrameworkDetailResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        fw, fw_ver, comps = CompetencyFrameworkService.get_framework_version(
            uow, actor, framework_id
        )
        return _to_framework_detail(fw, fw_ver, comps)


@router.post(
    "/competency-frameworks/{framework_id}/versions",
    response_model=CompetencyFrameworkDetailResponse,
    status_code=201,
    operation_id="createCompetencyFrameworkVersion",
)
def create_competency_framework_version(
    payload: CreateFrameworkVersionRequest,
    framework_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> CompetencyFrameworkDetailResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        fw, fw_ver, comps = CompetencyFrameworkService.create_new_version(
            uow=uow,
            actor=actor,
            framework_id=framework_id,
            expected_framework_version=payload.expected_framework_version,
            summary=payload.summary,
            behavioral_scale_guide=payload.behavioral_scale_guide,
            competencies=[
                CompetencyDraftSpec(
                    code=c.code,
                    name=c.name,
                    definition=c.definition,
                    observation_required=c.observation_required,
                    rubric_anchors=c.rubric_anchors,
                    display_order=c.display_order,
                )
                for c in payload.competencies
            ],
        )
        resp = _to_framework_detail(fw, fw_ver, comps)
        uow.commit()
        return resp


@router.get(
    "/competency-frameworks/{framework_id}/versions/{version_number}",
    response_model=CompetencyFrameworkDetailResponse,
    operation_id="getCompetencyFrameworkVersion",
)
def get_competency_framework_version(
    framework_id: UUID = Path(...),
    version_number: int = Path(..., ge=1),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> CompetencyFrameworkDetailResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        fw, fw_ver, comps = CompetencyFrameworkService.get_framework_version(
            uow, actor, framework_id, version_number=version_number
        )
        return _to_framework_detail(fw, fw_ver, comps)


@router.post(
    "/competency-frameworks/{framework_id}/versions/{version_number}/publish",
    response_model=CompetencyFrameworkDetailResponse,
    operation_id="publishCompetencyFrameworkVersion",
)
def publish_competency_framework_version(
    framework_id: UUID = Path(...),
    version_number: int = Path(..., ge=1),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> CompetencyFrameworkDetailResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        fw, fw_ver, comps = CompetencyFrameworkService.publish_version(
            uow, actor, framework_id, version_number
        )
        resp = _to_framework_detail(fw, fw_ver, comps)
        uow.commit()
        return resp


@router.put(
    "/interviews/{interview_id}/pack-context",
    response_model=InterviewContextResponse,
    operation_id="syncInterviewPackContext",
)
def sync_interview_pack_context(
    payload: SyncInterviewContextRequest,
    interview_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> InterviewContextResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        ctx, op_id = InterviewPackService.upsert_interview_context(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            application_id=payload.application_id,
            stage=payload.stage,
            seniority_level=payload.seniority_level,
            duration_minutes=payload.duration_minutes,
            schedule_revision=payload.schedule_revision,
            status=payload.status,
            scheduled_start_at=payload.scheduled_start_at,
            scheduled_end_at=payload.scheduled_end_at,
            timezone=payload.timezone,
            participant_user_ids=payload.participant_user_ids,
            booking_event_key=payload.booking_event_key,
        )
        resp = InterviewContextResponse(
            interview_id=ctx.id,
            application_id=ctx.application_id,
            job_id=ctx.job_id,
            candidate_id=ctx.candidate_id,
            stage=ctx.stage,
            seniority_level=ctx.seniority_level,
            duration_minutes=ctx.duration_minutes,
            schedule_revision=ctx.schedule_revision,
            status=ctx.status,
            timezone=ctx.timezone,
            participant_user_ids=[UUID(u) for u in (ctx.participant_user_ids or [])],
            admitted_operation_id=op_id,
        )
        uow.commit()
        return resp


@router.post(
    "/interviews/{interview_id}/pack-runs",
    response_model=PackRevisionResponse,
    status_code=201,
    operation_id="createInterviewPackRun",
)
def create_interview_pack_run(
    request: Request,
    payload: CreatePackRunRequest,
    interview_id: UUID = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> Any:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        idem = IdempotencyService()
        route_key = f"POST /api/v1/interviews/{interview_id}/pack-runs"
        req_hash = compute_request_hash(payload.model_dump(mode="json"))
        if idempotency_key and actor.tenant_id:
            cached = idem.check_idempotency(
                uow.session, actor.tenant_id, route_key, idempotency_key, req_hash
            )
            if cached is not None:
                return JSONResponse(
                    status_code=cached.response_status, content=cached.response_body
                )

        bundle = InterviewPackService.generate_draft_pack(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            reason=payload.reason,
            framework_version_id=payload.framework_version_id,
            ai_provider=_get_ai_provider(request),
            interview_port=_get_interview_port(request),
        )
        resp = _to_pack_response(bundle)
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
    "/interviews/{interview_id}/packs",
    response_model=list[PackRevisionResponse],
    operation_id="listInterviewPacks",
)
def list_interview_packs(
    request: Request,
    interview_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[PackRevisionResponse]:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        bundles = InterviewPackService.list_packs_for_interview(
            uow=uow,
            actor=actor,
            interview_id=interview_id,
            interview_port=_get_interview_port(request),
        )
        return [_to_pack_response(b) for b in bundles]


@router.get(
    "/pack-revisions/{pack_revision_id}",
    response_model=PackRevisionResponse,
    operation_id="getPackRevision",
)
def get_pack_revision(
    request: Request,
    pack_revision_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> PackRevisionResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        bundle = InterviewPackService.get_pack_revision(
            uow=uow,
            actor=actor,
            pack_revision_id=pack_revision_id,
            interview_port=_get_interview_port(request),
        )
        return _to_pack_response(bundle)


@router.patch(
    "/pack-revisions/{pack_revision_id}",
    response_model=PackRevisionResponse,
    operation_id="updatePackRevision",
)
def patch_pack_revision(
    request: Request,
    payload: PatchPackRevisionRequest,
    pack_revision_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> PackRevisionResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        bundle = InterviewPackService.update_pack_revision(
            uow=uow,
            actor=actor,
            pack_revision_id=pack_revision_id,
            expected_version=payload.expected_version,
            candidate_overview=payload.candidate_overview,
            interviewer_instructions=payload.interviewer_instructions,
            notes_guidance=payload.notes_guidance,
            claims=(
                [
                    PackClaimEditSpec(
                        section=c.section,
                        content_scope=c.content_scope,
                        statement=c.statement,
                        classification=c.classification,
                        fact_revision_id=c.fact_revision_id,
                        evidence_fragment_ids=c.evidence_fragment_ids,
                        source_note=c.source_note,
                    )
                    for c in payload.claims
                ]
                if payload.claims is not None
                else None
            ),
            questions=(
                [
                    PackQuestionEditSpec(
                        category=q.category,
                        content_scope=q.content_scope,
                        premise_classification=q.premise_classification,
                        competency_id=q.competency_id,
                        allocated_minutes=q.allocated_minutes,
                        prompt_text=q.prompt_text,
                        premise_text=q.premise_text,
                        expected_signals=q.expected_signals,
                        fact_revision_id=q.fact_revision_id,
                        evidence_fragment_ids=q.evidence_fragment_ids,
                    )
                    for q in payload.questions
                ]
                if payload.questions is not None
                else None
            ),
            interview_port=_get_interview_port(request),
        )
        resp = _to_pack_response(bundle)
        uow.commit()
        return resp


@router.post(
    "/pack-revisions/{pack_revision_id}/publish",
    response_model=PackRevisionResponse,
    operation_id="publishPackRevision",
)
def publish_pack_revision(
    request: Request,
    payload: PublishPackRevisionRequest,
    pack_revision_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> PackRevisionResponse:
    with UnitOfWork(session=db, tenant_id=actor.tenant_id, subject_id=actor.user_id) as uow:
        bundle = InterviewPackService.publish_pack_revision(
            uow=uow,
            actor=actor,
            pack_revision_id=pack_revision_id,
            expected_version=payload.expected_version,
            review_notes=payload.review_notes,
            interview_port=_get_interview_port(request),
        )
        resp = _to_pack_response(bundle)
        uow.commit()
        return resp
