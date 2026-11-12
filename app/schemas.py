"""Channel-neutral contracts.

Every channel adapter turns its provider payload into an `InboundMessage` and
renders an `OutboundMessage` back into its own format. The agent never sees a
provider payload - only these.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import Channel


class InboundMessage(BaseModel):
    """A customer message, normalised out of whatever the provider sent."""

    model_config = ConfigDict(frozen=True)

    channel: Channel
    provider: str
    # The endpoint that received this - Twilio "To" number or WhatsApp phone_number_id.
    external_id: str
    from_number: str
    body: str | None = None
    media_url: str | None = None
    transcript: str | None = None  # voice, v2
    provider_msg_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        """What the agent reads: body for text channels, transcript for voice."""
        return (self.body or self.transcript or "").strip()


class OutboundMessage(BaseModel):
    """A reply, before any channel-specific formatting."""

    channel: Channel
    provider: str
    external_id: str
    to_number: str
    body: str
    media_url: str | None = None

    # Carried so the WhatsApp adapter can enforce its own 24-hour free-form rule
    # without reaching into the database. None means "no window constraint".
    window_expires_at: datetime | None = None
    # Set when the send is an approved template rather than a free-form reply.
    template_name: str | None = None


class SendResult(BaseModel):
    """What an adapter reports back after attempting a send."""

    ok: bool
    provider_msg_id: str | None = None
    error: str | None = None
    blocked: bool = False  # refused by a channel policy, not a transport failure
    segments: int = 1


class AgentReply(BaseModel):
    """The single structured result of one Claude call.

    Language detection rides along with the reply rather than needing a second
    call or a separate detection service.
    """

    reply: str
    detected_language: str
    language_confidence: float = Field(ge=0.0, le=1.0)
    handoff: bool = False
    handoff_reason: str = ""
    understood: bool = True


# Mirrors AgentReply. Sent as output_config.format so the model's final turn is
# guaranteed-valid JSON. Keep in sync with the model above - the agent validates
# the parsed JSON through AgentReply, so a drift here fails loudly in tests.
AGENT_REPLY_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": (
                    "The message to send the customer, in the customer's language. "
                    "No markdown, no links unless the style rules allow them."
                ),
            },
            "detected_language": {
                "type": "string",
                "description": (
                    "ISO 639-1 code of the language the customer's latest message "
                    "is written in, e.g. en, es, pl, de. Use 'und' if undeterminable."
                ),
            },
            "language_confidence": {
                "type": "number",
                "description": (
                    "0.0-1.0 confidence in detected_language. Short or neutral "
                    "messages ('ok', an emoji, a phone number) must score below 0.5."
                ),
            },
            "handoff": {
                "type": "boolean",
                "description": "True if this conversation must go to a human now.",
            },
            "handoff_reason": {
                "type": "string",
                "description": "Short reason for the handoff, or an empty string.",
            },
            "understood": {
                "type": "boolean",
                "description": (
                    "False if you could not understand what the customer wants. "
                    "Two consecutive false values trigger a handoff."
                ),
            },
        },
        "required": [
            "reply",
            "detected_language",
            "language_confidence",
            "handoff",
            "handoff_reason",
            "understood",
        ],
        "additionalProperties": False,
    },
}
