"""Tenant-Scoped, Cursor-Paginated Audit Query & Restricted Export Service (P17)."""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select

from talvora.modules.applications.domain.models import Application
from talvora.modules.audit.domain.models import (
    AUDIT_GOVERNANCE_DISCLOSURE,
    AuditEvent,
    derive_audit_actor_type,
    sanitize_audit_details,
)
from talvora.modules.feedback.domain.models import ExportArtifact, Feedback
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.interview_packs.domain.models import PackRevision
from talvora.modules.jobs.domain.models import JobAssignment, JobRole
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

ALLOWLISTED_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "organization",
        "organization_policy",
        "membership",
        "membership_invitation",
        "team",
        "job",
        "job_assignment",
        "requirement_version",
        "candidate",
        "application",
        "upload_batch",
        "upload_item",
        "document",
        "document_version",
        "application_document",
        "extraction_run",
        "candidate_fact",
        "candidate_fact_revision",
        "screening_run",
        "screening_override",
        "candidate_summary_revision",
        "competency_framework",
        "interview_pack_context",
        "pack_revision",
        "feedback",
        "export_artifact",
        "job_operation",
        "privacy_request",
        "retention_hold",
        "deletion_ledger",
        "audit_event",
        "integration_connection",
        "interview_request",
        "interview",
        "external_action",
        "candidate_link",
    }
)


@dataclass(frozen=True)
class AuditQueryFilters:
    resource_type: str | None = None
    resource_id: UUID | None = None
    actor_id: UUID | None = None
    action: str | None = None
    correlation_id: str | None = None
    from_timestamp: datetime | None = None
    to_timestamp: datetime | None = None


@dataclass(frozen=True)
class AuditEventView:
    id: UUID
    tenant_id: UUID
    actor_id: UUID | None
    actor_type: str
    actor_capability: str | None
    resource_type: str
    resource_id: UUID | None
    action: str
    correlation_id: str | None
    timestamp: datetime
    details: dict[str, object]


@dataclass(frozen=True)
class AuditPageResult:
    items: list[AuditEventView]
    next_cursor: str | None
    has_more: bool
    governance_disclosure: str


