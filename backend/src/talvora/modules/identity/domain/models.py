"""Identity User Domain Model."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from talvora.shared.infrastructure.models import Base, TimestampMixin


class User(Base, TimestampMixin):
    """Physical user identity provisioned via OIDC."""

    __tablename__ = "users"
    __table_args__ = (
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    issuer: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    email_verified: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
