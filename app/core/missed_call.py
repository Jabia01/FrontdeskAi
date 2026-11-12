"""Missed-call text-back.

The shop keeps its own number and forwards unanswered calls to a Twilio number
we own. In v1 that number does not answer: it logs the call and texts the caller
within seconds, which is the whole point of the product - the job that would
have been lost is recovered as a text conversation.

SMS is the default channel because a missed call leaves no open WhatsApp window,
so a WhatsApp opener would need an approved template.
"""

from __future__ import annotations

import logging
import uuid

from app.agent import language as lang
from app.agent.runner import run_agent
from app.agent.tools import ToolContext
from app.core import owner as owner_mod
from app.core.delivery import send_reply
from app.core.router import load_shop
from app.db import session_scope
from app.models import CallOutcome, Channel
from app.repo import (
    call_exists,
    find_or_create_conversation,
    find_or_create_customer,
    get_client_endpoint,
    get_customer,
    log_call,
    mark_texted_back,
    record_usage,
)

log = logging.getLogger(__name__)

MISSED_STATUSES = {"no-answer", "busy", "failed", "canceled"}


def is_missed(call_status: str, duration: str | None = None) -> bool:
    """Which Twilio call statuses count as a missed call."""
    status = (call_status or "").strip().lower()
    if status in MISSED_STATUSES:
        return True
    # A "completed" call of zero seconds never actually connected.
    if status == "completed":
        try:
            return int(duration or 0) == 0
        except (TypeError, ValueError):
            return False
    return False


async def record_missed_call(
    client_id: uuid.UUID, from_number: str, provider_call_id: str | None
) -> dict:
    """Log the call. Idempotent on the provider's call id."""
    async with session_scope() as session:
        if provider_call_id:
            existing = await call_exists(session, client_id, provider_call_id)
            if existing is not None:
                return {
                    "status": "duplicate",
                    "call_id": str(existing.id),
                    "already_texted": existing.texted_back_at is not None,
                }

        customer = await find_or_create_customer(session, client_id, from_number)
        call = await log_call(
            session,
            client_id,
            customer_id=customer.id,
            from_number=from_number,
            provider_call_id=provider_call_id,
            outcome=CallOutcome.missed,
        )
        return {
            "status": "logged",
            "call_id": str(call.id),
            "customer_id": str(customer.id),
            "already_texted": False,
        }


async def text_back(client_id: uuid.UUID, call_id: uuid.UUID) -> dict:
    """Write and send the opener to whoever just called."""
    async with session_scope() as session:
        from app.repo import get_call

        call = await get_call(session, client_id, call_id)
        if call is None:
            return {"status": "unknown_call"}
        if call.texted_back_at is not None:
            return {"status": "already_texted"}

        shop = await load_shop(session, client_id)
        customer = (
            await get_customer(session, client_id, call.customer_id)
            if call.customer_id
            else None
        )
        if customer is None:
            return {"status": "unknown_customer"}

        if customer.opted_out_at is not None:
            log.info("not texting back %s: opted out", customer.phone)
            await mark_texted_back(session, client_id, call.id)
            return {"status": "opted_out"}

        channel = _pick_channel(shop.config.get("missed_call_channel"))
        if await get_client_endpoint(session, client_id, channel) is None:
            # Fall back to anything the shop can actually send on.
            for candidate in (Channel.sms, Channel.whatsapp, Channel.simulator):
                if (
                    await get_client_endpoint(session, client_id, candidate)
                    is not None
                ):
                    channel = candidate
                    break
            else:
                log.error("client %s cannot text back: no endpoint", client_id)
                return {"status": "no_endpoint"}

        conversation = await find_or_create_conversation(
            session, client_id, customer.id, channel
        )

        decision = lang.resolve(
            phone=customer.phone,
            stored=customer.language,
            enabled=shop.enabled_languages,
            default_customer_language=shop.default_customer_language,
            overrides=shop.country_language_overrides,
        )

        tool_ctx = ToolContext(
            session=session,
            shop=shop,
            conversation_id=conversation.id,
            customer_id=customer.id,
        )
        outcome = await run_agent(
            shop=shop,
            channel=channel.value,
            history=[],
            customer_text="",
            customer_phone=customer.phone,
            stored_language=customer.language,
            misunderstand_count=0,
            tool_ctx=tool_ctx,
            first_contact_greeting=True,
            opener_instruction=lang.missed_call_instruction(decision, shop.name),
        )

        await record_usage(
            session,
            client_id,
            type="claude",
            conversation_id=conversation.id,
            model=outcome.model,
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
            cache_read_input_tokens=outcome.usage.cache_read_input_tokens,
            cache_creation_input_tokens=outcome.usage.cache_creation_input_tokens,
            cost=outcome.usage.cost,
        )

        body = (
            outcome.reply.reply
            if outcome.reply is not None
            else _fallback_opener(shop.name, decision.primary)
        )
        if outcome.reply is None:
            log.error("missed-call opener fell back to canned text: %s", outcome.error)

        result, message_id = await send_reply(
            session,
            client_id=client_id,
            conversation=conversation,
            to_number=customer.phone,
            body=body,
            channel=channel,
            detected_language=lang.normalise(decision.primary),
        )
        await mark_texted_back(session, client_id, call.id)

        await owner_mod.send_to_owner(
            session,
            shop,
            owner_mod.missed_call_alert(
                conversation.short_code or "?", customer.phone
            ),
        )

        return {
            "status": "texted_back" if result.ok else "send_failed",
            "channel": channel.value,
            "message_id": str(message_id),
            "blocked": result.blocked,
            "error": result.error,
        }


def _pick_channel(configured: str | None) -> Channel:
    try:
        return Channel((configured or "sms").strip().lower())
    except ValueError:
        return Channel.sms


FALLBACK_OPENERS = {
    "en": "Sorry we missed your call! This is {shop}. How can we help?",
    "es": "Perdon por no contestar su llamada. Somos {shop}. Como podemos ayudar?",
}


def _fallback_opener(shop_name: str, language: str) -> str:
    """Used only if Claude is unavailable - a missed call must never go silent."""
    template = FALLBACK_OPENERS.get(
        lang.normalise(language) or "en", FALLBACK_OPENERS["en"]
    )
    return template.format(shop=shop_name)
