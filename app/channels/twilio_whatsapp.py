"""Twilio WhatsApp adapter.

Enforces the rules the architecture doc assigns to this adapter:
  - free-form replies only within 24 hours of the customer's last message
  - outside that window, only a pre-approved template may be sent
  - every send is logged so per-message cost can be tracked

In Phase 1 no templates are approved yet, so an out-of-window send is refused
with `blocked=True`. The caller marks the message blocked and alerts the owner
rather than dropping it silently.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.channels.base import register
from app.channels.twilio_base import WHATSAPP_PREFIX, TwilioAdapterBase
from app.models import Channel
from app.schemas import OutboundMessage, SendResult


class TwilioWhatsAppAdapter(TwilioAdapterBase):
    channel = Channel.whatsapp
    enforces_free_form_window = True

    def _wire_number(self, number: str) -> str:
        number = (number or "").strip()
        if number.lower().startswith(WHATSAPP_PREFIX):
            return number
        return f"{WHATSAPP_PREFIX}{number}"

    def _check_policy(self, message: OutboundMessage) -> SendResult | None:
        # An approved template is always allowed, in or out of the window.
        if message.template_name:
            return None

        expires = message.window_expires_at
        if expires is None:
            # No recorded inbound message, so there is no open window to rely on.
            return SendResult(
                ok=False,
                blocked=True,
                error=(
                    "no open 24-hour window for this conversation; an approved "
                    "template is required"
                ),
            )

        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)

        if expires <= datetime.now(UTC):
            return SendResult(
                ok=False,
                blocked=True,
                error=(
                    f"24-hour free-form window closed at {expires.isoformat()}; "
                    "an approved template is required"
                ),
            )
        return None


adapter = register(TwilioWhatsAppAdapter())
