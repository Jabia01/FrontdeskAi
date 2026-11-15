"""The three niche pages.

The home page routes into these rather than trying to address a clinic, an
estate agency and an immigration firm at once - a page written for all three
says less to each.

What these tests mostly pin is the second column: what the assistant will
*not* do. For a regulated business that is the part that decides the sale, and
it is the part most likely to get quietly softened in a copy edit.
"""

from __future__ import annotations

import pytest

from app.web import content
from app.web.locales import en

PAGES = sorted(en.NICHE_PAGES.items())
IDS = [slug for slug, _ in PAGES]


@pytest.mark.parametrize(("slug", "page"), PAGES, ids=IDS)
class TestEachNichePage:
    async def test_it_renders(self, client, db, slug, page):
        response = await client.get(page["path"])
        assert response.status_code == 200
        assert "<!doctype html>" in response.text.lower()

    async def test_it_leads_with_its_own_headline(self, client, db, slug, page):
        body = (await client.get(page["path"])).text
        assert page["eyebrow"] in body
        for word in page["headline"].split()[:6]:
            assert word.strip(".,") in body

    async def test_it_says_what_it_will_not_do(self, client, db, slug, page):
        body = (await client.get(page["path"])).text
        assert page["refuses"], f"{slug} lists no limits"
        assert len(page["refuses"]) >= 4
        for item in page["refuses"]:
            assert item[:40] in body

    async def test_it_is_illustrated_with_its_own_conversations(
        self, client, db, slug, page
    ):
        """A clinic shown someone else's enquiry is a worse demo than none."""
        codes = {c["code"] for c in content.DEMO_CONVERSATIONS}
        assert set(page["demo"]) <= codes
        body = (await client.get(page["path"])).text
        shops = {
            c["shop"]
            for c in content.DEMO_CONVERSATIONS
            if c["code"] in page["demo"]
        }
        assert any(shop in body for shop in shops)

    async def test_it_links_back_to_signup_and_pricing(self, client, db, slug, page):
        body = (await client.get(page["path"])).text
        assert "/#signup" in body
        assert "/#pricing" in body

    async def test_it_loads_nothing_from_anyone_else(self, client, db, slug, page):
        """Same rule as every other public page - no third-party anything."""
        body = (await client.get(page["path"])).text
        assert "http://" not in body
        assert 'src="https://' not in body


class TestTheyAreReachable:
    async def test_the_home_page_links_to_every_one(self, client, db):
        body = (await client.get("/")).text
        for page in en.NICHE_PAGES.values():
            assert f'href="{page["path"]}"' in body

    async def test_every_niche_has_a_page(self):
        """A niche the agent supports but the site never mentions is a niche
        nobody can buy."""
        from app.agent.niche import REGULATED

        covered = {page["niche"] for page in en.NICHE_PAGES.values()}
        assert covered == set(REGULATED)

    async def test_the_limits_match_what_the_agent_actually_refuses(self):
        """The page promises a boundary; the niche spec has to carry one."""
        from app.agent.niche import niche_for

        for page in en.NICHE_PAGES.values():
            spec = niche_for(page["niche"])
            assert spec.regulated is True
            assert spec.boundary, f"{page['niche']} advertises limits it lacks"
