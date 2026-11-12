"""Tenant router.

Turns a `client_id` into an immutable `ShopContext`: config, language settings,
and the per-channel system prompt with its version hash. Cached in process
because it is read on every single message and changes rarely.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompt import build_system_prompt, prompt_version
from app.repo import get_client_with_config

CACHE_TTL_SECONDS = 60.0


class UnknownClientError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ShopContext:
    client_id: uuid.UUID
    name: str
    timezone: str
    status: str
    owner_phone: str | None
    owner_language: str
    default_customer_language: str
    enabled_languages: tuple[str, ...]
    country_language_overrides: dict[str, list[str]]
    handoff_keywords: tuple[str, ...]
    max_misunderstandings: int
    config: dict[str, Any] = field(default_factory=dict)
    #: channel value -> (system prompt, version)
    _prompts: dict[str, tuple[str, str]] = field(default_factory=dict)

    def system_prompt(self, channel: str) -> tuple[str, str]:
        """(prompt text, version). Memoised per channel; identical byte-for-byte
        on every call, which is what makes prompt caching hit."""
        cached = self._prompts.get(channel)
        if cached is not None:
            return cached
        shop = {
            "name": self.name,
            "timezone": self.timezone,
            "config": self.config,
        }
        text = build_system_prompt(shop, channel)
        entry = (text, prompt_version(text))
        self._prompts[channel] = entry
        return entry

    def style(self, channel: str) -> dict[str, Any]:
        styles = self.config.get("channel_style") or {}
        return styles.get(channel) or {}


_CACHE: dict[uuid.UUID, tuple[float, ShopContext]] = {}


def _config_to_dict(config) -> dict[str, Any]:
    if config is None:
        return {}
    return {
        "business_info": config.business_info or {},
        "services": config.services or [],
        "hours": config.hours or {},
        "price_ranges": config.price_ranges or [],
        "tone": config.tone or "",
        "handoff_rules": config.handoff_rules or {},
        "faqs": config.faqs or [],
        "channel_style": config.channel_style or {},
        # Phase 2: booking, missed calls and owner routing.
        "calendar_id": config.calendar_id,
        "booking": config.booking or {},
        "missed_call_channel": config.missed_call_channel or "sms",
        "owner_channel": config.owner_channel or "auto",
        "owner_language": config.owner_language or "en",
        # Phase 3 / v1.5: what kind of business this is, the word it uses for a
        # job, and the scheduled-message settings.
        "vertical": config.vertical or "other",
        "job_noun": config.job_noun or "job",
        "reminder_hours_before": (
            config.reminder_hours_before
            if config.reminder_hours_before is not None
            else 24
        ),
        "summary_enabled": bool(config.summary_enabled),
        "summary_hour": config.summary_hour if config.summary_hour is not None else 7,
        "templates": config.templates or {},
    }


async def load_shop(
    session: AsyncSession, client_id: uuid.UUID, *, use_cache: bool = True
) -> ShopContext:
    now = time.monotonic()
    if use_cache:
        hit = _CACHE.get(client_id)
        if hit is not None and (now - hit[0]) < CACHE_TTL_SECONDS:
            return hit[1]

    client = await get_client_with_config(session, client_id)
    if client is None:
        raise UnknownClientError(f"no client {client_id}")

    config = client.config
    config_dict = _config_to_dict(config)
    handoff = config_dict.get("handoff_rules") or {}

    context = ShopContext(
        client_id=client.id,
        name=client.name,
        timezone=client.timezone or "America/Chicago",
        status=client.status.value if client.status else "onboarding",
        owner_phone=client.owner_phone,
        owner_language=(config.owner_language if config else "en") or "en",
        default_customer_language=(
            (config.default_customer_language if config else "en") or "en"
        ),
        enabled_languages=tuple(
            (config.enabled_languages if config else None) or ["en"]
        ),
        country_language_overrides=dict(
            (config.country_language_overrides if config else None) or {}
        ),
        handoff_keywords=tuple(
            sorted(k.lower() for k in (handoff.get("keywords") or []))
        ),
        max_misunderstandings=int(handoff.get("max_misunderstandings") or 2),
        config=config_dict,
    )

    _CACHE[client_id] = (now, context)
    return context


def invalidate(client_id: uuid.UUID | None = None) -> None:
    """Called after a config or prompt change. No arg clears everything."""
    if client_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(client_id, None)
