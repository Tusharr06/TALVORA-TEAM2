"""Evidence Extraction: Extraction Runs, Source Segments, Evidence Fragments.

Revision ID: 0006_evidence_extraction
Revises: 0005_document_intake
Create Date: 2026-11-06 20:00:00 UTC
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_evidence_extraction"
down_revision: Union[str, None] = "0005_document_intake"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_fk_target = "identity.users.id" if is_postgres else "users.id"

    # ------------------------------------------------------------------
    # 1. Table: extraction_run (one immutable, versioned extraction attempt)
    # ------------------------------------------------------------------
    op.create_table(
        "extraction_run",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("extractor_version", sa.String(100), nullable=False),
        sa.Column("normalization_version", sa.String(50), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("problem_code", sa.String(100), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=True),
        sa.Column("processed_pages", sa.Integer(), nullable=True),
        sa.Column("ocr_pages_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_characters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("triggered_by", sa.String(50), nullable=False),
        sa.Column("requested_by_actor_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("job_operation_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_extraction_run"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_extraction_run_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_version_id",
            "revision_number",
            name="uq_extraction_run_revision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_extraction_run_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["document.tenant_id", "document.id"],
            name="fk_extraction_run_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_extraction_run_document_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_actor_id"],
            [user_fk_target],
            name="fk_extraction_run_requested_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_operation_id"],
            ["job_operation.tenant_id", "job_operation.id"],
            name="fk_extraction_run_job_operation",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_extraction_run_tenant", "extraction_run", ["tenant_id"])
    op.create_index(
        "ix_extraction_run_tenant_version",
        "extraction_run",
        ["tenant_id", "document_version_id", "revision_number"],
    )

    # ------------------------------------------------------------------
    # 2. Table: source_segment (immutable, normalized unit of source text)
    # ------------------------------------------------------------------
    op.create_table(
        "source_segment",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("locator_type", sa.String(50), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("docx_path", sa.String(500), nullable=True),
        sa.Column("extraction_method", sa.String(50), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("canonical_text_sha256", sa.String(64), nullable=False),
        sa.Column(
            "is_low_confidence", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("ocr_mean_confidence", sa.Float(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_source_segment"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_source_segment_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "extraction_run_id", "segment_index", name="uq_source_segment_index"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_source_segment_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "extraction_run_id"],
            ["extraction_run.tenant_id", "extraction_run.id"],
            name="fk_source_segment_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_source_segment_document_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("char_count >= 0", name="ck_source_segment_char_count_nonneg"),
    )
    op.create_index("ix_source_segment_tenant", "source_segment", ["tenant_id"])
    op.create_index(
        "ix_source_segment_tenant_run", "source_segment", ["tenant_id", "extraction_run_id"]
    )
    op.create_index(
        "ix_source_segment_tenant_docversion",
        "source_segment",
        ["tenant_id", "document_version_id"],
    )

    # ------------------------------------------------------------------
    # 3. Table: evidence_fragment (bounded, verified excerpt of one segment)
    # ------------------------------------------------------------------
    op.create_table(
        "evidence_fragment",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source_segment_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_evidence_fragment"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_evidence_fragment_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_segment_id",
            "start_offset",
            "end_offset",
            name="uq_evidence_fragment_span",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_evidence_fragment_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_segment_id"],
            ["source_segment.tenant_id", "source_segment.id"],
            name="fk_evidence_fragment_segment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "extraction_run_id"],
            ["extraction_run.tenant_id", "extraction_run.id"],
            name="fk_evidence_fragment_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_evidence_fragment_document_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("start_offset >= 0", name="ck_evidence_fragment_start_nonneg"),
        sa.CheckConstraint(
            "end_offset > start_offset", name="ck_evidence_fragment_end_after_start"
        ),
    )
    op.create_index("ix_evidence_fragment_tenant", "evidence_fragment", ["tenant_id"])
    op.create_index(
        "ix_evidence_fragment_tenant_segment",
        "evidence_fragment",
        ["tenant_id", "source_segment_id"],
    )
    op.create_index(
        "ix_evidence_fragment_tenant_docversion",
        "evidence_fragment",
        ["tenant_id", "document_version_id"],
    )

    # ------------------------------------------------------------------
    # 4. Row Level Security for all new tenant-scoped tables (PostgreSQL only)
    # ------------------------------------------------------------------
    if is_postgres:
        for table in ["extraction_run", "source_segment", "evidence_fragment"]:
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
        for table in ["evidence_fragment", "source_segment", "extraction_run"]:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};"))

    op.drop_table("evidence_fragment")
    op.drop_table("source_segment")
    op.drop_table("extraction_run")
