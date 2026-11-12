"""Conversation engine.

Decides whether the AI answers at all, and orchestrates one turn. The order is
deliberate:

  1. Is this the owner? Their messages are commands, not a conversation.
  2. Compliance - opt-out and STOP/HELP, handled before the AI sees anything.
  3. Has a human taken over? A takeover always beats the AI.
  4. Run the agent.
  5. Shadow mode? Draft it for the owner instead of sending it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import language as lang
from app.agent.runner import AgentOutcome, run_agent
from app.agent.tools import ToolContext
from app.config import get_settings
from app.core import owner as owner_mod
from app.core import owner_commands
from app.core.delivery import send_reply
from app.core.router import ShopContext, load_shop
from app.db import session_scope
from app.models import (
    ClientStatus,
    Conversation,
    ConversationStatus,
    Customer,
    Direction,
    LanguageSource,
    MessageStatus,
)
from app.repo import (
    add_message,
    get_conversation,
    get_customer,
    get_message,
    hand_off,
    recent_messages,
    record_usage,
    set_counters,
    set_customer_opted_out,
    update_customer_language,
)

log = logging.getLogger(__name__)

# 10DLC / carrier requirements - handled automatically, never by the AI.
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout"}
HELP_WORDS = {"help", "info"}
START_WORDS = {"start", "yes", "unstop"}

HOLDING_REPLY = {
    "en": (
        "Thanks for your message - I'm having trouble answering right now. "
        "Someone from the shop will get back to you shortly."
    ),
    "es": (
        "Gracias por su mensaje. Tengo un problema para responder ahora mismo. "
        "Alguien del taller le contestara en breve."
    ),
}


@dataclass(slots=True)
class Decision:
    should_reply: bool
    reason: str


def classify_control(text: str) -> str | None:
    """Carrier control keyword, if the message is nothing but one."""
    token = (text or "").strip().lower().strip(".!?")
    if not token or len(token.split()) > 1:
        return None
    if token in STOP_WORDS:
        return "stop"
    if token in HELP_WORDS:
        return "help"
    if token in START_WORDS:
        return "start"
    return None


def should_ai_reply(conversation: Conversation, customer: Customer) -> Decision:
    settings = get_settings()
    if not settings.ai_enabled:
        return Decision(False, "ai disabled globally")
    if customer.opted_out_at is not None:
        return Decision(False, "customer opted out")
    if conversation.status == ConversationStatus.human:
        return Decision(False, "human has taken over")
    if conversation.status == ConversationStatus.closed:
        return Decision(False, "conversation closed")
    return Decision(True, "ok")


def holding_text(language: str | None) -> str:
    code = lang.normalise(language) or "en"
    return HOLDING_REPLY.get(code, HOLDING_REPLY["en"])


async def _alert_owner(client_id: uuid.UUID, body: str) -> None:
    from app.queue.client import enqueue_owner_alert

    await enqueue_owner_alert(client_id, body)


async def handle_inbound_message(
    client_id: uuid.UUID, message_id: uuid.UUID
) -> dict:
    """One full turn: decide, answer, deliver, account. Called by the worker."""
    async with session_scope() as session:
        message = await get_message(session, client_id, message_id)
        if message is None:
            return {"status": "unknown_message"}

        conversation = await get_conversation(
            session, client_id, message.conversation_id
        )
        if conversation is None:
            return {"status": "unknown_conversation"}

        customer = await get_customer(session, client_id, conversation.customer_id)
        if customer is None:
            return {"status": "unknown_customer"}

        shop = await load_shop(session, client_id)
        text = (message.body or message.transcript or "").strip()

        # --- 1. the owner, giving a command ---------------------------------
        if customer.is_owner or owner_commands.is_owner_number(
            shop, customer.phone
        ):
            if not customer.is_owner:
                customer.is_owner = True
            result = await owner_commands.handle(session, shop, text)
            reply = result.pop("reply_to_owner", "")
            # Pulled out so the spread below cannot clobber the outer status.
            command_status = result.pop("status", "unknown")
            if reply:
                await owner_mod.send_to_owner(session, shop, reply)
            log.info("owner command -> %s", command_status)
            return {"status": "owner_command", "command": command_status, **result}

        # --- 2. compliance --------------------------------------------------
        control = classify_control(text)
        if control == "stop":
            await set_customer_opted_out(session, client_id, customer.id, True)
            await send_reply(
                session,
                client_id=client_id,
                conversation=conversation,
                to_number=customer.phone,
                body=(
                    f"You have been unsubscribed from {shop.name} messages and "
                    f"will not receive any more. Reply START to opt back in."
                ),
                channel=message.channel,
            )
            return {"status": "opted_out"}

        if control == "start":
            await set_customer_opted_out(session, client_id, customer.id, False)
        elif control == "help":
            await send_reply(
                session,
                client_id=client_id,
                conversation=conversation,
                to_number=customer.phone,
                body=(
                    f"{shop.name}. Reply with your question and we will help. "
                    f"Reply STOP to unsubscribe."
                ),
                channel=message.channel,
            )
            return {"status": "help_sent"}

        # --- 3. should the AI answer at all? --------------------------------
        decision = should_ai_reply(conversation, customer)
        if not decision.should_reply:
            log.info("not replying to %s: %s", message.id, decision.reason)
            if conversation.status == ConversationStatus.human:
                await _alert_owner(
                    client_id,
                    f"[{conversation.short_code}] {customer.phone} replied "
                    f"while you have this chat:\n{text[:400]}",
                )
            return {"status": "skipped", "reason": decision.reason}

        # --- 4. run the agent -----------------------------------------------
        history = await recent_messages(session, client_id, conversation.id, limit=20)
        tool_ctx = ToolContext(
            session=session,
            shop=shop,
            conversation_id=conversation.id,
            customer_id=customer.id,
        )

        outcome = await run_agent(
            shop=shop,
            channel=message.channel.value,
            history=history,
            customer_text=text,
            customer_phone=customer.phone,
            stored_language=customer.language,
            misunderstand_count=conversation.misunderstand_count,
            tool_ctx=tool_ctx,
            first_contact_greeting=not text,
        )

        await _record_usage(session, shop, conversation, outcome)

        if outcome.failed or outcome.reply is None:
            return await _holding_reply(
                session, shop, conversation, customer, message, outcome, text
            )

        reply = outcome.reply
        settings = get_settings()

        # --- language bookkeeping -------------------------------------------
        detected = lang.normalise(reply.detected_language)
        if (
            detected
            and reply.language_confidence >= settings.language_confidence_threshold
        ):
            await update_customer_language(
                session,
                client_id,
                customer.id,
                detected,
                reply.language_confidence,
                LanguageSource.detected,
            )

        # --- two failed attempts to understand -> hand off -------------------
        misunderstands = (
            conversation.misunderstand_count + 1 if not reply.understood else 0
        )
        await set_counters(
            session, client_id, conversation.id, misunderstand_count=misunderstands
        )
        forced_handoff = (
            not reply.understood and misunderstands >= shop.max_misunderstandings
        )

        # The model can signal a handoff two ways: by calling handoff_to_human
        # (which pauses the AI itself) or by setting `handoff` in its structured
        # reply. Pause here too, so the two paths cannot diverge and leave the
        # AI answering a conversation it has already escalated.
        needs_pause = forced_handoff or reply.handoff or outcome.handoff_requested
        if needs_pause and conversation.status != ConversationStatus.human:
            await hand_off(
                session,
                client_id,
                conversation.id,
                outcome.handoff_reason
                or reply.handoff_reason
                or "could not understand the customer twice",
            )

        # --- 5. shadow mode: draft for the owner instead of sending ----------
        if shop.status == ClientStatus.shadow.value:
            draft = await add_message(
                session,
                client_id,
                conversation.id,
                direction=Direction.outbound,
                channel=message.channel,
                body=reply.reply,
                status=MessageStatus.draft,
                detected_language=detected,
            )
            await _alert_owner(
                client_id,
                owner_mod.draft_alert(
                    conversation.short_code or "?", text[:200], reply.reply
                ),
            )
            return {
                "status": "drafted",
                "message_id": str(draft.id),
                "model": outcome.model,
                "escalated": outcome.escalated,
                "tool_calls": outcome.tool_calls,
                "detected_language": detected,
                "handoff": outcome.handoff_requested or forced_handoff,
                "cost": round(outcome.usage.cost, 6),
            }

        # --- deliver ---------------------------------------------------------
        result, out_id = await send_reply(
            session,
            client_id=client_id,
            conversation=conversation,
            to_number=customer.phone,
            body=reply.reply,
            channel=message.channel,
            detected_language=detected,
        )

        # --- owner notifications --------------------------------------------
        code = conversation.short_code or "?"
        if tool_ctx.booking_id is not None:
            await _alert_owner(
                client_id, owner_mod.booking_alert(code, tool_ctx.booking_summary)
            )
        if needs_pause:
            await _alert_owner(
                client_id,
                owner_mod.handoff_alert(
                    code,
                    outcome.handoff_reason
                    or "could not understand the customer twice",
                    outcome.handoff_summary or text[:400],
                    customer.phone,
                ),
            )
        if result.blocked:
            await _alert_owner(
                client_id,
                f"[{code}] I could not reach {customer.phone}: the 24-hour "
                f"WhatsApp window has closed. My reply was:\n{reply.reply[:300]}",
            )

        await record_usage(
            session,
            client_id,
            type="message_out",
            conversation_id=conversation.id,
            units=result.segments,
        )

        return {
            "status": "replied" if result.ok else "send_failed",
            "message_id": str(out_id),
            "model": outcome.model,
            "escalated": outcome.escalated,
            "tool_calls": outcome.tool_calls,
            "detected_language": detected,
            "handoff": needs_pause,
            "booked": tool_ctx.booking_id is not None,
            "cache_hit": outcome.cache_hit,
            "cost": round(outcome.usage.cost, 6),
        }


async def _holding_reply(
    session: AsyncSession,
    shop: ShopContext,
    conversation: Conversation,
    customer: Customer,
    message,
    outcome: AgentOutcome,
    text: str,
) -> dict:
    """Claude failed entirely. The customer still gets an answer."""
    failures = conversation.ai_failure_count + 1
    await set_counters(
        session, shop.client_id, conversation.id, ai_failure_count=failures
    )
    await send_reply(
        session,
        client_id=shop.client_id,
        conversation=conversation,
        to_number=customer.phone,
        body=holding_text(customer.language),
        channel=message.channel,
    )
    await _alert_owner(
        shop.client_id,
        f"[{conversation.short_code}] I could not answer {customer.phone} "
        f"({outcome.error}). They said:\n{text[:400]}",
    )
    return {"status": "holding_reply", "error": outcome.error}


async def _record_usage(
    session: AsyncSession,
    shop: ShopContext,
    conversation: Conversation,
    outcome: AgentOutcome,
) -> None:
    await record_usage(
        session,
        shop.client_id,
        type="claude",
        conversation_id=conversation.id,
        model=outcome.model,
        input_tokens=outcome.usage.input_tokens,
        output_tokens=outcome.usage.output_tokens,
        cache_read_input_tokens=outcome.usage.cache_read_input_tokens,
        cache_creation_input_tokens=outcome.usage.cache_creation_input_tokens,
        cost=outcome.usage.cost,
    )
