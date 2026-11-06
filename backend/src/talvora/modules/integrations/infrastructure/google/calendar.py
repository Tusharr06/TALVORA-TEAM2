"""Google Calendar Provider Adapters: Real API v3 Client and High-Fidelity Test Double."""

import urllib.parse
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from talvora.modules.integrations.application.ports import (
    CalendarAvailabilityStatus,
    CalendarChangesResponse,
    CalendarEvent,
    CalendarEventAttendee,
    CalendarProvider,
    CalendarSchedule,
    CreateEventRequest,
    FreeBusyResponse,
    TimeInterval,
    UpdateEventRequest,
)
from talvora.modules.integrations.domain.errors import (
    InsufficientScopesError,
    IntegrationError,
    IntegrationReauthRequiredError,
    ProviderETagConflictError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_google_datetime(val: dict[str, Any]) -> datetime:
    raw = val.get("dateTime") or val.get("date")
    if not raw:
        return datetime.now(UTC)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return _ensure_utc(dt)


class GoogleCalendarAdapter(CalendarProvider):
    """Production Calendar adapter interacting with Google Calendar API v3."""

    def __init__(
        self,
        base_url: str = "https://www.googleapis.com/calendar/v3",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def _headers(self, access_token: str, etag: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if etag:
            clean_etag = etag.strip('"')
            headers["If-Match"] = f'"{clean_etag}"'
        return headers

    def _handle_error(self, resp: httpx.Response, action: str) -> None:
        if resp.status_code == 401:
            raise IntegrationReauthRequiredError(
                f"{action} failed: access token expired or revoked."
            )
        if resp.status_code == 403:
            raise InsufficientScopesError(
                f"{action} failed: insufficient Calendar scopes or permission denied."
            )
        if resp.status_code == 404:
            raise IntegrationError(
                status_code=404, detail=f"{action} failed: calendar resource not found."
            )
        if resp.status_code == 409:
            raise IntegrationError(
                status_code=409,
                detail=f"{action} failed: duplicate or conflicting event identifier.",
            )
        if resp.status_code == 412:
            raise ProviderETagConflictError(
                f"{action} failed: event was modified concurrently by another process."
            )
        if resp.status_code == 429:
            raise ProviderRateLimitError(
                f"{action} failed: Google Calendar API rate limit exceeded."
            )
        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"{action} failed: Google Calendar service error ({resp.status_code})."
            )
        raise IntegrationError(status_code=resp.status_code, detail=f"{action} failed: {resp.text}")

    async def query_freebusy(
        self,
        access_token: str,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
    ) -> FreeBusyResponse:
        t_min_utc = _ensure_utc(time_min)
        t_max_utc = _ensure_utc(time_max)

        payload = {
            "timeMin": t_min_utc.isoformat(),
            "timeMax": t_max_utc.isoformat(),
            "items": [{"id": cal_id} for cal_id in calendar_ids],
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/freeBusy",
                    headers=self._headers(access_token),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("FreeBusy query timed out.") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(f"FreeBusy network error: {exc}") from exc

        if resp.status_code != 200:
            self._handle_error(resp, "FreeBusy query")

        data = resp.json()
        calendars_data = data.get("calendars", {})
        schedules: dict[str, CalendarSchedule] = {}

        for cal_id in calendar_ids:
            cal_info = calendars_data.get(cal_id, {})
            errors = cal_info.get("errors", [])
            if errors:
                err_msg = "; ".join(e.get("reason", "unknown_error") for e in errors)
                schedules[cal_id] = CalendarSchedule(
                    calendar_id=cal_id,
                    status=CalendarAvailabilityStatus.UNKNOWN,
                    busy_intervals=[],
                    error_detail=f"Calendar query failed: {err_msg}",
                )
                continue

            raw_busy = cal_info.get("busy", [])
            intervals: list[TimeInterval] = []
            for b in raw_busy:
                start_dt = _parse_google_datetime({"dateTime": b["start"]})
                end_dt = _parse_google_datetime({"dateTime": b["end"]})
                if start_dt < end_dt:
                    intervals.append(TimeInterval(start=start_dt, end=end_dt))

            intervals.sort(key=lambda i: i.start)
            status = (
                CalendarAvailabilityStatus.BUSY
                if intervals
                else CalendarAvailabilityStatus.AVAILABLE
            )
            schedules[cal_id] = CalendarSchedule(
                calendar_id=cal_id,
                status=status,
                busy_intervals=intervals,
            )

        return FreeBusyResponse(
            time_min=t_min_utc,
            time_max=t_max_utc,
            schedules=schedules,
        )

    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        request: CreateEventRequest,
    ) -> CalendarEvent:
        start_utc = _ensure_utc(request.start)
        end_utc = _ensure_utc(request.end)

        payload: dict[str, Any] = {
            "id": request.event_id,
            "summary": request.summary,
            "start": {"dateTime": start_utc.isoformat()},
            "end": {"dateTime": end_utc.isoformat()},
            "attendees": [{"email": email} for email in request.attendees],
        }
        if request.description:
            payload["description"] = request.description

        if request.create_meet_link:
            payload["conferenceData"] = {
                "createRequest": {
                    "requestId": f"meet_{request.event_id[:20]}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        params = {"conferenceDataVersion": "1", "sendUpdates": "all"}
        encoded_cal = urllib.parse.quote(calendar_id)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/calendars/{encoded_cal}/events",
                    headers=self._headers(access_token),
                    params=params,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("Event creation timed out.") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(f"Event creation network error: {exc}") from exc

        if resp.status_code not in (200, 201):
            self._handle_error(resp, "Event creation")

        data = resp.json()
        meet_link = None
        conf = data.get("conferenceData", {})
        for entry in conf.get("entryPoints", []):
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri")
                break

        attendees = [
            CalendarEventAttendee(
                email=att.get("email", ""),
                response_status=att.get("responseStatus", "needsAction"),
                optional=bool(att.get("optional", False)),
                organizer=bool(att.get("organizer", False)),
            )
            for att in data.get("attendees", [])
        ]

        return CalendarEvent(
            id=data["id"],
            summary=data.get("summary", ""),
            start=_parse_google_datetime(data["start"]),
            end=_parse_google_datetime(data["end"]),
            description=data.get("description"),
            attendees=attendees,
            html_link=data.get("htmlLink"),
            meet_link=meet_link,
            status=data.get("status", "confirmed"),
            etag=data.get("etag", "").strip('"'),
            organizer_email=data.get("organizer", {}).get("email"),
        )

    async def get_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> CalendarEvent:
        encoded_cal = urllib.parse.quote(calendar_id)
        encoded_evt = urllib.parse.quote(event_id)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/calendars/{encoded_cal}/events/{encoded_evt}",
                    headers=self._headers(access_token),
                )
        except Exception as exc:
            raise ProviderUnavailableError(f"Event fetch error: {exc}") from exc

        if resp.status_code != 200:
            self._handle_error(resp, "Event fetch")

        data = resp.json()
        meet_link = None
        conf = data.get("conferenceData", {})
        for entry in conf.get("entryPoints", []):
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri")
                break

        attendees = [
            CalendarEventAttendee(
                email=att.get("email", ""),
                response_status=att.get("responseStatus", "needsAction"),
                optional=bool(att.get("optional", False)),
                organizer=bool(att.get("organizer", False)),
            )
            for att in data.get("attendees", [])
        ]

        return CalendarEvent(
            id=data["id"],
            summary=data.get("summary", ""),
            start=_parse_google_datetime(data["start"]),
            end=_parse_google_datetime(data["end"]),
            description=data.get("description"),
            attendees=attendees,
            html_link=data.get("htmlLink"),
            meet_link=meet_link,
            status=data.get("status", "confirmed"),
            etag=data.get("etag", "").strip('"'),
            organizer_email=data.get("organizer", {}).get("email"),
        )

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        request: UpdateEventRequest,
    ) -> CalendarEvent:
        encoded_cal = urllib.parse.quote(calendar_id)
        encoded_evt = urllib.parse.quote(request.event_id)

        payload: dict[str, Any] = {}
        if request.summary is not None:
            payload["summary"] = request.summary
        if request.start is not None:
            payload["start"] = {"dateTime": _ensure_utc(request.start).isoformat()}
        if request.end is not None:
            payload["end"] = {"dateTime": _ensure_utc(request.end).isoformat()}
        if request.description is not None:
            payload["description"] = request.description
        if request.attendees is not None:
            payload["attendees"] = [{"email": email} for email in request.attendees]
        if request.status is not None:
            payload["status"] = request.status

        headers = self._headers(access_token, etag=request.etag)
        params = {"sendUpdates": "all"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.patch(
                    f"{self.base_url}/calendars/{encoded_cal}/events/{encoded_evt}",
                    headers=headers,
                    params=params,
                    json=payload,
                )
        except Exception as exc:
            raise ProviderUnavailableError(f"Event update network error: {exc}") from exc

        if resp.status_code != 200:
            self._handle_error(resp, "Event update")

        data = resp.json()
        meet_link = None
        conf = data.get("conferenceData", {})
        for entry in conf.get("entryPoints", []):
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri")
                break

        attendees = [
            CalendarEventAttendee(
                email=att.get("email", ""),
                response_status=att.get("responseStatus", "needsAction"),
                optional=bool(att.get("optional", False)),
                organizer=bool(att.get("organizer", False)),
            )
            for att in data.get("attendees", [])
        ]

        return CalendarEvent(
            id=data["id"],
            summary=data.get("summary", ""),
            start=_parse_google_datetime(data["start"]),
            end=_parse_google_datetime(data["end"]),
            description=data.get("description"),
            attendees=attendees,
            html_link=data.get("htmlLink"),
            meet_link=meet_link,
            status=data.get("status", "confirmed"),
            etag=data.get("etag", "").strip('"'),
            organizer_email=data.get("organizer", {}).get("email"),
        )

    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> bool:
        encoded_cal = urllib.parse.quote(calendar_id)
        encoded_evt = urllib.parse.quote(event_id)
        params = {"sendUpdates": "all"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.delete(
                    f"{self.base_url}/calendars/{encoded_cal}/events/{encoded_evt}",
                    headers=self._headers(access_token),
                    params=params,
                )
        except Exception as exc:
            raise ProviderUnavailableError(f"Event cancellation network error: {exc}") from exc

        if resp.status_code in (200, 204, 410):
            return True
        if resp.status_code == 404:
            return True
        self._handle_error(resp, "Event cancellation")
        return False

    async def sync_changes(
        self,
        access_token: str,
        calendar_id: str,
        sync_token: str | None = None,
    ) -> CalendarChangesResponse:
        encoded_cal = urllib.parse.quote(calendar_id)
        params: dict[str, str] = {"singleEvents": "true"}
        if sync_token:
            params["syncToken"] = sync_token

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/calendars/{encoded_cal}/events",
                    headers=self._headers(access_token),
                    params=params,
                )
        except Exception as exc:
            raise ProviderUnavailableError(f"Calendar sync error: {exc}") from exc

        if resp.status_code == 410:
            return await self.sync_changes(access_token, calendar_id, sync_token=None)

        if resp.status_code != 200:
            self._handle_error(resp, "Calendar sync")

        data = resp.json()
        events: list[CalendarEvent] = []
        for item in data.get("items", []):
            attendees = [
                CalendarEventAttendee(
                    email=a.get("email", ""),
                    response_status=a.get("responseStatus", "needsAction"),
                    optional=bool(a.get("optional", False)),
                    organizer=bool(a.get("organizer", False)),
                )
                for a in item.get("attendees", [])
            ]
            events.append(
                CalendarEvent(
                    id=item["id"],
                    summary=item.get("summary", ""),
                    start=_parse_google_datetime(item.get("start", {})),
                    end=_parse_google_datetime(item.get("end", {})),
                    description=item.get("description"),
                    attendees=attendees,
                    html_link=item.get("htmlLink"),
                    meet_link=None,
                    status=item.get("status", "confirmed"),
                    etag=item.get("etag", "").strip('"'),
                    organizer_email=item.get("organizer", {}).get("email"),
                )
            )

        return CalendarChangesResponse(
            events=events,
            next_sync_token=data.get("nextSyncToken"),
        )


class MockGoogleCalendarAdapter(CalendarProvider):
    """High-fidelity in-memory Calendar adapter for test and offline dev environments."""

    def __init__(self) -> None:
        self.schedules: dict[str, list[TimeInterval]] = {}
        self.events: dict[str, CalendarEvent] = {}
        self.inaccessible_calendars: set[str] = set()

        # Fault injection
        self.simulate_timeout: bool = False
        self.simulate_create_timeout: bool = False
        self.simulate_rate_limit: bool = False
        self.simulate_etag_conflict: bool = False

    def add_busy_interval(self, calendar_id: str, start: datetime, end: datetime) -> None:
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        if calendar_id not in self.schedules:
            self.schedules[calendar_id] = []
        self.schedules[calendar_id].append(TimeInterval(start=start_utc, end=end_utc))
        self.schedules[calendar_id].sort(key=lambda i: i.start)

    def mark_inaccessible(self, calendar_id: str) -> None:
        self.inaccessible_calendars.add(calendar_id)

    async def query_freebusy(
        self,
        access_token: str,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
    ) -> FreeBusyResponse:
        if self.simulate_timeout:
            raise ProviderUnavailableError("Simulated FreeBusy timeout.")
        if self.simulate_rate_limit:
            raise ProviderRateLimitError("Simulated Google Calendar rate limit.")

        t_min_utc = _ensure_utc(time_min)
        t_max_utc = _ensure_utc(time_max)
        query_window = TimeInterval(start=t_min_utc, end=t_max_utc)

        schedules: dict[str, CalendarSchedule] = {}
        for cal_id in calendar_ids:
            if cal_id in self.inaccessible_calendars:
                schedules[cal_id] = CalendarSchedule(
                    calendar_id=cal_id,
                    status=CalendarAvailabilityStatus.UNKNOWN,
                    busy_intervals=[],
                    error_detail=f"Calendar '{cal_id}' permission denied or not found.",
                )
                continue

            all_intervals = self.schedules.get(cal_id, [])
            overlapping = [i for i in all_intervals if i.overlaps(query_window)]
            status = (
                CalendarAvailabilityStatus.BUSY
                if overlapping
                else CalendarAvailabilityStatus.AVAILABLE
            )
            schedules[cal_id] = CalendarSchedule(
                calendar_id=cal_id,
                status=status,
                busy_intervals=overlapping,
            )

        return FreeBusyResponse(
            time_min=t_min_utc,
            time_max=t_max_utc,
            schedules=schedules,
        )

    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        request: CreateEventRequest,
    ) -> CalendarEvent:
        if self.simulate_timeout or self.simulate_create_timeout:
            raise ProviderUnavailableError("Simulated event creation timeout.")
        if self.simulate_rate_limit:
            raise ProviderRateLimitError("Simulated Google Calendar rate limit.")

        if request.event_id in self.events:
            raise IntegrationError(
                status_code=409, detail=f"Event ID '{request.event_id}' already exists."
            )

        event = CalendarEvent(
            id=request.event_id,
            summary=request.summary,
            start=_ensure_utc(request.start),
            end=_ensure_utc(request.end),
            description=request.description,
            attendees=[
                CalendarEventAttendee(email=e, response_status="needsAction")
                for e in request.attendees
            ],
            html_link=f"https://calendar.google.com/event?eid={request.event_id}",
            meet_link=f"https://meet.google.com/mock-{uuid4().hex[:8]}"
            if request.create_meet_link
            else None,
            status="confirmed",
            etag=f"etag_{uuid4().hex[:8]}",
            organizer_email="interviewer@talvora.test",
        )
        self.events[request.event_id] = event
        self.add_busy_interval(calendar_id, event.start, event.end)
        return event

    async def get_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> CalendarEvent:
        event = self.events.get(event_id)
        if not event:
            raise IntegrationError(status_code=404, detail=f"Event '{event_id}' not found.")
        return event

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        request: UpdateEventRequest,
    ) -> CalendarEvent:
        if self.simulate_etag_conflict:
            raise ProviderETagConflictError("Simulated ETag conflict.")

        existing = self.events.get(request.event_id)
        if not existing:
            raise IntegrationError(status_code=404, detail=f"Event '{request.event_id}' not found.")

        if request.etag and existing.etag and request.etag != existing.etag:
            raise ProviderETagConflictError("ETag mismatch; event was updated concurrently.")

        updated_event = CalendarEvent(
            id=existing.id,
            summary=request.summary or existing.summary,
            start=_ensure_utc(request.start) if request.start else existing.start,
            end=_ensure_utc(request.end) if request.end else existing.end,
            description=request.description
            if request.description is not None
            else existing.description,
            attendees=[
                CalendarEventAttendee(email=e, response_status="needsAction")
                for e in (
                    request.attendees
                    if request.attendees is not None
                    else [a.email for a in existing.attendees]
                )
            ],
            html_link=existing.html_link,
            meet_link=existing.meet_link,
            status=request.status or existing.status,
            etag=f"etag_{uuid4().hex[:8]}",
            organizer_email=existing.organizer_email,
        )
        self.events[request.event_id] = updated_event
        return updated_event

    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> bool:
        if event_id in self.events:
            del self.events[event_id]
        return True

    async def sync_changes(
        self,
        access_token: str,
        calendar_id: str,
        sync_token: str | None = None,
    ) -> CalendarChangesResponse:
        return CalendarChangesResponse(
            events=list(self.events.values()),
            next_sync_token=f"sync_token_{uuid4().hex[:8]}",
        )
