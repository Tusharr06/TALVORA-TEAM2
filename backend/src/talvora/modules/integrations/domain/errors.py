"""Typed RFC 7807 problem exceptions for Google OAuth and Workspace Integrations."""

from talvora.shared.transport.problems import ProblemException


class IntegrationError(ProblemException):
    """Base exception for integration subsystem."""

    def __init__(
        self,
        status_code: int = 400,
        title: str = "Integration Error",
        code: str = "INTEGRATION_ERROR",
        detail: str = "An error occurred with an external integration.",
    ) -> None:
        super().__init__(
            status_code=status_code,
            title=title,
            code=code,
            detail=detail,
        )


class OAuthStateError(IntegrationError):
    def __init__(self, detail: str = "OAuth state validation failed.") -> None:
        super().__init__(
            status_code=400,
            title="OAuth State Error",
            code="OAUTH_STATE_INVALID",
            detail=detail,
        )


class OAuthConsentDeniedError(IntegrationError):
    def __init__(
        self, detail: str = "Google authorization was denied or consent was not granted."
    ) -> None:
        super().__init__(
            status_code=400,
            title="OAuth Consent Denied",
            code="OAUTH_CONSENT_DENIED",
            detail=detail,
        )


class IntegrationReauthRequiredError(IntegrationError):
    def __init__(
        self, detail: str = "Integration credentials expired or revoked; reauthorization required."
    ) -> None:
        super().__init__(
            status_code=401,
            title="Reauthorization Required",
            code="INTEGRATION_REAUTH_REQUIRED",
            detail=detail,
        )


class InsufficientScopesError(IntegrationError):
    def __init__(
        self, detail: str = "Granted OAuth scopes are insufficient for the requested operation."
    ) -> None:
        super().__init__(
            status_code=403,
            title="Insufficient Scopes",
            code="INSUFFICIENT_SCOPES",
            detail=detail,
        )


class ProviderUnavailableError(IntegrationError):
    def __init__(
        self, detail: str = "Google API service is temporarily unavailable or timed out."
    ) -> None:
        super().__init__(
            status_code=503,
            title="Provider Unavailable",
            code="PROVIDER_UNAVAILABLE",
            detail=detail,
        )


class ProviderRateLimitError(IntegrationError):
    def __init__(self, detail: str = "Google API rate limit exceeded.") -> None:
        super().__init__(
            status_code=429,
            title="Provider Rate Limited",
            code="PROVIDER_RATE_LIMITED",
            detail=detail,
        )


class ProviderETagConflictError(IntegrationError):
    def __init__(self, detail: str = "Resource modified concurrently (ETag mismatch).") -> None:
        super().__init__(
            status_code=409,
            title="ETag Conflict",
            code="ETAG_CONFLICT",
            detail=detail,
        )
