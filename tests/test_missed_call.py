"""Missed-call text-back.

This is the feature the product is sold on - a missed call must become a text
conversation within seconds, or the job is lost.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.runner import AgentOutcome, Usage
from app.core import missed_call
from app.core.router import load_shop
from app.db import session_scope
from app.models import CallOutcome, Channel
from app.repo import (
    find_customer_by_phone,
    find_or_create_customer,
    get_call,
    latest_conversation,
    outbound_for_customer_since,
    set_customer_opted_out,
)
from app.schemas import AgentReply


class TestIsMissed:
    @pytest.mark.parametrize(
        "status,duration,expected",
        [
            ("no-answer", None, True),
            ("busy", None, True),
            ("failed", None, True),
            ("canceled", None, True),
            ("NO-ANSWER", None, True),
            # A "completed" call of zero seconds never connected.
            ("completed", "0", True),
            ("completed", "42", False),
            ("completed", None, True),
            ("in-progress", None, False),
            ("ringing", None, False),
            ("", None, False),
        ],
    )
    def test_cases(self, status, duration, expected):
        assert missed_call.is_missed(status, duration) is expected

    def test_garbage_duration_is_not_a_missed_call(self):
        assert missed_call.is_missed("completed", "abc") is False


class TestRecording:
    async def test_logs_the_call_and_the_caller(self, shop_a):
        result = await missed_call.record_missed_call(
            shop_a, "+13125557001", "CA-1"
        )
        assert result["status"] == "logged"

        async with session_scope() as session:
            call = await get_call(session, shop_a, uuid.UUID(result["call_id"]))
            customer = await find_customer_by_phone(
                session, shop_a, "+13125557001"
            )
        assert call.outcome == CallOutcome.missed
        assert call.from_number == "+13125557001"
        assert call.texted_back_at is None
        assert customer is not None

    async def test_duplicate_webhooks_are_ignored(self, shop_a):
        """Twilio resends call status callbacks."""
        first = await missed_call.record_missed_call(shop_a, "+13125557002", "CA-2")
        second = await missed_call.record_missed_call(shop_a, "+13125557002", "CA-2")

        assert first["status"] == "logged"
        assert second["status"] == "duplicate"
        assert second["call_id"] == first["call_id"]

    async def test_calls_without_a_sid_are_each_logged(self, shop_a):
        a = await missed_call.record_missed_call(shop_a, "+13125557003", None)
        b = await missed_call.record_missed_call(shop_a, "+13125557003", None)
        assert a["call_id"] != b["call_id"]


@pytest.fixture
def stub_opener(monkeypatch):
    """Canned opener, so these tests assert routing rather than Claude's words."""
    calls: list[dict] = []

    async def fake_run_agent(**kwargs):
        calls.append(kwargs)
        return AgentOutcome(
            reply=AgentReply(
                reply="Sorry we missed your call! This is the shop.",
                detected_language="en",
                language_confidence=0.3,
            ),
            model="claude-haiku-5-5",
            usage=Usage(input_tokens=400, output_tokens=30, cost=0.0001),
        )

    monkeypatch.setattr(missed_call, "run_agent", fake_run_agent)
    return calls


class TestTextBack:
    async def test_texts_the_caller_on_sms(self, shop_a, stub_opener):
        logged = await missed_call.record_missed_call(
            shop_a, "+13125557010", "CA-10"
        )
        result = await missed_call.text_back(
            shop_a, uuid.UUID(logged["call_id"])
        )

        assert result["status"] == "texted_back"
        assert result["channel"] == "sms"

        async with session_scope() as session:
            customer = await find_customer_by_phone(
                session, shop_a, "+13125557010"
            )
            rows = await outbound_for_customer_since(
                session, shop_a, customer.id
            )
            conversation = await latest_conversation(
                session, shop_a, customer.id, Channel.sms
            )
        assert any("missed your call" in (r.body or "") for r in rows)
        assert conversation is not None
        assert conversation.short_code

    async def test_uses_the_missed_call_opener_instruction(
        self, shop_a, stub_opener
    ):
        logged = await missed_call.record_missed_call(
            shop_a, "+13125557011", "CA-11"
        )
        await missed_call.text_back(shop_a, uuid.UUID(logged["call_id"]))

        instruction = stub_opener[-1]["opener_instruction"]
        assert "nobody picked up" in instruction
        assert "reply in any language" in instruction
        assert stub_opener[-1]["first_contact_greeting"] is True

    async def test_a_known_caller_gets_their_stored_language(
        self, shop_a, stub_opener
    ):
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125557012"
            )
            customer.language = "es"

        logged = await missed_call.record_missed_call(
            shop_a, "+13125557012", "CA-12"
        )
        await missed_call.text_back(shop_a, uuid.UUID(logged["call_id"]))

        instruction = stub_opener[-1]["opener_instruction"]
        assert "Spanish" in instruction
        # A known caller gets one language, not a bilingual opener.
        assert "one line per language" not in instruction

    async def test_marks_the_call_so_it_is_not_texted_twice(
        self, shop_a, stub_opener
    ):
        logged = await missed_call.record_missed_call(
            shop_a, "+13125557013", "CA-13"
        )
        call_id = uuid.UUID(logged["call_id"])

        first = await missed_call.text_back(shop_a, call_id)
        second = await missed_call.text_back(shop_a, call_id)

        assert first["status"] == "texted_back"
        assert second["status"] == "already_texted"

    async def test_an_opted_out_caller_is_not_texted(self, shop_a, stub_opener):
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125557014"
            )
            await set_customer_opted_out(session, shop_a, customer.id, True)

        logged = await missed_call.record_missed_call(
            shop_a, "+13125557014", "CA-14"
        )
        result = await missed_call.text_back(
            shop_a, uuid.UUID(logged["call_id"])
        )
        assert result["status"] == "opted_out"

    async def test_claude_failing_still_sends_a_canned_opener(
        self, shop_a, monkeypatch
    ):
        """A missed call must never go unanswered."""

        async def failing(**kwargs):
            return AgentOutcome(
                reply=None, model="claude-haiku-5-5", failed=True, error="boom"
            )

        monkeypatch.setattr(missed_call, "run_agent", failing)

        logged = await missed_call.record_missed_call(
            shop_a, "+13125557015", "CA-15"
        )
        result = await missed_call.text_back(
            shop_a, uuid.UUID(logged["call_id"])
        )
        assert result["status"] == "texted_back"

        async with session_scope() as session:
            customer = await find_customer_by_phone(
                session, shop_a, "+13125557015"
            )
            rows = await outbound_for_customer_since(session, shop_a, customer.id)
        assert any("missed your call" in (r.body or "") for r in rows)

    async def test_the_owner_is_told(self, shop_a, stub_opener):
        logged = await missed_call.record_missed_call(
            shop_a, "+13125557016", "CA-16"
        )
        await missed_call.text_back(shop_a, uuid.UUID(logged["call_id"]))

        async with session_scope() as session:
            shop = await load_shop(session, shop_a, use_cache=False)
            owner = await find_customer_by_phone(
                session, shop_a, shop.owner_phone
            )
            rows = await outbound_for_customer_since(session, shop_a, owner.id)
        assert any("Missed call" in (r.body or "") for r in rows)


