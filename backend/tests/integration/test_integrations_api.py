"""Integration tests for Integrations REST API: Google OAuth flow, Connections, Reconnect, and Disconnect."""

import urllib.parse
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.modules.integrations.application.service import DEFAULT_GOOGLE_SCOPES
from talvora.modules.integrations.infrastructure.google.oauth_client import MockGoogleOAuthClient
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider
from talvora.shared.infrastructure.models import Base


@pytest.fixture
def integrations_test_app(
    test_settings: Settings,
) -> tuple[Any, MockOidcClient, MockGoogleOAuthClient]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    mock_google = MockGoogleOAuthClient()
    mail_provider = InMemorySystemMailProvider()

    app.state.oidc_client = mock_oidc
    app.state.google_oauth_client = mock_google
    app.state.mail_provider = mail_provider
    return app, mock_oidc, mock_google


async def _login_and_setup_org(
    client: AsyncClient,
    email: str = "interviewer@talvora.test",
    org_name: str = "Talvora Integration Org",
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
        headers={"X-CSRF-Token": csrf_token},
    )
    assert create_org_resp.status_code == 200
    org_id = create_org_resp.json()["id"]

    # Select Organization
    sel_resp = await client.post(
        f"/api/v1/organizations/{org_id}/select",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert sel_resp.status_code == 200

    # Fetch new CSRF token after session rotation
    csrf_resp2 = await client.get("/api/v1/auth/csrf")
    csrf_token2 = csrf_resp2.json()["csrf_token"]
    session_token2 = client.cookies.get("talvora_session") or session_token

    return session_token2, csrf_token2, org_id


@pytest.mark.integration
async def test_integrations_oauth_and_lifecycle_flow(
    integrations_test_app: tuple[Any, MockOidcClient, MockGoogleOAuthClient],
) -> None:
    app, _, mock_google = integrations_test_app

    async with ASGITransport(app=app) as transport:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            session_token, csrf_token, _ = await _login_and_setup_org(client)
            client.cookies.set("talvora_session", session_token)
            headers = {"X-CSRF-Token": csrf_token}

            # 1. Initial list: should be empty
            list_resp = await client.get("/api/v1/integrations", headers=headers)
            assert list_resp.status_code == 200
            assert list_resp.json()["total_count"] == 0

            # 2. Initiate Google OAuth
            auth_resp = await client.post(
                "/api/v1/integrations/google/authorize",
                json={"requested_capabilities": ["freebusy", "calendar_events"]},
                headers=headers,
            )
            assert auth_resp.status_code == 200
            auth_data = auth_resp.json()
            assert "accounts.google.com" in auth_data["authorization_url"]
            state = auth_data["state"]

            # 3. Simulate callback
            code = "valid_google_oauth_code_xyz"
            mock_google.register_simulated_user(
                code=code,
                sub="google_sub_carol",
                email="carol@workspace.test",
                granted_scopes=DEFAULT_GOOGLE_SCOPES,
                refresh_token="google_refresh_token_carol",
            )

            cb_resp = await client.get(
                f"/api/v1/integrations/google/callback?code={code}&state={state}",
                headers=headers,
            )
            assert cb_resp.status_code == 200
            conn_data = cb_resp.json()
            assert conn_data["connection_status"] == "ACTIVE"
            assert conn_data["verified_email"] == "carol@workspace.test"
            assert conn_data["provider"] == "GOOGLE"
            # Verify safe DTO: sensitive tokens NOT exposed
            assert "encrypted_access_token" not in conn_data
            assert "encrypted_refresh_token" not in conn_data
            assert "access_token" not in conn_data
            connection_id = conn_data["id"]

            # 4. List now returns 1 connection
            list_resp2 = await client.get("/api/v1/integrations", headers=headers)
            assert list_resp2.status_code == 200
            assert list_resp2.json()["total_count"] == 1
            assert list_resp2.json()["items"][0]["id"] == connection_id

            # 5. Reconnect initiation
            reconn_resp = await client.post(
                f"/api/v1/integrations/{connection_id}/reconnect",
                headers=headers,
            )
            assert reconn_resp.status_code == 200
            reconn_data = reconn_resp.json()
            assert "prompt=consent" in reconn_data["authorization_url"]

            # 6. Disconnect
            disc_resp = await client.delete(
                f"/api/v1/integrations/{connection_id}",
                headers=headers,
            )
            assert disc_resp.status_code == 200
            disc_data = disc_resp.json()
            assert disc_data["connection_status"] == "DISCONNECTED"

            # 7. List reflects disconnected status
            list_resp3 = await client.get("/api/v1/integrations", headers=headers)
            assert list_resp3.status_code == 200
            assert list_resp3.json()["items"][0]["connection_status"] == "DISCONNECTED"
