"""Google Calendar access.

Auth is a **single service account**, not per-shop OAuth: during onboarding the
shop shares its calendar with the service account's email and we store the
calendar id on `client_config.calendar_id`. No consent screen, no refresh tokens,
no Google app verification - and nothing a low-tech owner can get stuck on.

With no credentials configured, `StubCalendarClient` takes over: it keeps events
in memory so the whole booking flow is exercisable locally, exactly as the
Twilio adapters stub their sends in Phase 1.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.config import get_settings

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


@dataclass(frozen=True, slots=True)
class Busy:
    start: datetime
    end: datetime


@runtime_checkable
class CalendarClient(Protocol):
    async def busy(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[Busy]:
        """Periods already taken, so the slot builder can subtract them."""
        ...

    async def create_event(
        self,
        calendar_id: str,
        *,
        start: datetime,
        end: datetime,
        summary: str,
        description: str = "",
        timezone: str = "UTC",
    ) -> str:
        """Returns the provider's event id."""
        ...

    async def delete_event(self, calendar_id: str, event_id: str) -> bool: ...


# --------------------------------------------------------------------------- stub


@dataclass
class StubCalendarClient:
    """In-memory calendar. Deterministic, so tests can assert on it."""

    events: dict[str, list[dict]] = field(default_factory=dict)

    async def busy(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[Busy]:
        out = []
        for event in self.events.get(calendar_id, []):
            if event["end"] > start and event["start"] < end:
                out.append(Busy(start=event["start"], end=event["end"]))
        return sorted(out, key=lambda b: b.start)

    async def create_event(
        self,
        calendar_id: str,
        *,
        start: datetime,
        end: datetime,
        summary: str,
        description: str = "",
        timezone: str = "UTC",
    ) -> str:
        event_id = f"stub-evt-{uuid.uuid4().hex[:12]}"
        self.events.setdefault(calendar_id, []).append(
            {
                "id": event_id,
                "start": start,
                "end": end,
                "summary": summary,
                "description": description,
            }
        )
        log.info(
            "[calendar:stub] %s booked %s -> %s (%s)",
            calendar_id,
            start.isoformat(),
            end.isoformat(),
            summary,
        )
        return event_id

    async def delete_event(self, calendar_id: str, event_id: str) -> bool:
        events = self.events.get(calendar_id, [])
        before = len(events)
        self.events[calendar_id] = [e for e in events if e["id"] != event_id]
        return len(self.events[calendar_id]) < before

    def clear(self) -> None:
        self.events.clear()


# ------------------------------------------------------------------------ google


class GoogleCalendarClient:
    """Real Google Calendar via a service account.

    The google client library is synchronous, so every call is pushed to a
    thread - a blocking HTTP call on the event loop would stall the worker.
    """

    def __init__(self, credentials_path: str = "", credentials_json: str = ""):
        self._credentials_path = credentials_path
        self._credentials_json = credentials_json
        self._service = None

    def _build(self):
        if self._service is None:
            import json as _json

            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            if self._credentials_json:
                creds = service_account.Credentials.from_service_account_info(
                    _json.loads(self._credentials_json), scopes=SCOPES
                )
            else:
                creds = service_account.Credentials.from_service_account_file(
                    self._credentials_path, scopes=SCOPES
                )
            self._service = build(
                "calendar", "v3", credentials=creds, cache_discovery=False
            )
        return self._service

    async def busy(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[Busy]:
        def call():
            body = {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": calendar_id}],
            }
            response = self._build().freebusy().query(body=body).execute()
            periods = (
                response.get("calendars", {}).get(calendar_id, {}).get("busy", [])
            )
            return [
                Busy(
                    start=datetime.fromisoformat(p["start"]),
                    end=datetime.fromisoformat(p["end"]),
                )
                for p in periods
            ]

        return await asyncio.to_thread(call)

    async def create_event(
        self,
        calendar_id: str,
        *,
        start: datetime,
        end: datetime,
        summary: str,
        description: str = "",
        timezone: str = "UTC",
    ) -> str:
        def call():
            event = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": start.isoformat(), "timeZone": timezone},
                "end": {"dateTime": end.isoformat(), "timeZone": timezone},
            }
            created = (
                self._build()
                .events()
                .insert(calendarId=calendar_id, body=event)
                .execute()
            )
            return created["id"]

        return await asyncio.to_thread(call)

    async def delete_event(self, calendar_id: str, event_id: str) -> bool:
        def call():
            try:
                self._build().events().delete(
                    calendarId=calendar_id, eventId=event_id
                ).execute()
                return True
            except Exception as exc:
                log.warning("calendar delete failed: %s", exc)
                return False

        return await asyncio.to_thread(call)


# -------------------------------------------------------------------- selection

_client: CalendarClient | None = None


def get_calendar_client() -> CalendarClient:
    global _client
    if _client is None:
        settings = get_settings()
        if settings.google_credentials_json:
            log.info("using Google Calendar service account from the environment")
            _client = GoogleCalendarClient(
                credentials_json=settings.google_credentials_json
            )
        elif settings.google_credentials_path:
            log.info(
                "using Google Calendar service account at %s",
                settings.google_credentials_path,
            )
            _client = GoogleCalendarClient(
                credentials_path=settings.google_credentials_path
            )
        else:
            log.warning(
                "no Google credentials configured - calendar runs on the "
                "in-memory stub; bookings will not appear in a real calendar"
            )
            _client = StubCalendarClient()
    return _client


def set_calendar_client(client: CalendarClient | None) -> None:
    """Test hook."""
    global _client
    _client = client


def is_stubbed() -> bool:
    return isinstance(get_calendar_client(), StubCalendarClient)
