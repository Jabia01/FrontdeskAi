"""The owner dashboard.

Read-only, deliberately. The architecture doc's owner-first rule says every
action a shop owner needs is a WhatsApp reply - `OK`, `READY 2014 Civic`,
`RESUME`, or just typing what to send. Duplicating those as buttons would mean
two code paths for every action, two things to keep in step, and a far larger
attack surface on a session that is unlocked by a six-digit SMS code. So this
is a window, not a control panel: it answers "what is going on in my shop",
and the phone answers "do something about it".

**Isolation.** Every route here reads `principal.shop_id` - the client_id
stored in the session row at login. No route takes a client id from the path,
the query string or a form, so there is no shop-shaped parameter for anyone to
tamper with. This module also may not import `repo_admin`, whose queries can
cross tenants; `tests/test_web_isolation.py` checks that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import repo_shop
from app.core.router import load_shop
from app.db import get_session
from app.models import ConversationStatus, JobStatus
from app.web.auth import Principal, owner_principal
from app.web.templating import render

router = APIRouter(prefix="/shop", tags=["owner"])


async def _shop(session: AsyncSession, principal: Principal):
    return await load_shop(session, principal.shop_id)


@router.get("", include_in_schema=False)
async def today(
    request: Request,
    principal: Principal = Depends(owner_principal),
    session: AsyncSession = Depends(get_session),
):
    client_id = principal.shop_id
    shop = await _shop(session, principal)
    now = datetime.now(UTC)
    week = now - timedelta(days=7)

    return render(
        request,
        "shop/today.html",
        principal=principal,
        section="today",
        shop=shop,
        tz=shop.timezone,
        noun=shop.config.get("job_noun") or "job",
        stats=await repo_shop.counts_since(session, client_id, since=week),
        handoffs=await repo_shop.open_handoffs(session, client_id, limit=20),
        drafts=await repo_shop.pending_drafts(session, client_id, limit=20),
        appointments=await repo_shop.appointments_from(
            session, client_id, since=now - timedelta(hours=12), limit=12
        ),
        jobs=await repo_shop.jobs_overview(
            session, client_id, status=JobStatus.ready, limit=12
        ),
        calls=await repo_shop.recent_calls(session, client_id, limit=8),
        recent=await repo_shop.conversation_page(session, client_id, limit=8),
    )


@router.get("/conversations", include_in_schema=False)
async def conversations(
    request: Request,
    principal: Principal = Depends(owner_principal),
    session: AsyncSession = Depends(get_session),
):
    shop = await _shop(session, principal)
    raw_status = request.query_params.get("status", "")
    status = None
    if raw_status:
        try:
            status = ConversationStatus(raw_status)
        except ValueError:
            status = None
    return render(
        request,
        "shop/conversations.html",
        principal=principal,
        section="conversations",
        shop=shop,
        tz=shop.timezone,
        noun=shop.config.get("job_noun") or "job",
        rows=await repo_shop.conversation_page(
            session, principal.shop_id, status=status, limit=100
        ),
        status=raw_status,
    )


@router.get("/conversations/{conversation_id}", include_in_schema=False)
async def conversation(
    request: Request,
    conversation_id: uuid.UUID,
    principal: Principal = Depends(owner_principal),
    session: AsyncSession = Depends(get_session),
):
    """The conversation id comes from the path, but the `client_id` it is
    filtered by comes from the session - so another shop's conversation id
    resolves to nothing rather than to their transcript."""
    client_id = principal.shop_id
    found = await repo_shop.conversation_with_customer(
        session, client_id, conversation_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail="No such conversation.")
    conversation_row, customer = found
    shop = await _shop(session, principal)
    return render(
        request,
        "shop/conversation.html",
        principal=principal,
        section="conversations",
        shop=shop,
        tz=shop.timezone,
        noun=shop.config.get("job_noun") or "job",
        conversation=conversation_row,
        customer=customer,
        messages=await repo_shop.full_transcript(
            session, client_id, conversation_id
        ),
    )


@router.get("/appointments", include_in_schema=False)
async def appointments(
    request: Request,
    principal: Principal = Depends(owner_principal),
    session: AsyncSession = Depends(get_session),
):
    shop = await _shop(session, principal)
    return render(
        request,
        "shop/appointments.html",
        principal=principal,
        section="appointments",
        shop=shop,
        tz=shop.timezone,
        noun=shop.config.get("job_noun") or "job",
        rows=await repo_shop.appointments_from(
            session,
            principal.shop_id,
            since=datetime.now(UTC) - timedelta(days=7),
            limit=200,
        ),
    )


@router.get("/jobs", include_in_schema=False)
async def jobs(
    request: Request,
    principal: Principal = Depends(owner_principal),
    session: AsyncSession = Depends(get_session),
):
    shop = await _shop(session, principal)
    return render(
        request,
        "shop/jobs.html",
        principal=principal,
        section="jobs",
        shop=shop,
        tz=shop.timezone,
        noun=shop.config.get("job_noun") or "job",
        rows=await repo_shop.jobs_overview(session, principal.shop_id, limit=200),
    )


@router.get("/calls", include_in_schema=False)
async def calls(
    request: Request,
    principal: Principal = Depends(owner_principal),
    session: AsyncSession = Depends(get_session),
):
    shop = await _shop(session, principal)
    return render(
        request,
        "shop/calls.html",
        principal=principal,
        section="calls",
        shop=shop,
        tz=shop.timezone,
        noun=shop.config.get("job_noun") or "job",
        rows=await repo_shop.recent_calls(session, principal.shop_id, limit=200),
    )
