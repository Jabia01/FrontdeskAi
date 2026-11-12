"""Owner notifications.

The owner lives on WhatsApp, but WhatsApp's 24-hour rule applies to them too: we
can only send free-form once they have messaged us recently. So `owner_channel`
defaults to `auto` - WhatsApp while their window is open, SMS otherwise. A shop
can pin it to one channel in config.

Every alert carries the conversation's short code, which is how the owner
addresses one chat out of several when replying.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import get_adapter
from app.core.router import ShopContext
from app.models import Channel, Conversation, Direction, MessageStatus
from app.repo import (
    add_message,
    find_or_create_conversation,
    find_or_create_owner,
    get_client_endpoint,
    mark_message_status,
)
from app.schemas import OutboundMessage

log = logging.getLogger(__name__)


async def _pick_channel(
    session: AsyncSession, shop: ShopContext, owner_conversations: dict
) -> tuple[Channel, Conversation] | None:
    """WhatsApp while the owner's window is open, else SMS."""
    preference = (shop.config.get("owner_channel") or "auto").lower()
    order: list[Channel]
    if preference == "whatsapp":
        order = [Channel.whatsapp, Channel.sms]
    elif preference == "sms":
        order = [Channel.sms, Channel.whatsapp]
    else:
        order = [Channel.whatsapp, Channel.sms]

    now = datetime.now(UTC)
    for channel in order:
        conversation = owner_conversations.get(channel)
        if conversation is None:
            continue
        if await get_client_endpoint(session, shop.client_id, channel) is None:
            continue
        if channel is Channel.whatsapp and preference == "auto":
            expires = conversation.window_expires_at
            if expires is None:
                continue
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= now:
                continue  # window closed - fall through to SMS
        return channel, conversation
    return None


async def send_to_owner(
    session: AsyncSession, shop: ShopContext, body: str
) -> tuple[bool, str]:
    """Deliver a message to the shop owner. Returns (ok, detail)."""
    if not shop.owner_phone:
        log.warning("client %s has no owner_phone", shop.client_id)
        return False, "no owner phone configured"

    owner = await find_or_create_owner(session, shop.client_id, shop.owner_phone)

    conversations: dict[Channel, Conversation] = {}
    for channel in (Channel.whatsapp, Channel.sms, Channel.simulator):
        if await get_client_endpoint(session, shop.client_id, channel) is None:
            continue
        conversations[channel] = await find_or_create_conversation(
            session, shop.client_id, owner.id, channel
        )

    chosen = await _pick_channel(session, shop, conversations)
    if chosen is None:
        # Local dev has only the simulator; use it rather than dropping the alert.
        if Channel.simulator in conversations:
            chosen = (Channel.simulator, conversations[Channel.simulator])
        else:
            log.error("no usable owner channel for client %s", shop.client_id)
            return False, "no usable owner channel"

    channel, conversation = chosen
    endpoint = await get_client_endpoint(session, shop.client_id, channel)

    message = await add_message(
        session,
        shop.client_id,
        conversation.id,
        direction=Direction.outbound,
        channel=channel,
        body=body,
        status=MessageStatus.queued,
    )
    result = await get_adapter(channel).send(
        OutboundMessage(
            channel=channel,
            provider=endpoint.provider,
            external_id=endpoint.external_id,
            to_number=shop.owner_phone,
            body=body,
            window_expires_at=conversation.window_expires_at,
        )
    )
    await mark_message_status(
        session,
        shop.client_id,
        message.id,
        MessageStatus.sent
        if result.ok
        else (MessageStatus.blocked if result.blocked else MessageStatus.failed),
        provider_msg_id=result.provider_msg_id,
        error=result.error,
    )
    if not result.ok:
        log.error(
            "owner alert not delivered on %s: %s", channel.value, result.error
        )
    return result.ok, result.error or channel.value


# ----------------------------------------------------------------- composition


def handoff_alert(
    code: str, reason: str, summary: str, customer: str
) -> str:
    return (
        f"[{code}] Needs you - {reason}\n"
        f"From: {customer}\n"
        f"{summary}\n\n"
        f"Reply \"{code} <your message>\" and I will send it to them in their "
        f"language. Reply \"{code} RESUME\" to hand it back to me."
    )


def booking_alert(code: str, summary: str) -> str:
    return (
        f"[{code}] New booking\n"
        f"{summary}\n\n"
        f'Reply "{code} OK" to confirm, or "{code} CHANGE" if it will not work.'
    )


def draft_alert(code: str, customer_text: str, draft: str) -> str:
    return (
        f"[{code}] Shadow mode - draft reply\n"
        f"Customer: {customer_text}\n\n"
        f"My draft: {draft}\n\n"
        f'Reply "{code} OK" to send it, or "{code} <your own wording>" to send '
        f"yours instead."
    )


def missed_call_alert(code: str, from_number: str) -> str:
    return (
        f"[{code}] Missed call from {from_number} - I have texted them to ask "
        f"what they need. I will let you know what they say."
    )
