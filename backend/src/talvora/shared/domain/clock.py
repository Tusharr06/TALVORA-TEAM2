"""Injectable UTC Clock and Time Provider.

Guarantees all time operations are timezone-aware UTC. Supports freezing
time in unit and integration test fixtures without system monkey-patching.
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Protocol for time providers."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        ...


class SystemClock:
    """Production system clock returning actual UTC datetime."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Test clock returning a fixed or manually advanced UTC datetime."""

    def __init__(self, fixed_time: datetime | None = None) -> None:
        if fixed_time is None:
            self._current = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
        else:
            if fixed_time.tzinfo is None:
                self._current = fixed_time.replace(tzinfo=UTC)
            else:
                self._current = fixed_time.astimezone(UTC)

    def now(self) -> datetime:
        return self._current

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current = new_time.replace(tzinfo=UTC)
        else:
            self._current = new_time.astimezone(UTC)
