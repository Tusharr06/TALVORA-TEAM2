"""Initial Tenancy, Operations, Outbox, and RLS Setup.

Revision ID: 0001_initial_tenancy
Revises: None
Create Date: 2026-11-05 23:15:00 UTC
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_tenancy"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. Create identity schema if PostgreSQL
    if is_postgres:
        op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS identity;"))

    # 2. Table: identity.users
    user_schema = "identity" if is_postgres else None
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        schema=user_schema,
    )
    op.create_index("ix_users_email", "users", ["email"], schema=user_schema)

    # 3. Table: organizations
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])

    # 4. Table: memberships
    op.create_table(
        "memberships",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="INTERVIEWER"),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_memberships"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_memberships_tenant", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_memberships_tenant", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user", "memberships", ["user_id"])

    # 5. Table: job_operation
    op.create_table(
        "job_operation",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("resource_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_detail", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_job_operation"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_job_operation_tenant", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_job_operation_tenant", "job_operation", ["tenant_id"])
    op.create_index("ix_job_operation_status", "job_operation", ["status"])
    op.create_index(
        "ix_job_op_tenant_status_next",
        "job_operation",
        ["tenant_id", "status", "next_attempt_at"],
    )
    op.create_index(
        "ix_job_op_tenant_status_lease",
        "job_operation",
        ["tenant_id", "status", "lease_expires_at"],
    )

    # 6. Table: outbox_event
    op.create_table(
        "outbox_event",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("payload_ref", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_outbox_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_outbox_event_tenant", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_outbox_tenant", "outbox_event", ["tenant_id"])
    op.create_index("ix_outbox_status_created", "outbox_event", ["status", "created_at"])

    # 7. Table: idempotency_record
    op.create_table(
        "idempotency_record",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("route", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_idempotency_record"),
        sa.UniqueConstraint(
            "tenant_id", "route", "idempotency_key", name="uq_idempotency_tenant_route_key"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_idempotency_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_idempotency_tenant", "idempotency_record", ["tenant_id"])

    # 8. Table: audit_event
    op.create_table(
        "audit_event",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_capability", sa.String(100), nullable=True),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_audit_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_audit_event_tenant", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_audit_tenant_resource", "audit_event", ["tenant_id", "resource_type", "resource_id"])
    op.create_index("ix_audit_tenant_timestamp", "audit_event", ["tenant_id", "timestamp"])

    # 9. PostgreSQL Row Level Security (RLS) Policies
    if is_postgres:
        tenant_tables = [
            "memberships",
            "job_operation",
            "outbox_event",
            "idempotency_record",
            "audit_event",
        ]
        for tbl in tenant_tables:
            op.execute(sa.text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;"))
            op.execute(sa.text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;"))

            # Standard tenant policy
            if tbl in ("job_operation", "outbox_event"):
                op.execute(
                    sa.text(
                        f"""
                        CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                        AS RESTRICTIVE
                        USING (
                            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                            OR current_setting('app.is_coordinator', true) = 'true'
                        )
                        WITH CHECK (
                            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                        );
                        """
                    )
                )
            else:
                op.execute(
                    sa.text(
                        f"""
                        CREATE POLICY tenant_isolation_{tbl} ON {tbl}
                        AS RESTRICTIVE
                        USING (
                            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                        )
                        WITH CHECK (
                            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                        );
                        """
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_table("audit_event")
    op.drop_table("idempotency_record")
    op.drop_table("outbox_event")
    op.drop_table("job_operation")
    op.drop_table("memberships")
    op.drop_table("organizations")

    user_schema = "identity" if is_postgres else None
    op.drop_table("users", schema=user_schema)
    if is_postgres:
        op.execute(sa.text("DROP SCHEMA IF EXISTS identity CASCADE;"))
