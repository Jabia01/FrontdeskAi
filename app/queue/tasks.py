"""Worker tasks.

`handle_inbound` is the slow half of answering a customer. arq retries each task
with backoff; the engine's own holding-reply path covers the case where Claude is
the thing failing, so a customer never gets silence.

The two `sweep_*` tasks are cron jobs rather than per-item delayed jobs. They
derive what is due from the database every run, so nothing is lost if Redis is
flushed and an outage self-heals on the next pass. See `app/core/reminders.py`.
"""

from __future__ import annotations

import logging
import uuid

from app.core import reminders, summary
from app.core.engine import handle_inbound_message
from app.core.missed_call import text_back
from app.core.owner import send_to_owner
from app.core.router import load_shop
from app.db import session_scope
from app.repo_admin import live_client_ids

log = logging.getLogger(__name__)


async def handle_inbound(ctx: dict, client_id: str, message_id: str) -> dict:
    result = await handle_inbound_message(uuid.UUID(client_id), uuid.UUID(message_id))
    log.info("handle_inbound %s -> %s", message_id, result.get("status"))
    return result


async def notify_owner(ctx: dict, client_id: str, body: str) -> dict:
    """Deliver one already-composed alert to the shop owner."""
    cid = uuid.UUID(client_id)
    async with session_scope() as session:
        shop = await load_shop(session, cid)
        ok, detail = await send_to_owner(session, shop, body)

    log.info(
        "owner alert for %s -> %s (%s)", shop.name, "sent" if ok else "FAILED", detail
    )
    return {"status": "sent" if ok else "failed", "detail": detail}


async def handle_missed_call(ctx: dict, client_id: str, call_id: str) -> dict:
    """Text the caller back within seconds of an unanswered call."""
    result = await text_back(uuid.UUID(client_id), uuid.UUID(call_id))
    log.info("missed-call text-back %s -> %s", call_id, result.get("status"))
    return result


async def sweep_reminders(ctx: dict) -> dict:
    """Cron: send the appointment reminders that have come due.

    Runs every fifteen minutes. Derived entirely from the database, so a worker
    that was down catches up on its next run instead of losing reminders.
    """
    totals = {"businesses": 0, "sent": 0, "blocked": 0, "skipped": 0}
    async with session_scope() as session:
        client_ids = await live_client_ids(session)
    for client_id in client_ids:
        # A session per business: one business's failure cannot roll back
        # another's, and a long sweep does not hold one transaction open.
        try:
            async with session_scope() as session:
                result = await reminders.sweep_client(session, client_id)
        except Exception as exc:
            log.error("reminder sweep failed for %s: %s", client_id, exc)
            continue
        totals["businesses"] += 1
        totals["sent"] += result["sent"]
        totals["blocked"] += result["blocked"]
        totals["skipped"] += result["skipped"]
    if totals["sent"] or totals["blocked"]:
        log.info("reminder sweep: %s", totals)
    return totals


async def sweep_summaries(ctx: dict) -> dict:
    """Cron: send each business its daily summary once its local hour arrives.

    Runs hourly. `client_config.last_summary_on` keeps it to one per business
    per local day however many times this runs.
    """
    totals = {"sent": 0, "skipped": 0}
    async with session_scope() as session:
        client_ids = await live_client_ids(session)
    for client_id in client_ids:
        try:
            async with session_scope() as session:
                result = await summary.send_for_client(session, client_id)
        except Exception as exc:
            log.error("daily summary failed for %s: %s", client_id, exc)
            continue
        totals["sent" if result["status"] == "sent" else "skipped"] += 1
    if totals["sent"]:
        log.info("daily summaries: %s", totals)
    return totals
