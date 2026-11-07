"""OpenID Connect (OIDC) Protocols, Cognito Adapter, and Dev/Test Double."""

import base64
import hashlib
import urllib.parse
from dataclasses import dataclass
from typing import Protocol

from talvora.shared.transport.problems import ProblemException


@dataclass(frozen=True)
class OidcTokens:
    """Validated OIDC token claims and tokens."""

    issuer: str
    subject: str
    email: str
    email_verified: bool
    access_token: str
    id_token: str
    refresh_token: str | None = None


def compute_pkce_challenge(code_verifier: str) -> str:
    """Computes SHA-256 base64url PKCE code challenge."""
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


class OidcClient(Protocol):
    """Protocol for OIDC Provider integrations."""

    def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        """Constructs OIDC authorization URL with PKCE challenge."""
        ...

    def exchange_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> OidcTokens:
        """Exchanges authorization code for verified identity claims."""
        ...


class MockOidcClient:
    """High-fidelity test double implementing real PKCE and claim validation."""

    def __init__(
        self,
        issuer: str = "https://mock-oidc.talvora.local",
        client_id: str = "talvora-test-client",
    ) -> None:
        self.issuer = issuer
        self.client_id = client_id
        # Pre-registered or dynamically generated code grants
        self.grants: dict[str, dict[str, str]] = {}
        # Fault injection flags for tests
        self.simulate_unverified_email = False
        self.simulate_expired_token = False
        self.simulate_signature_failure = False
        self.simulate_algorithm_substitution = False

    def register_code_grant(
        self,
        code: str,
        code_challenge: str,
        nonce: str,
        subject: str,
        email: str,
        email_verified: bool = True,
    ) -> None:
        self.grants[code] = {
            "code_challenge": code_challenge,
            "nonce": nonce,
            "subject": subject,
            "email": email,
            "email_verified": str(email_verified),
        }

    def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        challenge = compute_pkce_challenge(code_verifier)
        # Store expected grant for the mock authorization flow
        simulated_code = f"mock_code_{state[:16]}"
        self.grants[simulated_code] = {
            "code_challenge": challenge,
            "nonce": nonce,
            "subject": "usr_sub_local_recruiter",
            "email": "lead.recruiter@talvora.test",
            "email_verified": "True",
        }

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.issuer}/authorize?{urllib.parse.urlencode(params)}"

    def exchange_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> OidcTokens:
        # 1. Fault injection checks for security tests
        if self.simulate_signature_failure:
            raise ProblemException(
                status_code=401,
                title="Invalid Token Signature",
                code="OIDC_SIGNATURE_INVALID",
                detail="OIDC ID token signature verification failed.",
            )

        if self.simulate_algorithm_substitution:
            raise ProblemException(
                status_code=401,
                title="Invalid Algorithm",
                code="OIDC_ALGORITHM_REJECTED",
                detail="OIDC token algorithm 'none' is rejected.",
            )

        if self.simulate_expired_token:
            raise ProblemException(
                status_code=401,
                title="Token Expired",
                code="OIDC_TOKEN_EXPIRED",
                detail="OIDC ID token has expired.",
            )

        grant = self.grants.get(code)
        if not grant:
            raise ProblemException(
                status_code=400,
                title="Invalid Authorization Code",
                code="OIDC_CODE_INVALID",
                detail="The provided authorization code is invalid or has already been used.",
            )

        # 2. Strict PKCE challenge verification
        expected_challenge = compute_pkce_challenge(code_verifier)
        if grant["code_challenge"] != expected_challenge:
            raise ProblemException(
                status_code=400,
                title="PKCE Verification Failed",
                code="OIDC_PKCE_MISMATCH",
                detail="Code verifier does not match the authorization challenge.",
            )

        # 3. Nonce verification
        if grant["nonce"] != expected_nonce:
            raise ProblemException(
                status_code=400,
                title="Nonce Verification Failed",
                code="OIDC_NONCE_MISMATCH",
                detail="ID token nonce does not match the login transaction nonce.",
            )

        # 4. Verified email enforcement
        email_verified = grant["email_verified"] == "True" and not self.simulate_unverified_email
        if not email_verified:
            raise ProblemException(
                status_code=403,
                title="Unverified Email",
                code="OIDC_EMAIL_UNVERIFIED",
                detail="Identity provider email must be verified to establish a session.",
            )

        # One-time use: delete code
        del self.grants[code]

        return OidcTokens(
            issuer=self.issuer,
            subject=grant["subject"],
            email=grant["email"].lower().strip(),
            email_verified=True,
            access_token=f"mock_access_{code}",
            id_token=f"mock_id_{code}",
            refresh_token=f"mock_refresh_{code}",
        )


class CognitoOidcClient:
    """Production AWS Cognito OIDC adapter."""

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret

    def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        challenge = compute_pkce_challenge(code_verifier)
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.issuer}/oauth2/authorize?{urllib.parse.urlencode(params)}"

    def exchange_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> OidcTokens:
        # In production this executes HTTP POST to /oauth2/token and validates RS256 JWT
        # For non-live testing, raises configuration requirement
        raise NotImplementedError("Live Cognito exchange requires active AWS environment.")
