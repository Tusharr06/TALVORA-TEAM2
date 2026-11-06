"""End-to-end integration tests for Phase P17 Governance (Audit, Analytics, Privacy & Restore)."""

import hashlib
import shutil
import urllib.parse
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
import pytest
from botocore.client import Config as BotoConfig
from httpx import ASGITransport, AsyncClient
from moto.server import ThreadedMotoServer

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.analytics.application.service import AnalyticsService
from talvora.modules.applications.domain.models import (
    Application,
    ApplicationStage,
    StageEvent,
)
from talvora.modules.candidates.domain.models import (
    Candidate,
    CandidateContactRevision,
    CandidateContactVerification,
)
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
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.jobs.domain.models import JobAssignment, JobRole
from talvora.modules.organizations.domain.models import Membership
from talvora.modules.privacy.domain.models import CandidateTombstonedError
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork


@pytest.fixture(scope="module")
def moto_s3_endpoint() -> Generator[str]:
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


class FaultInjectableS3Wrapper:
    """Wraps real S3ObjectStorageAdapter to inject a one-shot S3 failure during purge testing."""

    def __init__(self, inner: S3ObjectStorageAdapter) -> None:
        self._inner = inner
        self.fail_deletes = False

    @property
    def _client(self) -> Any:
        if self.fail_deletes:
            raise OSError("Simulated transient S3 endpoint outage during purge")
        return self._inner._client

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        sha256_checksum: str,
    ) -> Any:
        return self._inner.put_object(bucket, key, body, content_type, sha256_checksum)

    def get_object_stream(self, bucket: str, key: str) -> Any:
        return self._inner.get_object_stream(bucket, key)

    def copy_object(self, src_bucket: str, src_key: str, dest_bucket: str, dest_key: str) -> None:
        self._inner.copy_object(src_bucket, src_key, dest_bucket, dest_key)

    def delete_object(self, bucket: str, key: str) -> None:
        if self.fail_deletes:
            raise OSError("Simulated transient S3 delete error")
        self._inner.delete_object(bucket, key)

    def object_exists(self, bucket: str, key: str) -> bool:
        return self._inner.object_exists(bucket, key)


@pytest.fixture
def governance_env(
    tmp_path: Path, test_settings: Settings, moto_s3_endpoint: str
) -> tuple[Any, Path, FrozenClock, FaultInjectableS3Wrapper, str]:
    db_path = tmp_path / "governance_p17.sqlite"
    clean_bucket = f"talvora-clean-{uuid4().hex[:8]}"
    quarantine_bucket = f"talvora-quarantine-{uuid4().hex[:8]}"
    settings = test_settings.model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{db_path}",
            "moto_endpoint_url": moto_s3_endpoint,
            "s3_bucket_name": quarantine_bucket,
            "s3_clean_bucket_name": clean_bucket,
        }
    )
    app = create_app(settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    real_s3 = S3ObjectStorageAdapter(settings)
    real_s3.ensure_bucket(quarantine_bucket)
    real_s3.ensure_bucket(clean_bucket)

    # Enable real S3 bucket versioning on clean_bucket so old object versions are tested!
    raw_boto = boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=moto_s3_endpoint,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=BotoConfig(signature_version="s3v4"),
    )
    raw_boto.put_bucket_versioning(
        Bucket=clean_bucket, VersioningConfiguration={"Status": "Enabled"}
    )

    wrapped_s3 = FaultInjectableS3Wrapper(real_s3)
    app.state.object_storage = wrapped_s3

    clock = FrozenClock(fixed_time=datetime(2026, 11, 6, 10, 0, tzinfo=UTC))
    app.state.clock = clock
    app.state.oidc_client = MockOidcClient(
        issuer="https://idp.talvora.test", client_id="test-client-id"
    )
    app.state.mail_provider = InMemorySystemMailProvider()
    return app, db_path, clock, wrapped_s3, moto_s3_endpoint


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


