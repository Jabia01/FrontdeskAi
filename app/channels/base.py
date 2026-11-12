"""Channel adapter contract and registry.

An adapter has exactly two jobs:
  1. turn an incoming provider event into an `InboundMessage`
  2. deliver an `OutboundMessage` in that channel's format

Adding voice in v2 means adding one adapter here. Nothing in the core changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from app.models import Channel
from app.schemas import InboundMessage, OutboundMessage, SendResult


@runtime_checkable
class ChannelAdapter(Protocol):
    channel: Channel
    provider: str

    def parse_inbound(self, payload: Mapping[str, str]) -> InboundMessage:
        """Normalise a provider payload. Raises ValueError if unusable."""
        ...

    async def send(self, message: OutboundMessage) -> SendResult:
        """Deliver the reply. Never raises for an expected policy refusal."""
        ...


_REGISTRY: dict[Channel, ChannelAdapter] = {}


def register(adapter: ChannelAdapter) -> ChannelAdapter:
    _REGISTRY[adapter.channel] = adapter
    return adapter


def get_adapter(channel: Channel) -> ChannelAdapter:
    try:
        return _REGISTRY[channel]
    except KeyError:
        raise LookupError(f"no adapter registered for channel {channel!r}") from None


def registered_channels() -> list[Channel]:
    return sorted(_REGISTRY, key=lambda c: c.value)


def load_adapters() -> None:
    """Import side-effect registration. Called once at app start."""
    from app.channels import simulator, twilio_sms, twilio_whatsapp  # noqa: F401
