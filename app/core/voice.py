"""Answering the phone.

Retell owns the telephony, the speech recognition and the voice. Chichi owns the
thinking: the same prompt, the same tools, the same booking and handoff rules
that answer a text message answer a phone call, because `run_agent` was written
channel-agnostic and this module finally collects on that.

Three things make voice genuinely different from text, and each one is a
decision rather than a detail:

**There is no queue.** A text message can wait two seconds in Redis; a caller
cannot. So a turn runs inline in the websocket handler, and the only database
work on the critical path is what the tools themselves need.

**The transcript is Retell's, not ours.** Retell sends the full conversation on
every turn and revises earlier utterances as recognition improves. Treating that
as the source of truth - rather than reassembling history from our own rows -
means we never argue with the speech recogniser about what the caller said.

**Nothing is written per turn.** The transcript is persisted once, at the end,
from the final corrected version. A half-written conversation is worse than one
written a few seconds late, and the things that must survive a dropped call -
a booking, a lead, a handoff - are written by the tools as they happen.

The one rule voice cannot honour is shadow mode. Shadow mode means the owner
approves each reply before it goes out, and there is no approving anything in
the second and a half before a caller hears silence. A shop that is not `live`
therefore has its calls declined, so they fall back to the shop's own line
rather than reaching an AI the owner has not signed off.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import language as lang
from app.agent.runner import run_agent
from app.agent.tools import ToolContext
from app.config import get_settings
from app.core import owner as owner_mod
from app.core.router import ShopContext, load_shop
from app.db import session_scope
from app.models import (
    Call,
    CallOutcome,
    Channel,
    ConversationStatus,
    Direction,
    LanguageSource,
    Message,
    MessageStatus,
)
from app.queue.client import enqueue_owner_alert
from app.repo import (
    add_message,
    call_exists,
    find_or_create_conversation,
    find_or_create_customer,
    get_conversation,
    get_customer,
    get_endpoint,
    hand_off,
    log_call,
    message_exists,
    record_usage,
    set_counters,
    update_customer_language,
)

log = logging.getLogger(__name__)

#: Statuses that may answer a live call. Everything else falls back to the
#: shop's own line - see the module docstring on shadow mode.
ANSWERABLE = {"live"}


class CallNotRegistered(Exception):
    """A websocket arrived for a call we never saw an inbound webhook for.

    Either the number is pointed at us without the inbound webhook configured,
    or someone is guessing call ids at our bridge. Both are a refusal.
    """


# --------------------------------------------------------------- spoken furniture
# Said before any model has run, so they have to be instant and therefore
# canned. A shop whose language is not here is greeted in English and Chichi
# switches from the caller's first sentence onward - which is the same thing
# that happens on text, and is why these stay a short list rather than a
# half-maintained atlas.

GREETINGS: dict[str, str] = {
    "en": "Thanks for calling {shop}. How can I help?",
    "es": "Gracias por llamar a {shop}. En que puedo ayudarle?",
    "pl": "Dziekujemy za telefon do {shop}. Jak moge pomoc?",
    "ar": "شكرا لاتصالك بـ {shop}. كيف يمكنني مساعدتك؟",
    "fr": "Merci d'appeler {shop}. Comment puis-je vous aider?",
    "de": "Danke fur Ihren Anruf bei {shop}. Wie kann ich helfen?",
    "pt": "Obrigado por ligar para {shop}. Como posso ajudar?",
    "it": "Grazie per aver chiamato {shop}. Come posso aiutarla?",
    "ru": "Спасибо, что позвонили в {shop}. Чем могу помочь?",
    "uk": "Дякуємо за дзвінок до {shop}. Чим можу допомогти?",
    "vi": "Cam on ban da goi {shop}. Toi co the giup gi?",
    "zh": "感谢致电{shop}。请问需要什么帮助？",
    "ko": "{shop}에 전화해 주셔서 감사합니다. 무엇을 도와드릴까요?",
    "hi": "{shop} को कॉल करने के लिए धन्यवाद। मैं कैसे मदद कर सकता हूं?",
}

NUDGES: dict[str, str] = {
    "en": "Are you still there?",
    "es": "Sigue ahi?",
    "pl": "Jest tam Pan?",
    "ar": "هل أنت موجود؟",
    "fr": "Vous etes toujours la?",
    "de": "Sind Sie noch da?",
    "pt": "Ainda esta ai?",
    "it": "E ancora in linea?",
    "ru": "Вы еще на линии?",
    "uk": "Ви ще на лінії?",
    "vi": "Ban con do khong?",
    "zh": "您还在吗？",
    "ko": "아직 듣고 계신가요?",
    "hi": "आप अभी भी हैं?",
}

#: Said when a turn is taking long enough that the line would otherwise be
#: silent. On a phone call, two seconds of nothing reads as a dropped
#: connection - a human receptionist fills it without thinking.
FILLERS: dict[str, str] = {
    "en": "One moment.",
    "es": "Un momento.",
    "pl": "Chwileczke.",
    "ar": "لحظة واحدة.",
    "fr": "Un instant.",
    "de": "Einen Moment.",
    "pt": "Um momento.",
    "it": "Un momento.",
    "ru": "Одну минуту.",
    "uk": "Одну хвилину.",
    "vi": "Mot chut.",
    "zh": "请稍等。",
    "ko": "잠시만요.",
    "hi": "एक पल।",
}

TROUBLE: dict[str, str] = {
    "en": "Sorry, I am having trouble on my end. Let me have someone call you back.",
    "es": "Disculpe, tengo un problema tecnico. Hare que alguien le devuelva la llamada.",
    "pl": "Przepraszam, mam problem techniczny. Ktos oddzwoni do Pana.",
    "ar": "أعتذر، لدي مشكلة تقنية. سأجعل أحدا يعاود الاتصال بك.",
}

TRANSFERRING: dict[str, str] = {
    "en": "Let me put you through to someone now.",
    "es": "Le comunico con alguien ahora mismo.",
    "pl": "Przelaczam Pana teraz do kogos.",
    "ar": "سأحولك إلى أحد الآن.",
}

PROMISE_CALLBACK: dict[str, str] = {
    "en": "I will have someone call you straight back on this number.",
    "es": "Hare que alguien le llame de inmediato a este numero.",
    "pl": "Ktos zadzwoni do Pana z powrotem na ten numer.",
    "ar": "سأجعل أحدا يتصل بك على هذا الرقم فورا.",
}


def _say(table: Mapping[str, str], language: str | None, **kwargs: Any) -> str:
    code = lang.normalise(language) or "en"
    template = table.get(code) or table["en"]
    return template.format(**kwargs)


def greeting(shop: ShopContext) -> str:
    """The first thing a caller hears, with no model call in the way.

    Running the agent for an opener would put a second of dead air at pickup -
    the single worst place to spend it, because the caller does not yet know
    anyone is there.
    """
    return _say(GREETINGS, shop.default_customer_language, shop=shop.name)


def nudge(language: str | None) -> str:
    return _say(NUDGES, language)


def filler(language: str | None) -> str:
    """A held beat, not an answer.

    Sent as an incomplete chunk so Retell speaks it and then speaks the real
    reply after it, in one turn. Costs nothing and no model call - it exists
    only so the caller knows the line is still alive.
    """
    return _say(FILLERS, language)


def trouble(language: str | None) -> str:
    """What a caller hears when something on our side broke.

    It promises a callback, so it is never spoken on its own: `trouble_reply`
    is the entry point, and it alerts the owner first.
    """
    return _say(TROUBLE, language)


async def trouble_reply(state: CallState) -> VoiceReply:
    """The last resort - the turn itself raised.

    Separate from `escalate` because there may be no transferring our way out
    of this: whatever broke might be the database that `escalate` would need.
    All it does is tell the owner and say one honest sentence to the caller.
    """
    if not state.alerted:
        state.alerted = True
        with suppress(Exception):
            await enqueue_owner_alert(
                state.client_id,
                owner_mod.handoff_alert(
                    state.short_code,
                    "on a live call - Chichi hit an error mid-call",
                    "The caller was told someone would ring them back.",
                    state.customer_phone,
                ),
            )
    return VoiceReply(text=trouble(state.language))


# --------------------------------------------------------------------- call state


@dataclass(slots=True)
class CallState:
    """Everything a turn needs, resolved once when the websocket opens."""

    client_id: uuid.UUID
    retell_call_id: str
    call_row_id: uuid.UUID
    conversation_id: uuid.UUID
    customer_id: uuid.UUID
    customer_phone: str
    shop: ShopContext
    #: The owner's handle for this conversation, so an alert about a live call
    #: can be replied to the same way as any other.
    short_code: str = "?"
    #: Last language we believe the caller is speaking. Seeds the next turn so a
    #: one-word answer ("si") does not reset the call to English.
    language: str | None = None
    turns: int = 0
    transferred: bool = False
    #: One alert per call, however many times a turn escalates. An owner whose
    #: phone buzzes four times for one caller stops reading them.
    alerted: bool = False
    transcript: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class VoiceReply:
    """One spoken turn. Maps onto Retell's `response` event."""

    text: str
    end_call: bool = False
    transfer_number: str | None = None
    #: Carried for logging and for the owner alert, not spoken.
    handoff_reason: str = ""