def test_channel_selection_defaults_to_sms():
    assert missed_call._pick_channel(None) == Channel.sms
    assert missed_call._pick_channel("") == Channel.sms
    assert missed_call._pick_channel("nonsense") == Channel.sms
    assert missed_call._pick_channel("whatsapp") == Channel.whatsapp
    assert missed_call._pick_channel("SMS") == Channel.sms


def test_fallback_openers_exist_for_both_launch_languages():
    assert "Shop" in missed_call._fallback_opener("Shop", "en")
    assert "Shop" in missed_call._fallback_opener("Shop", "es")
    # An unknown language falls back to English rather than failing.
    assert "Shop" in missed_call._fallback_opener("Shop", "pl")


class TestWebhook:
    @pytest.fixture
    async def client(self):
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield http

    async def test_simulator_call_queues_a_text_back(
        self, client, shop_a, monkeypatch
    ):
        queued: list = []

        async def fake_enqueue(client_id, call_id):
            queued.append(call_id)
            return "job"

        monkeypatch.setattr("app.gateway.routes.enqueue_missed_call", fake_enqueue)

        res = await client.post(
            "/webhooks/sim/call",
            json={
                "external_id": "sim-a",
                "from_number": "+13125557020",
                "call_sid": "CA-20",
            },
        )
        assert res.status_code == 200
        assert res.json()["status"] == "queued"
        assert len(queued) == 1

        # A resent webhook must not queue a second text-back.
        again = await client.post(
            "/webhooks/sim/call",
            json={
                "external_id": "sim-a",
                "from_number": "+13125557020",
                "call_sid": "CA-20",
            },
        )
        assert again.json()["status"] == "duplicate"
        assert len(queued) == 1

    async def test_answered_calls_are_ignored(self, client, shop_a, monkeypatch):
        queued: list = []

        async def fake_enqueue(client_id, call_id):
            queued.append(call_id)
            return "job"

        monkeypatch.setattr("app.gateway.routes.enqueue_missed_call", fake_enqueue)

        res = await client.post(
            "/webhooks/twilio/voice-status",
            data={
                "CallSid": "CA-answered",
                "From": "+13125557021",
                "To": "+13125550001",
                "CallStatus": "completed",
                "CallDuration": "95",
            },
        )
        assert res.status_code == 200
        assert queued == [], "an answered call must not trigger a text-back"

    async def test_no_answer_triggers_a_text_back(self, client, shop_a, monkeypatch):
        queued: list = []

        async def fake_enqueue(client_id, call_id):
            queued.append(call_id)
            return "job"

        monkeypatch.setattr("app.gateway.routes.enqueue_missed_call", fake_enqueue)

        res = await client.post(
            "/webhooks/twilio/voice-status",
            data={
                "CallSid": "CA-noanswer",
                "From": "+13125557022",
                "To": "+13125550001",
                "CallStatus": "no-answer",
            },
        )
        assert res.status_code == 200
        assert len(queued) == 1

    async def test_unknown_number_is_404(self, client, shop_a):
        res = await client.post(
            "/webhooks/twilio/voice-status",
            data={
                "CallSid": "CA-x",
                "From": "+13125557023",
                "To": "+19995550000",
                "CallStatus": "no-answer",
            },
        )
        assert res.status_code == 404

    async def test_simulator_call_requires_both_fields(self, client, shop_a):
        res = await client.post(
            "/webhooks/sim/call", json={"external_id": "sim-a"}
        )
        assert res.status_code == 400
