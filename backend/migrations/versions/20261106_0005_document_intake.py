"""Document Intake: Upload Batches/Items, Documents, Versions, Receipts, Bindings.

Revision ID: 0005_document_intake
Revises: 0004_candidates_applications
Create Date: 2026-11-06 18:00:00 UTC
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_document_intake"
down_revision: Union[str, None] = "0004_candidates_applications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_fk_target = "identity.users.id" if is_postgres else "users.id"

    # ------------------------------------------------------------------
    # 1. Table: upload_batch
    # ------------------------------------------------------------------
    op.create_table(
        "upload_batch",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="OPEN"),
        sa.Column("max_items", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_file_bytes", sa.BigInteger(), nullable=False, server_default=str(10 * 1024 * 1024)),
        sa.Column(
            "max_total_bytes", sa.BigInteger(), nullable=False, server_default=str(500 * 1024 * 1024)
        ),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_upload_batch"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_upload_batch_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_upload_batch_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_upload_batch_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [user_fk_target], name="fk_upload_batch_created_by", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_upload_batch_tenant", "upload_batch", ["tenant_id"])
    op.create_index("ix_upload_batch_tenant_job", "upload_batch", ["tenant_id", "job_id"])

    # ------------------------------------------------------------------
    # 2. Table: upload_item
    # ------------------------------------------------------------------
    op.create_table(
        "upload_item",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="RESERVED"),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_draft_candidate", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("declared_filename", sa.String(255), nullable=True),
        sa.Column("declared_content_type", sa.String(255), nullable=True),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("document_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("document_version_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("problem_code", sa.String(100), nullable=True),
        sa.Column("problem_detail", sa.Text(), nullable=True),
        sa.Column("next_action", sa.String(100), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_upload_item"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_upload_item_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "batch_id", "sequence_number", name="uq_upload_item_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_upload_item_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            ["upload_batch.tenant_id", "upload_batch.id"],
            name="fk_upload_item_batch",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_upload_item_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_upload_item_application",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [user_fk_target], name="fk_upload_item_created_by", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_upload_item_tenant", "upload_item", ["tenant_id"])
    op.create_index("ix_upload_item_tenant_batch", "upload_item", ["tenant_id", "batch_id"])
    op.create_index(
        "ix_upload_item_tenant_idem", "upload_item", ["tenant_id", "batch_id", "idempotency_key"]
    )

    # ------------------------------------------------------------------
    # 3. Table: document
    # ------------------------------------------------------------------
    op.create_table(
        "document",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("latest_version_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_document"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_document_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["candidate.tenant_id", "candidate.id"],
            name="fk_document_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [user_fk_target], name="fk_document_created_by", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_document_tenant", "document", ["tenant_id"])
    op.create_index("ix_document_tenant_candidate", "document", ["tenant_id", "candidate_id"])

    # ------------------------------------------------------------------
    # 4. Table: document_version (immutable)
    # ------------------------------------------------------------------
    op.create_table(
        "document_version",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("sha256_checksum", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("storage_bucket", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("storage_state", sa.String(50), nullable=False, server_default="PENDING_RECEIPT"),
        sa.Column("scan_result", sa.JSON(), nullable=True),
        sa.Column("source_upload_item_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("source_actor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_document_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_version_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "document_id", "version_number", name="uq_document_version_number"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_document_version_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["document.tenant_id", "document.id"],
            name="fk_document_version_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_upload_item_id"],
            ["upload_item.tenant_id", "upload_item.id"],
            name="fk_document_version_upload_item",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_actor_id"],
            [user_fk_target],
            name="fk_document_version_source_actor",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_document_version_tenant", "document_version", ["tenant_id"])
    op.create_index(
        "ix_document_version_tenant_document", "document_version", ["tenant_id", "document_id"]
    )
    op.create_index(
        "ix_document_version_tenant_checksum", "document_version", ["tenant_id", "sha256_checksum"]
    )

    # ------------------------------------------------------------------
    # 5. Table: object_receipt (dual-write-gap bridge)
    # ------------------------------------------------------------------
    op.create_table(
        "object_receipt",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("upload_item_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_object_receipt"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_object_receipt_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "upload_item_id"],
            ["upload_item.tenant_id", "upload_item.id"],
            name="fk_object_receipt_upload_item",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_object_receipt_tenant", "object_receipt", ["tenant_id"])
    op.create_index(
        "ix_object_receipt_tenant_status_created",
        "object_receipt",
        ["tenant_id", "status", "created_at"],
    )

    # ------------------------------------------------------------------
    # 6. Table: application_document (explicit, version-checked binding)
    # ------------------------------------------------------------------
    op.create_table(
        "application_document",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("bound_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_application_document"),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "binding_generation",
            name="uq_application_document_generation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_application_document_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["application.tenant_id", "application.id"],
            name="fk_application_document_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["document.tenant_id", "document.id"],
            name="fk_application_document_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_version.tenant_id", "document_version.id"],
            name="fk_application_document_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bound_by"], [user_fk_target], name="fk_application_document_bound_by", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_application_document_tenant", "application_document", ["tenant_id"])
    op.create_index(
        "ix_application_document_tenant_app",
        "application_document",
        ["tenant_id", "application_id", "status"],
    )

    # Partial unique index: only one ACTIVE binding per application at a time.
    if is_postgres:
        op.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_application_document_active
                ON application_document (tenant_id, application_id)
                WHERE status = 'ACTIVE';
                """
            )
        )
    else:
        op.create_index(
            "uq_application_document_active",
            "application_document",
            ["tenant_id", "application_id"],
            unique=True,
            sqlite_where=sa.text("status = 'ACTIVE'"),
        )

    # ------------------------------------------------------------------
    # 7. Row Level Security for all new tenant-scoped tables (PostgreSQL only)
    # ------------------------------------------------------------------
    if is_postgres:
        for table in [
            "upload_batch",
            "upload_item",
            "document",
            "document_version",
            "object_receipt",
            "application_document",
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
            "application_document",
            "object_receipt",
            "document_version",
            "document",
            "upload_item",
            "upload_batch",
        ]:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};"))
        op.execute(sa.text("DROP INDEX IF EXISTS uq_application_document_active;"))

    op.drop_table("application_document")
    op.drop_table("object_receipt")
    op.drop_table("document_version")
    op.drop_table("document")
    op.drop_table("upload_item")
    op.drop_table("upload_batch")
