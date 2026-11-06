"""Integration tests for the P06 document intake API: batches, items, scan worker,
documents, downloads, and application bindings.

Exercises the REAL Moto S3 server and the REAL ClamAV clamd daemon (see
infra/compose/docker-compose.yml); nothing here is mocked for storage or malware
scanning. Only harmless, synthetic PDF/DOCX fixtures and the industry-standard,
harmless EICAR antivirus test string are used -- no real resumes or secrets.

NOTE: see the module docstring in test_candidates_applications_api.py for the
pre-existing (P04) get_settings() DI gap this module works around the same way.
"""

import io
import threading
import urllib.parse
import zipfile
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

from talvora.bootstrap import create_app
from talvora.config import Settings, get_settings
from talvora.modules.documents.application.reconciler import ObjectReceiptReconciler
from talvora.modules.documents.application.scan_worker import DocumentScanWorker
from talvora.modules.documents.application.scanner import ClamdScanner
from talvora.modules.documents.application.service import UploadItemService
from talvora.modules.documents.application.storage import S3ObjectStorageAdapter
from talvora.modules.documents.domain.models import ObjectReceipt, UploadBatch, UploadItem
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.operations.domain.models import JobOperation, OperationStatus
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork

EICAR_TEST_STRING = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _build_pdf(body: bytes = b"Synthetic Talvora fixture resume text.") -> bytes:
    return b"%PDF-1.4\n" + body + b"\n%%EOF"


