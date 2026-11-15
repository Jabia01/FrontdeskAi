"""The public landing page and its signup form.

The only routes in the app that need no login, which makes them the only ones a
stranger can hammer. So: a honeypot field, a per-number limit and a per-address
limit, and no reflection of anything submitted back into the page.

A signup is *not* a `leads` row. `leads` belongs to a shop and holds a customer
who wants a callback; a `signups` row is a prospective shop, and has no
`client_id` at all until the console converts it.
"""

from __future__ import annotations

import html
import json
import logging
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import repo_admin
from app.config import get_settings
from app.db import get_session
from app.web import content, infra, locales, signup_form
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


def _consent_detail(copy: dict) -> str:
    """The small print under the consent box, with its link in place.

    Built here rather than with chained `replace` filters in the template
    because the sentence is a translated string with the link *inside* it -
    Spanish will not put it where English does - and because the result is
    marked safe, which is only defensible when the only HTML in it was put
    there by this function.
    """
    return html.escape(copy["consent_detail"]).replace(
        "{privacy}",
        f'<a href="/privacy">{html.escape(copy["privacy_link"])}</a>',
    )


def _home(locale: str | None) -> str:
    """The home path for a locale: "/" for English, "/es" otherwise.

    The nav and the footer link through this rather than hardcoding "/", so a
    Spanish visitor clicking "Precios" stays on the Spanish page instead of
    being dropped back into English halfway through reading.
    """
    code = (locale or locales.DEFAULT_LOCALE).lower()
    return "/" if code == locales.DEFAULT_LOCALE else f"/{code}"


def _alternates(locale: str | None) -> list[dict[str, str]]:
    """hreflang alternates, and the data behind the language switch.

    Only publishable locales: a half-translated language that `missing()`
    still has something to say about should not be advertised to a search
    engine or offered in the switcher. The switch is a plain link per
    language, so it works with JavaScript off.
    """
    current = (locale or locales.DEFAULT_LOCALE).lower()
    return [
        {
            "code": code,
            "href": _home(code),
            "label": locales.LOCALES[code].COPY["language_name"],
            "current": code == current,
        }
        for code in locales.LOCALES
        if locales.is_publishable(code)
    ]


def _landing_context(locale: str | None = None) -> dict:
    """Everything the landing page renders, for one locale.

    `locale` is the single seam the whole translation effort hangs off: every
    string below comes from the locale module rather than from a template, so
    a second language is a file and not a second set of templates.

    The two `*_json` values are embedded in the page for site.js to read -
    `tojson` rather than string building, so a quote in a Spanish sentence
    cannot break the script tag.
    """
    text = locales.get(locale)
    return {
        "locale": (locale or locales.DEFAULT_LOCALE),
        "consent_detail": _consent_detail(text.COPY["signup"]),
        "html_lang": (locale or locales.DEFAULT_LOCALE),
        "copy": text.COPY,
        "home": _home(locale),
        "alternates": _alternates(locale),
        "conversations": text.DEMO_CONVERSATIONS,
        "conversations_json": json.dumps(
            text.DEMO_CONVERSATIONS, ensure_ascii=False
        ),
        "headline_stem": text.HEADLINE_STEM,
        "headline_tails": text.HEADLINE_TAILS,
        "demo_shop": text.DEMO_SHOP,
        "demo_caption": text.DEMO_CAPTION,
        "owner_alert": text.OWNER_ALERT,
        "metrics": text.METRICS,
        "channels": text.CHANNELS,
        "features": text.FEATURES,
        "languages": text.LANGUAGE_DEMO,
        "languages_json": json.dumps(text.LANGUAGE_DEMO, ensure_ascii=False),
        "sectors": text.SECTORS,
        # The signup form's dropdowns. Derived from the niche specs rather
        # than written into the template - see app/web/signup_form.py.
        "niche_choices": signup_form.NICHE_CHOICES,
        "country_choices": signup_form.COUNTRY_CHOICES,
        "language_choices": signup_form.LANGUAGE_CHOICES,
        "crm_choices": signup_form.CRM_CHOICES,
        "faq": text.FAQ,
        # Empty while the quotes are invented. `content.SHOW_REVIEWS` rather
        # than the locale's own flag: it is the module-level name the existing
        # tests monkeypatch, and the rule is a product decision rather than a
        # per-language one - a Spanish visitor should be no likelier to read an
        # invented testimonial than an English one.
        "reviews": list(text.REVIEWS) if content.SHOW_REVIEWS else [],
        "reviews_are_samples": any(r.get("sample") for r in text.REVIEWS),
    }


