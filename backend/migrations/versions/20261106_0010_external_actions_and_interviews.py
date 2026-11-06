"""External Actions, Human Approvals, Booking Saga, and Interview Reservations.

Revision ID: 0010_external_actions_and_interviews
Revises: 0009_interview_scheduling
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_external_actions_and_interviews"
down_revision: str | None = "0009_interview_scheduling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [
    "interview_reservation",
    "interview_revision",
    "interview",
    "provider_attempt",
    "approval",
    "external_action",
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. external_action
    op.create_table(
        "external_action",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING_APPROVAL"),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("verified_recipient_email", sa.String(320), nullable=False),
        sa.Column("contact_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("contact_revision_number", sa.Integer(), nullable=True),
        sa.Column("organizer_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("organizer_connection_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_external_action"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_external_action_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_external_action_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_external_action_organizer_mem",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_external_action_creator_mem",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_external_action_status", "external_action", ["tenant_id", "status"])
    op.create_index("ix_external_action_resource", "external_action", ["tenant_id", "resource_id", "action_type"])
    op.create_index("ix_external_action_hash", "external_action", ["tenant_id", "payload_hash"])
    op.create_index("ix_external_action_idemp", "external_action", ["tenant_id", "idempotency_key"])

    # 2. approval
    op.create_table(
        "approval",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_action_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.String(50), nullable=False, server_default="APPROVED"),
        sa.Column("approved_payload_hash", sa.String(64), nullable=False),
        sa.Column("approved_resource_version", sa.Integer(), nullable=False),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("resolution_notes", sa.String(500), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_approval"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_approval_tenant_id"),
        sa.UniqueConstraint("tenant_id", "external_action_id", name="uq_approval_external_action"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_approval_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_action_id"],
            ["external_action.tenant_id", "external_action.id"],
            name="fk_approval_action",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approver_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_approval_membership",
            ondelete="RESTRICT",
        ),
    )

    # 3. provider_attempt
    op.create_table(
        "provider_attempt",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_action_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "request_payload_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "response_payload_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_provider_attempt"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_provider_attempt_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_provider_attempt_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_action_id"],
            ["external_action.tenant_id", "external_action.id"],
            name="fk_provider_attempt_action",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_provider_attempt_action_seq",
        "provider_attempt",
        ["tenant_id", "external_action_id", "attempt_number"],
    )

    # 4. interview
    op.create_table(
        "interview",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_request_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("organizer_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("location_type", sa.String(50), nullable=False, server_default="GOOGLE_MEET"),
        sa.Column("status", sa.String(50), nullable=False, server_default="SCHEDULED"),
        sa.Column("google_calendar_event_id", sa.String(255), nullable=True),
        sa.Column("google_meet_link", sa.String(1024), nullable=True),
        sa.Column("google_calendar_html_link", sa.String(1024), nullable=True),
        sa.Column("external_event_etag", sa.String(255), nullable=True),
        sa.Column("current_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("completion_reason", sa.Text(), nullable=True),
        sa.Column("reconciliation_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_interview"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_interview_tenant_id"),
        sa.UniqueConstraint("tenant_id", "interview_request_id", name="uq_interview_request_active"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_interview_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_interview_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_interview_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_interview_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_organizer",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_interview_status", "interview", ["tenant_id", "status"])
    op.create_index("ix_interview_event_id", "interview", ["tenant_id", "google_calendar_event_id"])

    # 5. interview_revision
    op.create_table(
        "interview_revision",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("slot_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column("provider_etag", sa.String(255), nullable=True),
        sa.Column("external_event_status", sa.String(50), nullable=False, server_default="confirmed"),
        sa.Column("candidate_confirmation_status", sa.String(50), nullable=False, server_default="UNCONFIRMED"),
        sa.Column("candidate_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_change_reason", sa.Text(), nullable=True),
        sa.Column(
            "participant_rsvp_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("external_action_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_revision"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_interview_revision_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "interview_id",
            "revision_number",
            name="uq_interview_revision_num",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_revision_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_interview_revision_interview",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_interview_rev_interview", "interview_revision", ["tenant_id", "interview_id"])

    # 6. interview_reservation
    op.create_table(
        "interview_reservation",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("interview_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("slot_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interview_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interview_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_reservation"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_interview_reservation_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_reservation_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_interview_reservation_interview",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_reservation_membership",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_interview_res_mem_window",
        "interview_reservation",
        ["tenant_id", "membership_id", "status", "slot_start_utc", "slot_end_utc"],
    )

    if is_postgres:
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist;"))
        op.execute(
            sa.text(
                """
                ALTER TABLE interview_reservation
                ADD CONSTRAINT ex_interview_reservation_overlap
                EXCLUDE USING gist (
                    tenant_id WITH =,
                    membership_id WITH =,
                    tstzrange(slot_start_utc, slot_end_utc, '[)') WITH &&
                )
                WHERE (status IN ('HELD', 'ACTIVE', 'UNKNOWN'));
                """
            )
        )
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


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(
            sa.text(
                "ALTER TABLE interview_reservation DROP CONSTRAINT IF EXISTS ex_interview_reservation_overlap;"
            )
        )
        for table in _TABLES:
            op.execute(
                sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};")
            )
    for table in _TABLES:
        op.drop_table(table)