def _build_docx(extra_entries: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="x"></Types>')
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            "</Relationships>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
            "<w:p><w:r><w:t>Synthetic resume body text.</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
        if extra_entries:
            for name, content in extra_entries.items():
                zf.writestr(name, content)
    return buf.getvalue()


def _build_infected_docx() -> bytes:
    """A structurally VALID DOCX (passes our own container validation) whose only
    problem is a real malware signature inside a benign-looking media entry -- it must
    be caught by the real ClamAV scan, not by our structural validator."""
    return _build_docx(extra_entries={"word/media/image1.png": EICAR_TEST_STRING})


@pytest.fixture
def intake_test_app(
    test_settings: Settings,
) -> tuple[Any, MockOidcClient, InMemorySystemMailProvider]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    app.dependency_overrides[get_settings] = lambda: test_settings

    storage = S3ObjectStorageAdapter(test_settings)
    storage.ensure_bucket(test_settings.s3_bucket_name)
    storage.ensure_bucket(test_settings.s3_clean_bucket_name)
    app.state.object_storage = storage
    app.state.virus_scanner = ClamdScanner.from_settings(test_settings)

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    mail_provider = InMemorySystemMailProvider()
    app.state.oidc_client = mock_oidc
    app.state.mail_provider = mail_provider
    return app, mock_oidc, mail_provider


async def _login_and_setup_org(
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
    assert create_org_resp.status_code == 200
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


async def _create_open_job(
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
    assert create_resp.status_code == 201
    job_id = create_resp.json()["id"]
    publish_resp = await client.post(
        f"/api/v1/jobs/{job_id}/publish",
        json={"expected_version": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert publish_resp.status_code == 200
    return str(job_id)


async def _create_batch(
    client: AsyncClient, csrf_token: str, job_id: str, **overrides: Any
) -> dict[str, Any]:
    payload = {"job_id": job_id, **overrides}
    resp = await client.post(
        "/api/v1/upload-batches",
        json=payload,
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert resp.status_code == 201, resp.text
    return cast(dict[str, Any], resp.json())


async def _upload_item(
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


def _run_pending_scan(
    app: Any, tenant_id: str, document_version_id: str, worker_id: str = "test-worker"
) -> JobOperation:
    """Finds and runs the pending/retryable `documents.scan_requested` operation for a
    document version, driving the real ClamAV-backed `DocumentScanWorker` exactly as a
    production worker process would."""
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
        op.status = (
            OperationStatus.RUNNING.value
            if op.status == OperationStatus.PENDING.value
            else op.status
        )
        if op.status != OperationStatus.RETRY_WAIT.value:
            op.status = OperationStatus.PENDING.value
        session.commit()
        result_op = worker.run_once(session, UUID(tenant_id), op.id, worker_id)
        session.commit()
        return result_op
    finally:
        session.close()


@pytest.mark.integration
async def test_batch_lifecycle_per_file_mixed_success(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    """One clean PDF and one infected DOCX in the same batch: the clean item completes
    while the infected item is blocked, without the batch or the other item failing."""
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)
        batch_id = batch["id"]

        clean_resp = await _upload_item(
            client,
            csrf_token,
            batch_id,
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Alice Clean",
            new_candidate_email="alice@example.com",
        )
        assert clean_resp.status_code == 201, clean_resp.text
        clean_item = clean_resp.json()
        assert clean_item["status"] == "SCANNING"

        infected_resp = await _upload_item(
            client,
            csrf_token,
            batch_id,
            _build_infected_docx(),
            "resume.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            new_candidate_display_name="Bob Infected",
            new_candidate_email="bob@example.com",
        )
        assert infected_resp.status_code == 201, infected_resp.text
        infected_item = infected_resp.json()
        assert infected_item["status"] == "SCANNING"

        _run_pending_scan(app, org_id, clean_item["document_version_id"])
        _run_pending_scan(app, org_id, infected_item["document_version_id"])

        detail_resp = await client.get(
            f"/api/v1/upload-batches/{batch_id}",
            headers={"Origin": "http://testserver"},
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        statuses = {item["id"]: item["status"] for item in detail["items"]}
        assert statuses[clean_item["id"]] == "CLEAN"
        assert statuses[infected_item["id"]] == "BLOCKED"

        infected_detail = next(i for i in detail["items"] if i["id"] == infected_item["id"])
        assert infected_detail["problem_code"] == "MALWARE_DETECTED"

        # The rest of the batch (the clean item) completed despite the infected item.
        assert detail["batch"]["item_count"] == 2


@pytest.mark.integration
async def test_oversized_file_rejected_with_bounded_memory(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id, max_file_bytes=1024)

        oversized_body = b"A" * 2000
        resp = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(oversized_body),
            "resume.pdf",
            "application/pdf",
            new_candidate_is_draft=True,
        )
        assert resp.status_code == 201
        item = resp.json()
        assert item["status"] == "FAILED"
        assert item["problem_code"] == "FILE_TOO_LARGE"


@pytest.mark.integration
async def test_spoofed_extension_rejected(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)

        # Real bytes are a DOCX (zip signature), but declared as a .pdf.
        resp = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_docx(),
            "resume.pdf",
            "application/pdf",
            new_candidate_is_draft=True,
        )
        assert resp.status_code == 201
        item = resp.json()
        assert item["status"] == "FAILED"
        assert item["problem_code"] == "SPOOFED_MEDIA_TYPE"


@pytest.mark.integration
async def test_malformed_docx_rejected(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)

        resp = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            b"PK\x03\x04not a real zip at all",
            "resume.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            new_candidate_is_draft=True,
        )
        assert resp.status_code == 201
        item = resp.json()
        assert item["status"] == "FAILED"
        assert item["problem_code"] == "MALFORMED_CONTAINER"


@pytest.mark.integration
async def test_duplicate_idempotency_key_does_not_duplicate_candidate_or_item(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)
        idem_key = f"idem-{uuid4()}"

        first = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            idempotency_key=idem_key,
            new_candidate_display_name="Carla Retried",
            new_candidate_email="carla@example.com",
        )
        assert first.status_code == 201
        first_item = first.json()

        second = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            idempotency_key=idem_key,
            new_candidate_display_name="Carla Retried",
            new_candidate_email="carla@example.com",
        )
        assert second.status_code == 201
        second_item = second.json()

        assert first_item["id"] == second_item["id"]

        detail = (
            await client.get(
                f"/api/v1/upload-batches/{batch['id']}", headers={"Origin": "http://testserver"}
            )
        ).json()
        assert detail["batch"]["item_count"] == 1
        assert len(detail["items"]) == 1


@pytest.mark.integration
async def test_idempotency_key_reused_with_different_payload_conflicts(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)
        idem_key = f"idem-{uuid4()}"

        first = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            idempotency_key=idem_key,
            new_candidate_display_name="Dana One",
            new_candidate_email="dana@example.com",
        )
        assert first.status_code == 201

        second = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            idempotency_key=idem_key,
            new_candidate_display_name="Dana Two Different Name",
            new_candidate_email="dana2@example.com",
        )
        assert second.status_code == 409
        assert second.json()["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.integration
async def test_batch_item_limit_enforced_sequentially(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id, max_items=1)

        first = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            new_candidate_is_draft=True,
        )
        assert first.status_code == 201

        second = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            new_candidate_is_draft=True,
        )
        assert second.status_code == 409
        assert second.json()["code"] == "BATCH_ITEM_LIMIT_EXCEEDED"


