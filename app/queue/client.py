"""Enqueue side of the job queue.

`arq` is used instead of RQ or Celery because both of those are fork-based and do
not run on Windows, which is the current dev box. arq is asyncio + Redis, works
everywhere, and already supports the delayed jobs Phase 1.5 needs for reminders
and follow-ups.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

log = logging.getLogger(__name__)

_pool: ArqRedis | None = None

#: When Redis is unreachable the gateway falls back to running the job inline so
#: a customer still gets an answer. Set False in tests that assert on enqueueing.
INLINE_FALLBACK = True


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
    _pool = None


async def enqueue_handle_inbound(
    client_id: uuid.UUID, message_id: uuid.UUID
) -> str | None:
    """Queue the slow half of handling a message. Returns the arq job id."""
    try:
        pool = await get_pool()
        job = await pool.enqueue_job(
            "handle_inbound", str(client_id), str(message_id)
        )
        return job.job_id if job else None
    except Exception as exc:
        log.error("could not enqueue handle_inbound: %s", exc)
        if not INLINE_FALLBACK:
            raise
        log.warning("running handle_inbound inline as a fallback")
        from app.queue.tasks import handle_inbound

        await handle_inbound({}, str(client_id), str(message_id))
        return None


async def enqueue_owner_alert(client_id: uuid.UUID, body: str) -> str | None:
    """`body` is the finished message - composition belongs to the caller."""
    try:
        pool = await get_pool()
        job = await pool.enqueue_job("notify_owner", str(client_id), body)
        return job.job_id if job else None
    except Exception as exc:
        log.error("could not enqueue notify_owner: %s", exc)
        if not INLINE_FALLBACK:
            raise
        from app.queue.tasks import notify_owner

        await notify_owner({}, str(client_id), body)
        return None


async def enqueue_missed_call(
    client_id: uuid.UUID, call_id: uuid.UUID
) -> str | None:
    try:
        pool = await get_pool()
        job = await pool.enqueue_job(
            "handle_missed_call", str(client_id), str(call_id)
        )
        return job.job_id if job else None
    except Exception as exc:
        log.error("could not enqueue handle_missed_call: %s", exc)
        if not INLINE_FALLBACK:
            raise
        from app.queue.tasks import handle_missed_call

        await handle_missed_call({}, str(client_id), str(call_id))
        return None


def describe_pool() -> dict[str, Any]:
    """For `/health`, which is public and unauthenticated.

    Never return the URL itself. A Redis DSN carries its password, and this was
    being served to anyone who asked - a credential leak with no login in front
    of it. Host and port are enough to tell one environment from another.
    """
    url = get_settings().redis_url
    host = "not configured"
    if url:
        parts = urlsplit(url.strip())
        host = f"{parts.hostname or '?'}:{parts.port or '?'}"
    return {"connected": _pool is not None, "host": host}