@router.get("/", include_in_schema=False)
async def landing(request: Request):
    return render(request, "marketing/landing.html", **_landing_context())


def _niche_context(slug: str, locale: str | None = None) -> dict:
    """A niche page: its own copy, and only its own captured conversations."""
    text = locales.get(locale)
    page = text.NICHE_PAGES[slug]
    wanted = page["demo"]
    conversations = [
        c for c in text.DEMO_CONVERSATIONS if c["code"] in wanted
    ] or text.DEMO_CONVERSATIONS
    return {
        "page": page,
        "copy": text.COPY,
        "locale": (locale or locales.DEFAULT_LOCALE),
        "html_lang": (locale or locales.DEFAULT_LOCALE),
        "home": _home(locale),
        "alternates": _alternates(locale),
        "conversations": conversations,
        "conversations_json": json.dumps(conversations, ensure_ascii=False),
        "demo_caption": text.DEMO_CAPTION,
    }


def _thanks_context(locale: str | None = None) -> dict:
    text = locales.get(locale)
    return {
        "copy": text.COPY,
        "locale": (locale or locales.DEFAULT_LOCALE),
        "html_lang": (locale or locales.DEFAULT_LOCALE),
        "home": _home(locale),
        "alternates": _alternates(locale),
    }


def _register_pages() -> None:
    """Every marketing page, for every locale, generated in a loop.

    Explicit paths throughout. Deliberately *not* a catch-all `/{slug}` or
    `/{locale}`: either one would shadow /privacy, /terms, /messaging, /gdpr,
    /dpa, /thanks and anything else added under / later, and the failure would
    be a legal page quietly serving a 404 page's content.

    A non-English locale gets its own prefix - /es, /es/dental - and only if
    `locales.is_publishable()` agrees. A locale with a `TODO:` left in it
    simply has no routes, which is a stronger guarantee than a template
    remembering to check.
    """
    for code in locales.LOCALES:
        if code != locales.DEFAULT_LOCALE and not locales.is_publishable(code):
            continue
        prefix = "" if code == locales.DEFAULT_LOCALE else f"/{code}"
        text = locales.get(code)

        if prefix:

            def home(request: Request, code: str = code):
                return render(
                    request, "marketing/landing.html", **_landing_context(code)
                )

            router.add_api_route(
                prefix,
                home,
                methods=["GET"],
                include_in_schema=False,
                name=f"landing_{code}",
            )

            def thanks_page(request: Request, code: str = code):
                return render(
                    request, "marketing/thanks.html", **_thanks_context(code)
                )

            router.add_api_route(
                f"{prefix}/thanks",
                thanks_page,
                methods=["GET"],
                include_in_schema=False,
                name=f"thanks_{code}",
            )

        for slug, page in text.NICHE_PAGES.items():

            def view(request: Request, slug: str = slug, code: str = code):
                return render(
                    request, "marketing/niche.html", **_niche_context(slug, code)
                )

            router.add_api_route(
                prefix + page["path"],
                view,
                methods=["GET"],
                include_in_schema=False,
                name=f"niche_{code}_{slug}",
            )


_register_pages()


@router.get("/thanks", include_in_schema=False)
async def thanks(request: Request):
    return render(request, "marketing/thanks.html", **_thanks_context())


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


def _residency_context() -> dict:
    """The facts both the GDPR page and the DPA render.

    One context for both, so the DPA's annex cannot say Frankfurt while the
    page a buyer read five minutes earlier says Ohio - and all of it comes
    from `app/web/infra.py`, which a deploy test pins to `render.yaml`.
    """
    return {
        "hosting_location": infra.hosting_location(),
        "eu_hosted": infra.hosted_in_eu(),
        "subprocessors": infra.SUBPROCESSORS,
    }


@router.get("/gdpr", include_in_schema=False)
async def gdpr(request: Request):
    """What a clinic's data protection officer reads before signing."""
    return render(request, "marketing/gdpr.html", **_residency_context())


