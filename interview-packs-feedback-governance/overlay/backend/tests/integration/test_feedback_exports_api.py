"""End-to-end integration & security tests for Blind Feedback and Controlled Exports (P16)."""

import hashlib
import io
import urllib.parse
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import docx
import pypdf
import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from moto.server import ThreadedMotoServer

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.documents.application.storage import S3ObjectStorageAdapter
from talvora.modules.documents.domain.models import (
    ApplicationDocument,
    Document,
    DocumentVersion,
)
from talvora.modules.evidence.domain.models import (
    EvidenceFragment,
    ExtractionRun,
    ExtractionRunStatus,
    ExtractionTriggerKind,
    SourceLocatorType,
    SourceSegment,
)
from talvora.modules.facts.domain.models import (
    CandidateFactEvidence,
    CandidateFactPolarity,
    CandidateFactRevision,
    CandidateFactStatus,
    CandidateFactType,
)
from talvora.modules.feedback.domain.models import EXPORT_REVOCATION_DISCLOSURE
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.interview_packs.application.pack_worker import PackGenerationWorker
from talvora.modules.jobs.domain.models import JobAssignment, JobRole
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base


@pytest.fixture(scope="module")
def moto_s3_endpoint() -> Generator[str]:
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


@pytest.fixture
def feedback_app(
    test_settings: Settings, moto_s3_endpoint: str
) -> tuple[Any, FrozenClock, S3ObjectStorageAdapter]:
    settings = test_settings.model_copy(update={"moto_endpoint_url": moto_s3_endpoint})
    app = create_app(settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    storage = S3ObjectStorageAdapter(settings)
    storage.ensure_bucket(settings.s3_bucket_name)
    storage.ensure_bucket(settings.s3_clean_bucket_name)
    app.state.object_storage = storage

    clock = FrozenClock(fixed_time=datetime(2026, 11, 6, 12, 0, tzinfo=UTC))
    app.state.clock = clock
    app.state.oidc_client = MockOidcClient(
        issuer="https://idp.talvora.test", client_id="test-client-id"
    )
    app.state.mail_provider = InMemorySystemMailProvider()
    return app, clock, storage


async def _login_user(client: AsyncClient) -> tuple[str, str]:
    resp_login = await client.get("/api/v1/auth/login")
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(resp_login.json()["authorization_url"]).query
    )["state"][0]
    code = f"mock_code_{state[:16]}"
    await client.get(f"/api/v1/auth/callback?code={code}&state={state}", follow_redirects=False)
    csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    me = (await client.get("/api/v1/auth/me")).json()
    return me["user_id"], csrf


