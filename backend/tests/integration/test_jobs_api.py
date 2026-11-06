"""Integration tests for Jobs REST API: CRUD, Assignments, Versioning, Status, and Cross-Tenant Isolation."""

import urllib.parse
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base


@pytest.fixture
def jobs_test_app(
    test_settings: Settings,
) -> tuple[Any, MockOidcClient, InMemorySystemMailProvider]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

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
    """Helper completing OIDC login, creating org, and returning session token, csrf token, and org ID."""
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

    # Create Organization
    create_org_resp = await client.post(
        "/api/v1/organizations",
        json={"name": org_name, "slug": f"org-{uuid4().hex[:8]}"},
        headers={
            "X-CSRF-Token": csrf_token,
            "Origin": "http://testserver",
        },
    )
    assert create_org_resp.status_code == 200
    org_id = create_org_resp.json()["id"]

    # Select Organization
    sel_resp = await client.post(
        f"/api/v1/organizations/{org_id}/select",
        headers={
            "X-CSRF-Token": csrf_token,
            "Origin": "http://testserver",
        },
    )
    assert sel_resp.status_code == 200

    # Fetch new CSRF token after session rotation
    csrf_resp2 = await client.get("/api/v1/auth/csrf")
    csrf_token2 = csrf_resp2.json()["csrf_token"]
    session_token2 = client.cookies.get("talvora_session") or session_token

    return session_token2, csrf_token2, org_id


