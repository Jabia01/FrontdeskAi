"""Owner commands.

The owner controls the bot by replying to it on WhatsApp or SMS - no dashboard
required. Everything they can send:

    OK                  confirm the booking, or send the shadow-mode draft
    CHANGE              flag a booking that will not work
    READY <item>        mark the job ready; the customer is told in their language
    RESUME              hand the conversation back to the AI
    HELP                list the commands
    <anything else>     relayed to the customer, translated into their language

`<item>` is whatever the business works on, and differs by trade: a garage types
`READY 2014 Civic`, a groomer `READY Bella`, a repair shop `READY iPhone 13`.
The word the owner is shown comes from `client_config.job_noun`.

Any of these may be prefixed with a conversation's short code to address one
chat out of several: "K7Q2 OK", "B3K9 we need another day".

The parser is pure and the handler is async, so the grammar is testable without
a database.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.language import normalise
from app.agent.translate import translate
from app.core.delivery import deliver_existing, send_reply
from app.core.router import ShopContext
from app.models import ConversationStatus, JobStatus, MessageStatus
from app.repo import (
    confirm_appointment,
    conversation_by_short_code,
    find_job_by_item,
    get_conversation,
    get_customer,
    latest_handoff_conversation,
    latest_pending_draft,
    latest_unconfirmed_appointment,
    mark_message_status,
    pending_draft,
    set_conversation_status,
    upsert_job,
)

log = logging.getLogger(__name__)

#: A leading token is only a conversation code if it is 4-8 characters from the
#: code alphabet *and* mixes letters with digits. Without the mix requirement a
#: message like "2014 Civic is done" would lose its first word to a bogus code.
CODE_RE = re.compile(
    r"^(?=.*[BCDFGHJKLMNPQRSTVWXZ])(?=.*[2-9])[0-9BCDFGHJKLMNPQRSTVWXZ]{4,8}$"
)

READY_TEMPLATE = "Your {item} is ready for collection at {business}. Thank you!"

#: Only used to make the "which one?" question concrete in the owner's own
#: trade. `client_config.job_noun` chooses the row.
EXAMPLES = {
    "vehicle": "2014 Civic",
    "car": "2014 Civic",
    "pet": "Bella",
    "device": "iPhone 13",
    "bike": "Trek 520",
    "garment": "navy suit",
    "order": "order 1482",
    "job": "job 1482",
}


def job_noun(shop: ShopContext) -> str:
    """What this business calls the thing a job is about."""
    return (shop.config.get("job_noun") or "job").strip() or "job"


class Kind(str, Enum):
    ok = "ok"
    change = "change"
    ready = "ready"
    resume = "resume"
    help = "help"
    reply = "reply"


KEYWORDS = {
    "OK": Kind.ok,
    "OKAY": Kind.ok,
    "YES": Kind.ok,
    "SEND": Kind.ok,
    "CHANGE": Kind.change,
    "NO": Kind.change,
    "READY": Kind.ready,
    "RESUME": Kind.resume,
    "AI": Kind.resume,
    "HELP": Kind.help,
    "COMMANDS": Kind.help,
}


@dataclass(frozen=True, slots=True)
class OwnerCommand:
    kind: Kind
    code: str | None
    payload: str
    raw: str


def parse(text: str) -> OwnerCommand:
    """Grammar only - the code is validated against the database by the handler."""
    raw = (text or "").strip()
    tokens = raw.split()
    if not tokens:
        return OwnerCommand(Kind.help, None, "", raw)

    code: str | None = None
    if len(tokens) > 1 and CODE_RE.match(tokens[0].upper()):
        # Only treat it as a code if a command or message follows it.
        code = tokens[0].upper()
        tokens = tokens[1:]

    keyword = KEYWORDS.get(tokens[0].upper().strip(".!?"))
    if keyword is not None:
        return OwnerCommand(keyword, code, " ".join(tokens[1:]).strip(), raw)

    # Not a keyword, so the owner is writing to the customer.
    return OwnerCommand(Kind.reply, code, " ".join(tokens).strip(), raw)


def help_text(shop: ShopContext) -> str:
    """Rendered per business, so the owner reads their own vocabulary."""
    noun = job_noun(shop)
    return (
        "I can take these:\n"
        "OK - confirm a booking or send my draft\n"
        "CHANGE - that booking will not work\n"
        f"READY <{noun}> - tell the customer theirs is ready\n"
        "RESUME - hand the chat back to me\n"
        "Anything else you write is sent to the customer, translated into "
        "their language. Put the code first to pick a chat, e.g. "
        "\"K7Q2 on my way\"."
    )


async def _resolve_conversation(
    session: AsyncSession, shop: ShopContext, command: OwnerCommand
):
    """The chat the owner means: the code if valid, else the latest handoff."""
    if command.code:
        conversation = await conversation_by_short_code(
            session, shop.client_id, command.code
        )
        if conversation is not None:
            return conversation, True
    return await latest_handoff_conversation(session, shop.client_id), False


async def _normalise_command(
    session: AsyncSession, shop: ShopContext, command: OwnerCommand
) -> OwnerCommand:
    """Undo code-stripping when the code turns out not to exist.

    `parse` cannot know which codes are real. If it stripped a leading token that
    is not a live conversation, the words belong to the message after all - so
    the command is re-parsed from the raw text with no code.
    """
    if command.code is None:
        return command
    if (
        await conversation_by_short_code(session, shop.client_id, command.code)
        is not None
    ):
        return command

    log.info("owner used unknown code %s - treating it as message text", command.code)
    tokens = command.raw.split()
    keyword = KEYWORDS.get(tokens[0].upper().strip(".!?")) if tokens else None
    if keyword is not None:
        return OwnerCommand(
            keyword, None, " ".join(tokens[1:]).strip(), command.raw
        )
    return OwnerCommand(Kind.reply, None, command.raw.strip(), command.raw)


async def handle(
    session: AsyncSession, shop: ShopContext, text: str
) -> dict:
    """Run one owner command. Returns a dict for logging plus `reply_to_owner`."""
    command = await _normalise_command(session, shop, parse(text))
    log.info(
        "owner command %s code=%s for client %s",
        command.kind.value,
        command.code,
        shop.client_id,
    )

    if command.kind is Kind.help:
        return {"status": "help", "reply_to_owner": help_text(shop)}

    if command.kind is Kind.ready:
        return await _handle_ready(session, shop, command)

    if command.kind is Kind.ok:
        return await _handle_ok(session, shop, command)

    if command.kind is Kind.change:
        return await _handle_change(session, shop, command)

    if command.kind is Kind.resume:
        return await _handle_resume(session, shop, command)

    return await _handle_reply(session, shop, command)


# --------------------------------------------------------------------- handlers


async def _handle_ready(
    session: AsyncSession, shop: ShopContext, command: OwnerCommand
) -> dict:
    item = command.payload.strip()
    if not item:
        noun = job_noun(shop)
        return {
            "status": "ready_needs_item",
            "reply_to_owner": (
                f'Which {noun}? Reply like "READY '
                f'{EXAMPLES.get(noun, "job 1482")}".'
            ),
        }

    job = await find_job_by_item(session, shop.client_id, item)
    if job is None or job.customer_id is None:
        return {
            "status": "ready_no_job",
            "reply_to_owner": (
                f'I have no job on file for "{item}", so I do not know who to '
                f"text. Check the spelling, or reply with the chat code and your "
                f"message to write to them directly."
            ),
        }

    await upsert_job(
        session,
        shop.client_id,
        job.customer_id,
        job.item or item,
        JobStatus.ready,
        updated_by="owner",
    )

    customer = await get_customer(session, shop.client_id, job.customer_id)
    conversation = await _customer_conversation(session, shop, customer.id)

    if conversation is None:
        return {
            "status": "ready_no_conversation",
            "reply_to_owner": (
                f"Marked the {job.item} ready, but I have no chat with that "
                f"customer to text."
            ),
        }

    english = READY_TEMPLATE.format(item=job.item or item, business=shop.name)
    body, _ = await translate(
        english,
        to_language=customer.language or shop.default_customer_language,
        from_language="en",
    )
    result, _ = await send_reply(
        session,
        client_id=shop.client_id,
        conversation=conversation,
        to_number=customer.phone,
        body=body,
        channel=conversation.channel,
        detected_language=normalise(customer.language),
    )
    if result.blocked:
        return {
            "status": "ready_blocked",
            "reply_to_owner": (
                f"Marked the {job.item} ready, but I could not text them: "
                f"the 24-hour WhatsApp window has closed and no approved "
                f'"ready" template is configured yet. Please call them.'
            ),
        }
    return {
        "status": "ready_sent",
        "reply_to_owner": f"Done - told them the {job.item} is ready.",
    }


async def _customer_conversation(
    session: AsyncSession, shop: ShopContext, customer_id: uuid.UUID
):
    """The customer's most recent chat, preferring WhatsApp then SMS."""
    from app.models import Channel
    from app.repo import latest_conversation

    for channel in (Channel.whatsapp, Channel.sms, Channel.simulator):
        found = await latest_conversation(
            session, shop.client_id, customer_id, channel
        )
        if found is not None:
            return found
    return None


