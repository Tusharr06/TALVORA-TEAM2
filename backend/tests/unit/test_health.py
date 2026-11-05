"""Unit tests for liveness and readiness health endpoints."""

from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.shared.application.ports import HealthChecker


class MockHealthChecker(HealthChecker):
    def __init__(
        self, name: str, healthy: bool, message: str = "ok", latency: float = 0.005
    ) -> None:
        self.name = name
        self.healthy = healthy
        self.message = message
        self.latency = latency

    async def check(self) -> tuple[bool, str, float]:
        return self.healthy, self.message, self.latency


async def test_health_live_endpoint(test_settings: Settings) -> None:
    app = create_app(test_settings)
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health/live")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "alive"
            assert data["service"] == test_settings.service_name


async def test_health_ready_healthy_when_database_is_healthy(test_settings: Settings) -> None:
    app = create_app(test_settings)
    app.state.health_checkers = [
        MockHealthChecker("database", healthy=True, message="connected", latency=0.002),
        MockHealthChecker("storage", healthy=True, message="bucket_accessible", latency=0.010),
    ]

    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health/ready")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert len(data["components"]) == 2
            assert data["components"][0]["name"] == "database"
            assert data["components"][0]["healthy"] is True


async def test_health_ready_returns_503_when_database_is_down(test_settings: Settings) -> None:
    app = create_app(test_settings)
    app.state.health_checkers = [
        MockHealthChecker("database", healthy=False, message="connection_timeout", latency=3.0),
    ]

    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health/ready")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["components"][0]["healthy"] is False
            assert "timeout" in data["components"][0]["status"]
