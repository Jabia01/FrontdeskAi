"""What a niche puts into - and keeps out of - the system prompt.

`tests/test_prompt.py` covers the prompt every business shares.
`tests/test_prompt_snapshots.py` catches any unreviewed drift in the rendered
output. This file covers the part that differs per niche, and the two
properties that matter most for a regulated one: that its boundary is actually
there, and that nobody else's boundary is.
"""

from __future__ import annotations

import pytest

from app.agent.niche import NICHES, REGULATED
from app.agent.prompt import build_system_prompt, prompt_version
from tests.prompt_fixtures import CHANNELS, snapshot_shop

LEGACY = ("auto", "salon", "pet", "trades", "repair", "other")


def render(vertical: str, channel: str = "whatsapp") -> str:
    return build_system_prompt(snapshot_shop(vertical), channel)


def squash(text: str) -> str:
    """Whitespace-normalised, so a content assertion does not also pin the
    line wrapping. The rules are written as prose and wrapped at render."""
    return " ".join(text.split())


class TestTheBoundaryIsRendered:
    @pytest.mark.parametrize("key", REGULATED)
    def test_a_regulated_niche_carries_its_boundary(self, key):
        text = render(key)
        assert "## What you must not do" in text
        # Not a paraphrase: the boundary is a compliance control, so the
        # rendered prompt must contain the text the niche actually declares.
        assert squash(NICHES[key].boundary) in squash(text)

    @pytest.mark.parametrize("key", LEGACY)
    def test_an_unregulated_vertical_has_no_boundary_section(self, key):
        assert "## What you must not do" not in render(key)

    @pytest.mark.parametrize("key", REGULATED)
    def test_a_boundary_never_leaks_into_another_niche(self, key):
        """The failure this guards against is a shared constant: a dental
        clinic inheriting the immigration boundary would read as nonsense, and
        an immigration firm inheriting dental's would be worse - it would look
        plausible."""
        mine = squash(NICHES[key].boundary)
        for other in NICHES:
            if other == key:
                continue
            assert mine not in squash(render(other)), (
                f"{key}'s boundary leaked into {other}"
            )

    @pytest.mark.parametrize("key", REGULATED)
    def test_the_niche_rules_are_rendered(self, key):
        text = squash(render(key))
        for rule in NICHES[key].rules:
            assert squash(rule) in text


class TestTheToolDependentRules:
    """A rule naming a tool the model was not handed is worse than no rule: it
    invites a call that `dispatch` refuses."""

    @pytest.mark.parametrize("key", ("dental", "real_estate"))
    def test_no_job_status_rule_without_the_tool(self, key):
        assert "get_job_status" not in render(key)

    @pytest.mark.parametrize("key", (*LEGACY, "immigration"))
    def test_the_job_status_rule_is_there_when_the_tool_is(self, key):
        assert "get_job_status" in render(key)

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_every_rendered_tool_name_is_one_the_niche_has(self, key):
        text = render(key)
        allowed = set(NICHES[key].tools)
        for name in ("book_appointment", "check_availability", "get_job_status"):
            if f"`{name}`" in text:
                assert name in allowed, f"{key} names {name} but was not given it"


class TestTheRulesAreNumberedContiguously:
    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_the_numbering_has_no_holes(self, key):
        text = render(key)
        block = text.split("## Hard rules\n\n", 1)[1].split("\n\n##", 1)[0]
        numbers = [
            int(line.split(".", 1)[0])
            for line in block.splitlines()
            if line and line[0].isdigit() and ". " in line[:5]
        ]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"{key} has a hole or a restart in its rule numbering: {numbers}"
        )

    def test_a_niche_without_job_status_still_counts_from_one(self):
        """Dropping rule 5 must close the gap, not leave 4 then 6."""
        block = render("dental").split("## Hard rules\n\n", 1)[1]
        assert block.startswith("1. ")
        assert "\n5. " in block


class TestDeterminism:
    """The contract in app/agent/prompt.py, re-asserted now that the prompt is
    assembled from blocks rather than one template."""

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_rendering_twice_is_byte_identical(self, key):
        for channel in CHANNELS:
            assert render(key, channel) == render(key, channel)

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_a_reordered_config_does_not_move_the_fingerprint(self, key):
        shop = snapshot_shop(key)
        shuffled = dict(reversed(list(shop["config"].items())))
        a = build_system_prompt(shop, "sms")
        b = build_system_prompt({**shop, "config": shuffled}, "sms")
        assert prompt_version(a) == prompt_version(b)

    def test_two_niches_do_not_render_the_same_prompt(self):
        seen = {key: render(key) for key in NICHES}
        assert len(set(seen.values())) == len(seen)


class TestCurrency:
    def test_price_ranges_render_in_the_shops_currency(self):
        shop = snapshot_shop("dental")
        shop["config"]["currency"] = "€"
        text = build_system_prompt(shop, "whatsapp")
        assert "€100" in text
        assert "$100" not in text

    def test_a_shop_without_a_currency_still_renders_dollars(self):
        shop = snapshot_shop("auto")
        shop["config"].pop("currency", None)
        assert "$100" in build_system_prompt(shop, "whatsapp")