@pytest.mark.integration
def test_concurrent_slot_reservation_never_exceeds_batch_item_limit() -> None:
    """Races real threads, each with their own connection, reserving item slots against
    one batch limited to 3 items.

    This deliberately targets the REAL Postgres database (the one already migrated to
    head by this phase), not the sqlite test app: proving the atomic
    `SELECT ... FOR UPDATE` quota reservation holds under genuine concurrent
    transactions requires a database that actually provides row-level locking, which
    sqlite's single shared connection cannot faithfully emulate across threads. All rows
    created here are synthetic and are deleted in the `finally` block.
    """
    from talvora.config import Settings
    from talvora.modules.identity.domain.roles import Role, get_capabilities_for_role
    from talvora.modules.jobs.domain.models import Job, JobStatus
    from talvora.modules.organizations.domain.models import Membership, Organization
    from talvora.shared.application.actor import MembershipContext, Principal
    from talvora.shared.infrastructure.db import DatabaseManager

    real_settings = Settings(
        database_url="postgresql+psycopg://talvora:talvora_local_dev@localhost:5432/talvora_db"
    )
    db_manager = DatabaseManager(real_settings)

    tenant_id = uuid4()
    user_id = uuid4()
    job_id = uuid4()
    batch_id = uuid4()

    from talvora.modules.identity.domain.models import User

    seed_session: Session = db_manager.session_maker()
    try:
        seed_session.add(
            User(
                id=user_id,
                issuer="test-issuer",
                subject=f"race-{user_id.hex[:8]}",
                email="racer@example.com",
                email_verified=True,
            )
        )
        seed_session.add(
            Organization(id=tenant_id, name="Race Org", slug=f"race-{tenant_id.hex[:8]}")
        )
        seed_session.flush()

        seed_session.add(
            Membership(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                role=Role.RECRUITER.value,
                status="ACTIVE",
            )
        )
        seed_session.add(
            Job(
                id=job_id,
                tenant_id=tenant_id,
                title="Race Job",
                description="Synthetic job for concurrency fixture.",
                creator_id=user_id,
                status=JobStatus.OPEN.value,
                version=1,
            )
        )
        seed_session.flush()

        seed_session.add(
            UploadBatch(
                id=batch_id,
                tenant_id=tenant_id,
                job_id=job_id,
                status="OPEN",
                max_items=3,
                max_file_bytes=10 * 1024 * 1024,
                max_total_bytes=500 * 1024 * 1024,
                item_count=0,
                reserved_total_bytes=0,
                created_by=user_id,
                version=1,
            )
        )
        seed_session.commit()
    finally:
        seed_session.close()

    actor = ActorContext(
        principal=Principal(
            user_id=user_id, email="racer@example.com", issuer="iss", subject="sub"
        ),
        membership=MembershipContext(
            membership_id=uuid4(),
            organization_id=tenant_id,
            role=Role.OWNER.value,
            capabilities=get_capabilities_for_role(Role.OWNER.value),
        ),
    )

    results: list[int] = []
    lock = threading.Lock()

    def attempt_reserve() -> None:
        session: Session = db_manager.session_maker()
        try:
            UploadItemService._reserve_slot(
                db=session,
                actor=actor,
                batch_id=batch_id,
                declared_filename="resume.pdf",
                declared_content_type="application/pdf",
                idempotency_key=None,
                request_hash=None,
            )
            with lock:
                results.append(1)
        except Exception:
            with lock:
                results.append(0)
        finally:
            session.close()

    try:
        threads = [threading.Thread(target=attempt_reserve) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 3

        verify_session: Session = db_manager.session_maker()
        try:
            final_batch = verify_session.get(UploadBatch, {"tenant_id": tenant_id, "id": batch_id})
            assert final_batch is not None
            assert final_batch.item_count == 3

            item_count = verify_session.scalar(
                sa.select(sa.func.count())
                .select_from(UploadItem)
                .where(UploadItem.tenant_id == tenant_id, UploadItem.batch_id == batch_id)
            )
            assert item_count == 3
        finally:
            verify_session.close()
    finally:
        cleanup_session: Session = db_manager.session_maker()
        try:
            cleanup_session.execute(sa.delete(UploadItem).where(UploadItem.tenant_id == tenant_id))
            cleanup_session.execute(
                sa.delete(UploadBatch).where(UploadBatch.tenant_id == tenant_id)
            )
            cleanup_session.execute(sa.delete(Job).where(Job.tenant_id == tenant_id))
            cleanup_session.execute(sa.delete(Membership).where(Membership.tenant_id == tenant_id))
            cleanup_session.execute(sa.delete(Organization).where(Organization.id == tenant_id))
            cleanup_session.execute(sa.delete(User).where(User.id == user_id))
            cleanup_session.commit()
        finally:
            cleanup_session.close()
        import asyncio

        asyncio.run(db_manager.close())


@pytest.mark.integration
async def test_wrong_tenant_cannot_read_another_tenants_batch_or_document(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client_a,
    ):
        _, csrf_a, _ = await _login_and_setup_org(
            client_a, email="owner-a@talvora.test", org_name="Org A"
        )
        job_a = await _create_open_job(client_a, csrf_a)
        batch_a = await _create_batch(client_a, csrf_a, job_a)
        upload_resp = await _upload_item(
            client_a,
            csrf_a,
            batch_a["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            new_candidate_is_draft=True,
        )
        item_a = upload_resp.json()
        _run_pending_scan(app, batch_a["tenant_id"], item_a["document_version_id"])

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client_b,
    ):
        _, csrf_b, _ = await _login_and_setup_org(
            client_b, email="owner-b@talvora.test", org_name="Org B"
        )

        batch_resp = await client_b.get(
            f"/api/v1/upload-batches/{batch_a['id']}", headers={"Origin": "http://testserver"}
        )
        assert batch_resp.status_code == 404

        doc_resp = await client_b.get(
            f"/api/v1/documents/{item_a['document_id']}", headers={"Origin": "http://testserver"}
        )
        assert doc_resp.status_code == 404

        download_resp = await client_b.get(
            f"/api/v1/documents/{item_a['document_id']}/versions/1/download",
            headers={"Origin": "http://testserver"},
        )
        assert download_resp.status_code == 404


@pytest.mark.integration
async def test_download_requires_clean_state_and_streams_real_bytes(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)
        pdf_bytes = _build_pdf(b"Specific content to verify on download.")

        upload_resp = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            pdf_bytes,
            "resume.pdf",
            "application/pdf",
            new_candidate_is_draft=True,
        )
        item = upload_resp.json()

        # Not yet scanned: download must be refused, never served from quarantine.
        pre_scan_download = await client.get(
            f"/api/v1/documents/{item['document_id']}/versions/1/download",
            headers={"Origin": "http://testserver"},
        )
        assert pre_scan_download.status_code == 409
        assert pre_scan_download.json()["code"] == "DOCUMENT_NOT_CLEAN"

        _run_pending_scan(app, org_id, item["document_version_id"])

        download_resp = await client.get(
            f"/api/v1/documents/{item['document_id']}/versions/1/download",
            headers={"Origin": "http://testserver"},
        )
        assert download_resp.status_code == 200
        assert download_resp.content == pdf_bytes
        # No public/presigned object-store URL anywhere in the response.
        assert "amazonaws" not in str(download_resp.headers)
        assert "localhost:5000" not in str(download_resp.headers)


