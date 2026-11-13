"""Voice: Retell answers the phone, Chichi does the thinking.

The model call itself is stubbed throughout. What is being tested is everything
around it - which tenant a call belongs to, which calls we refuse, what a caller
hears when something breaks, and whether the call survives as a record - because
that is where a voice integration actually goes wrong.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import func, select

from app.agent.runner import AgentOutcome, Usage
from app.channels.base import get_adapter
from app.core import voice
from app.db import session_scope
from app.gateway import retell as retell_gateway
from app.gateway.retell_signature import sign
from app.integrations import retell as retell_api
from app.models import Call, CallOutcome, Channel, Client, ClientStatus, Message
from app.schemas import AgentReply, OutboundMessage
from tests.conftest import make_shop

KEY = "test-retell-api-key"
SECRET = "test-retell-ws-secret"
VOICE_NUMBER = "+13125557000"
CALLER = "+13125551234"


# --------------------------------------------------------------------- scaffolding


def agent_outcome(
    reply: str = "We open at eight. Would you like a slot tomorrow?",
    *,
    language: str = "en",
    confidence: float = 0.95,
    handoff: bool = False,
    reason: str = "",
    understood: bool = True,
    failed: bool = False,
) -> AgentOutcome:
    if failed:
        return AgentOutcome(
            reply=None, model="claude-haiku-5-5", failed=True, error="the model fell over"
        )
    return AgentOutcome(
        reply=AgentReply(
            reply=reply,
            detected_language=language,
            language_confidence=confidence,
            handoff=handoff,
            handoff_reason=reason,
            understood=understood,
        ),
        model="claude-haiku-5-5",
        usage=Usage(input_tokens=900, output_tokens=40),
        handoff_requested=handoff,
        handoff_reason=reason,
        handoff_summary=reason,
    )


@pytest.fixture
def stub_agent(monkeypatch):
    """Replaces the model call and records what it was asked."""
    calls: list[dict] = []
    box: dict = {"outcome": agent_outcome()}

    async def fake(**kwargs):
        calls.append(kwargs)
        result = box["outcome"]
        return result(**kwargs) if callable(result) else result

    monkeypatch.setattr(voice, "run_agent", fake)
    return type("Stub", (), {"calls": calls, "box": box})()


@pytest.fixture
def owner_alerts(monkeypatch):
    sent: list[tuple] = []

    async def fake(client_id, body):
        sent.append((client_id, body))
        return "job-1"

    monkeypatch.setattr(voice, "enqueue_owner_alert", fake)
    return sent


@pytest.fixture
async def voice_shop(db) -> uuid.UUID:
    return await make_shop(
        "Voice Shop",
        enabled_languages=["en", "es"],
        sms_number="+13125557001",
        whatsapp_id="whatsapp:+13125557001",
        sim_id="sim-voice",
        voice_number=VOICE_NUMBER,
    )


def transcript(*turns: tuple[str, str]) -> list[dict]:
    """("user", "hi"), ("agent", "hello") -> Retell's transcript array."""
    return [{"role": role, "content": content} for role, content in turns]


async def post_webhook(client, path: str, body: dict):
    raw = json.dumps(body).encode()
    return await client.post(
        path,
        content=raw,
        headers={
            "X-Retell-Signature": sign(raw, KEY),
            "Content-Type": "application/json",
        },
    )


def inbound_body(call_id: str, *, to: str = VOICE_NUMBER, frm: str = CALLER) -> dict:
    return {
        "event": "call_inbound",
        "call_inbound": {"call_id": call_id, "to_number": to, "from_number": frm},
    }


class FakeSocket:
    """A websocket that plays a script.

    Standing in for Starlette's socket rather than running a real one keeps the
    test in this event loop with this database connection - and still exercises
    the real `retell_bridge`, including its cancellation path.
    """

    def __init__(self, incoming: list[dict]) -> None:
        self._incoming = list(incoming)
        self.sent: list[dict] = []
        self.accepted = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive_text(self) -> str:
        if not self._incoming:
            from starlette.websockets import WebSocketDisconnect

            raise WebSocketDisconnect(1000)
        # Yield, so a turn task created on the previous frame gets a chance to
        # run - which is what makes the interruption test meaningful.
        await asyncio.sleep(0)
        return json.dumps(self._incoming.pop(0))

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    def spoken(self) -> list[str]:
        return [
            event["content"]
            for event in self.sent
            if event.get("response_type") == "response"
        ]


async def count_messages(client_id: uuid.UUID) -> int:
    async with session_scope() as session:
        result = await session.execute(
            select(func.count()).select_from(Message).where(Message.client_id == client_id)
        )
        return int(result.scalar_one())


# -------------------------------------------------------------------- the adapter


class TestTheAdapter:
    def test_it_is_registered_for_the_voice_channel(self):
        assert get_adapter(Channel.voice).provider == "retell"

    def test_it_reads_the_number_dialled_the_caller_and_the_call_id(self):
        inbound = get_adapter(Channel.voice).parse_inbound(
            {"to_number": VOICE_NUMBER, "from_number": CALLER, "call_id": "call_abc"}
        )
        assert inbound.external_id == VOICE_NUMBER
        assert inbound.from_number == CALLER
        assert inbound.provider_msg_id == "call_abc"
        assert inbound.channel is Channel.voice
        # No speech has happened yet - the phone is still ringing.
        assert inbound.text == ""

    @pytest.mark.parametrize(
        "missing", ["to_number", "from_number", "call_id"]
    )
    def test_it_refuses_a_payload_missing_anything_it_needs(self, missing):
        payload = {
            "to_number": VOICE_NUMBER,
            "from_number": CALLER,
            "call_id": "call_abc",
        }
        payload[missing] = ""
        with pytest.raises(ValueError, match=missing):
            get_adapter(Channel.voice).parse_inbound(payload)

    async def test_it_refuses_to_send_out_of_band_and_says_why(self):
        """A spoken reply goes down the live socket. Anything that tries to
        *send* on voice is a misconfiguration, and it should show up as blocked
        rather than as quietly fine."""
        result = await get_adapter(Channel.voice).send(
            OutboundMessage(
                channel=Channel.voice,
                provider="retell",
                external_id=VOICE_NUMBER,
                to_number=CALLER,
                body="this cannot be delivered",
            )
        )
        assert result.ok is False
        assert result.blocked is True
        assert "live call" in (result.error or "")


