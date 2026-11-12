"""Drop-off slot generation and booking.

Candidate slots come from the shop's own opening hours, then anything already
busy on its calendar - or already booked in our `appointments` table - is
subtracted. Our table is checked as well as the calendar so two customers
texting at the same moment cannot both take the last slot while the calendar is
still catching up.

All times are stored in UTC and presented in the shop's timezone.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.router import ShopContext
from app.integrations.calendar import Busy, get_calendar_client
from app.models import Appointment

log = logging.getLogger(__name__)

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

DEFAULTS = {
    "slot_minutes": 30,
    "lead_time_hours": 2,
    "max_days_ahead": 10,
    "max_concurrent": 1,
    "duration_minutes": 30,
}


class BookingError(Exception):
    """Raised for a slot that is not offerable - message is customer-safe."""


@dataclass(frozen=True, slots=True)
class Slot:
    start: datetime  # UTC
    end: datetime  # UTC

    def local(self, tz: ZoneInfo) -> datetime:
        return self.start.astimezone(tz)


def _fmt(local: datetime) -> str:
    """Windows strftime has no %-d / %-I, so format without padding by hand."""
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return (
        f"{local.strftime('%A')} {local.day} {local.strftime('%b')}, "
        f"{hour}:{local.strftime('%M')} {ampm}"
    )


def slot_label(slot: Slot, tz: ZoneInfo) -> str:
    return _fmt(slot.local(tz))


def label_at(when: datetime, tz: ZoneInfo) -> str:
    """The same wording for any instant, not just a candidate slot.

    Reminders and the daily summary speak times through this, so a customer who
    was offered "Thursday 12 Nov, 9:00 AM" is reminded in exactly those words.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return _fmt(when.astimezone(tz))


def booking_settings(shop: ShopContext) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(shop.config.get("booking") or {})
    return cfg


