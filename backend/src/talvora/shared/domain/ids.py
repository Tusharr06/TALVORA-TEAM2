"""Identifier types and UUID helpers.

Provides strongly-typed domain identifiers and deterministic UUID generation.
"""

from typing import NewType
from uuid import UUID, uuid4

TenantId = NewType("TenantId", UUID)
PrincipalId = NewType("PrincipalId", UUID)
CorrelationId = NewType("CorrelationId", str)


def new_id() -> UUID:
    """Generate a high-entropy random UUID v4."""
    return uuid4()


def new_correlation_id() -> CorrelationId:
    """Generate a UUID v4 string for request tracing and correlation."""
    return CorrelationId(str(uuid4()))
