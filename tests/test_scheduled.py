"""Recalls and follow-ups: the rows, the sweep, and what stops them.

The interesting cases here are all about *not* sending. An unprompted message
months after the last one is the easiest thing in this product to get wrong -
it arrives when nobody is expecting it, to someone who may have opted out,
booked already, or simply moved on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.agent.tools import ToolContext
from app.core import scheduled
from app.core.router import load_shop
from app.db import session_scope
from app.models import Channel, ClientStatus, Customer, ScheduledMessage
from app.repo import (
    cancel_scheduled_for_customer,
    find_or_create_conversation,
    find_or_create_customer,
    schedule_message,
    scheduled_due,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
async def shop_ctx(shop_a):
    """A live shop with one customer and an open conversation."""
    async with session_scope() as session:
        customer = await find_or_create_customer(session, shop_a, "+34600555666")
        conversation = await find_or_create_conversation(
            session, shop_a, customer.id, Channel.simulator
        )
        await session.execute(
            update(Customer)
            .where(Customer.id == customer.id)
            .values(language="de")
        )
        ids = (customer.id, conversation.id)

    async with session_scope() as session:
        from app.models import Client

        await session.execute(
            update(Client).where(Client.id == shop_a).values(status=ClientStatus.live)
        )

    from app.core.router import invalidate

    invalidate(shop_a)
    return {"client_id": shop_a, "customer_id": ids[0], "conversation_id": ids[1]}


async def add(ctx, *, kind="recall", days=180, topic="your check-up"):
    async with session_scope() as session:
        row = await schedule_message(
            session,
            ctx["client_id"],
            customer_id=ctx["customer_id"],
            conversation_id=ctx["conversation_id"],
            kind=kind,
            topic=topic,
            due_at=NOW + timedelta(days=days),
        )
        return row.id


async def rows_in(session, client_id):
    result = await session.execute(
        select(ScheduledMessage).where(ScheduledMessage.client_id == client_id)
    )
    return list(result.scalars().all())


async def rows(client_id):
    async with session_scope() as session:
        return await rows_in(session, client_id)


async def sweep(client_id, *, now):
    async with session_scope() as session:
        return await scheduled.sweep_client(session, client_id, now=now)


class TestWhatIsDue:
    async def test_nothing_is_due_before_its_time(self, shop_ctx):
        await add(shop_ctx, days=180)
        async with session_scope() as session:
            due = await scheduled_due(session, shop_ctx["client_id"], now=NOW)
        assert due == []

    async def test_it_is_due_once_the_date_passes(self, shop_ctx):
        await add(shop_ctx, days=180)
        async with session_scope() as session:
            due = await scheduled_due(
                session, shop_ctx["client_id"], now=NOW + timedelta(days=181)
            )
        assert len(due) == 1

    async def test_a_late_message_is_still_sent_rather_than_dropped(self, shop_ctx):
        """A worker that was down for a week sends slightly late, not never -
        the self-healing property the whole sweep design exists for."""
        await add(shop_ctx, days=1)
        async with session_scope() as session:
            due = await scheduled_due(
                session, shop_ctx["client_id"], now=NOW + timedelta(days=90)
            )
        assert len(due) == 1

    async def test_another_shop_cannot_see_it(self, shop_ctx, shop_b):
        await add(shop_ctx, days=1)
        async with session_scope() as session:
            due = await scheduled_due(session, shop_b, now=NOW + timedelta(days=10))
        assert due == []


class TestTheSweepSends:
    async def test_it_sends_and_marks_the_attempt(self, shop_ctx):
        await add(shop_ctx)
        result = await sweep(shop_ctx["client_id"], now=NOW + timedelta(days=200))
        assert result["sent"] == 1
        assert [r.sent_at is not None for r in await rows(shop_ctx["client_id"])] == [
            True
        ]

    async def test_a_sent_message_is_never_sent_twice(self, shop_ctx):
        await add(shop_ctx)
        first = await sweep(shop_ctx["client_id"], now=NOW + timedelta(days=200))
        second = await sweep(shop_ctx["client_id"], now=NOW + timedelta(days=201))
        assert first["sent"] == 1
        assert second["considered"] == 0

    async def test_the_text_is_composed_from_a_template_not_stored(self, shop_ctx):
        """Nothing in the row is the message. Compose builds it at send time."""
        async with session_scope() as session:
            shop = await load_shop(session, shop_ctx["client_id"], use_cache=False)
        row = ScheduledMessage(kind="recall", topic="your check-up")
        text = scheduled.compose(shop, row)
        assert "your check-up" in text
        assert shop.name in text
        assert "STOP" in text

    async def test_an_unknown_kind_is_marked_and_never_sent(self, shop_ctx):
        await add(shop_ctx, kind="nonsense")
        result = await sweep(shop_ctx["client_id"], now=NOW + timedelta(days=200))
        assert result["sent"] == 0
        assert "unknown kind" in " ".join(result["reasons"])
        assert (await rows(shop_ctx["client_id"]))[0].sent_at is not None

    async def test_a_row_with_no_topic_still_reads_properly(self, shop_ctx):
        async with session_scope() as session:
            shop = await load_shop(session, shop_ctx["client_id"], use_cache=False)
        text = scheduled.compose(shop, ScheduledMessage(kind="recall", topic=None))
        assert "{" not in text and "}" not in text
        assert "your next appointment" in text


class TestWhatStopsIt:
    async def test_a_shop_that_is_not_live_sends_nothing(self, shop_ctx):
        from app.core.router import invalidate
        from app.models import Client

        await add(shop_ctx)
        async with session_scope() as session:
            await session.execute(
                update(Client)
                .where(Client.id == shop_ctx["client_id"])
                .values(status=ClientStatus.shadow)
            )
        invalidate(shop_ctx["client_id"])
        result = await sweep(shop_ctx["client_id"], now=NOW + timedelta(days=200))
        assert result["sent"] == 0
        assert "status shadow" in " ".join(result["reasons"])
        # and it stays pending rather than being burned
        assert (await rows(shop_ctx["client_id"]))[0].sent_at is None

    async def test_an_opted_out_customer_cancels_the_rest(self, shop_ctx):
        """One opt-out must not leave two more messages queued behind it."""
        await add(shop_ctx, days=180)
        await add(shop_ctx, days=200)
        async with session_scope() as session:
            await session.execute(
                update(Customer)
                .where(Customer.id == shop_ctx["customer_id"])
                .values(opted_out_at=NOW)
            )
        result = await sweep(shop_ctx["client_id"], now=NOW + timedelta(days=201))
        assert result["sent"] == 0
        assert "opted out" in " ".join(result["reasons"])
        for row in await rows(shop_ctx["client_id"]):
            assert row.sent_at is not None or row.cancelled_at is not None

    async def test_cancelling_takes_it_out_of_the_sweep(self, shop_ctx):
        await add(shop_ctx)
        async with session_scope() as session:
            count = await cancel_scheduled_for_customer(
                session,
                shop_ctx["client_id"],
                shop_ctx["customer_id"],
                reason="booked",
                when=NOW,
            )
        assert count == 1
        result = await sweep(shop_ctx["client_id"], now=NOW + timedelta(days=200))
        assert result["considered"] == 0

    async def test_cancelling_can_be_limited_to_one_kind(self, shop_ctx):
        await add(shop_ctx, kind="recall")
        await add(shop_ctx, kind="follow_up")
        async with session_scope() as session:
            count = await cancel_scheduled_for_customer(
                session,
                shop_ctx["client_id"],
                shop_ctx["customer_id"],
                reason="booked",
                when=NOW,
                kind="follow_up",
            )
        assert count == 1

    async def test_deleting_the_customer_takes_their_messages_with_them(
        self, shop_ctx
    ):
        """`customer_id` is NOT NULL with ON DELETE CASCADE, so a pending
        recall cannot outlive the person it was for. That matters beyond
        tidiness: an erasure request must not leave a message queued that
        arrives six months later."""
        await add(shop_ctx)
        async with session_scope() as session:
            await session.execute(
                delete(Customer).where(Customer.id == shop_ctx["customer_id"])
            )
        assert await rows(shop_ctx["client_id"]) == []


class TestTheTools:
    @pytest.fixture
    async def tool_ctx(self, shop_ctx):
        async with session_scope() as session:
            shop = await load_shop(session, shop_ctx["client_id"], use_cache=False)
            shop.config["vertical"] = "dental"
            yield ToolContext(
                session=session,
                shop=shop,
                conversation_id=shop_ctx["conversation_id"],
                customer_id=shop_ctx["customer_id"],
                now=NOW,
            )

    async def test_a_recall_writes_one_row_at_the_right_date(self, tool_ctx):
        from app.agent import tools

        out = await tools._recall_reminder(tool_ctx, "6", "your check-up")
        assert "6 months" in out
        written = await rows_in(tool_ctx.session, tool_ctx.shop.client_id)
        assert len(written) == 1
        assert written[0].kind == "recall"
        assert written[0].due_at.date() == (NOW + timedelta(days=180)).date()

    @pytest.mark.parametrize("months", ["0", "1", "60", "", "soon"])
    async def test_an_implausible_interval_schedules_nothing(
        self, tool_ctx, months
    ):
        from app.agent import tools

        out = await tools._recall_reminder(tool_ctx, months, "your check-up")
        assert "not a sensible interval" in out
        assert await rows_in(tool_ctx.session, tool_ctx.shop.client_id) == []

    async def test_a_follow_up_writes_the_whole_sequence(self, tool_ctx):
        from app.agent import tools

        await tools._follow_up_sequence(tool_ctx, "the villa in Marbella")
        written = sorted(
            await rows_in(tool_ctx.session, tool_ctx.shop.client_id), key=lambda r: r.due_at
        )
        assert [r.kind for r in written] == ["follow_up"] * 3
        assert [(r.due_at - NOW).days for r in written] == [3, 10, 30]

    async def test_it_refuses_to_pile_up_sequences(self, tool_ctx):
        """Five conversations must not leave fifteen messages queued."""
        from app.agent import tools

        await tools._follow_up_sequence(tool_ctx, "a villa")
        await tools._follow_up_sequence(tool_ctx, "a villa")
        out = await tools._follow_up_sequence(tool_ctx, "a villa")
        assert "already has several messages scheduled" in out
        assert len(await rows_in(tool_ctx.session, tool_ctx.shop.client_id)) <= 6

    async def test_both_tools_tell_the_customer_how_to_stop(self, tool_ctx):
        from app.agent import tools

        recall = await tools._recall_reminder(tool_ctx, "6", "your check-up")
        follow = await tools._follow_up_sequence(tool_ctx, "a villa")
        assert "STOP" in recall
        assert "STOP" in follow
