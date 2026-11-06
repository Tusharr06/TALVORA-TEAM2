"""Integration tests for Candidates/Applications REST API: pipeline, auth, and isolation.

NOTE: `get_settings()` in `talvora.config` constructs a fresh `Settings()` from the real
on-disk `.env` file rather than using the `test_settings` fixture injected into
`app.state`. That pre-existing (P04) dependency-injection gap makes any test that sends
an explicit `Origin: http://testserver` header see the *real* `.env`'s
`TALVORA_ALLOWED_HOSTS` (which omits "testserver") and fail Origin validation. This test
module works around it locally via `app.dependency_overrides[get_settings]`, without
touching the shared `enforce_csrf_and_origin` dependency itself.
"""

import urllib.parse
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings, get_settings
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base


@pytest.fixture
def pipeline_test_app(
    test_settings: Settings,
) -> tuple[Any, MockOidcClient, InMemorySystemMailProvider]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    # Work around the pre-existing get_settings() DI gap (see module docstring) so Origin
    # validation uses the injected test settings instead of re-reading the real .env file.
    app.dependency_overrides[get_settings] = lambda: test_settings

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
    """Complete OIDC login, create + select an org, return (session_token, csrf_token, org_id)."""
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
    client: AsyncClient, csrf_token: str, title: str = "Platform Engineer"
) -> str:
    create_resp = await client.post(
        "/api/v1/jobs",
        json={
            "title": title,
            "description": "Builds and operates core platform services.",
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


@pytest.mark.integration
async def test_candidate_and_application_full_pipeline_with_contact_verification(
    pipeline_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = pipeline_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)

        # 1. Create candidate -- surfaces (empty) duplicate suggestions.
        create_candidate_resp = await client.post(
            "/api/v1/candidates",
            json={"display_name": "Priya Sharma", "email": "priya@example.com"},
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "Idempotency-Key": f"cand-{uuid4()}",
            },
        )
        assert create_candidate_resp.status_code == 201
        candidate_payload = create_candidate_resp.json()
        candidate_id = candidate_payload["candidate"]["id"]
        assert candidate_payload["candidate"]["is_contact_verified"] is False
        assert candidate_payload["duplicate_suggestions"] == []

        # 2. Create a second candidate sharing the same email -- a duplicate suggestion
        #    is surfaced, but no automatic merge occurs.
        dup_resp = await client.post(
            "/api/v1/candidates",
            json={"display_name": "Priya S.", "email": "priya@example.com"},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert dup_resp.status_code == 201
        dup_payload = dup_resp.json()
        assert len(dup_payload["duplicate_suggestions"]) == 1
        assert dup_payload["duplicate_suggestions"][0]["candidate_id"] == candidate_id
        assert "@" not in dup_payload["duplicate_suggestions"][0]["masked_email"].split("@")[0][1:]

        duplicates_listing = await client.get(
            "/api/v1/candidates/duplicates", params={"email": "priya@example.com"}
        )
        assert duplicates_listing.status_code == 200
        assert len(duplicates_listing.json()) == 2

        # 3. Verify the candidate's contact -- explicitly NOT proof of ownership.
        verify_resp = await client.post(
            f"/api/v1/candidates/{candidate_id}/contact-verifications",
            json={
                "contact_revision_number": 1,
                "verified_address": "priya@example.com",
                "verification_source": "RECRUITER_ATTESTATION",
                "expected_version": 1,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert verify_resp.status_code == 201
        assert verify_resp.json()["is_proof_of_ownership"] is False

        get_candidate_resp = await client.get(f"/api/v1/candidates/{candidate_id}")
        assert get_candidate_resp.json()["is_contact_verified"] is True

        # 4. Create an application for this candidate against the job.
        create_app_resp = await client.post(
            "/api/v1/applications",
            json={"job_id": job_id, "candidate_id": candidate_id},
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "Idempotency-Key": f"app-{uuid4()}",
            },
        )
        assert create_app_resp.status_code == 201
        application = create_app_resp.json()
        application_id = application["id"]
        assert application["stage"] == "NEW"
        assert application["version"] == 1

        # 5. Duplicate active application attempt -> 409.
        dup_app_resp = await client.post(
            "/api/v1/applications",
            json={"job_id": job_id, "candidate_id": candidate_id},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert dup_app_resp.status_code == 409
        assert dup_app_resp.json()["code"] == "DUPLICATE_ACTIVE_APPLICATION"

        # 6. Progress through the pipeline via explicit human stage transitions.
        transition_resp = await client.post(
            f"/api/v1/applications/{application_id}/stage-transitions",
            json={"target_stage": "IN_REVIEW", "expected_version": 1},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert transition_resp.status_code == 200
        assert transition_resp.json()["stage"] == "IN_REVIEW"

        # 7. Stale expected_version -> 409 VERSION_CONFLICT ("simultaneous decisions").
        stale_resp = await client.post(
            f"/api/v1/applications/{application_id}/stage-transitions",
            json={"target_stage": "INTERVIEW_APPROVED", "expected_version": 1},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert stale_resp.status_code == 409
        assert stale_resp.json()["code"] == "VERSION_CONFLICT"

        # 8. Repeated transition with the same Idempotency-Key replays the cached result
        #    exactly once rather than re-executing the transition.
        idem_key = f"stage-{uuid4()}"
        approve_payload = {"target_stage": "INTERVIEW_APPROVED", "expected_version": 2}
        first_approve = await client.post(
            f"/api/v1/applications/{application_id}/stage-transitions",
            json=approve_payload,
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "Idempotency-Key": idem_key,
            },
        )
        assert first_approve.status_code == 200
        assert first_approve.json()["version"] == 3

        second_approve = await client.post(
            f"/api/v1/applications/{application_id}/stage-transitions",
            json=approve_payload,
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "Idempotency-Key": idem_key,
            },
        )
        assert second_approve.status_code == 200
        assert second_approve.json()["version"] == 3  # unchanged: not re-executed

        history_resp = await client.get(f"/api/v1/applications/{application_id}/stage-history")
        assert history_resp.status_code == 200
        # NEW(creation) -> IN_REVIEW -> INTERVIEW_APPROVED: exactly 3 immutable events,
        # even though the approval was requested twice via the idempotent replay.
        assert len(history_resp.json()) == 3

        # 9. Interview eligibility is true now (approved stage + verified contact + open job).
        eligibility_resp = await client.get(
            f"/api/v1/applications/{application_id}/interview-eligibility"
        )
        assert eligibility_resp.status_code == 200
        assert eligibility_resp.json()["eligible"] is True

        # 10. Candidate application history.
        candidate_history_resp = await client.get(f"/api/v1/candidates/{candidate_id}/applications")
        assert candidate_history_resp.status_code == 200
        assert len(candidate_history_resp.json()) == 1

        # 11. Job-scoped application listing.
        job_scoped_resp = await client.get("/api/v1/applications", params={"job_id": job_id})
        assert job_scoped_resp.status_code == 200
        assert job_scoped_resp.json()["total_count"] == 1

        # 12. Withdraw -- no longer interview eligible, and job assignment required on reopen.
        withdraw_resp = await client.post(
            f"/api/v1/applications/{application_id}/stage-transitions",
            json={
                "target_stage": "WITHDRAWN",
                "reason": "Candidate accepted another offer.",
                "expected_version": 3,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert withdraw_resp.status_code == 200

        eligibility_after_withdraw = await client.get(
            f"/api/v1/applications/{application_id}/interview-eligibility"
        )
        assert eligibility_after_withdraw.json()["eligible"] is False


@pytest.mark.integration
async def test_malformed_email_and_missing_reason_rejected(
    pipeline_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = pipeline_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)
        job_id = await _create_open_job(client, csrf_token)

        bad_email_resp = await client.post(
            "/api/v1/candidates",
            json={"display_name": "Bad Email", "email": "not-an-email"},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert bad_email_resp.status_code == 422

        create_app_resp = await client.post(
            "/api/v1/applications",
            json={"job_id": job_id, "new_candidate_is_draft": True},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert create_app_resp.status_code == 201
        application_id = create_app_resp.json()["id"]

        missing_reason_resp = await client.post(
            f"/api/v1/applications/{application_id}/stage-transitions",
            json={"target_stage": "REJECTED", "expected_version": 1},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert missing_reason_resp.status_code == 422


@pytest.mark.integration
async def test_unauthenticated_requests_rejected_with_401(
    pipeline_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = pipeline_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        resp = await client.get("/api/v1/candidates")
        assert resp.status_code == 401

        resp2 = await client.post(
            "/api/v1/applications",
            json={"job_id": str(uuid4()), "new_candidate_is_draft": True},
        )
        assert resp2.status_code == 401


@pytest.mark.integration
async def test_cross_tenant_candidate_and_job_concealment(
    pipeline_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = pipeline_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client_a,
    ):
        _, csrf_a, _ = await _login_and_setup_org(
            client_a, email="recruiter.a@talvora.test", org_name="Tenant A Org"
        )
        job_id_a = await _create_open_job(client_a, csrf_a, title="Tenant A Role")

        candidate_resp = await client_a.post(
            "/api/v1/candidates",
            json={"display_name": "Tenant A Candidate", "email": "a@example.com"},
            headers={"X-CSRF-Token": csrf_a, "Origin": "http://testserver"},
        )
        candidate_id_a = candidate_resp.json()["candidate"]["id"]

    async with (
        ASGITransport(app=app) as transport_b,
        AsyncClient(transport=transport_b, base_url="http://testserver") as client_b,
    ):
        _, csrf_b, _ = await _login_and_setup_org(
            client_b, email="recruiter.b@talvora.test", org_name="Tenant B Org"
        )

        # Tenant B cannot see Tenant A's job.
        get_job_resp = await client_b.get(f"/api/v1/jobs/{job_id_a}")
        assert get_job_resp.status_code == 404

        # Tenant B cannot see Tenant A's candidate.
        get_candidate_resp = await client_b.get(f"/api/v1/candidates/{candidate_id_a}")
        assert get_candidate_resp.status_code == 404

        # Tenant B cannot create an application pairing its own job with Tenant A's candidate.
        job_id_b = await _create_open_job(client_b, csrf_b, title="Tenant B Role")
        cross_app_resp = await client_b.post(
            "/api/v1/applications",
            json={"job_id": job_id_b, "candidate_id": candidate_id_a},
            headers={"X-CSRF-Token": csrf_b, "Origin": "http://testserver"},
        )
        assert cross_app_resp.status_code == 404

        # Nor Tenant A's job with a brand-new Tenant B candidate ID pointed at Tenant A's job.
        cross_job_resp = await client_b.post(
            "/api/v1/applications",
            json={"job_id": job_id_a, "new_candidate_is_draft": True},
            headers={"X-CSRF-Token": csrf_b, "Origin": "http://testserver"},
        )
        assert cross_job_resp.status_code == 404
