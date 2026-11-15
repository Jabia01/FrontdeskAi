"""Run a real conversation against the agent and print it as demo data.

    python scripts/capture_demo.py --niche dental --out captured.json

`app/web/locales/en.py` opens with a rule: the demo on the landing page is a
conversation that actually happened against this system, and it may only be
replaced with another real capture. The reason is in the file - a fabricated
demo is the one claim on that page a visitor can disprove in thirty seconds,
because they will try it themselves.

This is how you get another real capture. It builds a shop on the local
database, sends the customer messages through the same path a WhatsApp message
takes - ingest, tenant router, engine, agent, delivery - and records what came
back, with the time each reply actually took.

It calls the real model, so it costs real money. A capture is a handful of
messages; the cost is cents.

Nothing here writes to the marketing copy. It prints JSON; a human reads the
transcript, decides it is worth showing, and pastes it in.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

sys.path.insert(0, ".")

# The console on Windows is cp1252 and cannot print Arabic, Chinese or Greek -
# which is most of what this script exists to capture. Force UTF-8 on both
# streams so a successful capture is not lost to an encoding error at the last
# line, after the model has already been paid for.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover - a non-tty stream
        pass

from app.channels.base import load_adapters
from app.core import defaults
from app.core.engine import handle_inbound_message
from app.core.ingest import ingest
from app.core.router import invalidate
from app.db import dispose_engine, session_scope
from app.models import Channel, ClientStatus, Direction
from app.repo import add_endpoint, outbound_for_customer_since, set_client_status
from app.repo_admin import create_client_with_config
from app.schemas import InboundMessage

#: The shops the captures run against. Realistic, in the sense that every
#: figure here is one a real clinic or agency would have typed in - the agent
#: may only quote what it is given, so a thin config produces a thin demo.
SHOPS: dict[str, dict] = {
    "dental": {
        "name": "Clinica Dental Marbella",
        "timezone": "Europe/Madrid",
        "languages": ["es", "en", "de", "nl", "fr", "sv"],
        "config": {
            "business_info": {
                "address": "Avenida Ricardo Soriano 42, Marbella",
                "phone": "+34952000000",
                "payment": "Card, cash or bank transfer",
            },
            "services": [
                "Consultation",
                "Check-up and hygiene",
                "Veneers",
                "Implants",
                "Orthodontics",
            ],
            "price_ranges": [
                {"service": "Consultation", "low": 60, "high": 60},
                {"service": "Veneers", "low": 450, "high": 700},
                {"service": "Implants", "low": 1200, "high": 1800},
                {"service": "Check-up and hygiene", "low": 80, "high": 120},
            ],
            "faqs": [
                {
                    "q": "Do you treat patients visiting from abroad?",
                    "a": "Yes. Most of our patients come from northern Europe.",
                }
            ],
        },
    },
    "real_estate": {
        "name": "Costa Property Partners",
        "timezone": "Europe/Madrid",
        "languages": ["es", "en", "sv", "de", "nl"],
        "config": {
            "business_info": {
                "address": "Puerto Banus, Marbella",
                "phone": "+34952111111",
                "listings": [
                    {
                        "reference": "MRB-412",
                        "summary": "Three-bedroom villa with a pool, ten minutes "
                        "from Puerto Banus. South-facing garden.",
                        "price": "875000",
                        "area": "Marbella",
                        "bedrooms": "3",
                    },
                    {
                        "reference": "EST-118",
                        "summary": "Two-bedroom apartment with sea views, walking "
                        "distance to the old town.",
                        "price": "640000",
                        "area": "Estepona",
                        "bedrooms": "2",
                    },
                ],
            },
            "services": ["Viewings", "Buyer registration", "Valuation"],
            "price_ranges": [],
        },
    },
    "immigration": {
        "name": "Bufete Extranjeria Madrid",
        "timezone": "Europe/Madrid",
        "languages": ["es", "ar", "fr", "en", "ru"],
        "config": {
            "business_info": {
                "address": "Calle Gran Via 28, Madrid",
                "phone": "+34910000000",
                "document_checklists": {
                    "arraigo": [
                        "Passport, valid and complete",
                        "Certificado de empadronamiento",
                        "Certificado de antecedentes penales",
                        "Proof of three years' residence in Spain",
                    ]
                },
            },
            "services": [
                "Initial consultation",
                "Arraigo",
                "Nationality",
                "TIE renewal",
            ],
            "price_ranges": [
                {"service": "Initial consultation", "low": 80, "high": 80},
            ],
        },
    },
}


async def build_shop(niche: str) -> tuple[uuid.UUID, str]:
    load_adapters()
    spec = SHOPS[niche]
    config = defaults.new_shop_config(
        vertical=niche,
        owner_language="es",
        default_customer_language="es",
        enabled_languages=spec["languages"],
    )
    config.update(spec["config"])
    sim_id = f"capture-{niche}-{uuid.uuid4().hex[:6]}"

    async with session_scope() as session:
        client = await create_client_with_config(
            session,
            name=spec["name"],
            timezone=spec["timezone"],
            owner_phone="+34600000000",
            languages=spec["languages"],
            config=config,
        )
        await add_endpoint(
            session,
            client.id,
            channel=Channel.simulator,
            external_id=sim_id,
            number=None,
            provider="local",
        )
        client_id = client.id

    async with session_scope() as session:
        # Live, because a shadow-mode shop drafts for the owner instead of
        # answering - and a draft is not what a visitor would see.
        await set_client_status(session, client_id, ClientStatus.live)
    invalidate(client_id)
    return client_id, sim_id


async def say(client_id: uuid.UUID, sim_id: str, phone: str, text: str) -> dict:
    """One customer message in, one agent reply out, timed."""
    started = time.monotonic()
    async with session_scope() as session:
        result = await ingest(
            InboundMessage(
                channel=Channel.simulator,
                provider="local",
                external_id=sim_id,
                from_number=phone,
                to_number=sim_id,
                body=text,
                provider_msg_id=f"cap-{uuid.uuid4().hex[:12]}",
            )
        )
        message_id, customer_id = result.message_id, result.customer_id

    await handle_inbound_message(client_id, message_id)
    elapsed = time.monotonic() - started

    async with session_scope() as session:
        rows = await outbound_for_customer_since(session, client_id, customer_id, None)
    replies = [r for r in rows if r.direction == Direction.outbound]
    return {
        "customer": text,
        "reply": replies[-1].body if replies else "",
        "seconds": round(elapsed, 1),
    }


async def run(niche: str, lines: list[str], phone: str) -> int:
    client_id, sim_id = await build_shop(niche)
    turns = []
    for line in lines:
        turn = await say(client_id, sim_id, phone, line)
        turns.append(turn)
        print(f"  customer > {turn['customer']}", file=sys.stderr)
        print(f"  chichi   > {turn['reply']}  [{turn['seconds']}s]", file=sys.stderr)
        if not turn["reply"]:
            print("  (no reply - stopping)", file=sys.stderr)
            break
    print(json.dumps({"niche": niche, "turns": turns}, ensure_ascii=False, indent=2))
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a real demo conversation.")
    parser.add_argument("--niche", required=True, choices=sorted(SHOPS))
    parser.add_argument("--phone", default="+31600000000")
    parser.add_argument("--say", action="append", required=True)
    args = parser.parse_args()
    try:
        return await run(args.niche, args.say, args.phone)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
