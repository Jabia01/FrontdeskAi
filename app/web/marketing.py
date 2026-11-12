"""The public landing page and its signup form.

The only routes in the app that need no login, which makes them the only ones a
stranger can hammer. So: a honeypot field, a per-number limit and a per-address
limit, and no reflection of anything submitted back into the page.

A signup is *not* a `leads` row. `leads` belongs to a shop and holds a customer
who wants a callback; a `signups` row is a prospective shop, and has no
`client_id` at all until the console converts it.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import repo_admin
from app.db import get_session
from app.web import content
from app.web import phone as phone_util
from app.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["marketing"])

#: Per phone number, enforced in the database.
SIGNUP_WINDOW = timedelta(hours=1)
MAX_SIGNUPS_PER_NUMBER = 3
#: Per client address, in memory - enough to stop a single noisy source.
MAX_SIGNUPS_PER_ADDRESS = 10

_BY_ADDRESS: dict[str, list[float]] = {}


def _address_limited(address: str) -> bool:
    cutoff = time.monotonic() - SIGNUP_WINDOW.total_seconds()
    recent = [t for t in _BY_ADDRESS.get(address, []) if t > cutoff]
    _BY_ADDRESS[address] = recent
    return len(recent) >= MAX_SIGNUPS_PER_ADDRESS


def _record_address(address: str) -> None:
    _BY_ADDRESS.setdefault(address, []).append(time.monotonic())


def reset_signup_throttle() -> None:
    """Test hook."""
    _BY_ADDRESS.clear()


def _landing_context() -> dict:
    """Everything the landing page renders, from app/web/content.py.

    The two `*_json` values are embedded in the page for site.js to read -
    `tojson` rather than string building, so a quote in a Spanish sentence
    cannot break the script tag.
    """
    return {
        "demo_script_json": json.dumps(content.DEMO_SCRIPT, ensure_ascii=False),
        "demo_shop": content.DEMO_SHOP,
        "demo_caption": content.DEMO_CAPTION,
        "owner_alert": content.OWNER_ALERT,
        "metrics": content.METRICS,
        "features": content.FEATURES,
        "languages": content.LANGUAGE_DEMO,
        "languages_json": json.dumps(content.LANGUAGE_DEMO, ensure_ascii=False),
        "trades": content.TRADES,
        "faq": content.FAQ,
        # Empty while the quotes are invented - see content.visible_reviews.
        "reviews": content.visible_reviews(),
        "reviews_are_samples": content.reviews_are_samples(),
    }


@router.get("/", include_in_schema=False)
async def landing(request: Request):
    return render(request, "marketing/landing.html", **_landing_context())


@router.get("/thanks", include_in_schema=False)
async def thanks(request: Request):
    return render(request, "marketing/thanks.html")


# The legal pages. These are not optional furniture: A2P 10DLC campaign
# registration requires a public privacy policy URL and a terms URL, and a
# carrier reviewer reads them. See docs/whatsapp-templates.md and
# app/web/brand.py.


@router.get("/privacy", include_in_schema=False)
async def privacy(request: Request):
    return render(request, "marketing/privacy.html")


@router.get("/terms", include_in_schema=False)
async def terms(request: Request):
    return render(request, "marketing/terms.html")


@router.get("/messaging", include_in_schema=False)
async def messaging(request: Request):
    """Opt-in, frequency, rates and how to stop - what carrier vetting looks
    for, and what the footer of every page links to."""
    return render(request, "marketing/messaging.html")


@router.post("/signup", include_in_schema=False)
async def signup(
    request: Request,
    business_name: str = Form(""),
    contact_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    city: str = Form(""),
    trade: str = Form(""),
    message: str = Form(""),
    # Honeypot: a real browser leaves it empty because it is hidden.
    website: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    def again(error: str):
        return render(
            request,
            "marketing/landing.html",
            **_landing_context(),
            error=error,
            form={
                "business_name": business_name,
                "contact_name": contact_name,
                "phone": phone,
                "email": email,
                "city": city,
                "trade": trade,
                "message": message,
            },
            status_code=400,
        )

    if website.strip():
        # Silently accept and discard - a bot that gets an error learns.
        log.info("signup honeypot tripped")
        return RedirectResponse("/thanks", status_code=303)

    if not business_name.strip():
        return again("Please tell us the name of your shop.")

    normalised = phone_util.normalise(phone)
    if normalised is None:
        return again(
            "That phone number does not look right. "
            "A mobile number we can text works best."
        )

    address = request.client.host if request.client else "unknown"
    if _address_limited(address):
        log.warning("signup rate limit hit for an address")
        return again("Too many submissions just now. Please try again later.")

    since = repo_admin.utcnow() - SIGNUP_WINDOW
    already = await repo_admin.signups_with_phone_since(session, normalised, since)
    if already >= MAX_SIGNUPS_PER_NUMBER:
        # Looks like success: they almost certainly did already reach us.
        log.info("duplicate signup for the same number, ignored")
        return RedirectResponse("/thanks", status_code=303)

    row = await repo_admin.create_signup(
        session,
        business_name=business_name.strip()[:200],
        contact_name=contact_name.strip()[:200] or None,
        phone=normalised,
        email=email.strip()[:200] or None,
        city=city.strip()[:120] or None,
        trade=trade.strip()[:120] or None,
        message=message.strip()[:2000] or None,
        source="landing",
    )
    _record_address(address)
    # Loud on purpose: until there is an alerting channel, the log and the
    # console's Signups tab are how a new shop gets noticed.
    log.warning(
        "NEW SIGNUP %s (%s) from %s - signup id %s",
        business_name.strip()[:80],
        normalised,
        city.strip()[:40] or "unknown",
        row.id,
    )
    return RedirectResponse("/thanks", status_code=303)