@router.get("/dpa", include_in_schema=False)
async def dpa(request: Request):
    """The Art. 28 processor agreement, published rather than emailed."""
    return render(request, "marketing/dpa.html", **_residency_context())


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
    country: str = Form(""),
    niche: str = Form(""),
    trade: str = Form(""),
    crm_tool: str = Form(""),
    site_url: str = Form(""),
    message: str = Form(""),
    consent: str = Form(""),
    languages: list[str] = Form([]),
    locale: str = Form(""),
    # Honeypot: a real browser leaves it empty because it is hidden. Note the
    # name - `site_url` above is the field a human fills in. The attractive
    # name stays on the trap, because that is the one a bot picks by name.
    website: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    page_locale = locale if locales.is_known(locale) else locales.DEFAULT_LOCALE
    # Where "thanks" lives for the page they submitted from. A Spanish
    # visitor should not be dropped into an English confirmation, and the
    # field is validated above rather than trusted - it arrives in the body.
    thanks_path = (
        "/thanks"
        if page_locale == locales.DEFAULT_LOCALE
        else f"/{page_locale}/thanks"
    )
    picked_niche = signup_form.clean_niche(niche)
    picked_country = signup_form.clean_country(country)
    picked_languages = signup_form.clean_languages(languages)

    def again(error: str):
        return render(
            request,
            "marketing/landing.html",
            **_landing_context(page_locale),
            error=error,
            form={
                "business_name": business_name,
                "contact_name": contact_name,
                "phone": phone,
                "email": email,
                "city": city,
                "country": picked_country or "",
                "niche": picked_niche or "",
                "trade": trade,
                "crm_tool": signup_form.clean_crm(crm_tool) or "",
                "site_url": site_url,
                "message": message,
                "languages": picked_languages,
                "consent": bool(consent.strip()),
            },
            status_code=400,
        )

    if website.strip():
        # Silently accept and discard - a bot that gets an error learns.
        log.info("signup honeypot tripped")
        return RedirectResponse(thanks_path, status_code=303)

    if not business_name.strip():
        return again("Please tell us the name of your business.")

    # Before the phone, because the country decides how the phone is read.
    # A Spanish mobile typed as "600 111 222" is only valid if we know to
    # parse it as Spanish, and telling someone their correct number is wrong
    # is the single most effective way to lose them.
    region = signup_form.region_for(
        picked_country, get_settings().default_phone_region
    )
    normalised = phone_util.normalise(phone, region=region)
    if normalised is None:
        return again(
            "That number does not look right. "
            "A WhatsApp number, with the country code, works best."
        )

    # GDPR Art. 6(1)(a): there is no other lawful basis for texting a
    # stranger who filled in a form, so this is not a formality we can treat
    # as pre-ticked. Validated here and *recorded* below.
    if not consent.strip():
        return again(
            "Please tick the box so we know we may contact you about this."
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
        return RedirectResponse(thanks_path, status_code=303)

    row = await repo_admin.create_signup(
        session,
        business_name=business_name.strip()[:200],
        contact_name=contact_name.strip()[:200] or None,
        phone=normalised,
        email=email.strip()[:200] or None,
        city=city.strip()[:120] or None,
        trade=trade.strip()[:120] or None,
        message=message.strip()[:2000] or None,
        niche=picked_niche,
        country=picked_country,
        languages=picked_languages or None,
        crm_tool=signup_form.clean_crm(crm_tool),
        website_url=signup_form.clean_website(site_url),
        consent_at=repo_admin.utcnow(),
        # The locale rides in `source`, so which language they were reading
        # when they signed up survives without a column.
        source="landing" if page_locale == locales.DEFAULT_LOCALE
        else f"landing/{page_locale}",
    )
    _record_address(address)
    # Loud on purpose: until there is an alerting channel, the log and the
    # console's Signups tab are how a new shop gets noticed.
    log.warning(
        "NEW SIGNUP %s (%s) %s/%s - signup id %s",
        business_name.strip()[:80],
        normalised,
        picked_niche or "no niche",
        picked_country or "no country",
        row.id,
    )
    return RedirectResponse(thanks_path, status_code=303)
