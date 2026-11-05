"""Integration tests for Organizations, Members, Teams, Invitations, CSRF, and Origin."""

import urllib.parse
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base


@pytest.fixture
def org_test_app(test_settings: Settings) -> tuple[Any, MockOidcClient, InMemorySystemMailProvider]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    mail_provider = InMemorySystemMailProvider()
    app.state.oidc_client = mock_oidc
    app.state.mail_provider = mail_provider
    return app, mock_oidc, mail_provider


async def authenticate_session(
    client: AsyncClient, email: str = "alice@talvora.test"
) -> tuple[str, str]:
    """Helper completing OIDC login and retrieving session token and CSRF token."""
    resp_login = await client.get("/api/v1/auth/login")
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(resp_login.json()["authorization_url"]).query
    )["state"][0]
    code = f"mock_code_{state[:16]}"

    resp_cb = await client.get(
        f"/api/v1/auth/callback?code={code}&state={state}", follow_redirects=False
    )
    session_token = resp_cb.cookies["talvora_session"]

    # Fetch CSRF token
    csrf_resp = await client.get("/api/v1/auth/csrf")
    csrf_token = csrf_resp.json()["csrf_token"]
    return session_token, csrf_token


@pytest.mark.integration
async def test_create_organization_requires_csrf_and_assigns_owner(
    org_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = org_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        session_token, csrf_token = await authenticate_session(client)

        # 1. Mutating POST without CSRF token must be rejected with 403
        resp_no_csrf = await client.post(
            "/api/v1/organizations",
            json={"name": "No CSRF Corp", "slug": "no-csrf"},
        )
        assert resp_no_csrf.status_code == 403
        assert resp_no_csrf.json()["code"] == "CSRF_TOKEN_MISSING"

        # 2. Mutating POST with valid CSRF token succeeds
        resp_with_csrf = await client.post(
            "/api/v1/organizations",
            json={"name": "Acme Talent", "slug": "acme-talent"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp_with_csrf.status_code == 200
        org_data = resp_with_csrf.json()
        assert org_data["name"] == "Acme Talent"
        assert org_data["role"] == "OWNER"
        org_id = org_data["id"]

        # 3. List organizations reflects ownership
        resp_list = await client.get("/api/v1/organizations")
        assert resp_list.status_code == 200
        orgs = resp_list.json()
        assert any(o["id"] == org_id and o["role"] == "OWNER" for o in orgs)


@pytest.mark.integration
async def test_forged_origin_header_rejected_with_403(
    org_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = org_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token = await authenticate_session(client)

        # Forged Origin from attacker website
        resp = await client.post(
            "/api/v1/organizations",
            json={"name": "Attacker Org", "slug": "attacker-org"},
            headers={
                "X-CSRF-Token": csrf_token,
                "Origin": "http://evil-phishing-site.com",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ORIGIN_FORBIDDEN"


@pytest.mark.integration
async def test_last_owner_protection_on_member_delete_api(
    org_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = org_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token = await authenticate_session(client)

        # Create organization
        resp_org = await client.post(
            "/api/v1/organizations",
            json={"name": "Solo Org", "slug": "solo-org"},
            headers={"X-CSRF-Token": csrf_token},
        )
        org_id = resp_org.json()["id"]

        # Switch to org
        await client.post(
            f"/api/v1/organizations/{org_id}/select", headers={"X-CSRF-Token": csrf_token}
        )
        csrf_token = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]

        # List members to find owner user ID
        resp_members = await client.get(f"/api/v1/organizations/{org_id}/members")
        assert resp_members.status_code == 200
        members = resp_members.json()
        assert len(members) == 1
        owner_user_id = members[0]["user_id"]

        # Attempt to delete the last active owner -> 409 Conflict
        del_resp = await client.delete(
            f"/api/v1/organizations/{org_id}/members/{owner_user_id}",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert del_resp.status_code == 409
        assert del_resp.json()["code"] == "LAST_OWNER_PROTECTION"


@pytest.mark.integration
async def test_cross_tenant_resource_concealment(
    org_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = org_test_app

    # Client A creates Org A
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client_a,
    ):
        _, csrf_a = await authenticate_session(client_a, email="user_a@talvora.test")
        resp_a = await client_a.post(
            "/api/v1/organizations",
            json={"name": "Org A", "slug": "org-a"},
            headers={"X-CSRF-Token": csrf_a},
        )
        org_a_id = resp_a.json()["id"]

    # Client B creates Org B
    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client_b,
    ):
        _, csrf_b = await authenticate_session(client_b, email="user_b@talvora.test")
        resp_b = await client_b.post(
            "/api/v1/organizations",
            json={"name": "Org B", "slug": "org-b"},
            headers={"X-CSRF-Token": csrf_b},
        )
        assert resp_b.status_code == 200

        # Client B attempts to list members of Org A
        # INVARIANT: Must return 404 Not Found (Resource Concealment), NEVER 200 or 403!
        cross_resp = await client_b.get(f"/api/v1/organizations/{org_a_id}/members")
        assert cross_resp.status_code == 404
        assert cross_resp.json()["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.integration
async def test_teams_crud_and_membership_assignment(
    org_test_app: tuple[Any, MockOidcClient, InMemorySystemMailProvider],
) -> None:
    app, _, _ = org_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        _, csrf_token = await authenticate_session(client)

        # Create org and select it
        resp_org = await client.post(
            "/api/v1/organizations",
            json={"name": "Engineering Corp", "slug": "eng-corp"},
            headers={"X-CSRF-Token": csrf_token},
        )
        org_id = resp_org.json()["id"]
        await client.post(
            f"/api/v1/organizations/{org_id}/select", headers={"X-CSRF-Token": csrf_token}
        )
        csrf_token = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]

        # 1. Create Team
        team_resp = await client.post(
            "/api/v1/teams",
            json={"name": "Backend Engineering", "description": "Core platform hiring panel"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert team_resp.status_code == 200
        team_data = team_resp.json()
        assert team_data["name"] == "Backend Engineering"
        team_id = team_data["id"]

        # 2. List Teams
        list_teams = await client.get("/api/v1/teams")
        assert list_teams.status_code == 200
        assert any(t["id"] == team_id for t in list_teams.json())

        # 3. Add member to team
        members = (await client.get(f"/api/v1/organizations/{org_id}/members")).json()
        mem_id = members[0]["membership_id"]

        add_tm = await client.post(
            f"/api/v1/teams/{team_id}/members",
            json={"membership_id": mem_id},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert add_tm.status_code == 200

        # 4. Remove member from team
        del_tm = await client.delete(
            f"/api/v1/teams/{team_id}/members/{mem_id}",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert del_tm.status_code == 200
