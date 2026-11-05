"""Integration tests for database connectivity, health checks, and lifecycle."""

import pytest

from talvora.config import Settings
from talvora.shared.infrastructure.db import DatabaseHealthChecker, DatabaseManager


@pytest.mark.integration
async def test_database_health_checker_success() -> None:
    # Use SQLite in-memory async engine for fast unit/integration testing
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    db_manager = DatabaseManager(settings)
    checker = DatabaseHealthChecker(db_manager, timeout=2.0)

    try:
        is_healthy, msg, latency = await checker.check()
        assert is_healthy is True
        assert msg == "connected"
        assert latency >= 0.0
    finally:
        await db_manager.close()


@pytest.mark.integration
async def test_database_health_checker_handles_unreachable_host() -> None:
    # Invalid host to test connection failure handling
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://talvora:wrong_pw@127.0.0.1:54399/nonexistent_db",
        db_timeout_seconds=0.5,
    )
    db_manager = DatabaseManager(settings)
    checker = DatabaseHealthChecker(db_manager, timeout=0.5)

    try:
        is_healthy, msg, latency = await checker.check()
        assert is_healthy is False
        assert "connection_failed" in msg
    finally:
        await db_manager.close()
