"""The landing page and its signup form - the only routes with no login."""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import Lead, Signup
from app.web.marketing import MAX_SIGNUPS_PER_NUMBER

GOOD = {
    "business_name": "Westside Auto Repair",
    "contact_name": "Marta",
    "phone": "(312) 555-0144",
    "email": "marta@example.com",
    "city": "Chicago",
    "message": "Three bays, two of us, phone never stops.",
}


async def signups() -> list[Signup]:
    async with session_scope() as session:
        result = await session.execute(select(Signup).order_by(Signup.created_at))
        return list(result.scalars().all())


class TestLanding:
    async def test_the_page_loads_without_a_session(self, client, db):
        response = await client.get("/")
        assert response.status_code == 200
        assert "AI Front Desk" in response.text
        assert 'action="/signup"' in response.text

    async def test_it_is_the_only_indexable_page(self, client, db):
        landing = await client.get("/")
        assert "index, follow" in landing.text
        console = await client.get("/admin/login")
        assert "noindex" in console.text

    async def test_the_thank_you_page_loads(self, client, db):
        assert (await client.get("/thanks")).status_code == 200


class TestSignup:
    async def test_a_good_submission_is_stored_and_normalised(self, client, db):
        response = await client.post("/signup", data=GOOD)
        assert response.status_code == 303
        assert response.headers["location"] == "/thanks"

        rows = await signups()
        assert len(rows) == 1
        assert rows[0].business_name == "Westside Auto Repair"
        # Stored in E.164 so it matches `clients.owner_phone` later.
        assert rows[0].phone == "+13125550144"
        assert rows[0].status.value == "new"
        assert rows[0].client_id is None

    async def test_a_signup_is_not_a_customer_lead(self, client, db):
        """`leads` belongs to a shop; a signup has no shop yet."""
        await client.post("/signup", data=GOOD)
        async with session_scope() as session:
            leads = (await session.execute(select(Lead))).scalars().all()
        assert leads == []

    async def test_a_missing_name_is_rejected_with_the_form_refilled(
        self, client, db
    ):
        response = await client.post("/signup", data={**GOOD, "business_name": ""})
        assert response.status_code == 400
        assert "name of your shop" in response.text
        # The rest of what they typed is still there.
        assert "Marta" in response.text
        assert await signups() == []

    async def test_a_bad_number_is_rejected(self, client, db):
        response = await client.post("/signup", data={**GOOD, "phone": "12"})
        assert response.status_code == 400
        assert "phone number does not look right" in response.text
        assert await signups() == []

    async def test_the_honeypot_looks_like_success_and_stores_nothing(
        self, client, db
    ):
        """A bot that gets an error message learns how to avoid it."""
        response = await client.post("/signup", data={**GOOD, "website": "spam.biz"})
        assert response.status_code == 303
        assert response.headers["location"] == "/thanks"
        assert await signups() == []

    async def test_the_same_number_cannot_flood_us(self, client, db):
        for _ in range(MAX_SIGNUPS_PER_NUMBER + 3):
            await client.post("/signup", data=GOOD)
        assert len(await signups()) == MAX_SIGNUPS_PER_NUMBER

    async def test_a_duplicate_still_looks_like_success(self, client, db):
        for _ in range(MAX_SIGNUPS_PER_NUMBER):
            await client.post("/signup", data=GOOD)
        response = await client.post("/signup", data=GOOD)
        assert response.status_code == 303
        assert response.headers["location"] == "/thanks"

    async def test_long_input_is_truncated_not_rejected(self, client, db):
        await client.post(
            "/signup", data={**GOOD, "message": "x" * 5000, "city": "y" * 500}
        )
        rows = await signups()
        assert len(rows[0].message) == 2000
        assert len(rows[0].city) == 120

    async def test_html_in_a_submission_is_escaped_on_the_console(
        self, admin_client, client
    ):
        """Everything on these pages is text a stranger typed."""
        await client.post(
            "/signup",
            data={**GOOD, "business_name": "<script>alert(1)</script>"},
        )
        page = await admin_client.get("/admin/signups")
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text
