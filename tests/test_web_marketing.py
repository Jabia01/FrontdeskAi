"""The landing page and its signup form - the only routes with no login."""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import Lead, Signup
from app.web import signup_form
from app.web.marketing import MAX_SIGNUPS_PER_NUMBER

#: A Spanish clinic, which is what the form is for now. `country` is load
#: bearing: it is what makes a locally typed number parse.
GOOD = {
    "business_name": "Clinica Dental Marbella",
    "niche": "dental",
    "contact_name": "Marta",
    "country": "ES",
    "phone": "600 111 222",
    "email": "marta@example.com",
    "city": "Marbella",
    "languages": ["es", "en", "de", "nl"],
    "crm_tool": "doctoralia",
    "site_url": "clinicadentalmarbella.es",
    "message": "Four chairs, most patients from the Netherlands and Germany.",
    "consent": "yes",
}

#: The old market, kept as a case rather than deleted: US tenants exist, they
#: still sign up, and a number typed the American way has to keep resolving to
#: the same E.164 string their `clients.owner_phone` already holds.
US = {
    **GOOD,
    "business_name": "Westside Auto Repair",
    "niche": "other",
    "trade": "brakes and tyres",
    "country": "US",
    "phone": "(312) 555-0144",
    "city": "Chicago",
    "languages": ["en", "es"],
    "crm_tool": "",
    "site_url": "",
}


async def signups() -> list[Signup]:
    async with session_scope() as session:
        result = await session.execute(select(Signup).order_by(Signup.created_at))
        return list(result.scalars().all())


class TestLanding:
    async def test_the_page_loads_without_a_session(self, client, db):
        response = await client.get("/")
        assert response.status_code == 200
        assert "Chichi" in response.text
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
        assert rows[0].business_name == "Clinica Dental Marbella"
        # "600 111 222" is only a valid number if it is read as Spanish, and
        # it is read as Spanish because the form asked which country they are
        # in. Stored in E.164 so it matches `clients.owner_phone` later.
        assert rows[0].phone == "+34600111222"
        assert rows[0].status.value == "new"
        assert rows[0].client_id is None

    async def test_the_answers_the_console_needs_are_stored_as_fields(
        self, client, db
    ):
        """Not prose in `message`. These four decide the shop's prompt, its
        timezone and the languages it answers in, and a human re-reading them
        out of a paragraph is how a clinic goes live on the wrong template."""
        await client.post("/signup", data=GOOD)
        row = (await signups())[0]
        assert row.niche == "dental"
        assert row.country == "ES"
        assert row.languages == ["es", "en", "de", "nl"]
        assert row.crm_tool == "doctoralia"

    async def test_consent_is_recorded_not_just_required(self, client, db):
        """GDPR Art. 7(1) puts the burden of showing someone agreed on us. A
        checkbox that is validated and then discarded shows nothing."""
        await client.post("/signup", data=GOOD)
        row = (await signups())[0]
        assert row.consent_at is not None

    async def test_a_us_number_typed_the_us_way_still_works(self, client, db):
        await client.post("/signup", data=US)
        assert (await signups())[0].phone == "+13125550144"

    async def test_a_missing_consent_box_is_rejected(self, client, db):
        response = await client.post("/signup", data={**GOOD, "consent": ""})
        assert response.status_code == 400
        assert await signups() == []

    async def test_an_unknown_niche_is_dropped_not_stored(self, client, db):
        """Everything in the body is a stranger's bytes. An unrecognised niche
        is not an error - it is simply not an answer."""
        await client.post("/signup", data={**GOOD, "niche": "wizardry"})
        assert (await signups())[0].niche is None

    async def test_an_unoffered_language_cannot_be_smuggled_in(self, client, db):
        await client.post(
            "/signup", data={**GOOD, "languages": ["es", "klingon", "ja"]}
        )
        # `ja` is a real language the agent knows, and is still dropped: the
        # list is the form's allowlist, not `LANGUAGE_NAMES`.
        assert (await signups())[0].languages == ["es"]

    async def test_a_javascript_url_is_not_stored(self, client, db):
        """It renders as a link on a page an admin is logged into."""
        await client.post(
            "/signup", data={**GOOD, "site_url": "javascript:alert(1).x"}
        )
        assert (await signups())[0].website_url is None

    async def test_a_business_outside_the_listed_countries_can_still_sign_up(
        self, client, db
    ):
        """The country list is eight long. A required list of eight is a closed
        door to the ninth, so it is optional - and a number typed in full needs
        no region to be read."""
        response = await client.post(
            "/signup",
            data={**GOOD, "country": "", "phone": "+49 30 1234567"},
        )
        assert response.status_code == 303
        row = (await signups())[0]
        assert row.country is None
        assert row.phone == "+49301234567"

    async def test_a_bare_host_gets_a_scheme(self, client, db):
        await client.post("/signup", data=GOOD)
        assert (await signups())[0].website_url == (
            "https://clinicadentalmarbella.es"
        )

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
        assert "name of your business" in response.text
        # The rest of what they typed is still there.
        assert "Marta" in response.text
        assert await signups() == []

    async def test_a_bad_number_is_rejected(self, client, db):
        response = await client.post("/signup", data={**GOOD, "phone": "12"})
        assert response.status_code == 400
        assert "number does not look right" in response.text
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