# ------------------------------------------------------------ language and plumbing


class TestLanguageMapping:
    def test_our_iso_codes_become_retell_locales(self):
        assert retell_api.locales_for(["es", "pl"]) == ["es-419", "pl-PL", "en-US"]

    def test_english_is_always_included(self):
        """A caller in a language nobody enabled should still be understood
        rather than met with silence."""
        assert "en-US" in retell_api.locales_for(["pl"])

    def test_spanish_is_latin_american_not_castilian(self):
        """A regional variant is a real choice: es-ES transcribes and speaks
        like Madrid, and these shops' customers are not in Madrid."""
        assert retell_api.locales_for(["es"])[0] == "es-419"

    def test_duplicates_collapse_and_order_is_stable(self):
        assert retell_api.locales_for(["en", "en", "es"]) == ["en-US", "es-419"]

    def test_a_language_retell_cannot_do_is_dropped_rather_than_guessed(self):
        assert retell_api.locales_for(["xx"]) == ["en-US"]

    def test_every_language_chichi_knows_has_a_locale(self):
        """Otherwise a shop could enable a language the voice channel silently
        cannot hear."""
        from app.agent.language import LANGUAGE_NAMES

        missing = sorted(set(LANGUAGE_NAMES) - set(retell_api.RETELL_LOCALES))
        assert missing == []


class TestTheWebsocketUrl:
    def test_https_becomes_wss(self):
        assert retell_api.websocket_url("https://x.onrender.com", "s") == (
            "wss://x.onrender.com/ws/retell/s"
        )

    def test_http_becomes_ws(self):
        assert retell_api.websocket_url("http://localhost:8000", "s") == (
            "ws://localhost:8000/ws/retell/s"
        )

    def test_a_trailing_slash_does_not_double_up(self):
        assert retell_api.websocket_url("https://x.dev/", "s") == "wss://x.dev/ws/retell/s"

    def test_the_call_id_is_not_included_because_retell_appends_it(self):
        assert not retell_api.websocket_url("https://x.dev", "s").endswith("/")


class TestTheAgentPayload:
    def _payload(self, **kwargs):
        return retell_api.agent_payload(
            websocket="wss://x.dev/ws/retell/s",
            voice_id="some-voice",
            languages=["en", "es"],
            webhook_url="https://x.dev/webhooks/retell/events",
            **kwargs,
        )

    def test_it_asks_for_our_own_llm(self):
        engine = self._payload()["response_engine"]
        assert engine["type"] == "custom-llm"
        assert engine["llm_websocket_url"] == "wss://x.dev/ws/retell/s"

    def test_it_asks_for_accurate_transcription(self):
        """A misheard street name is a lost customer, and `fast` is the mode
        that mishears proper nouns."""
        assert self._payload()["stt_mode"] == "accurate"

    def test_backchannel_is_off(self):
        """Retell's "mm-hm"s are English, and Chichi takes calls in any
        language."""
        assert self._payload()["enable_backchannel"] is False

    def test_it_does_not_subscribe_to_per_turn_transcript_webhooks(self):
        """We already have every turn from the websocket; the extra event would
        be pure noise and cost."""
        assert "transcript_updated" not in self._payload()["webhook_events"]

    def test_the_call_length_cap_is_carried_through_in_milliseconds(self):
        assert self._payload(max_call_seconds=600)["max_call_duration_ms"] == 600_000

    def test_the_voicemail_action_is_spelled_the_way_the_api_accepts(self):
        """`hang_up` reads correctly and is rejected with a 400. Retell's
        allowed values are `hangup` and `bridge_transfer`, and this cost one
        failed agent creation to find out."""
        assert self._payload()["voicemail_option"] == {"action": {"type": "hangup"}}

    def test_no_voice_model_is_sent_when_none_is_chosen(self):
        """Sending null would clear a model set in Retell's dashboard every
        time the agent is re-pushed. Omitting it leaves Retell's own default
        for the voice."""
        assert "voice_model" not in self._payload()

    def test_a_chosen_voice_model_is_sent(self):
        """This is the field that decides whether a Polish caller is spoken to
        in Polish - the voice id alone carries no language at all."""
        payload = self._payload(voice_model="eleven_multilingual_v2")
        assert payload["voice_model"] == "eleven_multilingual_v2"

    def test_it_never_ships_the_prompt_or_the_prices_to_retell(self):
        """The one thing that must have a single home. If any of this leaked
        into the agent config it would be a second place to keep it right."""
        rendered = json.dumps(self._payload())
        for leak in ("price", "hours", "tone", "faq", "system"):
            assert leak not in rendered.lower()


# ----------------------------------------------------------- the inbound webhook