# ------------------------------------------------------------- the inbound webhook


@dataclass(slots=True)
class InboundDecision:
    """What we tell Retell to do with a call that is still ringing."""

    accept: bool
    reason: str = ""
    client_id: uuid.UUID | None = None
    call_row_id: uuid.UUID | None = None
    dynamic_variables: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    duplicate: bool = False


async def register_inbound_call(
    *, call_id: str, to_number: str, from_number: str
) -> InboundDecision:
    """Resolve the tenant while the phone is still ringing.

    Retell gives us the call id here, before the websocket exists, which is what
    lets the bridge authenticate a connection against a row we wrote rather than
    against metadata handed back to us. It retries this webhook up to three
    times, so it has to be idempotent on the call id.
    """
    async with session_scope() as session:
        endpoint = await get_endpoint(session, Channel.voice, to_number)
        if endpoint is None:
            return InboundDecision(accept=False, reason=f"no shop mapped to {to_number}")

        client_id = endpoint.client_id
        shop = await load_shop(session, client_id)

        if shop.status not in ANSWERABLE:
            # Not a failure - a shop in shadow or paused has deliberately not
            # handed us the phone yet.
            return InboundDecision(
                accept=False,
                reason=f"shop status is {shop.status}, which cannot answer live calls",
                client_id=client_id,
            )

        existing = await call_exists(session, client_id, call_id)
        if existing is not None:
            return InboundDecision(
                accept=True,
                client_id=client_id,
                call_row_id=existing.id,
                duplicate=True,
                dynamic_variables=_dynamic_variables(shop),
                metadata={"client_id": str(client_id), "call_row_id": str(existing.id)},
            )

        customer = await find_or_create_customer(session, client_id, from_number)
        conversation = await find_or_create_conversation(
            session, client_id, customer.id, Channel.voice
        )
        call = await log_call(
            session,
            client_id,
            customer_id=customer.id,
            from_number=from_number,
            provider_call_id=call_id,
            # Optimistic, and corrected at call_ended if it turns out nobody
            # ever spoke. The alternative - logging it missed and upgrading
            # later - leaves a missed call in the dashboard for the duration of
            # every successful call.
            outcome=CallOutcome.answered_ai,
        )
        _ = conversation

        return InboundDecision(
            accept=True,
            client_id=client_id,
            call_row_id=call.id,
            dynamic_variables=_dynamic_variables(shop),
            metadata={"client_id": str(client_id), "call_row_id": str(call.id)},
        )


