"""Drop-off slot generation and booking.

The clock is injected everywhere, so none of this is time-dependent.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import booking
from app.core.router import load_shop
from app.db import session_scope
from app.integrations.calendar import (
    StubCalendarClient,
    set_calendar_client,
)
from app.models import Channel, Job, JobStatus
from app.repo import find_or_create_conversation, find_or_create_customer

TZ = ZoneInfo("America/Chicago")
CALENDAR = "shop-cal@example.com"

# Thursday 12 Nov 2026, 07:00 Chicago - before the shop opens at 08:00.
NOW = datetime(2026, 11, 12, 7, 0, tzinfo=TZ).astimezone(UTC)


@pytest.fixture
def calendar():
    stub = StubCalendarClient()
    set_calendar_client(stub)
    yield stub
    set_calendar_client(None)


@pytest.fixture
async def shop(shop_a):
    async with session_scope() as session:
        ctx = await load_shop(session, shop_a, use_cache=False)
    # conftest's shop has Mon 8-18 and Sunday closed; fill the rest in.
    ctx.config["hours"] = {
        "monday": "8:00-18:00",
        "tuesday": "8:00-18:00",
        "wednesday": "8:00-18:00",
        "thursday": "8:00-18:00",
        "friday": "8:00-18:00",
        "saturday": "9:00-14:00",
        "sunday": "closed",
    }
    ctx.config["calendar_id"] = CALENDAR
    ctx.config["booking"] = {
        "slot_minutes": 30,
        "duration_minutes": 30,
        "lead_time_hours": 2,
        "max_days_ahead": 10,
        "max_concurrent": 1,
    }
    return ctx


def busy(start_h: int, end_h: int, day: int = 12) -> dict:
    return {
        "id": f"busy-{day}-{start_h}",
        "start": datetime(2026, 11, day, start_h, 0, tzinfo=TZ).astimezone(UTC),
        "end": datetime(2026, 11, day, end_h, 0, tzinfo=TZ).astimezone(UTC),
        "summary": "existing work",
    }


class TestHourParsing:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("8:00-18:00", True),
            ("9:00-14:00", True),
            ("8-18", True),
            ("closed", False),
            ("", False),
            ("-", False),
            ("nonsense", False),
            (None, False),
        ],
    )
    def test_cases(self, value, expected):
        assert (booking.parse_hours(value) is not None) is expected


class TestSlotGeneration:
    async def test_lead_time_is_respected(self, shop, calendar):
        """At 07:00 with a 2-hour lead time, nothing before 09:00 is offered."""
        async with session_scope() as session:
            slots = await booking.available_slots(
                session, shop, limit=4, now=NOW
            )
        assert [s.local(TZ).strftime("%H:%M") for s in slots] == [
            "09:00",
            "09:30",
            "10:00",
            "10:30",
        ]

    async def test_busy_periods_are_subtracted(self, shop, calendar):
        calendar.events[CALENDAR] = [busy(9, 11)]
        async with session_scope() as session:
            slots = await booking.available_slots(
                session, shop, limit=3, now=NOW
            )
        assert [s.local(TZ).strftime("%H:%M") for s in slots] == [
            "11:00",
            "11:30",
            "12:00",
        ]

    async def test_closed_day_rolls_forward(self, shop, calendar):
        """Sunday 15 Nov is closed, so Monday is offered instead."""
        async with session_scope() as session:
            slots = await booking.available_slots(
                session,
                shop,
                limit=1,
                preferred_day=date(2026, 11, 15),
                now=NOW,
            )
        assert slots[0].local(TZ).date() == date(2026, 11, 16)
        assert slots[0].local(TZ).strftime("%H:%M") == "08:00"

    async def test_saturday_hours_are_honoured(self, shop, calendar):
        async with session_scope() as session:
            slots = await booking.available_slots(
                session,
                shop,
                limit=200,
                preferred_day=date(2026, 11, 14),
                now=NOW,
            )
        saturday = [s for s in slots if s.local(TZ).date() == date(2026, 11, 14)]
        assert saturday[0].local(TZ).strftime("%H:%M") == "09:00"
        # Saturday closes at 14:00, so the last 30-minute slot starts at 13:30.
        assert saturday[-1].local(TZ).strftime("%H:%M") == "13:30"
        assert saturday[-1].end.astimezone(TZ).strftime("%H:%M") == "14:00"

    async def test_existing_appointments_also_block(self, shop, calendar):
        """Our own table counts, not just the calendar - two customers texting
        at once must not both take the last slot."""
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop.client_id, "+13125551111"
            )
            slot = booking.Slot(
                start=datetime(2026, 11, 12, 9, 0, tzinfo=TZ).astimezone(UTC),
                end=datetime(2026, 11, 12, 9, 30, tzinfo=TZ).astimezone(UTC),
            )
            await booking.create_booking(
                session,
                shop,
                customer_id=customer.id,
                conversation_id=None,
                slot=slot,
                service="Brakes",
                item="2014 Civic",
                customer_name="Maria",
            )

        async with session_scope() as session:
            slots = await booking.available_slots(
                session, shop, limit=1, now=NOW
            )
        assert slots[0].local(TZ).strftime("%H:%M") == "09:30"

    async def test_max_concurrent_allows_overlap(self, shop, calendar):
        shop.config["booking"]["max_concurrent"] = 2
        calendar.events[CALENDAR] = [busy(9, 10)]
        async with session_scope() as session:
            slots = await booking.available_slots(
                session, shop, limit=1, now=NOW
            )
        assert slots[0].local(TZ).strftime("%H:%M") == "09:00"

    async def test_calendar_failure_raises_rather_than_offering_blind(
        self, shop, calendar, monkeypatch
    ):
        """A calendar outage must not silently offer double-booked slots."""

        async def boom(*args, **kwargs):
            raise RuntimeError("calendar down")

        monkeypatch.setattr(calendar, "busy", boom)
        async with session_scope() as session:
            with pytest.raises(booking.BookingError):
                await booking.available_slots(session, shop, now=NOW)

    async def test_no_calendar_configured_still_offers_slots(self, shop, calendar):
        shop.config["calendar_id"] = None
        async with session_scope() as session:
            slots = await booking.available_slots(
                session, shop, limit=1, now=NOW
            )
        assert slots


class TestConfirmSlot:
    async def test_rejects_a_busy_time(self, shop, calendar):
        calendar.events[CALENDAR] = [busy(9, 11)]
        async with session_scope() as session:
            with pytest.raises(booking.BookingError, match="no longer free"):
                await booking.confirm_slot(
                    session,
                    shop,
                    datetime(2026, 11, 12, 9, 30, tzinfo=TZ).astimezone(UTC),
                    now=NOW,
                )

    async def test_rejects_a_time_inside_the_lead_window(self, shop, calendar):
        async with session_scope() as session:
            with pytest.raises(booking.BookingError):
                await booking.confirm_slot(
                    session,
                    shop,
                    datetime(2026, 11, 12, 8, 0, tzinfo=TZ).astimezone(UTC),
                    now=NOW,
                )

    async def test_rejects_a_closed_day(self, shop, calendar):
        async with session_scope() as session:
            with pytest.raises(booking.BookingError):
                await booking.confirm_slot(
                    session,
                    shop,
                    datetime(2026, 11, 15, 10, 0, tzinfo=TZ).astimezone(UTC),
                    now=NOW,
                )

    async def test_accepts_a_free_time(self, shop, calendar):
        async with session_scope() as session:
            slot = await booking.confirm_slot(
                session,
                shop,
                datetime(2026, 11, 12, 11, 0, tzinfo=TZ).astimezone(UTC),
                now=NOW,
            )
        assert slot.local(TZ).strftime("%H:%M") == "11:00"
        assert (slot.end - slot.start) == timedelta(minutes=30)


class TestCreateBooking:
    async def test_writes_the_row_and_the_calendar_event(self, shop, calendar):
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop.client_id, "+13125552222"
            )
            conversation = await find_or_create_conversation(
                session, shop.client_id, customer.id, Channel.sms
            )
            slot = await booking.confirm_slot(
                session,
                shop,
                datetime(2026, 11, 12, 11, 0, tzinfo=TZ).astimezone(UTC),
                now=NOW,
            )
            appointment = await booking.create_booking(
                session,
                shop,
                customer_id=customer.id,
                conversation_id=conversation.id,
                slot=slot,
                service="Front brakes",
                item="2014 Honda Civic",
                customer_name="Maria Lopez",
            )

        assert appointment.status == "booked"
        assert appointment.calendar_event_id is not None
        assert appointment.start_at == slot.start
        assert appointment.end_at == slot.end
        assert appointment.owner_confirmed_at is None

        events = calendar.events[CALENDAR]
        assert len(events) == 1
        assert "Maria Lopez" in events[0]["summary"]
        assert "2014 Honda Civic" in events[0]["summary"]

    async def test_booking_creates_a_job_so_ready_can_find_the_customer(
        self, shop, calendar
    ):
        from sqlalchemy import select

        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop.client_id, "+13125553333"
            )
            slot = await booking.confirm_slot(
                session,
                shop,
                datetime(2026, 11, 12, 11, 0, tzinfo=TZ).astimezone(UTC),
                now=NOW,
            )
            await booking.create_booking(
                session,
                shop,
                customer_id=customer.id,
                conversation_id=None,
                slot=slot,
                service="Front brakes",
                item="2014 Honda Civic",
                customer_name="Maria",
            )

        async with session_scope() as session:
            jobs = (await session.execute(select(Job))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].item == "2014 Honda Civic"
        assert jobs[0].status == JobStatus.in_shop
        assert jobs[0].updated_by == "booking"

    async def test_calendar_failure_keeps_the_booking(self, shop, calendar, monkeypatch):
        """Our row is the source of truth; a calendar outage must not lose the job."""

        async def boom(*args, **kwargs):
            raise RuntimeError("calendar insert failed")

        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop.client_id, "+13125554444"
            )
            slot = await booking.confirm_slot(
                session,
                shop,
                datetime(2026, 11, 12, 11, 0, tzinfo=TZ).astimezone(UTC),
                now=NOW,
            )
            monkeypatch.setattr(calendar, "create_event", boom)
            appointment = await booking.create_booking(
                session,
                shop,
                customer_id=customer.id,
                conversation_id=None,
                slot=slot,
                service="Brakes",
                item="2014 Civic",
                customer_name="Maria",
            )

        assert appointment.status == "booked"
        assert appointment.calendar_event_id is None
        assert "calendar sync failed" in (appointment.notes or "")


def test_slot_label_formats_without_platform_specific_codes():
    """%-d / %-I are not portable, so the label is built by hand."""
    slot = booking.Slot(
        start=datetime(2026, 11, 12, 9, 0, tzinfo=TZ).astimezone(UTC),
        end=datetime(2026, 11, 12, 9, 30, tzinfo=TZ).astimezone(UTC),
    )
    assert booking.slot_label(slot, TZ) == "Thursday 12 Nov, 9:00 AM"

    afternoon = booking.Slot(
        start=datetime(2026, 11, 12, 13, 30, tzinfo=TZ).astimezone(UTC),
        end=datetime(2026, 11, 12, 14, 0, tzinfo=TZ).astimezone(UTC),
    )
    assert booking.slot_label(afternoon, TZ) == "Thursday 12 Nov, 1:30 PM"
