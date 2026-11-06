"""Jobs, Assignments, Requirement Versions, and Criteria.

Revision ID: 0003_jobs_requirements
Revises: 0002_identity_access
Create Date: 2026-11-06 00:07:00 UTC
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_jobs_requirements"
down_revision: Union[str, None] = "0002_identity_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_fk_target = "identity.users.id" if is_postgres else "users.id"
    json_col = JSONB if is_postgres else sa.JSON

    # 1. Table: job
    op.create_table(
        "job",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("department", sa.String(100), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("employment_type", sa.String(50), nullable=False, server_default="FULL_TIME"),
        sa.Column("team_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("creator_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("hiring_manager_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="DRAFT"),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("current_published_version_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("current_published_version_number", sa.Integer(), nullable=True),
        sa.Column("draft_requirements", json_col(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_job"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_job_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_job_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            ["team.tenant_id", "team.id"],
            name="fk_job_team",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            [user_fk_target],
            name="fk_job_creator",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_job_tenant", "job", ["tenant_id"])
    op.create_index("ix_job_tenant_status", "job", ["tenant_id", "status"])
    op.create_index("ix_job_tenant_team", "job", ["tenant_id", "team_id"])
    op.create_index("ix_job_tenant_created", "job", ["tenant_id", "created_at", "id"])

    # 2. Table: job_assignment
    op.create_table(
        "job_assignment",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("assigned_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_job_assignment"),
        sa.UniqueConstraint("tenant_id", "job_id", "user_id", "role", name="uq_job_assignment_unique"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_job_assignment_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_job_assignment_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [user_fk_target],
            name="fk_job_assignment_user",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_job_assignment_tenant", "job_assignment", ["tenant_id"])
    op.create_index("ix_job_assignment_job", "job_assignment", ["tenant_id", "job_id"])
    op.create_index("ix_job_assignment_user", "job_assignment", ["tenant_id", "user_id"])

    # 3. Table: requirement_version
    op.create_table(
        "requirement_version",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("published_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_requirement_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_requirement_version_tenant_id"),
        sa.UniqueConstraint("tenant_id", "job_id", "version_number", name="uq_requirement_version_job_ver"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_requirement_version_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_requirement_version_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_by"],
            [user_fk_target],
            name="fk_requirement_version_published_by",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_requirement_version_tenant", "requirement_version", ["tenant_id"])
    op.create_index("ix_requirement_version_job", "requirement_version", ["tenant_id", "job_id"])

    # 4. Table: criterion
    op.create_table(
        "criterion",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("requirement_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("criterion_type", sa.String(50), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source_requirement_text", sa.Text(), nullable=False),
        sa.Column("relevance_rationale", sa.Text(), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("competency_reference", sa.String(255), nullable=True),
        sa.Column("configuration", json_col(), nullable=False),
        sa.Column("equivalence_rules", json_col(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_criterion"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_criterion_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requirement_version_id"],
            ["requirement_version.tenant_id", "requirement_version.id"],
            name="fk_criterion_requirement_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_criterion_tenant", "criterion", ["tenant_id"])
    op.create_index("ix_criterion_req_ver", "criterion", ["tenant_id", "requirement_version_id"])
    op.create_index("ix_criterion_order", "criterion", ["tenant_id", "requirement_version_id", "order_index"])

    # 5. Row Level Security for new tenant-scoped tables in PostgreSQL
    if is_postgres:
        for table in ["job", "job_assignment", "requirement_version", "criterion"]:
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
        for table in ["criterion", "requirement_version", "job_assignment", "job"]:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};"))

    op.drop_table("criterion")
    op.drop_table("requirement_version")
    op.drop_table("job_assignment")
    op.drop_table("job")
