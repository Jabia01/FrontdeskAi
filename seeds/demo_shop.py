"""Seed two pilot shops. Idempotent - safe to re-run.

  Westside Auto Repair  (Chicago, English + Spanish, no country override)
  Cicero Tire & Brake   (Spanish-first on +1, per the doc's Cicero example)

The second shop exists so the language tests can exercise a per-shop
country_language_overrides map, and so the tenant-isolation test has a real
neighbour to try to read across.

Run:  python -m seeds.demo_shop
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core import defaults
from app.db import dispose_engine, session_scope
from app.models import (
    Channel,
    ChannelEndpoint,
    Client,
    ClientConfig,
    ClientStatus,
)

WESTSIDE = {
    "name": "Westside Auto Repair",
    "timezone": "America/Chicago",
    "languages": ["en", "es"],
    "owner_phone": "+13125550111",
    "plan": "pilot",
    "endpoints": [
        (Channel.sms, "+13125550100", "+13125550100"),
        (Channel.whatsapp, "whatsapp:+13125550100", "+13125550100"),
        (Channel.simulator, "sim-westside", "+13125550100"),
        # The number the shop forwards unanswered calls to. v1 logs the
        # missed call and texts back; v2 points the voice adapter here.
        (Channel.voice, "+13125550100", "+13125550100"),
    ],
    "config": {
        "business_info": {
            "address": "2431 W Chicago Ave, Chicago, IL 60622",
            "phone": "+1 312 555 0100",
            "parking": "Free lot behind the shop, enter from the alley.",
            "payment": "Cash, debit, all major cards. No personal checks.",
            "towing": "We do not tow. We can recommend a local tow company.",
        },
        "services": [
            "Brake pads and rotors",
            "Oil change and filters",
            "Check-engine diagnostics",
            "Suspension and shocks",
            "Alternator and starter replacement",
            "Tire mounting, rotation and balancing",
            "Pre-purchase inspection",
            "Illinois emissions test prep",
        ],
        "hours": {
            "monday": "8:00-18:00",
            "tuesday": "8:00-18:00",
            "wednesday": "8:00-18:00",
            "thursday": "8:00-18:00",
            "friday": "8:00-18:00",
            "saturday": "9:00-14:00",
            "sunday": "closed",
        },
        "price_ranges": [
            {"service": "Front brake pads and rotors", "low": 320, "high": 480},
            {"service": "Oil change (synthetic)", "low": 70, "high": 110},
            {"service": "Check-engine diagnostic", "low": 95, "high": 95},
            {"service": "Alternator replacement", "low": 450, "high": 780},
            {"service": "Tire mounting and balancing (per tire)", "low": 25, "high": 40},
            {"service": "Pre-purchase inspection", "low": 120, "high": 160},
        ],
        "tone": (
            "Warm, plain-spoken and brief, the way a trusted neighbourhood mechanic "
            "talks. No jargon unless the customer uses it first. Never pushy."
        ),
        "handoff_rules": {
            "keywords": [
                "accident",
                "crash",
                "tow",
                "towed",
                "lawyer",
                "attorney",
                "refund",
                "insurance claim",
                "lawsuit",
                "accidente",
                "abogado",
                "reembolso",
                "grua",
                "grúa",
            ],
            "max_misunderstandings": 2,
            "notes": "Also hand off anything over $1500 or any angry customer.",
        },
        "faqs": [
            {
                "q": "Do you need an appointment?",
                "a": (
                    "Walk-ins are welcome for oil changes and tires. Diagnostics and "
                    "brake jobs are better with a drop-off appointment."
                ),
            },
            {
                "q": "How long does a diagnostic take?",
                "a": "Usually the same day if the car is dropped off before 10am.",
            },
            {
                "q": "Do you offer loaner cars?",
                "a": "No loaners, but we are a 5 minute walk from the Blue Line.",
            },
            {
                "q": "Do you honour warranties?",
                "a": "12 months or 12,000 miles on parts and labour we supply.",
            },
        ],
        "calendar_id": "westside-shop@group.calendar.google.com",
        "booking": {
            "slot_minutes": 30,
            "duration_minutes": 30,
            "lead_time_hours": 2,
            "max_days_ahead": 10,
            "max_concurrent": 1,
        },
        "missed_call_channel": "sms",
        "owner_channel": "auto",
        # Both pilot shops are garages, so they say "vehicle". The platform
        # itself is trade-neutral - see app/core/defaults.py VERTICALS.
        "vertical": "auto",
        "job_noun": "vehicle",
        "reminder_hours_before": 24,
        "summary_enabled": True,
        "summary_hour": 7,
        "owner_language": "en",
        "default_customer_language": "en",
        "enabled_languages": ["en", "es"],
        "country_language_overrides": {},
    },
}

CICERO = {
    "name": "Cicero Tire & Brake",
    "timezone": "America/Chicago",
    "languages": ["es", "en"],
    "owner_phone": "+17085550222",
    "plan": "pilot",
    "endpoints": [
        (Channel.sms, "+17085550200", "+17085550200"),
        (Channel.whatsapp, "whatsapp:+17085550200", "+17085550200"),
        (Channel.simulator, "sim-cicero", "+17085550200"),
        (Channel.voice, "+17085550200", "+17085550200"),
    ],
    "config": {
        "business_info": {
            "address": "5812 W Cermak Rd, Cicero, IL 60804",
            "phone": "+1 708 555 0200",
            "payment": "Efectivo y tarjetas. Cash and cards.",
        },
        "services": [
            "Llantas nuevas y usadas / new and used tires",
            "Frenos / brakes",
            "Alineacion / alignment",
            "Cambio de aceite / oil change",
        ],
        "hours": {
            "monday": "8:00-19:00",
            "tuesday": "8:00-19:00",
            "wednesday": "8:00-19:00",
            "thursday": "8:00-19:00",
            "friday": "8:00-19:00",
            "saturday": "8:00-17:00",
            "sunday": "9:00-13:00",
        },
        "price_ranges": [
            {"service": "Llanta usada montada", "low": 45, "high": 90},
            {"service": "Frenos delanteros", "low": 280, "high": 420},
            {"service": "Alineacion 4 ruedas", "low": 80, "high": 120},
        ],
        "tone": (
            "Directo y amable. Habla como un taller de barrio. Spanish is the "
            "house language; switch to English the moment the customer does."
        ),
        "handoff_rules": {
            "keywords": [
                "accidente",
                "abogado",
                "grua",
                "grúa",
                "reembolso",
                "accident",
                "lawyer",
                "tow",
                "refund",
            ],
            "max_misunderstandings": 2,
        },
        "faqs": [
            {
                "q": "Aceptan sin cita?",
                "a": "Si, llantas y aceite sin cita. Frenos mejor con cita.",
            }
        ],
        "calendar_id": "cicero-shop@group.calendar.google.com",
        "booking": {
            "slot_minutes": 30,
            "duration_minutes": 45,
            "lead_time_hours": 1,
            "max_days_ahead": 7,
            "max_concurrent": 2,
        },
        "missed_call_channel": "sms",
        "owner_channel": "auto",
        # Both pilot shops are garages, so they say "vehicle". The platform
        # itself is trade-neutral - see app/core/defaults.py VERTICALS.
        "vertical": "auto",
        "job_noun": "vehicle",
        "reminder_hours_before": 24,
        "summary_enabled": True,
        "summary_hour": 7,
        "owner_language": "es",
        "default_customer_language": "es",
        "enabled_languages": ["es", "en"],
        # The doc's example: a Cicero shop that wants Spanish first on every +1.
        "country_language_overrides": {"+1": ["es", "en"]},
    },
}

# Style rules live in app/core/defaults.py so that a seeded shop and a shop
# created in the console are byte-identical.
CHANNEL_STYLE = defaults.CHANNEL_STYLE


async def upsert_shop(spec: dict) -> None:
    async with session_scope() as session:
        existing = await session.execute(
            select(Client).where(Client.name == spec["name"])
        )
        client = existing.scalar_one_or_none()

        if client is None:
            client = Client(
                name=spec["name"],
                timezone=spec["timezone"],
                languages=spec["languages"],
                owner_phone=spec["owner_phone"],
                plan=spec["plan"],
                status=ClientStatus.live,
            )
            session.add(client)
            await session.flush()
            print(f"  created client {client.name} ({client.id})")
        else:
            client.status = ClientStatus.live
            client.languages = spec["languages"]
            client.owner_phone = spec["owner_phone"]
            print(f"  updating client {client.name} ({client.id})")

        cfg_row = await session.execute(
            select(ClientConfig).where(ClientConfig.client_id == client.id)
        )
        config = cfg_row.scalar_one_or_none()
        fields = dict(spec["config"])
        fields["channel_style"] = CHANNEL_STYLE

        if config is None:
            config = ClientConfig(client_id=client.id, **fields)
            session.add(config)
        else:
            for key, value in fields.items():
                setattr(config, key, value)
        # Recomputed on first use by the prompt builder.
        config.prompt_version = None

        for channel, external_id, number in spec["endpoints"]:
            row = await session.execute(
                select(ChannelEndpoint).where(
                    ChannelEndpoint.channel == channel,
                    ChannelEndpoint.external_id == external_id,
                )
            )
            endpoint = row.scalar_one_or_none()
            if endpoint is None:
                session.add(
                    ChannelEndpoint(
                        client_id=client.id,
                        channel=channel,
                        provider="twilio" if channel != Channel.simulator else "local",
                        external_id=external_id,
                        number=number,
                        active=True,
                    )
                )
                print(f"    + endpoint {channel.value}:{external_id}")
            else:
                endpoint.client_id = client.id
                endpoint.number = number
                endpoint.active = True


async def main() -> None:
    print("Seeding shops...")
    for spec in (WESTSIDE, CICERO):
        await upsert_shop(spec)
    await dispose_engine()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
