"""Defaults for a brand-new shop.

One definition, used by both the seed script and the console's onboarding form,
so a shop created in the console behaves exactly like a seeded one. Everything
here is config, not code: the whole point of the architecture is that a new shop
is rows, never a fork.
"""

from __future__ import annotations

from typing import Any

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

#: The agent picks the block matching the channel it is answering on.
CHANNEL_STYLE: dict[str, dict[str, Any]] = {
    "whatsapp": {
        "max_chars": 700,
        "rules": "Short paragraphs are fine. Lists allowed. Links allowed.",
    },
    "sms": {
        "max_chars": 320,
        "rules": (
            "Keep it under two sentences where possible - every 160 characters is "
            "a billable segment. No markdown. Avoid links."
        ),
    },
    "simulator": {
        "max_chars": 700,
        "rules": "Same as WhatsApp.",
    },
    "voice": {
        "max_chars": 240,
        "rules": (
            "Spoken sentences only. No lists, no URLs, no email addresses, no "
            "markdown. Spell out numbers that matter. One question at a time. "
            "The caller is on the phone right now: if you hand off, say you "
            "are putting them through to someone, never that somebody will "
            "reply or text them later."
        ),
    },
}

BOOKING: dict[str, Any] = {
    "slot_minutes": 30,
    "duration_minutes": 30,
    "lead_time_hours": 2,
    "max_days_ahead": 10,
    "max_concurrent": 1,
}

#: Universal triggers. A vertical preset adds its own on top.
HANDOFF_RULES: dict[str, Any] = {
    "keywords": ["accident", "injury", "lawyer", "refund", "complaint"],
    "max_misunderstandings": 2,
    "notes": "",
}

#: What a kind of business starts with. Config only - there is no per-vertical
#: code path anywhere, which is the whole point: one codebase, many businesses.
#:
#: `job_noun` is the word for what a job is about, and it is load-bearing: it is
#: rendered into the system prompt and into what the owner is texted, so the
#: same code reads naturally to a garage and to a dog groomer.
#:
#: Regulated trades - medical, dental, legal, financial - are deliberately
#: absent. They need advice boundaries and record-keeping rules reviewed before
#: anyone onboards one, not a preset that makes it look routine.
VERTICALS: dict[str, dict[str, Any]] = {
    "auto": {
        "label": "Auto repair, tyres, bodywork",
        "job_noun": "vehicle",
        "services": ["Brakes", "Oil change", "Tyres", "Diagnostics"],
        "handoff_keywords": ["tow", "accident", "insurance", "lawyer"],
        "example": "READY 2014 Civic",
    },
    "salon": {
        "label": "Salon, barber, nails, beauty",
        "job_noun": "appointment",
        "services": ["Cut", "Colour", "Blow dry"],
        "handoff_keywords": ["complaint", "refund", "allergic", "reaction"],
        "example": "READY Maria",
    },
    "pet": {
        "label": "Pet grooming, boarding, daycare",
        "job_noun": "pet",
        "services": ["Full groom", "Bath and tidy", "Nail trim"],
        "handoff_keywords": ["bite", "aggressive", "injury", "sick", "vet"],
        "example": "READY Bella",
    },
    "trades": {
        "label": "Plumbing, electrical, HVAC, handyman",
        "job_noun": "job",
        "services": ["Call-out", "Repair", "Installation"],
        "handoff_keywords": ["leak", "flood", "gas", "emergency", "no power"],
        "example": "READY job 1482",
    },
    "repair": {
        "label": "Phone, computer and appliance repair",
        "job_noun": "device",
        "services": ["Screen replacement", "Battery replacement", "Diagnostics"],
        "handoff_keywords": ["data loss", "warranty", "insurance", "refund"],
        "example": "READY iPhone 13",
    },
    "other": {
        "label": "Something else",
        "job_noun": "job",
        "services": [],
        "handoff_keywords": [],
        "example": "READY job 1482",
    },
}

HOURS: dict[str, str] = {
    "monday": "8:00-18:00",
    "tuesday": "8:00-18:00",
    "wednesday": "8:00-18:00",
    "thursday": "8:00-18:00",
    "friday": "8:00-18:00",
    "saturday": "9:00-14:00",
    "sunday": "closed",
}

#: Offered in the console's timezone picker. Not a limit - any zoneinfo name
#: works - just the ones a US pilot actually needs.
TIMEZONES = (
    "America/Chicago",
    "America/New_York",
    "America/Denver",
    "America/Phoenix",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
    "UTC",
)


def new_shop_config(
    *,
    owner_language: str = "en",
    default_customer_language: str = "en",
    enabled_languages: list[str] | None = None,
    vertical: str = "other",
) -> dict[str, Any]:
    """The config row a business starts with.

    Deliberately empty where it matters: no price ranges, so the agent refuses
    to quote any number until someone has filled them in. The vertical preset
    fills in a starting point for services and handoff keywords, and the word
    this business uses for a job.
    """
    preset = VERTICALS.get(vertical) or VERTICALS["other"]
    keywords = list(HANDOFF_RULES["keywords"])
    for keyword in preset["handoff_keywords"]:
        if keyword not in keywords:
            keywords.append(keyword)
    return {
        "vertical": vertical if vertical in VERTICALS else "other",
        "job_noun": preset["job_noun"],
        "business_info": {},
        "services": list(preset["services"]),
        "hours": dict(HOURS),
        "price_ranges": [],
        "tone": "Friendly, brief and practical. Never pushy.",
        "handoff_rules": {
            "keywords": keywords,
            "max_misunderstandings": HANDOFF_RULES["max_misunderstandings"],
            "notes": "",
        },
        "faqs": [],
        "channel_style": {k: dict(v) for k, v in CHANNEL_STYLE.items()},
        "owner_language": owner_language,
        "default_customer_language": default_customer_language,
        "enabled_languages": enabled_languages or ["en"],
        "country_language_overrides": {},
        "calendar_id": None,
        "booking": dict(BOOKING),
        "missed_call_channel": "sms",
        "owner_channel": "auto",
        # v1.5
        "reminder_hours_before": 24,
        "summary_enabled": True,
        "summary_hour": 7,
        "templates": {},
    }
