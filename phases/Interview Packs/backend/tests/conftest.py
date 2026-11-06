"""Test configuration and shared pytest fixtures."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure all domain models are registered in Base.metadata for tests
import talvora.modules.applications.domain.models  # noqa: F401
import talvora.modules.audit.domain.models  # noqa: F401
import talvora.modules.candidates.domain.models  # noqa: F401
import talvora.modules.documents.domain.models  # noqa: F401
import talvora.modules.evidence.domain.models  # noqa: F401
import talvora.modules.facts.domain.models  # noqa: F401
import talvora.modules.identity.domain.models  # noqa: F401
import talvora.modules.interview_packs.domain.models  # noqa: F401
import talvora.modules.jobs.domain.models  # noqa: F401
import talvora.modules.operations.domain.models  # noqa: F401
import talvora.modules.organizations.domain.models  # noqa: F401
import talvora.modules.screening.domain.models  # noqa: F401
import talvora.modules.summaries.domain.models  # noqa: F401
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
