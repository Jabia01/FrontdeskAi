"""Owner login: phone number, then a six-digit code.

Separate from `app/web/owner.py` on purpose. This module has to reach the
cross-tenant lookup "which shop owns this number"; the dashboard module must
never be able to, and `tests/test_web_isolation.py` enforces that by asserting
`owner.py` does not import `repo_admin` at all.

No enumeration: every outcome of the first step renders the same page. Whether
the number owns a shop, is rate limited, or has nothing to do with us, the
answer is "we sent a code". Otherwise this form is a free directory of which
businesses are our clients.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import repo_admin
from app.config import get_settings
from app.db import get_session
from app.models import SessionKind
from app.web import phone as phone_util
from app.web.auth import (
    MAX_CODE_ATTEMPTS,
    OWNER_COOKIE,
    Principal,
    clear_cookie,
    end_session,
    owner_post,
    principal_from_token,
    request_login_code,
    set_cookie,
    start_session,
    verify_login_code,
)
from app.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/shop", tags=["owner"])


def _see(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _local_dev_hint() -> str | None:
    """With no Twilio credentials there is no SMS, so say where the code went."""
    if get_settings().twilio_configured:
        return None
    return (
        "Twilio is not configured on this server, so the code was not texted. "
        "It is in the application log, and in the /sim window."
    )


@router.get("/login", include_in_schema=False)
async def login_form(
    request: Request, session: AsyncSession = Depends(get_session)
):
    existing = await principal_from_token(
        session, request.cookies.get(OWNER_COOKIE), kind=SessionKind.owner
    )
    if existing is not None:
        return _see("/shop")
    return render(request, "shop/login.html")


@router.post("/login", include_in_schema=False)
async def send_code(
    request: Request,
    phone: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    typed = phone.strip()
    if not typed:
        return render(
            request,
            "shop/login.html",
            error="Please enter the mobile number we text you on.",
            status_code=400,
        )

    await request_login_code(session, typed)
    # Identical page in every case - see the module docstring.
    return render(
        request,
        "shop/verify.html",
        phone=typed,
        masked=phone_util.mask(phone_util.normalise(typed) or typed),
        hint=_local_dev_hint(),
    )


@router.post("/verify", include_in_schema=False)
async def verify(
    request: Request,
    phone: str = Form(""),
    code: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    client_id = await verify_login_code(session, phone, code)
    if client_id is None:
        return render(
            request,
            "shop/verify.html",
            phone=phone,
            masked=phone_util.mask(phone_util.normalise(phone) or phone),
            hint=_local_dev_hint(),
            error=(
                f"That code is not right, or it has expired. You get "
                f"{MAX_CODE_ATTEMPTS} tries, then you need a new code."
            ),
            status_code=401,
        )

    token, principal = await start_session(
        session,
        kind=SessionKind.owner,
        label=phone_util.normalise(phone) or phone,
        client_id=client_id,
        user_agent=request.headers.get("user-agent"),
    )
    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.owner,
        action="owner.login",
        client_id=client_id,
    )
    response = _see("/shop")
    set_cookie(response, SessionKind.owner, token)
    return response


@router.post("/logout", include_in_schema=False)
async def logout(
    principal: Principal = Depends(owner_post),
    session: AsyncSession = Depends(get_session),
):
    await end_session(session, principal)
    response = _see("/shop/login")
    clear_cookie(response, SessionKind.owner)
    return response
