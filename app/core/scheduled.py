"""Messages that come due later: recalls and follow-ups.

The same shape as `app/core/reminders.py`, and for the same reasons. A sweep
reads what is due from the database every few minutes, so nothing is held in a
queue that a flush can lose, a worker outage self-heals on the next pass, and
calling a message off is an UPDATE rather than a hunt through Redis.

What differs from a reminder: a reminder is derived from an appointment that
already exists, so there is nothing to cancel - move the appointment and the
reminder moves with it. A recall has no anchor. It is scheduled because
somebody asked for it, and it has to be called off explicitly when the reason
goes away: they booked, they opted out, the owner said stop.

The text is composed here, from a template, at send time. A message written
months ahead and stored verbatim is a message that eventually goes out after
the business changed its name, its prices or its mind.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.translate import translate
from app.config import get_settings
from app.core.delivery import send_reply
from app.core.router import ShopContext, load_shop
from app.models import Channel, ClientStatus, Customer, ScheduledMessage
from app.repo import (
    cancel_scheduled_for_customer,
    get_conversation,
    get_customer,
    latest_conversation,
    mark_scheduled_sent,
    scheduled_due,
)

log = logging.getLogger(__name__)

#: Written in English and translated at send, like the appointment reminder.
#: Deliberately short, deliberately a question: an unprompted message months
#: after the last one should give the customer an obvious way to say no.
TEMPLATES: dict[str, str] = {
    "recall": (
        "Hello, this is {business}. It is about time for {topic}. "
        "Would you like to book? Reply STOP if you would rather not hear from us."
    ),
    "follow_up": (
        "Hello, this is {business}. Are you still looking for {topic}? "
        "Reply STOP if you would rather not hear from us."
    ),
}

GENERIC_TOPIC = {
    "recall": "your next appointment",
    "follow_up": "what we spoke about",
}

#: Kinds a tool may schedule. Anything else is a bug, and is skipped rather
#: than sent as a half-rendered template.
KINDS = tuple(sorted(TEMPLATES))


@dataclass(slots=True)
class SweepResult:
    """What one pass did, for the log and the tests."""

    considered: int = 0
    sent: int = 0
    blocked: int = 0
    skipped: int = 0
    reasons: list[str] = field(default_factory=list)

    def note(self, reason: str) -> None:
        self.skipped += 1
        self.reasons.append(reason)

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "sent": self.sent,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "reasons": self.reasons,
        }


def compose(shop: ShopContext, row: ScheduledMessage) -> str | None:
    """The English text. `None` for an unknown kind, which is never sent."""
    template = TEMPLATES.get(row.kind)
    if template is None:
        return None
    topic = (row.topic or "").strip() or GENERIC_TOPIC.get(row.kind, "your enquiry")
    return template.format(business=shop.name, topic=topic)


async def send_one(
    session: AsyncSession,
    shop: ShopContext,
    row: ScheduledMessage,
    customer: Customer,
    *,
    result: SweepResult,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    client_id = shop.client_id

    if customer.opted_out_at is not None:
        # Mark the attempt and cancel the rest. An opt-out is not a reason to
        # reconsider this one message in fifteen minutes' time.
        await mark_scheduled_sent(session, client_id, row.id, now)
        await cancel_scheduled_for_customer(
            session, client_id, customer.id, reason="opted out", when=now
        )
        result.note("customer opted out")
        return

    body_en = compose(shop, row)
    if body_en is None:
        await mark_scheduled_sent(session, client_id, row.id, now)
        log.error("scheduled message %s has unknown kind %r", row.id, row.kind)
        result.note(f"unknown kind {row.kind!r}")
        return

    conversation = None
    if row.conversation_id is not None:
        conversation = await get_conversation(session, client_id, row.conversation_id)
    if conversation is None:
        # The conversation it was scheduled from is gone, or it was scheduled
        # outside one. Use whatever channel we last spoke to them on.
        for channel in (Channel.whatsapp, Channel.sms, Channel.simulator):
            conversation = await latest_conversation(
                session, client_id, customer.id, channel
            )
            if conversation is not None:
                break
    if conversation is None:
        await mark_scheduled_sent(session, client_id, row.id, now)
        result.note("no conversation to reply in")
        return

    body, _ = await translate(
        body_en,
        to_language=(customer.language or shop.default_customer_language or "en"),
        # Always "en", never the owner's language. `compose` writes English by
        # construction, so telling `translate` the source is Spanish for a
        # Spanish-speaking shop makes it see source == target and skip the
        # translation - and the German patient gets an English recall. The
        # same trap is documented in app/core/reminders.py.
        from_language="en",
    )

    outcome, _ = await send_reply(
        session,
        client_id=client_id,
        conversation=conversation,
        to_number=customer.phone,
        body=body,
        channel=conversation.channel,
    )
    await mark_scheduled_sent(session, client_id, row.id, now)
    if outcome.ok:
        result.sent += 1
    elif outcome.blocked:
        result.blocked += 1
        result.reasons.append("outside the messaging window")
    else:
        result.note(outcome.error or "send failed")


async def sweep_client(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> dict:
    """Send everything due for one business."""
    now = now or datetime.now(UTC)
    result = SweepResult()

    if not get_settings().ai_enabled:
        result.note("AI disabled")
        return result.as_dict()

    shop = await load_shop(session, client_id, use_cache=False)
    if shop.status != ClientStatus.live.value:
        # Same rule as reminders: a shop still onboarding does not send
        # unprompted messages to its customers.
        result.note(f"status {shop.status}")
        return result.as_dict()

    for row in await scheduled_due(session, client_id, now=now):
        result.considered += 1
        customer = await get_customer(session, client_id, row.customer_id)
        if customer is None:
            await mark_scheduled_sent(session, client_id, row.id, now)
            result.note("customer gone")
            continue
        await send_one(session, shop, row, customer, result=result, now=now)

    return result.as_dict()
