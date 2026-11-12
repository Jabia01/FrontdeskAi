"""Conversation engine: who answers, when, and what gets recorded.

The Claude call is stubbed here on purpose - this file tests the decision layer.
`test_agent_fixtures.py` covers the agent's own contract.
"""

from __future__ import annotations

import pytest

from app.agent.runner import AgentOutcome, Usage
from app.core import engine
from app.core.ingest import ingest
from app.db import session_scope
from app.models import Channel, ConversationStatus, LanguageSource
from app.repo import (
    find_customer_by_phone,
    get_conversation,
    outbound_since,
)
from app.schemas import AgentReply, InboundMessage

PHONE = "+13125554321"


def reply(
    text="We close at 6pm.",
    *,
    language="en",
    confidence=0.95,
    handoff=False,
    understood=True,
) -> AgentReply:
    return AgentReply(
        reply=text,
        detected_language=language,
        language_confidence=confidence,
        handoff=handoff,
        handoff_reason="",
        understood=understood,
    )


def outcome(
    agent_reply: AgentReply | None = None,
    *,
    failed=False,
    error=None,
    handoff_requested=False,
    handoff_reason="",
    model="claude-haiku-5-5",
) -> AgentOutcome:
    return AgentOutcome(
        reply=agent_reply,
        model=model,
        usage=Usage(input_tokens=500, output_tokens=40, cost=0.0001),
        handoff_requested=handoff_requested,
        handoff_reason=handoff_reason,
        handoff_summary="summary for the owner",
        failed=failed,
        error=error,
    )


@pytest.fixture
def alerts(monkeypatch):
    """Capture owner alerts instead of enqueueing them.

    The engine composes the finished message now, so each entry is the text the
    owner would receive.
    """
    captured: list[str] = []

    async def fake(client_id, body):
        captured.append(body)
        return "job"

    monkeypatch.setattr("app.queue.client.enqueue_owner_alert", fake)
    return captured


@pytest.fixture
def stub_agent(monkeypatch):
    """Install a canned AgentOutcome; returns the recorded call kwargs."""
    calls: list[dict] = []

    def install(result: AgentOutcome):
        # Reset on each install so `calls == []` means "not called since".
        calls.clear()

        async def fake_run_agent(**kwargs):
            calls.append(kwargs)
            return result

        monkeypatch.setattr(engine, "run_agent", fake_run_agent)
        return calls

    return install


async def send(client_id, body, *, sim_id="sim-a", phone=PHONE, msg_id=None):
    """Ingest one simulator message and run the engine over it."""
    inbound = InboundMessage(
        channel=Channel.simulator,
        provider="local",
        external_id=sim_id,
        from_number=phone,
        body=body,
        provider_msg_id=msg_id or f"sim-{body[:12]}-{phone[-4:]}",
    )
    result = await ingest(inbound)
    assert result.message_id is not None
    return await engine.handle_inbound_message(client_id, result.message_id)


