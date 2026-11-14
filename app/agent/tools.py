"""Agent tools.

Two things matter about the list this module produces:

* **It is byte-stable.** Tools render *before* `system` in the cache prefix, so a
  reordered or conditionally-included tool silently destroys the prompt cache.
  The enabled set is therefore fixed per phase, in a fixed order, with schemas
  that contain no per-shop data.
* **Every tool is `strict`.** Forced `tool_choice` returns a 400 on both Haiku
  5.5 and Sonnet 5.5, so the prompt names the tools and `strict: True` keeps the
  arguments schema-valid. That requires `additionalProperties: false` and every
  property listed in `required`.

Phase 2 enables `check_availability`, `book_appointment` and `get_job_status` by
flipping `enabled` - no change to the runner.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.niche import NICHES
from app.core.router import ShopContext
from app.integrations import listings
from app.repo import create_lead as repo_create_lead
from app.repo import hand_off, latest_job_for_item

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolContext:
    session: AsyncSession
    shop: ShopContext
    conversation_id: uuid.UUID
    customer_id: uuid.UUID
    #: Set by handoff_to_human so the caller knows to alert the owner and stop.
    handoff_requested: bool = False
    handoff_reason: str = ""
    handoff_summary: str = ""
    #: Set by book_appointment so the engine can alert the owner.
    booking_id: uuid.UUID | None = None
    booking_summary: str = ""
    #: Overridable clock, so booking tests are not time-dependent.
    now: datetime | None = None


Handler = Callable[..., Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    enabled: bool = True

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": self.input_schema,
        }


# ------------------------------------------------------------------- handlers


async def _get_business_info(ctx: ToolContext, topic: str) -> str:
    config = ctx.shop.config
    topics: dict[str, Any] = {
        "hours": config.get("hours") or {},
        "address": (config.get("business_info") or {}).get("address"),
        "contact": {
            "phone": (config.get("business_info") or {}).get("phone"),
        },
        "services": config.get("services") or [],
        "prices": config.get("price_ranges") or [],
        "payment": (config.get("business_info") or {}).get("payment"),
        "policies": config.get("faqs") or [],
    }
    if topic == "all":
        payload: Any = topics
    else:
        payload = topics.get(topic)

    if payload in (None, {}, []):
        return (
            f"No information configured for '{topic}'. Tell the customer you will "
            f"check with the shop, and hand off."
        )

    import json

    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


async def _create_lead(
    ctx: ToolContext, item: str, issue: str, contact: str
) -> str:
    lead = await repo_create_lead(
        ctx.session,
        ctx.shop.client_id,
        customer_id=ctx.customer_id,
        conversation_id=ctx.conversation_id,
        item=item or None,
        issue=issue or None,
        contact=contact or None,
    )
    log.info("lead %s created for client %s", lead.id, ctx.shop.client_id)
    return (
        "Saved. The business has the details and the contact. Tell the "
        "customer someone will be in touch shortly. Do not promise a "
        "specific time."
    )


async def _handoff_to_human(ctx: ToolContext, reason: str, summary: str) -> str:
    await hand_off(ctx.session, ctx.shop.client_id, ctx.conversation_id, reason)
    ctx.handoff_requested = True
    ctx.handoff_reason = reason
    ctx.handoff_summary = summary
    return (
        "The owner has been alerted and will take over this conversation. "
        "Write one short, calm closing message telling the customer that someone "
        "from the shop will reply personally. Ask no further questions."
    )


async def _check_availability(
    ctx: ToolContext, service: str, preferred_day: str
) -> str:
    """Offer free drop-off slots.

    Returns each slot with an exact `start_at` the model must copy verbatim into
    `book_appointment` - inventing a timestamp is the easiest way to double-book.
    """
    from app.core.booking import BookingError, available_slots, slot_label

    wanted: date | None = None
    if preferred_day.strip():
        try:
            wanted = date.fromisoformat(preferred_day.strip())
        except ValueError:
            return (
                f"'{preferred_day}' is not a date I can read. Ask the customer "
                f"which day suits them and call this again with YYYY-MM-DD, or "
                f"leave it empty for the soonest openings."
            )

    tz = ZoneInfo(ctx.shop.timezone)
    try:
        slots = await available_slots(
            ctx.session, ctx.shop, limit=6, preferred_day=wanted, now=ctx.now
        )
    except BookingError as exc:
        return (
            f"Could not read the calendar ({exc}). Tell the customer you will "
            f"confirm a time shortly, save the details with create_lead, and "
            f"hand off to a human."
        )

    if not slots:
        return (
            "No free drop-off slots in the booking window. Save the details with "
            "create_lead and tell the customer the shop will confirm a time."
        )

    return json.dumps(
        {
            "timezone": ctx.shop.timezone,
            "slots": [
                {"start_at": s.start.isoformat(), "when": slot_label(s, tz)}
                for s in slots
            ],
            "instruction": (
                "Offer at most three of these in the customer's language. To "
                "book, pass the chosen start_at back exactly as written here."
            ),
        },
        ensure_ascii=False,
    )


async def _book_appointment(
    ctx: ToolContext,
    service: str,
    start_at: str,
    item: str,
    customer_name: str,
) -> str:
    from app.core.booking import (
        BookingError,
        confirm_slot,
        create_booking,
        slot_label,
    )

    try:
        parsed = datetime.fromisoformat(start_at.strip())
    except ValueError:
        return (
            f"'{start_at}' is not a timestamp I can read. Call "
            f"check_availability and pass back one of its start_at values exactly."
        )
    if parsed.tzinfo is None:
        return (
            "That timestamp has no timezone. Use a start_at from "
            "check_availability exactly as given."
        )

    tz = ZoneInfo(ctx.shop.timezone)
    try:
        slot = await confirm_slot(ctx.session, ctx.shop, parsed, now=ctx.now)
    except BookingError as exc:
        return (
            f"Could not book that time: {exc}. Call check_availability again and "
            f"offer the customer what is actually free. Do not apologise twice."
        )

    appointment = await create_booking(
        ctx.session,
        ctx.shop,
        customer_id=ctx.customer_id,
        conversation_id=ctx.conversation_id,
        slot=slot,
        service=service,
        item=item,
        customer_name=customer_name,
    )

    when = slot_label(slot, tz)
    ctx.booking_id = appointment.id
    ctx.booking_summary = (
        f"{customer_name or 'Customer'}"
        + (f", {item}" if item else "")
        + f", {service or 'appointment'}, {when}"
    )
    return (
        f"Booked: {when} ({ctx.shop.timezone}). Confirm this back to the "
        f"customer in their language, including the day and time. The owner "
        f"has been notified."
    )


async def _get_job_status(ctx: ToolContext, item: str) -> str:
    job = await latest_job_for_item(
        ctx.session, ctx.shop.client_id, ctx.customer_id, item or None
    )
    if job is None:
        return (
            "No job on file for this customer. Do not guess whether it is "
            "ready - tell them you will check, and hand off."
        )

    phrasing = {
        "in": "with us, being worked on",
        "waiting_parts": "waiting on parts",
        "ready": "ready for collection",
    }
    status = job.status.value
    return json.dumps(
        {
            "item": job.item,
            "status": status,
            "say": phrasing.get(status, status),
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        },
        ensure_ascii=False,
    )


async def _price_range(ctx: ToolContext, service: str) -> str:
    """A "from" figure for one service, or an honest refusal.

    `get_business_info("prices")` hands the model the whole table and lets it
    work out which row applies. For a regulated niche that is one inference too
    many: a clinic's veneer price depends on the consultation, and a model
    holding five rows is a model that might average two of them. This answers
    one service and refuses everything else.

    It never computes. No arithmetic, no combining, no scaling - the only
    numbers it can emit are ones the business typed in.
    """
    wanted = " ".join((service or "").split()).lower()
    ranges = ctx.shop.config.get("price_ranges") or []
    currency = ctx.shop.config.get("currency") or "$"

    if not ranges:
        return (
            "This business has not listed any prices. Say you will have someone "
            "confirm the price - do not estimate one."
        )

    match = None
    for item in ranges:
        name = " ".join(str(item.get("service", "")).split()).lower()
        if name and (name == wanted or wanted in name or name in wanted):
            match = item
            break

    if match is None:
        listed = ", ".join(str(i.get("service", "?")) for i in ranges)
        return (
            f"No price is listed for '{service}'. Say the business will confirm "
            f"that one, and do not estimate it. Prices exist for: {listed}."
        )

    low, high = match.get("low"), match.get("high")
    if low is None:
        return (
            f"'{match.get('service')}' has no figure set. Say the business will "
            f"confirm it, and do not estimate."
        )

    payload: dict[str, Any] = {
        "service": match.get("service"),
        "from": f"{currency}{low}",
        "instruction": (
            "Quote this as a 'from' figure only. Say the exact price depends on "
            "seeing them. Never add, scale or combine listed prices."
        ),
    }
    if high is not None and high != low:
        payload["listed_upper"] = f"{currency}{high}"
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


async def _send_deposit_link(ctx: ToolContext, reason: str) -> str:
    """Hand over a link the business configured. It never mints one.

    The URL lives in `business_info.deposit_url`, a JSONB blob that already
    takes arbitrary keys - so this needs no column and no migration. An unset
    link is a refusal rather than an improvisation: a payment URL invented by a
    language model is the worst thing on this list to get wrong.
    """
    url = (ctx.shop.config.get("business_info") or {}).get("deposit_url")
    if not url:
        return (
            "No deposit link is configured for this business. Do not invent a "
            "link or a payment method. Say someone will send it, and hand off."
        )
    return json.dumps(
        {
            "url": url,
            "reason": (reason or "").strip()[:200],
            "instruction": (
                "Send this link exactly as written. Do not shorten it, change "
                "it, or quote an amount that is not already in it."
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


async def _capture_travel_dates(
    ctx: ToolContext, arriving: str, leaving: str, treatment: str
) -> str:
    """When a patient flying in is actually in the country.

    Stored as a lead rather than on the customer: it is true of one trip, not
    of the person. `treatment` is the only clinical-adjacent field, and it is
    the one the patient already volunteered by asking about it - nothing here
    invites a medical history.
    """
    arriving = (arriving or "").strip()[:40]
    leaving = (leaving or "").strip()[:40]
    if not arriving and not leaving:
        return "No dates given. Ask when they arrive before calling this again."

    note = f"Travelling: arrives {arriving or '?'}, leaves {leaving or '?'}."
    await repo_create_lead(
        ctx.session,
        ctx.shop.client_id,
        customer_id=ctx.customer_id,
        conversation_id=ctx.conversation_id,
        item=(treatment or "").strip()[:200] or None,
        issue=note,
        contact=None,
    )
    return (
        "Travel dates saved. Offer times inside that window only, and do not "
        "promise that a treatment can be finished within it - the clinic "
        "decides that."
    )


async def _qualify_buyer(
    ctx: ToolContext,
    budget: str,
    area: str,
    timeline: str,
    funding: str,
    nie_status: str,
) -> str:
    """The five facts an agent needs before a viewing is worth arranging.

    Written to a lead, not to the customer record: a budget is true of one
    search, not of a person, and the next enquiry from the same number may be
    for something else entirely.

    `nie_status` is asked as a fact, never advised on. Whether they need one,
    how to get one and what it means for tax are all legal questions this
    niche's boundary forbids.
    """
    fields = {
        "budget": budget,
        "area": area,
        "timeline": timeline,
        "funding": funding,
        "nie": nie_status,
    }
    given = {k: " ".join(str(v).split())[:120] for k, v in fields.items() if v}
    if not given:
        return (
            "Nothing to save yet. Ask for the budget and the area first, one "
            "question at a time."
        )

    note = "; ".join(f"{key}: {value}" for key, value in sorted(given.items()))
    await repo_create_lead(
        ctx.session,
        ctx.shop.client_id,
        customer_id=ctx.customer_id,
        conversation_id=ctx.conversation_id,
        item=given.get("area") or None,
        issue=note,
        contact=None,
    )
    missing = sorted(set(fields) - set(given))
    return json.dumps(
        {
            "saved": sorted(given),
            "still_missing": missing,
            "instruction": (
                "An agent has this now. Ask for at most one missing item, then "
                "offer to arrange a viewing. Do not advise on NIE, mortgages, "
                "tax or residency."
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


async def _send_matching_listings(
    ctx: ToolContext, area: str, max_price: str, bedrooms: str
) -> str:
    """What the agency has, in the agency's own words.

    The search goes through `app.integrations.listings.SOURCE`, so wiring a
    real feed later does not touch this handler. Whatever comes back is passed
    through verbatim - no summarising, no translating the description, no
    filling in a detail the agency did not write.
    """
    found = await listings.SOURCE.search(
        ctx.shop.config,
        area=" ".join((area or "").split()),
        max_price=(max_price or "").strip(),
        bedrooms=(bedrooms or "").strip(),
    )
    if not found:
        return (
            "No listing matches, or none is configured. Say you will have an "
            "agent send what is available - do not describe a property, invent "
            "a reference, or promise something is on the books."
        )
    return json.dumps(
        {
            "listings": [item.as_dict() for item in found],
            "instruction": (
                "Send these in the agency's own words. Do not add a size, a "
                "build year, a licence status or any detail that is not here. "
                "Do not confirm availability - offer a viewing instead."
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


#: The case types a firm is asked to sort an enquiry into. A fixed list, so the
#: schema stays shop-independent and the cache prefix holds.
CASE_TYPES = (
    "arraigo",
    "nationality",
    "tie_renewal",
    "digital_nomad_visa",
    "family_reunification",
    "other",
)


async def _intake_case_type(ctx: ToolContext, case_type: str, summary: str) -> str:
    """Sort the enquiry, take the facts, book the consultation.

    This is the only shape the immigration niche is allowed: it records what
    the client says the matter is. It does not decide whether that is the right
    route, which is the legal question the boundary forbids.
    """
    kind = (case_type or "").strip().lower()
    if kind not in CASE_TYPES:
        kind = "other"

    await repo_create_lead(
        ctx.session,
        ctx.shop.client_id,
        customer_id=ctx.customer_id,
        conversation_id=ctx.conversation_id,
        item=kind,
        issue=" ".join((summary or "").split())[:2000] or None,
        contact=None,
    )
    return json.dumps(
        {
            "case_type": kind,
            "instruction": (
                "Recorded. Offer a consultation next. Do not say whether they "
                "qualify, which route is better, how long it takes, or what "
                "will happen - a lawyer decides all of that."
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


async def _document_checklist(ctx: ToolContext, case_type: str) -> str:
    """Only a list the firm wrote down.

    A plausible-looking document list is the most dangerous thing this niche
    could produce: a client who turns up at a cita missing one paper loses the
    appointment and sometimes the application. So there is no fallback, no
    general knowledge and no partial list - either the firm has published one
    for this case type or the answer is "I will have someone confirm".
    """
    kind = (case_type or "").strip().lower()
    published = (ctx.shop.config.get("business_info") or {}).get(
        "document_checklists"
    ) or {}
    items = published.get(kind) if isinstance(published, dict) else None

    if not items:
        return (
            f"The firm has not published a document list for '{case_type}'. Say "
            f"someone will confirm exactly what is needed. Do not list "
            f"documents from your own knowledge, and do not give a partial list."
        )
    return json.dumps(
        {
            "case_type": kind,
            "documents": list(items),
            "instruction": (
                "Send this list exactly as written. Do not add to it, do not "
                "explain what any document is for, and say the firm will "
                "confirm anything specific to their case."
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


async def _not_enabled(ctx: ToolContext, **kwargs: Any) -> str:
    return "This capability is not available yet. Hand off to a human instead."


# --------------------------------------------------------------------- registry

_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_business_info",
        description=(
            "Look up this shop's hours, address, contact, services, price ranges, "
            "payment methods or policies. Use this instead of guessing whenever "
            "the customer asks something factual about the business."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": [
                        "hours",
                        "address",
                        "contact",
                        "services",
                        "prices",
                        "payment",
                        "policies",
                        "all",
                    ],
                    "description": "Which piece of business information to fetch.",
                }
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
        handler=_get_business_info,
    ),
    ToolSpec(
        name="create_lead",
        description=(
            "Save what the customer needs, the details, and how to reach "
            "them. Call this when they want work done but no time has been "
            "agreed - it is how the business gets the job."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": (
                        "What the job is about, exactly as the customer "
                        "described it - a vehicle, a pet, a device, a garment, "
                        "an order number. Empty string if not known yet."
                    ),
                },
                "issue": {
                    "type": "string",
                    "description": "What the customer says is wrong, in their words.",
                },
                "contact": {
                    "type": "string",
                    "description": (
                        "Name, and a phone number or other contact if the customer "
                        "gave one. Empty string if not known."
                    ),
                },
            },
            "required": ["item", "issue", "contact"],
            "additionalProperties": False,
        },
        handler=_create_lead,
    ),
    ToolSpec(
        name="handoff_to_human",
        description=(
            "Pause the AI and alert the owner. Call this for accidents, "
            "injuries, lawyers, insurance, complaints, refunds, angry "
            "customers, unusually large or unusual jobs, anything you cannot "
            "answer from the business's own information, or when the customer "
            "asks for a person."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short reason, e.g. 'accident' or 'asked for a person'.",
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "One or two lines the owner can read to pick up the "
                        "conversation, in the owner's language."
                    ),
                },
            },
            "required": ["reason", "summary"],
            "additionalProperties": False,
        },
        handler=_handoff_to_human,
    ),
    ToolSpec(
        name="check_availability",
        description=(
            "List free drop-off slots. Call this before offering any time, and "
            "never invent or guess a time yourself. Returns exact `start_at` "
            "values to pass to book_appointment."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": (
                        "What the work is, as the customer described it."
                    ),
                },
                "preferred_day": {
                    "type": "string",
                    "description": (
                        "The day the customer asked for as YYYY-MM-DD, or an "
                        "empty string for the soonest openings."
                    ),
                },
            },
            "required": ["service", "preferred_day"],
            "additionalProperties": False,
        },
        handler=_check_availability,
    ),
    ToolSpec(
        name="book_appointment",
        description=(
            "Book a drop-off slot and notify the owner. Only ever call this with "
            "a `start_at` copied exactly from check_availability, and only after "
            "the customer has chosen that time."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "What the appointment is for.",
                },
                "start_at": {
                    "type": "string",
                    "description": (
                        "A start_at value from check_availability, copied "
                        "verbatim. Never construct this yourself."
                    ),
                },
                "item": {
                    "type": "string",
                    "description": (
                        "What the job is about, as the customer described it. "
                        "Empty string if it does not apply."
                    ),
                },
                "customer_name": {
                    "type": "string",
                    "description": "The customer's name, or an empty string.",
                },
            },
            "required": ["service", "start_at", "item", "customer_name"],
            "additionalProperties": False,
        },
        handler=_book_appointment,
    ),
    ToolSpec(
        name="get_job_status",
        description=(
            "Answer 'is it ready yet?' and 'how is my job going?'. Reports "
            "the status the owner last set, and nothing else. Never guess "
            "whether something is ready."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": (
                        "What the customer is asking about, as they "
                        "describe it, or an empty string to use their most "
                        "recent job."
                    ),
                }
            },
            "required": ["item"],
            "additionalProperties": False,
        },
        handler=_get_job_status,
    ),
    # ----------------------------------------------------------------- dental
    # Appended rather than slotted in beside the tools they resemble: the order
    # of this tuple is the order of the cache prefix, so inserting in the
    # middle would move every existing shop's prefix for no behavioural gain.
    ToolSpec(
        name="price_range",
        description=(
            "Look up the 'from' price for one named service. Use this instead "
            "of reading the price table yourself whenever the customer asks "
            "what something costs. It returns only figures the business has "
            "listed, and refuses when there is none - it never estimates."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": (
                        "The service the customer asked about, in their own "
                        "words - e.g. 'veneers', 'implant', 'check-up'."
                    ),
                }
            },
            "required": ["service"],
            "additionalProperties": False,
        },
        handler=_price_range,
    ),
    ToolSpec(
        name="send_deposit_link",
        description=(
            "Fetch the business's own deposit or payment link so you can send "
            "it. Call this when the customer agrees to pay a deposit. Never "
            "write a payment link yourself - if none is configured this "
            "refuses, and you must hand off."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "What the deposit is for, in a few words, for the "
                        "business's own record."
                    ),
                }
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        handler=_send_deposit_link,
    ),
    ToolSpec(
        name="capture_travel_dates",
        description=(
            "Save when a customer travelling from abroad will be in the "
            "country. Call this once they mention travel dates, before "
            "offering appointment times, so only times inside their stay are "
            "offered."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "arriving": {
                    "type": "string",
                    "description": "Arrival date as the customer gave it, or ''.",
                },
                "leaving": {
                    "type": "string",
                    "description": "Departure date as the customer gave it, or ''.",
                },
                "treatment": {
                    "type": "string",
                    "description": (
                        "The treatment or service they are coming for, as they "
                        "described it. Do not add clinical detail of your own."
                    ),
                },
            },
            "required": ["arriving", "leaving", "treatment"],
            "additionalProperties": False,
        },
        handler=_capture_travel_dates,
    ),
    # ------------------------------------------------------------ real estate
    ToolSpec(
        name="qualify_buyer",
        description=(
            "Save what a buyer is looking for: budget, area, timeline, whether "
            "they are buying with cash or a mortgage, and whether they already "
            "have an NIE. Call this as the facts emerge. Ask about NIE as a "
            "fact only - never advise on how to get one or what it means."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "budget": {"type": "string", "description": "Their budget, or ''."},
                "area": {"type": "string", "description": "Where they want to buy."},
                "timeline": {
                    "type": "string",
                    "description": "When they want to buy, in their words.",
                },
                "funding": {
                    "type": "string",
                    "description": "'cash', 'mortgage', or '' if not said.",
                },
                "nie_status": {
                    "type": "string",
                    "description": "Whether they have an NIE, as stated, or ''.",
                },
            },
            "required": [
                "budget",
                "area",
                "timeline",
                "funding",
                "nie_status",
            ],
            "additionalProperties": False,
        },
        handler=_qualify_buyer,
    ),
    ToolSpec(
        name="send_matching_listings",
        description=(
            "Find properties the agency has that match what the buyer wants. "
            "Returns the agency's own listing text. Never describe a property "
            "that did not come from this tool, and never confirm that one is "
            "still available."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "area": {"type": "string", "description": "Area or town, or ''."},
                "max_price": {
                    "type": "string",
                    "description": "Upper budget as given, e.g. '900000', or ''.",
                },
                "bedrooms": {
                    "type": "string",
                    "description": "Minimum bedrooms as a number, or ''.",
                },
            },
            "required": ["area", "max_price", "bedrooms"],
            "additionalProperties": False,
        },
        handler=_send_matching_listings,
    ),
    # ------------------------------------------------------------- immigration
    ToolSpec(
        name="intake_case_type",
        description=(
            "Record what kind of matter the client has, and the facts they "
            "gave. Call this once it is clear what they are asking about, "
            "before offering a consultation. Recording a case type is not "
            "advising that it is the right route."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "case_type": {
                    "type": "string",
                    "enum": list(CASE_TYPES),
                    "description": "The matter, as the client describes it.",
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "The facts in the client's own words. Do not include "
                        "passport, NIE or case file numbers."
                    ),
                },
            },
            "required": ["case_type", "summary"],
            "additionalProperties": False,
        },
        handler=_intake_case_type,
    ),
    ToolSpec(
        name="document_checklist",
        description=(
            "Fetch the firm's own published list of documents for a case type. "
            "Only ever send a list this returns. If it has none, say someone "
            "will confirm - never list documents from your own knowledge."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "case_type": {
                    "type": "string",
                    "enum": list(CASE_TYPES),
                    "description": "Which case type the list is for.",
                }
            },
            "required": ["case_type"],
            "additionalProperties": False,
        },
        handler=_document_checklist,
    ),
)

#: Fixed order, enabled only. Byte-stable across requests - do not sort or filter
#: this per shop without accepting a cold prompt cache.
ENABLED_TOOLS: tuple[ToolSpec, ...] = tuple(s for s in _SPECS if s.enabled)

BY_NAME: dict[str, ToolSpec] = {s.name: s for s in _SPECS}

#: The tool list for each niche, resolved once at import.
#:
#: This does not break the cache rule above. What the prefix needs is
#: *stability*, not uniformity: the list a given shop sees must be the same
#: bytes on every request. It is, because the niche is a property of the shop
#: and the order comes from `_SPECS` rather than from a set or a sort. Two
#: shops in different niches get two cache entries, which they would anyway -
#: the system prompt already differs per shop.
#:
#: Built by filtering `ENABLED_TOOLS` rather than by listing names in order, so
#: a niche cannot accidentally reorder the prefix.
NICHE_TOOLS: dict[str, tuple[ToolSpec, ...]] = {
    key: tuple(spec for spec in ENABLED_TOOLS if spec.name in niche.tools)
    for key, niche in NICHES.items()
}


def tools_for(niche: str = "") -> tuple[ToolSpec, ...]:
    """The specs for one niche. No argument, or an unknown one, is every
    enabled tool - which is what every caller got before niches existed."""
    if not niche:
        return ENABLED_TOOLS
    return NICHE_TOOLS.get(niche, ENABLED_TOOLS)


def tool_definitions(niche: str = "") -> list[dict[str, Any]]:
    return [spec.definition() for spec in tools_for(niche)]


def tool_names(niche: str = "") -> list[str]:
    return [spec.name for spec in tools_for(niche)]


async def dispatch(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
    """Run one tool call. Raises KeyError for an unknown or out-of-niche tool.

    The niche check is not belt-and-braces. The model sees a cached prefix, and
    a shop that changed niche - or a stale turn replayed from history - can ask
    for a tool this shop must not have. A dental clinic answering "is my case
    ready?" out of the trades mechanism is exactly the failure the boundary
    exists to prevent, so it is refused here as well as discouraged in the
    prompt.
    """
    spec = BY_NAME.get(name)
    if spec is None or not spec.enabled:
        raise KeyError(name)
    niche = (ctx.shop.config.get("vertical") or "") if ctx.shop else ""
    if niche and spec not in tools_for(niche):
        log.warning("refused out-of-niche tool %s for a %s shop", name, niche)
        raise KeyError(name)
    return await spec.handler(ctx, **arguments)
