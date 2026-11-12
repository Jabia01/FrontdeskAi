"""The daily summary: what it says, when it goes, and that it goes once.

The failure that matters most is not a wrong number in the message - it is
sending two summaries for one day, or sending an empty one every morning until
the owner mutes us.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core import summary
from app.core.router import invalidate
from app.db import session_scope
from app.models import (
    Appointment,
    Channel,
    Client,
    ClientConfig,
    ClientStatus,
    Direction,
    JobStatus,
    MessageStatus,
)
from app.repo import (
    add_message,
    find_or_create_conversation,
    find_or_create_customer,
    hand_off,
    last_summary_on,
    upsert_job,
)

#: 13:00 UTC is 7:00 AM in Chicago - exactly the default summary hour.
MORNING = datetime(2026, 11, 11, 13, 30, tzinfo=UTC)
YESTERDAY_NOON = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_translation(monkeypatch):
    async def fake(text, *, to_language, from_language="en"):
        if to_language and to_language != from_language:
            return f"[{to_language}] {text}", True
        return text, False

    monkeypatch.setattr("app.core.summary.translate", fake)


@pytest.fixture
def to_owner(monkeypatch):
    """Capture what the owner would have been sent."""
    sent: list[str] = []

    async def fake(session, shop, body):
        sent.append(body)
        return True, "sms"

    monkeypatch.setattr("app.core.summary.send_to_owner", fake)
    return sent


async def set_config(client_id, **values) -> None:
    async with session_scope() as session:
        await session.execute(
            update(ClientConfig)
            .where(ClientConfig.client_id == client_id)
            .values(**values)
        )
    invalidate(client_id)


async def busy_yesterday(client_id, *, phone="+13125557171") -> dict:
    """Two inbound messages, one reply, one appointment booked yesterday."""
    async with session_scope() as session:
        customer = await find_or_create_customer(session, client_id, phone)
        customer.name = "Maria"
        conversation = await find_or_create_conversation(
            session, client_id, customer.id, Channel.sms
        )
        for body, direction in (
            ("what time do you close?", Direction.inbound),
            ("We close at 6pm.", Direction.outbound),
            ("can I come tomorrow?", Direction.inbound),
        ):
            message = await add_message(
                session,
                client_id,
                conversation.id,
                direction=direction,
                channel=Channel.sms,
                body=body,
                status=(
                    MessageStatus.sent
                    if direction is Direction.outbound
                    else MessageStatus.received
                ),
            )
            message.created_at = YESTERDAY_NOON
        appointment = Appointment(
            client_id=client_id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            start_at=MORNING + timedelta(hours=1),
            service="Front brakes",
            item="2014 Civic",
            status="booked",
        )
        session.add(appointment)
        await session.flush()
        appointment.created_at = YESTERDAY_NOON
        return {"customer_id": customer.id, "conversation_id": conversation.id}


class TestContent:
    async def test_it_reports_yesterday_and_today(self, shop_a, to_owner):
        await busy_yesterday(shop_a)
        result = await send(shop_a)

        assert result["status"] == "sent"
        body = to_owner[0]
        assert "Shop A" in body
        assert "2 messages in" in body
        assert "1 answered" in body
        assert "1 appointment booked" in body
        assert "Today (1):" in body
        assert "Front brakes" in body
        assert "2014 Civic" in body
        assert "Maria" in body

    async def test_times_are_in_the_business_own_timezone(self, shop_a, to_owner):
        await busy_yesterday(shop_a)
        await send(shop_a)
        # MORNING + 1h is 14:30 UTC, which is 8:30 AM in Chicago.
        assert "8:30 AM" in to_owner[0]

    async def test_it_is_written_in_the_owners_language(self, shop_a, to_owner):
        await set_config(shop_a, owner_language="es")
        await busy_yesterday(shop_a)
        await send(shop_a)
        assert to_owner[0].startswith("[es] ")

    async def test_it_lists_what_is_waiting_on_them(self, shop_a, to_owner):
        ids = await busy_yesterday(shop_a)
        async with session_scope() as session:
            await hand_off(session, shop_a, ids["conversation_id"], "accident")
        await send(shop_a)
        body = to_owner[0]
        assert "Waiting on you:" in body
        assert "accident" in body
        assert "Reply HELP" in body

    async def test_it_lists_work_ready_for_collection(self, shop_a, to_owner):
        ids = await busy_yesterday(shop_a)
        async with session_scope() as session:
            await upsert_job(
                session,
                shop_a,
                ids["customer_id"],
                "2014 Civic",
                JobStatus.ready,
                updated_by="owner",
            )
        await send(shop_a)
        assert "Ready for collection (1):" in to_owner[0]

    async def test_it_mentions_replies_that_could_not_be_delivered(
        self, shop_a, to_owner
    ):
        ids = await busy_yesterday(shop_a)
        async with session_scope() as session:
            message = await add_message(
                session,
                shop_a,
                ids["conversation_id"],
                direction=Direction.outbound,
                channel=Channel.whatsapp,
                body="never reached them",
                status=MessageStatus.blocked,
            )
            message.created_at = YESTERDAY_NOON
        await send(shop_a)
        assert "could not be delivered" in to_owner[0]

    async def test_today_with_nothing_booked_says_so(self, shop_a, to_owner):
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125557172"
            )
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.sms
            )
            message = await add_message(
                session,
                shop_a,
                conversation.id,
                direction=Direction.inbound,
                channel=Channel.sms,
                body="hello",
            )
            message.created_at = YESTERDAY_NOON
        await send(shop_a)
        assert "Nothing booked today." in to_owner[0]


class TestWhenItGoes:
    async def test_nothing_is_sent_before_the_configured_hour(
        self, shop_a, to_owner
    ):
        await busy_yesterday(shop_a)
        # 09:00 UTC is 3 AM in Chicago, before the 7 AM default.
        result = await send(shop_a, now=datetime(2026, 11, 11, 9, 0, tzinfo=UTC))
        assert result["reason"] == "too early in their day"
        assert to_owner == []

    async def test_a_later_hour_can_be_configured(self, shop_a, to_owner):
        await set_config(shop_a, summary_hour=11)
        await busy_yesterday(shop_a)
        assert (await send(shop_a))["reason"] == "too early in their day"
        later = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)  # noon in Chicago
        assert (await send(shop_a, now=later))["status"] == "sent"

    async def test_it_can_be_turned_off(self, shop_a, to_owner):
        await set_config(shop_a, summary_enabled=False)
        await busy_yesterday(shop_a)
        assert (await send(shop_a))["reason"] == "summary turned off"
        assert to_owner == []

    async def test_a_business_that_is_not_live_gets_none(self, shop_a, to_owner):
        async with session_scope() as session:
            await session.execute(
                update(Client)
                .where(Client.id == shop_a)
                .values(status=ClientStatus.paused)
            )
        invalidate(shop_a)
        await busy_yesterday(shop_a)
        assert "paused" in (await send(shop_a))["reason"]


class TestOnlyOnce:
    async def test_a_second_run_the_same_day_sends_nothing(self, shop_a, to_owner):
        await busy_yesterday(shop_a)
        assert (await send(shop_a))["status"] == "sent"
        assert (await send(shop_a))["reason"] == "already sent today"
        assert len(to_owner) == 1

    async def test_the_hourly_sweep_can_run_all_day_safely(self, shop_a, to_owner):
        await busy_yesterday(shop_a)
        for hour in range(13, 24):
            await send(shop_a, now=datetime(2026, 11, 11, hour, 5, tzinfo=UTC))
        assert len(to_owner) == 1

    async def test_the_next_day_sends_again(self, shop_a, to_owner):
        await busy_yesterday(shop_a)
        assert (await send(shop_a))["status"] == "sent"
        tomorrow = MORNING + timedelta(days=1)
        result = await send(shop_a, now=tomorrow)
        # Nothing happened on the 11th in this fixture, so the 12th has nothing
        # to report - but the day was still considered, not blocked.
        assert result["reason"] != "already sent today"

    async def test_the_marker_is_the_businesss_own_local_date(
        self, shop_a, to_owner
    ):
        await busy_yesterday(shop_a)
        await send(shop_a)
        async with session_scope() as session:
            marker = await last_summary_on(session, shop_a)
        assert marker.isoformat() == "2026-11-11"


class TestQuietDays:
    async def test_a_business_with_nothing_to_report_is_not_messaged(
        self, shop_a, to_owner
    ):
        """An empty summary every morning is how an owner learns to ignore us."""
        result = await send(shop_a)
        assert result["reason"] == "nothing happened"
        assert to_owner == []

    async def test_a_quiet_day_still_marks_itself_done(self, shop_a, to_owner):
        """Otherwise the hourly sweep rebuilds the same empty summary every
        hour until midnight."""
        await send(shop_a)
        async with session_scope() as session:
            assert await last_summary_on(session, shop_a) is not None

    async def test_waiting_work_alone_is_enough_to_send(self, shop_a, to_owner):
        """Nothing happened yesterday, but a customer is still waiting."""
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125557173"
            )
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.sms
            )
            await hand_off(session, shop_a, conversation.id, "angry customer")
        result = await send(shop_a)
        assert result["status"] == "sent"
        assert "Waiting on you:" in to_owner[0]


class TestTenantScope:
    async def test_one_businesss_summary_contains_only_its_own_work(
        self, shop_a, shop_b, to_owner
    ):
        await busy_yesterday(shop_a, phone="+13125557181")
        await busy_yesterday(shop_b, phone="+13125557182")
        await send(shop_a)
        body = to_owner[0]
        assert "Shop A" in body
        assert "Shop B" not in body
        # Shop A's single appointment, not both.
        assert "Today (1):" in body


async def send(client_id, *, now: datetime = MORNING) -> dict:
    async with session_scope() as session:
        return await summary.send_for_client(session, client_id, now=now)