async def _setup_org(client: AsyncClient, csrf: str, name: str = "Talvora Labs") -> tuple[str, str]:
    org_resp = await client.post(
        "/api/v1/organizations",
        json={"name": name, "slug": f"org-{uuid4().hex[:8]}"},
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert org_resp.status_code == 200, org_resp.text
    org_id = org_resp.json()["id"]
    sel = await client.post(
        f"/api/v1/organizations/{org_id}/select",
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert sel.status_code == 200
    csrf2 = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    return org_id, csrf2


async def _create_published_job(client: AsyncClient, csrf: str) -> str:
    job_resp = await client.post(
        "/api/v1/jobs",
        json={
            "title": "Principal Platform Engineer — München",
            "description": "Design resilient multi-tenant PostgreSQL and Python distributed services.",
            "department": "Core Infrastructure",
            "location": "Bengaluru",
            "employment_type": "FULL_TIME",
            "draft_requirements": [
                {
                    "criterion_type": "SKILL_PRESENT",
                    "is_required": True,
                    "source_requirement_text": "Strong production PostgreSQL and Python skills",
                    "relevance_rationale": "Core stack for transactional platform services",
                    "requires_human_review": False,
                    "configuration": {
                        "criterion_type": "SKILL_PRESENT",
                        "skill_name": "PostgreSQL",
                    },
                    "order_index": 0,
                }
            ],
        },
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert job_resp.status_code == 201, job_resp.text
    job_id: str = job_resp.json()["id"]
    pub_resp = await client.post(
        f"/api/v1/jobs/{job_id}/publish",
        json={"expected_version": 1},
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert pub_resp.status_code == 200, pub_resp.text
    return job_id


def _seed_unicode_candidate_and_evidence(
    app: Any, tenant_id: UUID, job_id: UUID, actor_user_id: UUID
) -> tuple[UUID, UUID]:
    skill_quote = "Designed PostgreSQL transactional outbox handling 45k writes/sec in Python."
    session = app.state.db_manager.session_maker()
    try:
        now = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)
        cand = Candidate(
            id=uuid4(),
            tenant_id=tenant_id,
            display_name="Zofia Żółć-Nguyễn (東京)",
            created_by=actor_user_id,
        )
        session.add(cand)
        session.flush()

        application = Application(
            id=uuid4(),
            tenant_id=tenant_id,
            job_id=job_id,
            candidate_id=cand.id,
            stage=ApplicationStage.INTERVIEWING.value,
            created_by=actor_user_id,
        )
        session.add(application)
        session.flush()

        doc = Document(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=cand.id,
            created_by=actor_user_id,
        )
        session.add(doc)
        session.flush()

        doc_ver = DocumentVersion(
            id=uuid4(),
            tenant_id=tenant_id,
            document_id=doc.id,
            version_number=1,
            media_type="application/pdf",
            byte_size=1024,
            sha256_checksum="a" * 64,
            storage_bucket="talvora-clean-local",
            storage_key=f"{tenant_id}/{uuid4()}",
            storage_state="CLEAN",
            source_actor_id=actor_user_id,
        )
        session.add(doc_ver)
        session.flush()

        session.add(
            ApplicationDocument(
                id=uuid4(),
                tenant_id=tenant_id,
                application_id=application.id,
                document_id=doc.id,
                document_version_id=doc_ver.id,
                binding_generation=1,
                status="ACTIVE",
                bound_by=actor_user_id,
            )
        )
        ext_run = ExtractionRun(
            id=uuid4(),
            tenant_id=tenant_id,
            document_id=doc.id,
            document_version_id=doc_ver.id,
            revision_number=1,
            status=ExtractionRunStatus.SUCCEEDED.value,
            triggered_by=ExtractionTriggerKind.SYSTEM_SCAN_CLEAN.value,
            extractor_version="pypdf-test-v1",
            normalization_version="norm-v1",
            media_type="application/pdf",
            is_complete=True,
            total_pages=1,
            processed_pages=1,
            ocr_pages_used=0,
            total_characters=len(skill_quote),
            warnings=[],
            started_at=now,
            completed_at=now,
        )
        session.add(ext_run)
        session.flush()

        seg = SourceSegment(
            id=uuid4(),
            tenant_id=tenant_id,
            extraction_run_id=ext_run.id,
            document_version_id=doc_ver.id,
            segment_index=0,
            locator_type=SourceLocatorType.PDF_PAGE.value,
            page_number=1,
            docx_path=None,
            extraction_method="DIGITAL_TEXT",
            text=skill_quote,
            canonical_text_sha256="b" * 64,
            char_count=len(skill_quote),
            ocr_mean_confidence=None,
            warnings=[],
        )
        session.add(seg)
        session.flush()

        frag = EvidenceFragment(
            id=uuid4(),
            tenant_id=tenant_id,
            extraction_run_id=ext_run.id,
            document_version_id=doc_ver.id,
            source_segment_id=seg.id,
            start_offset=0,
            end_offset=len(skill_quote),
            quote=skill_quote,
        )
        session.add(frag)
        session.flush()

        fact = CandidateFactRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=cand.id,
            fact_key=uuid4(),
            revision_number=1,
            fact_type=CandidateFactType.SKILL.value,
            value="PostgreSQL",
            extracted_value="PostgreSQL",
            polarity=CandidateFactPolarity.AFFIRMED.value,
            extracted_polarity=CandidateFactPolarity.AFFIRMED.value,
            status=CandidateFactStatus.VERIFIED.value,
            review_note="Verified against PDF page 1.",
            extraction_run_id=ext_run.id,
            created_by=actor_user_id,
        )
        session.add(fact)
        session.flush()
        session.add(
            CandidateFactEvidence(
                id=uuid4(),
                tenant_id=tenant_id,
                fact_revision_id=fact.id,
                evidence_fragment_id=frag.id,
            )
        )
        session.commit()
        return cand.id, application.id
    finally:
        session.close()


def _framework_payload(job_id: str, slug_prefix: str = "fw") -> dict[str, Any]:
    anchors = {
        "1": "Leaves data integrity invariants unaddressed or omits failure handling.",
        "2": "Addresses basic happy-path behavior with guidance needed on concurrency.",
        "3": "Demonstrates clear transaction boundaries, indexing, and failure recovery.",
        "4": "Proactively identifies multi-tenant isolation, idempotency, and operational trade-offs.",
        "NOT_ASSESSED": "Not assessed in this interview session.",
    }
    return {
        "name": f"Technical Rubric {slug_prefix}",
        "slug": f"{slug_prefix}-{uuid4().hex[:6]}",
        "stage": "TECHNICAL",
        "seniority_band": "SENIOR",
        "job_id": job_id,
        "summary": "Observable technical and collaboration competencies.",
        "behavioral_scale_guide": "Rate 1-4 strictly from observed evidence; use NOT_ASSESSED when uncovered.",
        "competencies": [
            {
                "code": "DATA_INTEGRITY",
                "name": "Transactional & Multi-Tenant Integrity",
                "definition": "Designs schema constraints, concurrency controls, and safe migrations.",
                "observation_required": True,
                "rubric_anchors": anchors,
                "display_order": 0,
            },
            {
                "code": "EXECUTION_COLLAB",
                "name": "Technical Execution & Stakeholder Alignment",
                "definition": "Communicates trade-offs and adapts cleanly to changing constraints.",
                "observation_required": True,
                "rubric_anchors": anchors,
                "display_order": 1,
            },
        ],
    }


@pytest.mark.integration
async def test_blind_feedback_amendments_offboarding_and_controlled_exports_e2e(
    feedback_app: tuple[Any, FrozenClock, S3ObjectStorageAdapter],
) -> None:
    app, clock, storage = feedback_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as owner_client,
        AsyncClient(transport=transport, base_url="http://testserver") as int1_client,
        AsyncClient(transport=transport, base_url="http://testserver") as int2_client,
        AsyncClient(transport=transport, base_url="http://testserver") as unassigned_int_client,
        AsyncClient(transport=transport, base_url="http://testserver") as tenant2_client,
    ):
        owner_uid, owner_csrf = await _login_user(owner_client)
        org_id, owner_csrf = await _setup_org(owner_client, owner_csrf, "Talvora Primary")
        job_id = await _create_published_job(owner_client, owner_csrf)

        # Second tenant for cross-tenant 404 verification
        _, t2_csrf = await _login_user(tenant2_client)
        _, t2_csrf = await _setup_org(tenant2_client, t2_csrf, "External Tenant")

        # Register 3 interviewers in org 1 (int1 & int2 assigned to interview; unassigned_int not on interview)
        int1_uid, _ = await _login_user(int1_client)
        int2_uid, _ = await _login_user(int2_client)
        unassigned_uid, _ = await _login_user(unassigned_int_client)

        session = app.state.db_manager.session_maker()
        try:
            for uid in (int1_uid, int2_uid, unassigned_uid):
                session.add(
                    Membership(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        user_id=UUID(uid),
                        role="INTERVIEWER",
                        status="ACTIVE",
                    )
                )
                session.add(
                    JobAssignment(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        job_id=UUID(job_id),
                        user_id=UUID(uid),
                        role=JobRole.INTERVIEWER.value,
                        assigned_by=UUID(owner_uid),
                    )
                )
            session.commit()
        finally:
            session.close()

        int_csrfs: dict[str, str] = {}
        for key, c in (
            ("int1", int1_client),
            ("int2", int2_client),
            ("unassigned", unassigned_int_client),
        ):
            tmp_csrf = (await c.get("/api/v1/auth/csrf")).json()["csrf_token"]
            sel = await c.post(
                f"/api/v1/organizations/{org_id}/select",
                headers={"X-CSRF-Token": tmp_csrf, "Origin": "http://testserver"},
            )
            assert sel.status_code == 200
            int_csrfs[key] = (await c.get("/api/v1/auth/csrf")).json()["csrf_token"]

        _, app_id = _seed_unicode_candidate_and_evidence(
            app, UUID(org_id), UUID(job_id), UUID(owner_uid)
        )

        # Create & publish primary framework + a second framework (to test framework competency mismatch 422)
        fw_resp = await owner_client.post(
            "/api/v1/competency-frameworks",
            json=_framework_payload(job_id, "primary"),
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert fw_resp.status_code == 201
        fw_id = fw_resp.json()["framework"]["id"]
        pub_fw = await owner_client.post(
            f"/api/v1/competency-frameworks/{fw_id}/versions/1/publish",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert pub_fw.status_code == 200
        comps = pub_fw.json()["competencies"]
        comp1_id = comps[0]["id"]
        comp2_id = comps[1]["id"]

        other_fw_resp = await owner_client.post(
            "/api/v1/competency-frameworks",
            json=_framework_payload(job_id, "foreign"),
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert other_fw_resp.status_code == 201
        foreign_comp_id = other_fw_resp.json()["competencies"][0]["id"]

        # Book interview for int1 & int2 and generate pack
        interview_id = uuid4()
        ctx_resp = await owner_client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json={
                "application_id": str(app_id),
                "stage": "TECHNICAL",
                "seniority_level": "SENIOR",
                "duration_minutes": 60,
                "schedule_revision": 1,
                "status": "BOOKED",
                "participant_user_ids": [int1_uid, int2_uid],
                "booking_event_key": "gcal-p16-001",
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert ctx_resp.status_code == 200
        op_id = ctx_resp.json()["admitted_operation_id"]

        session = app.state.db_manager.session_maker()
        try:
            PackGenerationWorker().execute_operation(session, UUID(org_id), UUID(op_id))
        finally:
            session.close()

        packs = (await owner_client.get(f"/api/v1/interviews/{interview_id}/packs")).json()
        draft_pack_id = packs[0]["id"]

        # Exporting an UNPUBLISHED draft pack must fail with 409 Conflict
        draft_export_attempt = await owner_client.post(
            f"/api/v1/pack-revisions/{draft_pack_id}/exports",
            json={"format": "PDF", "content_scope": "INTERVIEWER_PACK"},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert draft_export_attempt.status_code == 409

        # Publish the pack
        pub_pack = await owner_client.post(
            f"/api/v1/pack-revisions/{draft_pack_id}/publish",
            json={"expected_version": packs[0]["version"]},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert pub_pack.status_code == 200, pub_pack.text
        published_pack_id = pub_pack.json()["id"]

        # ------------------------------------------------------------------
        # 1. Wrong interviewer (not on interview panel) -> 403 Forbidden
        # ------------------------------------------------------------------
        wrong_int_get = await unassigned_int_client.get(
            f"/api/v1/interviews/{interview_id}/feedback/me"
        )
        assert wrong_int_get.status_code == 403

        wrong_int_export = await unassigned_int_client.post(
            f"/api/v1/pack-revisions/{published_pack_id}/exports",
            json={"format": "DOCX", "content_scope": "INTERVIEWER_PACK"},
            headers={"X-CSRF-Token": int_csrfs["unassigned"], "Origin": "http://testserver"},
        )
        assert wrong_int_export.status_code == 403

        # ------------------------------------------------------------------
        # 2. Draft saving, validation, framework mismatch, & version race
        # ------------------------------------------------------------------
        me_init = await int1_client.get(f"/api/v1/interviews/{interview_id}/feedback/me")
        assert me_init.status_code == 200
        assert me_init.json()["status"] == "DRAFT"
        v_init = me_init.json()["version"]

        # Rating '0' rejected (missing != zero)
        zero_rating = await int1_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "expected_version": v_init,
                "overall_observations": "Draft notes",
                "ratings": [
                    {
                        "competency_id": comp1_id,
                        "rating": "0",
                        "observation": "Should reject numeric zero",
                    }
                ],
            },
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert zero_rating.status_code == 422

        # Foreign competency_id from another framework rejected (422)
        mismatch_resp = await int1_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "expected_version": v_init,
                "overall_observations": "Draft notes",
                "ratings": [
                    {
                        "competency_id": foreign_comp_id,
                        "rating": "3",
                        "observation": "Valid text, wrong framework competency.",
                    }
                ],
            },
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert mismatch_resp.status_code == 422

        # Renderer/script injection in observation rejected (422)
        inj_resp = await int1_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "expected_version": v_init,
                "overall_observations": '<script>fetch("https://evil.example")</script>',
                "ratings": [],
            },
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert inj_resp.status_code == 422

        # Valid draft save with anchor 4 + NOT_ASSESSED
        draft_saved = await int1_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "expected_version": v_init,
                "overall_observations": "Strong PostgreSQL outbox fencing design by Zofia Żółć.",
                "human_recommendation": "ADVANCE_DISCUSSION",
                "ratings": [
                    {
                        "competency_id": comp1_id,
                        "rating": "4",
                        "observation": "Explained WAL lag fencing and idempotent consumer deduplication.",
                    },
                    {
                        "competency_id": comp2_id,
                        "rating": "NOT_ASSESSED",
                        "observation": "",
                    },
                ],
            },
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert draft_saved.status_code == 200, draft_saved.text
        v_after_draft = draft_saved.json()["version"]

        # Stale expected_version -> 409 Conflict
        stale_put = await int1_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "expected_version": v_init,
                "overall_observations": "Stale write",
                "ratings": [],
            },
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert stale_put.status_code == 409

        # int2 saves an unsubmitted private draft (must stay invisible even to owner's draft fields)
        int2_draft = await int2_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "overall_observations": "PRIVATE_UNSUBMITTED_DRAFT_BY_INT2",
                "human_recommendation": "HOLD_NEEDS_MORE_SIGNAL",
                "ratings": [
                    {
                        "competency_id": comp1_id,
                        "rating": "3",
                        "observation": "Good transaction boundaries.",
                    },
                    {
                        "competency_id": comp2_id,
                        "rating": "3",
                        "observation": "Clear trade-off discussion.",
                    },
                ],
            },
            headers={"X-CSRF-Token": int_csrfs["int2"], "Origin": "http://testserver"},
        )
        assert int2_draft.status_code == 200

        # ------------------------------------------------------------------
        # 3. Submit feedback + Idempotency + Blind-feedback isolation
        # ------------------------------------------------------------------
        sub1 = await int1_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/submit",
            json={"expected_version": v_after_draft},
            headers={
                "X-CSRF-Token": int_csrfs["int1"],
                "Origin": "http://testserver",
                "Idempotency-Key": "submit-int1-v1",
            },
        )
        assert sub1.status_code == 200, sub1.text
        assert sub1.json()["status"] == "SUBMITTED"
        assert sub1.json()["latest_submitted_revision"] == 1
        assert len(sub1.json()["revisions"]) == 1
        ratings_rev1 = {
            r["competency_id"]: r["rating"] for r in sub1.json()["revisions"][0]["ratings"]
        }
        assert ratings_rev1[comp1_id] == "4"
        assert ratings_rev1[comp2_id] == "NOT_ASSESSED"

        # Replay with same Idempotency-Key returns exact 200 response
        sub1_replay = await int1_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/submit",
            json={"expected_version": v_after_draft},
            headers={
                "X-CSRF-Token": int_csrfs["int1"],
                "Origin": "http://testserver",
                "Idempotency-Key": "submit-int1-v1",
            },
        )
        assert sub1_replay.status_code == 200
        assert sub1_replay.json()["id"] == sub1.json()["id"]

        # Duplicate submit WITHOUT same idempotency key -> 409 Conflict (must use /amend)
        dup_sub = await int1_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/submit",
            json={"expected_version": sub1.json()["version"]},
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert dup_sub.status_code == 409

        # BLIND FEEDBACK CHECK 1: int2 has NOT submitted yet -> CANNOT see int1's feedback!
        int2_list_before_submit = await int2_client.get(
            f"/api/v1/interviews/{interview_id}/feedback"
        )
        assert int2_list_before_submit.status_code == 200
        visible_to_int2 = int2_list_before_submit.json()
        assert all(item["interviewer_user_id"] == int2_uid for item in visible_to_int2)
        assert not any(item["interviewer_user_id"] == int1_uid for item in visible_to_int2)

        # Owner/Recruiter sees int1's submitted revision, and NEVER sees int2's unsubmitted draft text
        owner_list = await owner_client.get(f"/api/v1/interviews/{interview_id}/feedback")
        assert owner_list.status_code == 200
        assert "PRIVATE_UNSUBMITTED_DRAFT_BY_INT2" not in owner_list.text

        # Now int2 submits their feedback
        int2_sub = await int2_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/submit",
            json={"expected_version": int2_draft.json()["version"]},
            headers={"X-CSRF-Token": int_csrfs["int2"], "Origin": "http://testserver"},
        )
        assert int2_sub.status_code == 200

        # Default policy: peer feedback stays hidden from peers even after submit
        int2_list_default_policy = await int2_client.get(
            f"/api/v1/interviews/{interview_id}/feedback"
        )
        assert len(int2_list_default_policy.json()) == 1
        assert int2_list_default_policy.json()[0]["interviewer_user_id"] == int2_uid

        # Enable organization_policy.reveal_peer_feedback_after_submit = True and verify configured reveal!
        session = app.state.db_manager.session_maker()
        try:
            session.execute(
                sa.text(
                    "CREATE TABLE IF NOT EXISTS organization_policy ("
                    "tenant_id TEXT PRIMARY KEY, "
                    "reveal_peer_feedback_after_submit BOOLEAN NOT NULL, "
                    "allow_blind_feedback_override BOOLEAN NOT NULL, "
                    "export_ttl_hours INTEGER NOT NULL)"
                )
            )
            session.execute(
                sa.text(
                    "INSERT INTO organization_policy "
                    "(tenant_id, reveal_peer_feedback_after_submit, allow_blind_feedback_override, export_ttl_hours) "
                    "VALUES (:tid, 1, 0, 24)"
                ),
                {"tid": org_id},
            )
            session.commit()
        finally:
            session.close()

        int2_list_revealed = await int2_client.get(f"/api/v1/interviews/{interview_id}/feedback")
        assert int2_list_revealed.status_code == 200
        assert {x["interviewer_user_id"] for x in int2_list_revealed.json()} == {
            int1_uid,
            int2_uid,
        }

        # ------------------------------------------------------------------
        # 4. Explicit Amendment creates Revision #2 & preserves Revision #1
        # ------------------------------------------------------------------
        amend_resp = await int1_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/amend",
            json={
                "expected_version": sub1.json()["version"],
                "amendment_reason": "Added concrete WAL failover latency numbers from my interview notes.",
                "overall_observations": "Zofia Żółć demonstrated sub-2s WAL fencing failover and zero lost events.",
                "human_recommendation": "ADVANCE_DISCUSSION",
                "ratings": [
                    {
                        "competency_id": comp1_id,
                        "rating": "4",
                        "observation": "Sub-2s WAL fencing failover with deterministic idempotency keys.",
                    },
                    {
                        "competency_id": comp2_id,
                        "rating": "NOT_ASSESSED",
                        "observation": "",
                    },
                ],
            },
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert amend_resp.status_code == 200, amend_resp.text
        amended_body = amend_resp.json()
        assert amended_body["status"] == "AMENDED"
        assert amended_body["latest_submitted_revision"] == 2
        assert len(amended_body["revisions"]) == 2
        assert amended_body["revisions"][0]["revision_number"] == 1
        assert amended_body["revisions"][1]["revision_number"] == 2
        assert (
            amended_body["revisions"][1]["prior_revision_id"] == amended_body["revisions"][0]["id"]
        )

        # ------------------------------------------------------------------
        # 5. Controlled DOCX & PDF Exports on Real Moto S3 + Inspection
        # ------------------------------------------------------------------
        # Assigned interviewer CANNOT export REVIEWER_PACK_WITH_FEEDBACK -> 403
        int2_forbidden_scope = await int2_client.post(
            f"/api/v1/pack-revisions/{published_pack_id}/exports",
            json={"format": "PDF", "content_scope": "REVIEWER_PACK_WITH_FEEDBACK"},
            headers={"X-CSRF-Token": int_csrfs["int2"], "Origin": "http://testserver"},
        )
        assert int2_forbidden_scope.status_code == 403

        # Assigned interviewer CAN export INTERVIEWER_PACK (no peer feedback leaked)
        int2_pack_export = await int2_client.post(
            f"/api/v1/pack-revisions/{published_pack_id}/exports",
            json={"format": "DOCX", "content_scope": "INTERVIEWER_PACK"},
            headers={"X-CSRF-Token": int_csrfs["int2"], "Origin": "http://testserver"},
        )
        assert int2_pack_export.status_code == 201, int2_pack_export.text

        # Owner exports DOCX + PDF with REVIEWER_PACK_WITH_FEEDBACK
        docx_exp = await owner_client.post(
            f"/api/v1/pack-revisions/{published_pack_id}/exports",
            json={"format": "DOCX", "content_scope": "REVIEWER_PACK_WITH_FEEDBACK"},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert docx_exp.status_code == 201, docx_exp.text
        docx_id = docx_exp.json()["id"]
        assert docx_exp.json()["revocation_disclosure"] == EXPORT_REVOCATION_DISCLOSURE

        pdf_exp = await owner_client.post(
            f"/api/v1/pack-revisions/{published_pack_id}/exports",
            json={"format": "PDF", "content_scope": "REVIEWER_PACK_WITH_FEEDBACK"},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert pdf_exp.status_code == 201, pdf_exp.text
        pdf_id = pdf_exp.json()["id"]

        # Download & inspect real DOCX bytes from Moto S3
        docx_dl = await owner_client.get(f"/api/v1/exports/{docx_id}/download")
        assert docx_dl.status_code == 200
        assert docx_dl.headers["Cache-Control"] == "private, no-store, max-age=0"
        assert hashlib.sha256(docx_dl.content).hexdigest() == docx_exp.json()["sha256_checksum"]
        parsed_docx = docx.Document(io.BytesIO(docx_dl.content))
        docx_all_text = (
            "\n".join(p.text for p in parsed_docx.paragraphs)
            + "\n"
            + "\n".join(c.text for t in parsed_docx.tables for r in t.rows for c in r.cells)
        )
        assert "Zofia Żółć-Nguyễn (東京)" in docx_all_text
        assert "NOT_ASSESSED" in docx_all_text
        assert "Sub-2s WAL fencing failover" in docx_all_text

        # Download & inspect real PDF bytes + rasterize via pypdfium2
        pdf_dl = await owner_client.get(f"/api/v1/exports/{pdf_id}/download")
        assert pdf_dl.status_code == 200
        assert hashlib.sha256(pdf_dl.content).hexdigest() == pdf_exp.json()["sha256_checksum"]
        pdf_reader = pypdf.PdfReader(io.BytesIO(pdf_dl.content))
        pdf_text = "\n".join(p.extract_text() or "" for p in pdf_reader.pages)
        assert "Zofia Żółć-Nguyễn" in pdf_text
        assert "NOT_ASSESSED" in pdf_text
        assert "Sub-2s WAL fencing failover" in pdf_text
        rendered_page = pdfium.PdfDocument(pdf_dl.content)[0].render(scale=1.5).to_pil()
        min_px, max_px = rendered_page.convert("L").getextrema()
        assert min_px < 80 and max_px > 240

        # Cross-tenant isolation: Tenant 2 gets 404 on metadata, download, and revoke
        assert (await tenant2_client.get(f"/api/v1/exports/{pdf_id}")).status_code == 404
        assert (await tenant2_client.get(f"/api/v1/exports/{pdf_id}/download")).status_code == 404
        assert (
            await tenant2_client.post(
                f"/api/v1/exports/{pdf_id}/revoke",
                json={"reason": "Cross-tenant attempt"},
                headers={"X-CSRF-Token": t2_csrf, "Origin": "http://testserver"},
            )
        ).status_code == 404

        # Revoke DOCX export -> deletes S3 object and returns 410 Gone on download
        rev_resp = await owner_client.post(
            f"/api/v1/exports/{docx_id}/revoke",
            json={"reason": "Superseded by updated review meeting."},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert rev_resp.status_code == 200
        assert rev_resp.json()["status"] == "REVOKED"
        assert (await owner_client.get(f"/api/v1/exports/{docx_id}/download")).status_code == 410

        # Advance FrozenClock by 25 hours -> PDF export expires (410 Gone)
        clock.set_time(clock.now() + timedelta(hours=25))
        assert (await owner_client.get(f"/api/v1/exports/{pdf_id}/download")).status_code == 410

        # ------------------------------------------------------------------
        # 6. Offboarding preserves historical authorship while blocking edits
        # ------------------------------------------------------------------
        session = app.state.db_manager.session_maker()
        try:
            m_int1 = (
                session.query(Membership)
                .filter_by(tenant_id=UUID(org_id), user_id=UUID(int1_uid))
                .one()
            )
            m_int1.status = "SUSPENDED"
            session.commit()
        finally:
            session.close()

        offboarded_amend = await int1_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/amend",
            json={
                "expected_version": amended_body["version"],
                "amendment_reason": "Should be blocked after offboarding",
                "overall_observations": "Blocked",
                "human_recommendation": "NONE",
                "ratings": [
                    {
                        "competency_id": comp1_id,
                        "rating": "3",
                        "observation": "Blocked",
                    }
                ],
            },
            headers={"X-CSRF-Token": int_csrfs["int1"], "Origin": "http://testserver"},
        )
        assert offboarded_amend.status_code == 403

        # Historical feedback by int1 remains intact for authorized reviewers!
        history_after_offboard = await owner_client.get(
            f"/api/v1/interviews/{interview_id}/feedback"
        )
        assert history_after_offboard.status_code == 200
        int1_hist = [
            f for f in history_after_offboard.json() if f["interviewer_user_id"] == int1_uid
        ]
        assert len(int1_hist) == 1
        assert int1_hist[0]["latest_submitted_revision"] == 2

        # ------------------------------------------------------------------
        # 7. Withdrawn application blocks further feedback mutations (409)
        # ------------------------------------------------------------------
        session = app.state.db_manager.session_maker()
        try:
            app_row = session.query(Application).filter_by(tenant_id=UUID(org_id), id=app_id).one()
            app_row.stage = ApplicationStage.WITHDRAWN.value
            session.commit()
        finally:
            session.close()

        withdrawn_amend = await int2_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/amend",
            json={
                "expected_version": int2_sub.json()["version"],
                "amendment_reason": "Late note after candidate withdrew.",
                "overall_observations": "Should reject mutation on withdrawn application.",
                "human_recommendation": "NONE",
                "ratings": [
                    {
                        "competency_id": comp1_id,
                        "rating": "3",
                        "observation": "Good.",
                    },
                    {
                        "competency_id": comp2_id,
                        "rating": "3",
                        "observation": "Good.",
                    },
                ],
            },
            headers={"X-CSRF-Token": int_csrfs["int2"], "Origin": "http://testserver"},
        )
        assert withdrawn_amend.status_code == 409
