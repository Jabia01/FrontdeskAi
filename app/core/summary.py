"""The daily summary the owner wakes up to.

One message, on the channel they already use, in their own language: what
happened yesterday, what is booked today, and what is waiting on them.

Three decisions worth knowing:

**It is derived, not accumulated.** Every number is a query over the day that
just ended, so a worker that was down overnight produces a correct summary when
it comes back rather than a partial one.

**It is idempotent on `client_config.last_summary_on`**, the business's own local
date. The sweep runs hourly and sends at most one summary per business per local
day, whatever restarts happen in between.

**A quiet day gets no message.** A business that had no messages, no
appointments and nothing waiting is not told that, daily, forever - that is how
an owner learns to ignore us. `build` returns None and the sweep skips it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.translate import translate
from app.config import get_settings
from app.core.booking import label_at
from app.core.owner import send_to_owner
from app.core.router import ShopContext, load_shop
from app.models import ClientStatus, JobStatus
from app.repo import appointments_between, last_summary_on, set_last_summary_on
from app.repo_shop import counts_since, jobs_overview, open_handoffs, pending_drafts

log = logging.getLogger(__name__)


def local_day(shop: ShopContext, now: datetime) -> date:
    return now.astimezone(ZoneInfo(shop.timezone)).date()


def _day_bounds(shop: ShopContext, day: date) -> tuple[datetime, datetime]:
    """The UTC instants of a business's own local day."""
    tz = ZoneInfo(shop.timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def summary_hour(shop: ShopContext) -> int:
    try:
        hour = int(shop.config.get("summary_hour"))
    except (TypeError, ValueError):
        return 7
    return hour if 0 <= hour <= 23 else 7


def is_enabled(shop: ShopContext) -> bool:
    return bool(shop.config.get("summary_enabled", True))


async def build(
    session: AsyncSession, shop: ShopContext, *, now: datetime | None = None
) -> str | None:
    """The English summary, or None when there is nothing worth sending."""
    now = now or datetime.now(UTC)
    client_id = shop.client_id
    tz = ZoneInfo(shop.timezone)
    today = local_day(shop, now)
    yesterday = today - timedelta(days=1)

    y_start, y_end = _day_bounds(shop, yesterday)
    t_start, t_end = _day_bounds(shop, today)

    stats = await counts_since(session, client_id, since=y_start, until=y_end)

    booked_today = await appointments_between(
        session, client_id, start=t_start, end=t_end
    )
    ready = await jobs_overview(session, client_id, status=JobStatus.ready, limit=50)
    handoffs = await open_handoffs(session, client_id, limit=50)
    drafts = await pending_drafts(session, client_id, limit=50)

    nothing_happened = (
        stats["inbound"] == 0
        and stats["outbound"] == 0
        and stats["appointments"] == 0
        and stats["calls"] == 0
        and not booked_today
        and not handoffs
        and not drafts
    )
    if nothing_happened:
        return None

    header = f"{shop.name} - {label_at(y_start, tz).split(',')[0]} summary"
    lines: list[str] = []

    if stats["inbound"] or stats["outbound"]:
        lines.append(
            f"Yesterday: {stats['inbound']} message"
            f"{'' if stats['inbound'] == 1 else 's'} in, "
            f"{stats['outbound']} answered."
        )
    if stats["conversations"]:
        lines.append(
            f"{stats['conversations']} new "
            f"{'conversation' if stats['conversations'] == 1 else 'conversations'}."
        )
    if stats["appointments"]:
        lines.append(
            f"{stats['appointments']} "
            f"{'appointment' if stats['appointments'] == 1 else 'appointments'} booked."
        )
    if stats["calls"]:
        lines.append(
            f"{stats['calls']} missed {'call' if stats['calls'] == 1 else 'calls'}, "
            f"{stats['texted_back']} texted back."
        )
    if stats["blocked"]:
        lines.append(
            f"{stats['blocked']} repl"
            f"{'y' if stats['blocked'] == 1 else 'ies'} could not be delivered."
        )

    if not lines:
        lines.append("Nothing came in yesterday.")

    if booked_today:
        lines += ["", f"Today ({len(booked_today)}):"]
        for appointment, customer in booked_today[:8]:
            when = label_at(appointment.start_at, tz).split(", ")[-1]
            parts = [when]
            if customer is not None and (customer.name or customer.phone):
                parts.append(customer.name or customer.phone)
            if appointment.item:
                parts.append(appointment.item)
            if appointment.service:
                parts.append(appointment.service)
            lines.append("- " + " - ".join(parts))
        if len(booked_today) > 8:
            lines.append(f"- and {len(booked_today) - 8} more")
    else:
        lines += ["", "Nothing booked today."]

    if ready:
        noun = shop.config.get("job_noun") or "job"
        lines += [
            "",
            f"Ready for collection ({len(ready)}):",
        ]
        for job, _customer in ready[:6]:
            lines.append(f"- {job.item or noun}")

    if handoffs or drafts:
        lines += ["", "Waiting on you:"]
        for row in handoffs[:6]:
            lines.append(
                f"- [{row.conversation.short_code}] {row.customer.phone}"
                f" - {row.conversation.handoff_reason or 'handed to you'}"
            )
        if drafts:
            lines.append(
                f"- {len(drafts)} draft{'' if len(drafts) == 1 else 's'} "
                f"waiting for your OK"
            )
        lines.append('Reply HELP if you want the list of what you can text me.')

    return "\n".join([header, "", *lines])


async def send_for_client(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Send one business's summary if it is due. `force` ignores the clock."""
    now = now or datetime.now(UTC)

    if not get_settings().ai_enabled and not force:
        return {"status": "skipped", "reason": "automation disabled globally"}

    shop = await load_shop(session, client_id)
    if shop.status != ClientStatus.live.value:
        return {"status": "skipped", "reason": f"business is {shop.status}"}
    if not is_enabled(shop):
        return {"status": "skipped", "reason": "summary turned off"}

    today = local_day(shop, now)
    local_hour = now.astimezone(ZoneInfo(shop.timezone)).hour

    if not force:
        if local_hour < summary_hour(shop):
            return {"status": "skipped", "reason": "too early in their day"}
        last = await last_summary_on(session, client_id)
        if last is not None and last >= today:
            return {"status": "skipped", "reason": "already sent today"}

    body = await build(session, shop, now=now)
    if body is None:
        # Still mark the day done, or every hour until midnight asks again.
        await set_last_summary_on(session, client_id, today)
        return {"status": "skipped", "reason": "nothing happened"}

    translated, _ = await translate(
        body, to_language=shop.owner_language or "en", from_language="en"
    )
    ok, detail = await send_to_owner(session, shop, translated)
    await set_last_summary_on(session, client_id, today)
    log.info("daily summary for %s: %s (%s)", shop.name, ok, detail)
    return {"status": "sent" if ok else "failed", "detail": detail}
