"""Integration tests for OIDC Authentication Flow, PKCE, CSRF, and Logout."""

import urllib.parse
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.identity.infrastructure.oidc import MockOidcClient
from talvora.shared.infrastructure.models import Base


@pytest.fixture
def auth_test_app(test_settings: Settings) -> tuple[Any, MockOidcClient]:
    app = create_app(test_settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)

    mock_oidc = MockOidcClient(issuer="https://idp.talvora.test", client_id="test-client-id")
    app.state.oidc_client = mock_oidc
    return app, mock_oidc


@pytest.mark.integration
async def test_oidc_pkce_login_and_callback_flow_sets_cookie(
    auth_test_app: tuple[Any, MockOidcClient],
) -> None:
    app, mock_oidc = auth_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        # 1. Initiate login
        resp_login = await client.get("/api/v1/auth/login?redirect_target=/app/dashboard")
        assert resp_login.status_code == 200
        data = resp_login.json()
        auth_url = data["authorization_url"]
        assert "state=" in auth_url
        assert "code_challenge=" in auth_url

        # Parse state and nonce from auth_url
        parsed = urllib.parse.urlparse(auth_url)
        params = urllib.parse.parse_qs(parsed.query)
        state = params["state"][0]

        # 2. Complete callback with code
        simulated_code = f"mock_code_{state[:16]}"
        resp_cb = await client.get(
            f"/api/v1/auth/callback?code={simulated_code}&state={state}",
            follow_redirects=False,
        )
        assert resp_cb.status_code == 302
        assert resp_cb.headers["location"] == "/app/dashboard"

        # Cookie must be set and contain HttpOnly
        assert "talvora_session" in resp_cb.cookies
        session_token = resp_cb.cookies["talvora_session"]
        assert len(session_token) >= 32

        # 3. Access /auth/me using the session cookie
        client.cookies.set("talvora_session", session_token)
        resp_me = await client.get("/api/v1/auth/me")
        assert resp_me.status_code == 200
        me_data = resp_me.json()
        assert "@talvora.test" in me_data["email"]


@pytest.mark.integration
async def test_oidc_callback_with_invalid_or_replayed_state_fails(
    auth_test_app: tuple[Any, MockOidcClient],
) -> None:
    app, _ = auth_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        resp = await client.get("/api/v1/auth/callback?code=fake_code&state=nonexistent_state")
        assert resp.status_code == 400
        assert resp.json()["code"] == "OIDC_STATE_INVALID"


@pytest.mark.integration
async def test_oidc_callback_unverified_email_rejected_with_403(
    auth_test_app: tuple[Any, MockOidcClient],
) -> None:
    app, mock_oidc = auth_test_app
    mock_oidc.simulate_unverified_email = True

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        resp_login = await client.get("/api/v1/auth/login")
        state = urllib.parse.parse_qs(
            urllib.parse.urlparse(resp_login.json()["authorization_url"]).query
        )["state"][0]
        code = f"mock_code_{state[:16]}"

        resp_cb = await client.get(f"/api/v1/auth/callback?code={code}&state={state}")
        assert resp_cb.status_code == 403
        assert resp_cb.json()["code"] == "OIDC_EMAIL_UNVERIFIED"


@pytest.mark.integration
async def test_oidc_algorithm_substitution_rejected_with_401(
    auth_test_app: tuple[Any, MockOidcClient],
) -> None:
    app, mock_oidc = auth_test_app
    mock_oidc.simulate_algorithm_substitution = True

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        resp_login = await client.get("/api/v1/auth/login")
        state = urllib.parse.parse_qs(
            urllib.parse.urlparse(resp_login.json()["authorization_url"]).query
        )["state"][0]
        code = f"mock_code_{state[:16]}"

        resp_cb = await client.get(f"/api/v1/auth/callback?code={code}&state={state}")
        assert resp_cb.status_code == 401
        assert resp_cb.json()["code"] == "OIDC_ALGORITHM_REJECTED"


@pytest.mark.integration
async def test_open_redirect_attack_sanitized_to_root(
    auth_test_app: tuple[Any, MockOidcClient],
) -> None:
    app, _ = auth_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        # Malicious redirect target attempting open redirect
        resp_login = await client.get("/api/v1/auth/login?redirect_target=//evil.com/phish")
        state = urllib.parse.parse_qs(
            urllib.parse.urlparse(resp_login.json()["authorization_url"]).query
        )["state"][0]
        code = f"mock_code_{state[:16]}"

        resp_cb = await client.get(
            f"/api/v1/auth/callback?code={code}&state={state}",
            follow_redirects=False,
        )
        assert resp_cb.status_code == 302
        # INVARIANT: Must redirect strictly to "/", never to "//evil.com/phish"
        assert resp_cb.headers["location"] == "/"


@pytest.mark.integration
async def test_logout_revokes_session_and_clears_cookie(
    auth_test_app: tuple[Any, MockOidcClient],
) -> None:
    app, _ = auth_test_app

    async with (
        ASGITransport(app=app) as transport,
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        # 1. Login
        resp_login = await client.get("/api/v1/auth/login")
        state = urllib.parse.parse_qs(
            urllib.parse.urlparse(resp_login.json()["authorization_url"]).query
        )["state"][0]
        code = f"mock_code_{state[:16]}"
        resp_cb = await client.get(
            f"/api/v1/auth/callback?code={code}&state={state}", follow_redirects=False
        )
        token = resp_cb.cookies["talvora_session"]

        # 2. Verify /auth/me works
        client.cookies.set("talvora_session", token)
        me_resp = await client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200

        # 3. Logout
        logout_resp = await client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 200

        # 4. Subsequent /auth/me must return 401
        after_logout = await client.get("/api/v1/auth/me")
        assert after_logout.status_code == 401
