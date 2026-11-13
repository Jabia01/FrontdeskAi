"""Retell voice adapter.

The `voice` channel is the one that does not fit the send-a-message shape, and
pretending otherwise would be the bug. A spoken reply is not delivered by an API
call we make later - it goes back down the websocket Retell already has open, in
the same breath as the question. So `parse_inbound` is real and `send` refuses.

That refusal is deliberate and typed: `blocked`, not `failed`. `blocked` means
"a channel policy says no", which is exactly true here, and it is the status the
rest of the system already knows how to surface to an owner instead of retrying
forever.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from app.channels.base import register
from app.models import Channel
from app.schemas import InboundMessage, OutboundMessage, SendResult

log = logging.getLogger(__name__)


class RetellVoiceAdapter:
    channel = Channel.voice
    provider = "retell"
    enforces_free_form_window = False

    def parse_inbound(self, payload: Mapping[str, str]) -> InboundMessage:
        """Normalise Retell's inbound-call webhook into our shape.

        This runs before the call connects, when there is no speech yet, so
        `transcript` is empty by definition. What matters is that it resolves
        the number dialled - that is the tenant - and the call id, which is the
        only thread tying the webhook to the websocket that follows it.
        """
        to = (payload.get("to_number") or "").strip()
        from_ = (payload.get("from_number") or "").strip()
        call_id = (payload.get("call_id") or "").strip()

        if not to:
            raise ValueError("Retell payload missing to_number")
        if not from_:
            raise ValueError("Retell payload missing from_number")
        if not call_id:
            # Without it the websocket cannot be tied back to this shop, and a
            # call we cannot attribute is worse than a call we decline.
            raise ValueError("Retell payload missing call_id")

        return InboundMessage(
            channel=self.channel,
            provider=self.provider,
            external_id=to,
            from_number=from_,
            body=None,
            provider_msg_id=call_id,
            raw=dict(payload),
        )

    async def send(self, message: OutboundMessage) -> SendResult:
        """Always refuses. A voice reply is spoken in-band, not sent.

        Reached only by a misconfiguration - a shop with `missed_call_channel`
        set to voice, say - and the point of answering loudly here is that such
        a shop shows up in the dashboard as blocked rather than as silently
        fine.
        """
        log.warning(
            "refusing an out-of-band send on the voice channel to %s: voice replies "
            "are spoken over the live websocket, so there is nothing to deliver",
            message.to_number,
        )
        return SendResult(
            ok=False,
            blocked=True,
            error=(
                "the voice channel cannot send a message outside a live call - "
                "pick sms or whatsapp for this"
            ),
        )


adapter = register(RetellVoiceAdapter())
