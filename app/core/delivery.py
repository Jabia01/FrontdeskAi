"""Outbound delivery.

Persists the outbound row first, then asks the adapter to send, then records what
happened. A policy refusal (the WhatsApp 24-hour window with no approved
template) is marked `blocked` and surfaced to the caller so the owner can be
alerted - never dropped silently.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import get_adapter
from app.models import Channel, Conversation, Direction, MessageStatus
from app.repo import add_message, get_client_endpoint, mark_message_status
from app.schemas import OutboundMessage, SendResult

log = logging.getLogger(__name__)


class NoEndpointError(Exception):
    """The shop has no active endpoint to send from on this channel."""


async def deliver_existing(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    message,
    conversation: Conversation,
    to_number: str,
    template_name: str | None = None,
) -> SendResult:
    """Send a message row that already exists, updating it in place.

    Used when the owner approves a shadow-mode draft: the draft *is* the message,
    so persisting a second row would double-count it in the conversation and in
    the owner's view of what the customer received.
    """
    endpoint = await get_client_endpoint(session, client_id, message.channel)
    if endpoint is None:
        raise NoEndpointError(
            f"client {client_id} has no active {message.channel.value} endpoint"
        )

    result = await get_adapter(message.channel).send(
        OutboundMessage(
            channel=message.channel,
            provider=endpoint.provider,
            external_id=endpoint.external_id,
            to_number=to_number,
            body=message.body or "",
            window_expires_at=conversation.window_expires_at,
            template_name=template_name,
        )
    )

    if result.ok:
        status = MessageStatus.sent
    elif result.blocked:
        status = MessageStatus.blocked
    else:
        status = MessageStatus.failed

    await mark_message_status(
        session,
        client_id,
        message.id,
        status,
        provider_msg_id=result.provider_msg_id,
        error=result.error,
    )
    return result


async def send_reply(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    conversation: Conversation,
    to_number: str,
    body: str,
    channel: Channel,
    detected_language: str | None = None,
    template_name: str | None = None,
) -> tuple[SendResult, uuid.UUID]:
    endpoint = await get_client_endpoint(session, client_id, channel)
    if endpoint is None:
        raise NoEndpointError(
            f"client {client_id} has no active {channel.value} endpoint"
        )

    message = await add_message(
        session,
        client_id,
        conversation.id,
        direction=Direction.outbound,
        channel=channel,
        body=body,
        status=MessageStatus.queued,
        detected_language=detected_language,
    )

    adapter = get_adapter(channel)
    outbound = OutboundMessage(
        channel=channel,
        provider=endpoint.provider,
        external_id=endpoint.external_id,
        to_number=to_number,
        body=body,
        window_expires_at=conversation.window_expires_at,
        template_name=template_name,
    )

    result = await adapter.send(outbound)

    if result.ok:
        status = MessageStatus.sent
    elif result.blocked:
        status = MessageStatus.blocked
    else:
        status = MessageStatus.failed

    await mark_message_status(
        session,
        client_id,
        message.id,
        status,
        provider_msg_id=result.provider_msg_id,
        error=result.error,
    )

    if not result.ok:
        log.warning(
            "send %s on %s: %s",
            "blocked" if result.blocked else "failed",
            channel.value,
            result.error,
        )

    return result, message.id