async def _setup_org(client: AsyncClient, csrf: str, name: str) -> tuple[str, str]:
    org_resp = await client.post(
        "/api/v1/organizations",
        json={"name": name, "slug": f"org-{uuid4().hex[:8]}"},
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert org_resp.status_code == 200, org_resp.text
    org_id: str = org_resp.json()["id"]
    sel = await client.post(
        f"/api/v1/organizations/{org_id}/select",
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert sel.status_code == 200
    csrf2 = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    return org_id, csrf2


async def _create_published_job(client: AsyncClient, csrf: str, title: str) -> str:
    job_resp = await client.post(
        "/api/v1/jobs",
        json={
            "title": title,
            "description": "Build multi-tenant distributed systems in Python and PostgreSQL.",
            "department": "Platform",
            "location": "Bengaluru",
            "employment_type": "FULL_TIME",
            "draft_requirements": [
                {
                    "criterion_type": "SKILL_PRESENT",
                    "is_required": True,
                    "source_requirement_text": "PostgreSQL expertise",
                    "relevance_rationale": "Core transactional store",
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
    assert job_resp.status_code == 201
    jid: str = job_resp.json()["id"]
    pub = await client.post(
        f"/api/v1/jobs/{jid}/publish",
        json={"expected_version": 1},
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert pub.status_code == 200
    return jid


@pytest.mark.integration
async def test_audit_analytics_policy_privacy_purge_and_backup_restore_replay_e2e(
    governance_env: tuple[Any, Path, FrozenClock, FaultInjectableS3Wrapper, str],
    tmp_path: Path,
) -> None:
    app, db_path, clock, s3_wrapper, moto_endpoint = governance_env
    clean_bucket = app.state.settings.s3_clean_bucket_name

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as owner_client,
        AsyncClient(transport=transport, base_url="http://testserver") as recruiter_client,
        AsyncClient(transport=transport, base_url="http://testserver") as interviewer_client,
        AsyncClient(transport=transport, base_url="http://testserver") as other_tenant_client,
    ):
        owner_uid, owner_csrf = await _login_user(owner_client)
        org_id, owner_csrf = await _setup_org(owner_client, owner_csrf, "Governance Org")

        _, t2_csrf = await _login_user(other_tenant_client)
        _, t2_csrf = await _setup_org(other_tenant_client, t2_csrf, "Isolated Tenant Two")

        rec_uid, _ = await _login_user(recruiter_client)
        int_uid, _ = await _login_user(interviewer_client)

        job_assigned = await _create_published_job(owner_client, owner_csrf, "Assigned Staff Role")
        job_unassigned = await _create_published_job(
            owner_client, owner_csrf, "Confidential Unassigned Role"
        )

        # Grant RECRUITER access ONLY to job_assigned, and INTERVIEWER role to interviewer_client
        session = app.state.db_manager.session_maker()
        try:
            session.add(
                Membership(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    user_id=UUID(rec_uid),
                    role="RECRUITER",
                    status="ACTIVE",
                )
            )
            session.add(
                JobAssignment(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    job_id=UUID(job_assigned),
                    user_id=UUID(rec_uid),
                    role=JobRole.RECRUITER.value,
                    assigned_by=UUID(owner_uid),
                )
            )
            session.add(
                Membership(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    user_id=UUID(int_uid),
                    role="INTERVIEWER",
                    status="ACTIVE",
                )
            )
            session.commit()
        finally:
            session.close()

        staff_csrfs: dict[str, str] = {}
        for k, client in (("rec", recruiter_client), ("int", interviewer_client)):
            c_tok = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
            sel = await client.post(
                f"/api/v1/organizations/{org_id}/select",
                headers={"X-CSRF-Token": c_tok, "Origin": "http://testserver"},
            )
            assert sel.status_code == 200
            staff_csrfs[k] = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]

        # ------------------------------------------------------------------
        # 1. Audit Query, Cursor Pagination, Scope & Export Restrictions
        # ------------------------------------------------------------------
        # Interviewer denied audit access -> 403
        assert (await interviewer_client.get("/api/v1/audit-events")).status_code == 403

        # Disallowed resource_type filter -> 422
        assert (
            await owner_client.get("/api/v1/audit-events?resource_type=secret_internal_table")
        ).status_code == 422

        # Owner paginates across audit events using cursor
        p1 = await owner_client.get("/api/v1/audit-events?limit=2")
        assert p1.status_code == 200
        assert len(p1.json()["items"]) == 2
        assert p1.json()["has_more"] is True
        p2 = await owner_client.get(
            f"/api/v1/audit-events?limit=2&cursor={p1.json()['next_cursor']}"
        )
        assert p2.status_code == 200
        p1_ids = {x["id"] for x in p1.json()["items"]}
        p2_ids = {x["id"] for x in p2.json()["items"]}
        assert p1_ids.isdisjoint(p2_ids)

        # Recruiter sees job_assigned audit events, NEVER job_unassigned audit events!
        rec_audit = await recruiter_client.get("/api/v1/audit-events?resource_type=job")
        assert rec_audit.status_code == 200
        rec_resource_ids = {x["resource_id"] for x in rec_audit.json()["items"]}
        assert job_assigned in rec_resource_ids
        assert job_unassigned not in rec_resource_ids

        # Recruiter denied audit export (403); Owner allowed (200)
        assert (await recruiter_client.get("/api/v1/audit-events/export")).status_code == 403
        exp_audit = await owner_client.get("/api/v1/audit-events/export?max_rows=50")
        assert exp_audit.status_code == 200
        assert exp_audit.headers["Cache-Control"] == "private, no-store, max-age=0"

        # Tenant 2 sees NONE of Org 1's audit events
        t2_audit = await other_tenant_client.get("/api/v1/audit-events")
        assert t2_audit.status_code == 200
        assert all(x["tenant_id"] != org_id for x in t2_audit.json()["items"])

        # ------------------------------------------------------------------
        # 2. Pipeline, Operations & Usage Analytics + Cohort Suppression
        # ------------------------------------------------------------------
        # Seed 6 candidates/applications on job_assigned with stage_event dwell durations:
        # - 2 candidates stay <5 sample size on IN_REVIEW -> suppressed when min_cohort=5!
        # - All 6 transition NEW -> IN_REVIEW after 4 hours -> NEW has sample_n=6 >= 5 -> NOT suppressed!
        target_cand_id: UUID | None = None
        target_app_id: UUID | None = None
        target_s3_key = f"{org_id}/versioned-cv-{uuid4().hex}.pdf"

        raw_boto = boto3.client(
            "s3",
            region_name="us-east-1",
            endpoint_url=moto_endpoint,
            aws_access_key_id="test",
            aws_secret_access_key="test",
            config=BotoConfig(signature_version="s3v4"),
        )
        # Write TWO versions of target_s3_key in versioned S3 bucket!
        b1 = b"synthetic-resume-v1"
        b2 = b"synthetic-resume-v2-updated"
        raw_boto.put_object(Bucket=clean_bucket, Key=target_s3_key, Body=b1)
        raw_boto.put_object(
            Bucket=clean_bucket,
            Key=target_s3_key,
            Body=b2,
            Metadata={"sha256": hashlib.sha256(b2).hexdigest()},
        )

        session = app.state.db_manager.session_maker()
        try:
            t0 = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
            for i in range(6):
                cid = uuid4()
                aid = uuid4()
                cand = Candidate(
                    id=cid,
                    tenant_id=UUID(org_id),
                    display_name=f"Synthetic Candidate {i}",
                    email=f"cand{i}@synthetic.talvora.test",
                    normalized_email=f"cand{i}@synthetic.talvora.test",
                    created_by=UUID(owner_uid),
                    created_at=t0,
                    updated_at=t0,
                )
                session.add(cand)
                session.flush()

                app_row = Application(
                    id=aid,
                    tenant_id=UUID(org_id),
                    job_id=UUID(job_assigned),
                    candidate_id=cid,
                    stage=ApplicationStage.IN_REVIEW.value,
                    created_by=UUID(owner_uid),
                    created_at=t0,
                    updated_at=t0 + timedelta(hours=4),
                )
                session.add(app_row)
                session.flush()

                session.add(
                    StageEvent(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        application_id=aid,
                        from_stage=ApplicationStage.NEW.value,
                        to_stage=ApplicationStage.IN_REVIEW.value,
                        reason=None,
                        actor_id=UUID(owner_uid),
                        actor_role="OWNER",
                        expected_version=1,
                        created_at=t0 + timedelta(hours=4),
                    )
                )

                if i < 2:
                    # Only 2 exit IN_REVIEW -> INTERVIEW_APPROVED (dwell 6h), leaving 4 open in IN_REVIEW
                    # Wait: 2 exited + 4 open = 6 in IN_REVIEW, and 2 open in INTERVIEW_APPROVED (<5 -> suppressed!)
                    app_row.stage = ApplicationStage.INTERVIEW_APPROVED.value
                    session.add(
                        StageEvent(
                            id=uuid4(),
                            tenant_id=UUID(org_id),
                            application_id=aid,
                            from_stage=ApplicationStage.IN_REVIEW.value,
                            to_stage=ApplicationStage.INTERVIEW_APPROVED.value,
                            reason=None,
                            actor_id=UUID(owner_uid),
                            actor_role="OWNER",
                            expected_version=2,
                            created_at=t0 + timedelta(hours=10),
                        )
                    )

                if i == 0:
                    target_cand_id = cid
                    target_app_id = aid
                    # Seed document, document_version, extraction, and fact for candidate 0
                    doc = Document(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        candidate_id=cid,
                        created_by=UUID(owner_uid),
                    )
                    session.add(doc)
                    session.flush()
                    dv = DocumentVersion(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        document_id=doc.id,
                        version_number=1,
                        media_type="application/pdf",
                        byte_size=len(b2),
                        sha256_checksum=hashlib.sha256(b2).hexdigest(),
                        storage_bucket=clean_bucket,
                        storage_key=target_s3_key,
                        storage_state="CLEAN",
                        source_actor_id=UUID(owner_uid),
                    )
                    session.add(dv)
                    session.flush()
                    session.add(
                        ApplicationDocument(
                            id=uuid4(),
                            tenant_id=UUID(org_id),
                            application_id=aid,
                            document_id=doc.id,
                            document_version_id=dv.id,
                            binding_generation=1,
                            status="ACTIVE",
                            bound_by=UUID(owner_uid),
                        )
                    )
                    ext = ExtractionRun(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        document_id=doc.id,
                        document_version_id=dv.id,
                        revision_number=1,
                        status=ExtractionRunStatus.SUCCEEDED.value,
                        triggered_by=ExtractionTriggerKind.SYSTEM_SCAN_CLEAN.value,
                        extractor_version="pypdf-test",
                        normalization_version="v1",
                        media_type="application/pdf",
                        is_complete=True,
                        total_pages=1,
                        processed_pages=1,
                        ocr_pages_used=0,
                        total_characters=27,
                        warnings=[],
                        started_at=t0,
                        completed_at=t0,
                    )
                    session.add(ext)
                    session.flush()
                    seg = SourceSegment(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        extraction_run_id=ext.id,
                        document_version_id=dv.id,
                        segment_index=0,
                        locator_type=SourceLocatorType.PDF_PAGE.value,
                        page_number=1,
                        docx_path=None,
                        extraction_method="DIGITAL_TEXT",
                        text="PostgreSQL expert engineer.",
                        canonical_text_sha256="c" * 64,
                        char_count=27,
                        ocr_mean_confidence=None,
                        warnings=[],
                    )
                    session.add(seg)
                    session.flush()
                    frag = EvidenceFragment(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        extraction_run_id=ext.id,
                        document_version_id=dv.id,
                        source_segment_id=seg.id,
                        start_offset=0,
                        end_offset=27,
                        quote="PostgreSQL expert engineer.",
                    )
                    session.add(frag)
                    session.flush()
                    fact = CandidateFactRevision(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        candidate_id=cid,
                        fact_key=uuid4(),
                        revision_number=1,
                        fact_type=CandidateFactType.SKILL.value,
                        value="PostgreSQL",
                        extracted_value="PostgreSQL",
                        polarity=CandidateFactPolarity.AFFIRMED.value,
                        extracted_polarity=CandidateFactPolarity.AFFIRMED.value,
                        status=CandidateFactStatus.VERIFIED.value,
                        review_note="Verified",
                        extraction_run_id=ext.id,
                        created_by=UUID(owner_uid),
                    )
                    session.add(fact)
                    session.flush()
                    session.add(
                        CandidateFactEvidence(
                            id=uuid4(),
                            tenant_id=UUID(org_id),
                            fact_revision_id=fact.id,
                            evidence_fragment_id=frag.id,
                        )
                    )

            # Record AI token usage & two pending operations (to test policy cancellation!)
            with UnitOfWork(
                session=session,
                tenant_id=UUID(org_id),
                subject_id=UUID(owner_uid),
                clock=clock,
            ) as uow:
                AnalyticsService.record_usage(
                    uow=uow,
                    tenant_id=UUID(org_id),
                    usage_category="PACK_GENERATE",
                    provider_name="openai",
                    model_id="gpt-4.1-mini",
                    prompt_tokens=1200,
                    completion_tokens=450,
                    job_id=UUID(job_assigned),
                )
                uow.admit_operation(
                    operation_type="PACK_GENERATE",
                    payload={"candidate_id": str(target_cand_id)},
                    resource_id=target_app_id,
                )
                uow.admit_operation(
                    operation_type="EXTERNAL_EMAIL_SEND",
                    payload={"candidate_id": str(target_cand_id)},
                    resource_id=target_app_id,
                )
                uow.commit()
        finally:
            session.close()

        assert target_cand_id is not None and target_app_id is not None

        # Invalid timezone rejected -> 422
        assert (
            await owner_client.get("/api/v1/analytics/pipeline?timezone=Mars/Olympus_Mons")
        ).status_code == 422

        # Recruiter denied analytics for unassigned job -> 403
        assert (
            await recruiter_client.get(f"/api/v1/analytics/pipeline?job_id={job_unassigned}")
        ).status_code == 403

        pipe_resp = await owner_client.get("/api/v1/analytics/pipeline?timezone=Asia/Kolkata")
        assert pipe_resp.status_code == 200, pipe_resp.text
        pipe = pipe_resp.json()
        assert pipe["denominators"]["distinct_applications_count"] == 6
        assert pipe["denominators"]["distinct_candidates_count"] == 6
        assert "candidate_score" not in pipe_resp.text
        stages_by_name = {s["stage"]: s for s in pipe["stages"]}
        # NEW had 6 transitions -> sample_size=6 >= 5 -> NOT suppressed, median=4.0h!
        assert stages_by_name["NEW"]["suppressed_small_cohort"] is False
        assert stages_by_name["NEW"]["median_time_in_stage_hours"] == 4.0
        # INTERVIEW_APPROVED has sample_size=2 < 5 -> SUPPRESSED!
        assert stages_by_name["INTERVIEW_APPROVED"]["suppressed_small_cohort"] is True
        assert stages_by_name["INTERVIEW_APPROVED"]["median_time_in_stage_hours"] is None

        ops_resp = await owner_client.get("/api/v1/analytics/operations")
        assert ops_resp.status_code == 200
        assert ops_resp.json()["background_operations"]["pending_or_retry_wait"] == 2

        usage_resp = await owner_client.get("/api/v1/usage")
        assert usage_resp.status_code == 200
        assert usage_resp.json()["ai_usage"]["total_tokens"] == 1650

        # ------------------------------------------------------------------
        # 3. Organization Policy Update & Queued Operation Cancellation
        # ------------------------------------------------------------------
        pol_get = await owner_client.get("/api/v1/organization-policy")
        assert pol_get.status_code == 200
        pol_v1 = pol_get.json()["version"]

        # Stale version -> 409
        assert (
            await owner_client.patch(
                "/api/v1/organization-policy",
                json={"expected_version": 99, "reason": "Stale test"},
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).status_code == 409

        # Patch policy keeping AI & outbound disabled -> cancels the 2 queued AI/outbound operations!
        pol_patch = await owner_client.patch(
            "/api/v1/organization-policy",
            json={
                "expected_version": pol_v1,
                "reason": "Enforce strict pre-pilot AI and outbound kill-switches.",
                "ai_processing_enabled": False,
                "outbound_communications_enabled": False,
                "backup_expiry_days": 30,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert pol_patch.status_code == 200, pol_patch.text
        assert pol_patch.json()["canceled_queued_operations"] == 2

        # ------------------------------------------------------------------
        # 4. Privacy Request Verification, Holds, Partial Failure & Purge
        # ------------------------------------------------------------------
        pr_create = await recruiter_client.post(
            "/api/v1/privacy-requests",
            json={
                "candidate_id": str(target_cand_id),
                "request_type": "ERASURE",
                "request_reason": "Candidate requested erasure via verified recruitment inbox.",
            },
            headers={
                "X-CSRF-Token": staff_csrfs["rec"],
                "Origin": "http://testserver",
                "Idempotency-Key": "priv-req-001",
            },
        )
        assert pr_create.status_code == 201, pr_create.text
        pr_id = pr_create.json()["id"]
        assert pr_create.json()["status"] == "PENDING_VERIFICATION"

        # Unverified request CANNOT be approved or executed!
        unverified_approve = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/approve",
            json={"expected_version": 1, "approval_notes": "Premature approval"},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert unverified_approve.status_code == 409

        # Attempting HUMAN_CONTACT_VERIFIED before candidate contact is verified -> 422!
        unver_contact = await recruiter_client.post(
            f"/api/v1/privacy-requests/{pr_id}/verify",
            json={
                "expected_version": 1,
                "verification_method": "HUMAN_CONTACT_VERIFIED",
                "verification_notes": "Checking contact verification.",
            },
            headers={"X-CSRF-Token": staff_csrfs["rec"], "Origin": "http://testserver"},
        )
        assert unver_contact.status_code == 422

        # Add verified CandidateContactRevision + CandidateContactVerification, then verify!
        session = app.state.db_manager.session_maker()
        try:
            cand_row = (
                session.query(Candidate).filter_by(tenant_id=UUID(org_id), id=target_cand_id).one()
            )
            crev = CandidateContactRevision(
                id=uuid4(),
                tenant_id=UUID(org_id),
                candidate_id=target_cand_id,
                revision_number=1,
                display_name=cand_row.display_name,
                normalized_name="synthetic candidate 0",
                email="cand0@synthetic.talvora.test",
                normalized_email="cand0@synthetic.talvora.test",
                phone=None,
                source="CANDIDATE_SUBMITTED",
                created_by=UUID(rec_uid),
            )
            session.add(crev)
            session.flush()
            session.add(
                CandidateContactVerification(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    candidate_id=target_cand_id,
                    contact_revision_id=crev.id,
                    contact_revision_number=1,
                    verified_address="cand0@synthetic.talvora.test",
                    verification_source="CANDIDATE_CONFIRMED_REPLY",
                    notes="Confirmed reply from candidate mailbox.",
                    actor_id=UUID(rec_uid),
                )
            )
            cand_row.current_contact_revision_id = crev.id
            cand_row.current_contact_revision_number = 1
            cand_row.verified_contact_revision_id = crev.id
            cand_row.verified_contact_revision_number = 1
            session.commit()
        finally:
            session.close()

        pr_ver = await recruiter_client.post(
            f"/api/v1/privacy-requests/{pr_id}/verify",
            json={
                "expected_version": 1,
                "verification_method": "HUMAN_CONTACT_VERIFIED",
                "verification_notes": "Confirmed reply from verified candidate email.",
            },
            headers={"X-CSRF-Token": staff_csrfs["rec"], "Origin": "http://testserver"},
        )
        assert pr_ver.status_code == 200, pr_ver.text
        assert pr_ver.json()["status"] == "VERIFIED"

        # Approve WITHOUT override_active_applications first -> execute blocked by active application (409)!
        pr_app = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/approve",
            json={
                "expected_version": pr_ver.json()["version"],
                "approval_notes": "Approved subject to active recruitment & hold checks.",
                "override_active_applications": False,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert pr_app.status_code == 200

        active_app_block = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/execute",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert active_app_block.status_code == 409
        assert active_app_block.json()["code"] == "ACTIVE_RECRUITMENT_IN_PROGRESS"

        # Transition application to WITHDRAWN so active recruitment ends
        session = app.state.db_manager.session_maker()
        try:
            arow = (
                session.query(Application).filter_by(tenant_id=UUID(org_id), id=target_app_id).one()
            )
            arow.stage = ApplicationStage.WITHDRAWN.value
            session.commit()
        finally:
            session.close()

        # Issue legal RetentionHold -> execute MUST be blocked with 409 RETENTION_HOLD_ACTIVE!
        hold_resp = await owner_client.post(
            "/api/v1/retention-holds",
            json={
                "candidate_id": str(target_cand_id),
                "scope": "CANDIDATE",
                "reason": "Pending compliance audit inquiry.",
                "review_at": (clock.now() + timedelta(days=14)).isoformat(),
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert hold_resp.status_code == 201
        hold_id = hold_resp.json()["id"]

        hold_block = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/execute",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert hold_block.status_code == 409
        assert hold_block.json()["code"] == "RETENTION_HOLD_ACTIVE"

        # Release the hold!
        rel_resp = await owner_client.post(
            f"/api/v1/retention-holds/{hold_id}/release",
            json={
                "expected_version": 1,
                "release_reason": "Compliance inquiry concluded; cleared for purge.",
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert rel_resp.status_code == 200
        assert rel_resp.json()["status"] == "RELEASED"

        # Take a PRE-PURGE SQLite backup checkpoint (to test post-restore tombstone replay later!)
        backup_db_file = tmp_path / "pre_purge_checkpoint.sqlite"
        app.state.db_manager.sync_engine.dispose()
        shutil.copy2(db_path, backup_db_file)

        # Inject transient S3 failure -> marks PARTIAL_FAILURE and returns 503 (no false success!)
        s3_wrapper.fail_deletes = True
        partial_fail = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/execute",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert partial_fail.status_code == 503
        s3_wrapper.fail_deletes = False

        status_after_fail = await owner_client.get(f"/api/v1/privacy-requests/{pr_id}")
        assert status_after_fail.json()["status"] == "PARTIAL_FAILURE"

        # Retry execute -> succeeds cleanly, purges ALL S3 versions + writes encrypted S3 tombstone + purges DB!
        exec_ok = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/execute",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert exec_ok.status_code == 200, exec_ok.text
        exec_body = exec_ok.json()
        assert exec_body["status"] == "COMPLETED"
        assert exec_body["deletion_ledger"] is not None
        assert exec_body["deletion_ledger"]["scope_summary"]["candidates_deleted"] == 1
        assert exec_body["deletion_ledger"]["scope_summary"]["s3_object_versions_deleted"] >= 2

        # Duplicate execute call is 100% idempotent!
        dup_exec = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/execute",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert dup_exec.status_code == 200
        assert dup_exec.json()["deletion_ledger"]["id"] == exec_body["deletion_ledger"]["id"]

        # Verify ALL S3 object versions of target_s3_key were permanently deleted!
        ver_check = raw_boto.list_object_versions(Bucket=clean_bucket, Prefix=target_s3_key)
        assert ver_check.get("Versions", []) == []
        assert ver_check.get("DeleteMarkers", []) == []

        # Verify Resurrection Guard: a stale worker trying to insert a fact or candidate for target_cand_id FAILS!
        session = app.state.db_manager.session_maker()
        try:
            session.add(
                Candidate(
                    id=target_cand_id,
                    tenant_id=UUID(org_id),
                    display_name="Resurrected Ghost",
                    created_by=UUID(owner_uid),
                )
            )
            with pytest.raises(CandidateTombstonedError, match="permanently purged"):
                session.flush()
            session.rollback()
        finally:
            session.close()

        # ------------------------------------------------------------------
        # 5. Disaster-Recovery Restore + Encrypted S3 Tombstone Replay!
        # ------------------------------------------------------------------
        # Simulate restoring the pre-purge DB backup AND an old S3 object version existing!
        app.state.db_manager.sync_engine.dispose()
        shutil.copy2(backup_db_file, db_path)
        raw_boto.put_object(Bucket=clean_bucket, Key=target_s3_key, Body=b"resurrected-from-backup")

        # Confirm candidate WAS resurrected in the restored DB snapshot before replay
        session = app.state.db_manager.session_maker()
        try:
            assert (
                session.query(Candidate)
                .filter_by(tenant_id=UUID(org_id), id=target_cand_id)
                .count()
                == 1
            )
        finally:
            session.close()

        # Run S3 tombstone replay BEFORE resuming normal traffic!
        replay_resp = await owner_client.post(
            "/api/v1/privacy/tombstones/replay",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert replay_resp.status_code == 200, replay_resp.text
        assert replay_resp.json()["s3_tombstones_scanned"] == 1
        assert replay_resp.json()["replayed_resurrected_count"] == 1

        # Candidate, applications, facts, AND resurrected S3 object versions are GONE again!
        session = app.state.db_manager.session_maker()
        try:
            assert (
                session.query(Candidate)
                .filter_by(tenant_id=UUID(org_id), id=target_cand_id)
                .count()
                == 0
            )
            assert (
                session.query(CandidateFactRevision)
                .filter_by(tenant_id=UUID(org_id), candidate_id=target_cand_id)
                .count()
                == 0
            )
        finally:
            session.close()

        ver_after_replay = raw_boto.list_object_versions(Bucket=clean_bucket, Prefix=target_s3_key)
        assert ver_after_replay.get("Versions", []) == []
