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

from app.core.router import ShopContext
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
)

#: Fixed order, enabled only. Byte-stable across requests - do not sort or filter
#: this per shop without accepting a cold prompt cache.
ENABLED_TOOLS: tuple[ToolSpec, ...] = tuple(s for s in _SPECS if s.enabled)

BY_NAME: dict[str, ToolSpec] = {s.name: s for s in _SPECS}


def tool_definitions() -> list[dict[str, Any]]:
    return [spec.definition() for spec in ENABLED_TOOLS]


def tool_names() -> list[str]:
    return [spec.name for spec in ENABLED_TOOLS]


async def dispatch(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
    """Run one tool call. Raises KeyError for an unknown tool."""
    spec = BY_NAME.get(name)
    if spec is None or not spec.enabled:
        raise KeyError(name)
    return await spec.handler(ctx, **arguments)
