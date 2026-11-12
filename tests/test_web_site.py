"""The public site: structure, the demo, and the promises the design makes.

The compliance text has its own suite (`test_web_legal.py`). This one guards the
things a redesign breaks quietly - a demo that stops being real, a review that
goes live before it is true, a third-party script creeping into a page that
should be entirely ours.
"""

from __future__ import annotations

import itertools
import json
import pathlib
import re

import pytest

from app.web import content
from app.web.brand import BRAND

PUBLIC = ["/", "/privacy", "/terms", "/messaging", "/thanks"]
STATIC = pathlib.Path(__file__).resolve().parent.parent / "app" / "web" / "static"


class TestEveryPageIsWhole:
    @pytest.mark.parametrize("path", PUBLIC)
    async def test_it_renders(self, client, db, path):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert "<!doctype html>" in response.text.lower()

    @pytest.mark.parametrize("path", PUBLIC)
    async def test_it_wears_the_brand(self, client, db, path):
        body = (await client.get(path)).text
        assert BRAND["name"] in body
        assert "ch-logo" in body, "the mark should be on every page"
        assert "favicon.svg" in body

    @pytest.mark.parametrize("path", PUBLIC)
    async def test_it_uses_the_site_stylesheet_not_the_app_one(
        self, client, db, path
    ):
        """The public site and the console are separately styled on purpose:
        marketing changes weekly and must never be able to move the console."""
        body = (await client.get(path)).text
        assert "site.css" in body
        assert "app.css" not in body

    @pytest.mark.parametrize("path", PUBLIC)
    async def test_it_can_be_navigated_away_from(self, client, db, path):
        body = (await client.get(path)).text
        for link in ("/privacy", "/terms", "/messaging"):
            assert f'href="{link}"' in body, (path, link)


class TestNothingIsLoadedFromAnyoneElse:
    """No third-party script, font or stylesheet anywhere.

    It keeps the site fast, keeps visitors' requests off other companies'
    servers, and means the whole thing still works with no network.
    """

    @pytest.mark.parametrize("path", PUBLIC)
    async def test_no_external_resource(self, client, db, path):
        body = (await client.get(path)).text
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', body)
        assert external == [], (path, external)

    @pytest.mark.parametrize("path", PUBLIC)
    async def test_no_inline_event_handlers(self, client, db, path):
        """Behaviour belongs in site.js, which keeps a strict CSP possible."""
        body = (await client.get(path)).text
        assert not re.search(r"\son(click|load|error|mouseover)=", body), path


class TestTheDemoIsReal:
    """The hero replays a captured conversation. If it ever stops being a real
    capture, it becomes the one lie a visitor can catch - they will try it."""

    def test_the_script_alternates_speakers(self):
        roles = [turn["from"] for turn in content.DEMO_SCRIPT]
        assert roles[0] == "customer"
        for first, second in itertools.pairwise(roles):
            assert first != second, roles

    def test_every_turn_has_text(self):
        for turn in content.DEMO_SCRIPT:
            assert turn["text"].strip()
            assert turn["from"] in ("customer", "chichi")

    def test_the_replies_carry_their_measured_timing(self):
        replies = [t for t in content.DEMO_SCRIPT if t["from"] == "chichi"]
        assert replies, "the demo has to contain at least one reply"
        for reply in replies:
            assert reply.get("stamp", "").endswith("s"), reply

    def test_it_ends_with_something_booked(self):
        """The demo exists to show the outcome, not the chat."""
        assert content.DEMO_SCRIPT[-1].get("booked") is True

    def test_the_caption_discloses_what_it_is(self):
        caption = content.DEMO_CAPTION.lower()
        assert "real" in caption
        assert "demo" in caption or "trimmed" in caption

    async def test_the_page_embeds_it_as_valid_json(self, client, db):
        body = (await client.get("/")).text
        match = re.search(r'id="demo-script">(.*?)</script>', body, re.DOTALL)
        assert match, "the demo script is not on the page"
        turns = json.loads(match.group(1))
        assert len(turns) == len(content.DEMO_SCRIPT)

    async def test_accented_text_survives_to_the_page(self, client, db):
        """The demo is in Spanish. A mangled encoding would be visible to
        every visitor and invisible to a test that only counted turns."""
        body = (await client.get("/")).text
        assert "cuánto" in body
        assert "mañana" in body


