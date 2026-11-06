"""Application Services for Organization Policy, Retention Holds, Privacy Purge & Restore Replay (P17)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import delete, select
from sqlalchemy import inspect as sa_inspect

from talvora.modules.applications.domain.models import (
    TERMINAL_STAGES,
    Application,
    StageEvent,
)
from talvora.modules.candidates.domain.models import (
    Candidate,
    CandidateContactEvent,
    CandidateContactRevision,
    CandidateContactVerification,
)
from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.documents.domain.models import (
    ApplicationDocument,
    Document,
    DocumentVersion,
    ObjectReceipt,
    UploadItem,
)
from talvora.modules.evidence.domain.models import (
    EvidenceFragment,
    ExtractionRun,
    SourceSegment,
)
from talvora.modules.facts.domain.models import (
    CandidateFactEvidence,
    CandidateFactRevision,
)
from talvora.modules.feedback.domain.models import (
    CompetencyRating,
    ExportArtifact,
    Feedback,
    FeedbackRevision,
)
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.interview_packs.domain.models import (
    InterviewPackContext,
    PackClaim,
    PackQuestion,
    PackReview,
    PackRevision,
)
from talvora.modules.operations.domain.models import (
    JobOperation,
    OperationStatus,
    OutboxEvent,
    OutboxStatus,
)
from talvora.modules.organizations.domain.models import Membership
from talvora.modules.privacy.application.tombstone_storage import (
    PrivacyStoragePurgeError,
    TombstonePayload,
    list_and_decrypt_s3_tombstones,
    purge_all_object_versions,
    write_encrypted_s3_tombstone,
)
from talvora.modules.privacy.domain.models import (
    RESIDUAL_EXTERNAL_ARTIFACTS_DISCLOSURE,
    DeletionLedger,
    HoldScope,
    HoldStatus,
    OrganizationPolicy,
    OrganizationPolicyRevision,
    PrivacyRequest,
    PrivacyRequestStatus,
    PrivacyRequestType,
    PrivacyVerificationStatus,
    RetentionHold,
    compute_opaque_subject_hash,
)
from talvora.modules.screening.domain.models import (
    ScreeningOverride,
    ScreeningResult,
    ScreeningResultFact,
    ScreeningRun,
)
from talvora.modules.summaries.domain.models import (
    CandidateSummaryCitation,
    CandidateSummaryClaim,
    CandidateSummaryRevision,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

_AI_OPERATION_KEYWORDS = ("PACK_GENERATE", "SUMMARY", "AI_")
_OUTBOUND_OPERATION_KEYWORDS = ("EMAIL", "MAIL", "CALENDAR", "INVITE", "NOTIFICATION", "EXTERNAL")


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _require_active_member(
    uow: UnitOfWork,
    actor: ActorContext,
    allowed_roles: set[str] | None = None,
    required_capability: str | None = None,
) -> tuple[UUID, UUID, Membership]:
    if not actor.is_authenticated or actor.user_id is None:
        raise ProblemException(
            status_code=401,
            title="Unauthorized",
            code="AUTH_REQUIRED",
            detail="Authenticated session is required.",
        )
    if actor.tenant_id is None:
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail="Active organization context is required.",
        )
    membership = uow.session.scalar(
        select(Membership).where(
            Membership.tenant_id == actor.tenant_id,
            Membership.user_id == actor.user_id,
        )
    )
    if membership is None or membership.status != "ACTIVE":
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail="Active organization membership is required.",
        )
    if allowed_roles is not None and membership.role not in allowed_roles:
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail=f"Role '{membership.role}' is not permitted for this governance command.",
        )
    if required_capability is not None and not actor.has_capability(required_capability):
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail=f"Missing required capability '{required_capability}'.",
        )
    return actor.tenant_id, actor.user_id, membership


class OrganizationPolicyService:
    """Manages versioned tenant governance policy and immediate queue cancellation side-effects."""

    @classmethod
    def get_or_create_policy(cls, uow: UnitOfWork, actor: ActorContext) -> OrganizationPolicy:
        tenant_id, user_id, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={
                Role.OWNER.value,
                Role.ADMIN.value,
                Role.RECRUITER.value,
                Role.HIRING_MANAGER.value,
            },
        )
        policy = uow.session.scalar(
            select(OrganizationPolicy).where(OrganizationPolicy.tenant_id == tenant_id)
        )
        if policy is None:
            now = uow.clock.now()
            policy = OrganizationPolicy(
                tenant_id=tenant_id,
                ai_processing_enabled=False,
                ai_interview_packs_enabled=True,
                approved_ai_models=[],
                monthly_ai_token_budget=1_000_000,
                outbound_communications_enabled=False,
                reveal_peer_feedback_after_submit=False,
                allow_blind_feedback_override=False,
                export_ttl_hours=24,
                candidate_retention_days=365,
                audit_retention_days=730,
                backup_expiry_days=35,
                min_analytics_cohort_size=5,
                version=1,
                updated_by=user_id,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(policy)
            uow.session.flush()
        return policy

    @classmethod
    def update_policy(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        expected_version: int,
        reason: str,
        updates: dict[str, Any],
    ) -> tuple[OrganizationPolicy, int]:
        """Updates policy (OWNER/ADMIN), appends immutable revision, and cancels disallowed queued jobs."""
        tenant_id, user_id, membership = _require_active_member(
            uow,
            actor,
            allowed_roles={Role.OWNER.value, Role.ADMIN.value},
        )
        policy = cls.get_or_create_policy(uow, actor)
        if policy.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=(
                    f"Expected organization_policy version {expected_version}, "
                    f"found {policy.version}."
                ),
            )

        now = uow.clock.now()
        mutable_fields = {
            "ai_processing_enabled",
            "ai_interview_packs_enabled",
            "approved_ai_models",
            "monthly_ai_token_budget",
            "outbound_communications_enabled",
            "reveal_peer_feedback_after_submit",
            "allow_blind_feedback_override",
            "export_ttl_hours",
            "candidate_retention_days",
            "audit_retention_days",
            "backup_expiry_days",
            "min_analytics_cohort_size",
        }

        for k, val in updates.items():
            if k in mutable_fields and val is not None:
                if k == "export_ttl_hours" and not (1 <= int(val) <= 168):
                    raise ProblemException(
                        status_code=422,
                        title="Invalid Export TTL",
                        code="VALIDATION_ERROR",
                        detail="export_ttl_hours must be between 1 and 168.",
                    )
                if k == "min_analytics_cohort_size" and not (3 <= int(val) <= 100):
                    raise ProblemException(
                        status_code=422,
                        title="Invalid Minimum Cohort Size",
                        code="VALIDATION_ERROR",
                        detail="min_analytics_cohort_size must be between 3 and 100.",
                    )
                if k == "candidate_retention_days" and not (1 <= int(val) <= 3650):
                    raise ProblemException(
                        status_code=422,
                        title="Invalid Candidate Retention Window",
                        code="VALIDATION_ERROR",
                        detail="candidate_retention_days must be between 1 and 3650.",
                    )
                if k == "backup_expiry_days" and not (1 <= int(val) <= 365):
                    raise ProblemException(
                        status_code=422,
                        title="Invalid Backup Expiry Window",
                        code="VALIDATION_ERROR",
                        detail="backup_expiry_days must be between 1 and 365.",
                    )
                setattr(policy, k, val)

        policy.version += 1
        policy.updated_by = user_id
        policy.updated_at = now

        # Immediate side-effect: cancel queued AI / outbound jobs if disabled by policy
        canceled_ops_count = 0
        if not policy.ai_processing_enabled or not policy.outbound_communications_enabled:
            pending_ops = list(
                uow.session.scalars(
                    select(JobOperation).where(
                        JobOperation.tenant_id == tenant_id,
                        JobOperation.status.in_(
                            [OperationStatus.PENDING.value, OperationStatus.RETRY_WAIT.value]
                        ),
                    )
                ).all()
            )
            canceled_op_ids: list[UUID] = []
            for op in pending_ops:
                upper_type = op.operation_type.upper()
                kill_ai = not policy.ai_processing_enabled and any(
                    kw in upper_type for kw in _AI_OPERATION_KEYWORDS
                )
                kill_outbound = not policy.outbound_communications_enabled and any(
                    kw in upper_type for kw in _OUTBOUND_OPERATION_KEYWORDS
                )
                if kill_ai or kill_outbound:
                    op.status = OperationStatus.CANCELED.value
                    op.error_detail = {"code": "CANCELED_BY_ORG_POLICY", "reason": reason[:200]}
                    op.updated_at = now
                    op.version += 1
                    canceled_ops_count += 1
                    canceled_op_ids.append(op.id)

            if canceled_op_ids:
                outbox_rows = list(
                    uow.session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.tenant_id == tenant_id,
                            OutboxEvent.payload_ref.in_(canceled_op_ids),
                            OutboxEvent.status == OutboxStatus.PENDING.value,
                        )
                    ).all()
                )
                for ob in outbox_rows:
                    ob.status = OutboxStatus.FAILED.value

                # Also cancel pending external_action approvals if P13 table exists
                tables = set(sa_inspect(uow.session.connection()).get_table_names())
                if not policy.outbound_communications_enabled and "external_action" in tables:
                    uow.session.execute(
                        sa.text(
                            "UPDATE external_action SET status = 'CANCELED' "
                            "WHERE tenant_id = :tid AND status IN ('DRAFT', 'AWAITING_APPROVAL', 'APPROVED')"
                        ),
                        {"tid": str(tenant_id)},
                    )

        snapshot = {
            "ai_processing_enabled": policy.ai_processing_enabled,
            "approved_ai_models": list(policy.approved_ai_models or []),
            "monthly_ai_token_budget": policy.monthly_ai_token_budget,
            "outbound_communications_enabled": policy.outbound_communications_enabled,
            "reveal_peer_feedback_after_submit": policy.reveal_peer_feedback_after_submit,
            "allow_blind_feedback_override": policy.allow_blind_feedback_override,
            "export_ttl_hours": policy.export_ttl_hours,
            "candidate_retention_days": policy.candidate_retention_days,
            "audit_retention_days": policy.audit_retention_days,
            "backup_expiry_days": policy.backup_expiry_days,
            "min_analytics_cohort_size": policy.min_analytics_cohort_size,
            "version": policy.version,
        }
        rev = OrganizationPolicyRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            revision_number=policy.version,
            reason=reason.strip(),
            policy_snapshot=snapshot,
            changed_by=user_id,
            created_at=now,
        )
        uow.session.add(rev)

        uow.record_audit(
            resource_type="organization_policy",
            action="ORGANIZATION_POLICY_UPDATED",
            resource_id=tenant_id,
            actor_id=user_id,
            actor_capability=(
                Capability.MANAGE_ORGANIZATION_POLICY.value
                if actor.has_capability(Capability.MANAGE_ORGANIZATION_POLICY.value)
                else membership.role
            ),
            details={
                "new_version": policy.version,
                "canceled_queued_operations": canceled_ops_count,
                "ai_processing_enabled": policy.ai_processing_enabled,
                "outbound_communications_enabled": policy.outbound_communications_enabled,
            },
        )
        uow.session.flush()
        return policy, canceled_ops_count


class RetentionHoldService:
    """Issues, lists, and releases legal/compliance retention holds."""

    @classmethod
    def create_hold(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        candidate_id: UUID,
        scope: str,
        reason: str,
        review_at: datetime,
        application_id: UUID | None = None,
        expires_at: datetime | None = None,
    ) -> RetentionHold:
        tenant_id, user_id, membership = _require_active_member(
            uow, actor, allowed_roles={Role.OWNER.value, Role.ADMIN.value}
        )
        cand = uow.session.scalar(
            select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.id == candidate_id)
        )
        if cand is None:
            raise ProblemException(
                status_code=404,
                title="Candidate Not Found",
                code="NOT_FOUND",
                detail="Candidate was not found in this organization.",
            )
        norm_scope = scope.strip().upper()
        if norm_scope not in {HoldScope.CANDIDATE.value, HoldScope.APPLICATION.value}:
            raise ProblemException(
                status_code=422,
                title="Invalid Hold Scope",
                code="VALIDATION_ERROR",
                detail="Hold scope must be CANDIDATE or APPLICATION.",
            )
        if norm_scope == HoldScope.APPLICATION.value and application_id is None:
            raise ProblemException(
                status_code=422,
                title="Application Required",
                code="VALIDATION_ERROR",
                detail="application_id is required when hold scope is APPLICATION.",
            )

        now = uow.clock.now()
        hold = RetentionHold(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            application_id=application_id,
            scope=norm_scope,
            status=HoldStatus.ACTIVE.value,
            reason=reason.strip(),
            issued_by=user_id,
            issued_at=now,
            review_at=_ensure_utc(review_at),
            expires_at=_ensure_utc(expires_at) if expires_at else None,
            version=1,
        )
        uow.session.add(hold)
        uow.record_audit(
            resource_type="retention_hold",
            action="RETENTION_HOLD_ISSUED",
            resource_id=hold.id,
            actor_id=user_id,
            actor_capability=membership.role,
            details={
                "candidate_id": str(candidate_id),
                "scope": norm_scope,
                "review_at": hold.review_at.isoformat(),
            },
        )
        uow.session.flush()
        return hold

    @classmethod
    def list_holds(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        candidate_id: UUID | None = None,
        status: str | None = None,
    ) -> list[RetentionHold]:
        tenant_id, _, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={
                Role.OWNER.value,
                Role.ADMIN.value,
                Role.RECRUITER.value,
                Role.HIRING_MANAGER.value,
            },
        )
        stmt = select(RetentionHold).where(RetentionHold.tenant_id == tenant_id)
        if candidate_id is not None:
            stmt = stmt.where(RetentionHold.candidate_id == candidate_id)
        if status is not None:
            stmt = stmt.where(RetentionHold.status == status.strip().upper())
        stmt = stmt.order_by(RetentionHold.issued_at.desc())
        return list(uow.session.scalars(stmt).all())

    @classmethod
    def release_hold(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        hold_id: UUID,
        expected_version: int,
        release_reason: str,
    ) -> RetentionHold:
        tenant_id, user_id, membership = _require_active_member(
            uow, actor, allowed_roles={Role.OWNER.value, Role.ADMIN.value}
        )
        hold = uow.session.scalar(
            select(RetentionHold).where(
                RetentionHold.tenant_id == tenant_id, RetentionHold.id == hold_id
            )
        )
        if hold is None:
            raise ProblemException(
                status_code=404,
                title="Retention Hold Not Found",
                code="NOT_FOUND",
                detail="Retention hold was not found.",
            )
        if hold.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Expected hold version {expected_version}, found {hold.version}.",
            )
        if hold.status != HoldStatus.ACTIVE.value:
            raise ProblemException(
                status_code=409,
                title="Hold Not Active",
                code="CONFLICT",
                detail=f"Retention hold is already in state '{hold.status}'.",
            )
        now = uow.clock.now()
        hold.status = HoldStatus.RELEASED.value
        hold.released_by = user_id
        hold.released_at = now
        hold.release_reason = release_reason.strip()
        hold.version += 1

        uow.record_audit(
            resource_type="retention_hold",
            action="RETENTION_HOLD_RELEASED",
            resource_id=hold.id,
            actor_id=user_id,
            actor_capability=membership.role,
            details={"candidate_id": str(hold.candidate_id)},
        )
        uow.session.flush()
        return hold

    @classmethod
    def active_holds_for_candidate(
        cls, uow: UnitOfWork, tenant_id: UUID, candidate_id: UUID
    ) -> list[RetentionHold]:
        now = _ensure_utc(uow.clock.now())
        holds = list(
            uow.session.scalars(
                select(RetentionHold).where(
                    RetentionHold.tenant_id == tenant_id,
                    RetentionHold.candidate_id == candidate_id,
                    RetentionHold.status == HoldStatus.ACTIVE.value,
                )
            ).all()
        )
        still_active: list[RetentionHold] = []
        for h in holds:
            if h.expires_at is not None and _ensure_utc(h.expires_at) <= now:
                h.status = HoldStatus.EXPIRED.value
                h.version += 1
            else:
                still_active.append(h)
        return still_active


class PrivacyLifecycleService:
    """Verified candidate privacy requests, retention previews, durable purges & S3 tombstone replay."""

    @classmethod
    def build_retention_preview(
        cls, uow: UnitOfWork, tenant_id: UUID, candidate_id: UUID
    ) -> dict[str, Any]:
        cand = uow.session.scalar(
            select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.id == candidate_id)
        )
        apps = list(
            uow.session.scalars(
                select(Application).where(
                    Application.tenant_id == tenant_id,
                    Application.candidate_id == candidate_id,
                )
            ).all()
        )
        app_ids = [a.id for a in apps]
        active_apps = [a for a in apps if a.stage not in TERMINAL_STAGES]

        docs = list(
            uow.session.scalars(
                select(Document).where(
                    Document.tenant_id == tenant_id,
                    Document.candidate_id == candidate_id,
                )
            ).all()
        )
        doc_ids = [d.id for d in docs]
        doc_versions = (
            list(
                uow.session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id,
                        DocumentVersion.document_id.in_(doc_ids),
                    )
                ).all()
            )
            if doc_ids
            else []
        )
        exports = list(
            uow.session.scalars(
                select(ExportArtifact).where(
                    ExportArtifact.tenant_id == tenant_id,
                    ExportArtifact.candidate_id == candidate_id,
                )
            ).all()
        )
        facts_cnt = int(
            uow.session.scalar(
                select(sa.func.count(CandidateFactRevision.id)).where(
                    CandidateFactRevision.tenant_id == tenant_id,
                    CandidateFactRevision.candidate_id == candidate_id,
                )
            )
            or 0
        )
        summaries_cnt = (
            int(
                uow.session.scalar(
                    select(sa.func.count(CandidateSummaryRevision.id)).where(
                        CandidateSummaryRevision.tenant_id == tenant_id,
                        CandidateSummaryRevision.application_id.in_(app_ids),
                    )
                )
                or 0
            )
            if app_ids
            else 0
        )
        packs_cnt = int(
            uow.session.scalar(
                select(sa.func.count(PackRevision.id)).where(
                    PackRevision.tenant_id == tenant_id,
                    PackRevision.candidate_id == candidate_id,
                )
            )
            or 0
        )
        feedbacks_cnt = int(
            uow.session.scalar(
                select(sa.func.count(Feedback.id)).where(
                    Feedback.tenant_id == tenant_id,
                    Feedback.candidate_id == candidate_id,
                )
            )
            or 0
        )
        active_holds = RetentionHoldService.active_holds_for_candidate(uow, tenant_id, candidate_id)

        # Residual external side-effects that require human follow-up
        residuals: list[dict[str, Any]] = []
        if exports:
            residuals.append(
                {
                    "residual_type": "OFFLINE_EXPORT_COPIES",
                    "count": len(exports),
                    "action_required": RESIDUAL_EXTERNAL_ARTIFACTS_DISCLOSURE,
                }
            )
        tables = set(sa_inspect(uow.session.connection()).get_table_names())
        if "interview" in tables and app_ids:
            booked_cnt = int(
                uow.session.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM interview "
                        "WHERE tenant_id = :tid AND status IN ('BOOKED', 'CHANGE_PENDING')"
                    ),
                    {"tid": str(tenant_id)},
                ).scalar()
                or 0
            )
            if booked_cnt > 0:
                residuals.append(
                    {
                        "residual_type": "EXTERNAL_CALENDAR_EVENTS",
                        "count": booked_cnt,
                        "action_required": "Authorized organizer must cancel or redact external Google Calendar invite.",
                    }
                )

        return {
            "candidate_present": cand is not None,
            "is_contact_verified": bool(cand.is_contact_verified) if cand else False,
            "applications_total": len(apps),
            "active_nonterminal_applications": len(active_apps),
            "active_holds_count": len(active_holds),
            "documents_count": len(docs),
            "document_versions_count": len(doc_versions),
            "facts_count": facts_cnt,
            "summaries_count": summaries_cnt,
            "packs_count": packs_cnt,
            "feedback_records_count": feedbacks_cnt,
            "export_artifacts_count": len(exports),
            "eligible_for_immediate_purge": len(active_holds) == 0 and len(active_apps) == 0,
            "residual_review_items": residuals,
        }

    @classmethod
    def create_privacy_request(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        candidate_id: UUID,
        request_type: str,
        request_reason: str,
    ) -> PrivacyRequest:
        tenant_id, user_id, membership = _require_active_member(
            uow,
            actor,
            allowed_roles={
                Role.OWNER.value,
                Role.ADMIN.value,
                Role.RECRUITER.value,
                Role.HIRING_MANAGER.value,
            },
        )
        norm_type = request_type.strip().upper()
        if norm_type not in {m.value for m in PrivacyRequestType}:
            raise ProblemException(
                status_code=422,
                title="Invalid Privacy Request Type",
                code="VALIDATION_ERROR",
                detail="request_type must be ERASURE, EXPORT_PORTABILITY, or RETENTION_SWEEP.",
            )
        if norm_type == PrivacyRequestType.RETENTION_SWEEP.value and membership.role not in {
            Role.OWNER.value,
            Role.ADMIN.value,
        }:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Only OWNER or ADMIN may initiate an internal RETENTION_SWEEP request.",
            )

        cand = uow.session.scalar(
            select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.id == candidate_id)
        )
        if cand is None:
            raise ProblemException(
                status_code=404,
                title="Candidate Not Found",
                code="NOT_FOUND",
                detail="Candidate was not found in this organization.",
            )

        preview = cls.build_retention_preview(uow, tenant_id, candidate_id)
        now = uow.clock.now()

        is_internal_sweep = norm_type == PrivacyRequestType.RETENTION_SWEEP.value
        req = PrivacyRequest(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            subject_hash=compute_opaque_subject_hash(tenant_id, candidate_id),
            request_type=norm_type,
            status=(
                PrivacyRequestStatus.VERIFIED.value
                if is_internal_sweep
                else PrivacyRequestStatus.PENDING_VERIFICATION.value
            ),
            verification_status=(
                PrivacyVerificationStatus.INTERNAL_RETENTION_POLICY.value
                if is_internal_sweep
                else PrivacyVerificationStatus.UNVERIFIED.value
            ),
            verified_by=user_id if is_internal_sweep else None,
            verified_at=now if is_internal_sweep else None,
            retention_preview=preview,
            residual_review_items=list(preview["residual_review_items"]),
            requested_by=user_id,
            request_reason=request_reason.strip(),
            version=1,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(req)
        uow.record_audit(
            resource_type="privacy_request",
            action="PRIVACY_REQUEST_CREATED",
            resource_id=req.id,
            actor_id=user_id,
            actor_capability=membership.role,
            details={
                "request_type": norm_type,
                "subject_hash": req.subject_hash,
                "verification_status": req.verification_status,
            },
        )
        uow.session.flush()
        return req

    @classmethod
    def verify_privacy_request(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        request_id: UUID,
        expected_version: int,
        verification_method: str,
        verification_notes: str,
    ) -> PrivacyRequest:
        """Binds candidate privacy request to verified candidate contact or explicit human attestation."""
        tenant_id, user_id, membership = _require_active_member(
            uow,
            actor,
            allowed_roles={
                Role.OWNER.value,
                Role.ADMIN.value,
                Role.RECRUITER.value,
                Role.HIRING_MANAGER.value,
            },
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
        if req.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Expected version {expected_version}, found {req.version}.",
            )
        if req.status != PrivacyRequestStatus.PENDING_VERIFICATION.value:
            raise ProblemException(
                status_code=409,
                title="Invalid State Transition",
                code="CONFLICT",
                detail=f"Cannot verify request in status '{req.status}'.",
            )

        cand = uow.session.scalar(
            select(Candidate).where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == req.candidate_id,
            )
        )
        if cand is None:
            raise ProblemException(
                status_code=404,
                title="Candidate Not Found",
                code="NOT_FOUND",
                detail="Underlying candidate no longer exists.",
            )

        method = verification_method.strip().upper()
        if method == PrivacyVerificationStatus.HUMAN_CONTACT_VERIFIED.value:
            if not cand.is_contact_verified or cand.verified_contact_revision_id is None:
                raise ProblemException(
                    status_code=422,
                    title="Candidate Contact Not Verified",
                    code="UNVERIFIED_CANDIDATE_CONTACT",
                    detail=(
                        "Candidate does not have a current human-verified contact revision; "
                        "verify candidate contact first or use explicit HUMAN_IDENTITY_ATTESTED."
                    ),
                )
            req.verified_contact_revision_id = cand.verified_contact_revision_id
            req.verification_status = PrivacyVerificationStatus.HUMAN_CONTACT_VERIFIED.value
        elif method == PrivacyVerificationStatus.HUMAN_IDENTITY_ATTESTED.value:
            if len(verification_notes.strip()) < 10:
                raise ProblemException(
                    status_code=422,
                    title="Verification Evidence Required",
                    code="VALIDATION_ERROR",
                    detail="HUMAN_IDENTITY_ATTESTED requires at least 10 characters of verification notes.",
                )
            req.verification_status = PrivacyVerificationStatus.HUMAN_IDENTITY_ATTESTED.value
        else:
            raise ProblemException(
                status_code=422,
                title="Invalid Verification Method",
                code="VALIDATION_ERROR",
                detail="verification_method must be HUMAN_CONTACT_VERIFIED or HUMAN_IDENTITY_ATTESTED.",
            )

        now = uow.clock.now()
        req.status = PrivacyRequestStatus.VERIFIED.value
        req.verified_by = user_id
        req.verified_at = now
        req.verification_notes = verification_notes.strip()
        req.retention_preview = cls.build_retention_preview(uow, tenant_id, req.candidate_id)
        req.version += 1
        req.updated_at = now

        uow.record_audit(
            resource_type="privacy_request",
            action="PRIVACY_REQUEST_VERIFIED",
            resource_id=req.id,
            actor_id=user_id,
            actor_capability=membership.role,
            details={
                "verification_status": req.verification_status,
                "subject_hash": req.subject_hash,
            },
        )
        uow.session.flush()
        return req

    @classmethod
    def approve_privacy_request(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        request_id: UUID,
        expected_version: int,
        approval_notes: str,
        override_active_applications: bool = False,
    ) -> PrivacyRequest:
        tenant_id, user_id, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={Role.OWNER.value, Role.ADMIN.value},
            required_capability=Capability.EXECUTE_PRIVACY_PURGE.value,
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
        if req.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Expected version {expected_version}, found {req.version}.",
            )
        if req.verification_status == PrivacyVerificationStatus.UNVERIFIED.value:
            raise ProblemException(
                status_code=409,
                title="Unverified Privacy Request",
                code="PRIVACY_REQUEST_UNVERIFIED",
                detail="Privacy request cannot be approved before human verification.",
            )
        if req.status != PrivacyRequestStatus.VERIFIED.value:
            raise ProblemException(
                status_code=409,
                title="Invalid State Transition",
                code="CONFLICT",
                detail=f"Cannot approve request in status '{req.status}'.",
            )

        now = uow.clock.now()
        req.status = PrivacyRequestStatus.APPROVED.value
        req.approved_by = user_id
        req.approved_at = now
        req.approval_notes = approval_notes.strip()
        req.override_active_applications = override_active_applications
        req.retention_preview = cls.build_retention_preview(uow, tenant_id, req.candidate_id)
        req.version += 1
        req.updated_at = now

        uow.record_audit(
            resource_type="privacy_request",
            action="PRIVACY_REQUEST_APPROVED",
            resource_id=req.id,
            actor_id=user_id,
            actor_capability=Capability.EXECUTE_PRIVACY_PURGE.value,
            details={
                "subject_hash": req.subject_hash,
                "override_active_applications": override_active_applications,
            },
        )
        uow.session.flush()
        return req

    @classmethod
    def _collect_candidate_object_targets(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        candidate_id: UUID,
        quarantine_bucket: str,
        clean_bucket: str,
    ) -> list[dict[str, str]]:
        targets_set: set[tuple[str, str]] = set()

        docs = list(
            uow.session.scalars(
                select(Document).where(
                    Document.tenant_id == tenant_id,
                    Document.candidate_id == candidate_id,
                )
            ).all()
        )
        doc_ids = [d.id for d in docs]
        if doc_ids:
            dvs = list(
                uow.session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id,
                        DocumentVersion.document_id.in_(doc_ids),
                    )
                ).all()
            )
            for dv in dvs:
                targets_set.add((dv.storage_bucket, dv.storage_key))
                # Also purge matching key in quarantine & clean buckets in case promoted
                targets_set.add((quarantine_bucket, dv.storage_key))
                targets_set.add((clean_bucket, dv.storage_key))

        upload_item_ids = list(
            uow.session.scalars(
                select(UploadItem.id).where(
                    UploadItem.tenant_id == tenant_id,
                    UploadItem.candidate_id == candidate_id,
                )
            ).all()
        )
        if upload_item_ids:
            receipts = list(
                uow.session.scalars(
                    select(ObjectReceipt).where(
                        ObjectReceipt.tenant_id == tenant_id,
                        ObjectReceipt.upload_item_id.in_(upload_item_ids),
                    )
                ).all()
            )
            for rec in receipts:
                targets_set.add((rec.bucket, rec.storage_key))

        exports = list(
            uow.session.scalars(
                select(ExportArtifact).where(
                    ExportArtifact.tenant_id == tenant_id,
                    ExportArtifact.candidate_id == candidate_id,
                )
            ).all()
        )
        for exp in exports:
            targets_set.add((exp.storage_bucket, exp.storage_key))

        return [{"bucket": b, "key": k} for (b, k) in sorted(targets_set) if b and k]

    @classmethod
    def _delete_candidate_db_graph(
        cls, uow: UnitOfWork, tenant_id: UUID, candidate_id: UUID, now: datetime
    ) -> dict[str, int]:
        """Deletes all relational candidate & derived records in strict FK-safe order."""
        apps = list(
            uow.session.scalars(
                select(Application).where(
                    Application.tenant_id == tenant_id,
                    Application.candidate_id == candidate_id,
                )
            ).all()
        )
        app_ids = [a.id for a in apps]

        docs = list(
            uow.session.scalars(
                select(Document).where(
                    Document.tenant_id == tenant_id,
                    Document.candidate_id == candidate_id,
                )
            ).all()
        )
        doc_ids = [d.id for d in docs]
        dv_ids = (
            list(
                uow.session.scalars(
                    select(DocumentVersion.id).where(
                        DocumentVersion.tenant_id == tenant_id,
                        DocumentVersion.document_id.in_(doc_ids),
                    )
                ).all()
            )
            if doc_ids
            else []
        )

        # Cancel & sanitize any JobOperation / OutboxEvent referencing candidate/apps/docs
        related_ids_str = (
            {str(candidate_id)}
            | {str(x) for x in app_ids}
            | {str(x) for x in doc_ids}
            | {str(x) for x in dv_ids}
        )
        related_uuids = {candidate_id} | set(app_ids) | set(doc_ids) | set(dv_ids)

        ops = list(
            uow.session.scalars(
                select(JobOperation).where(JobOperation.tenant_id == tenant_id)
            ).all()
        )
        canceled_ops = 0
        affected_op_ids: list[UUID] = []
        for op in ops:
            payload_str = str(op.payload or {})
            matches = (op.resource_id in related_uuids) or any(
                rid in payload_str for rid in related_ids_str
            )
            if matches:
                if op.status in {
                    OperationStatus.PENDING.value,
                    OperationStatus.RUNNING.value,
                    OperationStatus.RETRY_WAIT.value,
                    OperationStatus.NEEDS_REVIEW.value,
                }:
                    op.status = OperationStatus.CANCELED.value
                    canceled_ops += 1
                # Scrub payload & error_detail so failed/canceled jobs retain zero candidate PII
                op.payload = {"purged": True}
                if op.result is not None:
                    op.result = {"purged": True}
                if op.error_detail is not None:
                    op.error_detail = {"code": "PURGED_BY_PRIVACY_REQUEST"}
                op.updated_at = now
                affected_op_ids.append(op.id)

        if affected_op_ids:
            outboxes = list(
                uow.session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.tenant_id == tenant_id,
                        OutboxEvent.payload_ref.in_(affected_op_ids),
                        OutboxEvent.status == OutboxStatus.PENDING.value,
                    )
                ).all()
            )
            for ob in outboxes:
                ob.status = OutboxStatus.FAILED.value

        # 1. Feedback & Export artifacts (P16)
        fb_ids = list(
            uow.session.scalars(
                select(Feedback.id).where(
                    Feedback.tenant_id == tenant_id,
                    Feedback.candidate_id == candidate_id,
                )
            ).all()
        )
        if fb_ids:
            fb_rev_ids = list(
                uow.session.scalars(
                    select(FeedbackRevision.id).where(
                        FeedbackRevision.tenant_id == tenant_id,
                        FeedbackRevision.feedback_id.in_(fb_ids),
                    )
                ).all()
            )
            if fb_rev_ids:
                uow.session.execute(
                    delete(CompetencyRating).where(
                        CompetencyRating.tenant_id == tenant_id,
                        CompetencyRating.feedback_revision_id.in_(fb_rev_ids),
                    )
                )
            uow.session.execute(
                delete(FeedbackRevision).where(
                    FeedbackRevision.tenant_id == tenant_id,
                    FeedbackRevision.feedback_id.in_(fb_ids),
                )
            )
            uow.session.execute(
                delete(Feedback).where(
                    Feedback.tenant_id == tenant_id,
                    Feedback.id.in_(fb_ids),
                )
            )

        exp_res = uow.session.execute(
            delete(ExportArtifact).where(
                ExportArtifact.tenant_id == tenant_id,
                ExportArtifact.candidate_id == candidate_id,
            )
        )

        # 2. Interview Packs (P15)
        pack_ids = list(
            uow.session.scalars(
                select(PackRevision.id).where(
                    PackRevision.tenant_id == tenant_id,
                    PackRevision.candidate_id == candidate_id,
                )
            ).all()
        )
        if pack_ids:
            uow.session.execute(
                delete(PackReview).where(
                    PackReview.tenant_id == tenant_id,
                    PackReview.pack_revision_id.in_(pack_ids),
                )
            )
            uow.session.execute(
                delete(PackQuestion).where(
                    PackQuestion.tenant_id == tenant_id,
                    PackQuestion.pack_revision_id.in_(pack_ids),
                )
            )
            uow.session.execute(
                delete(PackClaim).where(
                    PackClaim.tenant_id == tenant_id,
                    PackClaim.pack_revision_id.in_(pack_ids),
                )
            )
            uow.session.execute(
                delete(PackRevision).where(
                    PackRevision.tenant_id == tenant_id,
                    PackRevision.id.in_(pack_ids),
                )
            )
        if app_ids:
            uow.session.execute(
                delete(InterviewPackContext).where(
                    InterviewPackContext.tenant_id == tenant_id,
                    InterviewPackContext.application_id.in_(app_ids),
                )
            )

        # 3. Summaries (P10) — must precede screening_run & candidate_fact_revision
        if app_ids:
            sum_rev_ids = list(
                uow.session.scalars(
                    select(CandidateSummaryRevision.id).where(
                        CandidateSummaryRevision.tenant_id == tenant_id,
                        CandidateSummaryRevision.application_id.in_(app_ids),
                    )
                ).all()
            )
            if sum_rev_ids:
                claim_ids = list(
                    uow.session.scalars(
                        select(CandidateSummaryClaim.id).where(
                            CandidateSummaryClaim.tenant_id == tenant_id,
                            CandidateSummaryClaim.summary_revision_id.in_(sum_rev_ids),
                        )
                    ).all()
                )
                if claim_ids:
                    uow.session.execute(
                        delete(CandidateSummaryCitation).where(
                            CandidateSummaryCitation.tenant_id == tenant_id,
                            CandidateSummaryCitation.summary_claim_id.in_(claim_ids),
                        )
                    )
                    uow.session.execute(
                        delete(CandidateSummaryClaim).where(
                            CandidateSummaryClaim.tenant_id == tenant_id,
                            CandidateSummaryClaim.id.in_(claim_ids),
                        )
                    )
                uow.session.execute(
                    delete(CandidateSummaryRevision).where(
                        CandidateSummaryRevision.tenant_id == tenant_id,
                        CandidateSummaryRevision.id.in_(sum_rev_ids),
                    )
                )

        # 4. Screening (P09) — must precede candidate_fact_revision & application_document
        sr_ids = list(
            uow.session.scalars(
                select(ScreeningRun.id).where(
                    ScreeningRun.tenant_id == tenant_id,
                    ScreeningRun.candidate_id == candidate_id,
                )
            ).all()
        )
        if sr_ids:
            sres_ids = list(
                uow.session.scalars(
                    select(ScreeningResult.id).where(
                        ScreeningResult.tenant_id == tenant_id,
                        ScreeningResult.screening_run_id.in_(sr_ids),
                    )
                ).all()
            )
            uow.session.execute(
                delete(ScreeningOverride).where(
                    ScreeningOverride.tenant_id == tenant_id,
                    ScreeningOverride.screening_run_id.in_(sr_ids),
                )
            )
            if sres_ids:
                uow.session.execute(
                    delete(ScreeningResultFact).where(
                        ScreeningResultFact.tenant_id == tenant_id,
                        ScreeningResultFact.screening_result_id.in_(sres_ids),
                    )
                )
                uow.session.execute(
                    delete(ScreeningResult).where(
                        ScreeningResult.tenant_id == tenant_id,
                        ScreeningResult.id.in_(sres_ids),
                    )
                )
            uow.session.execute(
                delete(ScreeningRun).where(
                    ScreeningRun.tenant_id == tenant_id,
                    ScreeningRun.id.in_(sr_ids),
                )
            )

        # 5. Candidate Facts (P08) & Evidence (P07)
        fact_ids = list(
            uow.session.scalars(
                select(CandidateFactRevision.id).where(
                    CandidateFactRevision.tenant_id == tenant_id,
                    CandidateFactRevision.candidate_id == candidate_id,
                )
            ).all()
        )
        if fact_ids:
            uow.session.execute(
                delete(CandidateFactEvidence).where(
                    CandidateFactEvidence.tenant_id == tenant_id,
                    CandidateFactEvidence.fact_revision_id.in_(fact_ids),
                )
            )
            uow.session.execute(
                delete(CandidateFactRevision).where(
                    CandidateFactRevision.tenant_id == tenant_id,
                    CandidateFactRevision.id.in_(fact_ids),
                )
            )

        if dv_ids:
            uow.session.execute(
                delete(EvidenceFragment).where(
                    EvidenceFragment.tenant_id == tenant_id,
                    EvidenceFragment.document_version_id.in_(dv_ids),
                )
            )
            uow.session.execute(
                delete(SourceSegment).where(
                    SourceSegment.tenant_id == tenant_id,
                    SourceSegment.document_version_id.in_(dv_ids),
                )
            )
        if doc_ids:
            uow.session.execute(
                delete(ExtractionRun).where(
                    ExtractionRun.tenant_id == tenant_id,
                    ExtractionRun.document_id.in_(doc_ids),
                )
            )

        # 6. Documents, Upload Items, Bindings, Receipts (P06)
        if app_ids:
            uow.session.execute(
                delete(ApplicationDocument).where(
                    ApplicationDocument.tenant_id == tenant_id,
                    ApplicationDocument.application_id.in_(app_ids),
                )
            )
        upload_item_ids = list(
            uow.session.scalars(
                select(UploadItem.id).where(
                    UploadItem.tenant_id == tenant_id,
                    UploadItem.candidate_id == candidate_id,
                )
            ).all()
        )
        if upload_item_ids:
            uow.session.execute(
                delete(ObjectReceipt).where(
                    ObjectReceipt.tenant_id == tenant_id,
                    ObjectReceipt.upload_item_id.in_(upload_item_ids),
                )
            )
            uow.session.execute(
                delete(UploadItem).where(
                    UploadItem.tenant_id == tenant_id,
                    UploadItem.id.in_(upload_item_ids),
                )
            )
        if dv_ids:
            uow.session.execute(
                delete(DocumentVersion).where(
                    DocumentVersion.tenant_id == tenant_id,
                    DocumentVersion.id.in_(dv_ids),
                )
            )
        if doc_ids:
            uow.session.execute(
                delete(Document).where(
                    Document.tenant_id == tenant_id,
                    Document.id.in_(doc_ids),
                )
            )

        # 7. Reflective P11–P14 candidate links / notifications / scheduling rows if merged
        tables = set(sa_inspect(uow.session.connection()).get_table_names())
        for optional_tbl in ("candidate_link", "notification", "reminder_schedule"):
            if optional_tbl in tables:
                cols = {
                    c["name"]
                    for c in sa_inspect(uow.session.connection()).get_columns(optional_tbl)
                }
                if "candidate_id" in cols:
                    uow.session.execute(
                        sa.text(
                            f"DELETE FROM {optional_tbl} WHERE tenant_id = :tid AND candidate_id = :cid"
                        ),
                        {"tid": str(tenant_id), "cid": str(candidate_id)},
                    )

        # 8. Applications, StageEvents & Candidate Contacts/Aggregate (P05)
        if app_ids:
            uow.session.execute(
                delete(StageEvent).where(
                    StageEvent.tenant_id == tenant_id,
                    StageEvent.application_id.in_(app_ids),
                )
            )
            uow.session.execute(
                delete(Application).where(
                    Application.tenant_id == tenant_id,
                    Application.id.in_(app_ids),
                )
            )

        uow.session.execute(
            delete(CandidateContactEvent).where(
                CandidateContactEvent.tenant_id == tenant_id,
                CandidateContactEvent.candidate_id == candidate_id,
            )
        )
        uow.session.execute(
            delete(CandidateContactVerification).where(
                CandidateContactVerification.tenant_id == tenant_id,
                CandidateContactVerification.candidate_id == candidate_id,
            )
        )
        uow.session.execute(
            delete(CandidateContactRevision).where(
                CandidateContactRevision.tenant_id == tenant_id,
                CandidateContactRevision.candidate_id == candidate_id,
            )
        )
        cand_del = uow.session.execute(
            delete(Candidate).where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
            )
        )

        return {
            "candidates_deleted": int(getattr(cand_del, "rowcount", 0) or 0),
            "applications_deleted": len(app_ids),
            "documents_deleted": len(doc_ids),
            "document_versions_deleted": len(dv_ids),
            "facts_deleted": len(fact_ids),
            "packs_deleted": len(pack_ids),
            "feedback_deleted": len(fb_ids),
            "exports_deleted": int(getattr(exp_res, "rowcount", 0) or 0),
            "queued_operations_canceled": canceled_ops,
        }

    @classmethod
    def execute_privacy_purge(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        request_id: UUID,
        storage: ObjectStoragePort,
        quarantine_bucket: str,
        clean_bucket: str,
        encryption_key: str,
    ) -> tuple[PrivacyRequest, DeletionLedger]:
        """Executes verified & approved privacy purge across S3 versions, encrypted S3 tombstone, and DB."""
        tenant_id, user_id, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={Role.OWNER.value, Role.ADMIN.value},
            required_capability=Capability.EXECUTE_PRIVACY_PURGE.value,
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

        # Idempotent duplicate execution: if already COMPLETED, return existing ledger!
        if req.status == PrivacyRequestStatus.COMPLETED.value:
            existing_ledger = uow.session.scalar(
                select(DeletionLedger).where(
                    DeletionLedger.tenant_id == tenant_id,
                    DeletionLedger.candidate_id == req.candidate_id,
                )
            )
            if existing_ledger is not None:
                return req, existing_ledger

        if req.verification_status == PrivacyVerificationStatus.UNVERIFIED.value:
            raise ProblemException(
                status_code=409,
                title="Unverified Privacy Request",
                code="PRIVACY_REQUEST_UNVERIFIED",
                detail="Unverified privacy requests cannot be executed.",
            )
        if req.status not in {
            PrivacyRequestStatus.APPROVED.value,
            PrivacyRequestStatus.PARTIAL_FAILURE.value,
        }:
            raise ProblemException(
                status_code=409,
                title="Privacy Request Not Approved",
                code="CONFLICT",
                detail=f"Request must be APPROVED or PARTIAL_FAILURE to execute; found '{req.status}'.",
            )

        # 1. Check active legal/retention holds (always blocks purge!)
        active_holds = RetentionHoldService.active_holds_for_candidate(
            uow, tenant_id, req.candidate_id
        )
        if active_holds:
            raise ProblemException(
                status_code=409,
                title="Blocked by Active Retention Hold",
                code="RETENTION_HOLD_ACTIVE",
                detail=(
                    f"Cannot purge candidate while {len(active_holds)} active retention hold(s) "
                    "are in effect."
                ),
            )

        # 2. Check active non-terminal applications unless explicitly overridden in approval
        active_apps = list(
            uow.session.scalars(
                select(Application).where(
                    Application.tenant_id == tenant_id,
                    Application.candidate_id == req.candidate_id,
                    Application.stage.notin_(list(TERMINAL_STAGES)),
                )
            ).all()
        )
        if active_apps and not req.override_active_applications:
            raise ProblemException(
                status_code=409,
                title="Active Recruitment Purpose",
                code="ACTIVE_RECRUITMENT_IN_PROGRESS",
                detail=(
                    f"Candidate has {len(active_apps)} active non-terminal application(s). "
                    "Withdraw/close active applications first or approve with override_active_applications=true."
                ),
            )

        policy = OrganizationPolicyService.get_or_create_policy(uow, actor)
        now = _ensure_utc(uow.clock.now())
        backup_expires = now + timedelta(days=policy.backup_expiry_days)
        preview = cls.build_retention_preview(uow, tenant_id, req.candidate_id)
        object_targets = cls._collect_candidate_object_targets(
            uow, tenant_id, req.candidate_id, quarantine_bucket, clean_bucket
        )

        # 3. Purge ALL S3 object versions first; if partial failure occurs, commit PARTIAL_FAILURE!
        purged_versions_total = 0
        try:
            for target in object_targets:
                purged_versions_total += purge_all_object_versions(
                    storage, target["bucket"], target["key"]
                )

            ledger_id = uuid4()
            tombstone_payload = TombstonePayload(
                ledger_id=str(ledger_id),
                tenant_id=str(tenant_id),
                privacy_request_id=str(req.id),
                candidate_id=str(req.candidate_id),
                subject_hash=req.subject_hash,
                purged_at_iso=now.isoformat(),
                backup_window_expires_at_iso=backup_expires.isoformat(),
                object_targets=object_targets,
            )
            tombstone_receipt = write_encrypted_s3_tombstone(
                storage=storage,
                bucket=clean_bucket,
                encryption_key=encryption_key,
                tombstone=tombstone_payload,
            )
        except PrivacyStoragePurgeError as exc:
            req.status = PrivacyRequestStatus.PARTIAL_FAILURE.value
            req.last_error = str(exc)[:500]
            req.version += 1
            req.updated_at = now
            uow.record_audit(
                resource_type="privacy_request",
                action="PRIVACY_PURGE_PARTIAL_FAILURE",
                resource_id=req.id,
                actor_id=user_id,
                actor_capability=Capability.EXECUTE_PRIVACY_PURGE.value,
                details={"subject_hash": req.subject_hash, "error": req.last_error},
            )
            uow.commit()
            raise ProblemException(
                status_code=503,
                title="Object Storage Purge Incomplete",
                code="STORAGE_PURGE_INCOMPLETE",
                detail=(
                    "One or more object storage deletions or S3 tombstone writes failed; "
                    "privacy request marked PARTIAL_FAILURE for idempotent retry."
                ),
            ) from exc

        # 4. S3 versions & independent encrypted S3 tombstone are durable -> purge DB graph!
        db_counts = cls._delete_candidate_db_graph(uow, tenant_id, req.candidate_id, now)
        db_counts["s3_object_versions_deleted"] = purged_versions_total

        ledger = uow.session.scalar(
            select(DeletionLedger).where(
                DeletionLedger.tenant_id == tenant_id,
                DeletionLedger.candidate_id == req.candidate_id,
            )
        )
        if ledger is None:
            ledger = DeletionLedger(
                id=ledger_id,
                tenant_id=tenant_id,
                privacy_request_id=req.id,
                candidate_id=req.candidate_id,
                subject_hash=req.subject_hash,
                scope_summary=db_counts,
                s3_tombstone_bucket=tombstone_receipt.bucket,
                s3_tombstone_key=tombstone_receipt.key,
                s3_tombstone_sha256=tombstone_receipt.sha256_checksum,
                s3_tombstone_etag=tombstone_receipt.etag,
                purged_at=now,
                backup_window_expires_at=backup_expires,
                replayed_after_restore=False,
                executed_by=user_id,
            )
            uow.session.add(ledger)

        req.status = PrivacyRequestStatus.COMPLETED.value
        req.last_error = None
        req.completed_at = now
        req.residual_review_items = list(preview["residual_review_items"])
        req.version += 1
        req.updated_at = now

        uow.record_audit(
            resource_type="deletion_ledger",
            action="PRIVACY_PURGE_COMPLETED",
            resource_id=ledger.id,
            actor_id=user_id,
            actor_capability=Capability.EXECUTE_PRIVACY_PURGE.value,
            details={
                "privacy_request_id": str(req.id),
                "subject_hash": req.subject_hash,
                "s3_tombstone_key": tombstone_receipt.key,
                "backup_window_expires_at": backup_expires.isoformat(),
                "scope_summary": db_counts,
            },
        )
        uow.session.flush()
        return req, ledger

    @classmethod
    def replay_s3_tombstones_after_restore(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        storage: ObjectStoragePort,
        tombstone_bucket: str,
        encryption_key: str,
    ) -> dict[str, Any]:
        """Fetches independent encrypted S3 tombstones and re-purges resurrected DB/S3 state."""
        tenant_id, user_id, _ = _require_active_member(
            uow,
            actor,
            allowed_roles={Role.OWNER.value, Role.ADMIN.value},
            required_capability=Capability.EXECUTE_PRIVACY_PURGE.value,
        )
        now = _ensure_utc(uow.clock.now())
        tombstones = list_and_decrypt_s3_tombstones(
            storage=storage,
            bucket=tombstone_bucket,
            encryption_key=encryption_key,
            tenant_id=tenant_id,
        )

        replayed_count = 0
        already_applied_count = 0

        for ts in tombstones:
            cid = UUID(ts.candidate_id)
            existing = uow.session.scalar(
                select(DeletionLedger).where(
                    DeletionLedger.tenant_id == tenant_id,
                    DeletionLedger.candidate_id == cid,
                )
            )
            cand_still_exists = uow.session.scalar(
                select(Candidate.id).where(
                    Candidate.tenant_id == tenant_id,
                    Candidate.id == cid,
                )
            )
            if existing is not None and cand_still_exists is None:
                already_applied_count += 1
                continue

            # Re-purge any resurrected S3 versions and DB rows!
            s3_deleted = 0
            for target in ts.object_targets:
                s3_deleted += purge_all_object_versions(storage, target["bucket"], target["key"])
            db_counts = cls._delete_candidate_db_graph(uow, tenant_id, cid, now)
            db_counts["s3_object_versions_deleted"] = s3_deleted

            if existing is None:
                ledger = DeletionLedger(
                    id=UUID(ts.ledger_id),
                    tenant_id=tenant_id,
                    privacy_request_id=(
                        UUID(ts.privacy_request_id) if ts.privacy_request_id else None
                    ),
                    candidate_id=cid,
                    subject_hash=ts.subject_hash,
                    scope_summary=db_counts,
                    s3_tombstone_bucket=tombstone_bucket,
                    s3_tombstone_key=f"tombstones/{ts.tenant_id}/{ts.ledger_id}.enc",
                    s3_tombstone_sha256="replayed_from_s3",
                    s3_tombstone_etag=None,
                    purged_at=datetime.fromisoformat(ts.purged_at_iso),
                    backup_window_expires_at=datetime.fromisoformat(
                        ts.backup_window_expires_at_iso
                    ),
                    replayed_after_restore=True,
                    executed_by=user_id,
                )
                uow.session.add(ledger)
            else:
                existing.replayed_after_restore = True
                existing.scope_summary = db_counts

            replayed_count += 1

        uow.record_audit(
            resource_type="deletion_ledger",
            action="TOMBSTONE_LEDGER_REPLAYED",
            actor_id=user_id,
            actor_capability=Capability.EXECUTE_PRIVACY_PURGE.value,
            details={
                "s3_tombstones_scanned": len(tombstones),
                "replayed_resurrected_count": replayed_count,
                "already_applied_count": already_applied_count,
            },
        )
        uow.session.flush()
        return {
            "s3_tombstones_scanned": len(tombstones),
            "replayed_resurrected_count": replayed_count,
            "already_applied_count": already_applied_count,
            "replayed_at_utc": now.isoformat(),
        }