async def _handle_ok(
    session: AsyncSession, shop: ShopContext, command: OwnerCommand
) -> dict:
    """OK means either 'send my draft' (shadow mode) or 'confirm that booking'."""
    conversation, by_code = await _resolve_conversation(session, shop, command)

    draft = None
    if conversation is not None:
        draft = await pending_draft(session, shop.client_id, conversation.id)
    if draft is None and not by_code:
        draft = await latest_pending_draft(session, shop.client_id)

    if draft is not None:
        target = await get_conversation(
            session, shop.client_id, draft.conversation_id
        )
        customer = await get_customer(session, shop.client_id, target.customer_id)
        # The draft row *is* the message - sending it in place avoids a second
        # outbound row for one delivered reply.
        result = await deliver_existing(
            session,
            client_id=shop.client_id,
            message=draft,
            conversation=target,
            to_number=customer.phone,
        )
        return {
            "status": "draft_sent" if result.ok else "draft_send_failed",
            "reply_to_owner": (
                "Sent." if result.ok else f"Could not send it: {result.error}"
            ),
        }

    appointment = await latest_unconfirmed_appointment(session, shop.client_id)
    if appointment is not None:
        await confirm_appointment(session, shop.client_id, appointment.id)
        return {
            "status": "booking_confirmed",
            "reply_to_owner": "Confirmed. It stays on the calendar.",
        }

    return {
        "status": "nothing_to_confirm",
        "reply_to_owner": "There is nothing waiting for an OK right now.",
    }