class TestTheFormAsRendered:
    """What a visitor is actually shown. These are the assertions that fail if
    a field is added to the route and forgotten in the template, which is the
    failure mode a route test cannot see: a missing input just submits empty.
    """

    async def test_every_field_the_route_reads_is_on_the_page(self, client, db):
        raw = (await client.get("/")).text
        for name in (
            "business_name",
            "niche",
            "trade",
            "country",
            "city",
            "contact_name",
            "phone",
            "email",
            "languages",
            "crm_tool",
            "site_url",
            "message",
            "consent",
            "locale",
        ):
            assert f'name="{name}"' in raw, name

    async def test_the_niche_options_are_the_ones_the_agent_has_templates_for(
        self, client, db
    ):
        raw = (await client.get("/")).text
        for key, label in signup_form.NICHE_CHOICES:
            assert f'value="{key}"' in raw, key
            assert label in raw, label

    async def test_the_language_checkboxes_are_the_market(self, client, db):
        raw = (await client.get("/")).text
        for code, label in signup_form.LANGUAGE_CHOICES:
            assert f'name="languages" value="{code}"' in raw, code
            assert label in raw, label

    async def test_the_consent_box_is_required_and_not_pre_ticked(
        self, client, db
    ):
        """A pre-ticked box is not consent under GDPR Art. 4(11), and a box
        that is not required is not a lawful basis."""
        raw = (await client.get("/")).text
        box = raw[raw.index('name="consent"') : raw.index('name="consent"') + 200]
        assert "required" in box
        assert "checked" not in box

    async def test_the_honeypot_is_still_hidden_and_still_named_website(
        self, client, db
    ):
        """The real website field is `site_url`. The attractive name stays on
        the trap, because a bot filling fields by name picks that one."""
        raw = (await client.get("/")).text
        assert 'name="website"' in raw
        assert "left:-9999px" in raw

    async def test_a_rejected_submission_keeps_what_they_ticked(self, client, db):
        """Thirteen checkboxes and two dropdowns is a lot to re-enter because
        a phone number was wrong."""
        response = await client.post("/signup", data={**GOOD, "phone": "12"})
        assert response.status_code == 400
        markup = " ".join(response.text.split())
        assert '<option value="dental" selected>' in markup
        assert '<option value="ES" selected>' in markup
        assert 'value="nl" checked' in markup
        assert 'name="consent" value="yes" required checked' in markup

    async def test_the_us_trades_marquee_is_gone(self, client, db):
        """The page sells three niches now. `trade` survives as a free-text
        field for the ones that pick "Something else"."""
        raw = (await client.get("/")).text
        assert "Westside" not in raw
        assert "dog grooming" not in raw
