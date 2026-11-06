"""Google OAuth Connections and Transactions.

Revision ID: 0008_integrations_google_oauth
Revises: 0007_facts_screening_summaries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_integrations_google_oauth"
down_revision: str | None = "0007_facts_screening_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [
    "integration_oauth_transaction",
    "integration_connection",
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. integration_connection
    op.create_table(
        "integration_connection",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="GOOGLE"),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("verified_email", sa.String(320), nullable=False),
        sa.Column(
            "granted_scopes",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("encryption_key_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connection_status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_integration_connection"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_integration_connection_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "owner_membership_id", "provider",
            name="uq_integration_connection_owner_provider",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_integration_connection_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "owner_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_integration_connection_membership",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_integration_conn_owner",
        "integration_connection",
        ["tenant_id", "owner_membership_id", "provider"],
    )
    op.create_index(
        "ix_integration_conn_status",
        "integration_connection",
        ["tenant_id", "connection_status"],
    )

    # 2. integration_oauth_transaction
    op.create_table(
        "integration_oauth_transaction",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="GOOGLE"),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("session_binding", sa.String(255), nullable=False),
        sa.Column("redirect_uri", sa.String(1024), nullable=False),
        sa.Column(
            "requested_scopes",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_integration_oauth_tx"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_integration_oauth_tx_tenant_id"),
        sa.UniqueConstraint("state_hash", name="uq_integration_oauth_tx_state_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_integration_oauth_tx_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_integration_oauth_tx_membership",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_integration_oauth_tx_state",
        "integration_oauth_transaction",
        ["state_hash"],
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

