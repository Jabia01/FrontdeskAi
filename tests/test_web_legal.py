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

PUBLIC_PAGES = [
    "/",
    "/privacy",
    "/gdpr",
    "/dpa",
    "/terms",
    "/messaging",
    "/thanks",
]


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
        """The form is the opt-in record a reviewer asks to see.

        Two different disclosures live on this page and they are not
        interchangeable. The *form* asks a prospective business whether we may
        contact them, which under GDPR Art. 6(1)(a) is consent and nothing
        else - so its line is about that, and the moment the box is ticked is
        stored on the row. The *footer* carries the US carrier disclosure,
        which is about the SMS programme the shops themselves run and is what
        10DLC vetting reads. Replacing the second with the first would have
        quietly broken an A2P registration that is still live.
        """
        raw = (await client.get("/")).text
        body = prose(raw)
        assert "may contact me on WhatsApp or by email" in body
        assert "No payment details, no contract" in body
        assert 'href="/privacy"' in raw
        # And the carrier disclosure is still on the page, in the footer.
        assert "Message and data rates may apply" in body
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

    async def test_the_prices_come_from_one_place(self, client, db):
        """Every plan on the page is one defined in BRAND, and the page shows
        all of them - a plan added to config and forgotten in the template is
        the way a pricing page starts lying."""
        body = (await client.get("/")).text
        assert BRAND["plans"], "no plans configured"
        for plan in BRAND["plans"]:
            assert plan["price"] in body, plan["key"]
            assert plan["name"] in body, plan["key"]

    async def test_the_setup_fee_is_stated(self, client, db):
        assert BRAND["setup_fee"] in (await client.get("/")).text


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
        for key in ("name", "tagline", "description", "city", "setup_fee"):
            assert BRAND[key] != TODO
            assert BRAND[key]

    async def test_a_placeholder_is_visible_rather_than_hidden(self, client, db):
        """If the legal entity is unset, the page must look unfinished rather
        than quietly render an empty string that nobody notices."""
        if BRAND["legal_name"] != TODO:
            pytest.skip("legal name has been filled in")
        body = prose((await client.get("/privacy")).text)
        assert TODO in body


