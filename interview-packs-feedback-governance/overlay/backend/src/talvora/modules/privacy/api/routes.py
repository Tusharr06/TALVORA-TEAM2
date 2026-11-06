"""FastAPI Routes for Organization Policy, Retention Holds, Privacy Purge & S3 Tombstones (P17)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_current_actor,
    get_db_session,
)
from talvora.modules.privacy.api.dto import (
    ApprovePrivacyRequestDTO,
    CreatePrivacyRequestDTO,
    CreateRetentionHoldRequest,
    DeletionLedgerResponse,
    OrganizationPolicyResponse,
    PatchOrganizationPolicyRequest,
    PrivacyRequestResponse,
    ReleaseRetentionHoldRequest,
    RetentionHoldResponse,
    TombstoneReplayResponse,
    VerifyPrivacyRequestDTO,
)
from talvora.modules.privacy.application.service import (
    OrganizationPolicyService,
    PrivacyLifecycleService,
    RetentionHoldService,
    _require_active_member,
)
from talvora.modules.privacy.domain.models import (
    RESIDUAL_EXTERNAL_ARTIFACTS_DISCLOSURE,
    RETENTION_JURISDICTION_DISCLOSURE,
    DeletionLedger,
    OrganizationPolicy,
    PrivacyRequest,
    RetentionHold,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.domain.clock import Clock
from talvora.shared.infrastructure.idempotency import IdempotencyService, compute_request_hash
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

router = APIRouter(prefix="/api/v1", tags=["Governance — Privacy & Retention"])


def _get_clock(request: Request) -> Clock | None:
    return getattr(request.app.state, "clock", None)


def _get_storage(request: Request) -> ObjectStoragePort:
    storage: ObjectStoragePort = request.app.state.object_storage
    return storage


def _to_policy_response(
    policy: OrganizationPolicy, canceled_ops: int = 0
) -> OrganizationPolicyResponse:
    return OrganizationPolicyResponse(
        tenant_id=policy.tenant_id,
        ai_processing_enabled=policy.ai_processing_enabled,
        ai_interview_packs_enabled=policy.ai_interview_packs_enabled,
        approved_ai_models=list(policy.approved_ai_models or []),
        monthly_ai_token_budget=policy.monthly_ai_token_budget,
        outbound_communications_enabled=policy.outbound_communications_enabled,
        reveal_peer_feedback_after_submit=policy.reveal_peer_feedback_after_submit,
        allow_blind_feedback_override=policy.allow_blind_feedback_override,
        export_ttl_hours=policy.export_ttl_hours,
        candidate_retention_days=policy.candidate_retention_days,
        audit_retention_days=policy.audit_retention_days,
        backup_expiry_days=policy.backup_expiry_days,
        min_analytics_cohort_size=policy.min_analytics_cohort_size,
        version=policy.version,
        updated_by=policy.updated_by,
        updated_at=policy.updated_at,
        canceled_queued_operations=canceled_ops,
        jurisdiction_disclosure=RETENTION_JURISDICTION_DISCLOSURE,
    )


def _to_hold_response(h: RetentionHold) -> RetentionHoldResponse:
    return RetentionHoldResponse(
        id=h.id,
        tenant_id=h.tenant_id,
        candidate_id=h.candidate_id,
        application_id=h.application_id,
        scope=h.scope,
        status=h.status,
        reason=h.reason,
        issued_by=h.issued_by,
        issued_at=h.issued_at,
        review_at=h.review_at,
        expires_at=h.expires_at,
        released_by=h.released_by,
        released_at=h.released_at,
        release_reason=h.release_reason,
        version=h.version,
    )


def _to_privacy_response(
    req: PrivacyRequest, ledger: DeletionLedger | None = None
) -> PrivacyRequestResponse:
    return PrivacyRequestResponse(
        id=req.id,
        tenant_id=req.tenant_id,
        candidate_id=req.candidate_id,
        subject_hash=req.subject_hash,
        request_type=req.request_type,
        status=req.status,
        verification_status=req.verification_status,
        verified_contact_revision_id=req.verified_contact_revision_id,
        verified_by=req.verified_by,
        verified_at=req.verified_at,
        approved_by=req.approved_by,
        approved_at=req.approved_at,
        override_active_applications=req.override_active_applications,
        retention_preview=dict(req.retention_preview or {}),
        residual_review_items=list(req.residual_review_items or []),
        last_error=req.last_error,
        requested_by=req.requested_by,
        request_reason=req.request_reason,
        completed_at=req.completed_at,
        version=req.version,
        created_at=req.created_at,
        updated_at=req.updated_at,
        residual_disclosure=RESIDUAL_EXTERNAL_ARTIFACTS_DISCLOSURE,
        deletion_ledger=(
            DeletionLedgerResponse(
                id=ledger.id,
                tenant_id=ledger.tenant_id,
                privacy_request_id=ledger.privacy_request_id,
                candidate_id=ledger.candidate_id,
                subject_hash=ledger.subject_hash,
                scope_summary=dict(ledger.scope_summary or {}),
                s3_tombstone_bucket=ledger.s3_tombstone_bucket,
                s3_tombstone_key=ledger.s3_tombstone_key,
                s3_tombstone_sha256=ledger.s3_tombstone_sha256,
                purged_at=ledger.purged_at,
                backup_window_expires_at=ledger.backup_window_expires_at,
                replayed_after_restore=ledger.replayed_after_restore,
            )
            if ledger is not None
            else None
        ),
    )


@router.get(
    "/organization-policy",
    response_model=OrganizationPolicyResponse,
    operation_id="getOrganizationPolicy",
)
def get_organization_policy(
    request: Request,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> OrganizationPolicyResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        policy = OrganizationPolicyService.get_or_create_policy(uow, actor)
        resp = _to_policy_response(policy)
        uow.commit()
        return resp


@router.patch(
    "/organization-policy",
    response_model=OrganizationPolicyResponse,
    operation_id="updateOrganizationPolicy",
)
def patch_organization_policy(
    request: Request,
    payload: PatchOrganizationPolicyRequest,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> OrganizationPolicyResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        updates = payload.model_dump(exclude={"expected_version", "reason"}, exclude_none=True)
        policy, canceled = OrganizationPolicyService.update_policy(
            uow=uow,
            actor=actor,
            expected_version=payload.expected_version,
            reason=payload.reason,
            updates=updates,
        )
        resp = _to_policy_response(policy, canceled_ops=canceled)
        uow.commit()
        return resp


@router.post(
    "/retention-holds",
    response_model=RetentionHoldResponse,
    status_code=201,
    operation_id="createRetentionHold",
)
def create_retention_hold(
    request: Request,
    payload: CreateRetentionHoldRequest,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> RetentionHoldResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        hold = RetentionHoldService.create_hold(
            uow=uow,
            actor=actor,
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            scope=payload.scope,
            reason=payload.reason,
            review_at=payload.review_at,
            expires_at=payload.expires_at,
        )
        resp = _to_hold_response(hold)
        uow.commit()
        return resp


@router.get(
    "/retention-holds",
    response_model=list[RetentionHoldResponse],
    operation_id="listRetentionHolds",
)
def list_retention_holds(
    request: Request,
    candidate_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[RetentionHoldResponse]:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        holds = RetentionHoldService.list_holds(
            uow=uow, actor=actor, candidate_id=candidate_id, status=status
        )
        return [_to_hold_response(h) for h in holds]


@router.post(
    "/retention-holds/{hold_id}/release",
    response_model=RetentionHoldResponse,
    operation_id="releaseRetentionHold",
)
def release_retention_hold(
    request: Request,
    payload: ReleaseRetentionHoldRequest,
    hold_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> RetentionHoldResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        hold = RetentionHoldService.release_hold(
            uow=uow,
            actor=actor,
            hold_id=hold_id,
            expected_version=payload.expected_version,
            release_reason=payload.release_reason,
        )
        resp = _to_hold_response(hold)
        uow.commit()
        return resp


@router.post(
    "/privacy-requests",
    response_model=PrivacyRequestResponse,
    status_code=201,
    operation_id="createPrivacyRequest",
)
def create_privacy_request(
    request: Request,
    payload: CreatePrivacyRequestDTO,
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
        route_key = "POST /api/v1/privacy-requests"
        req_hash = compute_request_hash(payload.model_dump(mode="json"))
        if idempotency_key and actor.tenant_id:
            cached = idem.check_idempotency(
                uow.session, actor.tenant_id, route_key, idempotency_key, req_hash
            )
            if cached is not None:
                return JSONResponse(
                    status_code=cached.response_status, content=cached.response_body
                )

        req = PrivacyLifecycleService.create_privacy_request(
            uow=uow,
            actor=actor,
            candidate_id=payload.candidate_id,
            request_type=payload.request_type,
            request_reason=payload.request_reason,
        )
        resp = _to_privacy_response(req)
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
    "/privacy-requests",
    response_model=list[PrivacyRequestResponse],
    operation_id="listPrivacyRequests",
)
def list_privacy_requests(
    request: Request,
    status: str | None = Query(default=None),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[PrivacyRequestResponse]:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        tenant_id, _, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={"OWNER", "ADMIN", "RECRUITER", "HIRING_MANAGER"},
        )
        stmt = select(PrivacyRequest).where(PrivacyRequest.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(PrivacyRequest.status == status.strip().upper())
        stmt = stmt.order_by(PrivacyRequest.created_at.desc()).limit(200)
        rows = list(uow.session.scalars(stmt).all())
        return [_to_privacy_response(r) for r in rows]


@router.get(
    "/privacy-requests/{request_id}",
    response_model=PrivacyRequestResponse,
    operation_id="getPrivacyRequest",
)
def get_privacy_request(
    request: Request,
    request_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> PrivacyRequestResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        tenant_id, _, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={"OWNER", "ADMIN", "RECRUITER", "HIRING_MANAGER"},
        )
        req = uow.session.scalar(
            select(PrivacyRequest).where(
                PrivacyRequest.tenant_id == tenant_id,
                PrivacyRequest.id == request_id,
            )
        )
        if req is None:
            raise ProblemException(
                status_code=404,
                title="Privacy Request Not Found",
                code="NOT_FOUND",
                detail="Privacy request was not found.",
            )
        ledger = uow.session.scalar(
            select(DeletionLedger).where(
                DeletionLedger.tenant_id == tenant_id,
                DeletionLedger.candidate_id == req.candidate_id,
            )
        )
        return _to_privacy_response(req, ledger)


@router.get(
    "/privacy-requests/{request_id}/preview",
    response_model=dict[str, Any],
    operation_id="previewPrivacyRetentionImpact",
)
def preview_privacy_retention(
    request: Request,
    request_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        tenant_id, _, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={"OWNER", "ADMIN", "RECRUITER", "HIRING_MANAGER"},
        )
        req = uow.session.scalar(
            select(PrivacyRequest).where(
                PrivacyRequest.tenant_id == tenant_id,
                PrivacyRequest.id == request_id,
            )
        )
        if req is None:
            raise ProblemException(
                status_code=404,
                title="Privacy Request Not Found",
                code="NOT_FOUND",
                detail="Privacy request was not found.",
            )
        return PrivacyLifecycleService.build_retention_preview(uow, tenant_id, req.candidate_id)


@router.post(
    "/privacy-requests/{request_id}/verify",
    response_model=PrivacyRequestResponse,
    operation_id="verifyPrivacyRequest",
)
def verify_privacy_request(
    request: Request,
    payload: VerifyPrivacyRequestDTO,
    request_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> PrivacyRequestResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        req = PrivacyLifecycleService.verify_privacy_request(
            uow=uow,
            actor=actor,
            request_id=request_id,
            expected_version=payload.expected_version,
            verification_method=payload.verification_method,
            verification_notes=payload.verification_notes,
        )
        resp = _to_privacy_response(req)
        uow.commit()
        return resp


@router.post(
    "/privacy-requests/{request_id}/approve",
    response_model=PrivacyRequestResponse,
    operation_id="approvePrivacyRequest",
)
def approve_privacy_request(
    request: Request,
    payload: ApprovePrivacyRequestDTO,
    request_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> PrivacyRequestResponse:
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        req = PrivacyLifecycleService.approve_privacy_request(
            uow=uow,
            actor=actor,
            request_id=request_id,
            expected_version=payload.expected_version,
            approval_notes=payload.approval_notes,
            override_active_applications=payload.override_active_applications,
        )
        resp = _to_privacy_response(req)
        uow.commit()
        return resp


@router.post(
    "/privacy-requests/{request_id}/execute",
    response_model=PrivacyRequestResponse,
    operation_id="executePrivacyPurge",
)
def execute_privacy_purge(
    request: Request,
    request_id: UUID = Path(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> PrivacyRequestResponse:
    settings = request.app.state.settings
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        req, ledger = PrivacyLifecycleService.execute_privacy_purge(
            uow=uow,
            actor=actor,
            request_id=request_id,
            storage=_get_storage(request),
            quarantine_bucket=settings.s3_bucket_name,
            clean_bucket=settings.s3_clean_bucket_name,
            encryption_key=settings.encryption_key,
        )
        resp = _to_privacy_response(req, ledger)
        uow.commit()
        return resp


@router.post(
    "/privacy/tombstones/replay",
    response_model=TombstoneReplayResponse,
    operation_id="replayRestoredTombstoneLedger",
)
def replay_restored_tombstone_ledger(
    request: Request,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    _csrf: None = Depends(enforce_csrf_and_origin),
) -> TombstoneReplayResponse:
    settings = request.app.state.settings
    with UnitOfWork(
        session=db,
        tenant_id=actor.tenant_id,
        subject_id=actor.user_id,
        clock=_get_clock(request),
    ) as uow:
        result = PrivacyLifecycleService.replay_s3_tombstones_after_restore(
            uow=uow,
            actor=actor,
            storage=_get_storage(request),
            tombstone_bucket=settings.s3_clean_bucket_name,
            encryption_key=settings.encryption_key,
        )
        uow.commit()
        return TombstoneReplayResponse.model_validate(result)
