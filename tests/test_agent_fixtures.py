"""The agent's contract, replayed offline.

Responses are built as real `anthropic.types.Message` objects from recorded
payload shapes, so the suite runs with no API key and no network while still
exercising the actual parsing path.

The request-shape tests are the important ones: they pin the API decisions this
project depends on (prompt caching, structured output, strict tools, no forced
tool choice, no removed parameters). If a future edit breaks one, caching or the
whole call silently degrades.
"""

from __future__ import annotations

import json
import uuid

import pytest
from anthropic.types import Message

from app.agent import runner
from app.agent.tools import ToolContext
from app.core.router import load_shop
from app.db import session_scope
from app.models import Channel, ConversationStatus
from app.repo import (
    find_or_create_conversation,
    find_or_create_customer,
    get_conversation,
)

# --------------------------------------------------------------- fixture builder


def msg(
    content: list[dict],
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-haiku-5-5",
    input_tokens: int = 1200,
    output_tokens: int = 45,
    cache_read: int = 0,
    cache_write: int = 0,
    stop_details: dict | None = None,
) -> Message:
    payload = {
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    }
    if stop_details is not None:
        payload["stop_details"] = stop_details
    return Message.model_validate(payload)


def reply_json(
    reply="We close at 6pm.",
    language="en",
    confidence=0.95,
    handoff=False,
    handoff_reason="",
    understood=True,
) -> list[dict]:
    return [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "reply": reply,
                    "detected_language": language,
                    "language_confidence": confidence,
                    "handoff": handoff,
                    "handoff_reason": handoff_reason,
                    "understood": understood,
                }
            ),
        }
    ]


def tool_use(name: str, payload: dict, block_id="toolu_1") -> list[dict]:
    return [{"type": "tool_use", "id": block_id, "name": name, "input": payload}]


class FakeMessages:
    """Replays a queue of responses and records every request."""

    def __init__(self, responses: list[Message]):
        self._responses = list(responses)
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("agent made more calls than the fixture expected")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[Message]):
        self.messages = FakeMessages(responses)


@pytest.fixture
def fake_claude(monkeypatch):
    def install(responses: list[Message]) -> FakeClient:
        client = FakeClient(responses)
        monkeypatch.setattr(runner, "get_client", lambda: client)
        return client

    return install


@pytest.fixture
async def ctx(shop_a):
    """A ShopContext plus a real conversation and ToolContext."""
    async with session_scope() as session:
        customer = await find_or_create_customer(session, shop_a, "+13125551212")
        conversation = await find_or_create_conversation(
            session, shop_a, customer.id, Channel.simulator
        )
        customer_id, conversation_id = customer.id, conversation.id

    async with session_scope() as session:
        shop = await load_shop(session, shop_a, use_cache=False)
        yield {
            "client_id": shop_a,
            "shop": shop,
            "session": session,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "tool_ctx": ToolContext(
                session=session,
                shop=shop,
                conversation_id=conversation_id,
                customer_id=customer_id,
            ),
        }


async def run(ctx, text="what time do you close?", **overrides):
    kwargs = {
        "shop": ctx["shop"],
        "channel": "simulator",
        "history": [],
        "customer_text": text,
        "customer_phone": "+13125551212",
        "stored_language": None,
        "misunderstand_count": 0,
        "tool_ctx": ctx["tool_ctx"],
    }
    kwargs.update(overrides)
    return await runner.run_agent(**kwargs)


# ------------------------------------------------------------------ happy paths


class TestPlainReply:
    async def test_parses_reply_and_language(self, ctx, fake_claude):
        fake_claude([msg(reply_json("Cerramos a las 6.", "es", 0.97))])
        out = await run(ctx, "a que hora cierran?")

        assert out.failed is False
        assert out.reply.reply == "Cerramos a las 6."
        assert out.reply.detected_language == "es"
        assert out.reply.language_confidence == 0.97
        assert out.model == "claude-haiku-5-5"
        assert out.escalated is False

    async def test_usage_and_cost_are_accumulated(self, ctx, fake_claude):
        fake_claude(
            [msg(reply_json(), input_tokens=2000, output_tokens=100, cache_read=1800)]
        )
        out = await run(ctx)

        assert out.usage.input_tokens == 2000
        assert out.usage.output_tokens == 100
        assert out.usage.cache_read_input_tokens == 1800
        assert out.cache_hit is True
        # Haiku: 2000*0.10 + 100*0.50 + 1800*0.01 per million
        assert out.usage.cost == pytest.approx(
            (2000 * 0.10 + 100 * 0.50 + 1800 * 0.01) / 1_000_000
        )

    async def test_cache_miss_is_reported(self, ctx, fake_claude):
        fake_claude([msg(reply_json(), cache_read=0, cache_write=1500)])
        out = await run(ctx)
        assert out.cache_hit is False
        assert out.usage.cache_creation_input_tokens == 1500


