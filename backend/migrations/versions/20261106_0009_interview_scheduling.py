"""Interview Requests, Candidate Availability Links, and Slot Proposals.

Revision ID: 0009_interview_scheduling
Revises: 0008_integrations_google_oauth
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_interview_scheduling"
down_revision: str | None = "0008_integrations_google_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [
    "internal_reservation",
    "slot_proposal",
    "availability_snapshot",
    "availability_submission",
    "candidate_availability_link",
    "interview_participant",
    "interview_request",
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. interview_request
    op.create_table(
        "interview_request",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="45"),
        sa.Column("status", sa.String(50), nullable=False, server_default="DRAFT"),
        sa.Column("location_type", sa.String(50), nullable=False, server_default="GOOGLE_MEET"),
        sa.Column("organizer_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("target_date_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_date_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("working_hours_start", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("working_hours_end", sa.Integer(), nullable=False, server_default="17"),
        sa.Column(
            "working_days",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("buffer_before_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("buffer_after_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("minimum_lead_time_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_request"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_interview_request_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_request_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_interview_request_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_interview_request_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_interview_request_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_request_organizer",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_interview_req_app", "interview_request", ["tenant_id", "application_id"])
    op.create_index("ix_interview_req_status", "interview_request", ["tenant_id", "status"])

    # 2. interview_participant
    op.create_table(
        "interview_participant",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_request_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="INTERVIEWER"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("calendar_id", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_participant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_interview_participant_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "interview_request_id",
            "membership_id",
            name="uq_interview_participant_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_participant_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_interview_participant_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_interview_participant_membership",
            ondelete="CASCADE",
        ),
    )

    # 3. candidate_availability_link
    op.create_table(
        "candidate_availability_link",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_request_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("candidate_contact_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_contact_revision_number", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_cand_avail_link"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cand_avail_link_tenant_id"),
        sa.UniqueConstraint("token_hash", name="uq_cand_avail_link_token_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_cand_avail_link_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_cand_avail_link_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_cand_avail_link_candidate",
            ondelete="CASCADE",
        ),
    )

    # 4. availability_submission
    op.create_table(
        "availability_submission",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_request_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("link_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column(
            "available_intervals",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_availability_submission"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_availability_submission_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_availability_submission_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_availability_submission_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "link_id"],
            ["candidate_availability_link.tenant_id", "candidate_availability_link.id"],
            name="fk_availability_submission_link",
            ondelete="CASCADE",
        ),
    )

    # 5. availability_snapshot
    op.create_table(
        "availability_snapshot",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_request_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "interviewer_freebusy_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "candidate_availability_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_availability_snapshot"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_availability_snapshot_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_availability_snapshot_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_availability_snapshot_request",
            ondelete="CASCADE",
        ),
    )

    # 6. slot_proposal
    op.create_table(
        "slot_proposal",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_request_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "interviewer_membership_ids",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("invalidation_reason", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_slot_proposal"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_slot_proposal_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_slot_proposal_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_slot_proposal_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["availability_snapshot.tenant_id", "availability_snapshot.id"],
            name="fk_slot_proposal_snapshot",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_slot_proposal_req_status", "slot_proposal", ["tenant_id", "interview_request_id", "status"])

    # 7. internal_reservation
    op.create_table(
        "internal_reservation",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_request_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_internal_reservation"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_internal_reservation_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_internal_reservation_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_internal_reservation_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_request_id"],
            ["interview_request.tenant_id", "interview_request.id"],
            name="fk_internal_reservation_request",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_internal_res_membership_window",
        "internal_reservation",
        ["tenant_id", "membership_id", "slot_start_utc", "slot_end_utc"],
    )

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


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(
                sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};")
            )
    for table in _TABLES:
        op.drop_table(table)