class TestControlKeywords:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("STOP", "stop"),
            ("stop", "stop"),
            ("Stop.", "stop"),
            ("unsubscribe", "stop"),
            ("CANCEL", "stop"),
            ("HELP", "help"),
            ("info", "help"),
            ("START", "start"),
            ("yes", "start"),
            ("", None),
            ("stop by tomorrow", None),  # multi-word is a real message
            ("what are your hours", None),
            ("help me with my brakes", None),
        ],
    )
    def test_classification(self, text, expected):
        assert engine.classify_control(text) == expected

    async def test_stop_opts_out_and_skips_the_ai(self, shop_a, alerts, stub_agent):
        calls = stub_agent(outcome(reply()))
        result = await send(shop_a, "STOP")

        assert result["status"] == "opted_out"
        assert calls == [], "the AI must never see a STOP message"

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
        assert customer.opted_out_at is not None

    async def test_stop_still_confirms_to_the_customer(
        self, shop_a, alerts, stub_agent
    ):
        stub_agent(outcome(reply()))
        await send(shop_a, "STOP")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
            conversation_id = await engine_conversation_id(
                session, shop_a, customer.id
            )
            rows = await outbound_since(session, shop_a, conversation_id)

        assert len(rows) == 1
        assert "unsubscrib" in rows[0].body.lower()

    async def test_help_replies_without_the_ai(self, shop_a, alerts, stub_agent):
        calls = stub_agent(outcome(reply()))
        result = await send(shop_a, "HELP")
        assert result["status"] == "help_sent"
        assert calls == []

    async def test_opted_out_customer_gets_no_ai_reply(
        self, shop_a, alerts, stub_agent
    ):
        stub_agent(outcome(reply()))
        await send(shop_a, "STOP")

        calls = stub_agent(outcome(reply()))
        result = await send(shop_a, "are you open?", msg_id="after-stop")
        assert result["status"] == "skipped"
        assert result["reason"] == "customer opted out"
        assert calls == []

    async def test_start_opts_back_in(self, shop_a, alerts, stub_agent):
        stub_agent(outcome(reply()))
        await send(shop_a, "STOP")
        stub_agent(outcome(reply()))
        await send(shop_a, "START", msg_id="restart")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
        assert customer.opted_out_at is None


async def engine_conversation_id(session, client_id, customer_id):
    from app.repo import latest_conversation

    conversation = await latest_conversation(
        session, client_id, customer_id, Channel.simulator
    )
    return conversation.id