class TestRequestShape:
    """These pin the API contract the design depends on."""

    async def test_system_prompt_is_cached_and_deterministic(self, ctx, fake_claude):
        client = fake_claude([msg(reply_json()), msg(reply_json())])
        await run(ctx)
        await run(ctx, "and on sunday?")

        first, second = client.messages.requests[0], client.messages.requests[1]
        system = first["system"]
        assert isinstance(system, list) and len(system) == 1
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        # Byte-identical across requests, or nothing caches.
        assert first["system"] == second["system"]

    async def test_structured_output_and_effort(self, ctx, fake_claude):
        client = fake_claude([msg(reply_json())])
        await run(ctx)

        cfg = client.messages.requests[0]["output_config"]
        assert cfg["effort"] == "low"
        assert cfg["format"]["type"] == "json_schema"
        schema = cfg["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "reply",
            "detected_language",
            "language_confidence",
            "handoff",
            "handoff_reason",
            "understood",
        }

    async def test_tools_are_strict_and_no_tool_choice_is_forced(
        self, ctx, fake_claude
    ):
        client = fake_claude([msg(reply_json())])
        await run(ctx)
        request = client.messages.requests[0]

        assert all(t["strict"] is True for t in request["tools"])
        # Forced tool_choice is a 400 on both models - it must be absent.
        assert "tool_choice" not in request

    async def test_removed_parameters_are_never_sent(self, ctx, fake_claude):
        client = fake_claude([msg(reply_json())])
        await run(ctx)
        request = client.messages.requests[0]

        for banned in ("temperature", "top_p", "top_k", "thinking"):
            assert banned not in request, f"{banned} must not be sent"

    async def test_turn_hint_is_an_operator_system_message(self, ctx, fake_claude):
        """A customer must not be able to spoof an operator instruction."""
        client = fake_claude([msg(reply_json())])
        await run(ctx, "hola")

        messages = client.messages.requests[0]["messages"]
        assert messages[-1]["role"] == "system"
        assert messages[0]["role"] == "user"  # the API requires this

    async def test_greeting_path_when_there_is_no_customer_message(
        self, ctx, fake_claude
    ):
        client = fake_claude([msg(reply_json("Hi! Hallo!", "en", 0.4))])
        out = await run(ctx, "", customer_phone="+4915112345678")

        messages = client.messages.requests[0]["messages"]
        instruction = messages[-1]["content"]
        assert messages[-1]["role"] == "system"
        assert "German" in instruction and "English" in instruction
        assert "reply in any language" in instruction
        assert out.failed is False


# ------------------------------------------------------------------- tool loop