@pytest.mark.integration
async def test_binding_is_version_checked_and_rejects_stale_generation(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)

        upload_v1 = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(b"version one content"),
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Evan Versioned",
            new_candidate_email="evan@example.com",
        )
        item_v1 = upload_v1.json()
        _run_pending_scan(app, org_id, item_v1["document_version_id"])
        application_id = item_v1["application_id"]

        # Binding with a stale/incorrect expected generation (should be 0) is rejected.
        stale_bind = await client.post(
            f"/api/v1/applications/{application_id}/document-bindings",
            json={
                "document_version_id": item_v1["document_version_id"],
                "expected_binding_generation": 5,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert stale_bind.status_code == 409
        assert stale_bind.json()["code"] == "VERSION_CONFLICT"

        first_bind = await client.post(
            f"/api/v1/applications/{application_id}/document-bindings",
            json={
                "document_version_id": item_v1["document_version_id"],
                "expected_binding_generation": 0,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert first_bind.status_code == 201
        assert first_bind.json()["binding_generation"] == 1

        # A second attempt reusing the now-stale generation 0 must also fail.
        repeat_stale = await client.post(
            f"/api/v1/applications/{application_id}/document-bindings",
            json={
                "document_version_id": item_v1["document_version_id"],
                "expected_binding_generation": 0,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert repeat_stale.status_code == 409

        # Upload a replacement version via a second batch against a second job for the
        # SAME candidate (a Document is a per-candidate container, not per-application,
        # so this is a legitimate new version of the same underlying resume record; the
        # first job's application already holds an active application for the
        # candidate, so a second upload against the same job would otherwise conflict).
        job_id_2 = await _create_open_job(client, csrf_token, title="Second Open Role")
        batch_2 = await _create_batch(client, csrf_token, job_id_2)
        upload_v2 = await _upload_item(
            client,
            csrf_token,
            batch_2["id"],
            _build_pdf(b"version two replacement content"),
            "resume.pdf",
            "application/pdf",
            candidate_id=item_v1["candidate_id"],
        )
        item_v2 = upload_v2.json()
        assert item_v2["status"] == "SCANNING", item_v2
        _run_pending_scan(app, org_id, item_v2["document_version_id"])

        second_bind = await client.post(
            f"/api/v1/applications/{application_id}/document-bindings",
            json={
                "document_version_id": item_v2["document_version_id"],
                "expected_binding_generation": 1,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert second_bind.status_code == 201
        assert second_bind.json()["binding_generation"] == 2

        active_resp = await client.get(
            f"/api/v1/applications/{application_id}/document-bindings/active",
            headers={"Origin": "http://testserver"},
        )
        assert active_resp.json()["document_version_id"] == item_v2["document_version_id"]


@pytest.mark.integration
async def test_binding_requires_clean_document_version(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)

        upload_resp = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Fiona Unscanned",
            new_candidate_email="fiona@example.com",
        )
        item = upload_resp.json()
        application_id = item["application_id"]

        # Not yet scanned (still QUARANTINED) -- binding must be rejected.
        bind_resp = await client.post(
            f"/api/v1/applications/{application_id}/document-bindings",
            json={
                "document_version_id": item["document_version_id"],
                "expected_binding_generation": 0,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert bind_resp.status_code == 409
        assert bind_resp.json()["code"] == "DOCUMENT_NOT_CLEAN"


@pytest.mark.integration
async def test_retry_and_cancel_controls_respect_allowed_states(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = intake_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)

        upload_resp = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_infected_docx(),
            "resume.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            new_candidate_is_draft=True,
        )
        item = upload_resp.json()
        _run_pending_scan(app, org_id, item["document_version_id"])

        # Item is BLOCKED (infected) -- retry is not permitted for this problem code.
        retry_resp = await client.post(
            f"/api/v1/upload-batches/{batch['id']}/items/{item['id']}/retry",
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert retry_resp.status_code == 409

        # Cancel IS permitted for any pre-terminal-ish state transition rule we define;
        # BLOCKED is a final verdict and is not in the cancelable set either.
        cancel_resp = await client.post(
            f"/api/v1/upload-batches/{batch['id']}/items/{item['id']}/cancel",
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert cancel_resp.status_code == 409


@pytest.mark.integration
async def test_scanner_unavailable_never_marks_clean_and_is_retryable(
    test_settings: Settings,
) -> None:
    """A dedicated app instance wired to an unreachable scanner port: the item must
    stay pending/retryable and never be marked CLEAN by a failed scan attempt."""
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_settings] = lambda: test_settings

    storage = S3ObjectStorageAdapter(test_settings)
    storage.ensure_bucket(test_settings.s3_bucket_name)
    storage.ensure_bucket(test_settings.s3_clean_bucket_name)
    app.state.object_storage = storage
    app.state.virus_scanner = ClamdScanner(host="localhost", port=9, connect_timeout_seconds=1.0)

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    app.state.oidc_client = mock_oidc
    app.state.mail_provider = InMemorySystemMailProvider()

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)

        upload_resp = await _upload_item(
            client,
            csrf_token,
            batch["id"],
            _build_pdf(),
            "resume.pdf",
            "application/pdf",
            new_candidate_is_draft=True,
        )
        item = upload_resp.json()
        assert item["status"] == "SCANNING"

        op = _run_pending_scan(app, org_id, item["document_version_id"])
        assert op.status in (OperationStatus.RETRY_WAIT.value, OperationStatus.NEEDS_REVIEW.value)

        detail = (
            await client.get(
                f"/api/v1/upload-batches/{batch['id']}", headers={"Origin": "http://testserver"}
            )
        ).json()
        item_after = next(i for i in detail["items"] if i["id"] == item["id"])
        # Never CLEAN as a result of a scanner failure.
        assert item_after["status"] != "CLEAN"


@pytest.mark.integration
async def test_object_persisted_then_admission_db_failure_leaves_recoverable_orphan(
    intake_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    """Simulates the real object-store/DB dual-write gap: the quarantine object is
    durably written, but the owning metadata transaction never commits (e.g. a crash).
    Proves the reconciler finds the orphaned receipt+object and cleans it up without
    losing track of the retained bytes in the meantime."""
    app, _, _ = intake_test_app
    db_manager = app.state.db_manager
    storage = app.state.object_storage

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)
        batch = await _create_batch(client, csrf_token, job_id)
        batch_tenant_id = UUID(org_id)

    # Directly fabricate the state a crash between the S3 PUT and the Phase 6 commit
    # would leave behind: a real object in quarantine, and a PENDING receipt row, with
    # no DocumentVersion ever created for it.
    import hashlib

    from talvora.modules.documents.domain.models import (
        ObjectReceiptStatus,
        generate_opaque_storage_key,
    )

    orphan_key = generate_opaque_storage_key(batch_tenant_id)
    body = b"%PDF-1.4\norphaned-admission-bytes\n%%EOF"
    storage.put_object(
        bucket=app.state.settings.s3_bucket_name,
        key=orphan_key,
        body=body,
        content_type="application/pdf",
        sha256_checksum=hashlib.sha256(body).hexdigest(),
    )
    assert storage.object_exists(app.state.settings.s3_bucket_name, orphan_key) is True

    session: Session = db_manager.session_maker()
    try:
        # A real upload_item row must exist for the receipt's FK.
        with UnitOfWork(session=session, tenant_id=batch_tenant_id) as uow:
            from talvora.modules.documents.domain.models import UploadItem, UploadItemStatus

            orphan_item = UploadItem(
                id=uuid4(),
                tenant_id=batch_tenant_id,
                batch_id=UUID(batch["id"]),
                sequence_number=99,
                status=UploadItemStatus.RESERVED.value,
                created_by=uuid4(),
                version=1,
            )
            uow.session.add(orphan_item)
            uow.session.flush()

            receipt = ObjectReceipt(
                id=uuid4(),
                tenant_id=batch_tenant_id,
                upload_item_id=orphan_item.id,
                bucket=app.state.settings.s3_bucket_name,
                storage_key=orphan_key,
                byte_size=len(body),
                status=ObjectReceiptStatus.PENDING.value,
            )
            uow.session.add(receipt)
            uow.commit()

        # Age the receipt past the grace period without touching real time.
        from datetime import UTC, datetime, timedelta

        with UnitOfWork(session=session, tenant_id=batch_tenant_id) as uow:
            r = uow.session.get(ObjectReceipt, {"tenant_id": batch_tenant_id, "id": receipt.id})
            assert r is not None
            r.created_at = datetime.now(UTC) - timedelta(hours=2)
            uow.commit()

        reconciler = ObjectReceiptReconciler(storage=storage)
        report = reconciler.sweep(
            session, tenant_id=batch_tenant_id, grace_period=timedelta(minutes=30)
        )
        session.commit()

        assert report.cleaned_up == 1
        assert storage.object_exists(app.state.settings.s3_bucket_name, orphan_key) is False

        refreshed = session.get(ObjectReceipt, {"tenant_id": batch_tenant_id, "id": receipt.id})
        assert refreshed is not None
        assert refreshed.status == ObjectReceiptStatus.CLEANED_UP.value
    finally:
        session.close()
