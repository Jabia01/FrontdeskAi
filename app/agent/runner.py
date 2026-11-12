"""The Claude agent loop.

Channel-agnostic by contract: it takes a conversation plus the new message and
returns `AgentReply`. A voice turn in v2 is a transcript in and text out, so
nothing here needs to change.

API details that are deliberate, not incidental:

* `claude-haiku-5-5` by default at `effort: "low"` - effort defaults to `medium`
  on that model, which is slower and dearer than a front-desk reply needs.
* Escalation to `claude-sonnet-5-5` on a long or unclear message, a tool failure,
  a handoff keyword, or a refusal. Haiku 5.5 has no server-side refusal
  fallback, so this escalation *is* the fallback.
* No `temperature` / `top_p` (removed from the API) and no `budget_tokens`
  (a 400 on both models). Thinking is adaptive-on; left alone.
* No forced `tool_choice` - it returns a 400 on both models. The prompt names the
  tools and every tool is `strict`.
* The per-turn hint goes in a `role: "system"` message, not inside the user turn,
  so a customer cannot spoof an operator instruction by typing one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from app.agent import language as lang
from app.agent.tools import ToolContext, dispatch, tool_definitions, tool_names
from app.config import get_settings
from app.core.router import ShopContext
from app.models import Direction, Message
from app.schemas import AGENT_REPLY_SCHEMA, AgentReply

log = logging.getLogger(__name__)

MAX_TOKENS = 1024

# USD per million tokens. Used for the per-shop margin figure in usage_events.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {
        "input": 0.10,
        "output": 0.50,
        "cache_read": 0.01,
        "cache_write": 0.125,
    },
    "claude-sonnet-5-5": {
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.10,
        "cache_write": 2.50,
    },
}


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost: float = 0.0

    def add(self, model: str, usage: Any) -> None:
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        c_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        c_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read_input_tokens += c_read
        self.cache_creation_input_tokens += c_write

        rates = PRICING.get(model)
        if rates:
            self.cost += (
                inp * rates["input"]
                + out * rates["output"]
                + c_read * rates["cache_read"]
                + c_write * rates["cache_write"]
            ) / 1_000_000


@dataclass(slots=True)
class AgentOutcome:
    reply: AgentReply | None
    model: str
    escalated: bool = False
    tool_calls: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    handoff_requested: bool = False
    handoff_reason: str = ""
    handoff_summary: str = ""
    refused: bool = False
    failed: bool = False
    error: str | None = None
    prompt_version: str = ""
    cache_hit: bool = False


_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            # Let the SDK resolve credentials itself (env var or an `ant` profile)
            # rather than failing on an empty configured key.
            _client = anthropic.AsyncAnthropic()
        else:
            _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def reset_client() -> None:
    global _client
    _client = None


# ----------------------------------------------------------------- prompt pieces


def history_to_messages(history: list[Message]) -> list[dict[str, Any]]:
    """Conversation rows -> API messages. Must start with a user turn."""
    messages: list[dict[str, Any]] = []
    for row in history:
        text = (row.body or row.transcript or "").strip()
        if not text:
            continue
        role = "user" if row.direction == Direction.inbound else "assistant"
        messages.append({"role": role, "content": text})

    # The API requires the first message to be a user turn.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def build_turn_hint(
    shop: ShopContext,
    decision: lang.LanguageDecision,
    *,
    misunderstand_count: int,
    detected_not_enabled: bool,
    now: datetime | None = None,
) -> str:
    """Per-turn operator context.

    The current date lives here rather than in the system prompt on purpose: the
    model needs it to turn "tomorrow" into a `preferred_day`, but putting a
    changing value in the cached prefix would move `prompt_version` every day.
    """
    tz = ZoneInfo(shop.timezone)
    local = (now or datetime.now(UTC)).astimezone(tz)
    parts = [
        (
            f"Right now it is {local.strftime('%A %d %B %Y, %H:%M')} "
            f"({shop.timezone}). Today is {local.date().isoformat()}."
        )
    ]
    parts += [
        (
            f"Answer in {lang.language_name(decision.primary)} unless the "
            f"customer's latest message is clearly in another language - then "
            f"use that one."
        )
    ]
    if decision.source == lang.Source.STORED:
        parts.append(
            "The latest message is too short or neutral to judge, so this is the "
            "language they used before."
        )
    if misunderstand_count >= 1:
        remaining = max(1, shop.max_misunderstandings - misunderstand_count)
        parts.append(
            f"You have already failed to understand this customer "
            f"{misunderstand_count} time(s). If this message is also unclear, set "
            f"understood=false; after {remaining} more you must call "
            f"handoff_to_human."
        )
    if detected_not_enabled:
        parts.append(
            "This shop has not enabled the customer's language. Answer simple "
            "factual questions (hours, address, services) in it anyway, but call "
            "handoff_to_human for anything more involved."
        )
    parts.append(f"Tools available: {', '.join(tool_names())}.")
    return " ".join(parts)


def _should_escalate_upfront(text: str, shop: ShopContext) -> tuple[bool, str]:
    settings = get_settings()
    if len(text) > settings.escalate_message_chars:
        return True, "long message"
    lowered = text.lower()
    for keyword in shop.handoff_keywords:
        if keyword and keyword in lowered:
            return True, f"handoff keyword: {keyword}"
    return False, ""


def _extract_reply(response: Any) -> AgentReply:
    """The final turn is guaranteed-valid JSON by output_config.format."""
    text = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"), None
    )
    if not text:
        raise ValueError("model returned no text block")
    return AgentReply.model_validate(json.loads(text))


# ------------------------------------------------------------------- the loop


async def _one_pass(
    *,
    model: str,
    effort: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tool_ctx: ToolContext,
    usage: Usage,
    tool_calls: list[str],
) -> tuple[AgentReply | None, str | None, bool]:
    """Run the tool loop on one model.

    Returns (reply, error, refused). `messages` is mutated as the turn grows.
    """
    client = get_client()
    settings = get_settings()
    tool_failed = False

    for _ in range(settings.max_tool_iterations):
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
            tools=tool_definitions(),
            output_config={"effort": effort, "format": AGENT_REPLY_SCHEMA},
        )
        usage.add(model, response.usage)

        if response.stop_reason == "refusal":
            detail = ""
            if getattr(response, "stop_details", None):
                detail = getattr(response.stop_details, "category", "") or ""
            return None, f"refusal({detail})", True

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        tool_uses = [
            b for b in response.content if getattr(b, "type", None) == "tool_use"
        ]

        if not tool_uses:
            try:
                return _extract_reply(response), None, False
            except Exception as exc:
                return None, f"unparseable reply: {exc}", False

        messages.append({"role": "assistant", "content": response.content})

        # All results go back in a single user message - splitting them trains
        # the model out of parallel tool calls.
        results: list[dict[str, Any]] = []
        for block in tool_uses:
            tool_calls.append(block.name)
            try:
                output = await dispatch(tool_ctx, block.name, dict(block.input))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
            except Exception as exc:
                tool_failed = True
                log.warning("tool %s failed: %s", block.name, exc)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Tool error: {exc}",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": results})

    return None, f"tool loop did not converge (tool_failed={tool_failed})", False


async def run_agent(
    *,
    shop: ShopContext,
    channel: str,
    history: list[Message],
    customer_text: str,
    customer_phone: str,
    stored_language: str | None,
    misunderstand_count: int,
    tool_ctx: ToolContext,
    first_contact_greeting: bool = False,
    opener_instruction: str | None = None,
    now: datetime | None = None,
) -> AgentOutcome:
    """One customer turn in, one reply out."""
    settings = get_settings()
    if tool_ctx.now is None:
        tool_ctx.now = now
    system_prompt, version = shop.system_prompt(channel)

    decision = lang.resolve(
        phone=customer_phone,
        detected=None,
        stored=stored_language,
        enabled=shop.enabled_languages,
        default_customer_language=shop.default_customer_language,
        overrides=shop.country_language_overrides,
        confidence_threshold=settings.language_confidence_threshold,
    )

    if first_contact_greeting or not customer_text:
        # An outbound-first message: a missed-call text-back supplies its own
        # instruction, otherwise this is a plain first greeting.
        instruction = opener_instruction or lang.greeting_instruction(
            decision, shop.name
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "(no customer message yet)"},
            {"role": "system", "content": instruction},
        ]
    else:
        messages = history_to_messages(history)
        if not messages:
            messages = [{"role": "user", "content": customer_text}]
        messages.append(
            {
                "role": "system",
                "content": build_turn_hint(
                    shop,
                    decision,
                    misunderstand_count=misunderstand_count,
                    detected_not_enabled=False,
                    now=now,
                ),
            }
        )

    usage = Usage()
    tool_calls: list[str] = []

    escalate, why = _should_escalate_upfront(customer_text, shop)
    model = settings.model_escalation if escalate else settings.model_default
    effort = "medium" if escalate else "low"
    if escalate:
        log.info("starting on %s (%s)", model, why)

    try:
        reply, error, refused = await _one_pass(
            model=model,
            effort=effort,
            system_prompt=system_prompt,
            messages=messages,
            tool_ctx=tool_ctx,
            usage=usage,
            tool_calls=tool_calls,
        )
    except Exception as exc:
        # Deliberately broad: a missing credential raises TypeError, a bad
        # request raises APIError, a DNS failure raises something else again.
        # Every one of them must degrade to the holding reply rather than
        # crash the worker, because the customer must always get an answer.
        log.error("Claude call failed on %s: %s", model, exc)
        reply, error, refused = None, f"{type(exc).__name__}: {exc}"[:500], False

    escalated = escalate

    # Escalate once if the cheap model refused, erred, or could not converge.
    if reply is None and model != settings.model_escalation:
        log.info("escalating to %s after: %s", settings.model_escalation, error)
        escalated = True
        model = settings.model_escalation
        try:
            reply, error, refused = await _one_pass(
                model=model,
                effort="medium",
                system_prompt=system_prompt,
                # Fresh turn: the failed exchange is not replayed.
                messages=[
                    m for m in messages if m.get("role") in ("user", "system")
                ],
                tool_ctx=tool_ctx,
                usage=usage,
                tool_calls=tool_calls,
            )
        except Exception as exc:
            log.error("escalated Claude call failed: %s", exc)
            reply, error = None, f"{type(exc).__name__}: {exc}"[:500]

    return AgentOutcome(
        reply=reply,
        model=model,
        escalated=escalated,
        tool_calls=tool_calls,
        usage=usage,
        handoff_requested=tool_ctx.handoff_requested,
        handoff_reason=tool_ctx.handoff_reason,
        handoff_summary=tool_ctx.handoff_summary,
        refused=refused,
        failed=reply is None,
        error=error,
        prompt_version=version,
        cache_hit=usage.cache_read_input_tokens > 0,
    )
