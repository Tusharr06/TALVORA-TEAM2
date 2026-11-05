"""Structured JSON Logging & PII Redaction.

Formats application logs as single-line JSON with automatic correlation ID
injection, log-level standardization, and regex-based redaction of sensitive
candidate personal information, bearer tokens, passwords, and secrets.
"""

import contextvars
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

# ContextVar for propagating request correlation IDs across async calls
correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

# Regex patterns for redacting Personally Identifiable Information (PII)
PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer tokens and authorization headers
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    # Passwords and secrets in key-value format
    (
        re.compile(
            r'("(?:password|secret|key|token|credential|authorization)"\s*:\s*)"[^"]+"',
            re.IGNORECASE,
        ),
        r'\1"[REDACTED_SECRET]"',
    ),
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"),
        "[REDACTED_EMAIL]",
    ),
    # US / International Phone Numbers
    (
        re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[REDACTED_PHONE]",
    ),
    # Credit Card Numbers (13-16 digits)
    (
        re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        "[REDACTED_PAYMENT_CARD]",
    ),
]


def scrub_pii(text: str) -> str:
    """Scrub sensitive PII and credential patterns from text."""
    result = text
    for pattern, replacement in PII_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class JSONLogFormatter(logging.Formatter):
    """Custom logging formatter that produces clean, structured JSON."""

    def __init__(self, service_name: str = "talvora-backend") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        correlation_id = correlation_id_ctx.get()

        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": self.service_name,
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_pii(record.getMessage()),
            "correlation_id": correlation_id or getattr(record, "correlation_id", None),
        }

        # Include location metadata in error/debug logs
        if record.levelno >= logging.WARNING:
            log_data["module"] = record.module
            log_data["line"] = record.lineno

        # Exception information if present
        if record.exc_info and record.exc_text:
            log_data["exception"] = scrub_pii(record.exc_text)
        elif record.exc_info:
            log_data["exception"] = scrub_pii(self.formatException(record.exc_info))

        # Include custom extra fields if provided
        for key, val in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "correlation_id",
            ):
                if isinstance(val, str):
                    log_data[key] = scrub_pii(val)
                elif isinstance(val, (int, float, bool, list, dict)) or val is None:
                    log_data[key] = val
                else:
                    log_data[key] = str(val)

        return json.dumps(log_data)


def configure_logging(log_level: str = "INFO", service_name: str = "talvora-backend") -> None:
    """Configures the root application logger with JSON output."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicate log lines
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JSONLogFormatter(service_name=service_name))
    root_logger.addHandler(handler)
