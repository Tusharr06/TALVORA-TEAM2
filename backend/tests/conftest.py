"""Test configuration and shared pytest fixtures."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.shared.domain.clock import FrozenClock


@pytest.fixture
def test_settings() -> Settings:
    """Provides non-production settings configured for testing."""
    return Settings(
        environment="test",
        debug=False,
        database_url="sqlite+aiosqlite:///:memory:",
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
        allow_dev_identity_adapter=True,
        cookie_secure=False,
        internal_diagnostic_enabled=True,
        max_request_size_bytes=1024 * 1024,  # 1 MiB for tests
    )


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """Provides a frozen clock for deterministic time assertions."""
    return FrozenClock()


@pytest.fixture
async def async_client(test_settings: Settings) -> AsyncGenerator[AsyncClient]:
    """Provides an HTTPX AsyncClient bound to the FastAPI ASGI application."""
    app = create_app(test_settings)
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client
