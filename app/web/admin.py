"""The internal console.

Everything that used to need `psql` or a hand-edited seed script: provision a
shop, edit its config, read the rendered system prompt, flip shadow to live,
read any conversation, see what it costs and who changed what.

Two things make this safe to point at production data:

  * every route depends on `admin_principal`, so an anonymous request is
    redirected to the login page before a query runs;
  * every state-changing route depends on `admin_post`, which re-checks the
    session *and* the CSRF token, and writes an `audit_log` row.

Saving config does three things in one transaction - write the row, drop the
tenant-router cache, and re-hash `prompt_version`. Miss the second and the shop
keeps answering from a stale prompt for up to a minute; miss the third and we
lose the only record of which prompt is live.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import repo, repo_admin, repo_shop
from app.agent.prompt import config_prompt_version
from app.core import defaults
from app.core.router import invalidate, load_shop
from app.db import get_session
from app.models import (
    Channel,
    ClientStatus,
    ConversationStatus,
    SessionKind,
    SignupStatus,
)
from app.web import forms
from app.web import phone as phone_util
from app.web.auth import (
    ADMIN_COOKIE,
    Principal,
    admin_post,
    admin_principal,
    authenticate_admin,
    clear_cookie,
    clear_login_failures,
    end_session,
    login_locked,
    principal_from_token,
    record_login_failure,
    set_cookie,
    start_session,
)
from app.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

STATUS_CHOICES = [status.value for status in ClientStatus]
#: Every channel the data model knows. `voice` now has a real adapter (Retell
#: answers the call; see app/core/voice.py), and it is also still the channel the
#: missed-call text-back webhook resolves against, so a shop may have a voice
#: endpoint for either reason.
CHANNEL_CHOICES = [channel.value for channel in Channel]
#: Statuses whose owners may hold a dashboard session.
DASHBOARD_STATUSES = {ClientStatus.shadow, ClientStatus.live, ClientStatus.paused}


def _provider_for(channel: Channel) -> str:
    """Who actually carries this channel.

    Stored rather than inferred later because `delivery` hands it to the
    adapter, and because an endpoint outlives whichever provider we were using
    when it was created.
    """
    if channel is Channel.simulator:
        return "local"
    if channel is Channel.voice:
        return "retell"
    return "twilio"


def _see(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _safe_next(raw: str) -> str:
    """Only a relative path inside the console. Anything else is an open
    redirect waiting to be used in a phishing mail."""
    if raw.startswith("/admin") and "//" not in raw and "\\" not in raw:
        return raw
    return "/admin"


async def _chrome(session: AsyncSession) -> dict:
    """Context every console page needs for its navigation."""
    return {"new_signups": await repo_admin.count_new_signups(session)}


# ------------------------------------------------------------------------ login


@router.get("/login", include_in_schema=False)
async def login_form(
    request: Request, session: AsyncSession = Depends(get_session)
):
    existing = await principal_from_token(
        session, request.cookies.get(ADMIN_COOKIE), kind=SessionKind.admin
    )
    if existing is not None:
        return _see("/admin")
    return render(
        request, "admin/login.html", next_url=_safe_next(request.query_params.get("next", ""))
    )


@router.post("/login", include_in_schema=False)
async def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next_url: str = Form("/admin"),
    session: AsyncSession = Depends(get_session),
):
    """No CSRF token here: there is no session yet to protect, and SameSite=Lax
    already stops a cross-site POST from carrying one."""
    target = _safe_next(next_url)
    if login_locked(email):
        log.warning("admin login locked out")
        return render(
            request,
            "admin/login.html",
            error="Too many failed attempts. Try again in 15 minutes.",
            email=email,
            next_url=target,
            status_code=429,
        )

    admin_id = await authenticate_admin(session, email, password)
    if admin_id is None:
        record_login_failure(email)
        return render(
            request,
            "admin/login.html",
            error="That email and password do not match.",
            email=email,
            next_url=target,
            status_code=401,
        )

    clear_login_failures(email)
    token, principal = await start_session(
        session,
        kind=SessionKind.admin,
        label=email.strip().lower(),
        admin_user_id=admin_id,
        user_agent=request.headers.get("user-agent"),
    )
    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.admin,
        action="admin.login",
    )
    response = _see(target)
    set_cookie(response, SessionKind.admin, token)
    return response


@router.post("/logout", include_in_schema=False)
async def logout(
    principal: Principal = Depends(admin_post),
    session: AsyncSession = Depends(get_session),
):
    await end_session(session, principal)
    response = _see("/admin/login")
    clear_cookie(response, SessionKind.admin)
    return response


# ------------------------------------------------------------------- shop list


@router.get("", include_in_schema=False)
async def shops(
    request: Request,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    clients = await repo_admin.list_clients(session)
    since_week = repo_admin.utcnow() - timedelta(days=7)
    since_month = repo_admin.utcnow() - timedelta(days=30)
    messages = await repo_admin.message_counts_since(session, since_week)
    costs = await repo_admin.cost_since(session, since_month)
    attention = await repo_admin.attention_counts(session)
    totals = await repo_admin.platform_totals(session, since=since_month)

    rows = [
        {
            "client": client,
            "messages": messages.get(client.id, {"in": 0, "out": 0}),
            "cost": costs.get(client.id, 0.0),
            "attention": attention.get(client.id, {"handoffs": 0, "drafts": 0}),
        }
        for client in clients
    ]
    return render(
        request,
        "admin/shops.html",
        principal=principal,
        section="shops",
        rows=rows,
        totals=totals,
        **await _chrome(session),
    )


# --------------------------------------------------------------- new shop wizard


@router.get("/shops/new", include_in_schema=False)
async def new_shop_form(
    request: Request,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    signup = None
    signup_id = request.query_params.get("signup")
    if signup_id:
        try:
            signup = await repo_admin.get_signup(session, uuid.UUID(signup_id))
        except ValueError:
            signup = None
    return render(
        request,
        "admin/shop_new.html",
        principal=principal,
        section="shops",
        timezones=defaults.TIMEZONES,
        verticals=defaults.VERTICALS,
        signup=signup,
        **await _chrome(session),
    )


@router.post("/shops", include_in_schema=False)
async def create_shop(
    request: Request,
    name: str = Form(""),
    timezone: str = Form("America/Chicago"),
    owner_phone: str = Form(""),
    vertical: str = Form("other"),
    enabled_languages: str = Form("en"),
    owner_language: str = Form("en"),
    sms_number: str = Form(""),
    whatsapp_id: str = Form(""),
    voice_number: str = Form(""),
    signup_id: str = Form(""),
    principal: Principal = Depends(admin_post),
    session: AsyncSession = Depends(get_session),
):
    """Creates the shop in `shadow`, then sends you to its config page.

    Deliberately two steps: a shop is useful the moment it exists (it can
    receive and draft), and the long tail of config is easier to fill in
    against a real row than in one giant form.
    """

    def again(error: str):
        return render(
            request,
            "admin/shop_new.html",
            principal=principal,
            section="shops",
            timezones=defaults.TIMEZONES,
            verticals=defaults.VERTICALS,
            error=error,
            form={
                "name": name,
                "timezone": timezone,
                "owner_phone": owner_phone,
                "vertical": vertical,
                "enabled_languages": enabled_languages,
                "owner_language": owner_language,
                "sms_number": sms_number,
                "whatsapp_id": whatsapp_id,
                "voice_number": voice_number,
            },
            status_code=400,
        )

    if not name.strip():
        return again("The shop needs a name.")

    owner = phone_util.normalise(owner_phone)
    if owner is None:
        return again(
            "The owner's phone number is not valid. Everything reaches them "
            "there, so it has to be right."
        )

    languages = forms.csv_list(enabled_languages) or ["en"]
    if "en" not in languages:
        # The doc is explicit: English is always included in a first greeting.
        languages.append("en")
    owner_lang = (owner_language or "en").strip().lower() or "en"

    # Endpoints first, so a clash does not leave a half-created shop behind.
    wanted: list[tuple[Channel, str, str | None, str]] = []
    if sms_number.strip():
        normalised = phone_util.normalise(sms_number)
        if normalised is None:
            return again("The SMS number is not a valid phone number.")
        wanted.append((Channel.sms, normalised, normalised, "twilio"))
    if whatsapp_id.strip():
        raw = whatsapp_id.strip()
        # Twilio sends `To` as "whatsapp:+1312...", and the router matches it
        # verbatim, so the prefix is part of the stored id.
        if not raw.startswith("whatsapp:"):
            normalised = phone_util.normalise(raw)
            if normalised is None:
                return again(
                    "The WhatsApp sender is not valid. Use the number, or the "
                    "full whatsapp:+1... form."
                )
            raw = f"whatsapp:{normalised}"
        wanted.append((Channel.whatsapp, raw, raw.removeprefix("whatsapp:"), "twilio"))
    if voice_number.strip():
        normalised = phone_util.normalise(voice_number)
        if normalised is None:
            return again("The voice number is not a valid phone number.")
        # Retell, not Twilio: a voice endpoint is the number Retell answers
        # on. A shop that forwards through Twilio still registers the Retell
        # number here, because that is the `to_number` the webhook carries.
        wanted.append((Channel.voice, normalised, normalised, "retell"))

    for channel, external_id, _number, _provider in wanted:
        clash = await repo.get_endpoint(session, channel, external_id)
        if clash is not None:
            return again(
                f"{external_id} is already mapped to another shop on "
                f"{channel.value}. A number can only belong to one shop."
            )

    client = await repo_admin.create_client_with_config(
        session,
        name=name.strip()[:200],
        timezone=timezone.strip() or "America/Chicago",
        owner_phone=owner,
        languages=languages,
        config=defaults.new_shop_config(
            owner_language=owner_lang,
            default_customer_language=languages[0],
            enabled_languages=languages,
            vertical=vertical,
        ),
    )

    for channel, external_id, number, provider in wanted:
        await repo.add_endpoint(
            session,
            client.id,
            channel=channel,
            external_id=external_id,
            number=number,
            provider=provider,
        )
    # Always give a new shop the local simulator, so it can be exercised at
    # /sim before a single real number is pointed at it.
    await repo.add_endpoint(
        session,
        client.id,
        channel=Channel.simulator,
        external_id=f"sim-{client.id.hex[:8]}",
        number=None,
        provider="local",
    )

    if signup_id:
        try:
            await repo_admin.update_signup(
                session,
                uuid.UUID(signup_id),
                status=SignupStatus.converted,
                client_id=client.id,
            )
        except ValueError:
            log.warning("ignoring malformed signup id on shop creation")

    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.admin,
        action="shop.create",
        client_id=client.id,
        target=client.name,
        detail={"languages": languages, "endpoints": [c.value for c, *_ in wanted]},
    )
    invalidate(client.id)
    return _see(f"/admin/shops/{client.id}/config?created=1")


# ------------------------------------------------------------------ shop detail


async def _client_or_404(session: AsyncSession, client_id: uuid.UUID):
    client = await repo_admin.get_client(session, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="No such shop.")
    return client


@router.get("/shops/{client_id}", include_in_schema=False)
async def shop_detail(
    request: Request,
    client_id: uuid.UUID,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    client = await _client_or_404(session, client_id)
    since = repo_admin.utcnow() - timedelta(days=30)
    return render(
        request,
        "admin/shop_detail.html",
        principal=principal,
        section="shops",
        client=client,
        tz=client.timezone,
        config=client.config,
        endpoints=await repo.list_endpoints(session, client_id),
        stats=await repo_shop.counts_since(session, client_id, since=since),
        spend=await repo_shop.usage_by_day(session, client_id, since=since),
        languages=await repo_shop.languages_seen(session, client_id),
        conversations=await repo_shop.conversation_page(
            session, client_id, limit=8
        ),
        handoffs=await repo_shop.open_handoffs(session, client_id, limit=10),
        drafts=await repo_shop.pending_drafts(session, client_id, limit=10),
        audit=await repo_admin.recent_audit(session, client_id=client_id, limit=10),
        statuses=STATUS_CHOICES,
        channels=CHANNEL_CHOICES,
        **await _chrome(session),
    )


@router.post("/shops/{client_id}/status", include_in_schema=False)
async def set_status(
    client_id: uuid.UUID,
    status: str = Form(""),
    principal: Principal = Depends(admin_post),
    session: AsyncSession = Depends(get_session),
):
    await _client_or_404(session, client_id)
    try:
        target = ClientStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown status.") from None

    await repo.set_client_status(session, client_id, target)
    invalidate(client_id)
    if target not in DASHBOARD_STATUSES:
        # A shop that is no longer running loses its dashboard immediately.
        await repo_admin.revoke_sessions_for_client(session, client_id)
    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.admin,
        action="shop.status",
        client_id=client_id,
        target=target.value,
    )
    return _see(f"/admin/shops/{client_id}")


# ------------------------------------------------------------------ shop config


def _config_form(client, config) -> dict:
    """Config row -> the strings the form shows."""
    booking = (config.booking if config else None) or {}
    handoff = (config.handoff_rules if config else None) or {}
    return {
        "name": client.name,
        "timezone": client.timezone,
        "owner_phone": client.owner_phone or "",
        "plan": client.plan or "pilot",
        "tone": (config.tone if config else "") or "",
        "services": forms.from_lines(config.services if config else []),
        "hours": (config.hours if config else None) or {},
        "price_ranges": forms.price_ranges_to_text(
            config.price_ranges if config else []
        ),
        "faqs": forms.faqs_to_text(config.faqs if config else []),
        "business_info": forms.info_to_text(
            config.business_info if config else {}
        ),
        "handoff_keywords": forms.to_csv(handoff.get("keywords") or []),
        "handoff_notes": handoff.get("notes") or "",
        "max_misunderstandings": handoff.get("max_misunderstandings") or 2,
        "owner_language": (config.owner_language if config else "en") or "en",
        "default_customer_language": (
            (config.default_customer_language if config else "en") or "en"
        ),
        "enabled_languages": forms.to_csv(
            (config.enabled_languages if config else None) or ["en"]
        ),
        "country_language_overrides": forms.json_dump(
            (config.country_language_overrides if config else None) or {}
        ),
        "calendar_id": (config.calendar_id if config else "") or "",
        "booking": forms.json_dump(booking),
        "channel_style": forms.json_dump(
            (config.channel_style if config else None) or {}
        ),
        "missed_call_channel": (
            (config.missed_call_channel if config else "sms") or "sms"
        ),
        "owner_channel": (config.owner_channel if config else "auto") or "auto",
        "vertical": (config.vertical if config else "other") or "other",
        "job_noun": (config.job_noun if config else "job") or "job",
        "reminder_hours_before": (
            config.reminder_hours_before if config else 24
        ),
        "summary_enabled": bool(config.summary_enabled) if config else True,
        "summary_hour": config.summary_hour if config else 7,
        "templates": forms.json_dump((config.templates if config else None) or {}),
    }


@router.get("/shops/{client_id}/config", include_in_schema=False)
async def config_form(
    request: Request,
    client_id: uuid.UUID,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    client = await _client_or_404(session, client_id)
    return render(
        request,
        "admin/shop_config.html",
        principal=principal,
        section="shops",
        client=client,
        config=client.config,
        form=_config_form(client, client.config),
        weekdays=defaults.WEEKDAYS,
        timezones=defaults.TIMEZONES,
        verticals=defaults.VERTICALS,
        ok=(
            "Shop created. Fill in the services, hours and prices before you "
            "take it out of shadow mode."
            if request.query_params.get("created")
            else None
        ),
        **await _chrome(session),
    )


@router.post("/shops/{client_id}/config", include_in_schema=False)
async def save_config(
    request: Request,
    client_id: uuid.UUID,
    principal: Principal = Depends(admin_post),
    session: AsyncSession = Depends(get_session),
):
    client = await _client_or_404(session, client_id)
    data = await request.form()

    def value(key: str) -> str:
        return str(data.get(key) or "")

    def again(error: str):
        submitted = _config_form(client, client.config)
        submitted.update(
            {
                key: value(key)
                for key in submitted
                if key != "hours" and key in data
            }
        )
        submitted["hours"] = {
            day: value(f"hours_{day}") for day in defaults.WEEKDAYS
        }
        return render(
            request,
            "admin/shop_config.html",
            principal=principal,
            section="shops",
            client=client,
            config=client.config,
            form=submitted,
            weekdays=defaults.WEEKDAYS,
            timezones=defaults.TIMEZONES,
            verticals=defaults.VERTICALS,
            error=error,
            status_code=400,
        )

    owner = phone_util.normalise(value("owner_phone"))
    if owner is None:
        return again("The owner's phone number is not valid.")
    if not value("name").strip():
        return again("The shop needs a name.")

    try:
        hours = forms.hours_from_values(
            {day: value(f"hours_{day}") for day in defaults.WEEKDAYS}
        )
        config_fields = {
            "tone": value("tone").strip(),
            "services": forms.lines(value("services")),
            "hours": hours,
            "price_ranges": forms.price_ranges_from_text(value("price_ranges")),
            "faqs": forms.faqs_from_text(value("faqs")),
            "business_info": forms.info_from_text(value("business_info")),
            "handoff_rules": {
                "keywords": forms.csv_list(value("handoff_keywords")),
                "notes": value("handoff_notes").strip(),
                "max_misunderstandings": forms.positive_int(
                    value("max_misunderstandings"),
                    label="Hand off after",
                    default=2,
                    maximum=10,
                ),
            },
            "owner_language": value("owner_language").strip().lower() or "en",
            "default_customer_language": (
                value("default_customer_language").strip().lower() or "en"
            ),
            "enabled_languages": forms.csv_list(value("enabled_languages")) or ["en"],
            "country_language_overrides": forms.json_object(
                value("country_language_overrides"),
                label="Country language overrides",
            ),
            "calendar_id": value("calendar_id").strip() or None,
            "booking": forms.json_object(value("booking"), label="Booking"),
            "channel_style": forms.json_object(
                value("channel_style"), label="Channel style"
            ),
            "missed_call_channel": value("missed_call_channel").strip() or "sms",
            "owner_channel": value("owner_channel").strip() or "auto",
            "vertical": (
                value("vertical").strip()
                if value("vertical").strip() in defaults.VERTICALS
                else "other"
            ),
            "job_noun": value("job_noun").strip().lower()[:32] or "job",
            # 0 is meaningful here: it turns reminders off.
            "reminder_hours_before": forms.bounded_int(
                value("reminder_hours_before"),
                label="Reminder hours before",
                default=24,
                minimum=0,
                maximum=168,
            ),
            # Absent entirely (a partial POST) keeps whatever is set; present
            # and empty is the form's own "off". Without this distinction a
            # request that simply omitted the field would quietly disable the
            # summary and nobody would notice until an owner stopped getting it.
            "summary_enabled": (
                value("summary_enabled").strip() != ""
                if "summary_enabled" in data
                else bool(client.config.summary_enabled if client.config else True)
            ),
            "summary_hour": forms.bounded_int(
                value("summary_hour"),
                label="Summary hour",
                default=7,
                minimum=0,
                maximum=23,
            ),
            "templates": forms.json_object(
                value("templates"), label="WhatsApp templates"
            ),
        }
    except forms.FormError as exc:
        return again(str(exc))

    if "en" not in config_fields["enabled_languages"]:
        config_fields["enabled_languages"].append("en")

    before = {
        "tone": client.config.tone if client.config else "",
        "services": client.config.services if client.config else [],
        "hours": client.config.hours if client.config else {},
        "price_ranges": client.config.price_ranges if client.config else [],
        "faqs": client.config.faqs if client.config else [],
        "business_info": client.config.business_info if client.config else {},
        "handoff_rules": client.config.handoff_rules if client.config else {},
        "booking": client.config.booking if client.config else {},
        "channel_style": client.config.channel_style if client.config else {},
    }

    await repo.update_client(
        session,
        client_id,
        name=value("name").strip()[:200],
        timezone=value("timezone").strip() or client.timezone,
        owner_phone=owner,
        languages=config_fields["enabled_languages"],
        plan=value("plan").strip() or client.plan,
    )
    await repo.update_config(session, client_id, **config_fields)

    # The three steps that have to happen together.
    invalidate(client_id)
    shop = await load_shop(session, client_id, use_cache=False)
    version = config_prompt_version(
        {"name": shop.name, "timezone": shop.timezone, "config": shop.config},
        CHANNEL_CHOICES,
    )
    await repo.set_prompt_version(session, client_id, version)

    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.admin,
        action="shop.config",
        client_id=client_id,
        target=shop.name,
        detail={
            "changed": forms.changed_fields(before, config_fields),
            "prompt_version": version,
        },
    )
    return _see(f"/admin/shops/{client_id}?saved=1")


@router.get("/shops/{client_id}/prompt", include_in_schema=False)
async def shop_prompt(
    request: Request,
    client_id: uuid.UUID,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    """What Claude actually sees. The first thing to read when a shop answers
    oddly, and the reason the config page is worth having at all."""
    client = await _client_or_404(session, client_id)
    shop = await load_shop(session, client_id, use_cache=False)
    channel = request.query_params.get("channel", "sms")
    if channel not in CHANNEL_CHOICES:
        channel = "sms"
    text, version = shop.system_prompt(channel)
    # The stored version covers every channel's prompt at once, so it cannot be
    # compared against this one channel's hash - that always looks like drift.
    config_version = config_prompt_version(
        {"name": shop.name, "timezone": shop.timezone, "config": shop.config},
        CHANNEL_CHOICES,
    )
    stored = client.config.prompt_version if client.config else None
    return render(
        request,
        "admin/shop_prompt.html",
        principal=principal,
        section="shops",
        client=client,
        channel=channel,
        channels=CHANNEL_CHOICES,
        prompt_text=text,
        version=version,
        config_version=config_version,
        stored_version=stored,
        drifted=bool(stored) and stored != config_version,
        **await _chrome(session),
    )


# -------------------------------------------------------------------- endpoints


@router.post("/shops/{client_id}/endpoints", include_in_schema=False)
async def add_endpoint(
    client_id: uuid.UUID,
    channel: str = Form(""),
    external_id: str = Form(""),
    principal: Principal = Depends(admin_post),
    session: AsyncSession = Depends(get_session),
):
    await _client_or_404(session, client_id)
    try:
        target = Channel(channel)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown channel.") from None

    raw = external_id.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="An endpoint id is required.")
    if target is Channel.whatsapp and not raw.startswith("whatsapp:"):
        normalised = phone_util.normalise(raw)
        raw = f"whatsapp:{normalised or raw}"
    elif target in (Channel.sms, Channel.voice):
        raw = phone_util.normalise(raw) or raw

    clash = await repo.get_endpoint(session, target, raw)
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{raw} already belongs to another shop on {target.value}.",
        )

    await repo.add_endpoint(
        session,
        client_id,
        channel=target,
        external_id=raw,
        number=raw.removeprefix("whatsapp:") if target is not Channel.simulator else None,
        provider=_provider_for(target),
    )
    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.admin,
        action="endpoint.add",
        client_id=client_id,
        target=f"{target.value}:{raw}",
    )
    return _see(f"/admin/shops/{client_id}")


@router.post("/shops/{client_id}/endpoints/{endpoint_id}", include_in_schema=False)
async def toggle_endpoint(
    client_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    active: str = Form("false"),
    principal: Principal = Depends(admin_post),
    session: AsyncSession = Depends(get_session),
):
    await _client_or_404(session, client_id)
    wanted = active.lower() in {"1", "true", "on", "yes"}
    await repo.set_endpoint_active(session, client_id, endpoint_id, wanted)
    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.admin,
        action="endpoint.active" if wanted else "endpoint.inactive",
        client_id=client_id,
        target=str(endpoint_id),
    )
    return _see(f"/admin/shops/{client_id}")


# ----------------------------------------------------------------- conversations


@router.get("/shops/{client_id}/conversations", include_in_schema=False)
async def shop_conversations(
    request: Request,
    client_id: uuid.UUID,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    client = await _client_or_404(session, client_id)
    raw_status = request.query_params.get("status", "")
    status = None
    if raw_status:
        try:
            status = ConversationStatus(raw_status)
        except ValueError:
            status = None
    return render(
        request,
        "admin/conversations.html",
        principal=principal,
        section="shops",
        client=client,
        tz=client.timezone,
        rows=await repo_shop.conversation_page(
            session, client_id, status=status, limit=100
        ),
        status=raw_status,
        **await _chrome(session),
    )


@router.get(
    "/shops/{client_id}/conversations/{conversation_id}", include_in_schema=False
)
async def shop_conversation(
    request: Request,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    client = await _client_or_404(session, client_id)
    found = await repo_shop.conversation_with_customer(
        session, client_id, conversation_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail="No such conversation.")
    conversation, customer = found
    return render(
        request,
        "admin/conversation.html",
        principal=principal,
        section="shops",
        client=client,
        tz=client.timezone,
        noun=(client.config.job_noun if client.config else "job") or "job",
        conversation=conversation,
        customer=customer,
        messages=await repo_shop.full_transcript(
            session, client_id, conversation_id
        ),
        back=f"/admin/shops/{client_id}/conversations",
        **await _chrome(session),
    )


# -------------------------------------------------------------------- signups


@router.get("/signups", include_in_schema=False)
async def signups(
    request: Request,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    return render(
        request,
        "admin/signups.html",
        principal=principal,
        section="signups",
        rows=await repo_admin.list_signups(session),
        statuses=[s.value for s in SignupStatus],
        **await _chrome(session),
    )


@router.post("/signups/{signup_id}", include_in_schema=False)
async def update_signup(
    signup_id: uuid.UUID,
    status: str = Form(""),
    notes: str = Form(""),
    principal: Principal = Depends(admin_post),
    session: AsyncSession = Depends(get_session),
):
    if await repo_admin.get_signup(session, signup_id) is None:
        raise HTTPException(status_code=404, detail="No such signup.")
    target = None
    if status:
        try:
            target = SignupStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown status.") from None
    await repo_admin.update_signup(
        session, signup_id, status=target, notes=notes.strip() or None
    )
    await repo_admin.audit(
        session,
        actor=principal.label,
        actor_kind=SessionKind.admin,
        action="signup.update",
        target=f"{signup_id}:{status or 'notes'}",
    )
    return _see("/admin/signups")


# ---------------------------------------------------------------- usage & audit


@router.get("/usage", include_in_schema=False)
async def usage(
    request: Request,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    days = 30
    try:
        days = max(1, min(90, int(request.query_params.get("days", "30"))))
    except ValueError:
        days = 30
    since = repo_admin.utcnow() - timedelta(days=days)
    by_model = await repo_admin.usage_by_model(session, since=since)
    clients = await repo_admin.list_clients(session)
    costs = await repo_admin.cost_since(session, since)
    messages = await repo_admin.message_counts_since(session, since)
    per_shop = sorted(
        (
            {
                "client": client,
                "cost": costs.get(client.id, 0.0),
                "messages": messages.get(client.id, {"in": 0, "out": 0}),
            }
            for client in clients
        ),
        key=lambda row: row["cost"],
        reverse=True,
    )
    cache_read = sum(row["cache_read"] for row in by_model)
    cache_write = sum(row["cache_write"] for row in by_model)
    fresh_input = sum(row["input_tokens"] for row in by_model)
    return render(
        request,
        "admin/usage.html",
        principal=principal,
        section="usage",
        days=days,
        by_model=by_model,
        per_shop=per_shop,
        total_cost=sum(row["cost"] for row in by_model),
        total_calls=sum(row["calls"] for row in by_model),
        cache_read=cache_read,
        cache_write=cache_write,
        # The canary from the Phase 1 plan: if this stays at 0 across repeated
        # messages, something in the prompt builder is moving between calls.
        cache_share=(
            round(100 * cache_read / (cache_read + cache_write + fresh_input), 1)
            if (cache_read + cache_write + fresh_input)
            else 0.0
        ),
        **await _chrome(session),
    )


@router.get("/audit", include_in_schema=False)
async def audit(
    request: Request,
    principal: Principal = Depends(admin_principal),
    session: AsyncSession = Depends(get_session),
):
    return render(
        request,
        "admin/audit.html",
        principal=principal,
        section="audit",
        rows=await repo_admin.recent_audit(session, limit=200),
        **await _chrome(session),
    )
