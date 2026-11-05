"""Unit tests for JSON logging and PII scrubbing."""

import json
import logging

from talvora.shared.infrastructure.logging import (
    JSONLogFormatter,
    correlation_id_ctx,
    scrub_pii,
)


def test_scrub_pii_redacts_sensitive_patterns() -> None:
    raw_text = (
        "Candidate email is candidate.test@example.com, phone is +1 (555) 234-5678. "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123 was used. "
        'Payload: {"password": "SuperSecretPassword!", "token": "sensitive"}'
    )
    scrubbed = scrub_pii(raw_text)

    assert "candidate.test@example.com" not in scrubbed
    assert "[REDACTED_EMAIL]" in scrubbed

    assert "234-5678" not in scrubbed
    assert "[REDACTED_PHONE]" in scrubbed

    assert "eyJhbGci" not in scrubbed
    assert "[REDACTED_TOKEN]" in scrubbed

    assert "SuperSecretPassword!" not in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed


def test_json_formatter_injects_correlation_id() -> None:
    formatter = JSONLogFormatter(service_name="talvora-test")
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Processing candidate email candidate@domain.com",
        args=(),
        exc_info=None,
    )

    # Set correlation ID in context
    token = correlation_id_ctx.set("corr-uuid-12345")
    try:
        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert data["service"] == "talvora-test"
        assert data["level"] == "INFO"
        assert data["correlation_id"] == "corr-uuid-12345"
        assert "candidate@domain.com" not in data["message"]
        assert "[REDACTED_EMAIL]" in data["message"]
    finally:
        correlation_id_ctx.reset(token)
