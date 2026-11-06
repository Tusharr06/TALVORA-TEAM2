"""Ports, Protocols, and DTOs for External Identity and Workspace Integrations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    granted_scopes: list[str]
    token_type: str = "Bearer"
    id_token: str | None = None


@dataclass(frozen=True)
class GoogleUserInfo:
    sub: str
    email: str
    email_verified: bool
    name: str | None = None
    picture: str | None = None


class GoogleOAuthClient(ABC):
    """Protocol for Google OAuth 2.0 authorization code exchange and token management."""

    @abstractmethod
    def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        requested_scopes: list[str],
        prompt: str = "consent",
        access_type: str = "offline",
    ) -> str:
        """Constructs Google OAuth 2.0 consent screen URL."""
        ...

    @abstractmethod
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
    ) -> GoogleTokens:
        """Exchanges an authorization code for access and refresh tokens."""
        ...

    @abstractmethod
    async def refresh_access_token(
        self,
        refresh_token: str,
    ) -> GoogleTokens:
        """Uses a refresh token to obtain a fresh short-lived access token."""
        ...

    @abstractmethod
    async def get_user_info(
        self,
        access_token: str,
    ) -> GoogleUserInfo:
        """Retrieves verified OpenID identity attributes from Google UserInfo endpoint."""
        ...

    @abstractmethod
    async def revoke_token(
        self,
        token: str,
    ) -> bool:
        """Revokes an access or refresh token with Google."""
        ...


class CalendarAvailabilityStatus(StrEnum):
    """Tri-state availability result for a queried calendar or interval."""

    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TimeInterval:
    """Half-open time interval [start, end)."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"Interval start ({self.start}) must precede end ({self.end}).")

    def overlaps(self, other: "TimeInterval") -> bool:
        return self.start < other.end and self.end > other.start


@dataclass(frozen=True)
class CalendarSchedule:
    """FreeBusy busy periods and accessibility status for a single calendar ID."""

    calendar_id: str
    status: CalendarAvailabilityStatus
    busy_intervals: list[TimeInterval] = field(default_factory=list)
    error_detail: str | None = None


@dataclass(frozen=True)
class FreeBusyResponse:
    time_min: datetime
    time_max: datetime
    schedules: dict[str, CalendarSchedule]


@dataclass(frozen=True)
class CalendarEventAttendee:
    email: str
    response_status: str = "needsAction"  # "needsAction", "declined", "tentative", "accepted"
    optional: bool = False
    organizer: bool = False


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    attendees: list[CalendarEventAttendee] = field(default_factory=list)
    html_link: str | None = None
    meet_link: str | None = None
    status: str = "confirmed"  # "confirmed", "tentative", "cancelled"
    etag: str | None = None
    organizer_email: str | None = None


@dataclass(frozen=True)
class CreateEventRequest:
    event_id: str  # Deterministic event ID e.g. talvora_<uuid>
    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    attendees: list[str] = field(default_factory=list)
    create_meet_link: bool = True


@dataclass(frozen=True)
class UpdateEventRequest:
    event_id: str
    summary: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    description: str | None = None
    attendees: list[str] | None = None
    status: str | None = None
    etag: str | None = None


@dataclass(frozen=True)
class CalendarChangesResponse:
    events: list[CalendarEvent]
    next_sync_token: str | None = None


class CalendarProvider(ABC):
    """Hexagonal Port for Google Calendar FreeBusy and Event operations."""

    @abstractmethod
    async def query_freebusy(
        self,
        access_token: str,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
    ) -> FreeBusyResponse:
        """Queries Google Calendar FreeBusy API across one or more calendars."""
        ...

    @abstractmethod
    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        request: CreateEventRequest,
    ) -> CalendarEvent:
        """Creates a Google Calendar event with a deterministic event ID."""
        ...

    @abstractmethod
    async def get_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> CalendarEvent:
        """Fetches a single calendar event by its provider event ID."""
        ...

    @abstractmethod
    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        request: UpdateEventRequest,
    ) -> CalendarEvent:
        """Updates a calendar event, checking ETag for optimistic concurrency where supplied."""
        ...

    @abstractmethod
    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> bool:
        """Cancels/deletes a calendar event."""
        ...

    @abstractmethod
    async def sync_changes(
        self,
        access_token: str,
        calendar_id: str,
        sync_token: str | None = None,
    ) -> CalendarChangesResponse:
        """Fetches incremental calendar changes using Google Calendar incremental sync."""
        ...


class MailSendResultStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DEFINITIVE_FAILURE = "DEFINITIVE_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SendEmailPayload:
    from_address: str
    to_recipients: list[str]
    subject: str
    body_text: str
    body_html: str | None = None
    cc_recipients: list[str] = field(default_factory=list)
    bcc_recipients: list[str] = field(default_factory=list)
    reply_to: str | None = None


@dataclass(frozen=True)
class SendEmailResult:
    status: MailSendResultStatus
    provider_message_id: str | None = None
    error_message: str | None = None


class GmailProvider(ABC):
    """Hexagonal Port for delegated, least-privilege Gmail sending operations."""

    @abstractmethod
    async def send_message(
        self,
        access_token: str,
        payload: SendEmailPayload,
    ) -> SendEmailResult:
        """Sends an RFC 2822 email message via the user's connected Gmail account."""
        ...
