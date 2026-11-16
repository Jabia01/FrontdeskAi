"""The code and the summary actually reach the row.

`tests/test_handoff_codes.py` covers the taxonomy as a pure function. This file
covers the wiring, which is where the real bug lived: both columns shipped and
deployed in the Phase 1 migration, and for five commits nothing wrote to either
of them. A console that shows a conversation is with a human but cannot say why
is the thing a clinic notices first.

Four paths escalate, and they have almost nothing in common:

1. the model calls `handoff_to_human` and names a code itself;
2. the model sets `handoff: true` in its structured reply, with or without one;
3. the engine forces a handoff after two failed attempts to understand, with no
   model involvement at all;
4. the same thing on a live call, through `app.core.voice`.

Each is tested separately because each was written at a different time.
"""

from __future__ import annotations

import pytest

from app.core.handoff import HandoffCode
from app.db import session_scope
from app.models import ConversationStatus
from app.repo import get_conversation

# The engine's own fixtures and helpers, reused rather than copied. `alerts`
# and `stub_agent` are imported for pytest to collect, not to call: a second
# copy of either would be a second thing to keep in step with the engine.
# ruff: noqa: F811
#
# The engine's own fixtures and helpers, reused rather than copied: three
# other test files already keep their own `stub_agent`, and a fourth would be
# a fourth thing to keep in step with the engine.
#
# F811 is off for this file, not for a line. Pytest registers a fixture under
# the imported name, so it cannot be aliased away, and every test that then
# asks for `stub_agent` in its signature reads to ruff as a redefinition of
# it. The rule is right in general and wrong about this exact pattern.
from .test_engine import alerts, outcome, reply, send, stub_agent  # noqa: F401

pytestmark = pytest.mark.usefixtures("alerts")


async def _conversation(client_id):
    async with session_scope() as session:
        from sqlalchemy import select

        from app.models import Conversation

        row = (
            await session.execute(
                select(Conversation).where(Conversation.client_id == client_id)
            )
        ).scalars().one()
        return row


class TestTheToolPath:
    async def test_the_code_the_model_named_is_stored(self, shop_a, stub_agent):
        """The ordinary case: `handoff_to_human` ran, the handler classified
        the arguments, and `repo.hand_off` wrote them down."""
        stub_agent(
            outcome(
                reply("Someone will call you."),
                handoff_requested=True,
                handoff_reason="customer mentioned a lawyer",
            )
        )
        await send(shop_a, "my lawyer will be in touch")
        row = await _conversation(shop_a)
        assert row.status is ConversationStatus.human
        assert row.handoff_code == HandoffCode.LEGAL_OR_INSURANCE.value
        # The model's own words survive alongside the category. Both, not
        # either: the code is for counting and the reason is for reading.
        assert row.handoff_reason == "customer mentioned a lawyer"
        assert row.handoff_summary == "summary for the owner"


class TestTheStructuredReplyPath:
    async def test_a_bare_handoff_flag_is_still_categorised(
        self, shop_a, stub_agent
    ):
        """No tool call, so nothing named a code - the free-text reason is
        read instead, which is the only thing this path provides."""
        stub_agent(
            outcome(reply("I will get someone.", handoff=True), handoff_reason="")
        )
        await send(shop_a, "I want to speak to a human please")
        row = await _conversation(shop_a)
        assert row.status is ConversationStatus.human
        assert row.handoff_code == HandoffCode.OTHER.value

    async def test_a_code_in_the_reply_is_used(self, shop_a, stub_agent):
        from app.schemas import AgentReply

        answer = AgentReply(
            reply="I will get someone.",
            detected_language="en",
            language_confidence=0.9,
            handoff=True,
            handoff_reason="wants a refund",
            handoff_code="payment_or_refund",
        )
        stub_agent(outcome(answer))
        await send(shop_a, "I would like my money back")
        row = await _conversation(shop_a)
        assert row.handoff_code == HandoffCode.PAYMENT_OR_REFUND.value


