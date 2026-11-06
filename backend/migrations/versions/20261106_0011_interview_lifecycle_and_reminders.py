"""Notifications, Reminder Schedules, and Provider Synchronization State.

Revision ID: 0011_interview_lifecycle_and_reminders
Revises: 0010_external_actions_and_interviews
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_interview_lifecycle_and_reminders"
down_revision: str | None = "0010_external_actions_and_interviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [
    "provider_sync_state",
    "reminder_schedule",
    "notification",
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. notification
    op.create_table(
        "notification",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_revision_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("external_action_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("notification_type", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False, server_default="GMAIL"),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_notification"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_notification_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_notification_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_notification_interview",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_notification_interview", "notification", ["tenant_id", "interview_id"])
    op.create_index("ix_notification_status", "notification", ["tenant_id", "status"])

    # 2. reminder_schedule
    op.create_table(
        "reminder_schedule",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_revision_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("reminder_kind", sa.String(50), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="SCHEDULED"),
        sa.Column("skip_reason", sa.String(255), nullable=True),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_action_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_reminder_schedule"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_reminder_schedule_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "interview_revision_id",
            "recipient_email",
            "reminder_kind",
            name="uq_reminder_dedup",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_reminder_schedule_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interview_id"],
            ["interview.tenant_id", "interview.id"],
            name="fk_reminder_schedule_interview",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_reminder_status_due", "reminder_schedule", ["tenant_id", "status", "scheduled_for"])
    op.create_index("ix_reminder_interview", "reminder_schedule", ["tenant_id", "interview_id"])

    # 3. provider_sync_state
    op.create_table(
        "provider_sync_state",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("organizer_membership_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("calendar_id", sa.String(320), nullable=False),
        sa.Column("sync_token", sa.String(512), nullable=True),
        sa.Column("channel_id", sa.String(255), nullable=True),
        sa.Column("channel_token", sa.String(255), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("channel_expiration", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_provider_sync_state"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_provider_sync_state_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "organizer_membership_id",
            "calendar_id",
            name="uq_provider_sync_calendar",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_provider_sync_state_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "organizer_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_provider_sync_membership",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_provider_sync_channel", "provider_sync_state", ["tenant_id", "channel_id"])

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

