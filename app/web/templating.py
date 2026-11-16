"""Jinja environment, filters and the one render helper the routes use.

Autoescaping is on (`Jinja2Templates` sets it for .html), which matters here
more than usual: every page renders text a stranger typed into an SMS.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

from app.agent.language import LANGUAGE_NAMES
from app.core.handoff import label as handoff_label
from app.web import phone as phone_util
from app.web.auth import Principal
from app.web.brand import BRAND
from app.web.locales.en import COPY as DEFAULT_COPY

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _zone(tzname: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tzname or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def in_tz(value: datetime | None, tzname: str | None = "UTC") -> str:
    """"Wed 12 Nov, 9:00 AM" in the shop's own timezone.

    Built by hand rather than with %-d / %-I, which are not portable to
    Windows - the same reason `core.booking.slot_label` does it this way.
    """
    if value is None:
        return "-"
    local = _aware(value).astimezone(_zone(tzname))
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return (
        f"{local.strftime('%a')} {local.day} {local.strftime('%b')}, "
        f"{hour}:{local.strftime('%M')} {suffix}"
    )


def day_in_tz(value: datetime | None, tzname: str | None = "UTC") -> str:
    if value is None:
        return "-"
    local = _aware(value).astimezone(_zone(tzname))
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')}"


def clock_in_tz(value: datetime | None, tzname: str | None = "UTC") -> str:
    if value is None:
        return "-"
    local = _aware(value).astimezone(_zone(tzname))
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{hour}:{local.strftime('%M')} {suffix}"


def ago(value: datetime | None) -> str:
    if value is None:
        return "-"
    seconds = max((datetime.now(UTC) - _aware(value)).total_seconds(), 0)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    days = int(seconds // 86400)
    return "yesterday" if days == 1 else f"{days}d ago"


def money(value: float | None) -> str:
    """Claude costs run to fractions of a cent, so the precision has to adapt."""
    amount = float(value or 0)
    if amount == 0:
        return "$0"
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:,.2f}"


def language_name(code: str | None) -> str:
    if not code:
        return "-"
    return LANGUAGE_NAMES.get(code, code)


def number(value: float | None) -> str:
    return f"{int(value or 0):,}"


templates.env.filters["in_tz"] = in_tz
templates.env.filters["day_in_tz"] = day_in_tz
templates.env.filters["clock_in_tz"] = clock_in_tz
templates.env.filters["ago"] = ago
templates.env.filters["money"] = money
templates.env.filters["phone"] = phone_util.display
templates.env.filters["mask_phone"] = phone_util.mask
templates.env.filters["language_name"] = language_name
templates.env.filters["number"] = number
#: A handoff code to the sentence an owner would use. Never raises, and
#: shows an unrecognised code as itself - a blank cell would read as "no
#: handoff" when there was one.
templates.env.filters["handoff_label"] = handoff_label


def render(
    request: Request,
    name: str,
    *,
    principal: Principal | None = None,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    """Every template gets the request, the principal and a CSRF token."""
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            # Available to every template, so the brand lives in one file.
            "brand": BRAND,
            # The default locale's copy, as a fallback rather than a choice.
            # `_nav.html` and `_footer.html` are included by every page that
            # extends marketing/base.html, including the legal pages, which
            # have no locale of their own - without this, adding a translated
            # string to the nav would blank it on /privacy. A route that
            # resolves a locale passes its own `copy` in `context`, and the
            # spread below means that one wins.
            "copy": DEFAULT_COPY,
            # The home path for the locale being rendered, so the nav and the
            # footer can link to it without knowing which locale they are in.
            # "/" by default; a localised route passes "/es".
            "home": "/",
            "principal": principal,
            "csrf_token": principal.csrf_token if principal else "",
            "now": datetime.now(UTC),
            **context,
        },
        status_code=status_code,
    )
