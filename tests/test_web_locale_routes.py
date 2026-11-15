"""The Spanish site: /es and its niche pages.

Spain is the first market, so these routes are the product rather than a
nicety. The failure modes they cover are the ones that are invisible in a
browser tab opened by the person who wrote the page:

* a page that serves Spanish headings over English body copy, because
  `_landing_context` read `content.*` instead of the locale's own module;
* a nav or footer link that drops a Spanish reader back into English halfway
  down the page;
* a locale advertised in hreflang before it is finished.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Signup
from app.web import locales
from app.web.marketing import _alternates

SPANISH_PAGES = ["/es", "/es/dental", "/es/inmobiliarias", "/es/extranjeria"]
ENGLISH_PAGES = ["/", "/dental", "/inmobiliarias", "/extranjeria"]

SPANISH_SIGNUP = {
    "business_name": "Clinica Dental Marbella",
    "phone": "600 111 222",
    "country": "ES",
    "niche": "dental",
    "consent": "yes",
    "locale": "es",
}


class TestThePagesExist:
    @pytest.mark.parametrize("path", [*SPANISH_PAGES, "/es/thanks"])
    async def test_it_loads(self, client, db, path):
        assert (await client.get(path)).status_code == 200, path

    @pytest.mark.parametrize("path", SPANISH_PAGES)
    async def test_it_declares_the_language(self, client, db, path):
        assert '<html lang="es">' in (await client.get(path)).text, path

    @pytest.mark.parametrize("path", ENGLISH_PAGES)
    async def test_the_english_pages_still_say_english(self, client, db, path):
        assert '<html lang="en">' in (await client.get(path)).text, path

    async def test_the_urls_are_not_translated(self, client, db):
        """/dental and /inmobiliarias are already the words a Spanish visitor
        expects, and translating a URL breaks every link anyone shared."""
        assert (await client.get("/es/dentista")).status_code == 404

    async def test_an_unpublished_locale_has_no_route_at_all(self, client, db):
        """Stronger than a template remembering to check a flag: a locale with
        a TODO left in it is simply never registered."""
        assert not locales.is_known("de")
        assert (await client.get("/de")).status_code == 404


class TestItIsActuallySpanish:
    async def test_the_body_copy_is_translated_and_not_just_the_headings(
        self, client, db
    ):
        """The bug this catches: `_landing_context` used to read `content.*`,
        which is English by definition, so a Spanish page would have rendered
        Spanish headings above English features and an English FAQ."""
        body = (await client.get("/es")).text
        assert locales.get("es").FEATURES[0]["body"][:40] in body
        assert locales.get("es").FAQ[0]["a"][:40] in body
        assert locales.en.FAQ[0]["a"][:40] not in body

    async def test_the_accents_survive_to_the_page(self, client, db):
        """The shell this copy was written through silently mangles non-ASCII,
        and unaccented Spanish reads as illiterate - so check the page, not
        just the file."""
        body = (await client.get("/es")).text
        assert "Cómo funciona" in body
        assert "mañana" in body

    async def test_the_form_is_in_spanish(self, client, db):
        body = (await client.get("/es")).text
        assert locales.get("es").COPY["signup"]["phone"] in body
        assert "WhatsApp number" not in body

    async def test_the_footer_is_translated_and_says_the_documents_are_not(
        self, client, db
    ):
        """A machine-translated processor agreement is worse than an English
        one somebody can take to their own lawyer - so the labels move, the
        documents do not, and the page says which rather than letting a
        reader find out by clicking."""
        body = (await client.get("/es")).text
        assert locales.get("es").COPY["foot"]["privacy"] in body
        assert locales.get("es").COPY["foot"]["legal_note"] in body

    def test_the_english_footer_carries_no_such_note(self):
        """Nothing to apologise for when the documents are already in the
        language of the page."""
        assert locales.en.COPY["foot"]["legal_note"] == ""


class TestNavigationStaysInTheLocale:
    @pytest.mark.parametrize("path", SPANISH_PAGES)
    async def test_every_in_page_link_keeps_the_prefix(self, client, db, path):
        """A Spanish reader clicking "Precios" should not land in English."""
        body = (await client.get(path)).text
        assert 'href="/es#pricing"' in body
        assert 'href="/es#signup"' in body
        assert 'href="/#pricing"' not in body

    async def test_the_thank_you_page_goes_back_to_the_right_home(
        self, client, db
    ):
        assert 'href="/es"' in (await client.get("/es/thanks")).text
        assert 'href="/"' in (await client.get("/thanks")).text

    async def test_the_english_pages_are_unprefixed(self, client, db):
        body = (await client.get("/")).text
        assert 'href="/#pricing"' in body
        assert 'href="/es#pricing"' not in body

    async def test_the_sector_cards_point_at_the_niche_pages(self, client, db):
        """The home page is a router into the three niches, in both
        languages."""
        body = (await client.get("/es")).text
        for sector in locales.get("es").SECTORS:
            assert f'href="{sector["href"]}"' in body


class TestTheSwitchAndTheAlternates:
    @pytest.mark.parametrize("path", ["/", "/es"])
    async def test_both_languages_are_offered_on_both_sides(
        self, client, db, path
    ):
        body = (await client.get(path)).text
        assert 'hreflang="en"' in body, path
        assert 'hreflang="es"' in body, path

    async def test_x_default_points_at_english(self, client, db):
        """What a crawler with no language preference should be handed."""
        assert 'hreflang="x-default" href="/"' in (await client.get("/")).text

    async def test_the_current_language_is_marked_for_assistive_technology(
        self, client, db
    ):
        assert 'aria-current="page"' in (await client.get("/es")).text

    async def test_the_switch_is_a_real_link_not_a_script(self, client, db):
        """It has to work with JavaScript off, and a crawler has to be able to
        follow it to the page the hreflang promised."""
        body = (await client.get("/es")).text
        switch = body[body.index('class="lang-switch"') :][:500]
        assert 'href="/"' in switch
        assert "onchange" not in switch

    def test_only_publishable_locales_are_advertised(self):
        """Pointing a search engine at a half-translated page is worse than
        not having the page."""
        offered = {a["code"] for a in _alternates("es")}
        assert offered == {
            code for code in locales.LOCALES if locales.is_publishable(code)
        }

    def test_a_locale_with_a_todo_is_dropped_from_the_switch(self, monkeypatch):
        monkeypatch.setattr(
            locales.get("es"), "DEMO_CAPTION", locales.TRANSLATION_TODO + " x"
        )
        assert {a["code"] for a in _alternates("en")} == {"en"}


class TestTheFormRemembersTheLanguage:
    """`locale` is a hidden field, so it is a stranger's bytes like everything
    else in the body - validated against the registry rather than trusted."""

    async def test_a_spanish_signup_lands_on_the_spanish_thank_you_page(
        self, client, db
    ):
        response = await client.post("/signup", data=SPANISH_SIGNUP)
        assert response.status_code == 303
        assert response.headers["location"] == "/es/thanks"

    async def test_the_locale_is_recorded_on_the_row(self, client, db):
        """Which language they were reading when they signed up, carried in
        `source` so it needed no column of its own."""
        await client.post("/signup", data=SPANISH_SIGNUP)
        async with session_scope() as session:
            row = (await session.execute(select(Signup))).scalars().one()
        assert row.source == "landing/es"

    async def test_an_english_signup_is_still_recorded_as_landing(
        self, client, db
    ):
        await client.post("/signup", data={**SPANISH_SIGNUP, "locale": ""})
        async with session_scope() as session:
            row = (await session.execute(select(Signup))).scalars().one()
        assert row.source == "landing"

    async def test_a_made_up_locale_falls_back_to_english(self, client, db):
        response = await client.post(
            "/signup",
            data={**SPANISH_SIGNUP, "locale": "../../etc/passwd"},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/thanks"

    async def test_a_rejected_spanish_submission_comes_back_in_spanish(
        self, client, db
    ):
        """Otherwise a mistyped phone number switches the whole page to
        English, which reads like the site broke rather than like a form
        error."""
        response = await client.post(
            "/signup", data={**SPANISH_SIGNUP, "phone": "12"}
        )
        assert response.status_code == 400
        assert '<html lang="es">' in response.text
        assert locales.get("es").COPY["signup"]["phone"] in response.text
