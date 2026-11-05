"""Unit tests for configuration validation and production safety guards."""

import pytest
from pydantic import ValidationError

from talvora.config import Settings


def test_default_settings_loads_successfully() -> None:
    settings = Settings(environment="local")
    assert settings.environment == "local"
    assert settings.service_name == "talvora-backend"
    assert settings.cookie_secure is False
    assert settings.allow_dev_identity_adapter is True


@pytest.mark.parametrize(
    ("env", "invalid_override", "expected_err"),
    [
        (
            "production",
            {"debug": True},
            "Debug mode must be disabled in staging/production.",
        ),
        (
            "production",
            {"allow_dev_identity_adapter": True},
            "Development identity adapter is strictly forbidden in staging/production.",
        ),
        (
            "staging",
            {"moto_endpoint_url": "http://localhost:5000"},
            "Moto emulator endpoint cannot be configured in staging/production.",
        ),
        (
            "production",
            {"cookie_secure": False},
            "Cookie secure flag must be True in staging/production.",
        ),
        (
            "production",
            {"encryption_key": "local-dev-placeholder"},
            "Production encryption key must be a cryptographically secure key of at least 32 characters.",
        ),
        (
            "production",
            {"csrf_secret": "local-development-placeholder"},
            "Production CSRF secret must be a cryptographically secure key of at least 32 characters.",
        ),
        (
            "production",
            {"internal_diagnostic_enabled": True},
            "Internal diagnostic route must be disabled in staging/production.",
        ),
    ],
)
def test_production_rejects_unsafe_settings(
    env: str, invalid_override: dict[str, object], expected_err: str
) -> None:
    base_prod_kwargs: dict[str, object] = {
        "environment": env,
        "debug": False,
        "allow_dev_identity_adapter": False,
        "moto_endpoint_url": None,
        "cookie_secure": True,
        "encryption_key": "a" * 32,
        "csrf_secret": "b" * 32,
        "internal_diagnostic_enabled": False,
    }
    base_prod_kwargs.update(invalid_override)

    with pytest.raises(ValidationError) as exc_info:
        Settings(**base_prod_kwargs)  # type: ignore[arg-type]

    assert expected_err in str(exc_info.value)
