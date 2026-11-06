"""Structured candidate facts, deterministic screening, and cited summaries.

Revision ID: 0007_facts_screening_summaries
Revises: 0006_evidence_extraction
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_facts_screening_summaries"
down_revision: str | None = "0006_evidence_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = [
    "candidate_fact_revision",
    "candidate_fact_evidence",
    "screening_run",
    "screening_result",
    "screening_result_fact",
    "screening_override",
    "candidate_summary_revision",
    "candidate_summary_claim",
    "candidate_summary_citation",
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_fk_target = "identity.users.id" if is_postgres else "users.id"

    op.create_table(
        "candidate_fact_revision",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_key", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("fact_type", sa.String(50), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("extracted_value", sa.Text(), nullable=False),
        sa.Column("polarity", sa.String(20), nullable=False),
        sa.Column("extracted_polarity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("extraction_run_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_fact_revision"),
        sa.CheckConstraint(
            "fact_type IN ('SKILL', 'EXPERIENCE_MONTHS', 'EDUCATION_LEVEL', "
            "'CERTIFICATION', 'OTHER')",
            name="ck_candidate_fact_type",
        ),
        sa.CheckConstraint(
            "status IN ('EXTRACTED', 'VERIFIED', 'CORRECTED', 'REJECTED')",
            name="ck_candidate_fact_status",
        ),
        sa.CheckConstraint(
            "polarity IN ('AFFIRMED', 'NEGATED', 'UNKNOWN')",
            name="ck_candidate_fact_polarity",
        ),
        sa.CheckConstraint(
            "extracted_polarity IN ('AFFIRMED', 'NEGATED', 'UNKNOWN')",
            name="ck_candidate_fact_extracted_polarity",
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_candidate_fact_revision_positive"),
        sa.CheckConstraint("length(trim(value)) > 0", name="ck_candidate_fact_value_nonempty"),
        sa.UniqueConstraint(
            "tenant_id",
            "candidate_id",
            "fact_key",
            "revision_number",
            name="uq_candidate_fact_revision_number",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_candidate_fact_revision_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_fact_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_candidate_fact_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "extraction_run_id"],
            ["extraction_run.tenant_id", "extraction_run.id"],
            name="fk_candidate_fact_extraction_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [user_fk_target],
            name="fk_candidate_fact_created_by",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_candidate_fact_latest",
        "candidate_fact_revision",
        ["tenant_id", "candidate_id", "fact_key", "revision_number"],
    )

    op.create_table(
        "candidate_fact_evidence",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_fragment_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_fact_evidence"),
        sa.UniqueConstraint(
            "tenant_id",
            "fact_revision_id",
            "evidence_fragment_id",
            name="uq_candidate_fact_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_fact_evidence_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "fact_revision_id"],
            ["candidate_fact_revision.tenant_id", "candidate_fact_revision.id"],
            name="fk_candidate_fact_evidence_fact",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evidence_fragment_id"],
            ["evidence_fragment.tenant_id", "evidence_fragment.id"],
            name="fk_candidate_fact_evidence_fragment",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_candidate_fact_evidence_fragment",
        "candidate_fact_evidence",
        ["tenant_id", "evidence_fragment_id"],
    )

    op.create_table(
        "screening_run",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_document_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("document_version_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("requirement_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("rule_version", sa.String(50), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_run"),
        sa.CheckConstraint(
            "outcome IN ('PASS', 'FAIL', 'UNKNOWN')", name="ck_screening_run_outcome"
        ),
        sa.CheckConstraint("length(trim(rule_version)) > 0", name="ck_screening_run_rule_version"),
        sa.CheckConstraint(
            "(application_document_id IS NULL) = (document_version_id IS NULL)",
            name="ck_screening_run_binding_pair",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_screening_run_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_screening_run_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_screening_run_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_document_id"],
            ["application_document.tenant_id", "application_document.id"],
            name="fk_screening_run_application_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_screening_run_document_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requirement_version_id"],
            ["requirement_version.tenant_id", "requirement_version.id"],
            name="fk_screening_run_requirement_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [user_fk_target],
            name="fk_screening_run_created_by",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_screening_run_application",
        "screening_run",
        ["tenant_id", "application_id", "created_at"],
    )
    op.create_index(
        "ix_screening_run_document_version",
        "screening_run",
        ["tenant_id", "document_version_id"],
    )

    op.create_table(
        "screening_result",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("screening_run_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("criterion_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("criterion_type", sa.String(50), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_result"),
        sa.CheckConstraint(
            "outcome IN ('PASS', 'FAIL', 'UNKNOWN')", name="ck_screening_result_outcome"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_screening_result_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_screening_result_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "screening_run_id"],
            ["screening_run.tenant_id", "screening_run.id"],
            name="fk_screening_result_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "criterion_id"],
            ["criterion.tenant_id", "criterion.id"],
            name="fk_screening_result_criterion",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_screening_result_run",
        "screening_result",
        ["tenant_id", "screening_run_id"],
    )

    op.create_table(
        "screening_result_fact",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("screening_result_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_result_fact"),
        sa.UniqueConstraint(
            "tenant_id",
            "screening_result_id",
            "fact_revision_id",
            name="uq_screening_result_fact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_screening_result_fact_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "screening_result_id"],
            ["screening_result.tenant_id", "screening_result.id"],
            name="fk_screening_result_fact_result",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "fact_revision_id"],
            ["candidate_fact_revision.tenant_id", "candidate_fact_revision.id"],
            name="fk_screening_result_fact_fact",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "screening_override",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("screening_run_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_override"),
        sa.CheckConstraint(
            "outcome IN ('PASS', 'FAIL', 'UNKNOWN')", name="ck_screening_override_outcome"
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_screening_override_revision_positive"),
        sa.CheckConstraint(
            "length(trim(reason)) > 0", name="ck_screening_override_reason_nonempty"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "screening_run_id",
            "revision_number",
            name="uq_screening_override_revision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_screening_override_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "screening_run_id"],
            ["screening_run.tenant_id", "screening_run.id"],
            name="fk_screening_override_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], [user_fk_target], name="fk_screening_override_actor", ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_screening_override_run",
        "screening_override",
        ["tenant_id", "screening_run_id", "created_at"],
    )

    op.create_table(
        "candidate_summary_revision",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("screening_run_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("quality_status", sa.String(30), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("quality_issues", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_summary_revision"),
        sa.CheckConstraint(
            "quality_status IN ('ACCEPTED', 'REJECTED')",
            name="ck_candidate_summary_quality_status",
        ),
        sa.CheckConstraint(
            "revision_number >= 1", name="ck_candidate_summary_revision_positive"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "revision_number",
            name="uq_candidate_summary_revision_number",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_summary_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_candidate_summary_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "screening_run_id"],
            ["screening_run.tenant_id", "screening_run.id"],
            name="fk_candidate_summary_screening",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [user_fk_target],
            name="fk_candidate_summary_created_by",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_candidate_summary_application",
        "candidate_summary_revision",
        ["tenant_id", "application_id", "revision_number"],
    )

    op.create_table(
        "candidate_summary_claim",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("summary_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_index", sa.Integer(), nullable=False),
        sa.Column("fact_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_summary_claim"),
        sa.UniqueConstraint(
            "tenant_id",
            "summary_revision_id",
            "claim_index",
            name="uq_candidate_summary_claim_index",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_summary_claim_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "summary_revision_id"],
            ["candidate_summary_revision.tenant_id", "candidate_summary_revision.id"],
            name="fk_candidate_summary_claim_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "fact_revision_id"],
            ["candidate_fact_revision.tenant_id", "candidate_fact_revision.id"],
            name="fk_candidate_summary_claim_fact",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "candidate_summary_citation",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("summary_claim_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_fragment_id", sa.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_candidate_summary_citation"),
        sa.UniqueConstraint(
            "tenant_id",
            "summary_claim_id",
            "evidence_fragment_id",
            name="uq_candidate_summary_citation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_candidate_summary_citation_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "summary_claim_id"],
            ["candidate_summary_claim.tenant_id", "candidate_summary_claim.id"],
            name="fk_candidate_summary_citation_claim",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evidence_fragment_id"],
            ["evidence_fragment.tenant_id", "evidence_fragment.id"],
            name="fk_candidate_summary_citation_fragment",
            ondelete="RESTRICT",
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
