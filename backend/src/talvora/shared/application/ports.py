"""Application port interfaces.

Defines protocol abstractions for external infrastructure: database session
management, dependency health checks, object storage, and transactional outbox.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class HealthChecker(Protocol):
    """Protocol for checking dependency connectivity and latency."""

    name: str

    async def check(self) -> tuple[bool, str, float]:
        """Perform health check.

        Returns:
            tuple[bool, str, float]: (is_healthy, status_message, latency_seconds)
        """
        ...