async def _handle_change(
    session: AsyncSession, shop: ShopContext, command: OwnerCommand
) -> dict:
    appointment = await latest_unconfirmed_appointment(session, shop.client_id)
    if appointment is None:
        return {
            "status": "nothing_to_change",
            "reply_to_owner": "I have no unconfirmed booking to change.",
        }

    appointment.status = "needs_change"
    conversation = (
        await get_conversation(session, shop.client_id, appointment.conversation_id)
        if appointment.conversation_id
        else None
    )
    if conversation is not None:
        await set_conversation_status(
            session, shop.client_id, conversation.id, ConversationStatus.human
        )
    return {
        "status": "booking_flagged",
        "reply_to_owner": (
            "Flagged. I have paused that chat - reply with the code and your "
            "message to offer them another time."
        ),
    }


async def _handle_resume(
    session: AsyncSession, shop: ShopContext, command: OwnerCommand
) -> dict:
    conversation, _ = await _resolve_conversation(session, shop, command)
    if conversation is None:
        return {
            "status": "nothing_to_resume",
            "reply_to_owner": "I am not holding any chat for you.",
        }
    await set_conversation_status(
        session, shop.client_id, conversation.id, ConversationStatus.ai
    )
    return {
        "status": "resumed",
        "reply_to_owner": (
            f"Taken back over chat {conversation.short_code}. I will answer them "
            f"from here."
        ),
    }


async def _handle_reply(
    session: AsyncSession, shop: ShopContext, command: OwnerCommand
) -> dict:
    """Relay the owner's words to the customer, in the customer's language."""
    conversation, _ = await _resolve_conversation(session, shop, command)
    if conversation is None:
        return {
            "status": "no_target",
            "reply_to_owner": (
                "I am not sure who that is for. Put the chat code first, like "
                '"K7Q2 we can take it tomorrow".'
            ),
        }
    if not command.payload.strip():
        return {"status": "empty", "reply_to_owner": help_text(shop)}

    customer = await get_customer(session, shop.client_id, conversation.customer_id)

    # The owner wrote their own wording, so any pending draft is superseded -
    # otherwise a later bare "OK" would send stale text to the customer.
    superseded = await pending_draft(session, shop.client_id, conversation.id)
    if superseded is not None:
        await mark_message_status(
            session, shop.client_id, superseded.id, MessageStatus.rejected
        )

    target_language = customer.language or shop.default_customer_language
    body, translated = await translate(
        command.payload,
        to_language=target_language,
        from_language=shop.owner_language,
    )

    result, _ = await send_reply(
        session,
        client_id=shop.client_id,
        conversation=conversation,
        to_number=customer.phone,
        body=body,
        channel=conversation.channel,
        detected_language=normalise(target_language),
    )
    # The owner is now driving this conversation.
    await set_conversation_status(
        session, shop.client_id, conversation.id, ConversationStatus.human
    )

    if result.blocked:
        return {
            "status": "relay_blocked",
            "reply_to_owner": (
                "I could not send that: the 24-hour WhatsApp window has closed "
                "for this customer. Please call them."
            ),
        }
    if not result.ok:
        return {
            "status": "relay_failed",
            "reply_to_owner": f"That did not send: {result.error}",
        }

    note = (
        f"Sent to {customer.phone}"
        + (f" in {target_language}." if translated else ".")
    )
    return {"status": "relayed", "reply_to_owner": note, "translated": translated}


def is_owner_number(shop: ShopContext, phone: str) -> bool:
    owner = (shop.owner_phone or "").strip()
    return bool(owner) and owner == (phone or "").strip()
