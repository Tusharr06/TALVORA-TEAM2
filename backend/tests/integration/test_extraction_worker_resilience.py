"""Integration tests for extraction worker resilience: restart idempotency,
cancellation, resource-limit enforcement, and corrupted/encrypted documents --
driven through the real API + real (non-OCR) pypdf/python-docx extraction worker.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings, get_settings
from talvora.modules.documents.application.scanner import ClamdScanner
from talvora.modules.documents.application.storage import S3ObjectStorageAdapter
from talvora.modules.evidence.domain.models import ExtractionRun, SourceSegment
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.operations.domain.models import JobOperation, OperationStatus
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base
from tests.fixtures.synthetic.documents import (
    build_corrupted_pdf,
    build_digital_pdf,
    build_encrypted_pdf,
    build_many_page_digital_pdf,
)
from tests.integration._evidence_helpers import (
    create_batch,
    create_open_job,
    find_extraction_operation,
    login_and_setup_org,
    run_pending_extraction,
    run_pending_scan,
    upload_item,
)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "test",
        "debug": False,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "allowed_hosts": ["localhost", "127.0.0.1", "testserver"],
        "allow_dev_identity_adapter": True,
        "cookie_secure": False,
        "internal_diagnostic_enabled": True,
        "max_request_size_bytes": 1024 * 1024,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def resilience_app_factory() -> Any:
    def _make(settings: Settings) -> tuple[Any, MockOidcClient, InMemorySystemMailProvider]:
        app = create_app(settings)
        engine = app.state.db_manager.sync_engine
        Base.metadata.create_all(engine)
        app.dependency_overrides[get_settings] = lambda: settings

        storage = S3ObjectStorageAdapter(settings)
        storage.ensure_bucket(settings.s3_bucket_name)
        storage.ensure_bucket(settings.s3_clean_bucket_name)
        app.state.object_storage = storage
        app.state.virus_scanner = ClamdScanner.from_settings(settings)

        mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
        mail_provider = InMemorySystemMailProvider()
        app.state.oidc_client = mock_oidc
        app.state.mail_provider = mail_provider
        return app, mock_oidc, mail_provider

    return _make


async def _upload_scan_and_get_pending_op(
    app: Any, client: AsyncClient, csrf_token: str, batch_id: str, org_id: str, file_bytes: bytes
) -> dict[str, Any]:
    resp = await upload_item(
        client,
        csrf_token,
        batch_id,
        file_bytes,
        "resume.pdf",
        "application/pdf",
        new_candidate_display_name="Resilience Candidate",
        new_candidate_email="resilience@example.com",
    )
    assert resp.status_code == 201, resp.text
    item = resp.json()
    run_pending_scan(app, org_id, item["document_version_id"])
    return dict(item)


@pytest.mark.integration
async def test_worker_restart_does_not_duplicate_segments(
    resilience_app_factory: Any,
) -> None:
    """Simulate a crash between claiming the extraction job and finishing it: a
    second `run_once` against the same operation must not create duplicate segments,
    and must reuse (not abandon) the same extraction revision."""
    app, _, _ = resilience_app_factory(_settings())
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        item = await _upload_scan_and_get_pending_op(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            build_digital_pdf(["Restart idempotency test content, plenty of words here."]),
        )

        op = find_extraction_operation(app, org_id, item["document_version_id"])
        assert op is not None
        operation_id = str(op.id)

        first_result = run_pending_extraction(
            app, org_id, item["document_version_id"], operation_id=operation_id
        )
        assert first_result.status == OperationStatus.SUCCEEDED.value

        # Simulate a worker restart: the coordinator recovers an expired lease and
        # requeues the JobOperation to PENDING, but the ExtractionRun itself had
        # already reached a terminal status in a commit just before the crash
        # prevented `finalize_job` from running. A second `run_once` must detect the
        # already-terminal run and must not re-extract or duplicate segments.
        db_manager = app.state.db_manager
        session = db_manager.session_maker()
        try:
            op = session.scalar(
                sa.select(JobOperation).where(
                    JobOperation.tenant_id == UUID(org_id), JobOperation.id == UUID(operation_id)
                )
            )
            assert op is not None
            op.status = OperationStatus.PENDING.value
            session.commit()
        finally:
            session.close()

        second_result = run_pending_extraction(
            app, org_id, item["document_version_id"], operation_id=operation_id
        )
        assert second_result.status == OperationStatus.SUCCEEDED.value
        assert second_result.result is not None
        assert second_result.result.get("already_finalized") is True

        db_manager = app.state.db_manager
        session = db_manager.session_maker()
        try:
            run = session.scalar(
                sa.select(ExtractionRun).where(ExtractionRun.tenant_id == UUID(org_id))
            )
            assert run is not None
            segments = list(
                session.scalars(
                    sa.select(SourceSegment).where(
                        SourceSegment.tenant_id == UUID(org_id),
                        SourceSegment.extraction_run_id == run.id,
                    )
                )
            )
            assert len(segments) == 1  # not duplicated by the second run
            assert run.revision_number == 1  # same revision reused, not a new one
        finally:
            session.close()


@pytest.mark.integration
async def test_canceled_operation_is_never_executed(resilience_app_factory: Any) -> None:
    app, _, _ = resilience_app_factory(_settings())
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        item = await _upload_scan_and_get_pending_op(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            build_digital_pdf(["Cancellation test content with enough characters."]),
        )

        db_manager = app.state.db_manager
        session = db_manager.session_maker()
        try:
            op = session.scalar(
                sa.select(JobOperation).where(
                    JobOperation.tenant_id == UUID(org_id),
                    JobOperation.operation_type == "documents.extraction_requested",
                )
            )
            assert op is not None
            op.status = OperationStatus.CANCELED.value
            session.commit()
            operation_id = str(op.id)
        finally:
            session.close()

        result = run_pending_extraction(
            app, org_id, item["document_version_id"], operation_id=operation_id
        )
        assert result.status == OperationStatus.CANCELED.value

        session = db_manager.session_maker()
        try:
            segments = list(
                session.scalars(
                    sa.select(SourceSegment).where(SourceSegment.tenant_id == UUID(org_id))
                )
            )
            assert segments == []  # a canceled operation never extracts anything
        finally:
            session.close()


@pytest.mark.integration
async def test_page_limit_is_enforced_and_marks_partial(resilience_app_factory: Any) -> None:
    app, _, _ = resilience_app_factory(_settings(extraction_max_pdf_pages=2))
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        item = await _upload_scan_and_get_pending_op(
            app, client, csrf_token, batch["id"], org_id, build_many_page_digital_pdf(5)
        )
        run_pending_extraction(app, org_id, item["document_version_id"])

        resp = await client.get(
            f"/api/v1/documents/{item['document_id']}/versions/1/extraction",
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "PARTIAL"
        assert body["problem_code"] == "DOCUMENT_LIMIT_EXCEEDED"
        assert body["is_complete"] is False
        assert body["total_pages"] == 5
        assert body["processed_pages"] == 2
        assert "PAGE_LIMIT_EXCEEDED" in body["warnings"]


@pytest.mark.integration
async def test_character_limit_is_enforced_and_marks_partial(resilience_app_factory: Any) -> None:
    app, _, _ = resilience_app_factory(_settings(extraction_max_characters=40))
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        item = await _upload_scan_and_get_pending_op(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            build_digital_pdf(["A" * 100, "B" * 100]),
        )
        run_pending_extraction(app, org_id, item["document_version_id"])

        resp = await client.get(
            f"/api/v1/documents/{item['document_id']}/versions/1/extraction",
            headers={"Origin": "http://testserver"},
        )
        body = resp.json()
        assert body["status"] == "PARTIAL"
        assert body["problem_code"] == "DOCUMENT_LIMIT_EXCEEDED"
        assert "CHARACTER_LIMIT_EXCEEDED" in body["warnings"]
        assert body["total_characters"] <= 40


@pytest.mark.integration
async def test_corrupted_pdf_fails_within_worker_limits_and_is_actionable(
    resilience_app_factory: Any,
) -> None:
    app, _, _ = resilience_app_factory(_settings())
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        item = await _upload_scan_and_get_pending_op(
            app, client, csrf_token, batch["id"], org_id, build_corrupted_pdf()
        )
        run_pending_extraction(app, org_id, item["document_version_id"])

        resp = await client.get(
            f"/api/v1/documents/{item['document_id']}/versions/1/extraction",
            headers={"Origin": "http://testserver"},
        )
        body = resp.json()
        assert body["status"] == "FAILED"
        assert body["problem_code"] == "DOCUMENT_UNREADABLE"

        source_resp = await client.get(
            f"/api/v1/documents/{item['document_id']}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        assert source_resp.status_code == 200
        assert source_resp.json()["segments"] == []  # never a fabricated partial history


@pytest.mark.integration
async def test_encrypted_pdf_is_defensively_handled_if_ever_reached(
    resilience_app_factory: Any,
) -> None:
    """P06 already rejects `/Encrypt` at upload admission, so an encrypted PDF never
    reaches this worker in production. This test exercises the worker's own
    defense-in-depth handling directly against a document version whose bytes are
    swapped for an encrypted PDF after upload, proving the worker itself never
    fabricates success for undecryptable content."""
    app, _, _ = resilience_app_factory(_settings())
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        item = await _upload_scan_and_get_pending_op(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            build_digital_pdf(["placeholder, will be swapped for encrypted bytes"]),
        )

        storage = app.state.object_storage
        db_manager = app.state.db_manager
        session = db_manager.session_maker()
        try:
            from talvora.modules.documents.domain.models import DocumentVersion

            doc_version = session.scalar(
                sa.select(DocumentVersion).where(
                    DocumentVersion.tenant_id == UUID(org_id),
                    DocumentVersion.id == UUID(item["document_version_id"]),
                )
            )
            assert doc_version is not None
            encrypted_bytes = build_encrypted_pdf()
            storage.put_object(
                bucket=doc_version.storage_bucket,
                key=doc_version.storage_key,
                body=encrypted_bytes,
                content_type="application/pdf",
                sha256_checksum=hashlib.sha256(encrypted_bytes).hexdigest(),
            )
        finally:
            session.close()

        run_pending_extraction(app, org_id, item["document_version_id"])

        resp = await client.get(
            f"/api/v1/documents/{item['document_id']}/versions/1/extraction",
            headers={"Origin": "http://testserver"},
        )
        body = resp.json()
        assert body["status"] == "FAILED"
        assert body["problem_code"] == "DOCUMENT_UNREADABLE"