@pytest.mark.integration
async def test_job_crud_with_csrf_creator_assignment_and_optimistic_locking(
    jobs_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = jobs_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)

        # 1. Create Job Vacancy
        create_resp = await client.post(
            "/api/v1/jobs",
            json={
                "title": "Principal Distributed Systems Engineer",
                "description": "Architect high-performance distributed pipelines.",
                "department": "Platform Engineering",
                "location": "San Francisco, CA / Remote",
                "employment_type": "FULL_TIME",
                "draft_requirements": [
                    {
                        "criterion_type": "SKILL_PRESENT",
                        "is_required": True,
                        "source_requirement_text": "Strong mastery of Python and FastAPI",
                        "relevance_rationale": "Platform services written in FastAPI.",
                        "configuration": {
                            "criterion_type": "SKILL_PRESENT",
                            "skill_name": "Python",
                        },
                    }
                ],
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "Idempotency-Key": f"idemp-job-{uuid4()}",
            },
        )
        assert create_resp.status_code == 201
        job_data = create_resp.json()
        job_id = job_data["id"]
        assert job_data["title"] == "Principal Distributed Systems Engineer"
        assert job_data["status"] == "DRAFT"
        assert job_data["version"] == 1

        # 2. Verify Creator Job Assignment
        assignments_resp = await client.get(f"/api/v1/jobs/{job_id}/assignments")
        assert assignments_resp.status_code == 200
        assignments = assignments_resp.json()
        assert len(assignments) == 1
        assert assignments[0]["job_id"] == job_id

        # 3. Get Job Details
        get_resp = await client.get(f"/api/v1/jobs/{job_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == job_id

        # 4. Patch Job with matching If-Match header
        patch_resp = await client.patch(
            f"/api/v1/jobs/{job_id}",
            json={
                "department": "Core Infrastructure",
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "If-Match": '"1"',
            },
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["department"] == "Core Infrastructure"
        assert patch_resp.json()["version"] == 2

        # 5. Stale If-Match triggers 409 VERSION_CONFLICT
        stale_patch = await client.patch(
            f"/api/v1/jobs/{job_id}",
            json={"location": "Austin, TX"},
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "If-Match": '"1"',
            },
        )
        assert stale_patch.status_code == 409
        assert stale_patch.json()["code"] == "VERSION_CONFLICT"


@pytest.mark.integration
async def test_publish_requirements_creates_immutable_snapshot_and_idempotency(
    jobs_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = jobs_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)

        # 1. Create Vacancy with draft criteria
        create_resp = await client.post(
            "/api/v1/jobs",
            json={
                "title": "Lead Security Engineer",
                "description": "Zero trust architecture and IAM governance.",
                "draft_requirements": [
                    {
                        "criterion_type": "MIN_EXPERIENCE_MONTHS",
                        "is_required": True,
                        "source_requirement_text": "5+ years of security engineering experience",
                        "relevance_rationale": "Senior technical leadership required.",
                        "configuration": {
                            "criterion_type": "MIN_EXPERIENCE_MONTHS",
                            "min_months": 60,
                            "relevant_titles": ["Security Engineer", "AppSec Specialist"],
                        },
                    },
                    {
                        "criterion_type": "MANUAL_REQUIREMENT",
                        "is_required": True,
                        "source_requirement_text": "Pass federal security background investigation",
                        "relevance_rationale": "Compliance mandate for federal sector workloads.",
                        "requires_human_review": True,
                        "configuration": {
                            "criterion_type": "MANUAL_REQUIREMENT",
                            "human_verification_prompt": "Verify e-QIP submission confirmation with security office.",
                            "category": "BACKGROUND_CHECK",
                        },
                    },
                    {
                        "criterion_type": "SKILL_PRESENT",
                        "is_required": False,
                        "source_requirement_text": "Experience with Rust or Go systems programming",
                        "relevance_rationale": "Preferred language for security tool agents.",
                        "configuration": {
                            "criterion_type": "SKILL_PRESENT",
                            "skill_name": "Rust",
                            "synonyms": ["rustlang"],
                        },
                    },
                ],
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
            },
        )
        job_id = create_resp.json()["id"]

        # 2. Publish Requirements
        publish_key = f"pub-key-{uuid4()}"
        pub_resp = await client.post(
            f"/api/v1/jobs/{job_id}/publish",
            json={
                "change_summary": "Initial baseline requirement version",
                "expected_version": 1,
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "Idempotency-Key": publish_key,
                "If-Match": '"1"',
            },
        )
        assert pub_resp.status_code == 200
        ver1 = pub_resp.json()
        assert ver1["version_number"] == 1
        assert len(ver1["criteria"]) == 3

        # 3. Repeated Idempotent Publication returns cached result
        pub_replay = await client.post(
            f"/api/v1/jobs/{job_id}/publish",
            json={
                "change_summary": "Initial baseline requirement version",
                "expected_version": 1,
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://testserver",
                "Idempotency-Key": publish_key,
            },
        )
        assert pub_replay.status_code == 200
        assert pub_replay.json()["id"] == ver1["id"]

        # 4. Verify Job status updated to OPEN
        job_get = await client.get(f"/api/v1/jobs/{job_id}")
        assert job_get.json()["status"] == "OPEN"
        assert job_get.json()["current_published_version_number"] == 1
        assert job_get.json()["version"] == 2

        # 5. Fetch Requirement Versions list
        versions_list = await client.get(f"/api/v1/jobs/{job_id}/requirement-versions")
        assert len(versions_list.json()) == 1

        # 6. Fetch exact version snapshot
        version_detail = await client.get(f"/api/v1/jobs/{job_id}/requirement-versions/1")
        assert version_detail.status_code == 200
        assert version_detail.json()["version_number"] == 1


@pytest.mark.integration
async def test_job_status_transitions_and_rules(
    jobs_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = jobs_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)

        create_resp = await client.post(
            "/api/v1/jobs",
            json={
                "title": "DevOps Engineer",
                "description": "Infrastructure automation.",
                "draft_requirements": [
                    {
                        "criterion_type": "SKILL_PRESENT",
                        "is_required": True,
                        "source_requirement_text": "Terraform proficiency",
                        "relevance_rationale": "IaC deployments.",
                        "configuration": {
                            "criterion_type": "SKILL_PRESENT",
                            "skill_name": "Terraform",
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        job_id = create_resp.json()["id"]

        # 1. DRAFT -> OPEN without publish is rejected
        bad_open = await client.post(
            f"/api/v1/jobs/{job_id}/status-transitions",
            json={"target_status": "OPEN", "expected_version": 1},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert bad_open.status_code == 409
        assert bad_open.json()["code"] == "INVALID_TRANSITION"

        # Publish to open
        await client.post(
            f"/api/v1/jobs/{job_id}/publish",
            json={"expected_version": 1},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )

        # 2. OPEN -> PAUSED
        pause_resp = await client.post(
            f"/api/v1/jobs/{job_id}/status-transitions",
            json={"target_status": "PAUSED", "expected_version": 2},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "PAUSED"

        # 3. PAUSED -> CLOSED without reason fails
        bad_close = await client.post(
            f"/api/v1/jobs/{job_id}/status-transitions",
            json={"target_status": "CLOSED", "reason": "", "expected_version": 3},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert bad_close.status_code == 422

        # 4. PAUSED -> CLOSED with reason succeeds
        close_resp = await client.post(
            f"/api/v1/jobs/{job_id}/status-transitions",
            json={
                "target_status": "CLOSED",
                "reason": "Position filled by internal candidate.",
                "expected_version": 3,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert close_resp.status_code == 200
        assert close_resp.json()["status"] == "CLOSED"
        assert close_resp.json()["closed_reason"] == "Position filled by internal candidate."

        # 5. CLOSED -> OPEN with reason succeeds
        reopen_resp = await client.post(
            f"/api/v1/jobs/{job_id}/status-transitions",
            json={
                "target_status": "OPEN",
                "reason": "Additional headcount approved.",
                "expected_version": 4,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert reopen_resp.status_code == 200
        assert reopen_resp.json()["status"] == "OPEN"
        assert reopen_resp.json()["closed_reason"] is None


@pytest.mark.integration
async def test_cross_tenant_resource_concealment(
    jobs_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = jobs_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        # Tenant A creates a job
        _, csrf_a, _ = await _login_and_setup_org(
            client, email="alice@org-a.test", org_name="Org Alpha"
        )
        job_a_resp = await client.post(
            "/api/v1/jobs",
            json={"title": "Alpha Secret Job", "description": "Classified role."},
            headers={"X-CSRF-Token": csrf_a, "Origin": "http://testserver"},
        )
        job_a_id = job_a_resp.json()["id"]

    # Tenant B attempts access with completely different session
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client_b,
    ):
        _, csrf_b, _ = await _login_and_setup_org(
            client_b, email="bob@org-b.test", org_name="Org Beta"
        )

        # GET job in Tenant A -> Returns 404 Not Found (Concealment)
        get_res = await client_b.get(f"/api/v1/jobs/{job_a_id}")
        assert get_res.status_code == 404
        assert get_res.json()["code"] == "RESOURCE_NOT_FOUND"

        # PATCH job in Tenant A -> Returns 404 Not Found
        patch_res = await client_b.patch(
            f"/api/v1/jobs/{job_a_id}",
            json={"title": "Hacked Title"},
            headers={"X-CSRF-Token": csrf_b, "Origin": "http://testserver"},
        )
        assert patch_res.status_code == 404

        # PUBLISH job in Tenant A -> Returns 404 Not Found
        pub_res = await client_b.post(
            f"/api/v1/jobs/{job_a_id}/publish",
            json={},
            headers={"X-CSRF-Token": csrf_b, "Origin": "http://testserver"},
        )
        assert pub_res.status_code == 404


@pytest.mark.integration
async def test_cursor_pagination_and_team_filtering(
    jobs_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = jobs_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, _ = await _login_and_setup_org(client)

        # Create a Team
        team_resp = await client.post(
            "/api/v1/teams",
            json={"name": "Data Systems", "description": "Big data team."},
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
        )
        assert team_resp.status_code == 200
        team_id = team_resp.json()["id"]

        # Create 3 jobs for the team and 2 jobs without team
        for i in range(3):
            await client.post(
                "/api/v1/jobs",
                json={
                    "title": f"Data Engineer {i + 1}",
                    "description": "Building data pipelines.",
                    "team_id": team_id,
                },
                headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
            )

        for i in range(2):
            await client.post(
                "/api/v1/jobs",
                json={
                    "title": f"General Role {i + 1}",
                    "description": "General role.",
                },
                headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
            )

        # Filter by team_id
        team_jobs_resp = await client.get(f"/api/v1/jobs?team_id={team_id}")
        assert team_jobs_resp.status_code == 200
        assert team_jobs_resp.json()["total_count"] == 3

        # Pagination with limit=2
        page1 = await client.get("/api/v1/jobs?limit=2")
        assert page1.status_code == 200
        assert len(page1.json()["items"]) == 2
        assert page1.json()["total_count"] == 5
        cursor = page1.json()["next_cursor"]
        assert cursor is not None

        # Page 2 using cursor
        page2 = await client.get(f"/api/v1/jobs?limit=2&cursor={cursor}")
        assert page2.status_code == 200
        assert len(page2.json()["items"]) == 2
        cursor2 = page2.json()["next_cursor"]
        assert cursor2 is not None

        # Page 3 (final item)
        page3 = await client.get(f"/api/v1/jobs?limit=2&cursor={cursor2}")
        assert page3.status_code == 200
        assert len(page3.json()["items"]) == 1
        assert page3.json()["next_cursor"] is None