def _dynamic_variables(shop: ShopContext) -> dict[str, str]:
    """Values Retell can interpolate into its own furniture.

    Kept to things that are already public on the shop's own website. The
    prompt, the prices and the hours stay on our side: shipping them into a
    third party's agent config would be duplicating the one thing that must have
    a single home.
    """
    return {"shop_name": shop.name, "shop_timezone": shop.timezone}


# --------------------------------------------------------------- opening a socket


async def open_call(retell_call_id: str) -> CallState:
    """Resolve a websocket back to the call the inbound webhook registered.

    Raises `CallNotRegistered` for anything we did not write ourselves, which is
    the second half of the bridge's authentication - the first being the secret
    in the URL.
    """
    async with session_scope() as session:
        call = await find_call(session, retell_call_id)
        if call is None:
            raise CallNotRegistered(f"no registered call with id {retell_call_id!r}")

        shop = await load_shop(session, call.client_id)
        customer = (
            await get_customer(session, call.client_id, call.customer_id)
            if call.customer_id
            else None
        )
        if customer is None:
            raise CallNotRegistered(f"call {retell_call_id!r} has no customer")

        conversation = await find_or_create_conversation(
            session, call.client_id, customer.id, Channel.voice
        )

        return CallState(
            client_id=call.client_id,
            retell_call_id=retell_call_id,
            call_row_id=call.id,
            conversation_id=conversation.id,
            customer_id=customer.id,
            customer_phone=customer.phone,
            short_code=conversation.short_code or "?",
            shop=shop,
            language=customer.language or shop.default_customer_language,
        )


