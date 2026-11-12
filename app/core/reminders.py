"""Appointment reminders.

**A sweep, not a job per appointment.** The obvious design is to enqueue a
delayed job when a booking is made. It is also the fragile one: the job lives in
Redis, so flushing Redis loses every pending reminder silently, and a booking
that is later moved or flagged leaves an orphan job behind that still fires.

This instead derives what is due from the database every few minutes. Nothing is
stored that can go stale, a worker outage self-heals (the next sweep catches up
and sends slightly late rather than not at all), and a cancelled appointment
simply stops being selected.

Idempotency is `appointments.reminder_sent_at`, which marks the **attempt**. A
reminder that cannot be delivered - the WhatsApp window has closed and no
template is approved - is still marked, because the alternative is retrying
every fifteen minutes and alerting the owner every time.

Only `live` businesses are swept. A business in shadow mode is still onboarding,
and a reminder is an unprompted message to a customer.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.translate import translate
from app.config import get_settings
from app.core.booking import label_at
from app.core.delivery import send_reply
from app.core.router import ShopContext, load_shop
from app.models import Appointment, Channel, ClientStatus, Customer
from app.repo import (
    appointments_due_reminder,
    get_conversation,
    latest_conversation,
    mark_reminder_sent,
)

log = logging.getLogger(__name__)

#: Written in English, then translated into the customer's language. Composed
#: here rather than by the agent: the time must be exact, and a generated
#: reminder is a chance to invent one.
REMINDER = (
    "Reminder: your {what} at {business} is {when}. "
    "Reply here if you need to change it."
)
REMINDER_NO_SERVICE = (
    "Reminder: your appointment at {business} is {when}. "
    "Reply here if you need to change it."
)


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


def reminder_hours(shop: ShopContext) -> int:
    try:
        return int(shop.config.get("reminder_hours_before") or 0)
    except (TypeError, ValueError):
        return 0


def compose(shop: ShopContext, appointment: Appointment) -> str:
    """The English reminder. Never guesses the time - it reads the stored one."""
    when = label_at(appointment.start_at, ZoneInfo(shop.timezone))
    service = (appointment.service or "").strip()
    if service:
        return REMINDER.format(what=service, business=shop.name, when=when)
    return REMINDER_NO_SERVICE.format(business=shop.name, when=when)


async def send_one(
    session: AsyncSession,
    shop: ShopContext,
    appointment: Appointment,
    customer: Customer,
    *,
    result: SweepResult,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    client_id = shop.client_id

    if customer.opted_out_at is not None:
        await mark_reminder_sent(session, client_id, appointment.id, now)
        result.note("customer opted out")
        return

    conversation = None
    if appointment.conversation_id is not None:
        conversation = await get_conversation(
            session, client_id, appointment.conversation_id
        )
    if conversation is None:
        # The appointment has no conversation of its own - booked over the
        # phone, or by us on the customer's behalf. Use whatever channel we
        # last spoke to them on.
        for channel in (Channel.whatsapp, Channel.sms, Channel.simulator):
            conversation = await latest_conversation(
                session, client_id, customer.id, channel
            )
            if conversation is not None:
                break
    if conversation is None:
        await mark_reminder_sent(session, client_id, appointment.id, now)
        result.note("no conversation to send on")
        return

    english = compose(shop, appointment)
    body, _ = await translate(
        english,
        to_language=customer.language or shop.default_customer_language,
        # `compose` writes English by construction, whatever language the owner
        # uses. Passing the owner's language here made `translate` see source ==
        # target for a Spanish-speaking shop and skip the translation, so every
        # Spanish customer of a Spanish-run business got an English reminder.
        from_language="en",
    )

    templates = shop.config.get("templates") or {}
    send_result, _message_id = await send_reply(
        session,
        client_id=client_id,
        conversation=conversation,
        to_number=customer.phone,
        body=body,
        channel=conversation.channel,
        detected_language=customer.language,
        template_name=templates.get("reminder") or None,
    )
    await mark_reminder_sent(session, client_id, appointment.id, now)

    if send_result.blocked:
        result.blocked += 1
        from app.queue.client import enqueue_owner_alert

        await enqueue_owner_alert(
            client_id,
            f"I could not send {customer.phone} their reminder for "
            f"{label_at(appointment.start_at, ZoneInfo(shop.timezone))}: the "
            f"24-hour WhatsApp window has closed and no approved reminder "
            f"template is configured.",
        )
    elif send_result.ok:
        result.sent += 1
    else:
        result.note(f"send failed: {send_result.error}")


async def sweep_client(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> dict:
    """One business's due reminders."""
    now = now or datetime.now(UTC)
    result = SweepResult()

    if not get_settings().ai_enabled:
        result.note("automation disabled globally")
        return result.as_dict()

    shop = await load_shop(session, client_id)
    if shop.status != ClientStatus.live.value:
        result.note(f"business is {shop.status}, not live")
        return result.as_dict()

    hours = reminder_hours(shop)
    if hours <= 0:
        result.note("reminders turned off for this business")
        return result.as_dict()

    due = await appointments_due_reminder(
        session, client_id, now=now, until=now + timedelta(hours=hours)
    )
    result.considered = len(due)
    for appointment, customer in due:
        await send_one(
            session, shop, appointment, customer, result=result, now=now
        )

    if result.considered:
        log.info("reminders for %s: %s", client_id, result.as_dict())
    return result.as_dict()