class TestTheDataProtectionPages:
    """`/gdpr` and `/dpa`: the two pages a Spanish clinic's DPO and an
    extranjeria firm's partner read before they sign anything.

    These tests are mostly about honesty rather than about completeness. The
    failure mode they exist to prevent is a page that reads finished - EU
    hosting, a retention period, a transfer mechanism - while the code and the
    deployment say otherwise. A supervisory authority can hold us to a
    statement on a public page, so each of these says what is true today.
    """

    async def test_both_pages_say_they_are_not_lawyer_reviewed(
        self, client, db
    ):
        for path in ("/gdpr", "/dpa"):
            body = prose((await client.get(path)).text)
            assert "reviewed by a lawyer" in body, path

    async def test_the_controller_processor_split_is_stated(self, client, db):
        """Everything else follows from it, and it is the first question a DPO
        asks."""
        body = prose((await client.get("/gdpr")).text)
        assert "You are the controller" in body
        assert "We are your processor" in body

    async def test_the_retention_statement_is_the_true_one(self, client, db):
        """Nothing in the app deletes a conversation on a schedule - the only
        sweep that removes anything is `purge_expired_sessions`. Until that
        changes, the page says indefinitely.
        """
        body = prose((await client.get("/gdpr")).text)
        assert "Today, indefinitely" in body
        assert "no automatic deletion" in body

    async def test_the_hosting_location_comes_from_the_deployment(
        self, client, db
    ):
        """Not from prose somebody typed. `test_deploy_readiness.py` pins the
        same constant to `render.yaml`, so the two cannot drift."""
        from app.web import infra

        for path in ("/gdpr", "/dpa"):
            body = prose((await client.get(path)).text)
            assert infra.hosting_location() in body, path

    async def test_a_us_deployment_says_so_plainly(self, client, db):
        """The sentence a buyer needs to see, not buried. If this ever fails
        because the service moved to Frankfurt, the fix is to delete the test,
        not to soften the page."""
        from app.web import infra

        if infra.hosted_in_eu():
            pytest.skip("hosted in the EEA; the warning is correctly absent")
        body = prose((await client.get("/gdpr")).text)
        assert "outside the European Economic Area" in body
        assert "we are not there yet" in body

    async def test_every_subprocessor_is_listed_on_both_pages(
        self, client, db
    ):
        """Art. 28(2): the customer has to be told who else touches it. A
        provider added to `infra.SUBPROCESSORS` appears here without anyone
        editing a template."""
        from app.web import infra

        for path in ("/gdpr", "/dpa"):
            body = prose((await client.get(path)).text)
            for row in infra.SUBPROCESSORS:
                assert row["name"] in body, (path, row["name"])

    async def test_the_model_provider_is_named(self, client, db):
        """The one a clinic will ask about by name."""
        body = prose((await client.get("/gdpr")).text)
        assert "Anthropic" in body
        assert "does not train on it" in body

    async def test_the_clinical_boundary_is_described_in_the_same_words_it_is_built_in(
        self, client, db
    ):
        """The page claims the assistant cannot ask for clinical detail or a
        photograph. That claim is only safe because the dental niche's
        boundary block actually says so - so assert against the spec, not
        against the prose."""
        from app.agent.niche import NICHES

        boundary = NICHES["dental"].boundary.lower()
        assert "photo" in boundary
        body = prose((await client.get("/gdpr")).text).lower()
        assert "never ask for a photograph" in body
        assert "symptoms, medication, medical" in body

    async def test_the_ai_act_disclosure_is_claimed_only_because_it_happens(
        self, client, db
    ):
        from app.agent.niche import NICHES

        assert NICHES["dental"].discloses
        body = prose((await client.get("/gdpr")).text)
        assert "Art. 50" in body
        assert "first message it sends them" in body

    async def test_the_two_pages_link_to_each_other(self, client, db):
        """A DPO reading one will want the other, and a DPA that cites a
        separate security description has to point at it."""
        assert 'href="/dpa"' in (await client.get("/gdpr")).text
        assert 'href="/gdpr"' in (await client.get("/dpa")).text

    async def test_the_footer_links_to_both(self, client, db):
        raw = (await client.get("/")).text
        assert 'href="/gdpr"' in raw
        assert 'href="/dpa"' in raw

    async def test_the_unfilled_residency_facts_are_visible_not_hidden(
        self, client, db
    ):
        """Same rule as `brand.placeholders()`: a transfer mechanism nobody
        has confirmed renders as a placeholder on the page rather than as a
        blank cell that reads like "none needed"."""
        from app.web import infra

        assert infra.placeholders(), (
            "if this is empty, the TODOs were filled in - delete this test"
        )
        body = (await client.get("/gdpr")).text
        assert TODO in body

    async def test_the_move_the_page_promises_is_actually_written_down(
        self, client, db
    ):
        """`/gdpr` tells a prospective clinic the EU move is "planned and
        documented". That is a representation, so the document has to exist -
        and it has to be a runbook rather than a sentence of intent."""
        from pathlib import Path

        body = prose((await client.get("/gdpr")).text)
        assert "planned and documented" in body

        deploy = (
            Path(__file__).resolve().parents[1] / "docs" / "deploy.md"
        ).read_text(encoding="utf-8")
        assert "Moving the whole thing to Frankfurt" in deploy
        # The three steps that are easy to leave out and expensive to discover
        # afterwards: Render cannot change region in place, Twilio verifies
        # signatures against PUBLIC_BASE_URL, and every webhook needs
        # repointing or that channel goes quiet with no error anywhere.
        assert "not editable in place" in deploy
        assert "PUBLIC_BASE_URL" in deploy
        assert "Repoint the webhooks" in deploy

    async def test_the_dpa_covers_the_eight_article_28_duties(self, client, db):
        """Art. 28(3)(a) to (h). Not a substitute for legal review - it is the
        checklist that stops a section being lost in an edit."""
        body = prose((await client.get("/dpa")).text)
        for phrase in (
            "Only what you instruct",            # (a) documented instructions
            "bound to confidentiality",          # (b) confidentiality
            "Art. 32",                           # (c) security
            "before we add or replace one",      # (d) sub-processors
            "yours to answer",                   # (e) data subject rights
            "72 hours",                          # (f) breach assistance
            "delete everything or to export",    # (g) deletion at the end
            "answer an auditor's questions",     # (h) audits
        ):
            assert phrase in body, phrase
