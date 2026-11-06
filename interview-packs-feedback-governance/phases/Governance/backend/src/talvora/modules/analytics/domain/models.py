"""Usage Counter Persistence Model for AI Token and Operational Quota Tracking (P17)."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


COHORT_SUPPRESSION_DISCLOSURE = (
    "Small-cohort suppression (n < min_cohort_size) reduces casual re-identification risk "
    "in operational views; it is not a formal mathematical anonymity guarantee."
)

ANALYTICS_GOVERNANCE_DISCLOSURE = (
    "Operational metrics measure recruitment workflow throughput, latency, evidence quality, "
    "and resource consumption. Candidate ranking, composite candidate scoring, and demographic "
    "inference are strictly prohibited."
)


class UsageCounter(Base):
    """Append/rollup ledger tracking AI model token usage and operational quota consumption."""

    __tablename__ = "usage_counter"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    period_date: Mapped[date] = mapped_column(Date(), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    usage_category: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(80), nullable=False, default="deterministic")
    model_id: Mapped[str] = mapped_column(String(120), nullable=False, default="none")
    invocation_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_usage_counter"),
        CheckConstraint("invocation_count >= 0", name="ck_usage_invocation_nonneg"),
        CheckConstraint("prompt_tokens >= 0", name="ck_usage_prompt_tokens_nonneg"),
        CheckConstraint("completion_tokens >= 0", name="ck_usage_completion_tokens_nonneg"),
        CheckConstraint("total_tokens >= 0", name="ck_usage_total_tokens_nonneg"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_usage_counter_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_usage_counter_tenant_period", "tenant_id", "period_date", "usage_category"),
    )