class TestTheInboundWebhook:
    async def test_an_unsigned_request_is_refused(self, client, voice_shop):
        response = await client.post(
            "/webhooks/retell/inbound", json=inbound_body("call_1")
        )
        assert response.status_code == 403

    async def test_a_call_to_a_mapped_number_is_accepted(self, client, voice_shop):
        response = await post_webhook(
            client, "/webhooks/retell/inbound", inbound_body("call_1")
        )
        assert response.status_code == 200
        body = response.json()["call_inbound"]
        assert "reject" not in body
        assert body["metadata"]["client_id"] == str(voice_shop)
        assert body["dynamic_variables"]["shop_name"] == "Voice Shop"

    async def test_it_writes_the_call_row_the_websocket_will_authenticate_against(
        self, client, voice_shop
    ):
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body("call_1"))
        async with session_scope() as session:
            call = await voice.find_call(session, "call_1")
        assert call is not None
        assert call.client_id == voice_shop
        assert call.from_number == CALLER
        assert call.outcome is CallOutcome.answered_ai

    async def test_a_retried_webhook_does_not_create_a_second_call(
        self, client, voice_shop
    ):
        """Retell retries this up to three times, and two call rows for one
        call would double every number the owner sees."""
        for _ in range(3):
            response = await post_webhook(
                client, "/webhooks/retell/inbound", inbound_body("call_1")
            )
            assert response.status_code == 200
        async with session_scope() as session:
            result = await session.execute(
                select(func.count()).select_from(Call).where(Call.client_id == voice_shop)
            )
            assert int(result.scalar_one()) == 1

    async def test_a_call_to_an_unmapped_number_is_rejected(self, client, voice_shop):
        response = await post_webhook(
            client, "/webhooks/retell/inbound", inbound_body("call_1", to="+15555550000")
        )
        assert response.json() == {"call_inbound": {"reject": True}}

    async def test_a_rejection_carries_nothing_but_reject(self, client, voice_shop):
        """Retell validates sibling fields even on a rejection, so sending
        dynamic variables alongside it can turn a clean decline into an error."""
        response = await post_webhook(
            client, "/webhooks/retell/inbound", inbound_body("call_1", to="+15555550000")
        )
        assert list(response.json()["call_inbound"]) == ["reject"]

    @pytest.mark.parametrize(
        "status", [ClientStatus.shadow, ClientStatus.paused, ClientStatus.onboarding]
    )
    async def test_a_shop_that_is_not_live_has_its_calls_declined(
        self, client, voice_shop, status
    ):
        """Shadow mode means the owner approves each reply, and there is no
        approving anything inside a caller's patience. Declining sends them to
        the shop's own line instead of to an AI nobody signed off."""
        async with session_scope() as session:
            shop = await session.get(Client, voice_shop)
            shop.status = status
        from app.core.router import invalidate

        invalidate()

        response = await post_webhook(
            client, "/webhooks/retell/inbound", inbound_body("call_1")
        )
        assert response.json() == {"call_inbound": {"reject": True}}

    async def test_a_declined_call_leaves_no_call_row(self, client, voice_shop):
        await post_webhook(
            client, "/webhooks/retell/inbound", inbound_body("call_1", to="+15555550000")
        )
        async with session_scope() as session:
            assert await voice.find_call(session, "call_1") is None

    async def test_a_payload_missing_the_call_id_is_a_bad_request(
        self, client, voice_shop
    ):
        body = inbound_body("")
        response = await post_webhook(client, "/webhooks/retell/inbound", body)
        assert response.status_code == 400


# --------------------------------------------------------------- opening the call


class TestOpeningACall:
    async def test_a_registered_call_resolves_to_its_shop(self, client, voice_shop):
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body("call_1"))
        state = await voice.open_call("call_1")
        assert state.client_id == voice_shop
        assert state.customer_phone == CALLER
        assert state.shop.name == "Voice Shop"

    async def test_an_unregistered_call_id_is_refused(self, voice_shop):
        """The second half of the bridge's authentication: guessing a call id
        is not enough, because only a signed webhook writes the row."""
        with pytest.raises(voice.CallNotRegistered):
            await voice.open_call("call_never_registered")

    async def test_the_greeting_needs_no_model_call(self, client, voice_shop):
        """A second of dead air at pickup is the worst place to spend one - the
        caller does not yet know anyone is there."""
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body("call_1"))
        state = await voice.open_call("call_1")
        assert "Voice Shop" in voice.greeting(state.shop)

    async def test_the_greeting_is_in_the_shops_own_default_language(self, db):
        client_id = await make_shop(
            "Taller Hernandez",
            enabled_languages=["es", "en"],
            default_customer_language="es",
            sms_number="+13125557050",
            whatsapp_id="whatsapp:+13125557050",
            sim_id="sim-es",
            voice_number="+13125557051",
        )
        from app.core.router import load_shop

        async with session_scope() as session:
            shop = await load_shop(session, client_id)
        assert "Gracias por llamar" in voice.greeting(shop)

    async def test_a_language_with_no_canned_greeting_falls_back_to_english(self, db):
        client_id = await make_shop(
            "Thai Kitchen",
            enabled_languages=["th"],
            default_customer_language="th",
            sms_number="+13125557060",
            whatsapp_id="whatsapp:+13125557060",
            sim_id="sim-th",
            voice_number="+13125557061",
        )
        from app.core.router import load_shop

        async with session_scope() as session:
            shop = await load_shop(session, client_id)
        assert voice.greeting(shop) == "Thanks for calling Thai Kitchen. How can I help?"


# ------------------------------------------------------------------------ a turn