class TestTheForcedPath:
    async def test_two_misunderstandings_are_recorded_as_not_understood(
        self, shop_a, stub_agent
    ):
        """No model opinion exists here at all - the engine decided. So the
        engine names the code rather than leaving `classify` to re-read the
        sentence the engine itself just wrote."""
        stub_agent(outcome(reply("Sorry?", understood=False)))
        await send(shop_a, "???", msg_id="m1")
        await send(shop_a, "?!?!", msg_id="m2")
        row = await _conversation(shop_a)
        assert row.status is ConversationStatus.human
        assert row.handoff_code == HandoffCode.NOT_UNDERSTOOD.value

    async def test_a_model_named_code_still_wins_over_the_forced_default(
        self, shop_a, stub_agent
    ):
        """Both can be true at once: the model failed to understand twice *and*
        called the tool. The specific answer beats the generic one."""
        stub_agent(
            outcome(
                reply("Sorry?", understood=False),
                handoff_requested=True,
                handoff_reason="customer is furious",
            )
        )
        await send(shop_a, "???", msg_id="f1")
        await send(shop_a, "?!?!", msg_id="f2")
        row = await _conversation(shop_a)
        assert row.handoff_code == HandoffCode.ANGRY.value


class TestTheNicheChangesTheAnswer:
    async def test_the_same_sentence_is_read_differently_by_a_clinic(
        self, db, stub_agent
    ):
        """The point of passing the niche into `classify` at all. "the tooth
        is bleeding" is a medical concern to a clinic; to a garage it is not
        a recognised phrase, and `other` is the honest answer."""
        from .conftest import make_shop

        clinic = await make_shop(
            "Clinica Dental", vertical="dental", job_noun="treatment", currency="€",
            sim_id="sim-clinic",
        )
        garage = await make_shop("Westside Auto", sim_id="sim-garage")

        for client_id, sim_id, expected in (
            (clinic, "sim-clinic", HandoffCode.MEDICAL_CONCERN.value),
            (garage, "sim-garage", HandoffCode.OTHER.value),
        ):
            stub_agent(
                outcome(
                    reply("Someone will call you."),
                    handoff_requested=True,
                    handoff_reason="the tooth is bleeding",
                )
            )
            await send(
                client_id,
                "my tooth is bleeding",
                sim_id=sim_id,
                phone="+34600111222",
                msg_id=f"n-{sim_id}",
            )
            row = await _conversation(client_id)
            assert row.handoff_code == expected, sim_id


class TestNothingWritesAnUnknownCode:
    async def test_a_junk_code_becomes_other_rather_than_reaching_the_column(
        self, shop_a
    ):
        """`repo.hand_off` normalises rather than trusting its caller, because
        it is the only place the value reaches the column. An unrecognised
        string there would skew every count built on it."""
        from app.core.ingest import ingest
        from app.models import Channel
        from app.repo import hand_off
        from app.schemas import InboundMessage

        result = await ingest(
            InboundMessage(
                channel=Channel.simulator,
                provider="local",
                external_id="sim-a",
                from_number="+13125554321",
                body="hello",
                provider_msg_id="junk-code-test",
            )
        )
        async with session_scope() as session:
            conversation = await get_conversation(
                session, shop_a, result.conversation_id
            )
            await hand_off(
                session,
                shop_a,
                conversation.id,
                "owner took over",
                code="not_a_real_code",
                summary="a note",
            )
        row = await _conversation(shop_a)
        assert row.handoff_code is None
        assert row.handoff_summary == "a note"

    async def test_a_second_handoff_does_not_blank_the_first_ones_record(
        self, shop_a, stub_agent
    ):
        """Two paths can pause the same conversation inside one turn: the tool
        call pauses it, then the engine sees a stale `status` and pauses it
        again. The second call must not clear what the first recorded."""
        stub_agent(
            outcome(
                reply("Someone will call."),
                handoff_requested=True,
                handoff_reason="asked for a person",
            )
        )
        await send(shop_a, "can I talk to someone", msg_id="dbl-1")
        from app.repo import hand_off

        async with session_scope() as session:
            row = await _conversation(shop_a)
            await hand_off(session, shop_a, row.id, "owner took over")
        row = await _conversation(shop_a)
        assert row.handoff_reason == "owner took over"
        assert row.handoff_code == HandoffCode.ASKED_FOR_HUMAN.value
        assert row.handoff_summary == "summary for the owner"
