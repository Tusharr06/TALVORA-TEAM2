"""Competency frameworks and evidence-grounded interview packs (P15).

Revision ID: 0008_interview_packs
Revises: 0007_facts_screening_summaries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_interview_packs"
down_revision: str | None = "0007_facts_screening_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = [
    "competency_framework",
    "competency_framework_version",
    "competency",
    "interview_pack_context",
    "pack_revision",
    "pack_claim",
    "pack_question",
    "pack_review",
]


def _json_type(is_postgres: bool) -> sa.types.TypeEngine[object]:
    return postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_fk_target = "identity.users.id" if is_postgres else "users.id"
    json_col = _json_type(is_postgres)

    op.create_table(
        "competency_framework",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("seniority_band", sa.String(50), nullable=False, server_default="MID_SENIOR"),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("latest_version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("latest_published_version_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_published_version_number", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_competency_framework"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_competency_framework_tenant_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_competency_framework_slug"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')",
            name="ck_competency_framework_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_framework_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_competency_framework_job",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [user_fk_target],
            name="fk_competency_framework_created_by",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_competency_framework_job_stage",
        "competency_framework",
        ["tenant_id", "job_id", "stage"],
    )

    op.create_table(
        "competency_framework_version",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("behavioral_scale_guide", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("published_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_competency_framework_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_competency_framework_version_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "framework_id",
            "version_number",
            name="uq_competency_framework_version_num",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_competency_framework_version_pos"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED')",
            name="ck_competency_framework_version_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_framework_version_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "framework_id"],
            ["competency_framework.tenant_id", "competency_framework.id"],
            name="fk_competency_framework_version_parent",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "competency",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observation_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rubric_anchors", json_col, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_competency"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_competency_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "framework_version_id",
            "code",
            name="uq_competency_version_code",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_competency_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "framework_version_id"],
            ["competency_framework_version.tenant_id", "competency_framework_version.id"],
            name="fk_competency_framework_version",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "interview_pack_context",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("seniority_level", sa.String(50), nullable=False, server_default="SENIOR"),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="BOOKED"),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("participant_user_ids", json_col, nullable=False),
        sa.Column("processed_booking_keys", json_col, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_interview_pack_context"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_interview_pack_context_tenant_id"),
        sa.CheckConstraint(
            "duration_minutes >= 15 AND duration_minutes <= 240",
            name="ck_interview_pack_context_duration",
        ),
        sa.CheckConstraint("schedule_revision >= 1", name="ck_interview_pack_context_sched_rev"),
        sa.CheckConstraint(
            "status IN ('BOOKED', 'RESCHEDULED', 'COMPLETED', 'CANCELED')",
            name="ck_interview_pack_context_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_interview_pack_context_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_interview_pack_context_app",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_interview_pack_context_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_interview_pack_context_candidate",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_interview_pack_context_app",
        "interview_pack_context",
        ["tenant_id", "application_id"],
    )

    op.create_table(
        "pack_revision",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("requirement_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("screening_run_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("summary_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("framework_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("seniority_level", sa.String(50), nullable=False, server_default="SENIOR"),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("fact_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("generation_reason", sa.String(200), nullable=False),
        sa.Column("based_on_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "model_or_generator_id",
            sa.String(120),
            nullable=False,
            server_default="deterministic-pack-v1",
        ),
        sa.Column("candidate_overview", sa.Text(), nullable=False),
        sa.Column("role_requirements", json_col, nullable=False),
        sa.Column("candidate_evidence_summary", sa.Text(), nullable=False),
        sa.Column("relevant_responsibilities", json_col, nullable=False),
        sa.Column("competency_mapping", json_col, nullable=False),
        sa.Column("interviewer_instructions", sa.Text(), nullable=False),
        sa.Column("time_allocation", json_col, nullable=False),
        sa.Column("evaluation_rubric", json_col, nullable=False),
        sa.Column("notes_guidance", sa.Text(), nullable=False),
        sa.Column("feedback_form_structure", json_col, nullable=False),
        sa.Column("quality_warnings", json_col, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("author_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("published_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_revision"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pack_revision_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "interview_id",
            "revision_number",
            name="uq_pack_revision_interview_rev",
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_pack_revision_positive"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED')",
            name="ck_pack_revision_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_revision_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_pack_revision_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_pack_revision_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_pack_revision_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requirement_version_id"],
            ["requirement_version.tenant_id", "requirement_version.id"],
            name="fk_pack_revision_req_ver",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_pack_revision_doc_ver",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "framework_version_id"],
            ["competency_framework_version.tenant_id", "competency_framework_version.id"],
            name="fk_pack_revision_framework_version",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_pack_revision_interview",
        "pack_revision",
        ["tenant_id", "interview_id", "revision_number"],
    )

    op.create_table(
        "pack_claim",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(50), nullable=False),
        sa.Column("content_scope", sa.String(30), nullable=False),
        sa.Column("classification", sa.String(50), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("fact_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_fragment_ids", json_col, nullable=False),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_claim"),
        sa.UniqueConstraint(
            "tenant_id",
            "pack_revision_id",
            "claim_index",
            name="uq_pack_claim_index",
        ),
        sa.CheckConstraint(
            "content_scope IN ('ROLE_GENERIC', 'CANDIDATE_SPECIFIC')",
            name="ck_pack_claim_scope",
        ),
        sa.CheckConstraint(
            "classification IN ('RESUME_SUPPORTED', 'DERIVED_FROM_SUPPORTED_FACTS', "
            "'UNKNOWN', 'CONFLICT', 'HUMAN_ASSERTION_UNVERIFIED')",
            name="ck_pack_claim_classification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_claim_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_pack_claim_revision",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "pack_question",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("content_scope", sa.String(30), nullable=False),
        sa.Column("premise_classification", sa.String(50), nullable=False),
        sa.Column("competency_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("allocated_minutes", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("premise_text", sa.Text(), nullable=True),
        sa.Column("expected_signals", json_col, nullable=False),
        sa.Column("fact_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_fragment_ids", json_col, nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_question"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pack_question_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "pack_revision_id",
            "order_index",
            name="uq_pack_question_order",
        ),
        sa.CheckConstraint("allocated_minutes >= 1", name="ck_pack_question_minutes_pos"),
        sa.CheckConstraint(
            "category IN ('TECHNICAL', 'BEHAVIORAL', 'SCENARIO', 'PROJECT_SPECIFIC', 'CLARIFICATION')",
            name="ck_pack_question_category",
        ),
        sa.CheckConstraint(
            "content_scope IN ('ROLE_GENERIC', 'CANDIDATE_SPECIFIC')",
            name="ck_pack_question_scope",
        ),
        sa.CheckConstraint(
            "premise_classification IN ('ROLE_GENERIC', 'RESUME_SUPPORTED', 'HYPOTHETICAL', "
            "'CLARIFICATION_NEEDED', 'HUMAN_ASSERTION_UNVERIFIED')",
            name="ck_pack_question_premise_class",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_question_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_pack_question_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "competency_id"],
            ["competency.tenant_id", "competency.id"],
            name="fk_pack_question_competency",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "pack_review",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_pack_review"),
        sa.CheckConstraint(
            "decision IN ('APPROVED_FOR_PUBLISH', 'CHANGES_REQUESTED')",
            name="ck_pack_review_decision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_pack_review_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pack_revision_id"],
            ["pack_revision.tenant_id", "pack_revision.id"],
            name="fk_pack_review_revision",
            ondelete="CASCADE",
        ),
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