class TestATurn:
    async def _state(self, client) -> voice.CallState:
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body("call_1"))
        return await voice.open_call("call_1")

    async def test_the_caller_gets_the_agents_answer(
        self, client, voice_shop, stub_agent
    ):
        state = await self._state(client)
        reply = await voice.answer(state, transcript(("user", "what time do you open?")))
        assert reply.text == "We open at eight. Would you like a slot tomorrow?"
        assert reply.transfer_number is None

    async def test_the_agent_is_asked_on_the_voice_channel(
        self, client, voice_shop, stub_agent
    ):
        """Which is what selects the spoken style block - no lists, no URLs."""
        state = await self._state(client)
        await voice.answer(state, transcript(("user", "hello?")))
        assert stub_agent.calls[-1]["channel"] == "voice"

    async def test_history_comes_from_retells_transcript_not_from_our_rows(
        self, client, voice_shop, stub_agent
    ):
        """Retell revises earlier utterances as recognition improves, so its
        transcript is the thing that knows what was actually said."""
        state = await self._state(client)
        await voice.answer(
            state,
            transcript(
                ("user", "how much for brakes?"),
                ("agent", "Around three hundred."),
                ("user", "ok book me in"),
            ),
        )
        history = stub_agent.calls[-1]["history"]
        assert [row.body for row in history] == [
            "how much for brakes?",
            "Around three hundred.",
            "ok book me in",
        ]
        assert stub_agent.calls[-1]["customer_text"] == "ok book me in"

    async def test_a_turn_writes_no_message_rows(
        self, client, voice_shop, stub_agent
    ):
        """The transcript is written once, at the end, from the corrected
        version. A half-written conversation is worse than one written late."""
        state = await self._state(client)
        await voice.answer(state, transcript(("user", "hello?")))
        assert await count_messages(voice_shop) == 0

    async def test_speech_that_came_through_empty_gets_a_nudge_not_a_model_call(
        self, client, voice_shop, stub_agent
    ):
        state = await self._state(client)
        reply = await voice.answer(state, transcript(("agent", "Hello?")))
        assert reply.text == "Are you still there?"
        assert stub_agent.calls == []

    async def test_a_confident_detection_is_remembered_for_the_next_turn(
        self, client, voice_shop, stub_agent
    ):
        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome(
            "Abrimos a las ocho.", language="es", confidence=0.97
        )
        await voice.answer(state, transcript(("user", "a que hora abren?")))
        assert state.language == "es"
        # And the canned furniture follows the caller, not the shop.
        assert voice.nudge(state.language) == "Sigue ahi?"

    async def test_the_claude_cost_of_every_turn_is_recorded(
        self, client, voice_shop, stub_agent
    ):
        """Voice is the expensive channel; a turn whose cost is not booked is a
        margin we cannot see."""
        from app.models import UsageEvent

        state = await self._state(client)
        await voice.answer(state, transcript(("user", "hello?")))
        async with session_scope() as session:
            result = await session.execute(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.client_id == voice_shop)
            )
            assert int(result.scalar_one()) == 1

    async def test_a_misunderstanding_is_counted_the_same_as_on_text(
        self, client, voice_shop, stub_agent
    ):
        from app.repo import get_conversation

        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome("Sorry?", understood=False)
        await voice.answer(state, transcript(("user", "mmblgh")))
        async with session_scope() as session:
            conversation = await get_conversation(
                session, voice_shop, state.conversation_id
            )
        assert conversation.misunderstand_count == 1


class TestWhenSomethingGoesWrong:
    async def _state(self, client) -> voice.CallState:
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body("call_1"))
        return await voice.open_call("call_1")

    async def test_a_failed_model_call_transfers_rather_than_apologising(
        self, client, voice_shop, stub_agent, owner_alerts
    ):
        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome(failed=True)
        reply = await voice.answer(state, transcript(("user", "hello?")))
        assert reply.transfer_number == "+13125550999"

    async def test_a_handoff_transfers_the_caller_to_the_owner(
        self, client, voice_shop, stub_agent, owner_alerts
    ):
        """On text a handoff means "a human will reply". On a live call we can
        do better than a promise - the human is one transfer away."""
        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome(
            "Let me get someone.", handoff=True, reason="asked about an accident"
        )
        reply = await voice.answer(state, transcript(("user", "I had an accident")))
        assert reply.transfer_number == "+13125550999"
        assert reply.handoff_reason == "asked about an accident"

    async def test_a_handoff_still_alerts_the_owner(
        self, client, voice_shop, stub_agent, owner_alerts
    ):
        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome(
            "One moment.", handoff=True, reason="asked about an accident"
        )
        await voice.answer(state, transcript(("user", "I had an accident")))
        assert len(owner_alerts) == 1
        body = owner_alerts[0][1]
        assert "live call" in body
        assert CALLER in body

    async def test_a_handoff_marks_the_conversation_as_a_humans(
        self, client, voice_shop, stub_agent, owner_alerts
    ):
        from app.models import ConversationStatus
        from app.repo import get_conversation

        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome(handoff=True, reason="refund")
        await voice.answer(state, transcript(("user", "I want a refund")))
        async with session_scope() as session:
            conversation = await get_conversation(
                session, voice_shop, state.conversation_id
            )
        assert conversation.status is ConversationStatus.human

    async def test_a_shop_with_no_owner_number_promises_a_callback(
        self, db, stub_agent, owner_alerts, client
    ):
        """And because that is a commitment rather than an apology, the owner
        alert is what makes it true."""
        await make_shop(
            "No Owner Phone",
            sms_number="+13125557070",
            whatsapp_id="whatsapp:+13125557070",
            sim_id="sim-noowner",
            voice_number="+13125557071",
            owner_phone="",
        )
        await post_webhook(
            client,
            "/webhooks/retell/inbound",
            inbound_body("call_2", to="+13125557071"),
        )
        state = await voice.open_call("call_2")
        stub_agent.box["outcome"] = agent_outcome(handoff=True, reason="refund")
        stub_agent.box["outcome"] = agent_outcome(
            "I will sort that out for you.", handoff=True, reason="refund"
        )
        reply = await voice.answer(state, transcript(("user", "I want a refund")))
        assert reply.transfer_number is None
        # The agent's own wording survives, and the promise is added to it -
        # a reply that merely sounds reassuring is not a commitment.
        assert reply.text == (
            "I will sort that out for you. "
            "I will have someone call you straight back on this number."
        )
        assert len(owner_alerts) == 1

    async def test_the_callback_promise_is_made_even_if_the_agent_said_nothing_apt(
        self, db, stub_agent, owner_alerts, client
    ):
        """The agent sets handoff=True but its words may not promise anything.
        The caller still has to be told what happens next."""
        await make_shop(
            "No Owner Phone",
            sms_number="+13125557080",
            whatsapp_id="whatsapp:+13125557080",
            sim_id="sim-noowner2",
            voice_number="+13125557081",
            owner_phone="",
        )
        await post_webhook(
            client,
            "/webhooks/retell/inbound",
            inbound_body("call_3", to="+13125557081"),
        )
        state = await voice.open_call("call_3")
        stub_agent.box["outcome"] = agent_outcome(failed=True)
        reply = await voice.answer(state, transcript(("user", "hello?")))
        assert "call you straight back" in reply.text

    async def test_a_failed_model_call_also_alerts_the_owner(
        self, client, voice_shop, stub_agent, owner_alerts
    ):
        """It transfers the caller, so the owner is about to answer a call they
        know nothing about. The alert is their context."""
        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome(failed=True)
        await voice.answer(state, transcript(("user", "hello?")))
        assert len(owner_alerts) == 1
        assert "could not answer" in owner_alerts[0][1]

    async def test_a_turn_that_raises_alerts_the_owner(
        self, client, voice_shop, monkeypatch, owner_alerts
    ):
        """The caller is told someone will ring back. Something has to make
        that true."""
        state = await self._state(client)

        async def explode(**kwargs):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(voice, "run_agent", explode)
        reply = await voice.trouble_reply(state)
        assert "trouble on my end" in reply.text
        assert len(owner_alerts) == 1

    async def test_one_call_never_buzzes_the_owner_twice(
        self, client, voice_shop, stub_agent, owner_alerts
    ):
        """An owner whose phone goes off four times for one caller stops
        reading the alerts, which costs more than the duplicates do."""
        state = await self._state(client)
        stub_agent.box["outcome"] = agent_outcome(handoff=True, reason="accident")
        await voice.answer(state, transcript(("user", "I had an accident")))
        await voice.answer(state, transcript(("user", "are you still there?")))
        assert len(owner_alerts) == 1

    async def test_a_caller_already_handed_to_a_human_is_transferred_immediately(
        self, client, voice_shop, stub_agent
    ):
        """They rang back mid-handoff. Chichi talking now would be arguing with
        its own shop."""
        from app.repo import hand_off

        state = await self._state(client)
        async with session_scope() as session:
            await hand_off(session, voice_shop, state.conversation_id, "owner took over")

        reply = await voice.answer(state, transcript(("user", "hello again")))
        assert reply.transfer_number == "+13125550999"
        assert stub_agent.calls == []