class TestToolLoop:
    async def test_tool_call_then_reply(self, ctx, fake_claude):
        client = fake_claude(
            [
                msg(tool_use("get_business_info", {"topic": "hours"}),
                    stop_reason="tool_use"),
                msg(reply_json("We close at 6pm on weekdays.")),
            ]
        )
        out = await run(ctx)

        assert out.tool_calls == ["get_business_info"]
        assert out.reply.reply == "We close at 6pm on weekdays."

        # The tool result must come back as a single user message.
        second = client.messages.requests[1]["messages"]
        assert second[-1]["role"] == "user"
        assert second[-1]["content"][0]["type"] == "tool_result"
        assert second[-1]["content"][0]["tool_use_id"] == "toolu_1"

    async def test_create_lead_writes_a_row(self, ctx, fake_claude):
        from sqlalchemy import select

        from app.models import Lead

        fake_claude(
            [
                msg(
                    tool_use(
                        "create_lead",
                        {
                            "item": "2014 Honda Civic",
                            "issue": "brakes grinding",
                            "contact": "Juan",
                        },
                    ),
                    stop_reason="tool_use",
                ),
                msg(reply_json("Got it - the shop will confirm a time.")),
            ]
        )
        out = await run(ctx, "my civic brakes are grinding")
        assert out.tool_calls == ["create_lead"]
        await ctx["session"].commit()

        async with session_scope() as session:
            leads = (await session.execute(select(Lead))).scalars().all()
        assert len(leads) == 1
        assert leads[0].item == "2014 Honda Civic"
        assert leads[0].client_id == ctx["client_id"]

    async def test_handoff_tool_flips_the_conversation_to_human(
        self, ctx, fake_claude
    ):
        fake_claude(
            [
                msg(
                    tool_use(
                        "handoff_to_human",
                        {"reason": "accident", "summary": "Customer had a crash."},
                    ),
                    stop_reason="tool_use",
                ),
                msg(reply_json("Someone from the shop will call you.")),
            ]
        )
        out = await run(ctx, "I was in an accident, my lawyer needs the invoice")

        assert out.handoff_requested is True
        assert out.handoff_reason == "accident"
        assert out.handoff_summary == "Customer had a crash."
        await ctx["session"].commit()

        async with session_scope() as session:
            conversation = await get_conversation(
                session, ctx["client_id"], ctx["conversation_id"]
            )
        assert conversation.status == ConversationStatus.human

    async def test_a_failing_tool_is_reported_back_not_raised(
        self, ctx, fake_claude
    ):
        client = fake_claude(
            [
                msg(tool_use("no_such_tool", {}), stop_reason="tool_use"),
                msg(reply_json("Let me check with the shop.")),
            ]
        )
        out = await run(ctx)

        assert out.failed is False
        result = client.messages.requests[1]["messages"][-1]["content"][0]
        assert result["is_error"] is True

    async def test_the_enabled_tool_set_is_exact_and_ordered(self, ctx, fake_claude):
        """Tools render before `system`, so this order is part of the cache key."""
        client = fake_claude([msg(reply_json())])
        await run(ctx)

        names = [t["name"] for t in client.messages.requests[0]["tools"]]
        assert names == [
            "get_business_info",
            "create_lead",
            "handoff_to_human",
            "check_availability",
            "book_appointment",
            "get_job_status",
        ]

    async def test_runaway_tool_loop_is_capped(self, ctx, fake_claude, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "2")
        get_settings.cache_clear()
        try:
            fake_claude(
                [
                    msg(tool_use("get_business_info", {"topic": "hours"}, "t1"),
                        stop_reason="tool_use"),
                    msg(tool_use("get_business_info", {"topic": "prices"}, "t2"),
                        stop_reason="tool_use"),
                    # escalation pass, also looping
                    msg(tool_use("get_business_info", {"topic": "hours"}, "t3"),
                        stop_reason="tool_use"),
                    msg(tool_use("get_business_info", {"topic": "prices"}, "t4"),
                        stop_reason="tool_use"),
                ]
            )
            out = await run(ctx)
            assert out.failed is True
            assert "did not converge" in out.error
        finally:
            get_settings.cache_clear()


# ------------------------------------------------------------------ escalation


