"""Shared Twilio inbound parsing and outbound sending.

Phase 1 reality: no Twilio account yet, so when credentials are absent `send()`
logs the message and returns a synthetic id instead of calling the API. The
parse path is fully real and fixture-tested, because that is the half that has
to be right before a real number is pointed at us.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from app.config import get_settings
from app.models import Channel
from app.schemas import InboundMessage, OutboundMessage, SendResult

log = logging.getLogger(__name__)

WHATSAPP_PREFIX = "whatsapp:"

# Stubbed sends are kept so tests and the admin console can assert on them.
STUB_SENDS: list[dict] = []


def strip_channel_prefix(value: str) -> str:
    """`whatsapp:+1312...` -> `+1312...`

    Phone is the customer's identity across channels, so the stored number must
    not carry a channel prefix.
    """
    value = (value or "").strip()
    if value.lower().startswith(WHATSAPP_PREFIX):
        return value[len(WHATSAPP_PREFIX) :].strip()
    return value


class TwilioAdapterBase:
    channel: Channel
    provider = "twilio"
    #: True for WhatsApp, where free-form sends expire after 24 hours.
    enforces_free_form_window = False

    def parse_inbound(self, payload: Mapping[str, str]) -> InboundMessage:
        to = (payload.get("To") or "").strip()
        from_ = (payload.get("From") or "").strip()
        sid = (payload.get("MessageSid") or payload.get("SmsMessageSid") or "").strip()

        if not to:
            raise ValueError("Twilio payload missing To")
        if not from_:
            raise ValueError("Twilio payload missing From")

        media_url = None
        try:
            if int(payload.get("NumMedia") or 0) > 0:
                media_url = payload.get("MediaUrl0") or None
        except (TypeError, ValueError):
            media_url = payload.get("MediaUrl0") or None

        return InboundMessage(
            channel=self.channel,
            provider=self.provider,
            # Matched verbatim against channel_endpoints.external_id, so the
            # WhatsApp "whatsapp:" prefix is deliberately preserved here.
            external_id=to,
            from_number=strip_channel_prefix(from_),
            body=payload.get("Body") or None,
            media_url=media_url,
            provider_msg_id=sid or None,
            raw=dict(payload),
        )

    # ------------------------------------------------------------------ outbound

    def _wire_number(self, number: str) -> str:
        """Render a bare E.164 number into this channel's address form."""
        return number

    def _check_policy(self, message: OutboundMessage) -> SendResult | None:
        """Channel policy gate. Return a blocked result to refuse the send."""
        return None

    async def send(self, message: OutboundMessage) -> SendResult:
        refusal = self._check_policy(message)
        if refusal is not None:
            return refusal

        settings = get_settings()
        to = self._wire_number(message.to_number)
        from_ = self._wire_number(strip_channel_prefix(message.external_id))
        segments = max(1, (len(message.body) + 159) // 160)

        if not settings.twilio_configured:
            provider_msg_id = f"stub-{uuid.uuid4()}"
            STUB_SENDS.append(
                {
                    "provider_msg_id": provider_msg_id,
                    "channel": self.channel.value,
                    "from": from_,
                    "to": to,
                    "body": message.body,
                    "template_name": message.template_name,
                }
            )
            log.info(
                "[twilio:stub] %s %s -> %s: %s",
                self.channel.value,
                from_,
                to,
                message.body,
            )
            return SendResult(
                ok=True, provider_msg_id=provider_msg_id, segments=segments
            )

        try:
            from twilio.rest import Client as TwilioClient

            client = TwilioClient(
                settings.twilio_account_sid, settings.twilio_auth_token
            )
            kwargs: dict = {"from_": from_, "to": to, "body": message.body}
            if message.media_url:
                kwargs["media_url"] = [message.media_url]
            sent = await _to_thread(client.messages.create, **kwargs)
            return SendResult(ok=True, provider_msg_id=sent.sid, segments=segments)
        except Exception as exc:  # transport failure - the caller decides on retry
            log.warning("twilio send failed on %s: %s", self.channel.value, exc)
            return SendResult(ok=False, error=str(exc)[:500], segments=segments)


async def _to_thread(fn, /, *args, **kwargs):
    """Run the blocking Twilio SDK off the event loop."""
    import asyncio

    return await asyncio.to_thread(lambda: fn(*args, **kwargs))
