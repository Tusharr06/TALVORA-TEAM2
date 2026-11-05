"""Integration tests for API lifecycle, middleware pipeline, and security headers."""

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings


@pytest.mark.integration
async def test_correlation_id_propagation(test_settings: Settings) -> None:
    app = create_app(test_settings)
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Automatic generation when missing
            resp1 = await client.get("/health/live")
            assert "x-correlation-id" in resp1.headers
            generated_id = resp1.headers["x-correlation-id"]
            assert len(generated_id) > 10

            # 2. Preservation when provided by client
            custom_corr_id = "test-custom-trace-uuid-999"
            resp2 = await client.get("/health/live", headers={"X-Correlation-ID": custom_corr_id})
            assert resp2.headers["x-correlation-id"] == custom_corr_id


@pytest.mark.integration
async def test_security_headers_enforcement(test_settings: Settings) -> None:
    app = create_app(test_settings)
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/health/live")
            assert resp.headers["x-content-type-options"] == "nosniff"
            assert resp.headers["x-frame-options"] == "DENY"
            assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
            assert "default-src 'self'" in resp.headers["content-security-policy"]


@pytest.mark.integration
async def test_request_size_limit_rejection(test_settings: Settings) -> None:
    # Set tight limit of 50 bytes
    strict_settings = test_settings.model_copy(update={"max_request_size_bytes": 50})
    app = create_app(strict_settings)

    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            oversized_body = "x" * 100
            resp = await client.post(
                "/health/live",
                content=oversized_body,
                headers={"Content-Length": str(len(oversized_body))},
            )
            assert resp.status_code == 413
            data = resp.json()
            assert data["code"] == "LIMIT_EXCEEDED"


@pytest.mark.integration
async def test_diagnostic_route_in_local_vs_production(test_settings: Settings) -> None:
    # 1. Local test setting allows diagnostic route
    app_local = create_app(test_settings)
    async with ASGITransport(app=app_local) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/internal/diagnostic")
            assert resp.status_code == 200
            assert resp.json()["diagnostic"] == "ok"

    # 2. Diagnostic disabled explicitly
    app_disabled = create_app(
        test_settings.model_copy(update={"internal_diagnostic_enabled": False})
    )
    async with ASGITransport(app=app_disabled) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/internal/diagnostic")
            assert resp.status_code == 404