class TestEscalation:
    async def test_refusal_escalates_to_sonnet(self, ctx, fake_claude):
        """Haiku 5.5 has no server-side refusal fallback - we are the fallback."""
        client = fake_claude(
            [
                msg(
                    [],
                    stop_reason="refusal",
                    stop_details={"type": "refusal", "category": "cyber"},
                ),
                msg(reply_json("Happy to help with your car."),
                    model="claude-sonnet-5-5"),
            ]
        )
        out = await run(ctx)

        assert out.escalated is True
        assert out.model == "claude-sonnet-5-5"
        assert out.failed is False
        assert client.messages.requests[0]["model"] == "claude-haiku-5-5"
        assert client.messages.requests[1]["model"] == "claude-sonnet-5-5"
        assert client.messages.requests[1]["output_config"]["effort"] == "medium"

    async def test_unparseable_output_escalates(self, ctx, fake_claude):
        fake_claude(
            [
                msg([{"type": "text", "text": "not json at all"}]),
                msg(reply_json("We close at 6."), model="claude-sonnet-5-5"),
            ]
        )
        out = await run(ctx)
        assert out.escalated is True
        assert out.reply.reply == "We close at 6."

    async def test_a_long_message_starts_on_sonnet(self, ctx, fake_claude):
        client = fake_claude([msg(reply_json(), model="claude-sonnet-5-5")])
        out = await run(ctx, "x" * 500)

        assert out.escalated is True
        assert client.messages.requests[0]["model"] == "claude-sonnet-5-5"
        assert client.messages.requests[0]["output_config"]["effort"] == "medium"

    async def test_a_handoff_keyword_starts_on_sonnet(self, ctx, fake_claude):
        client = fake_claude([msg(reply_json(), model="claude-sonnet-5-5")])
        await run(ctx, "my car was towed after an accident")
        assert client.messages.requests[0]["model"] == "claude-sonnet-5-5"

    async def test_both_models_failing_reports_failure(self, ctx, fake_claude):
        fake_claude(
            [
                msg([{"type": "text", "text": "garbage"}]),
                msg([{"type": "text", "text": "garbage again"}],
                    model="claude-sonnet-5-5"),
            ]
        )
        out = await run(ctx)
        assert out.failed is True
        assert out.reply is None
        assert out.error

    async def test_a_transport_exception_degrades_instead_of_crashing(
        self, ctx, monkeypatch
    ):
        """Any exception must become a failed outcome, not a worker crash."""

        class Boom:
            class messages:
                @staticmethod
                async def create(**kwargs):
                    raise TypeError("Could not resolve authentication method")

        monkeypatch.setattr(runner, "get_client", lambda: Boom())
        out = await run(ctx)

        assert out.failed is True
        assert "TypeError" in out.error

    async def test_usage_from_both_passes_is_counted(self, ctx, fake_claude):
        fake_claude(
            [
                msg([{"type": "text", "text": "garbage"}], input_tokens=100,
                    output_tokens=10),
                msg(reply_json(), model="claude-sonnet-5-5", input_tokens=200,
                    output_tokens=20),
            ]
        )
        out = await run(ctx)
        assert out.usage.input_tokens == 300
        assert out.usage.output_tokens == 30


class TestHistory:
    async def test_history_maps_directions_to_roles(self, ctx, fake_claude):
        from app.models import Direction
        from app.repo import add_message, recent_messages

        async with session_scope() as session:
            for direction, body in (
                (Direction.inbound, "hola"),
                (Direction.outbound, "Hola! Como podemos ayudar?"),
                (Direction.inbound, "cuanto por frenos?"),
            ):
                await add_message(
                    session,
                    ctx["client_id"],
                    ctx["conversation_id"],
                    direction=direction,
                    channel=Channel.simulator,
                    body=body,
                )
        async with session_scope() as session:
            history = await recent_messages(
                session, ctx["client_id"], ctx["conversation_id"]
            )

        client = fake_claude([msg(reply_json("Entre 300 y 450.", "es", 0.96))])
        await run(ctx, "cuanto por frenos?", history=history)

        messages = client.messages.requests[0]["messages"]
        assert [m["role"] for m in messages[:3]] == ["user", "assistant", "user"]
        assert messages[0]["content"] == "hola"

    def test_history_must_begin_with_a_user_turn(self):
        """A conversation starting with an outbound message is trimmed."""

        class Row:
            def __init__(self, direction, body):
                self.direction = direction
                self.body = body
                self.transcript = None

        from app.models import Direction

        rows = [
            Row(Direction.outbound, "Missed your call - how can we help?"),
            Row(Direction.inbound, "brakes please"),
        ]
        messages = runner.history_to_messages(rows)
        assert messages[0]["role"] == "user"
        assert len(messages) == 1

    def test_empty_bodies_are_skipped(self):
        class Row:
            def __init__(self, direction, body):
                self.direction = direction
                self.body = body
                self.transcript = None

        from app.models import Direction

        rows = [Row(Direction.inbound, "  "), Row(Direction.inbound, "hi")]
        assert runner.history_to_messages(rows) == [
            {"role": "user", "content": "hi"}
        ]