def _encode_cursor(ts: datetime, event_id: UUID) -> str:
    ts_utc = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
    raw = json.dumps({"ts": ts_utc.isoformat(), "id": str(event_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        obj = json.loads(decoded)
        ts = datetime.fromisoformat(obj["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts, UUID(obj["id"])
    except Exception as exc:
        raise ProblemException(
            status_code=422,
            title="Invalid Pagination Cursor",
            code="VALIDATION_ERROR",
            detail="Provided audit cursor is malformed.",
        ) from exc


def _require_active_audit_actor(
    uow: UnitOfWork, actor: ActorContext
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
    if membership.role == Role.INTERVIEWER.value:
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail="Interviewers are not authorized to query organization audit logs.",
        )
    return actor.tenant_id, actor.user_id, membership


class AuditQueryService:
    """Tenant-isolated, role-scoped query and export over append-only AuditEvent rows."""

    @classmethod
    def _assigned_resource_ids_for_scoped_staff(
        cls, uow: UnitOfWork, tenant_id: UUID, user_id: UUID
    ) -> set[UUID]:
        """Collects job, application, candidate, pack, feedback, and export IDs assigned to a recruiter/HM."""
        job_ids = list(
            uow.session.scalars(
                select(JobAssignment.job_id).where(
                    JobAssignment.tenant_id == tenant_id,
                    JobAssignment.user_id == user_id,
                    JobAssignment.role.in_([JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value]),
                )
            ).all()
        )
        if not job_ids:
            return set()

        allowed: set[UUID] = set(job_ids)
        apps = list(
            uow.session.scalars(
                select(Application).where(
                    Application.tenant_id == tenant_id,
                    Application.job_id.in_(job_ids),
                )
            ).all()
        )
        for app_row in apps:
            allowed.add(app_row.id)
            allowed.add(app_row.candidate_id)

        pack_ids = list(
            uow.session.scalars(
                select(PackRevision.id).where(
                    PackRevision.tenant_id == tenant_id,
                    PackRevision.job_id.in_(job_ids),
                )
            ).all()
        )
        allowed.update(pack_ids)

        fb_ids = list(
            uow.session.scalars(
                select(Feedback.id).where(
                    Feedback.tenant_id == tenant_id,
                    Feedback.job_id.in_(job_ids),
                )
            ).all()
        )
        allowed.update(fb_ids)

        exp_ids = list(
            uow.session.scalars(
                select(ExportArtifact.id).where(
                    ExportArtifact.tenant_id == tenant_id,
                    ExportArtifact.job_id.in_(job_ids),
                )
            ).all()
        )
        allowed.update(exp_ids)
        return allowed

    @classmethod
    def query_audit_events(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        filters: AuditQueryFilters,
        limit: int = 50,
        cursor: str | None = None,
    ) -> AuditPageResult:
        tenant_id, user_id, membership = _require_active_audit_actor(uow, actor)

        if limit < 1 or limit > 200:
            raise ProblemException(
                status_code=422,
                title="Invalid Page Size",
                code="VALIDATION_ERROR",
                detail="Audit query limit must be between 1 and 200.",
            )

        if (
            filters.resource_type is not None
            and filters.resource_type not in ALLOWLISTED_RESOURCE_TYPES
        ):
            raise ProblemException(
                status_code=422,
                title="Disallowed Resource Filter",
                code="VALIDATION_ERROR",
                detail=f"resource_type '{filters.resource_type}' is not in the allowlisted audit resource types.",
            )

        if (
            filters.from_timestamp
            and filters.to_timestamp
            and filters.from_timestamp > filters.to_timestamp
        ):
            raise ProblemException(
                status_code=422,
                title="Invalid Time Window",
                code="VALIDATION_ERROR",
                detail="from_timestamp must be earlier than or equal to to_timestamp.",
            )

        stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)

        is_org_admin = membership.role in {Role.OWNER.value, Role.ADMIN.value}
        if not is_org_admin:
            scoped_ids = cls._assigned_resource_ids_for_scoped_staff(uow, tenant_id, user_id)
            if not scoped_ids:
                return AuditPageResult(
                    items=[],
                    next_cursor=None,
                    has_more=False,
                    governance_disclosure=AUDIT_GOVERNANCE_DISCLOSURE,
                )
            stmt = stmt.where(AuditEvent.resource_id.in_(scoped_ids))

        if filters.resource_type:
            stmt = stmt.where(AuditEvent.resource_type == filters.resource_type)
        if filters.resource_id:
            stmt = stmt.where(AuditEvent.resource_id == filters.resource_id)
        if filters.actor_id:
            stmt = stmt.where(AuditEvent.actor_id == filters.actor_id)
        if filters.action:
            stmt = stmt.where(AuditEvent.action == filters.action.strip().upper())
        if filters.correlation_id:
            stmt = stmt.where(AuditEvent.correlation_id == filters.correlation_id.strip())
        if filters.from_timestamp:
            stmt = stmt.where(AuditEvent.timestamp >= filters.from_timestamp)
        if filters.to_timestamp:
            stmt = stmt.where(AuditEvent.timestamp <= filters.to_timestamp)

        if cursor:
            cur_ts, cur_id = _decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    AuditEvent.timestamp < cur_ts,
                    and_(AuditEvent.timestamp == cur_ts, AuditEvent.id < cur_id),
                )
            )

        stmt = stmt.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).limit(limit + 1)
        rows = list(uow.session.scalars(stmt).all())
        has_more = len(rows) > limit
        page_rows = rows[:limit]

        items = [
            AuditEventView(
                id=r.id,
                tenant_id=r.tenant_id,
                actor_id=r.actor_id,
                actor_type=derive_audit_actor_type(r.actor_id, r.actor_capability, r.details),
                actor_capability=r.actor_capability,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                action=r.action,
                correlation_id=r.correlation_id,
                timestamp=r.timestamp,
                details=sanitize_audit_details(r.details),
            )
            for r in page_rows
        ]
        next_cursor = (
            _encode_cursor(page_rows[-1].timestamp, page_rows[-1].id)
            if has_more and page_rows
            else None
        )
        return AuditPageResult(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            governance_disclosure=AUDIT_GOVERNANCE_DISCLOSURE,
        )

    @classmethod
    def export_audit_events(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        filters: AuditQueryFilters,
        max_rows: int = 500,
    ) -> list[AuditEventView]:
        """Restricted OWNER/ADMIN bounded export; records its own audit event."""
        tenant_id, user_id, membership = _require_active_audit_actor(uow, actor)
        if membership.role not in {Role.OWNER.value, Role.ADMIN.value}:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Only organization owners and admins can export audit events.",
            )
        if max_rows < 1 or max_rows > 1000:
            raise ProblemException(
                status_code=422,
                title="Invalid Export Limit",
                code="VALIDATION_ERROR",
                detail="max_rows must be between 1 and 1000.",
            )

        collected: list[AuditEventView] = []
        cursor: str | None = None
        while len(collected) < max_rows:
            batch_size = min(200, max_rows - len(collected))
            page = cls.query_audit_events(
                uow=uow,
                actor=actor,
                filters=filters,
                limit=batch_size,
                cursor=cursor,
            )
            collected.extend(page.items)
            if not page.has_more or not page.next_cursor:
                break
            cursor = page.next_cursor

        uow.record_audit(
            resource_type="audit_event",
            action="AUDIT_EVENTS_EXPORTED",
            actor_id=user_id,
            actor_capability=Capability.MANAGE_ORGANIZATION_POLICY.value
            if actor.has_capability(Capability.MANAGE_ORGANIZATION_POLICY.value)
            else membership.role,
            details={
                "exported_count": len(collected),
                "filtered_resource_type": filters.resource_type,
            },
        )
        return collected
