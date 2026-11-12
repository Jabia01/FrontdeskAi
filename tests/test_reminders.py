"""Appointment reminders: what is due, what is skipped, and what is never twice.

The sweep is the part that can quietly go wrong - a reminder sent twice is worse
than one sent late, and a reminder for a cancelled appointment tells a customer
to turn up to nothing. Both are asserted here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core import reminders
from app.core.router import invalidate, load_shop
from app.db import session_scope
from app.models import (
    Appointment,
    Channel,
    Client,
    ClientConfig,
    ClientStatus,
    Direction,
    MessageStatus,
)
from app.repo import (
    add_message,
    find_or_create_conversation,
    find_or_create_customer,
    outbound_since,
    set_customer_opted_out,
    touch_window,
)

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_translation(monkeypatch):
    """The reminder text is composed here, not generated, so translation is the
    only Claude call in this path. Stub it and assert on the English."""

    async def fake(text, *, to_language, from_language="en"):
        if to_language and to_language != from_language:
            return f"[{to_language}] {text}", True
        return text, False

    monkeypatch.setattr("app.core.reminders.translate", fake)
    return fake


@pytest.fixture
def alerts(monkeypatch):
    captured: list[str] = []

    async def fake(client_id, body):
        captured.append(body)
        return "job"

    monkeypatch.setattr("app.queue.client.enqueue_owner_alert", fake)
    return captured


async def seed(
    client_id,
    *,
    start_at: datetime,
    status: str = "booked",
    phone: str = "+13125554141",
    language: str | None = "es",
    service: str = "Front brakes",
    item: str = "2014 Civic",
    with_conversation: bool = True,
    reminder_sent_at: datetime | None = None,
):
    async with session_scope() as session:
        customer = await find_or_create_customer(session, client_id, phone)
        customer.language = language
        conversation = None
        if with_conversation:
            conversation = await find_or_create_conversation(
                session, client_id, customer.id, Channel.sms
            )
            await add_message(
                session,
                client_id,
                conversation.id,
                direction=Direction.inbound,
                channel=Channel.sms,
                body="can I come in?",
            )
        appointment = Appointment(
            client_id=client_id,
            customer_id=customer.id,
            conversation_id=conversation.id if conversation else None,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=30),
            service=service,
            item=item,
            status=status,
            reminder_sent_at=reminder_sent_at,
        )
        session.add(appointment)
        await session.flush()
        return {
            "appointment_id": appointment.id,
            "customer_id": customer.id,
            "conversation_id": conversation.id if conversation else None,
        }


async def sweep(client_id, *, now: datetime = NOW) -> dict:
    async with session_scope() as session:
        return await reminders.sweep_client(session, client_id, now=now)


async def sent_bodies(client_id, conversation_id) -> list[str]:
    async with session_scope() as session:
        rows = await outbound_since(session, client_id, conversation_id)
    return [row.body for row in rows]


async def set_config(client_id, **values) -> None:
    async with session_scope() as session:
        await session.execute(
            update(ClientConfig)
            .where(ClientConfig.client_id == client_id)
            .values(**values)
        )
    invalidate(client_id)


class TestWhatIsDue:
    async def test_an_appointment_tomorrow_gets_a_reminder(self, shop_a, alerts):
        ids = await seed(shop_a, start_at=NOW + timedelta(hours=20))
        result = await sweep(shop_a)

        assert result["considered"] == 1
        assert result["sent"] == 1
        bodies = await sent_bodies(shop_a, ids["conversation_id"])
        assert len(bodies) == 1
        # Spanish, because that is the language on the customer row.
        assert bodies[0].startswith("[es] ")
        assert "Front brakes" in bodies[0]
        assert "Shop A" in bodies[0]

    async def test_the_reminder_carries_the_exact_agreed_time(self, shop_a, alerts):
        """Composed from the stored time, never generated - a model asked to
        write a reminder is a model given the chance to invent a time."""
        ids = await seed(shop_a, start_at=datetime(2026, 11, 12, 15, 0, tzinfo=UTC))
        await sweep(shop_a)
        body = (await sent_bodies(shop_a, ids["conversation_id"]))[0]
        # 15:00 UTC is 9:00 AM in Chicago.
        assert "Thursday 12 Nov, 9:00 AM" in body

    async def test_an_appointment_further_out_is_left_alone(self, shop_a, alerts):
        await seed(shop_a, start_at=NOW + timedelta(days=3))
        result = await sweep(shop_a)
        assert result["considered"] == 0
        assert result["sent"] == 0

    async def test_an_appointment_in_the_past_is_not_reminded(self, shop_a, alerts):
        await seed(shop_a, start_at=NOW - timedelta(hours=2))
        assert (await sweep(shop_a))["considered"] == 0

    async def test_a_window_boundary_is_included(self, shop_a, alerts):
        await seed(shop_a, start_at=NOW + timedelta(hours=24))
        assert (await sweep(shop_a))["sent"] == 1

    async def test_an_appointment_with_no_service_still_reads_properly(
        self, shop_a, alerts
    ):
        ids = await seed(shop_a, start_at=NOW + timedelta(hours=5), service="")
        await sweep(shop_a)
        body = (await sent_bodies(shop_a, ids["conversation_id"]))[0]
        assert "your appointment at Shop A" in body


class TestLanguage:
    async def test_it_translates_even_when_the_owner_is_not_english_speaking(
        self, shop_a, alerts, monkeypatch
    ):
        """The reminder is composed in English whatever language the business
        runs in. Treating the owner's language as the source made `translate`
        see source == target and send a Spanish customer English."""
        calls: list[dict] = []

        async def spy(text, *, to_language, from_language="en"):
            calls.append({"to": to_language, "from": from_language})
            return f"[{to_language}] {text}", True

        monkeypatch.setattr("app.core.reminders.translate", spy)
        await set_config(shop_a, owner_language="es", default_customer_language="es")

        ids = await seed(shop_a, start_at=NOW + timedelta(hours=10), language="es")
        assert (await sweep(shop_a))["sent"] == 1

        assert calls == [{"to": "es", "from": "en"}]
        body = (await sent_bodies(shop_a, ids["conversation_id"]))[0]
        assert body.startswith("[es] ")

    async def test_a_customer_with_no_language_gets_the_business_default(
        self, shop_a, alerts
    ):
        await set_config(shop_a, default_customer_language="pl")
        ids = await seed(shop_a, start_at=NOW + timedelta(hours=10), language=None)
        await sweep(shop_a)
        body = (await sent_bodies(shop_a, ids["conversation_id"]))[0]
        assert body.startswith("[pl] ")


class TestNeverTwice:
    async def test_a_second_sweep_sends_nothing(self, shop_a, alerts):
        ids = await seed(shop_a, start_at=NOW + timedelta(hours=10))
        assert (await sweep(shop_a))["sent"] == 1
        assert (await sweep(shop_a))["considered"] == 0
        assert len(await sent_bodies(shop_a, ids["conversation_id"])) == 1

    async def test_an_already_reminded_appointment_is_skipped(self, shop_a, alerts):
        await seed(
            shop_a,
            start_at=NOW + timedelta(hours=10),
            reminder_sent_at=NOW - timedelta(hours=1),
        )
        assert (await sweep(shop_a))["considered"] == 0

    async def test_the_marker_is_set_even_when_nothing_could_be_sent(
        self, shop_a, alerts
    ):
        """Otherwise the sweep retries every fifteen minutes, forever."""
        ids = await seed(
            shop_a, start_at=NOW + timedelta(hours=10), with_conversation=False
        )
        result = await sweep(shop_a)
        assert result["skipped"] == 1
        assert "no conversation to send on" in result["reasons"]

        async with session_scope() as session:
            appointment = (
                await session.execute(
                    select(Appointment).where(
                        Appointment.id == ids["appointment_id"]
                    )
                )
            ).scalar_one()
        assert appointment.reminder_sent_at is not None


class TestWhatIsSkipped:
    async def test_a_cancelled_appointment_is_never_reminded(self, shop_a, alerts):
        """Telling a customer to turn up to a cancelled appointment is worse
        than sending nothing."""
        await seed(
            shop_a, start_at=NOW + timedelta(hours=10), status="cancelled"
        )
        assert (await sweep(shop_a))["considered"] == 0

    async def test_one_the_owner_flagged_with_change_is_not_reminded(
        self, shop_a, alerts
    ):
        await seed(
            shop_a, start_at=NOW + timedelta(hours=10), status="needs_change"
        )
        assert (await sweep(shop_a))["considered"] == 0

    async def test_an_opted_out_customer_gets_nothing(self, shop_a, alerts):
        ids = await seed(shop_a, start_at=NOW + timedelta(hours=10))
        async with session_scope() as session:
            await set_customer_opted_out(
                session, shop_a, ids["customer_id"], True
            )
        result = await sweep(shop_a)
        assert result["sent"] == 0
        assert "customer opted out" in result["reasons"]
        assert await sent_bodies(shop_a, ids["conversation_id"]) == []

    async def test_reminders_can_be_turned_off_per_business(self, shop_a, alerts):
        await set_config(shop_a, reminder_hours_before=0)
        await seed(shop_a, start_at=NOW + timedelta(hours=10))
        result = await sweep(shop_a)
        assert result["sent"] == 0
        assert "reminders turned off for this business" in result["reasons"]

    async def test_a_shorter_window_is_respected(self, shop_a, alerts):
        await set_config(shop_a, reminder_hours_before=2)
        await seed(shop_a, start_at=NOW + timedelta(hours=10))
        assert (await sweep(shop_a))["considered"] == 0
        await seed(
            shop_a, start_at=NOW + timedelta(hours=1), phone="+13125554142"
        )
        assert (await sweep(shop_a))["sent"] == 1

    async def test_a_business_in_shadow_mode_sends_no_reminders(
        self, shop_a, alerts
    ):
        """Shadow mode means nothing reaches a customer unapproved, and a
        reminder is an unprompted message."""
        async with session_scope() as session:
            await session.execute(
                update(Client)
                .where(Client.id == shop_a)
                .values(status=ClientStatus.shadow)
            )
        invalidate(shop_a)
        await seed(shop_a, start_at=NOW + timedelta(hours=10))
        result = await sweep(shop_a)
        assert result["sent"] == 0
        assert "business is shadow, not live" in result["reasons"]

    async def test_the_global_kill_switch_stops_the_sweep(
        self, shop_a, alerts, monkeypatch
    ):
        from app.config import get_settings

        monkeypatch.setenv("AI_ENABLED", "false")
        get_settings.cache_clear()
        try:
            await seed(shop_a, start_at=NOW + timedelta(hours=10))
            result = await sweep(shop_a)
        finally:
            monkeypatch.delenv("AI_ENABLED", raising=False)
            get_settings.cache_clear()
        assert result["sent"] == 0
        assert "automation disabled globally" in result["reasons"]


class TestBlockedDelivery:
    async def test_a_closed_whatsapp_window_alerts_the_owner(self, shop_a, alerts):
        """No approved template yet, so the reminder cannot be delivered. The
        owner has to find out, or the customer is silently not reminded."""
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125554143"
            )
            customer.language = "en"
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.whatsapp
            )
            session.add(
                Appointment(
                    client_id=shop_a,
                    customer_id=customer.id,
                    conversation_id=conversation.id,
                    start_at=NOW + timedelta(hours=10),
                    service="Oil change",
                    status="booked",
                )
            )
            conversation_id = conversation.id

        result = await sweep(shop_a)
        assert result["blocked"] == 1
        assert any("could not send" in alert for alert in alerts)
        assert any("window has closed" in alert for alert in alerts)

        async with session_scope() as session:
            rows = await outbound_since(session, shop_a, conversation_id)
        assert [row.status for row in rows] == [MessageStatus.blocked]

    async def test_an_open_window_sends_normally(self, shop_a, alerts):
        # `touch_window` stamps the real clock, so this case has to be timed
        # against the real clock too rather than the fixed NOW.
        real_now = datetime.now(UTC)
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125554144"
            )
            customer.language = "en"
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.whatsapp
            )
            await touch_window(session, shop_a, conversation.id)
            session.add(
                Appointment(
                    client_id=shop_a,
                    customer_id=customer.id,
                    conversation_id=conversation.id,
                    start_at=real_now + timedelta(hours=10),
                    service="Oil change",
                    status="booked",
                )
            )
        result = await sweep(shop_a, now=real_now)
        assert result["sent"] == 1, result
        assert alerts == []


class TestTenantScope:
    async def test_a_sweep_only_touches_its_own_business(
        self, shop_a, shop_b, alerts
    ):
        a = await seed(shop_a, start_at=NOW + timedelta(hours=10))
        b = await seed(
            shop_b, start_at=NOW + timedelta(hours=10), phone="+13125554145"
        )
        assert (await sweep(shop_a))["sent"] == 1
        assert len(await sent_bodies(shop_a, a["conversation_id"])) == 1
        assert await sent_bodies(shop_b, b["conversation_id"]) == []


def test_the_sweep_is_registered_as_a_cron_job():
    """A perfect sweep that nothing calls sends no reminders."""
    from app.queue.worker import WorkerSettings

    names = {job.name for job in WorkerSettings.cron_jobs}
    assert "cron:sweep_reminders" in names
    assert "cron:sweep_summaries" in names


async def test_the_reminder_text_is_not_written_by_the_model(shop_a):
    """Guard on the design, not the wording: `compose` must be deterministic."""
    shop = None
    async with session_scope() as session:
        shop = await load_shop(session, shop_a)
    appointment = Appointment(
        client_id=shop_a,
        start_at=datetime(2026, 12, 24, 14, 30, tzinfo=UTC),
        service="Full groom",
    )
    first = reminders.compose(shop, appointment)
    second = reminders.compose(shop, appointment)
    assert first == second
    assert "8:30 AM" in first
