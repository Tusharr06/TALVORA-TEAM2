"""Integration tests for Operations REST API Endpoints."""

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.operations.domain.models import OperationStatus
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork


@pytest.fixture
def test_app_and_db(test_settings: Settings) -> tuple[Any, UUID, UUID]:
    app = create_app(test_settings)
    # Initialize SQLite in-memory tables
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    tenant_a = uuid4()
    tenant_b = uuid4()
    return app, tenant_a, tenant_b


@pytest.mark.integration
async def test_get_operation_and_cross_tenant_isolation(
    test_app_and_db: tuple[Any, UUID, UUID],
) -> None:
    app, tenant_a, tenant_b = test_app_and_db

    # 1. Admit an operation in Tenant A
    db_manager = app.state.db_manager
    session = db_manager.session_maker()
    with UnitOfWork(session=session, tenant_id=tenant_a) as uow:
        op_a = uow.admit_operation(operation_type="SCREENING", payload={"score": False})

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        # 2. Tenant A queries own operation -> 200 OK
        resp_a = await client.get(
            f"/api/v1/operations/{op_a.id}",
            headers={"X-Tenant-ID": str(tenant_a)},
        )
        assert resp_a.status_code == 200
        data_a = resp_a.json()
        assert data_a["operation_id"] == str(op_a.id)
        assert data_a["tenant_id"] == str(tenant_a)
        assert data_a["status"] == "PENDING"

        # 3. Tenant B attempts to query Tenant A's operation ID
        # INVARIANT: Must return 404 NOT_FOUND (Resource Concealment), never 200 or 403!
        resp_b = await client.get(
            f"/api/v1/operations/{op_a.id}",
            headers={"X-Tenant-ID": str(tenant_b)},
        )
        assert resp_b.status_code == 404
        assert resp_b.json()["code"] == "NOT_FOUND"


@pytest.mark.integration
async def test_missing_tenant_header_returns_401(
    test_app_and_db: tuple[Any, UUID, UUID],
) -> None:
    app, _, _ = test_app_and_db
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        resp = await client.get(f"/api/v1/operations/{uuid4()}")
        assert resp.status_code == 422 or resp.status_code == 401


@pytest.mark.integration
async def test_retry_and_cancel_endpoints(
    test_app_and_db: tuple[Any, UUID, UUID],
) -> None:
    app, tenant_a, _ = test_app_and_db
    db_manager = app.state.db_manager
    session = db_manager.session_maker()

    # Admit failed operation
    with UnitOfWork(session=session, tenant_id=tenant_a) as uow:
        op = uow.admit_operation(operation_type="EXTRACT_TEXT", payload={})
        op.status = OperationStatus.FAILED.value

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        # 1. Retry failed operation
        retry_resp = await client.post(
            f"/api/v1/operations/{op.id}/retry",
            headers={"X-Tenant-ID": str(tenant_a)},
        )
        assert retry_resp.status_code == 200
        assert retry_resp.json()["status"] == "PENDING"

        # 2. Cancel pending operation
        cancel_resp = await client.post(
            f"/api/v1/operations/{op.id}/cancel",
            headers={"X-Tenant-ID": str(tenant_a)},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "CANCELED"


@pytest.mark.integration
async def test_admit_test_diagnostic_operation(
    test_app_and_db: tuple[Any, UUID, UUID],
) -> None:
    app, tenant_a, _ = test_app_and_db
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        resp = await client.post(
            "/api/v1/operations/test-diagnostic",
            json={"input": "test_diagnostic_admission"},
            headers={"X-Tenant-ID": str(tenant_a)},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "operation_id" in data
        assert data["status"] == "PENDING"
        assert f"/api/v1/operations/{data['operation_id']}" in data["status_url"]