# --------------------------------------------------------------- the record of it


class TestWritingTheCallDown:
    async def _register(self, client) -> None:
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body("call_1"))

    async def test_the_transcript_becomes_messages(self, client, voice_shop):
        await self._register(client)
        written = await voice.persist_transcript(
            voice_shop,
            "call_1",
            transcript(
                ("user", "how much for an oil change?"),
                ("agent", "Sixty to eighty dollars."),
            ),
        )
        assert written == 2
        assert await count_messages(voice_shop) == 2

    async def test_both_body_and_transcript_are_filled(self, client, voice_shop):
        """`transcript` is the honest column for speech, and `body` is the one
        the dashboard and the daily summary actually read. A transcript nobody
        renders is a transcript nobody has."""
        await self._register(client)
        await voice.persist_transcript(
            voice_shop, "call_1", transcript(("user", "hello"))
        )
        async with session_scope() as session:
            result = await session.execute(
                select(Message).where(Message.client_id == voice_shop)
            )
            row = result.scalars().first()
        assert row.body == "hello"
        assert row.transcript == "hello"

    async def test_writing_it_twice_writes_it_once(self, client, voice_shop):
        """Both the socket closing and `call_ended` call this, and either may be
        last. A second record of one call would double everything the owner
        sees."""
        await self._register(client)
        turns = transcript(("user", "hello"), ("agent", "Hi there."))
        first = await voice.persist_transcript(voice_shop, "call_1", turns)
        second = await voice.persist_transcript(voice_shop, "call_1", turns)
        assert (first, second) == (2, 0)
        assert await count_messages(voice_shop) == 2

    async def test_a_longer_version_of_the_call_tops_the_record_up(
        self, client, voice_shop
    ):
        """The writer that gets there first is often the one with fewer turns -
        the socket closes before `call_ended` arrives with the full transcript.
        Locking the shorter version in used to cost the last thing Chichi said
        on every single call."""
        await self._register(client)
        short = transcript(("user", "hello"), ("agent", "Hi there."))
        full = transcript(
            ("user", "hello"),
            ("agent", "Hi there."),
            ("user", "do you do brakes?"),
            ("agent", "We do - tomorrow at nine?"),
        )
        assert await voice.persist_transcript(voice_shop, "call_1", short) == 2
        assert await voice.persist_transcript(voice_shop, "call_1", full) == 2
        assert await count_messages(voice_shop) == 4

    async def test_topping_up_does_not_reorder_or_duplicate(
        self, client, voice_shop
    ):
        await self._register(client)
        await voice.persist_transcript(
            voice_shop, "call_1", transcript(("user", "hello"))
        )
        await voice.persist_transcript(
            voice_shop,
            "call_1",
            transcript(("user", "hello"), ("agent", "Hi."), ("user", "bye")),
        )
        async with session_scope() as session:
            result = await session.execute(
                select(Message)
                .where(Message.client_id == voice_shop)
                .order_by(Message.seq)
            )
            rows = result.scalars().all()
        assert [r.body for r in rows] == ["hello", "Hi.", "bye"]
        assert [r.provider_msg_id for r in rows] == [
            "call_1:t0",
            "call_1:t1",
            "call_1:t2",
        ]

    async def test_the_calls_language_is_recorded_against_each_turn(
        self, client, voice_shop
    ):
        """Speech has no per-utterance detection to draw on, and the dashboard
        showing "Spanish" against a Spanish call beats it showing nothing."""
        await self._register(client)
        await voice.persist_transcript(
            voice_shop,
            "call_1",
            transcript(("user", "a que hora abren?")),
            language="es",
        )
        async with session_scope() as session:
            result = await session.execute(
                select(Message).where(Message.client_id == voice_shop)
            )
            row = result.scalars().first()
        assert row.detected_language == "es"

    async def test_an_empty_transcript_writes_nothing(self, client, voice_shop):
        await self._register(client)
        assert await voice.persist_transcript(voice_shop, "call_1", []) == 0

    async def test_roles_retell_adds_later_are_ignored_rather_than_stored(
        self, client, voice_shop
    ):
        """`transfer_target` is part of the call but not part of the dialogue
        Chichi is holding."""
        await self._register(client)
        written = await voice.persist_transcript(
            voice_shop,
            "call_1",
            transcript(("user", "hello"), ("transfer_target", "yeah?")),
        )
        assert written == 1