async def find_call(session: AsyncSession, retell_call_id: str) -> Call | None:
    """The one lookup here that cannot be client-scoped.

    A websocket arrives carrying nothing but a call id, so resolving the tenant
    *is* the question - the same exemption `get_endpoint` already has for an
    inbound number. `provider_call_id` is globally unique, so this returns at
    most one row, and every query after it is client-scoped again.
    """
    result = await session.execute(
        select(Call).where(Call.provider_call_id == retell_call_id)
    )
    return result.scalar_one_or_none()


# ------------------------------------------------------------------------ a turn


def _history(transcript: Sequence[Mapping[str, Any]]) -> list[Message]:
    """Retell's transcript -> the shape `run_agent` already understands.

    These `Message` objects are deliberately detached: constructed, read, and
    dropped. They are never added to a session, because the authoritative
    transcript is written once at the end of the call. Treat any `session.add`
    of one of these as a bug.
    """
    rows: list[Message] = []
    for entry in transcript:
        role = str(entry.get("role") or "").strip().lower()
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            direction = Direction.inbound
        elif role == "agent":
            direction = Direction.outbound
        else:
            # `transfer_target` and anything Retell adds later: present in the
            # call, but not part of the dialogue Chichi is holding.
            continue
        rows.append(Message(direction=direction, channel=Channel.voice, body=content))
    return rows


def latest_user_text(transcript: Sequence[Mapping[str, Any]]) -> str:
    for entry in reversed(list(transcript)):
        if str(entry.get("role") or "").strip().lower() == "user":
            text = str(entry.get("content") or "").strip()
            if text:
                return text
    return ""


