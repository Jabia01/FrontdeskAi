"""The public site and the legal pages carriers actually read.

The compliance assertions here are not box-ticking. Since 30 June 2026 Twilio
requires a privacy policy URL and a terms URL to register an A2P 10DLC
campaign, and error 30932 rejects a campaign whose privacy policy lacks the
"not shared with third parties" language. A campaign rejection costs days, and
US SMS is blocked until it clears - so each required sentence has a test.
"""

from __future__ import annotations

import pytest

from app.web.brand import BRAND, TODO, placeholders

PUBLIC_PAGES = ["/", "/privacy", "/terms", "/messaging", "/thanks"]


def prose(body: str) -> str:
    """The text as a reader sees it, not as the file is wrapped.

    HTML collapses newlines inside a paragraph into a space, so a required
    sentence broken across two source lines still reads as one sentence to a
    browser and to a carrier reviewer. Asserting on the raw bytes would make
    these tests fail every time someone reflowed a paragraph.
    """
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


class TestThePagesExist:
    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    async def test_it_loads_without_a_session(self, client, db, path):
        response = await client.get(path)
        assert response.status_code == 200, path

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    async def test_it_carries_the_brand(self, client, db, path):
        body = (await client.get(path)).text
        assert BRAND["name"] in body, path
        assert "AI Front Desk" not in body, path

    @pytest.mark.parametrize("path", ["/", "/privacy", "/terms", "/messaging"])
    async def test_it_is_indexable(self, client, db, path):
        """A legal page a crawler cannot see is a legal page a carrier
        reviewer may not trust."""
        assert "index, follow" in (await client.get(path)).text, path

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    async def test_every_page_links_to_the_legal_pages(self, client, db, path):
        body = (await client.get(path)).text
        if path == "/thanks":
            return  # the one page with a deliberately bare layout
        for link in ("/privacy", "/terms", "/messaging"):
            assert f'href="{link}"' in body, (path, link)


class TestTenDlcCompliance:
    """Each assertion maps to a documented registration requirement."""

    async def test_the_privacy_policy_refuses_third_party_sharing(
        self, client, db
    ):
        """Twilio error 30932: a campaign is rejected when the privacy policy
        does not state that mobile opt-in data is not shared."""
        body = prose((await client.get("/privacy")).text)
        assert "No mobile information will be shared with third parties" in body
        assert "marketing or promotional purposes" in body
        assert "will not be shared with any third parties" in body

    async def test_the_privacy_policy_says_we_do_not_sell_data(self, client, db):
        body = prose((await client.get("/privacy")).text)
        assert "do not sell personal information" in body
        assert "lead generators" in body

    async def test_the_messaging_page_states_the_five_required_things(
        self, client, db
    ):
        body = prose((await client.get("/messaging")).text)
        # 1. who is sending
        assert BRAND["name"] in body
        # 2. how they opted in
        assert "You texted the business first" in body
        # 3. frequency
        assert "Message frequency varies" in body
        # 4. cost
        assert "Message and data rates may apply" in body
        # 5. how to stop and how to get help
        assert "STOP" in body
        assert "HELP" in body

    async def test_the_signup_form_carries_a_consent_line(self, client, db):
        """The form is the opt-in record a reviewer asks to see."""
        raw = (await client.get("/")).text
        body = prose(raw)
        assert "Message and data rates may apply" in body
        assert "Reply STOP to unsubscribe" in body
        assert 'href="/messaging"' in raw

    async def test_every_page_footer_carries_the_messaging_disclosure(
        self, client, db
    ):
        for path in ("/", "/privacy", "/terms", "/messaging"):
            body = prose((await client.get(path)).text)
            assert "Message and data rates may apply" in body, path

    async def test_contact_details_are_consistent_across_the_legal_pages(
        self, client, db
    ):
        """Carriers check these against the brand registration, so the two
        pages must not drift apart."""
        privacy = prose((await client.get("/privacy")).text)
        terms = prose((await client.get("/terms")).text)
        for value in (BRAND["legal_name"], BRAND["address"], BRAND["support_email"]):
            assert value in privacy
            assert value in terms


class TestTheLandingPageIsHonest:
    async def test_it_makes_no_claim_we_cannot_support(self, client, db):
        """No invented testimonials, customer counts or endorsements. A vetted
        business should not have fiction on its website."""
        body = prose((await client.get("/")).text).lower()
        for invention in (
            "trusted by",
            "customers love",
            "5 stars",
            "testimonial",
            "as seen in",
            "join thousands",
            "rated #1",
        ):
            assert invention not in body, invention

    async def test_it_describes_features_that_exist(self, client, db):
        body = prose((await client.get("/")).text)
        for real in (
            # Still named on the page even now that calls are answered, because
            # it is a real feature and because a carrier reviewing this page
            # for 10DLC is looking for exactly this kind of messaging use.
            "missed call",
            "reminder",
            "language",
            "shadow",
            # Voice. The page may not promise a ringing phone gets answered
            # unless it does, and it may not stay quiet about it now it does.
            "phone calls",
            "puts the caller through",
        ):
            assert real.lower() in body.lower(), real

    async def test_the_price_comes_from_one_place(self, client, db):
        body = (await client.get("/")).text
        assert f"{BRAND['currency']}{BRAND['price_monthly']}" in body


class TestBrandPlaceholders:
    def test_unfilled_values_are_reported(self):
        """They appear on the public legal pages, so there has to be something
        that tells us they are still blank."""
        unfilled = placeholders()
        assert isinstance(unfilled, list)
        for key in unfilled:
            assert BRAND[key] == TODO

    def test_the_identity_values_are_set(self):
        """Name, tagline and price are ours to choose, so they must never be
        placeholders - unlike the legal entity details, which are the user's."""
        for key in ("name", "tagline", "description", "city", "price_monthly"):
            assert BRAND[key] != TODO
            assert BRAND[key]

    async def test_a_placeholder_is_visible_rather_than_hidden(self, client, db):
        """If the legal entity is unset, the page must look unfinished rather
        than quietly render an empty string that nobody notices."""
        if BRAND["legal_name"] != TODO:
            pytest.skip("legal name has been filled in")
        body = prose((await client.get("/privacy")).text)
        assert TODO in body
