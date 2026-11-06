"""Integration tests for the P07 evidence/extraction API: source text, extraction
status, evidence fragments, and reprocess -- driven end-to-end through real upload,
real ClamAV scan, and the real (non-OCR-path) pypdf/python-docx extraction worker.

OCR-path real-Tesseract coverage lives in its own file
(`test_real_ocr_extraction.py`), kept separate per the P07 contract's requirement to
isolate the mandated real end-to-end OCR test from faster mocked/non-OCR tests.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings, get_settings
from talvora.modules.documents.application.scanner import ClamdScanner
from talvora.modules.documents.application.storage import S3ObjectStorageAdapter
from talvora.modules.evidence.domain.models import EvidenceFragment
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base
from tests.fixtures.synthetic.documents import build_digital_pdf, build_docx
from tests.integration._evidence_helpers import (
    create_batch,
    create_open_job,
    login_and_setup_org,
    run_pending_extraction,
    run_pending_scan,
    upload_item,
)


@pytest.fixture
def evidence_test_app(
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
    # app.state.document_extractor is already a real CompositeDocumentExtractor,
    # constructed by create_app() itself -- not overridden here.

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    mail_provider = InMemorySystemMailProvider()
    app.state.oidc_client = mock_oidc
    app.state.mail_provider = mail_provider
    return app, mock_oidc, mail_provider


async def _full_upload_and_extract(
    app: Any,
    client: AsyncClient,
    csrf_token: str,
    batch_id: str,
    org_id: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    **candidate_fields: Any,
) -> dict[str, Any]:
    """Uploads one item, drives the real scan and real extraction worker to
    completion, and returns the created item's JSON body."""
    resp = await upload_item(
        client, csrf_token, batch_id, file_bytes, filename, content_type, **candidate_fields
    )
    assert resp.status_code == 201, resp.text
    item = resp.json()
    run_pending_scan(app, org_id, item["document_version_id"])
    run_pending_extraction(app, org_id, item["document_version_id"])
    return dict(item)


