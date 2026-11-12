"""Running the queue worker inside the web process.

Two processes is the better architecture and `app/queue/worker.py` is still the
way to run them apart - see the note at the bottom of `docs/deploy.md` for how
to split them again. This exists because two always-on services cost twice as
much as one, and at a handful of businesses that is the difference between $7
and $24 a month.

**Why it is safe to share a process.** Both halves are pure async I/O: waiting
on Postgres, on Claude, on Twilio. There is no CPU-bound work anywhere in the
request path, so the worker's waiting does not delay a webhook's acknowledgement
- which is the one latency that actually matters, because Twilio times out. The
gateway still acks before any Claude call happens; that call simply now runs as
a task on the same event loop rather than in another process.

**What it costs.** A crash takes both halves down together, and they scale
together. A worker that dies silently would be worse than either: messages would
queue up forever and every customer would be met with silence. So a dead worker
is reported by `/health`, which makes the platform's health check restart the
service - noisy recovery in preference to quiet failure.
"""

from __future__ import annotations

import asyncio
import logging

from arq.worker import Worker, create_worker

from app.queue.worker import WorkerSettings

log = logging.getLogger(__name__)


class EmbeddedWorker:
    """An arq worker running as a task beside the web application."""

    def __init__(self) -> None:
        self._worker: Worker | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.error: str | None = None

    @property
    def started(self) -> bool:
        return self._task is not None

    @property
    def alive(self) -> bool:
        """True while the worker task is still running.

        False once it has finished, which should only ever happen on shutdown -
        so `/health` treats it as a fault.
        """
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return

        self._worker = create_worker(
            WorkerSettings,
            # arq installs its own SIGINT/SIGTERM handlers by default. In a
            # shared process they fight uvicorn's, and shutdown stops being
            # predictable - so uvicorn keeps sole ownership of the signals.
            handle_signals=False,
            # The standalone worker's shutdown hook disposes the SQLAlchemy
            # engine. Here that engine belongs to the web application too, so
            # the app's own lifespan disposes it once, afterwards.
            on_shutdown=None,
        )
        self._task = asyncio.create_task(
            self._worker.async_run(), name="arq-embedded-worker"
        )
        self._task.add_done_callback(self._note_exit)
        log.info(
            "queue worker started in-process: %s functions, %s cron jobs",
            len(WorkerSettings.functions),
            len(WorkerSettings.cron_jobs),
        )

    def _note_exit(self, task: asyncio.Task) -> None:
        if self._stopping or task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            # Loud on purpose. Without the worker, every reply queues up
            # unanswered and nothing else in the system notices.
            log.error("queue worker died: %s", self.error, exc_info=exc)
        else:
            self.error = "worker exited unexpectedly"
            log.error(self.error)

    async def stop(self) -> None:
        self._stopping = True
        if self._worker is not None:
            try:
                await self._worker.close()
            except Exception as exc:
                log.warning("queue worker did not close cleanly: %s", exc)
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Expected: we just cancelled it.
            except Exception as exc:
                log.warning("queue worker raised while shutting down: %s", exc)
        self._task = None
        self._worker = None

    def status(self) -> dict:
        if not self.started:
            return {"embedded": False}
        return {
            "embedded": True,
            "alive": self.alive,
            "error": self.error,
        }
