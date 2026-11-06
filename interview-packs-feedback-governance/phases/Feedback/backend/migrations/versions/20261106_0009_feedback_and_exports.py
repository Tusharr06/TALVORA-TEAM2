"""Structured interviewer feedback and controlled DOCX/PDF exports (P16).

Revision ID: 0009_feedback_and_exports
Revises: 0008_interview_packs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_feedback_and_exports"
down_revision: str | None = "0008_interview_packs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = [
    "feedback",
    "feedback_revision",
    "competency_rating",
    "export_artifact",
]


def _json_type(is_postgres: bool) -> sa.types.TypeEngine[object]:
    return postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_fk_target = "identity.users.id" if is_postgres else "users.id"
    json_col = _json_type(is_postgres)

    op.create_table(
        "feedback",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interviewer_user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("draft_overall_observations", sa.Text(), nullable=True),
        sa.Column("draft_human_recommendation", sa.String(40), nullable=True),
        sa.Column("draft_ratings", json_col, nullable=False),
        sa.Column("latest_submitted_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_feedback"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_feedback_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "interview_id",
            "interviewer_user_id",
            name="uq_feedback_interview_interviewer",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'AMENDED')",
            name="ck_feedback_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_feedback_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_feedback_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_feedback_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_feedback_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_feedback_pack_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "framework_version_id"],
            ["competency_framework_version.tenant_id", "competency_framework_version.id"],
            name="fk_feedback_framework_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["interviewer_user_id"],
            [user_fk_target],
            name="fk_feedback_interviewer_user",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_feedback_interview", "feedback", ["tenant_id", "interview_id"])

    op.create_table(
        "feedback_revision",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("feedback_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("pack_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("overall_observations", sa.Text(), nullable=False),
        sa.Column("human_recommendation", sa.String(40), nullable=False, server_default="NONE"),
        sa.Column("amendment_reason", sa.Text(), nullable=True),
        sa.Column("prior_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("is_late_submission", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("submitted_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_feedback_revision"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_feedback_revision_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "feedback_id",
            "revision_number",
            name="uq_feedback_revision_num",
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_feedback_revision_pos"),
        sa.CheckConstraint(
            "human_recommendation IN ('ADVANCE_DISCUSSION', 'HOLD_NEEDS_MORE_SIGNAL', "
            "'DO_NOT_ADVANCE', 'NONE')",
            name="ck_feedback_revision_recommendation",
        ),
        sa.CheckConstraint(
            "(revision_number = 1) OR (amendment_reason IS NOT NULL AND length(trim(amendment_reason)) > 0)",
            name="ck_feedback_amendment_reason_required",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_feedback_revision_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "feedback_id"],
            ["feedback.tenant_id", "feedback.id"],
            name="fk_feedback_revision_parent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"],
            [user_fk_target],
            name="fk_feedback_revision_submitted_by",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "competency_rating",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("feedback_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("competency_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("question_notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_competency_rating"),
        sa.UniqueConstraint(
            "tenant_id",
            "feedback_revision_id",
            "competency_id",
            name="uq_competency_rating_rev_comp",
        ),
        sa.CheckConstraint(
            "rating IN ('1', '2', '3', '4', 'NOT_ASSESSED')",
            name="ck_competency_rating_scale",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_rating_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "feedback_revision_id"],
            ["feedback_revision.tenant_id", "feedback_revision.id"],
            name="fk_competency_rating_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "competency_id"],
            ["competency.tenant_id", "competency.id"],
            name="fk_competency_rating_competency",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "export_artifact",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("content_scope", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="READY"),
        sa.Column("operation_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("storage_bucket", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("sha256_checksum", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("requested_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_export_artifact"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_export_artifact_tenant_id"),
        sa.CheckConstraint("format IN ('DOCX', 'PDF')", name="ck_export_artifact_format"),
        sa.CheckConstraint(
            "content_scope IN ('INTERVIEWER_PACK', 'REVIEWER_PACK_WITH_FEEDBACK')",
            name="ck_export_artifact_scope",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'READY', 'FAILED', 'REVOKED', 'EXPIRED')",
            name="ck_export_artifact_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_export_artifact_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_export_artifact_pack_rev",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_export_artifact_candidate",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_export_artifact_candidate", "export_artifact", ["tenant_id", "candidate_id"]
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
        for table in reversed(_TABLES):
            op.execute(
                sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};")
            )
    for table in reversed(_TABLES):
        op.drop_table(table)
