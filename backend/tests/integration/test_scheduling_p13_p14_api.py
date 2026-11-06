"""Integration tests for Scheduling P13 (External Actions & Booking Saga) and P14 (Interview Lifecycle)."""

import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings, get_settings
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.integrations.infrastructure.google.calendar import MockGoogleCalendarAdapter
from talvora.modules.integrations.infrastructure.google.gmail import MockGoogleGmailAdapter
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base


@pytest.fixture
def scheduling_test_app(
    test_settings: Settings,
) -> tuple[Any, MockOidcClient, MockGoogleCalendarAdapter, MockGoogleGmailAdapter]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    app.dependency_overrides[get_settings] = lambda: test_settings

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    mock_cal = MockGoogleCalendarAdapter()
    mock_gmail = MockGoogleGmailAdapter()
    mail_provider = InMemorySystemMailProvider()

    app.state.oidc_client = mock_oidc
    app.state.mail_provider = mail_provider
    app.state.calendar_provider = mock_cal
    app.state.gmail_provider = mock_gmail
    return app, mock_oidc, mock_cal, mock_gmail


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


async def _setup_interview_prerequisites(
    client: AsyncClient,
    csrf_token: str,
) -> tuple[str, str, str, str]:
    """Creates open Job, verified Candidate, and INTERVIEW_APPROVED Application."""
    # 1. Job
    create_job_resp = await client.post(
        "/api/v1/jobs",
        json={
            "title": "Senior Distributed Systems Engineer",
            "description": "Architects high-throughput resilient services.",
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
    assert create_job_resp.status_code == 201
    job_id = create_job_resp.json()["id"]

    publish_job_resp = await client.post(
        f"/api/v1/jobs/{job_id}/publish",
        json={"expected_version": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert publish_job_resp.status_code == 200

    # 2. Candidate
    cand_resp = await client.post(
        "/api/v1/candidates",
        json={"display_name": "Devin Chen", "email": "devin@candidate.test"},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert cand_resp.status_code == 201
    cand_data = cand_resp.json()["candidate"]
    candidate_id = cand_data["id"]

    verify_cand_resp = await client.post(
        f"/api/v1/candidates/{candidate_id}/contact-verifications",
        json={
            "contact_revision_number": 1,
            "verified_address": "devin@candidate.test",
            "verification_source": "RECRUITER_ATTESTATION",
            "expected_version": 1,
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert verify_cand_resp.status_code == 201

    # 3. Application
    app_resp = await client.post(
        "/api/v1/applications",
        json={"job_id": job_id, "candidate_id": candidate_id},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert app_resp.status_code == 201
    app_id = app_resp.json()["id"]

    # Move to IN_REVIEW then INTERVIEW_APPROVED
    t1_resp = await client.post(
        f"/api/v1/applications/{app_id}/stage-transitions",
        json={"target_stage": "IN_REVIEW", "expected_version": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert t1_resp.status_code == 200

    t2_resp = await client.post(
        f"/api/v1/applications/{app_id}/stage-transitions",
        json={"target_stage": "INTERVIEW_APPROVED", "expected_version": 2},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert t2_resp.status_code == 200

    return job_id, candidate_id, app_id, "devin@candidate.test"


@pytest.mark.integration
async def test_p13_external_actions_and_booking_saga_api_flow(
    scheduling_test_app: tuple[
        Any, MockOidcClient, MockGoogleCalendarAdapter, MockGoogleGmailAdapter
    ],
) -> None:
    app, _, mock_cal, mock_gmail = scheduling_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        headers = {"X-CSRF-Token": csrf_token, "Origin": "http://testserver"}

        job_id, candidate_id, application_id, cand_email = await _setup_interview_prerequisites(
            client, csrf_token
        )

        # 1. Create Interview Request
        base_day = (datetime.now(UTC) + timedelta(days=2)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        target_start = base_day
        target_end = base_day + timedelta(days=4)

        create_req_payload = {
            "application_id": application_id,
            "title": "Systems Architecture Round",
            "target_date_start": target_start.isoformat(),
            "target_date_end": target_end.isoformat(),
            "duration_minutes": 60,
            "location_type": "GOOGLE_MEET",
            "timezone": "UTC",
            "working_hours_start": 9,
            "working_hours_end": 17,
            "working_days": [0, 1, 2, 3, 4, 5, 6],
            "buffer_before_minutes": 15,
            "buffer_after_minutes": 15,
            "minimum_lead_time_hours": 24,
        }
        req_resp = await client.post(
            "/api/v1/interview-requests",
            json=create_req_payload,
            headers=headers,
        )
        assert req_resp.status_code == 201
        req_data = req_resp.json()
        request_id = req_data["id"]
        assert req_data["status"] == "DRAFT"

        # 2. Issue candidate availability link
        link_resp = await client.post(
            f"/api/v1/interview-requests/{request_id}/availability-links",
            headers=headers,
        )
        assert link_resp.status_code == 200
        link_url = link_resp.json()["availability_url"]
        raw_token = link_url.split("/")[-1]

        # 3. Public candidate submits availability intervals
        avail_start = base_day + timedelta(hours=10)
        avail_end = base_day + timedelta(hours=16)
        submit_resp = await client.post(
            f"/api/v1/public/availability/{raw_token}",
            json={
                "candidate_timezone": "UTC",
                "available_intervals": [
                    {"start": avail_start.isoformat(), "end": avail_end.isoformat()}
                ],
            },
        )
        assert submit_resp.status_code == 200
        assert submit_resp.json()["status"] == "submitted"

        # 4. Recruiter generates slot proposals
        gen_resp = await client.post(
            f"/api/v1/interview-requests/{request_id}/proposals/generate",
            json={"step_minutes": 30, "max_proposals": 3},
            headers=headers,
        )
        assert gen_resp.status_code == 200
        proposals = gen_resp.json()
        assert len(proposals) >= 1
        chosen_proposal = proposals[0]
        proposal_id = chosen_proposal["id"]

        # 5. Recruiter creates external action (CREATE_INTERVIEW)
        create_action_resp = await client.post(
            "/api/v1/external-actions",
            json={
                "action_type": "CREATE_INTERVIEW",
                "resource_type": "interview_request",
                "resource_id": request_id,
                "resource_version": 1,
                "proposal_id": proposal_id,
                "custom_notes": "Please prepare for systems design discussion.",
            },
            headers=headers,
        )
        assert create_action_resp.status_code == 201
        action_data = create_action_resp.json()
        action_id = action_data["id"]
        assert action_data["status"] == "PENDING_APPROVAL"
        assert action_data["verified_recipient_email"] == cand_email
        payload_hash = action_data["payload_hash"]

        # 6. List and Get External Actions
        list_action_resp = await client.get("/api/v1/external-actions", headers=headers)
        assert list_action_resp.status_code == 200
        assert any(a["id"] == action_id for a in list_action_resp.json())

        get_action_resp = await client.get(f"/api/v1/external-actions/{action_id}", headers=headers)
        assert get_action_resp.status_code == 200
        assert get_action_resp.json()["id"] == action_id

        # 7. Exact approval validation: Tampered hash must fail with 409
        bad_hash_resp = await client.post(
            f"/api/v1/external-actions/{action_id}/approve",
            json={"expected_payload_hash": "bad" * 16, "expected_resource_version": 1},
            headers=headers,
        )
        assert bad_hash_resp.status_code == 409

        # 8. Approve and execute Booking Saga
        approve_resp = await client.post(
            f"/api/v1/external-actions/{action_id}/approve",
            json={"expected_payload_hash": payload_hash, "expected_resource_version": 1},
            headers=headers,
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "SUCCEEDED"

        # Verify Google Calendar event was created
        assert len(mock_cal.events) >= 1

        # 9. Query Interviews
        interviews_resp = await client.get("/api/v1/interviews", headers=headers)
        assert interviews_resp.status_code == 200
        interview_items = interviews_resp.json()
        assert len(interview_items) == 1
        interview_id = interview_items[0]["id"]
        assert interview_items[0]["status"] == "SCHEDULED"

        detail_resp = await client.get(f"/api/v1/interviews/{interview_id}", headers=headers)
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["current_revision"]["revision_number"] == 1
        assert len(detail["reservations"]) >= 1
        assert detail["reservations"][0]["status"] == "ACTIVE"


@pytest.mark.integration
async def test_p14_lifecycle_public_rsvp_reschedule_cancellation_completion_flow(
    scheduling_test_app: tuple[
        Any, MockOidcClient, MockGoogleCalendarAdapter, MockGoogleGmailAdapter
    ],
) -> None:
    app, _, mock_cal, _ = scheduling_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token, org_id = await _login_and_setup_org(client)
        headers = {"X-CSRF-Token": csrf_token, "Origin": "http://testserver"}

        job_id, candidate_id, application_id, cand_email = await _setup_interview_prerequisites(
            client, csrf_token
        )

        # 1. Setup Request and Book initial interview
        base_day = (datetime.now(UTC) + timedelta(days=3)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        target_start = base_day
        target_end = base_day + timedelta(days=4)

        req_resp = await client.post(
            "/api/v1/interview-requests",
            json={
                "application_id": application_id,
                "title": "Pair Programming Session",
                "target_date_start": target_start.isoformat(),
                "target_date_end": target_end.isoformat(),
                "duration_minutes": 45,
                "location_type": "GOOGLE_MEET",
                "timezone": "UTC",
                "working_hours_start": 9,
                "working_hours_end": 17,
                "working_days": [0, 1, 2, 3, 4, 5, 6],
                "buffer_before_minutes": 10,
                "buffer_after_minutes": 10,
                "minimum_lead_time_hours": 12,
            },
            headers=headers,
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["id"]

        link_resp = await client.post(
            f"/api/v1/interview-requests/{request_id}/availability-links",
            headers=headers,
        )
        raw_token = link_resp.json()["availability_url"].split("/")[-1]

        avail_start = base_day + timedelta(hours=10)
        avail_end = base_day + timedelta(hours=16)
        await client.post(
            f"/api/v1/public/availability/{raw_token}",
            json={
                "candidate_timezone": "UTC",
                "available_intervals": [
                    {"start": avail_start.isoformat(), "end": avail_end.isoformat()}
                ],
            },
        )

        gen_resp = await client.post(
            f"/api/v1/interview-requests/{request_id}/proposals/generate",
            json={"step_minutes": 30, "max_proposals": 3},
            headers=headers,
        )
        proposals = gen_resp.json()
        assert len(proposals) >= 2
        p1 = proposals[0]
        p2 = proposals[1]

        # Book initial
        action_resp = await client.post(
            "/api/v1/external-actions",
            json={
                "action_type": "CREATE_INTERVIEW",
                "resource_type": "interview_request",
                "resource_id": request_id,
                "proposal_id": p1["id"],
            },
            headers=headers,
        )
        action_data = action_resp.json()
        await client.post(
            f"/api/v1/external-actions/{action_data['id']}/approve",
            json={
                "expected_payload_hash": action_data["payload_hash"],
                "expected_resource_version": 1,
            },
            headers=headers,
        )

        list_resp = await client.get("/api/v1/interviews", headers=headers)
        interview_id = list_resp.json()[0]["id"]

        # 2. Public Candidate RSVP Confirmation
        # Issue fresh link for candidate confirmation
        link_resp2 = await client.post(
            f"/api/v1/interview-requests/{request_id}/availability-links",
            headers=headers,
        )
        cand_token = link_resp2.json()["availability_url"].split("/")[-1]

        confirm_resp = await client.post(
            "/api/v1/public/confirmation",
            json={
                "token": cand_token,
                "interview_id": interview_id,
                "decision": "CONFIRMED",
            },
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["candidate_confirmation_status"] == "CONFIRMED"

        # 3. Public Candidate Change Request
        change_resp = await client.post(
            "/api/v1/public/change-requests",
            json={
                "token": cand_token,
                "interview_id": interview_id,
                "reason": "Family emergency, need afternoon slot.",
            },
        )
        assert change_resp.status_code == 200
        assert change_resp.json()["candidate_confirmation_status"] == "CHANGE_REQUESTED"

        # 4. Recruiter Reschedule Request via API
        resched_req_resp = await client.post(
            f"/api/v1/interviews/{interview_id}/reschedule-requests",
            json={
                "new_slot_proposal_id": p2["id"],
                "reason": "Accommodating candidate change request.",
            },
            headers=headers,
        )
        assert resched_req_resp.status_code == 201
        resched_action = resched_req_resp.json()
        assert resched_action["action_type"] == "RESCHEDULE_INTERVIEW"
        assert resched_action["status"] == "PENDING_APPROVAL"

        # Approve Reschedule Action
        approve_resched = await client.post(
            f"/api/v1/external-actions/{resched_action['id']}/approve",
            json={
                "expected_payload_hash": resched_action["payload_hash"],
                "expected_resource_version": resched_action["resource_version"],
            },
            headers=headers,
        )
        assert approve_resched.status_code == 200
        assert approve_resched.json()["status"] == "SUCCEEDED"

        # Verify revision incremented to 2
        detail_after_resched = await client.get(
            f"/api/v1/interviews/{interview_id}", headers=headers
        )
        assert detail_after_resched.status_code == 200
        assert detail_after_resched.json()["current_revision"]["revision_number"] == 2
        assert len(detail_after_resched.json()["all_revisions"]) == 2

        # 5. Inbound Calendar Webhook
        webhook_resp = await client.post(
            "/api/v1/webhooks/google/calendar",
            headers={
                "X-Goog-Channel-ID": "channel_xyz",
                "X-Goog-Resource-ID": "resource_123",
                "X-Goog-Resource-State": "exists",
            },
        )
        assert webhook_resp.status_code == 200
        assert webhook_resp.json()["status"] == "received"

        # 6. Recruiter Cancellation Request
        cancel_req_resp = await client.post(
            f"/api/v1/interviews/{interview_id}/cancel-requests",
            json={"reason": "Position closed internally.", "send_updates": True},
            headers=headers,
        )
        assert cancel_req_resp.status_code == 201
        cancel_action = cancel_req_resp.json()
        assert cancel_action["action_type"] == "CANCEL_INTERVIEW"

        # Approve Cancellation Action
        approve_cancel = await client.post(
            f"/api/v1/external-actions/{cancel_action['id']}/approve",
            json={
                "expected_payload_hash": cancel_action["payload_hash"],
                "expected_resource_version": cancel_action["resource_version"],
            },
            headers=headers,
        )
        assert approve_cancel.status_code == 200
        assert approve_cancel.json()["status"] == "SUCCEEDED"

        # Verify interview status is now CANCELLED
        detail_after_cancel = await client.get(
            f"/api/v1/interviews/{interview_id}", headers=headers
        )
        assert detail_after_cancel.status_code == 200
        assert detail_after_cancel.json()["interview"]["status"] == "CANCELLED"

        # All reservations released
        for res in detail_after_cancel.json()["reservations"]:
            assert res["status"] == "RELEASED"

        # 7. Check Reminders and Notifications endpoints
        rem_resp = await client.get(f"/api/v1/interviews/{interview_id}/reminders", headers=headers)
        assert rem_resp.status_code == 200

        notif_resp = await client.get(
            f"/api/v1/interviews/{interview_id}/notifications", headers=headers
        )
        assert notif_resp.status_code == 200


@pytest.mark.integration
async def test_scheduling_tenant_isolation(
    scheduling_test_app: tuple[
        Any, MockOidcClient, MockGoogleCalendarAdapter, MockGoogleGmailAdapter
    ],
) -> None:
    """Verifies that Org B cannot inspect or operate on Org A's interviews or actions."""
    app, mock_oidc, _, _ = scheduling_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client_a,
    ):
        # Org A setup
        _, csrf_a, _ = await _login_and_setup_org(
            client_a, email="user_a@tenant-a.test", org_name="Tenant Alpha"
        )
        headers_a = {"X-CSRF-Token": csrf_a, "Origin": "http://testserver"}

        job_id, cand_id, app_id, _ = await _setup_interview_prerequisites(client_a, csrf_a)

        base_day = (datetime.now(UTC) + timedelta(days=2)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        target_start = base_day
        target_end = base_day + timedelta(days=3)

        req_resp = await client_a.post(
            "/api/v1/interview-requests",
            json={
                "application_id": app_id,
                "title": "Isolation Test Round",
                "target_date_start": target_start.isoformat(),
                "target_date_end": target_end.isoformat(),
                "duration_minutes": 30,
                "location_type": "GOOGLE_MEET",
                "timezone": "UTC",
                "working_hours_start": 9,
                "working_hours_end": 17,
                "working_days": [0, 1, 2, 3, 4, 5, 6],
            },
            headers=headers_a,
        )
        request_id = req_resp.json()["id"]

        link_resp = await client_a.post(
            f"/api/v1/interview-requests/{request_id}/availability-links",
            headers=headers_a,
        )
        raw_token = link_resp.json()["availability_url"].split("/")[-1]

        avail_start = base_day + timedelta(hours=10)
        avail_end = base_day + timedelta(hours=14)
        await client_a.post(
            f"/api/v1/public/availability/{raw_token}",
            json={
                "candidate_timezone": "UTC",
                "available_intervals": [
                    {"start": avail_start.isoformat(), "end": avail_end.isoformat()}
                ],
            },
        )

        gen_resp = await client_a.post(
            f"/api/v1/interview-requests/{request_id}/proposals/generate",
            json={"step_minutes": 30, "max_proposals": 1},
            headers=headers_a,
        )
        proposal_id = gen_resp.json()[0]["id"]

        action_resp = await client_a.post(
            "/api/v1/external-actions",
            json={
                "action_type": "CREATE_INTERVIEW",
                "resource_type": "interview_request",
                "resource_id": request_id,
                "proposal_id": proposal_id,
            },
            headers=headers_a,
        )
        action_id = action_resp.json()["id"]

        # Org B setup
        async with AsyncClient(transport=transport, base_url="http://testserver") as client_b:
            _, csrf_b, _ = await _login_and_setup_org(
                client_b, email="user_b@tenant-b.test", org_name="Tenant Beta"
            )
            headers_b = {"X-CSRF-Token": csrf_b, "Origin": "http://testserver"}

            # Org B tries to access Org A's action -> 404
            get_act_resp = await client_b.get(
                f"/api/v1/external-actions/{action_id}", headers=headers_b
            )
            assert get_act_resp.status_code == 404

            # Org B tries to approve Org A's action -> 404
            approve_resp = await client_b.post(
                f"/api/v1/external-actions/{action_id}/approve",
                json={
                    "expected_payload_hash": action_resp.json()["payload_hash"],
                    "expected_resource_version": 1,
                },
                headers=headers_b,
            )
            assert approve_resp.status_code == 404

            # Org B lists actions -> Org A's action must NOT appear
            list_b = await client_b.get("/api/v1/external-actions", headers=headers_b)
            assert list_b.status_code == 200
            assert not any(a["id"] == action_id for a in list_b.json())
