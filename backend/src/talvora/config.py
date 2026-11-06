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

    # --- Document Intake (P06): batch/file limits and object receipt reconciliation ---
    upload_max_items_per_batch: int = Field(default=100)
    upload_max_file_bytes: int = Field(default=10 * 1024 * 1024)  # 10 MiB per file
    upload_max_batch_bytes: int = Field(default=500 * 1024 * 1024)  # 500 MiB per batch
    upload_stream_chunk_bytes: int = Field(default=256 * 1024)
    object_receipt_orphan_grace_minutes: int = Field(default=30)

    # --- Evidence Extraction (P07): bounded PDF/DOCX parsing and OCR ---
    extraction_max_pdf_pages: int = Field(default=30)
    extraction_max_characters: int = Field(default=200_000)
    extraction_min_digital_text_chars_per_page: int = Field(default=20)
    tesseract_cmd: str = Field(default="tesseract")
    ocr_language: str = Field(default="eng")
    ocr_dpi: int = Field(default=200)
    ocr_page_timeout_seconds: float = Field(default=20.0)
    ocr_max_pages_per_document: int = Field(default=10)
    ocr_min_confidence_threshold: float = Field(default=60.0)

    # --- Malware Scanning (ClamAV clamd daemon) ---
    clamav_host: str = Field(default="localhost")
    clamav_port: int = Field(default=3310)
    clamav_connect_timeout_seconds: float = Field(default=5.0)
    clamav_scan_timeout_seconds: float = Field(default=30.0)
    clamav_max_stream_bytes: int = Field(default=15 * 1024 * 1024)
    clamav_chunk_bytes: int = Field(default=256 * 1024)

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
    session_idle_timeout_minutes: int = Field(default=30)
    session_absolute_timeout_hours: int = Field(default=8)
    csrf_secret: str = Field(
        default="local-development-csrf-secret-change-in-production-min32chars"
    )
    encryption_key: str = Field(default="local-dev-aes-encryption-key-32bytes-long!")
    allowed_redirect_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )

    # --- Google Workspace Integrations (P11) ---
    google_client_id: str | None = Field(default=None)
    google_client_secret: str | None = Field(default=None)
    google_redirect_uri: str = Field(
        default="http://localhost:8000/api/v1/integrations/google/callback"
    )
    google_auth_endpoint: str = Field(default="https://accounts.google.com/o/oauth2/v2/auth")
    google_token_endpoint: str = Field(default="https://oauth2.googleapis.com/token")
    google_userinfo_endpoint: str = Field(
        default="https://openidconnect.googleapis.com/v1/userinfo"
    )
    google_revoke_endpoint: str = Field(default="https://oauth2.googleapis.com/revoke")
    allow_mock_google_adapter: bool = Field(default=True)

    # --- System Mail ---
    mail_from_address: str = Field(default="system@talvora.local")
    mail_provider: str = Field(default="mailpit")
    mailpit_host: str = Field(default="localhost")
    mailpit_port: int = Field(default=1025)

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
