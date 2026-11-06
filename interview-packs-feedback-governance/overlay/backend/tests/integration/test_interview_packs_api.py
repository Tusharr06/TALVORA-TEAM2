"""Integration & security tests for Competency Frameworks and Interview Packs (P15)."""

import urllib.parse
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.candidates.domain.models import Candidate
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
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.interview_packs.application.pack_worker import PackGenerationWorker
from talvora.modules.interview_packs.application.ports import (
    GeneratedClaimSpec,
    GeneratedPackOutput,
    GeneratedQuestionSpec,
    PackGenerationInput,
)
from talvora.modules.jobs.domain.models import JobAssignment, JobRole
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base


class InMemoryStorageStub:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}


@pytest.fixture
def packs_app(test_settings: Settings) -> tuple[Any, MockOidcClient]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    app.state.oidc_client = mock_oidc
    app.state.mail_provider = InMemorySystemMailProvider()
    return app, mock_oidc


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


async def _create_published_job(client: AsyncClient, csrf: str) -> tuple[str, str]:
    job_resp = await client.post(
        "/api/v1/jobs",
        json={
            "title": "Senior Backend Systems Engineer",
            "description": "Design and operate multi-tenant Python and PostgreSQL backend services.",
            "department": "Platform",
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
    job_id = job_resp.json()["id"]

    pub_resp = await client.post(
        f"/api/v1/jobs/{job_id}/publish",
        json={"expected_version": 1},
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert pub_resp.status_code == 200, pub_resp.text
    return job_id, pub_resp.json()["id"]


def _seed_application_with_evidence(
    app: Any,
    tenant_id: UUID,
    job_id: UUID,
    actor_user_id: UUID,
    skill_quote: str = "Built PostgreSQL transactional outbox and Python FastAPI services for 4 years.",
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    """Seeds candidate, application, clean bound document version, segment, fragment, and verified fact."""
    session = app.state.db_manager.session_maker()
    try:
        now = datetime.now(UTC)
        cand = Candidate(
            id=uuid4(),
            tenant_id=tenant_id,
            display_name="Aarav Rao",
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

        app_doc = ApplicationDocument(
            id=uuid4(),
            tenant_id=tenant_id,
            application_id=application.id,
            document_id=doc.id,
            document_version_id=doc_ver.id,
            binding_generation=1,
            status="ACTIVE",
            bound_by=actor_user_id,
        )
        session.add(app_doc)
        session.flush()

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
        return cand.id, application.id, doc_ver.id, frag.id, fact.id
    finally:
        session.close()


def _framework_payload(job_id: str) -> dict[str, Any]:
    anchors = {
        "1": "Leaves data integrity invariants unaddressed or omits failure handling.",
        "2": "Addresses basic happy-path behavior with guidance needed on concurrency.",
        "3": "Demonstrates clear transaction boundaries, indexing, and failure recovery.",
        "4": "Proactively identifies multi-tenant isolation, idempotency, and operational trade-offs.",
        "NOT_ASSESSED": "Not assessed in this interview session.",
    }
    return {
        "name": "Backend Technical Stage Framework",
        "slug": f"backend-tech-{uuid4().hex[:6]}",
        "stage": "TECHNICAL",
        "seniority_band": "SENIOR",
        "job_id": job_id,
        "summary": "Observable technical and collaboration competencies for Senior Backend Engineers.",
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
async def test_full_interview_pack_lifecycle_staleness_forking_and_interviewer_scope(
    packs_app: tuple[Any, MockOidcClient],
) -> None:
    app, _ = packs_app
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as owner_client,
        AsyncClient(transport=transport, base_url="http://testserver") as interviewer_client,
        AsyncClient(transport=transport, base_url="http://testserver") as other_interviewer_client,
    ):
        owner_uid, owner_csrf = await _login_user(owner_client)
        org_id, owner_csrf = await _setup_org(owner_client, owner_csrf)
        job_id, req_ver_id = await _create_published_job(owner_client, owner_csrf)

        # Login two interviewers and add them as INTERVIEWER members of the org
        int1_uid, _ = await _login_user(interviewer_client)
        int2_uid, _ = await _login_user(other_interviewer_client)
        session = app.state.db_manager.session_maker()
        try:
            for uid in (int1_uid, int2_uid):
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

        for c in (interviewer_client, other_interviewer_client):
            csrf_tmp = (await c.get("/api/v1/auth/csrf")).json()["csrf_token"]
            sel = await c.post(
                f"/api/v1/organizations/{org_id}/select",
                headers={"X-CSRF-Token": csrf_tmp, "Origin": "http://testserver"},
            )
            assert sel.status_code == 200

        _, app_id, doc_ver_id, frag_id, fact_id = _seed_application_with_evidence(
            app, UUID(org_id), UUID(job_id), UUID(owner_uid)
        )

        # 1. Create & publish competency framework (with idempotency check)
        fw_payload = _framework_payload(job_id)
        fw_resp = await owner_client.post(
            "/api/v1/competency-frameworks",
            json=fw_payload,
            headers={
                "X-CSRF-Token": owner_csrf,
                "Origin": "http://testserver",
                "Idempotency-Key": "fw-create-1",
            },
        )
        assert fw_resp.status_code == 201, fw_resp.text
        fw_id = fw_resp.json()["framework"]["id"]

        # Replay same idempotency key -> exact same framework
        fw_replay = await owner_client.post(
            "/api/v1/competency-frameworks",
            json=fw_payload,
            headers={
                "X-CSRF-Token": owner_csrf,
                "Origin": "http://testserver",
                "Idempotency-Key": "fw-create-1",
            },
        )
        assert fw_replay.status_code == 201
        assert fw_replay.json()["framework"]["id"] == fw_id

        pub_fw = await owner_client.post(
            f"/api/v1/competency-frameworks/{fw_id}/versions/1/publish",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert pub_fw.status_code == 200
        fw_ver_id = pub_fw.json()["version_id"]
        comp_id = pub_fw.json()["competencies"][0]["id"]

        # 2. Register booked interview context with repeated booking_event_key deduplication
        interview_id = uuid4()
        ctx_body = {
            "application_id": str(app_id),
            "stage": "TECHNICAL",
            "seniority_level": "SENIOR",
            "duration_minutes": 60,
            "schedule_revision": 1,
            "status": "BOOKED",
            "participant_user_ids": [int1_uid],
            "booking_event_key": "gcal-evt-001",
        }
        ctx1 = await owner_client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json=ctx_body,
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert ctx1.status_code == 200, ctx1.text
        op_id = ctx1.json()["admitted_operation_id"]
        assert op_id is not None

        # Duplicate booking event must NOT admit a second background operation
        ctx2 = await owner_client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json=ctx_body,
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert ctx2.status_code == 200
        assert ctx2.json()["admitted_operation_id"] is None

        # Execute the fenced background pack worker
        session = app.state.db_manager.session_maker()
        try:
            worker = PackGenerationWorker()
            done_op = worker.execute_operation(session, UUID(org_id), UUID(op_id))
            assert done_op.status == "SUCCEEDED"
        finally:
            session.close()

        packs_resp = await owner_client.get(f"/api/v1/interviews/{interview_id}/packs")
        assert packs_resp.status_code == 200
        packs = packs_resp.json()
        assert len(packs) == 1
        draft_pack = packs[0]
        assert draft_pack["status"] == "DRAFT"
        assert draft_pack["requirement_version_id"] == req_ver_id
        assert draft_pack["document_version_id"] == str(doc_ver_id)
        assert draft_pack["framework_version_id"] == fw_ver_id
        assert draft_pack["duration_minutes"] == 60
        assert draft_pack["schedule_revision"] == 1
        assert draft_pack["time_allocation"]["total_allocated_minutes"] <= 60

        # Assigned interviewer CANNOT see unpublished DRAFT packs!
        int1_before_pub = await interviewer_client.get(f"/api/v1/interviews/{interview_id}/packs")
        assert int1_before_pub.status_code == 200
        assert int1_before_pub.json() == []

        # Assigned interviewer CANNOT publish a pack (403 FORBIDDEN)
        int1_csrf = (await interviewer_client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        unauth_pub = await interviewer_client.post(
            f"/api/v1/pack-revisions/{draft_pack['id']}/publish",
            json={"expected_version": 1},
            headers={"X-CSRF-Token": int1_csrf, "Origin": "http://testserver"},
        )
        assert unauth_pub.status_code == 403

        # 3. Factual edit losing citation support:
        #    (a) Explicitly claiming RESUME_SUPPORTED with invalidated text -> 422 EVIDENCE_INVALID
        bad_edit = await owner_client.patch(
            f"/api/v1/pack-revisions/{draft_pack['id']}",
            json={
                "expected_version": draft_pack["version"],
                "claims": [
                    {
                        "section": "OVERVIEW",
                        "content_scope": "CANDIDATE_SPECIFIC",
                        "classification": "RESUME_SUPPORTED",
                        "statement": "Candidate architected global Kubernetes clusters for 12 years.",
                        "evidence_fragment_ids": [str(frag_id)],
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert bad_edit.status_code == 422
        assert bad_edit.json()["code"] == "EVIDENCE_INVALID"

        #    (b) Omitting classification on a manual edit whose text no longer matches citation
        #        automatically downgrades to HUMAN_ASSERTION_UNVERIFIED and strips stale citation!
        downgraded_edit = await owner_client.patch(
            f"/api/v1/pack-revisions/{draft_pack['id']}",
            json={
                "expected_version": draft_pack["version"],
                "claims": [
                    {
                        "section": "OVERVIEW",
                        "content_scope": "CANDIDATE_SPECIFIC",
                        "statement": "PostgreSQL",
                        "fact_revision_id": str(fact_id),
                        "evidence_fragment_ids": [str(frag_id)],
                    },
                    {
                        "section": "MANUAL_NOTE",
                        "content_scope": "CANDIDATE_SPECIFIC",
                        "statement": "Recruiter phone screen noted familiarity with incident playbooks.",
                        "evidence_fragment_ids": [str(frag_id)],
                    },
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert downgraded_edit.status_code == 200, downgraded_edit.text
        edited_claims = downgraded_edit.json()["claims"]
        assert edited_claims[0]["classification"] == "RESUME_SUPPORTED"
        assert edited_claims[0]["evidence_fragment_ids"] == [str(frag_id)]
        assert edited_claims[1]["classification"] == "HUMAN_ASSERTION_UNVERIFIED"
        assert edited_claims[1]["evidence_fragment_ids"] == []

        # 4. Publish the reviewed pack
        pub_pack_resp = await owner_client.post(
            f"/api/v1/pack-revisions/{draft_pack['id']}/publish",
            json={
                "expected_version": downgraded_edit.json()["version"],
                "review_notes": "Citations, timing, and observable rubric verified.",
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert pub_pack_resp.status_code == 200, pub_pack_resp.text
        published_pack = pub_pack_resp.json()
        assert published_pack["status"] == "PUBLISHED"

        # Assigned interviewer CAN now read the PUBLISHED pack; unassigned interviewer gets 404!
        int1_after_pub = await interviewer_client.get(f"/api/v1/interviews/{interview_id}/packs")
        assert int1_after_pub.status_code == 200
        assert len(int1_after_pub.json()) == 1
        assert int1_after_pub.json()[0]["id"] == published_pack["id"]

        int2_unassigned = await other_interviewer_client.get(
            f"/api/v1/interviews/{interview_id}/packs"
        )
        assert int2_unassigned.status_code == 404

        # 5. Editing a PUBLISHED pack forks a new DRAFT revision without mutating PUBLISHED!
        fork_resp = await owner_client.patch(
            f"/api/v1/pack-revisions/{published_pack['id']}",
            json={
                "expected_version": published_pack["version"],
                "candidate_overview": "Updated overview for follow-up technical probe.",
                "questions": [
                    {
                        "category": "TECHNICAL",
                        "content_scope": "ROLE_GENERIC",
                        "premise_classification": "ROLE_GENERIC",
                        "competency_id": comp_id,
                        "allocated_minutes": 15,
                        "prompt_text": "Explain how row-level security interacts with connection pooling.",
                        "expected_signals": ["Mentions transaction-local set_config"],
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert fork_resp.status_code == 200, fork_resp.text
        forked = fork_resp.json()
        assert forked["id"] != published_pack["id"]
        assert forked["revision_number"] == 2
        assert forked["status"] == "DRAFT"
        assert forked["based_on_revision_id"] == published_pack["id"]

        # Original published pack remains PUBLISHED and untouched; interviewer still sees rev 1
        orig_check = await owner_client.get(f"/api/v1/pack-revisions/{published_pack['id']}")
        assert orig_check.status_code == 200
        assert orig_check.json()["status"] == "PUBLISHED"
        assert orig_check.json()["revision_number"] == 1

        # 6. Regenerating a draft does NOT overwrite the published pack
        regen_resp = await owner_client.post(
            f"/api/v1/interviews/{interview_id}/pack-runs",
            json={"reason": "REGENERATE_AFTER_REVIEW"},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert regen_resp.status_code == 201
        assert regen_resp.json()["revision_number"] == 3
        assert regen_resp.json()["status"] == "DRAFT"

        # 7. Changing interview schedule/duration marks existing revisions STALE and blocks publishing stale drafts
        resched = await owner_client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json={
                "application_id": str(app_id),
                "stage": "TECHNICAL",
                "seniority_level": "SENIOR",
                "duration_minutes": 30,
                "schedule_revision": 2,
                "status": "RESCHEDULED",
                "participant_user_ids": [int1_uid],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert resched.status_code == 200

        stale_pub_check = await owner_client.get(f"/api/v1/pack-revisions/{published_pack['id']}")
        assert stale_pub_check.json()["is_stale"] is True
        assert any("schedule_revision" in r for r in stale_pub_check.json()["stale_reasons"])

        # Attempting to publish the now-stale draft revision 3 is rejected with 409!
        stale_publish_attempt = await owner_client.post(
            f"/api/v1/pack-revisions/{regen_resp.json()['id']}/publish",
            json={"expected_version": 1},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert stale_publish_attempt.status_code == 409
        assert stale_publish_attempt.json()["code"] == "VERSION_CONFLICT"


@pytest.mark.integration
async def test_pack_provider_timeout_and_unsupported_model_premise_rejected(
    packs_app: tuple[Any, MockOidcClient],
) -> None:
    app, _ = packs_app

    class TimeoutAIProvider:
        def generate_pack(self, pack_input: PackGenerationInput) -> GeneratedPackOutput:
            raise TimeoutError("simulated upstream model timeout")

    class HallucinatingAIProvider:
        def generate_pack(self, pack_input: PackGenerationInput) -> GeneratedPackOutput:
            comp_id = pack_input.competencies[0].competency_id
            frag_id = pack_input.supported_facts[0].evidence_fragment_ids[0]
            return GeneratedPackOutput(
                generator_id="bad-model",
                candidate_overview="Overview",
                candidate_evidence_summary="Summary",
                interviewer_instructions="Instructions",
                notes_guidance="Guidance",
                claims=(
                    GeneratedClaimSpec(
                        section="OVERVIEW",
                        content_scope="CANDIDATE_SPECIFIC",
                        classification="RESUME_SUPPORTED",
                        statement="Led a 200-person compiler engineering org at NASA",
                        evidence_fragment_ids=(frag_id,),
                    ),
                ),
                questions=(
                    GeneratedQuestionSpec(
                        category="TECHNICAL",
                        content_scope="ROLE_GENERIC",
                        premise_classification="ROLE_GENERIC",
                        competency_id=comp_id,
                        allocated_minutes=10,
                        prompt_text="Explain database isolation levels.",
                        premise_text=None,
                        expected_signals=("Explains read committed vs serializable",),
                    ),
                ),
            )

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        uid, csrf = await _login_user(client)
        org_id, csrf = await _setup_org(client, csrf)
        job_id, _ = await _create_published_job(client, csrf)
        _, app_id, _, _, _ = _seed_application_with_evidence(
            app, UUID(org_id), UUID(job_id), UUID(uid)
        )

        fw_resp = await client.post(
            "/api/v1/competency-frameworks",
            json=_framework_payload(job_id),
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )
        fw_id = fw_resp.json()["framework"]["id"]
        await client.post(
            f"/api/v1/competency-frameworks/{fw_id}/versions/1/publish",
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

        interview_id = uuid4()
        await client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json={
                "application_id": str(app_id),
                "stage": "TECHNICAL",
                "duration_minutes": 30,
                "schedule_revision": 1,
                "participant_user_ids": [],
            },
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

        app.state.pack_ai_provider = TimeoutAIProvider()
        timeout_resp = await client.post(
            f"/api/v1/interviews/{interview_id}/pack-runs",
            json={"reason": "TEST_TIMEOUT"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )
        assert timeout_resp.status_code == 503
        assert timeout_resp.json()["code"] == "AI_UNAVAILABLE"

        app.state.pack_ai_provider = HallucinatingAIProvider()
        halluc_resp = await client.post(
            f"/api/v1/interviews/{interview_id}/pack-runs",
            json={"reason": "TEST_HALLUCINATION"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )
        assert halluc_resp.status_code == 422
        assert halluc_resp.json()["code"] == "EVIDENCE_INVALID"
