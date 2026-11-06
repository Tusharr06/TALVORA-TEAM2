"""Gmail Delegated Mail Provider Adapter: Send-Only Operations via Google Workspace."""

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from talvora.modules.integrations.application.ports import (
    GmailProvider,
    MailSendResultStatus,
    SendEmailPayload,
    SendEmailResult,
)
from talvora.modules.integrations.domain.errors import (
    InsufficientScopesError,
    IntegrationReauthRequiredError,
    ProviderRateLimitError,
)


class GoogleGmailAdapter(GmailProvider):
    """Production delegated email adapter communicating with Gmail REST API v1."""

    def __init__(
        self,
        base_url: str = "https://gmail.googleapis.com/gmail/v1",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_mime_message(self, payload: SendEmailPayload) -> str:
        msg = MIMEMultipart("alternative")
        msg["From"] = payload.from_address
        msg["To"] = ", ".join(payload.to_recipients)
        msg["Subject"] = payload.subject
        if payload.reply_to:
            msg["Reply-To"] = payload.reply_to
        if payload.cc_recipients:
            msg["Cc"] = ", ".join(payload.cc_recipients)

        part_text = MIMEText(payload.body_text, "plain", "utf-8")
        msg.attach(part_text)

        if payload.body_html:
            part_html = MIMEText(payload.body_html, "html", "utf-8")
            msg.attach(part_html)

        raw_bytes = msg.as_bytes()
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii")

    async def send_message(
        self,
        access_token: str,
        payload: SendEmailPayload,
    ) -> SendEmailResult:
        encoded_raw = self._build_mime_message(payload)
        body = {"raw": encoded_raw}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/users/me/messages/send",
                    headers=self._headers(access_token),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            return SendEmailResult(
                status=MailSendResultStatus.UNKNOWN,
                error_message=f"Gmail send request timed out: {exc}",
            )
        except httpx.RequestError as exc:
            return SendEmailResult(
                status=MailSendResultStatus.UNKNOWN,
                error_message=f"Network error during Gmail send: {exc}",
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            return SendEmailResult(
                status=MailSendResultStatus.ACCEPTED,
                provider_message_id=data.get("id"),
            )

        if resp.status_code == 401:
            raise IntegrationReauthRequiredError("Gmail token expired or revoked.")
        if resp.status_code == 403:
            raise InsufficientScopesError(
                "Gmail send permission denied or missing https://www.googleapis.com/auth/gmail.send scope."
            )
        if resp.status_code == 429:
            raise ProviderRateLimitError("Gmail API rate limit exceeded.")
        if resp.status_code >= 500:
            return SendEmailResult(
                status=MailSendResultStatus.UNKNOWN,
                error_message=f"Gmail service error ({resp.status_code}): {resp.text}",
            )

        return SendEmailResult(
            status=MailSendResultStatus.DEFINITIVE_FAILURE,
            error_message=f"Gmail send rejected ({resp.status_code}): {resp.text}",
        )


class MockGoogleGmailAdapter(GmailProvider):
    """High-fidelity test double for Gmail operations with in-memory delivery inspection."""

    def __init__(self) -> None:
        self.sent_messages: list[SendEmailPayload] = []
        self.simulate_timeout: bool = False
        self.simulate_reauth: bool = False
        self.simulate_rejected: bool = False

    async def send_message(
        self,
        access_token: str,
        payload: SendEmailPayload,
    ) -> SendEmailResult:
        if self.simulate_timeout:
            return SendEmailResult(
                status=MailSendResultStatus.UNKNOWN,
                error_message="Simulated Gmail timeout.",
            )
        if self.simulate_reauth:
            raise IntegrationReauthRequiredError("Simulated token expired.")
        if self.simulate_rejected:
            return SendEmailResult(
                status=MailSendResultStatus.DEFINITIVE_FAILURE,
                error_message="Simulated recipient rejection.",
            )

        self.sent_messages.append(payload)
        return SendEmailResult(
            status=MailSendResultStatus.ACCEPTED,
            provider_message_id=f"gmail_msg_{len(self.sent_messages)}",
        )
