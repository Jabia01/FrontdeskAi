"""arq worker entry point.

Run with:  arq app.queue.worker.WorkerSettings
"""

from __future__ import annotations

import logging
from typing import ClassVar

from arq import cron

from app.channels.base import load_adapters
from app.config import get_settings
from app.db import dispose_engine
from app.queue.client import redis_settings
from app.queue.tasks import (
    handle_inbound,
    handle_missed_call,
    notify_owner,
    sweep_reminders,
    sweep_summaries,
)


async def startup(ctx: dict) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    load_adapters()
    logging.getLogger(__name__).info("worker ready")


async def shutdown(ctx: dict) -> None:
    await dispose_engine()


class WorkerSettings:
    functions: ClassVar[list] = [
        handle_inbound,
        handle_missed_call,
        notify_owner,
        sweep_reminders,
        sweep_summaries,
    ]
    #: Both sweeps are idempotent and read what is due from the database, so
    #: running them more often than necessary is harmless - and a missed run
    #: costs nothing because the next one catches up.
    cron_jobs: ClassVar[list] = [
        cron(sweep_reminders, minute={0, 15, 30, 45}, run_at_startup=False),
        # Hourly. Each business is sent its summary on the first run after its
        # own local `summary_hour`, so one job covers every timezone.
        cron(sweep_summaries, minute=2, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_tries = 3
    job_timeout = 120
    keep_result = 300
