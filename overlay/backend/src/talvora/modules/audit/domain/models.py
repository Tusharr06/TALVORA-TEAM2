"""Audit Event Persistence Model, Payload Minimization, and Append-Only Guards (P17).

Operational Note:
Audit events are append-only for application/worker database roles (enforced via ORM
listeners and database triggers/grants). This provides operational immutability against
application bugs or compromised session flows, and is explicitly NOT claimed to be
cryptographically tamper-proof against a privileged database administrator (DBA).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base, TenantScopedMixin

AUDIT_GOVERNANCE_DISCLOSURE = (
    "Audit logs are append-only for normal application and worker roles with minimized "
    "change metadata; they are not tamper-proof against a privileged database administrator."
)

_FORBIDDEN_AUDIT_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "csrf_token",
        "raw_token",
        "password",
        "resume_text",
        "raw_resume",
        "extracted_text",
        "email_body",
        "html_body",
        "raw_payload_bytes",
    }
)

_MAX_AUDIT_STRING_LEN = 500


class AuditActorType(StrEnum):
    HUMAN_MEMBER = "HUMAN_MEMBER"
    SYSTEM_WORKER = "SYSTEM_WORKER"
    CANDIDATE_PUBLIC = "CANDIDATE_PUBLIC"
    IDENTITY_BOOTSTRAP = "IDENTITY_BOOTSTRAP"


class AuditImmutableError(RuntimeError):
    """Raised when application code attempts to UPDATE or DELETE an AuditEvent row."""


def sanitize_audit_details(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Minimizes audit metadata, redacting tokens, raw resume text, and email bodies."""
    if not raw:
        return {}

    def _clean_value(key_hint: str, val: Any) -> Any:
        lowered = key_hint.lower()
        if any(frag in lowered for frag in _FORBIDDEN_AUDIT_KEY_FRAGMENTS):
            return "[REDACTED_SENSITIVE_FIELD]"
        if isinstance(val, str):
            if len(val) > _MAX_AUDIT_STRING_LEN:
                return val[:_MAX_AUDIT_STRING_LEN] + "...[TRUNCATED]"
            return val
        if isinstance(val, dict):
            return {str(k): _clean_value(str(k), v) for k, v in val.items()}
        if isinstance(val, list | tuple):
            return [_clean_value(key_hint, item) for item in list(val)[:50]]
        return val

    return {str(k): _clean_value(str(k), v) for k, v in raw.items()}


def derive_audit_actor_type(
    actor_id: UUID | None,
    actor_capability: str | None,
    details: dict[str, Any] | None,
) -> str:
    """Derives explicit actor classification without altering historical schema columns."""
    if details and isinstance(details.get("actor_type"), str):
        explicit: str = str(details["actor_type"]).upper()
        if explicit in {m.value for m in AuditActorType}:
            return explicit
    cap = (actor_capability or "").upper()
    if "PUBLIC_CANDIDATE" in cap or "CANDIDATE_LINK" in cap:
        return AuditActorType.CANDIDATE_PUBLIC.value
    if "BOOTSTRAP" in cap:
        return AuditActorType.IDENTITY_BOOTSTRAP.value
    if actor_id is None:
        return AuditActorType.SYSTEM_WORKER.value
    return AuditActorType.HUMAN_MEMBER.value


class AuditEvent(Base, TenantScopedMixin):
    """Immutable, append-only operational audit event log."""

    __tablename__ = "audit_event"
    __table_args__ = (
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_audit_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_audit_event_tenant",
            ondelete="CASCADE",
        ),
        sa.Index("ix_audit_tenant_resource", "tenant_id", "resource_type", "resource_id"),
        sa.Index("ix_audit_tenant_timestamp", "tenant_id", "timestamp"),
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    actor_id: Mapped[UUID | None] = mapped_column(sa.UUID(as_uuid=True), nullable=True)
    actor_capability: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    resource_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(sa.UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    details: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)


@event.listens_for(AuditEvent, "before_insert")
def _minimize_audit_before_insert(_mapper: Any, _connection: Any, target: AuditEvent) -> None:
    target.details = sanitize_audit_details(target.details)


@event.listens_for(AuditEvent, "before_update")
def _deny_audit_update(_mapper: Any, _connection: Any, _target: AuditEvent) -> None:
    raise AuditImmutableError("audit_event is append-only; UPDATE is denied to application roles.")


@event.listens_for(AuditEvent, "before_delete")
def _deny_audit_delete(_mapper: Any, _connection: Any, _target: AuditEvent) -> None:
    raise AuditImmutableError("audit_event is append-only; DELETE is denied to application roles.")


@event.listens_for(AuditEvent.__table__, "after_create")
def _install_audit_append_only_triggers(
    _target: Any, connection: sa.Connection, **_kw: Any
) -> None:
    """Installs DB-level triggers denying direct SQL UPDATE/DELETE on audit_event."""
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.execute(
            sa.text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_audit_event_no_update
                BEFORE UPDATE ON audit_event
                BEGIN
                    SELECT RAISE(ABORT, 'audit_event is append-only: UPDATE denied');
                END;
                """
            )
        )
        connection.execute(
            sa.text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_audit_event_no_delete
                BEFORE DELETE ON audit_event
                WHEN EXISTS (SELECT 1 FROM organizations WHERE id = OLD.tenant_id)
                BEGIN
                    SELECT RAISE(ABORT, 'audit_event is append-only: DELETE denied');
                END;
                """
            )
        )
    elif dialect == "postgresql":
        connection.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION prevent_audit_event_mutation()
                RETURNS trigger AS $$
                BEGIN
                    IF TG_OP = 'UPDATE' THEN
                        RAISE EXCEPTION 'audit_event is append-only: UPDATE denied';
                    END IF;
                    IF TG_OP = 'DELETE' AND EXISTS (
                        SELECT 1 FROM organizations WHERE id = OLD.tenant_id
                    ) THEN
                        RAISE EXCEPTION 'audit_event is append-only: DELETE denied';
                    END IF;
                    RETURN OLD;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        connection.execute(
            sa.text(
                """
                DROP TRIGGER IF EXISTS trg_audit_event_immutable ON audit_event;
                CREATE TRIGGER trg_audit_event_immutable
                BEFORE UPDATE OR DELETE ON audit_event
                FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
                """
            )
        )