def parse_hours(value: str) -> tuple[time, time] | None:
    """'8:00-18:00' -> (08:00, 18:00). 'closed' or junk -> None.

    Public because the admin console validates with it: the form must not be
    able to save an hours string that the booking engine would silently read as
    "closed all day".
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in {"closed", "", "-"}:
        return None
    if "-" not in text:
        return None
    start_raw, _, end_raw = text.partition("-")
    try:
        start_h, _, start_m = start_raw.strip().partition(":")
        end_h, _, end_m = end_raw.strip().partition(":")
        return (
            time(int(start_h), int(start_m or 0)),
            time(int(end_h), int(end_m or 0)),
        )
    except ValueError:
        log.warning("unparseable hours value %r", value)
        return None


def _overlaps(start: datetime, end: datetime, busy: list[Busy]) -> int:
    return sum(1 for b in busy if b.end > start and b.start < end)


async def _existing(
    session: AsyncSession,
    client_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[Busy]:
    """Appointments already in our own table for the window."""
    rows = (
        (
            await session.execute(
                select(Appointment).where(
                    Appointment.client_id == client_id,
                    Appointment.status != "cancelled",
                    Appointment.start_at.is_not(None),
                    Appointment.start_at < end,
                    Appointment.end_at > start,
                )
            )
        )
        .scalars()
        .all()
    )
    return [Busy(start=r.start_at, end=r.end_at) for r in rows if r.end_at]


async def available_slots(
    session: AsyncSession,
    shop: ShopContext,
    *,
    limit: int = 6,
    preferred_day: date | None = None,
    now: datetime | None = None,
) -> list[Slot]:
    """Open drop-off slots, soonest first."""
    cfg = booking_settings(shop)
    tz = ZoneInfo(shop.timezone)
    now = (now or datetime.now(UTC)).astimezone(UTC)

    earliest = now + timedelta(hours=float(cfg["lead_time_hours"]))
    horizon_end = now + timedelta(days=int(cfg["max_days_ahead"]))

    calendar_id = shop.config.get("calendar_id")
    busy: list[Busy] = []
    if calendar_id:
        try:
            busy = await get_calendar_client().busy(
                calendar_id, now, horizon_end
            )
        except Exception as exc:
            # A calendar outage must not silently offer double-booked slots.
            log.error("calendar freebusy failed for %s: %s", calendar_id, exc)
            raise BookingError(
                "I can't reach the booking calendar right now."
            ) from exc

    busy = busy + await _existing(session, shop.client_id, now, horizon_end)

    hours = shop.config.get("hours") or {}
    duration = timedelta(minutes=int(cfg["duration_minutes"]))
    step = timedelta(minutes=int(cfg["slot_minutes"]))
    max_concurrent = int(cfg["max_concurrent"])

    found: list[Slot] = []
    start_day = (preferred_day or now.astimezone(tz).date())
    last_day = horizon_end.astimezone(tz).date()

    day = start_day
    while day <= last_day and len(found) < limit:
        window = parse_hours(hours.get(WEEKDAYS[day.weekday()], ""))
        if window is not None:
            open_at = datetime.combine(day, window[0], tzinfo=tz).astimezone(UTC)
            close_at = datetime.combine(day, window[1], tzinfo=tz).astimezone(UTC)

            cursor = open_at
            while cursor + duration <= close_at and len(found) < limit:
                if cursor >= earliest and _overlaps(
                    cursor, cursor + duration, busy
                ) < max_concurrent:
                    found.append(Slot(start=cursor, end=cursor + duration))
                cursor += step

        if preferred_day is not None and day == preferred_day and found:
            break  # the customer asked for this day and we have options
        day += timedelta(days=1)

    return found


async def confirm_slot(
    session: AsyncSession,
    shop: ShopContext,
    start_at: datetime,
    *,
    now: datetime | None = None,
) -> Slot:
    """Re-check a slot at booking time. Raises BookingError if it is gone."""
    cfg = booking_settings(shop)
    duration = timedelta(minutes=int(cfg["duration_minutes"]))
    start_at = start_at.astimezone(UTC)
    slot = Slot(start=start_at, end=start_at + duration)

    offered = await available_slots(
        session, shop, limit=200, preferred_day=None, now=now
    )
    if not any(s.start == slot.start for s in offered):
        raise BookingError("that time is no longer free")
    return slot


async def create_booking(
    session: AsyncSession,
    shop: ShopContext,
    *,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    slot: Slot,
    service: str,
    item: str,
    customer_name: str,
    notes: str = "",
) -> Appointment:
    """Write the appointment, then mirror it into Google Calendar.

    Our row is the source of truth: if the calendar write fails the booking still
    stands and the owner is told, rather than the customer being promised a slot
    that exists nowhere.
    """
    tz = ZoneInfo(shop.timezone)
    appointment = Appointment(
        client_id=shop.client_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
        start_at=slot.start,
        end_at=slot.end,
        service=service or None,
        item=item or None,
        notes=notes or None,
        status="booked",
    )
    session.add(appointment)
    await session.flush()

    calendar_id = shop.config.get("calendar_id")
    if calendar_id:
        who = customer_name or "Customer"
        summary = f"{who} - {service or 'drop-off'}"
        if item:
            summary = f"{who} - {item} - {service or 'drop-off'}"
        try:
            event_id = await get_calendar_client().create_event(
                calendar_id,
                start=slot.start,
                end=slot.end,
                summary=summary,
                description=(notes or "") + "\n\nBooked by the AI front desk.",
                timezone=shop.timezone,
            )
            appointment.calendar_event_id = event_id
        except Exception as exc:
            log.error("calendar insert failed for %s: %s", calendar_id, exc)
            appointment.notes = (
                (appointment.notes or "")
                + " [calendar sync failed - add to the calendar by hand]"
            ).strip()

    # A car booked in is a job the owner can later mark READY. Creating it here
    # is what lets "READY 2014 Civic" find the right customer to text.
    if item:
        from app.models import JobStatus
        from app.repo import upsert_job

        await upsert_job(
            session,
            shop.client_id,
            customer_id,
            item,
            JobStatus.in_shop,
            updated_by="booking",
        )

    log.info(
        "booked %s for client %s at %s",
        appointment.id,
        shop.client_id,
        slot.local(tz).isoformat(),
    )
    return appointment
