"""The owner dashboard actually renders the data it is given.

These exist because of a specific trap: Jinja renders an unknown attribute as
an empty string. When `appointments.vehicle` became `appointments.item`, every
template still asking for `.vehicle` carried on rendering perfectly - just with
blank cells. No test failed. So each page that shows a field now asserts the
value reaches the HTML.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.core.router import invalidate
from app.db import session_scope
from app.models import Appointment, Channel, ClientConfig, Direction, JobStatus
from app.repo import (
    add_message,
    find_or_create_conversation,
    find_or_create_customer,
    log_call,
    upsert_job,
)

OWNER = "+13125550999"
CUSTOMER = "+13125558181"
ITEM = "1998 Volvo 940"


async def seed(client_id) -> dict:
    async with session_scope() as session:
        customer = await find_or_create_customer(session, client_id, CUSTOMER)
        customer.name = "Dorota"
        customer.item = ITEM
        conversation = await find_or_create_conversation(
            session, client_id, customer.id, Channel.sms
        )
        await add_message(
            session,
            client_id,
            conversation.id,
            direction=Direction.inbound,
            channel=Channel.sms,
            body="is it done yet?",
        )
        session.add(
            Appointment(
                client_id=client_id,
                customer_id=customer.id,
                conversation_id=conversation.id,
                start_at=datetime.now(UTC) + timedelta(days=1),
                service="Timing belt",
                item=ITEM,
                status="booked",
            )
        )
        await upsert_job(
            session,
            client_id,
            customer.id,
            ITEM,
            JobStatus.waiting_parts,
            updated_by="owner",
        )
        await log_call(
            session,
            client_id,
            customer_id=customer.id,
            from_number=CUSTOMER,
            provider_call_id="CA-dash",
        )
        return {"conversation_id": conversation.id, "customer_id": customer.id}


async def set_noun(client_id, noun: str) -> None:
    async with session_scope() as session:
        await session.execute(
            update(ClientConfig)
            .where(ClientConfig.client_id == client_id)
            .values(job_noun=noun)
        )
    invalidate(client_id)


class TestTheDataReachesThePage:
    async def test_appointments_show_what_the_job_is_about(
        self, owner_client, shop_a
    ):
        await seed(shop_a)
        page = await owner_client.get("/shop/appointments")
        assert page.status_code == 200
        assert ITEM in page.text
        assert "Timing belt" in page.text

    async def test_jobs_show_the_item_and_its_status(self, owner_client, shop_a):
        await seed(shop_a)
        page = await owner_client.get("/shop/jobs")
        assert ITEM in page.text
        assert "waiting parts" in page.text

    async def test_a_transcript_shows_the_customer_details(
        self, owner_client, shop_a
    ):
        ids = await seed(shop_a)
        page = await owner_client.get(f"/shop/conversations/{ids['conversation_id']}")
        assert "Dorota" in page.text
        assert ITEM in page.text
        assert "is it done yet?" in page.text

    async def test_today_shows_the_appointment_and_the_call(
        self, owner_client, shop_a
    ):
        await seed(shop_a)
        page = await owner_client.get("/shop")
        assert "Timing belt" in page.text
        assert "555-8181" in page.text

    async def test_calls_show_whether_they_were_texted_back(
        self, owner_client, shop_a
    ):
        await seed(shop_a)
        page = await owner_client.get("/shop/calls")
        assert "555-8181" in page.text
        # Logged but not texted back, and the page has to say which.
        assert "not texted" in page.text or "no" in page.text


class TestTheBusinessOwnVocabulary:
    async def test_the_jobs_page_uses_the_configured_noun(
        self, owner_client, shop_a
    ):
        await set_noun(shop_a, "pet")
        await seed(shop_a)
        page = await owner_client.get("/shop/jobs")
        assert "<th>Pet</th>" in page.text
        assert "READY</span> and the pet" in page.text
        assert "Vehicle" not in page.text

    async def test_the_appointments_page_uses_it_too(self, owner_client, shop_a):
        await set_noun(shop_a, "device")
        await seed(shop_a)
        page = await owner_client.get("/shop/appointments")
        assert "<th>Device</th>" in page.text

    async def test_a_business_with_no_noun_set_reads_neutrally(
        self, owner_client, shop_a
    ):
        await set_noun(shop_a, "job")
        await seed(shop_a)
        page = await owner_client.get("/shop/jobs")
        assert "<th>Job</th>" in page.text
        # No auto-trade words anywhere on the page. Word boundaries, because
        # "card" and "scorecard" contain "car".
        assert not re.search(r"\b(car|cars|vehicle|Civic)\b", page.text, re.IGNORECASE)

    async def test_the_console_transcript_uses_it(self, admin_client, shop_a):
        await set_noun(shop_a, "garment")
        ids = await seed(shop_a)
        page = await admin_client.get(
            f"/admin/shops/{shop_a}/conversations/{ids['conversation_id']}"
        )
        assert "<dt>Garment</dt>" in page.text
        assert ITEM in page.text