async def answer(
    state: CallState,
    transcript: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> VoiceReply:
    """One caller turn in, one spoken turn out."""
    state.transcript = [dict(entry) for entry in transcript]
    spoken = latest_user_text(transcript)
    if not spoken:
        # Retell asked for a response before the caller said anything usable.
        return VoiceReply(text=nudge(state.language))

    history = _history(transcript)

    async with session_scope() as session:
        conversation = await get_conversation(
            session, state.client_id, state.conversation_id
        )
        if conversation is None:
            log.error("voice turn for a conversation that vanished: %s", state.conversation_id)
            return VoiceReply(text=trouble(state.language), end_call=False)

        if conversation.status is ConversationStatus.human:
            # The owner already took this customer over. Talking at them now
            # would be Chichi arguing with its own shop.
            return await escalate(
                state, reason="the owner has already taken this customer over"
            )

        customer = await get_customer(session, state.client_id, state.customer_id)

        tool_ctx = ToolContext(
            session=session,
            shop=state.shop,
            conversation_id=conversation.id,
            customer_id=state.customer_id,
            now=now,
        )

        outcome = await run_agent(
            shop=state.shop,
            channel=Channel.voice.value,
            history=history,
            customer_text=spoken,
            customer_phone=state.customer_phone,
            stored_language=(customer.language if customer else state.language),
            misunderstand_count=conversation.misunderstand_count,
            tool_ctx=tool_ctx,
            now=now,
        )

        await record_usage(
            session,
            state.client_id,
            type="claude",
            conversation_id=conversation.id,
            model=outcome.model,
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
            cache_read_input_tokens=outcome.usage.cache_read_input_tokens,
            cache_creation_input_tokens=outcome.usage.cache_creation_input_tokens,
            cost=outcome.usage.cost,
        )

        state.turns += 1

        if outcome.reply is None:
            log.error("voice turn failed on call %s: %s", state.retell_call_id, outcome.error)
            return await escalate(
                state,
                reason="the assistant could not answer",
                summary=f"Chichi could not answer on a live call: {outcome.error}",
            )

        reply = outcome.reply

        # Remember the language for the canned lines, and persist a confident
        # detection on exactly the same terms as the text engine - one threshold
        # for the whole product, or a caller's language would depend on whether
        # they rang or texted.
        detected = lang.normalise(reply.detected_language)
        if detected and detected != lang.UNDETERMINED:
            state.language = detected
            threshold = get_settings().language_confidence_threshold
            if customer is not None and reply.language_confidence >= threshold:
                await update_customer_language(
                    session,
                    state.client_id,
                    customer.id,
                    detected,
                    reply.language_confidence,
                    LanguageSource.detected,
                )

        await set_counters(
            session,
            state.client_id,
            conversation.id,
            misunderstand_count=(
                0 if reply.understood else conversation.misunderstand_count + 1
            ),
        )

        if outcome.handoff_requested or reply.handoff:
            reason = outcome.handoff_reason or reply.handoff_reason or "handoff requested"
            await hand_off(session, state.client_id, conversation.id, reason)
            return await escalate(
                state,
                reason=reason,
                spoken_already=reply.reply,
                summary=outcome.handoff_summary or reply.reply,
            )

        return VoiceReply(text=reply.reply)


async def escalate(
    state: CallState,
    *,
    reason: str,
    spoken_already: str = "",
    summary: str = "",
) -> VoiceReply:
    """Hand the caller to a human, by warm transfer if we can.

    A transfer is strictly better than a promise, so it is tried first.

    The owner is told either way, and that is the point of doing it here rather
    than at each call site. On a transfer the alert is context for a call they
    are about to answer cold; without one it is the thing that makes "someone
    will ring you back" true rather than a sentence that ended a call. Every
    path that escalates goes through this function, so neither can be forgotten.
    """
    if not state.alerted:
        state.alerted = True
        await enqueue_owner_alert(
            state.client_id,
            owner_mod.handoff_alert(
                state.short_code,
                f"on a live call - {reason}",
                summary or spoken_already or reason,
                state.customer_phone,
            ),
        )

    owner_number = (state.shop.owner_phone or "").strip()
    if owner_number and not state.transferred:
        state.transferred = True
        # The agent's own wording first if it had any - it is in the caller's
        # language and fits the conversation - then the handover happens anyway.
        text = spoken_already or _say(TRANSFERRING, state.language)
        return VoiceReply(
            text=text,
            transfer_number=owner_number,
            handoff_reason=reason,
        )

    # Nobody to transfer to, so the caller is owed a commitment - and it is
    # appended rather than substituted, because the agent may have said
    # something apt without actually promising anything. A promise that falls
    # back to English is still better than a caller who was told nothing.
    promise = _say(PROMISE_CALLBACK, state.language)
    text = f"{spoken_already.rstrip()} {promise}".strip() if spoken_already else promise
    return VoiceReply(text=text, handoff_reason=reason)


# ------------------------------------------------------------ writing it all down


async def persist_transcript(
    client_id: uuid.UUID,
    retell_call_id: str,
    transcript: Sequence[Mapping[str, Any]],
    *,
    conversation_id: uuid.UUID | None = None,
    language: str | None = None,
) -> int:
    """Write the call down, without writing it twice.

    Both the websocket closing and the `call_ended` webhook call this, because
    either may be the last thing that happens: a dropped socket still has to
    produce a record, and a webhook arriving after a clean close must not
    produce a second one.

    Each turn is keyed `<call_id>:t<index>`, and only the indices not already
    present are written. The obvious cheaper design - one marker row meaning
    "this call is done" - was tried and was wrong: whichever writer gets there
    first is often the one with *fewer* turns, and it would lock the shorter
    version in. That cost the final thing Chichi said on every call, which is
    the turn an owner most wants to read.

    The trade that remains: where a turn is already stored, Retell's later
    correction of its wording is not applied. Better text for a turn we have
    beats nothing for a turn we do not.

    `language` is the call's language rather than each turn's. Speech has no
    per-utterance detection to draw on the way a text message does, and the
    dashboard showing "Spanish" against a Spanish call is worth more than it
    showing nothing at all.
    """
    rows = _history(transcript)
    if not rows:
        return 0

    async with session_scope() as session:
        if conversation_id is None:
            call = await find_call(session, retell_call_id)
            if call is None or call.customer_id is None:
                log.warning("cannot persist transcript: call %s is unknown", retell_call_id)
                return 0
            conversation = await find_or_create_conversation(
                session, client_id, call.customer_id, Channel.voice
            )
            conversation_id = conversation.id

        written = 0
        for index, row in enumerate(rows):
            provider_msg_id = f"{retell_call_id}:t{index}"
            if await message_exists(session, provider_msg_id):
                continue
            await add_message(
                session,
                client_id,
                conversation_id,
                direction=row.direction,
                channel=Channel.voice,
                transcript=row.body,
                # `body` too: every reader in the dashboard and the daily
                # summary reads `body`, and a transcript nobody renders is a
                # transcript nobody has.
                body=row.body,
                status=(
                    MessageStatus.received
                    if row.direction is Direction.inbound
                    else MessageStatus.sent
                ),
                provider_msg_id=provider_msg_id,
                detected_language=lang.normalise(language),
            )
            written += 1
        return written
