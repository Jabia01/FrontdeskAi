"""Shadow mode, owner routing, and the owner alerts the engine fires.

Shadow mode is the two-week onboarding step from the architecture doc: the bot
drafts, the owner approves, then the shop goes live. Nothing may reach a
customer without the owner's OK while a shop is in shadow.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.agent.runner import AgentOutcome, Usage
from app.core import engine
from app.core.ingest import ingest
from app.db import session_scope
from app.models import (
    Channel,
    Client,
    ClientStatus,
    ConversationStatus,
    MessageStatus,
)
from app.repo import (
    find_customer_by_phone,
    get_conversation,
    latest_conversation,
    outbound_since,
)
from app.schemas import AgentReply, InboundMessage

PHONE = "+13125566001"


def outcome(
    text="We close at 6pm.",
    *,
    language="en",
    confidence=0.95,
    handoff=False,
    handoff_reason="",
    understood=True,
    booking_summary="",
) -> AgentOutcome:
    return AgentOutcome(
        reply=AgentReply(
            reply=text,
            detected_language=language,
            language_confidence=confidence,
            handoff=handoff,
            handoff_reason=handoff_reason,
            understood=understood,
        ),
        model="claude-haiku-5-5",
        usage=Usage(input_tokens=500, output_tokens=40, cost=0.0001),
        handoff_requested=handoff,
        handoff_reason=handoff_reason,
        handoff_summary="for the owner",
    )


@pytest.fixture
def alerts(monkeypatch):
    captured: list[str] = []

    async def fake(client_id, body):
        captured.append(body)
        return "job"

    monkeypatch.setattr("app.queue.client.enqueue_owner_alert", fake)
    return captured


@pytest.fixture
def stub_agent(monkeypatch):
    def install(result: AgentOutcome, *, booking_id=None, booking_summary=""):
        async def fake_run_agent(**kwargs):
            if booking_id is not None:
                kwargs["tool_ctx"].booking_id = booking_id
                kwargs["tool_ctx"].booking_summary = booking_summary
            return result

        monkeypatch.setattr(engine, "run_agent", fake_run_agent)

    return install


async def set_status(client_id, status: ClientStatus) -> None:
    async with session_scope() as session:
        await session.execute(
            update(Client).where(Client.id == client_id).values(status=status)
        )
    from app.core.router import invalidate

    invalidate(client_id)


async def send(client_id, body, *, phone=PHONE, sim_id="sim-a", msg_id=None):
    inbound = InboundMessage(
        channel=Channel.simulator,
        provider="local",
        external_id=sim_id,
        from_number=phone,
        body=body,
        provider_msg_id=msg_id or f"sm-{body[:14]}-{phone[-4:]}",
    )
    result = await ingest(inbound)
    assert result.message_id is not None
    return await engine.handle_inbound_message(client_id, result.message_id)


async def conversation_for(client_id, phone=PHONE):
    async with session_scope() as session:
        customer = await find_customer_by_phone(session, client_id, phone)
        return await latest_conversation(
            session, client_id, customer.id, Channel.simulator
        )


class TestShadowMode:
    async def test_nothing_reaches_the_customer(self, shop_a, alerts, stub_agent):
        await set_status(shop_a, ClientStatus.shadow)
        stub_agent(outcome("We close at 6pm."))

        result = await send(shop_a, "what time do you close?")
        assert result["status"] == "drafted"

        conversation = await conversation_for(shop_a)
        async with session_scope() as session:
            rows = await outbound_since(session, shop_a, conversation.id)

        assert len(rows) == 1
        assert rows[0].status == MessageStatus.draft
        assert not [r for r in rows if r.status == MessageStatus.sent]

    async def test_the_owner_gets_the_draft_to_approve(
        self, shop_a, alerts, stub_agent
    ):
        await set_status(shop_a, ClientStatus.shadow)
        stub_agent(outcome("We close at 6pm."))
        await send(shop_a, "what time do you close?")

        conversation = await conversation_for(shop_a)
        assert len(alerts) == 1
        assert conversation.short_code in alerts[0]
        assert "Shadow mode" in alerts[0]
        assert "We close at 6pm." in alerts[0]
        assert "what time do you close?" in alerts[0]
        assert "OK" in alerts[0]

    async def test_a_draft_is_not_replayed_as_history(
        self, shop_a, alerts, stub_agent
    ):
        """An unapproved draft must not look like something we already said."""
        from app.repo import recent_messages

        await set_status(shop_a, ClientStatus.shadow)
        stub_agent(outcome("Draft one."))
        await send(shop_a, "hello")

        conversation = await conversation_for(shop_a)
        async with session_scope() as session:
            history = await recent_messages(session, shop_a, conversation.id)
        assert [m.body for m in history] == ["hello"]

    async def test_language_is_still_learned_in_shadow_mode(
        self, shop_a, alerts, stub_agent
    ):
        await set_status(shop_a, ClientStatus.shadow)
        stub_agent(outcome("Cerramos a las 6.", language="es", confidence=0.97))
        await send(shop_a, "a que hora cierran?")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
        assert customer.language == "es"

    async def test_going_live_sends_normally(self, shop_a, alerts, stub_agent):
        await set_status(shop_a, ClientStatus.live)
        stub_agent(outcome("We close at 6pm."))

        result = await send(shop_a, "what time do you close?")
        assert result["status"] == "replied"

        conversation = await conversation_for(shop_a)
        async with session_scope() as session:
            rows = await outbound_since(session, shop_a, conversation.id)
        assert [r.status for r in rows] == [MessageStatus.sent]


class TestOwnerRoutingInTheEngine:
    async def test_the_owners_message_is_a_command_not_a_conversation(
        self, shop_a, alerts, monkeypatch
    ):
        """The owner texting their own number must never hit the front-desk agent."""
        agent_calls: list = []

        async def fake_run_agent(**kwargs):
            agent_calls.append(kwargs)
            return outcome()

        monkeypatch.setattr(engine, "run_agent", fake_run_agent)

        handled: list[str] = []

        async def fake_handle(session, shop, text):
            handled.append(text)
            return {"status": "help", "reply_to_owner": "command list"}

        monkeypatch.setattr(engine.owner_commands, "handle", fake_handle)

        sent_to_owner: list[str] = []

        async def fake_send(session, shop, body):
            sent_to_owner.append(body)
            return True, "sms"

        monkeypatch.setattr(engine.owner_mod, "send_to_owner", fake_send)

        result = await send(shop_a, "HELP", phone="+13125550999")

        assert result["status"] == "owner_command"
        assert handled == ["HELP"]
        assert sent_to_owner == ["command list"]
        assert agent_calls == [], "the agent must not see an owner message"

    async def test_the_owner_is_flagged_on_the_customer_row(
        self, shop_a, alerts, monkeypatch
    ):
        async def fake_handle(session, shop, text):
            return {"status": "help", "reply_to_owner": ""}

        monkeypatch.setattr(engine.owner_commands, "handle", fake_handle)

        await send(shop_a, "HELP", phone="+13125550999")

        async with session_scope() as session:
            owner = await find_customer_by_phone(
                session, shop_a, "+13125550999"
            )
        assert owner.is_owner is True


class TestEngineAlerts:
    async def test_a_booking_alerts_the_owner(self, shop_a, alerts, stub_agent):
        import uuid

        await set_status(shop_a, ClientStatus.live)
        stub_agent(
            outcome("You're booked for Thursday at 9."),
            booking_id=uuid.uuid4(),
            booking_summary="Maria, 2014 Civic, brakes, Thursday 12 Nov, 9:00 AM",
        )
        result = await send(shop_a, "can I book for thursday?")

        assert result["booked"] is True
        assert any("New booking" in a for a in alerts)
        assert any("Maria, 2014 Civic" in a for a in alerts)
        assert any("OK" in a for a in alerts)

    async def test_a_handoff_alert_carries_the_code_and_the_summary(
        self, shop_a, alerts, stub_agent
    ):
        await set_status(shop_a, ClientStatus.live)
        stub_agent(
            outcome(
                "Someone will call you.", handoff=True, handoff_reason="accident"
            )
        )
        await send(shop_a, "I was in an accident")

        conversation = await conversation_for(shop_a)
        assert any(conversation.short_code in a for a in alerts)
        assert any("accident" in a for a in alerts)
        assert any("RESUME" in a for a in alerts)

    async def test_every_conversation_gets_a_short_code(self, shop_a, alerts, stub_agent):
        await set_status(shop_a, ClientStatus.live)
        stub_agent(outcome())
        await send(shop_a, "hello")

        conversation = await conversation_for(shop_a)
        assert conversation.short_code
        assert 4 <= len(conversation.short_code) <= 8

    async def test_short_codes_are_unique_within_a_shop(self, shop_a):
        from app.models import Conversation
        from app.repo import find_or_create_conversation, find_or_create_customer

        async with session_scope() as session:
            for i in range(25):
                customer = await find_or_create_customer(
                    session, shop_a, f"+1312557{i:04d}"
                )
                await find_or_create_conversation(
                    session, shop_a, customer.id, Channel.sms
                )

        async with session_scope() as session:
            codes = (
                (
                    await session.execute(
                        select(Conversation.short_code).where(
                            Conversation.client_id == shop_a
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(codes) == len(set(codes))
        assert all(codes)

    async def test_a_handoff_stops_the_ai_from_replying_again(
        self, shop_a, alerts, stub_agent
    ):
        await set_status(shop_a, ClientStatus.live)
        stub_agent(
            outcome("Someone will call you.", handoff=True, handoff_reason="accident")
        )
        await send(shop_a, "I was in an accident")

        conversation = await conversation_for(shop_a)
        async with session_scope() as session:
            refreshed = await get_conversation(session, shop_a, conversation.id)
        assert refreshed.status == ConversationStatus.human

        stub_agent(outcome("should not be sent"))
        result = await send(shop_a, "any update?", msg_id="after-handoff")
        assert result["status"] == "skipped"
        assert result["reason"] == "human has taken over"
