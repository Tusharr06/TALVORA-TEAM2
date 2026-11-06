"""Phase P18 Release Gate: Fault Injection, Concurrency, O(1) Query Budgets & Latency Benchmarks.

Validates:
1. Worker fault injection & crash recovery:
   - Rolled-back DB transaction emits zero orphan OutboxEvent / JobOperation rows.
   - Queue broker outage (`InMemoryQueuePublisher(should_fail=True -> False)`) & clean recovery.
   - Duplicate worker claim/delivery idempotency (`SUCCEEDED` job is never re-executed).
   - Stale worker lease fencing (`TaskExecutionManager` / `StaleFencingTokenError`).
   - AI pack provider timeout (`503`) creates zero fabricated `PackRevision` rows.
   - Transient S3 failure during privacy purge records `PARTIAL_FAILURE` and converges to `COMPLETED` on retry.
2. Concurrency & idempotency invariants:
   - Last-owner demotion and last-owner removal blocked.
   - Optimistic-locking `409 Conflict` on stale `expected_version` (`Application`, `OrganizationPolicy`, `Feedback`).
   - `Idempotency-Key` exact replay vs mutated-payload `409 Conflict`.
3. SQLAlchemy `before_cursor_execute` O(1) SQL query budgets as dataset size scales ($N=2$ vs $N=10$).
4. Seeded synthetic pilot `p50`/`p95`/`p99` latency verification against Playbook Section 5 NFR targets
   plus full local DB + Moto S3 + AES-256-GCM tombstone restore rehearsal timing.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
import pytest
import sqlalchemy as sa
from botocore.config import Config as BotoConfig
from httpx import ASGITransport, AsyncClient
from moto.server import ThreadedMotoServer

from talvora.bootstrap import create_app
from talvora.config import Settings, get_settings
from talvora.modules.applications.domain.models import Application
from talvora.modules.candidates.domain.models import (
    Candidate,
    CandidateContactRevision,
    CandidateContactVerification,
)
from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.documents.application.storage import S3ObjectStorageAdapter
from talvora.modules.documents.domain.models import Document
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.interview_packs.application.pack_worker import PackGenerationWorker
from talvora.modules.interview_packs.domain.models import PackRevision
from talvora.modules.jobs.domain.models import JobAssignment, JobRole
from talvora.modules.operations.domain.models import (
    JobOperation,
    OperationStatus,
    OutboxEvent,
    OutboxStatus,
)
from talvora.modules.organizations.domain.models import Membership
from talvora.modules.privacy.domain.models import DeletionLedger
from talvora.shared.domain.clock import FrozenClock
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException
from talvora.workers.coordinator import InMemoryQueuePublisher, OutboxCoordinator
from talvora.workers.task_handlers import StaleFencingTokenError, TaskExecutionManager
from tests.integration.test_release_gate_vertical_e2e_p18 import (
    DeterministicEicarScanner,
    _build_digital_pdf,
    _drive_scan_and_extraction,
    _login_user,
    _setup_org,
)


def _percentile(samples_ms: list[float], pct: float) -> float:
    if not samples_ms:
        return 0.0
    ordered = sorted(samples_ms)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return round(ordered[idx], 2)


class FlakyDeleteStorageProxy:
    """Wraps a real S3ObjectStorageAdapter to inject transient S3 delete failures on demand."""

    def __init__(self, inner: ObjectStoragePort) -> None:
        self._inner = inner
        self.fail_deletes = False

    @property
    def _client(self) -> Any:
        if self.fail_deletes:
            raise OSError("Simulated transient S3 503 SlowDown during versioned purge")
        return getattr(self._inner, "_client", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def delete_object(self, bucket: str, key: str) -> None:
        if self.fail_deletes:
            raise RuntimeError("Simulated transient S3 503 SlowDown during object purge")
        return self._inner.delete_object(bucket, key)

    def delete_object_all_versions(self, bucket: str, key: str) -> int:
        if self.fail_deletes:
            raise RuntimeError("Simulated transient S3 503 SlowDown during versioned purge")
        fn = getattr(self._inner, "delete_object_all_versions", None)
        if callable(fn):
            return int(fn(bucket, key))
        self._inner.delete_object(bucket, key)
        return 1


class TimeoutPackAIProvider:
    """Simulates an upstream LLM provider timeout during structured pack generation."""

    provider_id = "timeout-llm-provider"

    def generate_structured_pack(self, *args: Any, **kwargs: Any) -> Any:
        raise ProblemException(
            status_code=503,
            title="AI Pack Provider Timeout",
            code="AI_PROVIDER_UNAVAILABLE",
            detail="Upstream model provider timed out after 15000ms.",
        )


@pytest.fixture(scope="module")
def moto_s3_url() -> Generator[str]:
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


@pytest.fixture
def perf_gate_env(
    test_settings: Settings, moto_s3_url: str
) -> Generator[tuple[Any, FrozenClock, FlakyDeleteStorageProxy]]:
    q_bucket = f"p18-rel-q-{uuid4().hex[:8]}"
    c_bucket = f"p18-rel-c-{uuid4().hex[:8]}"
    settings = test_settings.model_copy(
        update={
            "moto_endpoint_url": moto_s3_url,
            "s3_bucket_name": q_bucket,
            "s3_clean_bucket_name": c_bucket,
        }
    )

    app = create_app(settings)
    app.state.virus_scanner = DeterministicEicarScanner()
    clock = FrozenClock(fixed_time=datetime(2026, 11, 6, 10, 0, 0, tzinfo=UTC))
    app.state.clock = clock

    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    real_s3 = S3ObjectStorageAdapter(settings)
    real_s3.ensure_bucket(q_bucket)
    real_s3.ensure_bucket(c_bucket)
    flaky_storage = FlakyDeleteStorageProxy(real_s3)
    app.state.object_storage = flaky_storage

    raw_s3 = boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=moto_s3_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=BotoConfig(signature_version="s3v4"),
    )
    for bkt in (q_bucket, c_bucket):
        raw_s3.put_bucket_versioning(Bucket=bkt, VersioningConfiguration={"Status": "Enabled"})

    app.state.oidc_client = MockOidcClient(
        issuer="https://idp.talvora.test", client_id="test-client-id"
    )
    app.state.mail_provider = InMemorySystemMailProvider()
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        yield app, clock, flaky_storage
    finally:
        app.state.db_manager.sync_engine.dispose()


def _resolve_uploaded_app_and_candidate(
    app: Any, tenant_id: str, job_id: str, doc_id: str
) -> tuple[str, str]:
    session = app.state.db_manager.session_maker()
    try:
        doc_row = session.scalar(
            sa.select(Document).where(
                Document.tenant_id == UUID(tenant_id),
                Document.id == UUID(doc_id),
            )
        )
        assert doc_row is not None
        app_row = session.scalar(
            sa.select(Application).where(
                Application.tenant_id == UUID(tenant_id),
                Application.job_id == UUID(job_id),
                Application.candidate_id == doc_row.candidate_id,
            )
        )
        assert app_row is not None
        return str(app_row.id), str(doc_row.candidate_id)
    finally:
        session.close()


@contextmanager
def _count_sql_statements(engine: Any) -> Generator[list[str]]:
    statements: list[str] = []

    def _listener(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: Any,
    ) -> None:
        normalized = statement.strip().upper()
        if normalized.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "PRAGMA", "BEGIN", "COMMIT")):
            return
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        sa.event.remove(engine, "before_cursor_execute", _listener)


@pytest.mark.integration
async def test_worker_fault_injection_outbox_fencing_provider_timeout_and_s3_partial_purge_recovery(
    perf_gate_env: tuple[Any, FrozenClock, FlakyDeleteStorageProxy],
) -> None:
    """Proves crash-safe outbox rollback, broker outage recovery, duplicate claim safety,
    stale lease fencing (`StaleFencingTokenError`), LLM timeout `503` safety, and S3 partial-purge retry."""
    app, clock, flaky_storage = perf_gate_env
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as owner_client,
        AsyncClient(transport=transport, base_url="http://testserver") as admin2_client,
    ):
        owner_uid, owner_csrf = await _login_user(owner_client)
        org_id, owner_csrf = await _setup_org(owner_client, owner_csrf, "Fault Injection Org")
        admin2_uid, _ = await _login_user(admin2_client)

        session = app.state.db_manager.session_maker()
        try:
            session.add(
                Membership(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    user_id=UUID(admin2_uid),
                    role="ADMIN",
                    status="ACTIVE",
                )
            )
            session.commit()
        finally:
            session.close()
        a2_tmp = (await admin2_client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        await admin2_client.post(
            f"/api/v1/organizations/{org_id}/select",
            headers={"X-CSRF-Token": a2_tmp, "Origin": "http://testserver"},
        )
        admin2_csrf = (await admin2_client.get("/api/v1/auth/csrf")).json()["csrf_token"]

        # 1. Rolled-back transaction emits ZERO orphan JobOperation or OutboxEvent rows
        rolled_back_op_id: UUID | None = None
        session = app.state.db_manager.session_maker()
        try:
            with pytest.raises(RuntimeError, match="Simulated mid-transaction crash"):
                with UnitOfWork(
                    session=session,
                    tenant_id=UUID(org_id),
                    subject_id=UUID(owner_uid),
                    clock=clock,
                ) as uow:
                    op = uow.admit_operation(
                        operation_type="diagnostics.crash_test",
                        payload={"probe": True},
                    )
                    rolled_back_op_id = op.id
                    raise RuntimeError("Simulated mid-transaction crash")
        finally:
            session.close()

        session = app.state.db_manager.session_maker()
        try:
            assert rolled_back_op_id is not None
            assert (
                session.scalar(sa.select(JobOperation).where(JobOperation.id == rolled_back_op_id))
                is None
            )
            assert (
                session.scalar(
                    sa.select(OutboxEvent).where(OutboxEvent.payload_ref == rolled_back_op_id)
                )
                is None
            )
        finally:
            session.close()

        # 2. Outbox broker outage (`should_fail=True -> False`) + Stale Lease Fencing + Duplicate Claim
        publisher = InMemoryQueuePublisher()
        publisher.should_fail = True
        coordinator = OutboxCoordinator(publisher=publisher, clock=clock, lease_duration_seconds=60)
        task_mgr = TaskExecutionManager(clock=clock)

        session = app.state.db_manager.session_maker()
        try:
            with UnitOfWork(
                session=session,
                tenant_id=UUID(org_id),
                subject_id=UUID(owner_uid),
                clock=clock,
            ) as uow:
                fenced_op = uow.admit_operation(
                    operation_type="diagnostics.fenced_work",
                    payload={"step": "validate-fencing"},
                )
                fenced_op_id = fenced_op.id
                uow.commit()

            # Broker outage: sweep_outbox marks OutboxEvent FAILED without losing JobOperation
            published_during_outage = coordinator.sweep_outbox(session)
            session.commit()
            assert published_during_outage == 0
            ob_row = session.scalar(
                sa.select(OutboxEvent).where(OutboxEvent.payload_ref == fenced_op_id)
            )
            assert ob_row is not None and ob_row.status == OutboxStatus.FAILED.value

            # Broker recovers: reset failed outbox row to PENDING and re-sweep cleanly
            ob_row.status = OutboxStatus.PENDING.value
            session.commit()
            publisher.should_fail = False
            published_after_recovery = coordinator.sweep_outbox(session)
            session.commit()
            assert published_after_recovery == 1
            assert len(publisher.published_messages) == 1
            assert set(publisher.published_messages[0].keys()) == {
                "topic",
                "tenant_id",
                "job_id",
                "schema_version",
                "correlation_id",
            }

            # Worker A claims lease (token=1), then stalls past 60s lease expiration!
            op_a, token_a = task_mgr.claim_job(
                session, UUID(org_id), fenced_op_id, "worker-a-stalled"
            )
            session.commit()
            assert token_a == 1
            assert op_a.status == OperationStatus.RUNNING.value

            clock.set_time(clock.now() + timedelta(minutes=10))
            reaped = coordinator.recover_expired_leases(session)
            session.commit()
            assert reaped == 1

            clock.set_time(clock.now() + timedelta(minutes=10))
            requeued = coordinator.sweep_due_jobs(session)
            session.commit()
            assert requeued == 1

            # Worker B claims fresh lease (token=2) and completes cleanly
            op_b, token_b = task_mgr.claim_job(
                session, UUID(org_id), fenced_op_id, "worker-b-healthy"
            )
            assert token_b == 2
            assert op_b.status == OperationStatus.RUNNING.value
            task_mgr.finalize_job(
                session=session,
                tenant_id=UUID(org_id),
                job_id=fenced_op_id,
                fencing_token=token_b,
                status=OperationStatus.SUCCEEDED,
                result={"completed_by": "worker-b-healthy"},
            )
            session.commit()

            # Delayed Worker A wakes up and tries to finalize with stale token=1 -> FENCED!
            with pytest.raises(StaleFencingTokenError):
                task_mgr.finalize_job(
                    session=session,
                    tenant_id=UUID(org_id),
                    job_id=fenced_op_id,
                    fencing_token=token_a,
                    status=OperationStatus.SUCCEEDED,
                    result={"completed_by": "stale-worker-a"},
                )

            # Duplicate queue delivery after SUCCEEDED is an idempotent no-op
            dup_op, dup_tok = task_mgr.claim_job(
                session, UUID(org_id), fenced_op_id, "worker-c-duplicate"
            )
            assert dup_op.status == OperationStatus.SUCCEEDED.value
            assert dup_tok == 2
        finally:
            session.close()

        # 3. AI Provider Timeout (`503`) creates ZERO fabricated PackRevision rows
        pol = (await owner_client.get("/api/v1/organization-policy")).json()
        await owner_client.patch(
            "/api/v1/organization-policy",
            json={
                "expected_version": pol["version"],
                "reason": "Enable AI pack provider test.",
                "ai_processing_enabled": True,
                "ai_interview_packs_enabled": True,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        job_r = await owner_client.post(
            "/api/v1/jobs",
            json={
                "title": "Resilience Engineer",
                "description": "Fault-tolerant distributed Python systems.",
                "draft_requirements": [
                    {
                        "criterion_type": "SKILL_PRESENT",
                        "is_required": True,
                        "source_requirement_text": "Python resilience",
                        "relevance_rationale": "Core requirement",
                        "configuration": {
                            "criterion_type": "SKILL_PRESENT",
                            "skill_name": "Python",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        job_id = job_r.json()["id"]
        await owner_client.post(
            f"/api/v1/jobs/{job_id}/publish",
            json={"expected_version": 1},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        batch_id = (
            await owner_client.post(
                "/api/v1/upload-batches",
                json={"job_id": job_id},
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).json()["id"]
        up_item = (
            await owner_client.post(
                f"/api/v1/upload-batches/{batch_id}/items",
                data={
                    "new_candidate_display_name": "Arjun Nair",
                    "new_candidate_email": "arjun.nair@example.com",
                },
                files={
                    "file": (
                        "arjun.pdf",
                        _build_digital_pdf(["Arjun Nair — Senior Python & PostgreSQL Engineer"]),
                        "application/pdf",
                    )
                },
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).json()
        doc_ver_id = up_item["document_version_id"]
        _drive_scan_and_extraction(app, org_id, doc_ver_id)
        app_id, cand_id = _resolve_uploaded_app_and_candidate(
            app, org_id, job_id, up_item["document_id"]
        )
        await owner_client.post(
            f"/api/v1/applications/{app_id}/document-bindings",
            json={"document_version_id": doc_ver_id, "expected_binding_generation": 0},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        cur_v = (await owner_client.get(f"/api/v1/applications/{app_id}")).json()["version"]
        for st in ("IN_REVIEW", "INTERVIEW_APPROVED", "INTERVIEWING"):
            r_tr = await owner_client.post(
                f"/api/v1/applications/{app_id}/stage-transitions",
                json={"target_stage": st, "reason": f"Advance to {st}", "expected_version": cur_v},
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
            cur_v = r_tr.json()["version"]

        fw_r = await owner_client.post(
            "/api/v1/competency-frameworks",
            json={
                "job_id": job_id,
                "stage": "TECHNICAL_DEEP_DIVE",
                "name": "Resilience Framework",
                "slug": "resilience-fw",
                "summary": "Resilience behavioral rubric.",
                "behavioral_scale_guide": "1=Weak, 2=Basic, 3=Strong, 4=Exceptional.",
                "competencies": [
                    {
                        "code": "FAULT_TOLERANCE",
                        "name": "Fault Tolerance",
                        "definition": "Handles partial failures and idempotent retries.",
                        "observation_required": True,
                        "display_order": 0,
                        "rubric_anchors": {
                            "1": "Ignores partial failure modes.",
                            "2": "Uses ad-hoc retries without idempotency.",
                            "3": "Designs fenced idempotent retry workflows.",
                            "4": "Proves end-to-end recovery invariants.",
                            "NOT_ASSESSED": "Not covered during interview.",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        fw_id = fw_r.json()["framework"]["id"]
        fw_pub = await owner_client.post(
            f"/api/v1/competency-frameworks/{fw_id}/versions/1/publish",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        fw_ver_id = fw_pub.json()["version_id"]

        interview_id = str(uuid4())
        await owner_client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json={
                "application_id": app_id,
                "stage": "TECHNICAL_DEEP_DIVE",
                "duration_minutes": 45,
                "schedule_revision": 1,
                "status": "BOOKED",
                "participant_user_ids": [owner_uid],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        # Inject timeout AI provider -> 503 and ZERO fabricated PackRevision rows!
        app.state.pack_ai_provider = TimeoutPackAIProvider()
        try:
            timeout_resp = await owner_client.post(
                f"/api/v1/interviews/{interview_id}/pack-runs",
                json={
                    "reason": "Test provider timeout handling",
                    "framework_version_id": fw_ver_id,
                },
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
            assert timeout_resp.status_code == 503
        finally:
            app.state.pack_ai_provider = None

        session = app.state.db_manager.session_maker()
        try:
            pack_count = session.scalar(
                sa.select(sa.func.count())
                .select_from(PackRevision)
                .where(
                    PackRevision.tenant_id == UUID(org_id),
                    PackRevision.interview_id == UUID(interview_id),
                )
            )
            assert pack_count == 0
        finally:
            session.close()

        # 4. Transient S3 Outage During Privacy Erasure -> PARTIAL_FAILURE -> Retry -> COMPLETED
        app_now = await owner_client.get(f"/api/v1/applications/{app_id}")
        await owner_client.post(
            f"/api/v1/applications/{app_id}/stage-transitions",
            json={
                "target_stage": "WITHDRAWN",
                "reason": "Candidate requested erasure.",
                "expected_version": app_now.json()["version"],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        session = app.state.db_manager.session_maker()
        try:
            c_row = session.scalar(
                sa.select(Candidate).where(
                    Candidate.tenant_id == UUID(org_id),
                    Candidate.id == UUID(cand_id),
                )
            )
            assert c_row is not None
            next_cnum = (c_row.current_contact_revision_number or 0) + 1
            crev = CandidateContactRevision(
                id=uuid4(),
                tenant_id=UUID(org_id),
                candidate_id=UUID(cand_id),
                revision_number=next_cnum,
                display_name="Arjun Nair",
                normalized_name="arjun nair",
                email="arjun.nair@example.com",
                normalized_email="arjun.nair@example.com",
                phone=None,
                source="CANDIDATE_SUBMITTED",
                created_by=UUID(owner_uid),
            )
            session.add(crev)
            session.flush()
            session.add(
                CandidateContactVerification(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    candidate_id=UUID(cand_id),
                    contact_revision_id=crev.id,
                    contact_revision_number=next_cnum,
                    verified_address="arjun.nair@example.com",
                    verification_source="CANDIDATE_CONFIRMED_REPLY",
                    notes="Verified candidate reply.",
                    actor_id=UUID(owner_uid),
                )
            )
            c_row.current_contact_revision_id = crev.id
            c_row.current_contact_revision_number = next_cnum
            c_row.verified_contact_revision_id = crev.id
            c_row.verified_contact_revision_number = next_cnum
            session.commit()
        finally:
            session.close()

        pr_c = await owner_client.post(
            "/api/v1/privacy-requests",
            json={
                "candidate_id": cand_id,
                "request_type": "ERASURE",
                "request_reason": "Verified erasure with transient S3 fault test.",
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        pr_id = pr_c.json()["id"]
        pr_v = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/verify",
            json={
                "expected_version": pr_c.json()["version"],
                "verification_method": "HUMAN_CONTACT_VERIFIED",
                "verification_notes": "Verified candidate mailbox.",
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        await admin2_client.post(
            f"/api/v1/privacy-requests/{pr_id}/approve",
            json={
                "expected_version": pr_v.json()["version"],
                "override_active_applications": False,
                "approval_notes": "Second-party admin approval granted.",
            },
            headers={"X-CSRF-Token": admin2_csrf, "Origin": "http://testserver"},
        )

        # Inject S3 delete failure -> execute returns 503 and commits PARTIAL_FAILURE safely!
        flaky_storage.fail_deletes = True
        try:
            partial_exec = await owner_client.post(
                f"/api/v1/privacy-requests/{pr_id}/execute",
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
            assert partial_exec.status_code == 503, partial_exec.text
            assert partial_exec.json()["code"] == "STORAGE_PURGE_INCOMPLETE"
            pr_state = (await owner_client.get(f"/api/v1/privacy-requests/{pr_id}")).json()
            assert pr_state["status"] == "PARTIAL_FAILURE"
            assert pr_state["last_error"] is not None
        finally:
            flaky_storage.fail_deletes = False

        # Retry execute once S3 recovers -> converges cleanly to COMPLETED!
        recovered_exec = await owner_client.post(
            f"/api/v1/privacy-requests/{pr_id}/execute",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert recovered_exec.status_code == 200, recovered_exec.text
        assert recovered_exec.json()["status"] == "COMPLETED"
        assert (await owner_client.get(f"/api/v1/candidates/{cand_id}")).status_code == 404


@pytest.mark.integration
async def test_concurrency_last_owner_protection_optimistic_locking_and_idempotency_keys(
    perf_gate_env: tuple[Any, FrozenClock, FlakyDeleteStorageProxy],
) -> None:
    """Proves last-owner demotion/removal blocks, optimistic locking `409` contention across
    aggregates, and `Idempotency-Key` exact replay vs mutated-payload `409`."""
    app, _, _ = perf_gate_env
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as owner_client,
    ):
        owner_uid, owner_csrf = await _login_user(owner_client)
        org_id, owner_csrf = await _setup_org(owner_client, owner_csrf, "Concurrency Gate Org")

        # 1. Last-Owner Demotion & Removal Protection
        demote_self = await owner_client.patch(
            f"/api/v1/organizations/{org_id}/members/{owner_uid}",
            json={"role": "ADMIN"},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert demote_self.status_code in (400, 409, 422), demote_self.text

        remove_self = await owner_client.delete(
            f"/api/v1/organizations/{org_id}/members/{owner_uid}",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert remove_self.status_code in (400, 409, 422), remove_self.text

        # 2. Optimistic Locking 409 Contention on OrganizationPolicy
        pol = (await owner_client.get("/api/v1/organization-policy")).json()
        p_ok = await owner_client.patch(
            "/api/v1/organization-policy",
            json={
                "expected_version": pol["version"],
                "reason": "First concurrent policy update wins.",
                "ai_processing_enabled": True,
                "ai_interview_packs_enabled": True,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert p_ok.status_code == 200

        p_stale = await owner_client.patch(
            "/api/v1/organization-policy",
            json={
                "expected_version": pol["version"],
                "reason": "Second concurrent writer with stale version.",
                "export_ttl_hours": 12,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert p_stale.status_code == 409

        # 3. Optimistic Locking 409 on Application Stage Transition & Feedback + Idempotency-Key Replay
        job_r = await owner_client.post(
            "/api/v1/jobs",
            json={
                "title": "Concurrency Systems Role",
                "description": "Optimistic concurrency and idempotent APIs.",
                "draft_requirements": [
                    {
                        "criterion_type": "SKILL_PRESENT",
                        "is_required": True,
                        "source_requirement_text": "PostgreSQL locking",
                        "relevance_rationale": "Core requirement",
                        "configuration": {
                            "criterion_type": "SKILL_PRESENT",
                            "skill_name": "PostgreSQL",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        job_id = job_r.json()["id"]
        await owner_client.post(
            f"/api/v1/jobs/{job_id}/publish",
            json={"expected_version": 1},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        app_r = await owner_client.post(
            "/api/v1/applications",
            json={
                "job_id": job_id,
                "new_candidate_display_name": "Meera Iyer",
                "new_candidate_email": "meera.iyer@example.com",
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        app_id = app_r.json()["id"]
        initial_ver = app_r.json()["version"]

        tr1 = await owner_client.post(
            f"/api/v1/applications/{app_id}/stage-transitions",
            json={
                "target_stage": "IN_REVIEW",
                "reason": "First recruiter advances candidate.",
                "expected_version": initial_ver,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert tr1.status_code == 200

        tr_stale = await owner_client.post(
            f"/api/v1/applications/{app_id}/stage-transitions",
            json={
                "target_stage": "REJECTED",
                "reason": "Stale tab tries to reject concurrently.",
                "expected_version": initial_ver,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert tr_stale.status_code == 409

        # Advance to INTERVIEWING + publish framework + test Idempotency-Key on pack-runs
        cur_v = tr1.json()["version"]
        for st in ("INTERVIEW_APPROVED", "INTERVIEWING"):
            r_next = await owner_client.post(
                f"/api/v1/applications/{app_id}/stage-transitions",
                json={"target_stage": st, "reason": f"Advance {st}", "expected_version": cur_v},
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
            cur_v = r_next.json()["version"]

        fw_r = await owner_client.post(
            "/api/v1/competency-frameworks",
            json={
                "job_id": job_id,
                "stage": "TECHNICAL_DEEP_DIVE",
                "name": "Concurrency Rubric",
                "slug": "concurrency-rubric",
                "summary": "Concurrency rubric.",
                "behavioral_scale_guide": "1=Poor, 2=Fair, 3=Good, 4=Strong.",
                "competencies": [
                    {
                        "code": "LOCKING",
                        "name": "Optimistic Locking",
                        "definition": "Prevents lost updates via version checks.",
                        "observation_required": True,
                        "display_order": 0,
                        "rubric_anchors": {
                            "1": "Overwrites blindly without version check.",
                            "2": "Understands version columns conceptually.",
                            "3": "Enforces HTTP 409 conflict handling cleanly.",
                            "4": "Combines optimistic locking with idempotency keys.",
                            "NOT_ASSESSED": "Not assessed.",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        fw_id = fw_r.json()["framework"]["id"]
        comp_id = fw_r.json()["competencies"][0]["id"]
        fw_pub = await owner_client.post(
            f"/api/v1/competency-frameworks/{fw_id}/versions/1/publish",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        fw_ver_id = fw_pub.json()["version_id"]

        interview_id = str(uuid4())
        await owner_client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json={
                "application_id": app_id,
                "stage": "TECHNICAL_DEEP_DIVE",
                "duration_minutes": 60,
                "schedule_revision": 1,
                "status": "BOOKED",
                "participant_user_ids": [owner_uid],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        idem_key = f"idem-pack-{uuid4().hex}"
        first_pack = await owner_client.post(
            f"/api/v1/interviews/{interview_id}/pack-runs",
            json={"reason": "Initial idempotent generation", "framework_version_id": fw_ver_id},
            headers={
                "X-CSRF-Token": owner_csrf,
                "Origin": "http://testserver",
                "Idempotency-Key": idem_key,
            },
        )
        assert first_pack.status_code == 201, first_pack.text

        # Exact replay returns identical revision ID without creating revision #2
        replay_pack = await owner_client.post(
            f"/api/v1/interviews/{interview_id}/pack-runs",
            json={"reason": "Initial idempotent generation", "framework_version_id": fw_ver_id},
            headers={
                "X-CSRF-Token": owner_csrf,
                "Origin": "http://testserver",
                "Idempotency-Key": idem_key,
            },
        )
        assert replay_pack.status_code == 201
        assert replay_pack.json()["id"] == first_pack.json()["id"]
        assert replay_pack.json()["revision_number"] == 1

        # Mutated payload under the same Idempotency-Key -> 409 Conflict!
        mutated_pack = await owner_client.post(
            f"/api/v1/interviews/{interview_id}/pack-runs",
            json={"reason": "DIFFERENT mutated reason!", "framework_version_id": fw_ver_id},
            headers={
                "X-CSRF-Token": owner_csrf,
                "Origin": "http://testserver",
                "Idempotency-Key": idem_key,
            },
        )
        assert mutated_pack.status_code == 409

        # Publish pack & verify Feedback optimistic locking 409
        await owner_client.post(
            f"/api/v1/pack-revisions/{first_pack.json()['id']}/publish",
            json={"expected_version": first_pack.json()["version"], "review_notes": "Approved."},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        d1 = await owner_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "overall_observations": "Draft v1 observation.",
                "human_recommendation": "ADVANCE_DISCUSSION",
                "ratings": [
                    {
                        "competency_id": comp_id,
                        "rating": "3",
                        "observation": "Solid optimistic concurrency knowledge.",
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert d1.status_code == 200
        d2 = await owner_client.put(
            f"/api/v1/interviews/{interview_id}/feedback/me",
            json={
                "expected_version": d1.json()["version"],
                "overall_observations": "Draft v2 observation.",
                "human_recommendation": "ADVANCE_DISCUSSION",
                "ratings": [
                    {
                        "competency_id": comp_id,
                        "rating": "4",
                        "observation": "Updated observation.",
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert d2.status_code == 200
        stale_fb = await owner_client.post(
            f"/api/v1/interviews/{interview_id}/feedback/me/submit",
            json={"expected_version": d1.json()["version"]},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert stale_fb.status_code == 409


@pytest.mark.integration
async def test_o1_sql_query_budgets_under_dataset_scaling(
    perf_gate_env: tuple[Any, FrozenClock, FlakyDeleteStorageProxy],
) -> None:
    """Proves O(1) SQL statement counts (zero N+1 regressions) as dataset size scales
    from N=2 to N=10 across applications, packs, feedback, audit-events, and analytics."""
    app, _, _ = perf_gate_env
    engine = app.state.db_manager.sync_engine

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as owner_client,
    ):
        owner_uid, owner_csrf = await _login_user(owner_client)
        org_id, owner_csrf = await _setup_org(owner_client, owner_csrf, "O1 Query Budget Org")

        pol = (await owner_client.get("/api/v1/organization-policy")).json()
        await owner_client.patch(
            "/api/v1/organization-policy",
            json={
                "expected_version": pol["version"],
                "reason": "Enable AI packs for O(1) batch query verification.",
                "ai_processing_enabled": True,
                "ai_interview_packs_enabled": True,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        job_r = await owner_client.post(
            "/api/v1/jobs",
            json={
                "title": "O1 Query Scale Role",
                "description": "Batch hydration verification.",
                "draft_requirements": [
                    {
                        "criterion_type": "SKILL_PRESENT",
                        "is_required": True,
                        "source_requirement_text": "SQL batching",
                        "relevance_rationale": "Core requirement",
                        "configuration": {"criterion_type": "SKILL_PRESENT", "skill_name": "SQL"},
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        job_id = job_r.json()["id"]
        await owner_client.post(
            f"/api/v1/jobs/{job_id}/publish",
            json={"expected_version": 1},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        fw_r = await owner_client.post(
            "/api/v1/competency-frameworks",
            json={
                "job_id": job_id,
                "stage": "TECHNICAL_DEEP_DIVE",
                "name": "Query Budget Rubric",
                "slug": "query-budget-rubric",
                "summary": "Batch query check.",
                "behavioral_scale_guide": "1=Poor, 2=Fair, 3=Good, 4=Strong.",
                "competencies": [
                    {
                        "code": "BATCH_SQL",
                        "name": "Batch SQL Design",
                        "definition": "Eliminates N+1 loops.",
                        "observation_required": True,
                        "display_order": 0,
                        "rubric_anchors": {
                            "1": "Writes per-row ORM queries in loops.",
                            "2": "Uses eager loading inconsistently.",
                            "3": "Uses bounded IN batch hydration.",
                            "4": "Verifies O(1) SQL statement budgets in CI.",
                            "NOT_ASSESSED": "Not assessed.",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        fw_id = fw_r.json()["framework"]["id"]
        comp_id = fw_r.json()["competencies"][0]["id"]
        fw_ver_id = (
            await owner_client.post(
                f"/api/v1/competency-frameworks/{fw_id}/versions/1/publish",
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).json()["version_id"]

        app_main = (
            await owner_client.post(
                "/api/v1/applications",
                json={
                    "job_id": job_id,
                    "new_candidate_display_name": "Primary Candidate",
                    "new_candidate_email": "primary.cand@example.com",
                },
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).json()
        main_app_id = app_main["id"]
        v = app_main["version"]
        for st in ("IN_REVIEW", "INTERVIEW_APPROVED", "INTERVIEWING"):
            v = (
                await owner_client.post(
                    f"/api/v1/applications/{main_app_id}/stage-transitions",
                    json={"target_stage": st, "reason": f"Move {st}", "expected_version": v},
                    headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
                )
            ).json()["version"]

        interview_id = str(uuid4())
        panelists: list[tuple[AsyncClient, str, str]] = []
        for _idx in range(6):
            p_cli = AsyncClient(transport=transport, base_url="http://testserver")
            p_uid, _ = await _login_user(p_cli)
            session = app.state.db_manager.session_maker()
            try:
                session.add(
                    Membership(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        user_id=UUID(p_uid),
                        role="INTERVIEWER",
                        status="ACTIVE",
                    )
                )
                session.add(
                    JobAssignment(
                        id=uuid4(),
                        tenant_id=UUID(org_id),
                        job_id=UUID(job_id),
                        user_id=UUID(p_uid),
                        role=JobRole.INTERVIEWER.value,
                        assigned_by=UUID(owner_uid),
                    )
                )
                session.commit()
            finally:
                session.close()
            c0 = (await p_cli.get("/api/v1/auth/csrf")).json()["csrf_token"]
            await p_cli.post(
                f"/api/v1/organizations/{org_id}/select",
                headers={"X-CSRF-Token": c0, "Origin": "http://testserver"},
            )
            p_csrf = (await p_cli.get("/api/v1/auth/csrf")).json()["csrf_token"]
            panelists.append((p_cli, p_uid, p_csrf))

        try:
            await owner_client.put(
                f"/api/v1/interviews/{interview_id}/pack-context",
                json={
                    "application_id": main_app_id,
                    "stage": "TECHNICAL_DEEP_DIVE",
                    "duration_minutes": 60,
                    "schedule_revision": 1,
                    "status": "BOOKED",
                    "participant_user_ids": [owner_uid] + [u for _, u, _ in panelists],
                },
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )

            for i in range(2):
                await owner_client.post(
                    "/api/v1/applications",
                    json={
                        "job_id": job_id,
                        "new_candidate_display_name": f"Small Batch {i}",
                        "new_candidate_email": f"small.{i}@example.com",
                    },
                    headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
                )
                pr = await owner_client.post(
                    f"/api/v1/interviews/{interview_id}/pack-runs",
                    json={"reason": f"Pack run {i}", "framework_version_id": fw_ver_id},
                    headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
                )
                if i == 1:
                    await owner_client.post(
                        f"/api/v1/pack-revisions/{pr.json()['id']}/publish",
                        json={"expected_version": pr.json()["version"], "review_notes": "Publish"},
                        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
                    )

            for i in range(2):
                p_cli, _, p_csrf = panelists[i]
                d = await p_cli.put(
                    f"/api/v1/interviews/{interview_id}/feedback/me",
                    json={
                        "overall_observations": f"Panelist {i} observation.",
                        "human_recommendation": "ADVANCE_DISCUSSION",
                        "ratings": [
                            {
                                "competency_id": comp_id,
                                "rating": "4",
                                "observation": "Verified O(1) batching.",
                            }
                        ],
                    },
                    headers={"X-CSRF-Token": p_csrf, "Origin": "http://testserver"},
                )
                await p_cli.post(
                    f"/api/v1/interviews/{interview_id}/feedback/me/submit",
                    json={"expected_version": d.json()["version"]},
                    headers={"X-CSRF-Token": p_csrf, "Origin": "http://testserver"},
                )

            endpoints = {
                "applications": f"/api/v1/applications?job_id={job_id}",
                "packs": f"/api/v1/interviews/{interview_id}/packs",
                "feedback": f"/api/v1/interviews/{interview_id}/feedback",
                "audit_events": "/api/v1/audit-events",
                "analytics_pipeline": "/api/v1/analytics/pipeline",
            }

            for url in endpoints.values():
                assert (await owner_client.get(url)).status_code == 200

            small_counts: dict[str, int] = {}
            for name, url in endpoints.items():
                with _count_sql_statements(engine) as stmts:
                    r = await owner_client.get(url)
                    assert r.status_code == 200
                small_counts[name] = len(stmts)

            for i in range(2, 10):
                await owner_client.post(
                    "/api/v1/applications",
                    json={
                        "job_id": job_id,
                        "new_candidate_display_name": f"Large Batch {i}",
                        "new_candidate_email": f"large.{i}@example.com",
                    },
                    headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
                )
            for i in range(2, 6):
                await owner_client.post(
                    f"/api/v1/interviews/{interview_id}/pack-runs",
                    json={"reason": f"Scaled pack run {i}", "framework_version_id": fw_ver_id},
                    headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
                )
                p_cli, _, p_csrf = panelists[i]
                d = await p_cli.put(
                    f"/api/v1/interviews/{interview_id}/feedback/me",
                    json={
                        "overall_observations": f"Panelist {i} scaled observation.",
                        "human_recommendation": "ADVANCE_DISCUSSION",
                        "ratings": [
                            {
                                "competency_id": comp_id,
                                "rating": "3",
                                "observation": "Scaled panelist rating.",
                            }
                        ],
                    },
                    headers={"X-CSRF-Token": p_csrf, "Origin": "http://testserver"},
                )
                await p_cli.post(
                    f"/api/v1/interviews/{interview_id}/feedback/me/submit",
                    json={"expected_version": d.json()["version"]},
                    headers={"X-CSRF-Token": p_csrf, "Origin": "http://testserver"},
                )

            large_counts: dict[str, int] = {}
            for name, url in endpoints.items():
                with _count_sql_statements(engine) as stmts:
                    r = await owner_client.get(url)
                    assert r.status_code == 200
                large_counts[name] = len(stmts)

            for name in endpoints:
                assert large_counts[name] == small_counts[name], (
                    f"N+1 SQL regression on {name}: small={small_counts[name]}, large={large_counts[name]}"
                )
        finally:
            for p_cli, _, _ in panelists:
                await p_cli.aclose()


@pytest.mark.integration
async def test_seeded_synthetic_pilot_latency_budgets_and_restore_rehearsal_timing(
    perf_gate_env: tuple[Any, FrozenClock, FlakyDeleteStorageProxy],
) -> None:
    """Seeds synthetic pilot workload and verifies Playbook Section 5 latency budgets:
    - Ordinary read endpoints: p95 < 500 ms
    - Synchronous DB commands: p95 < 800 ms
    - Job admission endpoints: p95 < 1000 ms
    - Digital PDF extraction, pack generation, and local DB + Moto S3 + tombstone restore rehearsal.
    """
    app, clock, _ = perf_gate_env
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as owner_client,
        AsyncClient(transport=transport, base_url="http://testserver") as admin2_client,
    ):
        owner_uid, owner_csrf = await _login_user(owner_client)
        org_id, owner_csrf = await _setup_org(owner_client, owner_csrf, "Pilot Benchmark Org")
        admin2_uid, _ = await _login_user(admin2_client)

        session = app.state.db_manager.session_maker()
        try:
            session.add(
                Membership(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    user_id=UUID(admin2_uid),
                    role="ADMIN",
                    status="ACTIVE",
                )
            )
            session.commit()
        finally:
            session.close()
        a2_tmp = (await admin2_client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        await admin2_client.post(
            f"/api/v1/organizations/{org_id}/select",
            headers={"X-CSRF-Token": a2_tmp, "Origin": "http://testserver"},
        )
        admin2_csrf = (await admin2_client.get("/api/v1/auth/csrf")).json()["csrf_token"]

        pol = (await owner_client.get("/api/v1/organization-policy")).json()
        await owner_client.patch(
            "/api/v1/organization-policy",
            json={
                "expected_version": pol["version"],
                "reason": "Enable AI processing for pilot benchmark.",
                "ai_processing_enabled": True,
                "ai_interview_packs_enabled": True,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        read_samples_ms: list[float] = []
        command_samples_ms: list[float] = []
        admission_samples_ms: list[float] = []
        extraction_samples_ms: list[float] = []
        pack_gen_samples_ms: list[float] = []

        t0 = time.perf_counter()
        job_r = await owner_client.post(
            "/api/v1/jobs",
            json={
                "title": "Staff Backend Engineer (Pilot Benchmark)",
                "description": "PostgreSQL, Python, and cloud-native reliability.",
                "draft_requirements": [
                    {
                        "criterion_type": "SKILL_PRESENT",
                        "is_required": True,
                        "source_requirement_text": "PostgreSQL expertise",
                        "relevance_rationale": "Core store",
                        "configuration": {
                            "criterion_type": "SKILL_PRESENT",
                            "skill_name": "PostgreSQL",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        command_samples_ms.append((time.perf_counter() - t0) * 1000.0)
        assert job_r.status_code == 201
        job_id = job_r.json()["id"]

        t0 = time.perf_counter()
        pub_r = await owner_client.post(
            f"/api/v1/jobs/{job_id}/publish",
            json={"expected_version": 1},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        command_samples_ms.append((time.perf_counter() - t0) * 1000.0)
        assert pub_r.status_code == 200

        batch_id = (
            await owner_client.post(
                "/api/v1/upload-batches",
                json={"job_id": job_id},
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).json()["id"]

        admitted_apps: list[tuple[str, str, str]] = []
        for idx in range(10):
            pdf_bytes = _build_digital_pdf(
                [
                    f"Candidate {idx} — Staff Engineer",
                    "7 years building PostgreSQL and Python distributed systems.",
                ]
            )
            t0 = time.perf_counter()
            up_r = await owner_client.post(
                f"/api/v1/upload-batches/{batch_id}/items",
                data={
                    "new_candidate_display_name": f"Pilot Candidate {idx}",
                    "new_candidate_email": f"pilot.{idx}@example.com",
                },
                files={"file": (f"resume_{idx}.pdf", pdf_bytes, "application/pdf")},
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
            admission_samples_ms.append((time.perf_counter() - t0) * 1000.0)
            assert up_r.status_code == 201
            item = up_r.json()
            t_ext0 = time.perf_counter()
            _drive_scan_and_extraction(app, org_id, item["document_version_id"])
            extraction_samples_ms.append((time.perf_counter() - t_ext0) * 1000.0)
            a_id, c_id = _resolve_uploaded_app_and_candidate(
                app, org_id, job_id, item["document_id"]
            )
            admitted_apps.append((a_id, c_id, item["document_version_id"]))

        lead_app_id, lead_cand_id, lead_doc_ver_id = admitted_apps[0]
        await owner_client.post(
            f"/api/v1/applications/{lead_app_id}/document-bindings",
            json={"document_version_id": lead_doc_ver_id, "expected_binding_generation": 0},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        cur_v = (await owner_client.get(f"/api/v1/applications/{lead_app_id}")).json()["version"]
        for st in ("IN_REVIEW", "INTERVIEW_APPROVED", "INTERVIEWING"):
            t0 = time.perf_counter()
            tr = await owner_client.post(
                f"/api/v1/applications/{lead_app_id}/stage-transitions",
                json={"target_stage": st, "reason": f"Benchmark {st}", "expected_version": cur_v},
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
            command_samples_ms.append((time.perf_counter() - t0) * 1000.0)
            cur_v = tr.json()["version"]

        fw_r = await owner_client.post(
            "/api/v1/competency-frameworks",
            json={
                "job_id": job_id,
                "stage": "TECHNICAL_DEEP_DIVE",
                "name": "Pilot Benchmark Rubric",
                "slug": "pilot-bench-rubric",
                "summary": "Pilot rubric.",
                "behavioral_scale_guide": "1=Weak, 2=Fair, 3=Good, 4=Strong.",
                "competencies": [
                    {
                        "code": "PG_PERF",
                        "name": "PostgreSQL Performance",
                        "definition": "Query planning and indexing.",
                        "observation_required": True,
                        "display_order": 0,
                        "rubric_anchors": {
                            "1": "No indexing awareness.",
                            "2": "Basic single-column indexes.",
                            "3": "Composite tenant indexes & EXPLAIN.",
                            "4": "Deep MVCC, lock & hot-path tuning.",
                            "NOT_ASSESSED": "Not assessed.",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        fw_id = fw_r.json()["framework"]["id"]
        await owner_client.post(
            f"/api/v1/competency-frameworks/{fw_id}/versions/1/publish",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )

        interview_id = str(uuid4())
        t0 = time.perf_counter()
        ctx_r = await owner_client.put(
            f"/api/v1/interviews/{interview_id}/pack-context",
            json={
                "application_id": lead_app_id,
                "stage": "TECHNICAL_DEEP_DIVE",
                "duration_minutes": 60,
                "schedule_revision": 1,
                "status": "BOOKED",
                "participant_user_ids": [owner_uid],
                "booking_event_key": f"booking-{interview_id}",
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        admission_samples_ms.append((time.perf_counter() - t0) * 1000.0)
        assert ctx_r.status_code == 200
        pack_op_id = ctx_r.json()["admitted_operation_id"]
        assert pack_op_id is not None

        pack_worker = PackGenerationWorker(clock=clock)
        session = app.state.db_manager.session_maker()
        try:
            t0 = time.perf_counter()
            done_op = pack_worker.execute_operation(session, UUID(org_id), UUID(pack_op_id))
            pack_gen_samples_ms.append((time.perf_counter() - t0) * 1000.0)
            assert done_op.status == OperationStatus.SUCCEEDED.value
        finally:
            session.close()

        read_urls = [
            "/api/v1/jobs",
            f"/api/v1/applications?job_id={job_id}",
            f"/api/v1/interviews/{interview_id}/packs",
            "/api/v1/audit-events",
            "/api/v1/analytics/pipeline",
            "/api/v1/analytics/operations",
        ]
        for _ in range(4):
            for url in read_urls:
                t0 = time.perf_counter()
                r = await owner_client.get(url)
                read_samples_ms.append((time.perf_counter() - t0) * 1000.0)
                assert r.status_code == 200

        rehearsal_t0 = time.perf_counter()
        cur_v2 = (await owner_client.get(f"/api/v1/applications/{lead_app_id}")).json()["version"]
        await owner_client.post(
            f"/api/v1/applications/{lead_app_id}/stage-transitions",
            json={
                "target_stage": "WITHDRAWN",
                "reason": "Restore rehearsal erasure.",
                "expected_version": cur_v2,
            },
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        session = app.state.db_manager.session_maker()
        try:
            c_row = session.scalar(
                sa.select(Candidate).where(
                    Candidate.tenant_id == UUID(org_id),
                    Candidate.id == UUID(lead_cand_id),
                )
            )
            assert c_row is not None
            next_cnum = (c_row.current_contact_revision_number or 0) + 1
            crev = CandidateContactRevision(
                id=uuid4(),
                tenant_id=UUID(org_id),
                candidate_id=UUID(lead_cand_id),
                revision_number=next_cnum,
                display_name=c_row.display_name,
                normalized_name="pilot candidate 0",
                email="pilot.0@example.com",
                normalized_email="pilot.0@example.com",
                phone=None,
                source="CANDIDATE_SUBMITTED",
                created_by=UUID(owner_uid),
            )
            session.add(crev)
            session.flush()
            session.add(
                CandidateContactVerification(
                    id=uuid4(),
                    tenant_id=UUID(org_id),
                    candidate_id=UUID(lead_cand_id),
                    contact_revision_id=crev.id,
                    contact_revision_number=next_cnum,
                    verified_address="pilot.0@example.com",
                    verification_source="CANDIDATE_CONFIRMED_REPLY",
                    notes="Verified for restore rehearsal.",
                    actor_id=UUID(owner_uid),
                )
            )
            c_row.current_contact_revision_id = crev.id
            c_row.current_contact_revision_number = next_cnum
            c_row.verified_contact_revision_id = crev.id
            c_row.verified_contact_revision_number = next_cnum
            session.commit()
        finally:
            session.close()

        pr1 = (
            await owner_client.post(
                "/api/v1/privacy-requests",
                json={
                    "candidate_id": lead_cand_id,
                    "request_type": "ERASURE",
                    "request_reason": "Restore rehearsal purge.",
                },
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).json()
        pr2 = (
            await owner_client.post(
                f"/api/v1/privacy-requests/{pr1['id']}/verify",
                json={
                    "expected_version": pr1["version"],
                    "verification_method": "HUMAN_CONTACT_VERIFIED",
                    "verification_notes": "Verified.",
                },
                headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
            )
        ).json()
        await admin2_client.post(
            f"/api/v1/privacy-requests/{pr1['id']}/approve",
            json={
                "expected_version": pr2["version"],
                "override_active_applications": False,
                "approval_notes": "Approved.",
            },
            headers={"X-CSRF-Token": admin2_csrf, "Origin": "http://testserver"},
        )
        exec_r = await owner_client.post(
            f"/api/v1/privacy-requests/{pr1['id']}/execute",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        assert exec_r.json()["status"] == "COMPLETED"

        session = app.state.db_manager.session_maker()
        try:
            session.execute(
                sa.delete(DeletionLedger).where(DeletionLedger.tenant_id == UUID(org_id))
            )
            session.add(
                Candidate(
                    id=UUID(lead_cand_id),
                    tenant_id=UUID(org_id),
                    display_name="Restored Stale Candidate",
                    created_by=UUID(owner_uid),
                )
            )
            session.commit()
        finally:
            session.close()

        replay_r = await owner_client.post(
            "/api/v1/privacy/tombstones/replay",
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://testserver"},
        )
        restore_rehearsal_ms = round((time.perf_counter() - rehearsal_t0) * 1000.0, 2)
        assert replay_r.status_code == 200
        assert replay_r.json()["replayed_resurrected_count"] == 1

        metrics: dict[str, Any] = {
            "ordinary_reads_ms": {
                "p50": _percentile(read_samples_ms, 50),
                "p95": _percentile(read_samples_ms, 95),
                "p99": _percentile(read_samples_ms, 99),
                "budget_p95_ms": 500.0,
            },
            "db_commands_ms": {
                "p50": _percentile(command_samples_ms, 50),
                "p95": _percentile(command_samples_ms, 95),
                "p99": _percentile(command_samples_ms, 99),
                "budget_p95_ms": 800.0,
            },
            "job_admission_ms": {
                "p50": _percentile(admission_samples_ms, 50),
                "p95": _percentile(admission_samples_ms, 95),
                "p99": _percentile(admission_samples_ms, 99),
                "budget_p95_ms": 1000.0,
            },
            "digital_pdf_extraction_ms": {
                "p50": _percentile(extraction_samples_ms, 50),
                "p95": _percentile(extraction_samples_ms, 95),
                "budget_p95_ms": 20000.0,
            },
            "pack_generation_worker_ms": {
                "p95": _percentile(pack_gen_samples_ms, 95),
                "budget_p95_ms": 45000.0,
            },
            "restore_rehearsal_total_ms": restore_rehearsal_ms,
        }
        Path("/tmp/p18_benchmark_metrics.json").write_text(
            json.dumps(metrics, indent=2), encoding="utf-8"
        )

        assert metrics["ordinary_reads_ms"]["p95"] < 500.0, metrics
        assert metrics["db_commands_ms"]["p95"] < 800.0, metrics
        assert metrics["job_admission_ms"]["p95"] < 1000.0, metrics
        assert metrics["digital_pdf_extraction_ms"]["p95"] < 20000.0, metrics
        assert metrics["pack_generation_worker_ms"]["p95"] < 45000.0, metrics
