"""Inbound ingest: the fast path that must finish before the provider times out.

Resolve the tenant, dedupe, persist, enqueue - no Claude call, no outbound send.
Everything slow happens in the worker.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.db import session_scope
from app.models import Channel, Direction, MessageStatus
from app.repo import (
    add_message,
    find_or_create_conversation,
    find_or_create_customer,
    get_endpoint,
    message_exists,
    touch_window,
)
from app.schemas import InboundMessage

log = logging.getLogger(__name__)


class UnknownEndpointError(Exception):
    """The receiving number is not mapped to any shop."""


@dataclass(slots=True)
class IngestResult:
    client_id: uuid.UUID
    conversation_id: uuid.UUID
    customer_id: uuid.UUID
    message_id: uuid.UUID | None
    duplicate: bool


async def ingest(inbound: InboundMessage) -> IngestResult:
    """Persist an inbound message. Idempotent on `provider_msg_id`."""
    async with session_scope() as session:
        endpoint = await get_endpoint(session, inbound.channel, inbound.external_id)
        if endpoint is None:
            raise UnknownEndpointError(
                f"no shop mapped to {inbound.channel.value}:{inbound.external_id}"
            )

        client_id = endpoint.client_id

        customer = await find_or_create_customer(
            session, client_id, inbound.from_number
        )
        conversation = await find_or_create_conversation(
            session, client_id, customer.id, inbound.channel
        )

        # Dedupe after the tenant is known so the lookup stays cheap and the
        # duplicate still resolves to a conversation for logging.
        if inbound.provider_msg_id and await message_exists(
            session, inbound.provider_msg_id
        ):
            log.info(
                "duplicate webhook ignored: %s", inbound.provider_msg_id
            )
            return IngestResult(
                client_id=client_id,
                conversation_id=conversation.id,
                customer_id=customer.id,
                message_id=None,
                duplicate=True,
            )

        message = await add_message(
            session,
            client_id,
            conversation.id,
            direction=Direction.inbound,
            channel=inbound.channel,
            body=inbound.body,
            media_url=inbound.media_url,
            transcript=inbound.transcript,
            provider_msg_id=inbound.provider_msg_id,
            status=MessageStatus.received,
        )

        # A customer message reopens the WhatsApp free-form window.
        await touch_window(session, client_id, conversation.id)

        return IngestResult(
            client_id=client_id,
            conversation_id=conversation.id,
            customer_id=customer.id,
            message_id=message.id,
            duplicate=False,
        )


def channel_from_path(name: str) -> Channel:
    try:
        return Channel(name)
    except ValueError:
        raise LookupError(f"unknown channel {name!r}") from None