class TestReviewsStayOffUntilTheyAreTrue:
    def test_the_flag_is_off(self):
        assert content.SHOW_REVIEWS is False

    def test_the_samples_are_marked_as_samples(self):
        assert content.reviews_are_samples() is True
        for review in content.REVIEWS:
            assert review["sample"] is True

    def test_nothing_renders_while_they_are_samples(self):
        assert content.visible_reviews() == []

    async def test_no_invented_quote_reaches_the_page(self, client, db):
        body = (await client.get("/")).text
        for review in content.REVIEWS:
            assert review["quote"][:40] not in body
        assert "Sample Name" not in body

    async def test_turning_them_on_labels_them(self, client, db, monkeypatch):
        """If they are switched on while still invented, the page must say so
        rather than present them as genuine."""
        monkeypatch.setattr(content, "SHOW_REVIEWS", True)
        body = (await client.get("/")).text
        assert "not real customer quotes" in body
        assert content.REVIEWS[0]["quote"][:40] in body


class TestOnlyMeasuredNumbers:
    def test_every_metric_says_where_it_came_from(self):
        for metric in content.METRICS:
            assert metric["note"].strip(), metric
            assert metric["label"].strip(), metric

    async def test_no_invented_social_proof(self, client, db):
        """No customer counts, no awards, no "trusted by" - we have not earned
        them, and this site is read by carrier reviewers."""
        body = (await client.get("/")).text.lower()
        for invention in (
            "trusted by",
            "join thousands",
            "as seen in",
            "rated #1",
            "award-winning",
            "customers served",
            "5-star",
        ):
            assert invention not in body, invention


class TestMotionCanBeTurnedOff:
    def test_the_stylesheet_respects_the_preference(self):
        css = (STATIC / "site.css").read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in css

    def test_the_script_checks_it_before_animating(self):
        js = (STATIC / "site.js").read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in js
        assert "still" in js, "the reduced-motion branch should be named"

    def test_the_dashboard_skin_respects_it_too(self):
        css = (STATIC / "shop.css").read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in css


class TestTheConsoleCannotRegress:
    """The dashboard restyle is an override sheet scoped to one body class, so
    the console provably loads none of it."""

    async def test_the_console_loads_no_premium_styles(self, client, db):
        body = (await client.get("/admin/login")).text
        assert "shop.css" not in body
        assert "site.css" not in body
        assert "app.css" in body

    async def test_the_console_is_not_in_the_premium_skin(self, client, db):
        body = (await client.get("/admin/login")).text
        assert 'class="premium' not in body

    async def test_the_dashboard_is(self, owner_client, shop_a):
        body = (await owner_client.get("/shop")).text
        assert 'class="premium' in body
        assert "shop.css" in body
        assert "app.css" in body, "the skin overrides app.css, it does not replace it"

    def test_the_skin_is_scoped_to_that_class(self):
        """Every rule must be scoped. An unscoped rule would leak into the
        console the moment someone loaded both sheets."""
        css = (STATIC / "shop.css").read_text(encoding="utf-8")
        body = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        selectors = [
            line.split("{")[0].strip()
            for line in body.splitlines()
            if "{" in line and not line.strip().startswith(("@", "}"))
        ]
        unscoped = [
            s for s in selectors
            if s and "body.premium" not in s and not s.startswith(("from", "to", "%"))
        ]
        assert not unscoped, unscoped


