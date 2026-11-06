"""Shared (non-collected) helpers for P07 evidence/extraction integration tests.

Mirrors the login/job/batch/upload/scan helpers already duplicated across
`test_document_intake_api.py`, plus extraction-worker-specific helpers. Not named
`test_*.py` so pytest never collects it directly.
"""

from __future__ import annotations

import urllib.parse
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.orm import Session

from talvora.modules.documents.application.scan_worker import DocumentScanWorker
from talvora.modules.evidence.application.extraction_worker import ExtractionWorker
from talvora.modules.operations.domain.models import JobOperation, OperationStatus


async def login_and_setup_org(
    client: AsyncClient,
    email: str = "recruiter@talvora.test",
    org_name: str = "Talvora Robotics",
) -> tuple[str, str, str]:
    resp_login = await client.get("/api/v1/auth/login")
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(resp_login.json()["authorization_url"]).query
    )["state"][0]
    code = f"mock_code_{state[:16]}"

    resp_cb = await client.get(
        f"/api/v1/auth/callback?code={code}&state={state}", follow_redirects=False
    )
    session_token = resp_cb.cookies["talvora_session"]

    csrf_resp = await client.get("/api/v1/auth/csrf")
    csrf_token = csrf_resp.json()["csrf_token"]

    create_org_resp = await client.post(
        "/api/v1/organizations",
        json={"name": org_name, "slug": f"org-{uuid4().hex[:8]}"},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert create_org_resp.status_code == 200, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    sel_resp = await client.post(
        f"/api/v1/organizations/{org_id}/select",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert sel_resp.status_code == 200

    csrf_resp2 = await client.get("/api/v1/auth/csrf")
    csrf_token2 = csrf_resp2.json()["csrf_token"]
    session_token2 = client.cookies.get("talvora_session") or session_token
    return session_token2, csrf_token2, org_id


async def create_open_job(
    client: AsyncClient, csrf_token: str, title: str = "Resume Reviewer"
) -> str:
    create_resp = await client.post(
        "/api/v1/jobs",
        json={
            "title": title,
            "description": "Reviews candidate submissions.",
            "draft_requirements": [
                {
                    "criterion_type": "SKILL_PRESENT",
                    "is_required": True,
                    "source_requirement_text": "Proficiency in Python",
                    "relevance_rationale": "Core backend stack.",
                    "configuration": {"criterion_type": "SKILL_PRESENT", "skill_name": "Python"},
                }
            ],
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert create_resp.status_code == 201, create_resp.text
    job_id = create_resp.json()["id"]
    publish_resp = await client.post(
        f"/api/v1/jobs/{job_id}/publish",
        json={"expected_version": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert publish_resp.status_code == 200, publish_resp.text
    return str(job_id)


async def create_batch(
    client: AsyncClient, csrf_token: str, job_id: str, **overrides: Any
) -> dict[str, Any]:
    payload = {"job_id": job_id, **overrides}
    resp = await client.post(
        "/api/v1/upload-batches",
        json=payload,
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def upload_item(
    client: AsyncClient,
    csrf_token: str,
    batch_id: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    idempotency_key: str | None = None,
    **form_fields: Any,
) -> Any:
    headers = {"X-CSRF-Token": csrf_token, "Origin": "http://testserver"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    data = {k: str(v) for k, v in form_fields.items()}
    files = {"file": (filename, file_bytes, content_type)}
    return await client.post(
        f"/api/v1/upload-batches/{batch_id}/items", data=data, files=files, headers=headers
    )


def run_pending_scan(
    app: Any, tenant_id: str, document_version_id: str, worker_id: str = "test-scan-worker"
) -> JobOperation:
    """Drives the real ClamAV-backed `DocumentScanWorker` for one pending/retryable
    `documents.scan_requested` operation, exactly as `test_document_intake_api.py`
    does, so that extraction admission actually fires afterward."""
    db_manager = app.state.db_manager
    session: Session = db_manager.session_maker()
    try:
        op = session.scalar(
            sa.select(JobOperation)
            .where(
                JobOperation.tenant_id == UUID(tenant_id),
                JobOperation.operation_type == "documents.scan_requested",
                JobOperation.status.in_(
                    [OperationStatus.PENDING.value, OperationStatus.RETRY_WAIT.value]
                ),
                JobOperation.payload["document_version_id"].as_string() == document_version_id,
            )
            .order_by(JobOperation.created_at.desc())
        )
        assert op is not None, "expected a pending/retryable scan operation"
        worker = DocumentScanWorker(
            storage=app.state.object_storage,
            scanner=app.state.virus_scanner,
            clean_bucket=app.state.settings.s3_clean_bucket_name,
        )
        if op.status != OperationStatus.RETRY_WAIT.value:
            op.status = OperationStatus.PENDING.value
        session.commit()
        result_op = worker.run_once(session, UUID(tenant_id), op.id, worker_id)
        session.commit()
        return result_op
    finally:
        session.close()


def find_extraction_operation(
    app: Any, tenant_id: str, document_version_id: str
) -> JobOperation | None:
    db_manager = app.state.db_manager
    session: Session = db_manager.session_maker()
    try:
        return session.scalar(
            sa.select(JobOperation)
            .where(
                JobOperation.tenant_id == UUID(tenant_id),
                JobOperation.operation_type == "documents.extraction_requested",
                JobOperation.payload["document_version_id"].as_string() == document_version_id,
            )
            .order_by(JobOperation.created_at.desc())
        )
    finally:
        session.close()


def run_pending_extraction(
    app: Any,
    tenant_id: str,
    document_version_id: str,
    worker_id: str = "test-extraction-worker",
    operation_id: str | None = None,
) -> JobOperation:
    """Drives the real `ExtractionWorker` (real pypdf/python-docx/pypdfium2/Tesseract
    composite adapter registered on `app.state.document_extractor`) for one pending/
    retryable `documents.extraction_requested` operation."""
    db_manager = app.state.db_manager
    session: Session = db_manager.session_maker()
    try:
        if operation_id is not None:
            op = session.scalar(
                sa.select(JobOperation).where(
                    JobOperation.tenant_id == UUID(tenant_id), JobOperation.id == UUID(operation_id)
                )
            )
        else:
            op = session.scalar(
                sa.select(JobOperation)
                .where(
                    JobOperation.tenant_id == UUID(tenant_id),
                    JobOperation.operation_type == "documents.extraction_requested",
                    JobOperation.status.in_(
                        [OperationStatus.PENDING.value, OperationStatus.RETRY_WAIT.value]
                    ),
                    JobOperation.payload["document_version_id"].as_string() == document_version_id,
                )
                .order_by(JobOperation.created_at.desc())
            )
        assert op is not None, "expected a pending/retryable extraction operation"
        worker = ExtractionWorker(
            storage=app.state.object_storage,
            extractor=app.state.document_extractor,
            settings=app.state.settings,
        )
        # Only normalize a worker-internal RUNNING/lease-expiry state back to PENDING
        # for the test to re-drive; an operation a test deliberately moved to a
        # terminal status (CANCELED/SUCCEEDED/FAILED/NEEDS_REVIEW) must be left
        # exactly as-is so claim_job's own terminal-status short-circuit is actually
        # exercised, instead of being silently un-canceled by this test helper.
        if op.status not in (
            OperationStatus.RETRY_WAIT.value,
            OperationStatus.CANCELED.value,
            OperationStatus.SUCCEEDED.value,
            OperationStatus.FAILED.value,
            OperationStatus.NEEDS_REVIEW.value,
        ):
            op.status = OperationStatus.PENDING.value
        session.commit()
        result_op = worker.run_once(session, UUID(tenant_id), op.id, worker_id)
        session.commit()
        return result_op
    finally:
        session.close()
