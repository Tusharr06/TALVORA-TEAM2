"""Talvora Configuration and Typed Settings.

Enforces strict environment validation, rejecting unsafe development flags,
test doubles, and default placeholder secrets when running in staging or production.
"""

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentType = Literal["local", "test", "staging", "production"]
LogLevelType = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TALVORA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment & Core ---
    environment: EnvironmentType = Field(default="local")
    debug: bool = Field(default=False)
    log_level: LogLevelType = Field(default="INFO")
    service_name: str = Field(default="talvora-backend")
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    max_request_size_bytes: int = Field(default=10 * 1024 * 1024)  # 10 MiB default limit

    # --- Database (PostgreSQL 17) ---
    database_url: str = Field(
        default="postgresql+psycopg://talvora:talvora_local_dev@localhost:5432/talvora_db"
    )
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=5)
    db_timeout_seconds: float = Field(default=5.0)

    # --- Object Storage (S3 / Moto) ---
    s3_bucket_name: str = Field(default="talvora-quarantine-local")
    s3_clean_bucket_name: str = Field(default="talvora-clean-local")
    aws_region: str = Field(default="us-east-1")
    moto_endpoint_url: str | None = Field(default="http://localhost:5000")
    s3_kms_key_id: str | None = Field(default=None)

    # --- Messaging & SQS ---
    sqs_queue_url: str | None = Field(
        default="http://localhost:5000/123456789012/talvora-tasks-local"
    )
    sqs_dlq_url: str | None = Field(
        default="http://localhost:5000/123456789012/talvora-tasks-dlq-local"
    )

    # --- Authentication & Identity (OIDC / Cognito) ---
    oidc_issuer: str = Field(
        default="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example"
    )
    oidc_client_id: str = Field(default="example-client-id")
    oidc_client_secret: str | None = Field(default=None)
    allow_dev_identity_adapter: bool = Field(default=True)

    # --- Security & Cookies ---
    cookie_secure: bool = Field(default=False)
    session_cookie_name: str = Field(default="talvora_session")
    csrf_secret: str = Field(
        default="local-development-csrf-secret-change-in-production-min32chars"
    )
    encryption_key: str = Field(default="local-dev-aes-encryption-key-32bytes-long!")

    # --- Diagnostics & Internal Surfaces ---
    internal_metrics_enabled: bool = Field(default=True)
    internal_diagnostic_enabled: bool = Field(default=True)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, list):
            return [str(item) for item in v]
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except Exception:
                return [host.strip() for host in v.split(",") if host.strip()]
            return [v.strip()]
        return []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, list):
            return [str(item) for item in v]
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except Exception:
                return [origin.strip() for origin in v.split(",") if origin.strip()]
            return [v.strip()]
        return []

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        """Rejects unsafe development options when deployed in staging or production."""
        is_prod_like = self.environment in ("staging", "production")

        if is_prod_like:
            if self.debug:
                raise ValueError("Debug mode must be disabled in staging/production.")

            if self.allow_dev_identity_adapter:
                raise ValueError(
                    "Development identity adapter is strictly forbidden in staging/production."
                )

            if self.moto_endpoint_url:
                raise ValueError(
                    "Moto emulator endpoint cannot be configured in staging/production."
                )

            if not self.cookie_secure:
                raise ValueError("Cookie secure flag must be True in staging/production.")

            if "local-dev" in self.encryption_key or len(self.encryption_key) < 32:
                raise ValueError(
                    "Production encryption key must be a cryptographically secure key of at least 32 characters."
                )

            if "local-development" in self.csrf_secret or len(self.csrf_secret) < 32:
                raise ValueError(
                    "Production CSRF secret must be a cryptographically secure key of at least 32 characters."
                )

            if self.internal_diagnostic_enabled:
                raise ValueError(
                    "Internal diagnostic route must be disabled in staging/production."
                )

        return self


def get_settings() -> Settings:
    return Settings()