class TestTheEventsWebhook:
    async def _register(self, client) -> None:
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body("call_1"))

    def _ended(self, **overrides) -> dict:
        call = {
            "call_id": "call_1",
            "duration_ms": 94_000,
            "recording_url": "https://retell.example/rec/call_1.wav",
            "transcript_object": transcript(
                ("user", "do you do brakes?"), ("agent", "We do. Tomorrow at nine?")
            ),
            "call_analysis": {
                "call_summary": "Caller asked about brakes and booked for nine.",
            },
        }
        call.update(overrides)
        return {"event": "call_ended", "call": call}

    async def test_an_unsigned_event_is_refused(self, client, voice_shop):
        response = await client.post("/webhooks/retell/events", json=self._ended())
        assert response.status_code == 403

    async def test_the_call_row_is_completed(self, client, voice_shop):
        await self._register(client)
        response = await post_webhook(client, "/webhooks/retell/events", self._ended())
        assert response.status_code == 200
        async with session_scope() as session:
            call = await voice.find_call(session, "call_1")
        assert call.duration == 94
        assert call.recording_url.endswith("call_1.wav")
        assert "booked for nine" in call.summary
        assert call.outcome is CallOutcome.answered_ai

    async def test_the_transcript_is_persisted(self, client, voice_shop):
        await self._register(client)
        response = await post_webhook(client, "/webhooks/retell/events", self._ended())
        assert response.json()["turns_written"] == 2
        assert await count_messages(voice_shop) == 2

    async def test_a_call_where_nobody_spoke_is_recorded_as_missed(
        self, client, voice_shop
    ):
        """However it ended. An owner looking at the dashboard needs to know
        nobody got an answer."""
        await self._register(client)
        await post_webhook(
            client,
            "/webhooks/retell/events",
            self._ended(transcript_object=transcript(("agent", "Thanks for calling.")),
                        duration_ms=3_000),
        )
        async with session_scope() as session:
            call = await voice.find_call(session, "call_1")
        assert call.outcome is CallOutcome.missed

    async def test_a_missing_duration_does_not_blank_the_row(self, client, voice_shop):
        await self._register(client)
        body = self._ended()
        del body["call"]["duration_ms"]
        response = await post_webhook(client, "/webhooks/retell/events", body)
        assert response.status_code == 200

    async def test_an_event_for_a_call_we_never_registered_is_not_an_error(
        self, client, voice_shop
    ):
        """It means a number is pointed at our agent without our inbound
        webhook. Worth logging loudly, not worth a 500 and a Retell retry
        storm."""
        body = self._ended()
        body["call"]["call_id"] = "call_unknown"
        response = await post_webhook(client, "/webhooks/retell/events", body)
        assert response.status_code == 200
        assert response.json()["status"] == "unknown_call"

    async def test_call_started_is_acknowledged_and_does_nothing(
        self, client, voice_shop
    ):
        await self._register(client)
        response = await post_webhook(
            client, "/webhooks/retell/events", {"event": "call_started", "call": {"call_id": "call_1"}}
        )
        assert response.status_code == 200
        assert await count_messages(voice_shop) == 0

    async def test_an_unknown_event_is_ignored_rather_than_rejected(
        self, client, voice_shop
    ):
        await self._register(client)
        response = await post_webhook(
            client,
            "/webhooks/retell/events",
            {"event": "something_new", "call": {"call_id": "call_1"}},
        )
        assert response.json()["status"] == "ignored"

    async def test_an_event_with_no_call_id_is_a_bad_request(self, client, voice_shop):
        response = await post_webhook(
            client, "/webhooks/retell/events", {"event": "call_ended", "call": {}}
        )
        assert response.status_code == 400


# ------------------------------------------------------------------- the bridge


