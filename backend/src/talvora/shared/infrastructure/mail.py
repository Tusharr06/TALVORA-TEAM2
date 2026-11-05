"""System Mail Provider Protocol and Adapters (Mailpit & In-Memory)."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


@dataclass(frozen=True)
class SentEmail:
    """Record of an email dispatched through system mail provider."""

    recipient: str
    subject: str
    body_text: str
    body_html: str | None = None
    from_address: str = "system@talvora.local"


class SystemMailProvider(Protocol):
    """Protocol for sending transactional system and identity emails."""

    def send_system_email(
        self,
        recipient: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> bool:
        """Dispatches an email to recipient. Returns True if accepted."""
        ...


class InMemorySystemMailProvider:
    """In-memory mail sink for tests and local verification."""

    def __init__(self, from_address: str = "system@talvora.local") -> None:
        self.from_address = from_address
        self.outbox: list[SentEmail] = []

    def send_system_email(
        self,
        recipient: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> bool:
        self.outbox.append(
            SentEmail(
                recipient=recipient,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                from_address=self.from_address,
            )
        )
        return True


class MailpitSystemMailProvider:
    """SMTP adapter for local Mailpit service."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1025,
        from_address: str = "system@talvora.local",
    ) -> None:
        self.host = host
        self.port = port
        self.from_address = from_address

    def send_system_email(
        self,
        recipient: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> bool:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_address
        msg["To"] = recipient
        msg.set_content(body_text)

        if body_html:
            msg.add_alternative(body_html, subtype="html")

        try:
            with smtplib.SMTP(self.host, self.port, timeout=3.0) as smtp:
                smtp.send_message(msg)
            return True
        except Exception:
            return False