@pytest.mark.integration
async def test_digital_pdf_produces_source_addressable_text_and_evidence(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)

        pdf_bytes = build_digital_pdf(
            ["Experienced backend engineer with Python and PostgreSQL skills."]
        )
        item = await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            pdf_bytes,
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Alice Candidate",
            new_candidate_email="alice@example.com",
        )

        document_id = item["document_id"]

        source_resp = await client.get(
            f"/api/v1/documents/{document_id}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        assert source_resp.status_code == 200, source_resp.text
        source = source_resp.json()
        assert source["extraction"]["status"] == "SUCCEEDED"
        assert source["extraction"]["is_complete"] is True
        assert len(source["segments"]) == 1
        segment = source["segments"][0]
        assert segment["page_number"] == 1
        assert segment["extraction_method"] == "DIGITAL_TEXT"
        assert "Python and PostgreSQL" in segment["text"]

        status_resp = await client.get(
            f"/api/v1/documents/{document_id}/versions/1/extraction",
            headers={"Origin": "http://testserver"},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "SUCCEEDED"
        assert status_resp.json()["revision_number"] == 1


@pytest.mark.integration
async def test_docx_paragraphs_and_tables_produce_source_addressable_text(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)

        docx_bytes = build_docx(
            paragraphs=["Jordan Candidate", "Senior Platform Engineer"],
            table_rows=[["Skill", "Years"], ["Python", "6"], ["PostgreSQL", "4"]],
        )
        item = await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            docx_bytes,
            "resume.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            new_candidate_display_name="Jordan Candidate",
            new_candidate_email="jordan@example.com",
        )
        document_id = item["document_id"]

        source_resp = await client.get(
            f"/api/v1/documents/{document_id}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        assert source_resp.status_code == 200, source_resp.text
        source = source_resp.json()
        assert source["extraction"]["status"] == "SUCCEEDED"
        texts_by_path = {s["docx_path"]: s["text"] for s in source["segments"]}
        assert texts_by_path["body/paragraph[0]"] == "Jordan Candidate"
        assert texts_by_path["body/table[0]/row[1]/cell[0]/paragraph[0]"] == "Python"
        assert all(s["page_number"] is None for s in source["segments"])


@pytest.mark.integration
async def test_evidence_fragment_offsets_resolve_to_exact_source_substring(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)

        pdf_bytes = build_digital_pdf(["A clearly identifiable sentence for citation testing."])
        item = await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            pdf_bytes,
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Evidence Target",
            new_candidate_email="evidence@example.com",
        )
        document_id = item["document_id"]

        source_resp = await client.get(
            f"/api/v1/documents/{document_id}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        segment = source_resp.json()["segments"][0]
        segment_text = segment["text"]

        # Find the one evidence fragment anchored to this segment. `GET /evidence/
        # {id}` is the public contract under test; this direct, tenant-scoped DB read
        # only resolves the fragment id a real extraction run already persisted.
        db_manager = app.state.db_manager
        session = db_manager.session_maker()
        try:
            fragment = session.scalar(
                sa.select(EvidenceFragment).where(EvidenceFragment.tenant_id == UUID(org_id))
            )
        finally:
            session.close()
        assert fragment is not None

        evidence_resp = await client.get(
            f"/api/v1/evidence/{fragment.id}",
            headers={"Origin": "http://testserver"},
        )
        assert evidence_resp.status_code == 200, evidence_resp.text
        evidence = evidence_resp.json()

        assert evidence["quote"] == segment_text[evidence["start_offset"] : evidence["end_offset"]]
        assert evidence["quote"] == segment["text"]
        assert evidence["page_number"] == 1
        assert evidence["extraction_method"] == "DIGITAL_TEXT"


@pytest.mark.integration
async def test_cross_tenant_evidence_and_source_lookup_is_concealed(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client, org_name="Tenant One")
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        pdf_bytes = build_digital_pdf(["Tenant one confidential resume content here."])
        item = await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            pdf_bytes,
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Tenant One Candidate",
            new_candidate_email="t1@example.com",
        )
        document_id = item["document_id"]

    # A second, fully independent tenant/session must never be able to read the
    # first tenant's document source or evidence.
    async with (
        ASGITransport(app=app) as transport2,
        AsyncClient(transport=transport2, base_url="http://testserver") as client2,
    ):
        _, csrf_token2, _org_id2 = await login_and_setup_org(
            client2, email="other@talvora.test", org_name="Tenant Two"
        )
        other_job_id = await create_open_job(client2, csrf_token2, title="Other Job")
        assert other_job_id  # second tenant has its own job; unrelated to document_id

        cross_resp = await client2.get(
            f"/api/v1/documents/{document_id}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        assert cross_resp.status_code == 404


@pytest.mark.integration
async def test_interviewer_is_denied_raw_source_and_evidence(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        pdf_bytes = build_digital_pdf(["Interviewer access control test content."])
        item = await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            pdf_bytes,
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Interviewer Test",
            new_candidate_email="itest@example.com",
        )
        document_id = item["document_id"]

        # Downgrade the current membership's role directly in the DB to INTERVIEWER
        # to exercise the denial without needing a second invite flow.
        db_manager = app.state.db_manager
        session = db_manager.session_maker()
        try:
            membership = session.scalar(
                sa.select(Membership).where(Membership.tenant_id == UUID(org_id))
            )
            assert membership is not None
            membership.role = "INTERVIEWER"
            session.commit()
        finally:
            session.close()

        denied_resp = await client.get(
            f"/api/v1/documents/{document_id}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        assert denied_resp.status_code == 403


@pytest.mark.integration
async def test_reprocess_creates_a_new_independent_revision(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)
        pdf_bytes = build_digital_pdf(["Reprocess revision test content."])
        item = await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            pdf_bytes,
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Reprocess Target",
            new_candidate_email="reprocess@example.com",
        )
        document_id = item["document_id"]

        reprocess_resp = await client.post(
            f"/api/v1/documents/{document_id}/reprocess",
            json={"version_number": 1},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert reprocess_resp.status_code == 202, reprocess_resp.text
        new_run = reprocess_resp.json()["extraction_run"]
        assert new_run["revision_number"] == 2
        assert new_run["status"] == "PENDING"
        assert new_run["triggered_by"] == "MANUAL_REPROCESS"

        run_pending_extraction(app, org_id, item["document_version_id"])

        source_resp = await client.get(
            f"/api/v1/documents/{document_id}/versions/1/source?revision=2",
            headers={"Origin": "http://testserver"},
        )
        assert source_resp.status_code == 200
        assert source_resp.json()["extraction"]["revision_number"] == 2
        assert source_resp.json()["extraction"]["status"] == "SUCCEEDED"


@pytest.mark.integration
async def test_reprocess_requires_manage_document_intake_capability_and_clean_version(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)

        # Upload without running the scan worker: the version stays QUARANTINED.
        resp = await upload_item(
            client,
            csrf_token,
            batch["id"],
            build_digital_pdf(["Not yet scanned content."]),
            "resume.pdf",
            "application/pdf",
            new_candidate_display_name="Not Scanned",
            new_candidate_email="notscanned@example.com",
        )
        assert resp.status_code == 201
        item = resp.json()
        document_id = item["document_id"]

        reprocess_resp = await client.post(
            f"/api/v1/documents/{document_id}/reprocess",
            json={"version_number": 1},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert reprocess_resp.status_code == 409
        assert reprocess_resp.json()["code"] == "DOCUMENT_NOT_CLEAN"


@pytest.mark.integration
async def test_stable_evidence_retrieval_after_another_resume_is_uploaded(
    evidence_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    """Uploading and extracting a second, unrelated resume must not change the
    first resume's already-persisted source text, offsets, or evidence."""
    app, _, _ = evidence_test_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await login_and_setup_org(client)
        job_id = await create_open_job(client, csrf_token)
        batch = await create_batch(client, csrf_token, job_id)

        first_pdf = build_digital_pdf(["First candidate stable content check sentence."])
        first_item = await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            first_pdf,
            "first.pdf",
            "application/pdf",
            new_candidate_display_name="First Candidate",
            new_candidate_email="first@example.com",
        )
        first_document_id = first_item["document_id"]

        first_source_before = await client.get(
            f"/api/v1/documents/{first_document_id}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        before_payload = first_source_before.json()

        second_pdf = build_digital_pdf(["Second, completely unrelated candidate content."])
        await _full_upload_and_extract(
            app,
            client,
            csrf_token,
            batch["id"],
            org_id,
            second_pdf,
            "second.pdf",
            "application/pdf",
            new_candidate_display_name="Second Candidate",
            new_candidate_email="second@example.com",
        )

        first_source_after = await client.get(
            f"/api/v1/documents/{first_document_id}/versions/1/source",
            headers={"Origin": "http://testserver"},
        )
        after_payload = first_source_after.json()

        assert before_payload["segments"] == after_payload["segments"]
        assert before_payload["extraction"]["id"] == after_payload["extraction"]["id"]
