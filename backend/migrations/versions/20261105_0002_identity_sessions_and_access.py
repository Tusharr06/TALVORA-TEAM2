"""Identity Sessions, Login Transactions, Teams, and Invitations.

Revision ID: 0002_identity_access
Revises: 0001_initial_tenancy
Create Date: 2026-11-05 23:40:00 UTC
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_identity_access"
down_revision: Union[str, None] = "0001_initial_tenancy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    user_schema = "identity" if is_postgres else None
    user_fk_target = "identity.users.id" if is_postgres else "users.id"

    # 1. Table: auth_session
    op.create_table(
        "auth_session",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("csrf_secret", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("ip_address", sa.String(50), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], [user_fk_target], name="fk_auth_session_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_auth_session_tenant", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_auth_session_token_hash", "auth_session", ["session_token_hash"])
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_index("ix_auth_session_tenant_id", "auth_session", ["tenant_id"])

    # 2. Table: login_transaction
    op.create_table(
        "login_transaction",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("state", sa.String(128), nullable=False, unique=True),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("redirect_target", sa.String(500), nullable=False, server_default="/"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_transaction_state", "login_transaction", ["state"])

    # 3. Table: membership_invitation
    op.create_table(
        "membership_invitation",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by_user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_membership_invitation"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_membership_invitation_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], [user_fk_target], name="fk_membership_invitation_invited_by", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_membership_invitation_tenant", "membership_invitation", ["tenant_id"])
    op.create_index("ix_membership_invitation_token_hash", "membership_invitation", ["token_hash"])
    op.create_index("ix_membership_invitation_tenant_email", "membership_invitation", ["tenant_id", "email"])

    # 4. Table: team
    op.create_table(
        "team",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_team"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_team_tenant_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_team_tenant", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_team_tenant", "team", ["tenant_id"])

    # 5. Table: team_member
    op.create_table(
        "team_member",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_team_member"),
        sa.UniqueConstraint("tenant_id", "team_id", "membership_id", name="uq_team_members_unique"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["organizations.id"], name="fk_team_member_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_id"], ["team.tenant_id", "team.id"], name="fk_team_member_team", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"], name="fk_team_member_membership", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_team_member_tenant", "team_member", ["tenant_id"])
    op.create_index("ix_team_member_team", "team_member", ["team_id"])
    op.create_index("ix_team_member_membership", "team_member", ["membership_id"])

    # 6. Row Level Security for new tenant-scoped tables in PostgreSQL
    if is_postgres:
        for table in ["membership_invitation", "team", "team_member"]:
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
        for table in ["membership_invitation", "team", "team_member"]:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};"))

    op.drop_table("team_member")
    op.drop_table("team")
    op.drop_table("membership_invitation")
    op.drop_table("login_transaction")
    op.drop_table("auth_session")
