"""FastAPI application.

Four services in one app: gateway, tenant router, conversation engine, agent.
The queue runs beside it as a separate arq worker process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.channels.base import load_adapters, registered_channels
from app.config import get_settings
from app.db import dispose_engine, session_scope
from app.gateway.routes import router as gateway_router
from app.queue.client import close_pool, describe_pool
from app.queue.embedded import EmbeddedWorker
from app.web.admin import router as admin_router
from app.web.auth import NotAuthenticated
from app.web.marketing import router as marketing_router
from app.web.owner import router as owner_router
from app.web.owner_auth import router as owner_auth_router
from app.web.templating import STATIC_DIR, render

log = logging.getLogger(__name__)

SIM_DIR = Path(__file__).parent / "sim" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    load_adapters()
    logging.getLogger(__name__).info(
        "channels: %s", [c.value for c in registered_channels()]
    )

    # On a single-service deployment the queue worker runs here rather than as
    # its own process - see app/queue/embedded.py for why that is safe and what
    # it costs. Off by default, so running the two apart stays the normal case.
    worker = EmbeddedWorker()
    app.state.worker = worker
    if settings.run_worker_in_process:
        await worker.start()

    try:
        yield
    finally:
        await worker.stop()
        await close_pool()
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Front Desk",
        version="0.1.0",
        description="Multi-tenant AI front desk - WhatsApp, SMS, voice-ready.",
        lifespan=lifespan,
    )
    app.include_router(gateway_router)
    # Order matters only for readability here - no two of these share a path.
    app.include_router(admin_router)
    app.include_router(owner_auth_router)
    app.include_router(owner_router)
    app.include_router(marketing_router)
    app.mount(
        "/web-static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="web-static",
    )

    @app.exception_handler(NotAuthenticated)
    async def _needs_login(request: Request, exc: NotAuthenticated):
        """A page behind a login redirects; it never shows a JSON 401."""
        target = exc.login_path
        if exc.next_url:
            target = f"{target}?next={quote(exc.next_url, safe='/?=&')}"
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        """HTML for the browser pages, the framework's JSON for everything
        else - the webhooks and /sim/poll are consumed by machines."""
        path = request.url.path
        if path.startswith(("/admin", "/shop")) or path in {"/", "/thanks"}:
            return render(
                request,
                "error.html",
                status_code=exc.status_code,
                code=exc.status_code,
                detail=str(exc.detail or ""),
            )
        return await http_exception_handler(request, exc)

    @app.get("/health")
    async def health(request: Request, response: Response) -> dict:
        """Readiness, not just liveness.

        The database is actually queried, and a failure returns 503. This is
        what the platform's health check calls, so a wrong `DATABASE_URL` fails
        the deploy visibly instead of letting every webhook 500 one at a time.
        The timeout is short because a health check that hangs is itself an
        outage.
        """
        settings = get_settings()
        database = "ok"
        try:
            async with asyncio.timeout(5):
                async with session_scope() as session:
                    await session.execute(text("select 1"))
        except Exception as exc:
            database = f"error: {type(exc).__name__}"
            log.error("health check could not reach the database: %s", exc)
            response.status_code = 503

        # A worker that has died takes the whole product down quietly: replies
        # queue up and nobody is told. Reporting it here is what makes the
        # platform restart the service instead.
        worker = getattr(request.app.state, "worker", None)
        worker_status = worker.status() if worker is not None else {"embedded": False}
        if worker_status.get("embedded") and not worker_status.get("alive"):
            response.status_code = 503

        return {
            "status": (
                "ok"
                if database == "ok" and response.status_code != 503
                else "degraded"
            ),
            "database": database,
            "worker": worker_status,
            "channels": [c.value for c in registered_channels()],
            "ai_enabled": settings.ai_enabled,
            "anthropic_key_configured": bool(settings.anthropic_api_key),
            "twilio_configured": settings.twilio_configured,
            "calendar_configured": settings.calendar_configured,
            "queue": describe_pool(),
        }

    @app.get("/sim", include_in_schema=False)
    async def simulator_page() -> FileResponse:
        return FileResponse(SIM_DIR / "index.html")

    return app


app = create_app()
