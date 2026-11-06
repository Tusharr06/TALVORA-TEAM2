"""Google OAuth 2.0 Client Adapters: Real API Client and High-Fidelity Test Double."""

import urllib.parse
from typing import Any

import httpx

from talvora.modules.integrations.application.ports import (
    GoogleOAuthClient,
    GoogleTokens,
    GoogleUserInfo,
)
from talvora.modules.integrations.domain.errors import (
    IntegrationError,
    IntegrationReauthRequiredError,
    OAuthConsentDeniedError,
)


class GoogleOAuthHttpClient(GoogleOAuthClient):
    """Production implementation communicating with Google's OAuth 2.0 endpoints."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        auth_endpoint: str = "https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint: str = "https://oauth2.googleapis.com/token",
        userinfo_endpoint: str = "https://openidconnect.googleapis.com/v1/userinfo",
        revoke_endpoint: str = "https://oauth2.googleapis.com/revoke",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_endpoint = auth_endpoint
        self.token_endpoint = token_endpoint
        self.userinfo_endpoint = userinfo_endpoint
        self.revoke_endpoint = revoke_endpoint
        self.timeout = timeout_seconds

    def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        requested_scopes: list[str],
        prompt: str = "consent",
        access_type: str = "offline",
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(requested_scopes),
            "state": state,
            "access_type": access_type,
            "prompt": prompt,
            "include_granted_scopes": "true",
        }
        return f"{self.auth_endpoint}?{urllib.parse.urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
    ) -> GoogleTokens:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.token_endpoint, data=data)

        if resp.status_code != 200:
            error_data = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            raise IntegrationError(
                status_code=400,
                detail=f"Google token exchange failed: {error_data.get('error_description', resp.text)}",
            )

        payload = resp.json()
        scopes_str = payload.get("scope", "")
        granted_scopes = scopes_str.split() if scopes_str else []

        return GoogleTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_in=payload.get("expires_in", 3600),
            granted_scopes=granted_scopes,
            token_type=payload.get("token_type", "Bearer"),
            id_token=payload.get("id_token"),
        )

    async def refresh_access_token(
        self,
        refresh_token: str,
    ) -> GoogleTokens:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.token_endpoint, data=data)

        if resp.status_code != 200:
            error_data = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            err_code = error_data.get("error", "refresh_failed")
            if err_code in ("invalid_grant", "revoked_token"):
                raise IntegrationReauthRequiredError("Refresh token was revoked or expired.")
            raise IntegrationError(
                status_code=400,
                detail=f"Token refresh failed: {error_data.get('error_description', resp.text)}",
            )

        payload = resp.json()
        scopes_str = payload.get("scope", "")
        granted_scopes = scopes_str.split() if scopes_str else []

        return GoogleTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get(
                "refresh_token"
            ),  # Google often omits refresh_token on refresh
            expires_in=payload.get("expires_in", 3600),
            granted_scopes=granted_scopes,
            token_type=payload.get("token_type", "Bearer"),
            id_token=payload.get("id_token"),
        )

    async def get_user_info(
        self,
        access_token: str,
    ) -> GoogleUserInfo:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(self.userinfo_endpoint, headers=headers)

        if resp.status_code != 200:
            raise IntegrationError(
                status_code=401, detail="Failed to retrieve Google user identity."
            )

        data = resp.json()
        return GoogleUserInfo(
            sub=data["sub"],
            email=data["email"],
            email_verified=bool(data.get("email_verified", False)),
            name=data.get("name"),
            picture=data.get("picture"),
        )

    async def revoke_token(
        self,
        token: str,
    ) -> bool:
        params = {"token": token}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.revoke_endpoint, params=params)
                return resp.status_code == 200
        except Exception:
            return False


class MockGoogleOAuthClient(GoogleOAuthClient):
    """High-fidelity test double simulating Google OAuth with determinism and fault injection."""

    def __init__(
        self,
        client_id: str = "mock-google-client-id",
        client_secret: str = "mock-google-client-secret",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.registered_grants: dict[str, dict[str, Any]] = {}
        self.user_database: dict[str, GoogleUserInfo] = {}
        self.revoked_tokens: set[str] = set()

        # Fault injection flags
        self.simulate_invalid_grant: bool = False
        self.simulate_timeout: bool = False
        self.simulate_partial_scopes: bool = False
        self.simulate_unverified_email: bool = False

    def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        requested_scopes: list[str],
        prompt: str = "consent",
        access_type: str = "offline",
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(requested_scopes),
            "state": state,
            "access_type": access_type,
            "prompt": prompt,
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

    def register_simulated_user(
        self,
        code: str,
        sub: str,
        email: str,
        granted_scopes: list[str],
        refresh_token: str | None = "mock_google_refresh_token",
        email_verified: bool = True,
    ) -> None:
        self.registered_grants[code] = {
            "sub": sub,
            "email": email,
            "granted_scopes": granted_scopes,
            "refresh_token": refresh_token,
            "email_verified": email_verified,
        }
        self.user_database[sub] = GoogleUserInfo(
            sub=sub,
            email=email,
            email_verified=email_verified,
            name="Mock Google User",
        )

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
    ) -> GoogleTokens:
        if self.simulate_timeout:
            raise IntegrationError(status_code=504, detail="Token exchange timed out.")

        if self.simulate_invalid_grant:
            raise OAuthConsentDeniedError("Invalid authorization code.")

        grant = self.registered_grants.get(code)
        if not grant:
            # Generate default mock grant if not pre-registered
            sub = f"google_sub_{code[:8]}"
            email = f"user_{code[:8]}@example.com"
            scopes = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
            if not self.simulate_partial_scopes:
                scopes.extend(
                    [
                        "https://www.googleapis.com/auth/calendar.events.freebusy",
                        "https://www.googleapis.com/auth/calendar.events.owned",
                        "https://www.googleapis.com/auth/gmail.send",
                    ]
                )
            self.register_simulated_user(
                code=code,
                sub=sub,
                email=email,
                granted_scopes=scopes,
                email_verified=not self.simulate_unverified_email,
            )
            grant = self.registered_grants[code]

        access_token = f"mock_access_{code}"
        refresh_token = grant["refresh_token"]

        return GoogleTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=3600,
            granted_scopes=grant["granted_scopes"],
            token_type="Bearer",
            id_token=f"mock_id_token_{code}",
        )

    async def refresh_access_token(
        self,
        refresh_token: str,
    ) -> GoogleTokens:
        if self.simulate_invalid_grant or refresh_token in self.revoked_tokens:
            raise IntegrationReauthRequiredError("Refresh token has been revoked or expired.")

        return GoogleTokens(
            access_token=f"mock_refreshed_access_for_{refresh_token[:10]}",
            refresh_token=None,  # Standard Google behavior: omit refresh token on refresh
            expires_in=3600,
            granted_scopes=[
                "https://www.googleapis.com/auth/calendar.events.freebusy",
                "https://www.googleapis.com/auth/calendar.events.owned",
                "https://www.googleapis.com/auth/gmail.send",
            ],
            token_type="Bearer",
        )

    async def get_user_info(
        self,
        access_token: str,
    ) -> GoogleUserInfo:
        if self.simulate_timeout:
            raise IntegrationError(status_code=504, detail="UserInfo retrieval timed out.")

        # Extract code marker from mock token if present
        code = access_token.replace("mock_access_", "")
        grant = self.registered_grants.get(code)
        if grant:
            return GoogleUserInfo(
                sub=grant["sub"],
                email=grant["email"],
                email_verified=grant["email_verified"],
                name="Mock Google User",
            )

        return GoogleUserInfo(
            sub="mock_google_sub_default",
            email="default_interviewer@talvora.test",
            email_verified=True,
            name="Default Mock User",
        )

    async def revoke_token(
        self,
        token: str,
    ) -> bool:
        self.revoked_tokens.add(token)
        return True
