"""Candidates, Contact Revisions/Verifications/Events, Applications, and Stage Events.

Revision ID: 0004_candidates_applications
Revises: 0003_jobs_requirements
Create Date: 2026-11-06 12:00:00 UTC
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_candidates_applications"
down_revision: Union[str, None] = "0003_jobs_requirements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_fk_target = "identity.users.id" if is_postgres else "users.id"

    # ------------------------------------------------------------------
    # 1. Table: candidate
    # ------------------------------------------------------------------
    op.create_table(
        "candidate",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("normalized_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("normalized_email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="MANUAL_ENTRY"),
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("current_contact_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "current_contact_revision_number", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("verified_contact_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_contact_revision_number", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_candidate_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_candidate_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [user_fk_target], name="fk_candidate_created_by", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_candidate_tenant", "candidate", ["tenant_id"])
    op.create_index("ix_candidate_tenant_email", "candidate", ["tenant_id", "normalized_email"])
    op.create_index("ix_candidate_tenant_name", "candidate", ["tenant_id", "normalized_name"])
    op.create_index("ix_candidate_tenant_created", "candidate", ["tenant_id", "created_at", "id"])

    # ------------------------------------------------------------------
    # 2. Table: candidate_contact_revision
    # ------------------------------------------------------------------
    op.create_table(
        "candidate_contact_revision",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("normalized_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("normalized_email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_contact_revision"),
        sa.UniqueConstraint(
            "tenant_id",
            "candidate_id",
            "revision_number",
            name="uq_candidate_contact_revision_number",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_contact_revision_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_contact_revision_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [user_fk_target],
            name="fk_candidate_contact_revision_created_by",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_candidate_contact_revision_tenant", "candidate_contact_revision", ["tenant_id"]
    )
    op.create_index(
        "ix_candidate_contact_revision_candidate",
        "candidate_contact_revision",
        ["tenant_id", "candidate_id"],
    )

    # ------------------------------------------------------------------
    # 3. Table: candidate_contact_verification
    # ------------------------------------------------------------------
    op.create_table(
        "candidate_contact_verification",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_revision_number", sa.Integer(), nullable=False),
        sa.Column("verified_address", sa.String(320), nullable=False),
        sa.Column("verification_source", sa.String(50), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_contact_verification"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_contact_verification_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_contact_verification_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            [user_fk_target],
            name="fk_candidate_contact_verification_actor",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_candidate_contact_verification_tenant", "candidate_contact_verification", ["tenant_id"]
    )
    op.create_index(
        "ix_candidate_contact_verification_candidate",
        "candidate_contact_verification",
        ["tenant_id", "candidate_id"],
    )

    # ------------------------------------------------------------------
    # 4. Table: candidate_contact_event
    # ------------------------------------------------------------------
    op.create_table(
        "candidate_contact_event",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("contact_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_revision_number", sa.Integer(), nullable=False),
        sa.Column("previous_verified_revision_number", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_contact_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_contact_event_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_contact_event_candidate",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_candidate_contact_event_candidate",
        "candidate_contact_event",
        ["tenant_id", "candidate_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # 5. Table: application
    # ------------------------------------------------------------------
    op.create_table(
        "application",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False, server_default="NEW"),
        sa.Column("intake_cycle", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(50), nullable=False, server_default="MANUAL_ENTRY"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_application"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_application_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_application_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_application_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_application_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [user_fk_target], name="fk_application_created_by", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_application_tenant", "application", ["tenant_id"])
    op.create_index("ix_application_tenant_job", "application", ["tenant_id", "job_id"])
    op.create_index(
        "ix_application_tenant_candidate", "application", ["tenant_id", "candidate_id"]
    )
    op.create_index("ix_application_tenant_stage", "application", ["tenant_id", "stage"])
    op.create_index(
        "ix_application_tenant_created", "application", ["tenant_id", "created_at", "id"]
    )

    # Partial unique index: only one ACTIVE (non-withdrawn, non-rejected) application per
    # job/candidate pair. A withdrawn or rejected application does not block a later,
    # explicit re-application (a new intake cycle); full merge/reapply workflow tooling
    # remains deferred to a later phase.
    if is_postgres:
        op.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_application_active_per_job_candidate
                ON application (tenant_id, job_id, candidate_id)
                WHERE stage NOT IN ('WITHDRAWN', 'REJECTED');
                """
            )
        )
    else:
        op.create_index(
            "uq_application_active_per_job_candidate",
            "application",
            ["tenant_id", "job_id", "candidate_id"],
            unique=True,
            sqlite_where=sa.text("stage NOT IN ('WITHDRAWN', 'REJECTED')"),
        )

    # ------------------------------------------------------------------
    # 6. Table: stage_event
    # ------------------------------------------------------------------
    op.create_table(
        "stage_event",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("from_stage", sa.String(50), nullable=True),
        sa.Column("to_stage", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.String(50), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_stage_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_stage_event_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_stage_event_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], [user_fk_target], name="fk_stage_event_actor", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_stage_event_tenant", "stage_event", ["tenant_id"])
    op.create_index(
        "ix_stage_event_tenant_application",
        "stage_event",
        ["tenant_id", "application_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # 7. Row Level Security for all new tenant-scoped tables (PostgreSQL only)
    # ------------------------------------------------------------------
    if is_postgres:
        for table in [
            "candidate",
            "candidate_contact_revision",
            "candidate_contact_verification",
            "candidate_contact_event",
            "application",
            "stage_event",
        ]:
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
        for table in [
            "stage_event",
            "application",
            "candidate_contact_event",
            "candidate_contact_verification",
            "candidate_contact_revision",
            "candidate",
        ]:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};"))
        op.execute(sa.text("DROP INDEX IF EXISTS uq_application_active_per_job_candidate;"))

    op.drop_table("stage_event")
    op.drop_table("application")
    op.drop_table("candidate_contact_event")
    op.drop_table("candidate_contact_verification")
    op.drop_table("candidate_contact_revision")
    op.drop_table("candidate")
