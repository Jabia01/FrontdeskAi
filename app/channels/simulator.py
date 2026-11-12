"""Local simulator channel.

Exists so the whole loop - gateway, router, engine, agent, delivery - can be
exercised with nothing but an Anthropic API key. No Twilio, no Meta, no tunnel.

Delivery is the persisted outbound `messages` row, not an in-process queue: the
worker and the web process are separate processes, so anything held in memory
here would be invisible to the /sim page. The poll endpoint reads the database.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from app.channels.base import register
from app.models import Channel
from app.schemas import InboundMessage, OutboundMessage, SendResult

log = logging.getLogger(__name__)


class SimulatorAdapter:
    channel = Channel.simulator
    provider = "local"
    enforces_free_form_window = False

    def parse_inbound(self, payload: Mapping[str, str]) -> InboundMessage:
        external_id = (payload.get("external_id") or "").strip()
        from_number = (payload.get("from_number") or "").strip()
        if not external_id:
            raise ValueError("external_id is required")
        if not from_number:
            raise ValueError("from_number is required")

        return InboundMessage(
            channel=self.channel,
            provider=self.provider,
            external_id=external_id,
            from_number=from_number,
            body=payload.get("body") or None,
            media_url=payload.get("media_url") or None,
            # Ours to mint, but an explicit one lets the dedupe path be driven
            # from the UI.
            provider_msg_id=(payload.get("provider_msg_id") or f"sim-{uuid.uuid4()}"),
            raw=dict(payload),
        )

    async def send(self, message: OutboundMessage) -> SendResult:
        provider_msg_id = f"sim-out-{uuid.uuid4()}"
        log.info("[sim] -> %s: %s", message.to_number, message.body)
        return SendResult(ok=True, provider_msg_id=provider_msg_id)


adapter = register(SimulatorAdapter())