class TestTheBridge:
    async def _register(self, client, call_id: str = "call_1") -> None:
        await post_webhook(client, "/webhooks/retell/inbound", inbound_body(call_id))

    async def test_a_bad_secret_never_gets_an_open_socket(self, client, voice_shop):
        await self._register(client)
        ws = FakeSocket([])
        await retell_gateway.retell_bridge(ws, "wrong-secret", "call_1")
        assert ws.accepted is False
        assert ws.close_code == retell_gateway.WS_POLICY

    async def test_an_empty_configured_secret_refuses_everything(
        self, client, voice_shop, monkeypatch
    ):
        """The failure where an unset secret matches an empty path segment and
        quietly opens the bridge to the world."""
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "retell_ws_secret", "", raising=False)
        ws = FakeSocket([])
        await retell_gateway.retell_bridge(ws, "", "call_1")
        assert ws.accepted is False
        assert ws.close_code == retell_gateway.WS_POLICY

    async def test_an_unregistered_call_is_refused_even_with_the_right_secret(
        self, client, voice_shop
    ):
        ws = FakeSocket([])
        await retell_gateway.retell_bridge(ws, SECRET, "call_guessed")
        assert ws.accepted is False
        assert ws.close_code == retell_gateway.WS_POLICY

    async def test_it_greets_the_caller_before_anything_else(
        self, client, voice_shop, stub_agent
    ):
        await self._register(client)
        ws = FakeSocket([])
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        assert ws.accepted is True
        assert ws.sent[0]["response_type"] == "config"
        assert ws.sent[1] == {
            "response_type": "response",
            "response_id": 0,
            "content": "Thanks for calling Voice Shop. How can I help?",
            "content_complete": True,
        }

    async def test_it_asks_retell_to_keep_the_socket_alive(
        self, client, voice_shop, stub_agent
    ):
        await self._register(client)
        ws = FakeSocket([])
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        assert ws.sent[0]["config"]["auto_reconnect"] is True

    async def test_it_answers_a_keepalive(self, client, voice_shop, stub_agent):
        """auto_reconnect is only safe if we actually reply to the pings - an
        unanswered keepalive drops the call mid-sentence."""
        await self._register(client)
        ws = FakeSocket([{"interaction_type": "ping_pong", "timestamp": 1700}])
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        pongs = [e for e in ws.sent if e.get("response_type") == "ping_pong"]
        assert pongs == [{"response_type": "ping_pong", "timestamp": 1700}]

    async def test_it_speaks_the_agents_answer_against_the_right_response_id(
        self, client, voice_shop, stub_agent
    ):
        await self._register(client)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 3,
                    "transcript": transcript(("user", "what time do you open?")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        answer = [
            e
            for e in ws.sent
            if e.get("response_type") == "response" and e["response_id"] == 3
        ]
        assert len(answer) == 1
        assert answer[0]["content_complete"] is True
        assert "open at eight" in answer[0]["content"]

    async def test_a_silence_is_nudged_without_a_model_call(
        self, client, voice_shop, stub_agent
    ):
        await self._register(client)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "reminder_required",
                    "response_id": 2,
                    "transcript": transcript(("agent", "Hello?")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        assert "Are you still there?" in ws.spoken()
        assert stub_agent.calls == []

    async def test_an_update_only_frame_updates_the_transcript_but_says_nothing(
        self, client, voice_shop, stub_agent
    ):
        await self._register(client)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "update_only",
                    "transcript": transcript(("user", "hi there")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        # Only the greeting was spoken...
        assert len(ws.spoken()) == 1
        # ...but the frame still became the record of the call.
        assert await count_messages(voice_shop) == 1

    async def test_a_transfer_rides_along_with_the_spoken_turn(
        self, client, voice_shop, stub_agent, owner_alerts
    ):
        await self._register(client)
        stub_agent.box["outcome"] = agent_outcome(handoff=True, reason="accident")
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(("user", "I had an accident")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        transfers = [e for e in ws.sent if e.get("transfer_number")]
        assert transfers and transfers[0]["transfer_number"] == "+13125550999"

    async def test_a_quick_answer_is_not_padded_with_a_held_beat(
        self, client, voice_shop, stub_agent
    ):
        """A plain question answers in about a second. Prefixing every one of
        those with "one moment" would make the agent sound slower than it is."""
        await self._register(client)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(("user", "what time do you open?")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        assert not any("One moment" in line for line in ws.spoken())

    async def test_a_slow_turn_gets_a_held_beat_first(
        self, client, voice_shop, stub_agent, monkeypatch
    ):
        """Checking the calendar takes two model round trips. Several seconds
        of silence on a phone call reads as a dropped connection."""
        await self._register(client)
        monkeypatch.setattr(retell_gateway, "FILLER_AFTER_SECONDS", 0.05)

        async def slow(**kwargs):
            await asyncio.sleep(0.3)
            return agent_outcome("Nine, nine thirty, or ten?")

        monkeypatch.setattr(voice, "run_agent", slow)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(("user", "can I come in tomorrow?")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")

        chunks = [e for e in ws.sent if e.get("response_id") == 1]
        # The beat is an incomplete chunk against the same response id, so
        # Retell speaks it and then speaks the answer - one turn, not two.
        assert chunks[0]["content_complete"] is False
        assert "One moment" in chunks[0]["content"]
        assert chunks[-1]["content_complete"] is True
        assert "Nine, nine thirty" in chunks[-1]["content"]

    async def test_the_held_beat_is_in_the_callers_language(
        self, client, voice_shop, stub_agent, monkeypatch
    ):
        await self._register(client)
        monkeypatch.setattr(retell_gateway, "FILLER_AFTER_SECONDS", 0.05)

        async def slow(**kwargs):
            await asyncio.sleep(0.3)
            return agent_outcome("Abrimos a las ocho.", language="es")

        monkeypatch.setattr(voice, "run_agent", slow)
        state = await voice.open_call("call_1")
        state.language = "es"
        ws = FakeSocket([])
        # Drive the turn directly so the language is set before it runs.
        await retell_gateway._run_turn(
            ws, state, transcript(("user", "a que hora abren?")), 1
        )
        assert "Un momento" in ws.sent[0]["content"]

    async def test_the_held_beat_is_not_recorded_as_a_turn(
        self, client, voice_shop, stub_agent, monkeypatch
    ):
        """It is a held beat, not something Chichi said. In the owner's
        transcript it would read as a non-answer."""
        await self._register(client)
        monkeypatch.setattr(retell_gateway, "FILLER_AFTER_SECONDS", 0.05)

        async def slow(**kwargs):
            await asyncio.sleep(0.3)
            return agent_outcome("Nine or ten?")

        monkeypatch.setattr(voice, "run_agent", slow)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(("user", "can I come in tomorrow?")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        async with session_scope() as session:
            result = await session.execute(
                select(Message)
                .where(Message.client_id == voice_shop)
                .order_by(Message.seq)
            )
            bodies = [row.body for row in result.scalars().all()]
        assert not any("One moment" in body for body in bodies)
        assert bodies[-1] == "Nine or ten?"

    async def test_an_interrupted_turn_does_not_keep_talking(
        self, client, voice_shop, stub_agent, monkeypatch
    ):
        """The caller talked over us. Retell has already thrown away the answer
        it asked for, so speaking it would be speaking over the caller."""
        await self._register(client)

        async def slow(**kwargs):
            await asyncio.sleep(5)
            return agent_outcome("too late to matter")

        monkeypatch.setattr(voice, "run_agent", slow)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(("user", "what time do")),
                },
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": transcript(("user", "what time do you shut?")),
                },
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        assert not any("too late to matter" in line for line in ws.spoken())

    async def test_a_non_json_frame_is_ignored_rather_than_dropping_the_call(
        self, client, voice_shop, stub_agent, monkeypatch
    ):
        await self._register(client)

        ws = FakeSocket([{"interaction_type": "ping_pong", "timestamp": 1}])
        original = ws.receive_text
        state = {"first": True}

        async def flaky():
            if state["first"]:
                state["first"] = False
                return "}not json{"
            return await original()

        monkeypatch.setattr(ws, "receive_text", flaky)
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        assert any(e.get("response_type") == "ping_pong" for e in ws.sent)

    async def test_a_turn_that_raises_still_says_something_to_the_caller(
        self, client, voice_shop, monkeypatch
    ):
        """Silence on a phone call reads as a dropped line. Whatever broke, the
        caller has to hear a sentence."""
        await self._register(client)

        async def explode(**kwargs):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(voice, "run_agent", explode)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(("user", "hello?")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        assert any("trouble on my end" in line for line in ws.spoken())

    async def test_the_call_is_written_down_when_the_socket_closes(
        self, client, voice_shop, stub_agent
    ):
        """A dropped socket may be the last thing that happens to a call, so the
        record cannot wait for a webhook that might not come."""
        await self._register(client)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(
                        ("user", "do you do brakes?"), ("agent", "We do.")
                    ),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        # Two heard turns plus the one Chichi actually spoke. That last one is
        # in no frame Retell ever sends, because there is no frame after it.
        assert await count_messages(voice_shop) == 3

    async def test_the_last_thing_chichi_said_is_in_the_record(
        self, client, voice_shop, stub_agent
    ):
        """The turn an owner most wants to read, and the one that is easiest to
        lose - Retell includes our replies in the *next* transcript, and there
        is no next transcript after the last reply."""
        await self._register(client)
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": transcript(("user", "what time do you open?")),
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        async with session_scope() as session:
            result = await session.execute(
                select(Message)
                .where(Message.client_id == voice_shop)
                .order_by(Message.seq)
            )
            bodies = [row.body for row in result.scalars().all()]
        assert bodies[-1] == "We open at eight. Would you like a slot tomorrow?"

    async def test_a_later_call_ended_webhook_does_not_double_the_record(
        self, client, voice_shop, stub_agent
    ):
        await self._register(client)
        turns = transcript(("user", "do you do brakes?"), ("agent", "We do."))
        ws = FakeSocket(
            [
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": turns,
                }
            ]
        )
        await retell_gateway.retell_bridge(ws, SECRET, "call_1")
        before = await count_messages(voice_shop)
        await post_webhook(
            client,
            "/webhooks/retell/events",
            {
                "event": "call_ended",
                "call": {
                    "call_id": "call_1",
                    "duration_ms": 30_000,
                    "transcript_object": turns,
                },
            },
        )
        assert await count_messages(voice_shop) == before


# ------------------------------------------------------------------- isolation


class TestTenantIsolation:
    async def test_a_call_resolves_only_to_the_shop_that_owns_the_number(
        self, db, client
    ):
        first = await make_shop(
            "Shop One",
            sms_number="+13125557100",
            whatsapp_id="whatsapp:+13125557100",
            sim_id="sim-one",
            voice_number="+13125557101",
        )
        second = await make_shop(
            "Shop Two",
            sms_number="+13125557200",
            whatsapp_id="whatsapp:+13125557200",
            sim_id="sim-two",
            voice_number="+13125557201",
        )
        await post_webhook(
            client,
            "/webhooks/retell/inbound",
            inbound_body("call_one", to="+13125557101"),
        )
        await post_webhook(
            client,
            "/webhooks/retell/inbound",
            inbound_body("call_two", to="+13125557201"),
        )

        one = await voice.open_call("call_one")
        two = await voice.open_call("call_two")
        assert one.client_id == first
        assert two.client_id == second
        assert one.conversation_id != two.conversation_id

    async def test_the_same_caller_at_two_shops_stays_two_conversations(
        self, db, client
    ):
        """The caller's phone number is their identity, and it is the obvious
        way for one shop's history to leak into another's."""
        await make_shop(
            "Shop One",
            sms_number="+13125557100",
            whatsapp_id="whatsapp:+13125557100",
            sim_id="sim-one",
            voice_number="+13125557101",
        )
        await make_shop(
            "Shop Two",
            sms_number="+13125557200",
            whatsapp_id="whatsapp:+13125557200",
            sim_id="sim-two",
            voice_number="+13125557201",
        )
        await post_webhook(
            client,
            "/webhooks/retell/inbound",
            inbound_body("call_one", to="+13125557101", frm=CALLER),
        )
        await post_webhook(
            client,
            "/webhooks/retell/inbound",
            inbound_body("call_two", to="+13125557201", frm=CALLER),
        )
        one = await voice.open_call("call_one")
        two = await voice.open_call("call_two")
        assert one.customer_id != two.customer_id

    async def test_a_transcript_is_written_to_the_calls_own_shop(self, db, client):
        other = await make_shop(
            "Shop Two",
            sms_number="+13125557200",
            whatsapp_id="whatsapp:+13125557200",
            sim_id="sim-two",
            voice_number="+13125557201",
        )
        mine = await make_shop(
            "Shop One",
            sms_number="+13125557100",
            whatsapp_id="whatsapp:+13125557100",
            sim_id="sim-one",
            voice_number="+13125557101",
        )
        await post_webhook(
            client,
            "/webhooks/retell/inbound",
            inbound_body("call_one", to="+13125557101"),
        )
        await voice.persist_transcript(mine, "call_one", transcript(("user", "hello")))
        assert await count_messages(mine) == 1
        assert await count_messages(other) == 0