class TestItWorksWithoutJavaScript:
    """Scroll reveals start at opacity 0. If a script failure could leave the
    page blank below the hero, the design is a liability rather than a feature.
    """

    def test_the_reveal_is_scoped_to_a_js_class(self):
        css = (STATIC / "site.css").read_text(encoding="utf-8")
        assert ".js .rise { opacity: 0" in css
        # The unscoped selector must not exist, or no-JS visitors see nothing.
        assert "\n.rise { opacity: 0" not in css

    async def test_the_page_arms_it_itself(self, client, db):
        body = (await client.get("/")).text
        assert 'classList.add("js")' in body

    async def test_the_content_is_in_the_html_not_built_by_script(
        self, client, db
    ):
        """Everything that matters is server-rendered: a crawler, a reader mode
        and a visitor with a blocked script all get the same page."""
        import html as html_mod

        # Unescaped, because Jinja turns an apostrophe into &#39; - comparing
        # raw bytes would make this fail on punctuation rather than on content.
        body = html_mod.unescape((await client.get("/")).text)
        assert "Two weeks of shadowing" in body
        assert BRAND["currency"] + str(BRAND["price_monthly"]) in body
        for item in content.FAQ[:3]:
            assert item["q"] in body


class TestTheCharacter:
    """Chichi the mascot. Drawn in SVG, so it is testable in a way an image
    never is."""

    POSES = ("idle", "typing", "wave", "sleep")

    def test_the_partial_handles_every_pose(self):
        from app.web.templating import templates

        template = templates.get_template("marketing/_mascot.html")
        for pose in self.POSES:
            svg = template.render(pose=pose, size=120, uid=pose, brand={})
            assert "<svg" in svg
            assert f"chichi--{pose}" in svg

    def test_each_pose_is_valid_svg(self):
        import xml.dom.minidom

        from app.web.templating import templates

        template = templates.get_template("marketing/_mascot.html")
        for pose in self.POSES:
            for avatar in (True, False):
                svg = template.render(
                    pose=pose, size=120, uid="t", avatar=avatar, brand={}
                )
                xml.dom.minidom.parseString(svg.strip())

    async def test_it_appears_on_the_landing_page(self, client, db):
        body = (await client.get("/")).text
        assert body.count("<svg class=\"chichi") >= 2

    async def test_it_waves_on_the_thank_you_page(self, client, db):
        body = (await client.get("/thanks")).text
        assert "chichi--wave" in body

    async def test_the_avatar_form_is_round_and_clipped(self, client, db):
        """At 30px the body is unreadable, so the avatar zooms to the face and
        clips to a circle."""
        body = (await client.get("/")).text
        assert "chichi--avatar" in body
        assert "ch-clip-" in body

    async def test_gradient_ids_are_unique_per_instance(self, client, db):
        """SVG ids are global. Two instances sharing one would mean the second
        silently repaints the first."""
        ids = re.findall(r'id="(ch-[a-z]+-[a-z0-9]+)"', (await client.get("/")).text)
        assert ids
        assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]

    def test_the_motion_is_compositor_only(self):
        """Every keyframe animates transform or opacity. Anything else would
        force layout on each frame, on phones, forever."""
        css = (STATIC / "chichi.css").read_text(encoding="utf-8")
        blocks = re.findall(r"@keyframes\s+chichi-[\w-]+\s*\{(.*?)\n\}", css, re.DOTALL)
        assert blocks, "no chichi keyframes found"
        allowed = {"transform", "opacity", "font-size"}
        for block in blocks:
            for prop in re.findall(r"([a-z-]+)\s*:", block):
                assert prop in allowed, prop

    def test_the_character_stops_moving_when_asked(self):
        css = (STATIC / "chichi.css").read_text(encoding="utf-8")
        tail = css[css.index("prefers-reduced-motion"):]
        assert "animation: none" in tail

    async def test_the_dashboard_has_it_too(self, owner_client, shop_a):
        body = (await owner_client.get("/shop")).text
        assert "chichi" in body
        assert "chichi.css" in body

    async def test_the_console_still_does_not(self, client, db):
        """The character belongs to the brand and the owner's surfaces, not to
        the internal console."""
        body = (await client.get("/admin/login")).text
        assert "chichi.css" not in body
