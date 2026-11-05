"""Unit tests for RFC 7807 Problem Details and exception handling."""

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from talvora.shared.domain.errors import (
    AuthorizationDeniedError,
    EntityNotFoundError,
    InvalidTransitionError,
    VersionConflictError,
)
from talvora.shared.transport.problems import register_exception_handlers


class DummyInput(BaseModel):
    name: str
    count: int


def create_test_error_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/error/not-found")
    async def route_not_found() -> None:
        raise EntityNotFoundError("Candidate", "cand-123")

    @app.get("/error/conflict")
    async def route_conflict() -> None:
        raise VersionConflictError("Job", expected_version=2, actual_version=3)

    @app.get("/error/transition")
    async def route_transition() -> None:
        raise InvalidTransitionError("ApplicationStage", "NEW", "HIRED")

    @app.get("/error/forbidden")
    async def route_forbidden() -> None:
        raise AuthorizationDeniedError("OVERRIDE_SCREENING")

    @app.get("/error/http-exception")
    async def route_http() -> None:
        raise HTTPException(status_code=403, detail="Custom capability denial.")

    @app.post("/error/validation")
    async def route_validation(payload: DummyInput) -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/error/unhandled")
    async def route_unhandled() -> None:
        raise RuntimeError("Unexpected internal crash with database secret: secret_db_pw")

    return app


async def test_entity_not_found_returns_404_problem() -> None:
    app = create_test_error_app()
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/error/not-found")
            assert resp.status_code == 404
            assert resp.headers["content-type"] == "application/problem+json"
            data = resp.json()
            assert data["code"] == "NOT_FOUND"
            assert "Candidate" in data["detail"]


async def test_version_conflict_returns_409_problem() -> None:
    app = create_test_error_app()
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/error/conflict")
            assert resp.status_code == 409
            data = resp.json()
            assert data["code"] == "VERSION_CONFLICT"
            assert "expected 2, but actual is 3" in data["detail"]


async def test_invalid_transition_returns_409_problem() -> None:
    app = create_test_error_app()
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/error/transition")
            assert resp.status_code == 409
            data = resp.json()
            assert data["code"] == "INVALID_TRANSITION"


async def test_authorization_denied_returns_403_problem() -> None:
    app = create_test_error_app()
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/error/forbidden")
            assert resp.status_code == 403
            data = resp.json()
            assert data["code"] == "FORBIDDEN"
            assert "OVERRIDE_SCREENING" in data["detail"]


async def test_validation_error_populates_invalid_params() -> None:
    app = create_test_error_app()
    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/error/validation", json={"name": "test", "count": "not-an-integer"}
            )
            assert resp.status_code == 422
            data = resp.json()
            assert data["code"] == "VALIDATION_ERROR"
            assert len(data["invalid_params"]) > 0
            assert data["invalid_params"][0]["name"] == "count"


async def test_unhandled_exception_does_not_leak_details() -> None:
    app = create_test_error_app()
    async with ASGITransport(app=app, raise_app_exceptions=False) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/error/unhandled")
            assert resp.status_code == 500
            data = resp.json()
            assert data["code"] == "INTERNAL_SERVER_ERROR"
            # Ensure internal secrets or stack trace details are NOT in client response
            assert "secret_db_pw" not in data["detail"]
            assert "RuntimeError" not in data["detail"]