class TestHumanTakeover:
    async def test_ai_stays_quiet_and_the_owner_is_told(
        self, shop_a, alerts, stub_agent
    ):
        stub_agent(outcome(reply()))
        await send(shop_a, "hello there")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
            conversation_id = await engine_conversation_id(
                session, shop_a, customer.id
            )
            from app.repo import hand_off

            await hand_off(session, shop_a, conversation_id, "owner took over")

        calls = stub_agent(outcome(reply()))
        result = await send(shop_a, "any update?", msg_id="during-human")

        assert result["status"] == "skipped"
        assert result["reason"] == "human has taken over"
        assert calls == []
        assert any("replied" in a for a in alerts)

    async def test_ai_disabled_globally(self, shop_a, alerts, stub_agent, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("AI_ENABLED", "false")
        get_settings.cache_clear()
        try:
            calls = stub_agent(outcome(reply()))
            result = await send(shop_a, "hello")
            assert result["status"] == "skipped"
            assert calls == []
        finally:
            get_settings.cache_clear()


class TestSuccessfulReply:
    async def test_reply_is_delivered_and_recorded(self, shop_a, alerts, stub_agent):
        stub_agent(outcome(reply("We close at 6pm today.")))
        result = await send(shop_a, "what time do you close?")

        assert result["status"] == "replied"
        assert result["detected_language"] == "en"

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
            conversation_id = await engine_conversation_id(
                session, shop_a, customer.id
            )
            rows = await outbound_since(session, shop_a, conversation_id)
        assert [r.body for r in rows] == ["We close at 6pm today."]

    async def test_confident_detection_updates_the_stored_language(
        self, shop_a, alerts, stub_agent
    ):
        stub_agent(outcome(reply("Cerramos a las 6.", language="es", confidence=0.97)))
        await send(shop_a, "a que hora cierran?")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
        assert customer.language == "es"
        assert customer.language_source == LanguageSource.detected

    async def test_weak_detection_does_not_overwrite(
        self, shop_a, alerts, stub_agent
    ):
        """'ok' must not flip a Spanish customer to English."""
        stub_agent(outcome(reply("Perfecto.", language="es", confidence=0.98)))
        await send(shop_a, "hola, cerrado?")

        stub_agent(outcome(reply("Great.", language="en", confidence=0.2)))
        await send(shop_a, "ok", msg_id="neutral-1")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
        assert customer.language == "es"

    async def test_usage_is_recorded_for_margin_tracking(
        self, shop_a, alerts, stub_agent
    ):
        from sqlalchemy import select

        from app.models import UsageEvent

        stub_agent(outcome(reply()))
        await send(shop_a, "hours?")

        async with session_scope() as session:
            rows = (
                (await session.execute(select(UsageEvent))).scalars().all()
            )
        kinds = {r.type for r in rows}
        assert "claude" in kinds
        assert "message_out" in kinds
        claude = next(r for r in rows if r.type == "claude")
        assert claude.input_tokens == 500
        assert claude.model == "claude-haiku-5-5"
        assert all(r.client_id == shop_a for r in rows)


class TestHandoff:
    async def test_tool_handoff_alerts_the_owner(self, shop_a, alerts, stub_agent):
        stub_agent(
            outcome(
                reply("Someone from the shop will call you."),
                handoff_requested=True,
                handoff_reason="accident",
            )
        )
        result = await send(shop_a, "I was in an accident")

        assert result["handoff"] is True
        assert any("accident" in a for a in alerts)

    async def test_two_misunderstandings_force_a_handoff(
        self, shop_a, alerts, stub_agent
    ):
        stub_agent(outcome(reply("Sorry, could you say that again?", understood=False)))
        first = await send(shop_a, "asdfgh", msg_id="mis-1")
        assert first["handoff"] is False

        stub_agent(outcome(reply("Sorry, I still did not get that.", understood=False)))
        second = await send(shop_a, "qwerty", msg_id="mis-2")

        assert second["handoff"] is True
        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
            conversation_id = await engine_conversation_id(
                session, shop_a, customer.id
            )
            conversation = await get_conversation(session, shop_a, conversation_id)
        assert conversation.status == ConversationStatus.human
        assert any("understand" in a for a in alerts)

    async def test_understanding_resets_the_counter(
        self, shop_a, alerts, stub_agent
    ):
        stub_agent(outcome(reply("Pardon?", understood=False)))
        await send(shop_a, "asdfgh", msg_id="r-1")

        stub_agent(outcome(reply("We close at 6.", understood=True)))
        await send(shop_a, "closing time?", msg_id="r-2")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
            conversation_id = await engine_conversation_id(
                session, shop_a, customer.id
            )
            conversation = await get_conversation(session, shop_a, conversation_id)
        assert conversation.misunderstand_count == 0
        assert conversation.status == ConversationStatus.ai


class TestFailureIsNeverSilence:
    async def test_holding_reply_and_owner_alert(self, shop_a, alerts, stub_agent):
        stub_agent(outcome(None, failed=True, error="RateLimitError: slow down"))
        result = await send(shop_a, "are you open on sunday?")

        assert result["status"] == "holding_reply"

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
            conversation_id = await engine_conversation_id(
                session, shop_a, customer.id
            )
            rows = await outbound_since(session, shop_a, conversation_id)
            conversation = await get_conversation(session, shop_a, conversation_id)

        assert len(rows) == 1, "the customer must still get something"
        assert "trouble answering" in rows[0].body
        assert conversation.ai_failure_count == 1
        assert any("could not answer" in a for a in alerts)

    async def test_holding_reply_uses_the_stored_language(
        self, shop_a, alerts, stub_agent
    ):
        stub_agent(outcome(reply("Hola.", language="es", confidence=0.98)))
        await send(shop_a, "hola")

        stub_agent(outcome(None, failed=True, error="boom"))
        await send(shop_a, "y los frenos?", msg_id="fail-es")

        async with session_scope() as session:
            customer = await find_customer_by_phone(session, shop_a, PHONE)
            conversation_id = await engine_conversation_id(
                session, shop_a, customer.id
            )
            rows = await outbound_since(session, shop_a, conversation_id)
        assert "Gracias por su mensaje" in rows[-1].body


def test_holding_text_falls_back_to_english():
    assert "trouble answering" in engine.holding_text("pl")
    assert "Gracias" in engine.holding_text("es")
    assert "trouble answering" in engine.holding_text(None)
