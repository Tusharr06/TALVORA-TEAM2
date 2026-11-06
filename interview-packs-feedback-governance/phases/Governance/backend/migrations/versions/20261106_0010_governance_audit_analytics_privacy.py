"""Governance: append-only audit triggers, usage counters, org policy, holds & privacy purge (P17).

Revision ID: 0010_governance_audit_analytics_privacy
Revises: 0009_feedback_and_exports
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_governance_audit_analytics_privacy"
down_revision: str | None = "0009_feedback_and_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [
    "organization_policy",
    "organization_policy_revision",
    "usage_counter",
    "retention_hold",
    "privacy_request",
    "deletion_ledger",
]


def _json_type(is_postgres: bool) -> sa.types.TypeEngine[object]:
    return postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_col = _json_type(is_postgres)

    op.create_table(
        "organization_policy",
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("ai_processing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "ai_interview_packs_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("approved_ai_models", json_col, nullable=False),
        sa.Column(
            "monthly_ai_token_budget", sa.Integer(), nullable=False, server_default="1000000"
        ),
        sa.Column(
            "outbound_communications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "reveal_peer_feedback_after_submit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "allow_blind_feedback_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("export_ttl_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("candidate_retention_days", sa.Integer(), nullable=False, server_default="365"),
        sa.Column("audit_retention_days", sa.Integer(), nullable=False, server_default="730"),
        sa.Column("backup_expiry_days", sa.Integer(), nullable=False, server_default="35"),
        sa.Column("min_analytics_cohort_size", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_organization_policy"),
        sa.CheckConstraint(
            "export_ttl_hours >= 1 AND export_ttl_hours <= 168",
            name="ck_org_policy_export_ttl",
        ),
        sa.CheckConstraint(
            "candidate_retention_days >= 1 AND candidate_retention_days <= 3650",
            name="ck_org_policy_cand_retention",
        ),
        sa.CheckConstraint(
            "backup_expiry_days >= 1 AND backup_expiry_days <= 365",
            name="ck_org_policy_backup_expiry",
        ),
        sa.CheckConstraint(
            "min_analytics_cohort_size >= 3 AND min_analytics_cohort_size <= 100",
            name="ck_org_policy_min_cohort",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_organization_policy_tenant",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "organization_policy_revision",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_snapshot", json_col, nullable=False),
        sa.Column("changed_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_organization_policy_revision"),
        sa.UniqueConstraint("tenant_id", "revision_number", name="uq_org_policy_revision_number"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_org_policy_revision_tenant",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "usage_counter",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("usage_category", sa.String(80), nullable=False),
        sa.Column("provider_name", sa.String(80), nullable=False, server_default="deterministic"),
        sa.Column("model_id", sa.String(120), nullable=False, server_default="none"),
        sa.Column("invocation_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_usage_counter"),
        sa.CheckConstraint("invocation_count >= 0", name="ck_usage_invocation_nonneg"),
        sa.CheckConstraint("prompt_tokens >= 0", name="ck_usage_prompt_tokens_nonneg"),
        sa.CheckConstraint("completion_tokens >= 0", name="ck_usage_completion_tokens_nonneg"),
        sa.CheckConstraint("total_tokens >= 0", name="ck_usage_total_tokens_nonneg"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_usage_counter_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_usage_counter_tenant_period",
        "usage_counter",
        ["tenant_id", "period_date", "usage_category"],
    )

    op.create_table(
        "retention_hold",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.String(30), nullable=False, server_default="CANDIDATE"),
        sa.Column("status", sa.String(30), nullable=False, server_default="ACTIVE"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("issued_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_retention_hold"),
        sa.CheckConstraint("scope IN ('CANDIDATE', 'APPLICATION')", name="ck_retention_hold_scope"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'RELEASED', 'EXPIRED')", name="ck_retention_hold_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_retention_hold_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_retention_hold_candidate_status",
        "retention_hold",
        ["tenant_id", "candidate_id", "status"],
    )

    op.create_table(
        "privacy_request",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("request_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING_VERIFICATION"),
        sa.Column(
            "verification_status", sa.String(40), nullable=False, server_default="UNVERIFIED"
        ),
        sa.Column("verified_contact_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_notes", sa.Text(), nullable=True),
        sa.Column(
            "override_active_applications",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("retention_preview", json_col, nullable=False),
        sa.Column("residual_review_items", json_col, nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_privacy_request"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_privacy_request_tenant_id"),
        sa.CheckConstraint(
            "request_type IN ('ERASURE', 'EXPORT_PORTABILITY', 'RETENTION_SWEEP')",
            name="ck_privacy_request_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_VERIFICATION', 'VERIFIED', 'APPROVED', "
            "'PARTIAL_FAILURE', 'COMPLETED', 'CANCELED')",
            name="ck_privacy_request_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_privacy_request_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_privacy_request_candidate", "privacy_request", ["tenant_id", "candidate_id", "status"]
    )

    op.create_table(
        "deletion_ledger",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("privacy_request_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("scope_summary", json_col, nullable=False),
        sa.Column("s3_tombstone_bucket", sa.String(255), nullable=False),
        sa.Column("s3_tombstone_key", sa.String(512), nullable=False),
        sa.Column("s3_tombstone_sha256", sa.String(64), nullable=False),
        sa.Column("s3_tombstone_etag", sa.String(255), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("backup_window_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "replayed_after_restore", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("executed_by", sa.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_deletion_ledger"),
        sa.UniqueConstraint("tenant_id", "candidate_id", name="uq_deletion_ledger_candidate"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_deletion_ledger_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_deletion_ledger_purged_at", "deletion_ledger", ["tenant_id", "purged_at"])

    if is_postgres:
        for table in _TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"))
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"))
            op.execute(
                sa.text(
                    f"""
                    CREATE POLICY {table}_tenant_isolation_policy ON {table}
                    FOR ALL
                    USING (
                        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    )
                    WITH CHECK (
                        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    );
                    """
                )
            )
        op.execute(
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

                DROP TRIGGER IF EXISTS trg_audit_event_immutable ON audit_event;
                CREATE TRIGGER trg_audit_event_immutable
                BEFORE UPDATE OR DELETE ON audit_event
                FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
                """
            )
        )
    else:
        op.execute(
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
        op.execute(
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


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_audit_event_immutable ON audit_event;"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_audit_event_mutation();"))
        for table in reversed(_TABLES):
            op.execute(
                sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};")
            )
    else:
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_audit_event_no_delete;"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_audit_event_no_update;"))

    for table in reversed(_TABLES):
        op.drop_table(table)
