"""Which tools each niche is handed, and the cache rule that constrains it.

`app/agent/tools.py` opens with a warning: tools render *before* `system` in
the cache prefix, so a reordered or conditionally-included tool destroys the
prompt cache. Per-niche sets are compatible with that, but only under two
conditions, and both are asserted here:

* the list for a given niche is the same bytes every time, and
* its order comes from `_SPECS`, never from a set, a sort or a name list.

The third condition - no per-shop data in any schema - is asserted too, because
that is the one a new tool is most likely to break.
"""

from __future__ import annotations

import json

import pytest

from app.agent import tools as tools_mod
from app.agent.niche import NICHES
from app.agent.tools import (
    ENABLED_TOOLS,
    NICHE_TOOLS,
    tool_definitions,
    tool_names,
    tools_for,
)

GLOBAL_ORDER = [spec.name for spec in ENABLED_TOOLS]
WITH_JOB_STATUS = ("auto", "salon", "pet", "trades", "repair", "other", "immigration")
WITHOUT_JOB_STATUS = ("dental", "real_estate")


class TestTheCachePrefixStaysStable:
    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_the_list_is_identical_on_every_call(self, key):
        assert tool_definitions(key) == tool_definitions(key)
        assert json.dumps(tool_definitions(key)) == json.dumps(tool_definitions(key))

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_the_order_comes_from_the_global_registry(self, key):
        """A subset, in the registry's own order - not re-sorted, not the order
        the niche happened to list its names in."""
        names = tool_names(key)
        assert names == [n for n in GLOBAL_ORDER if n in set(names)]

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_no_schema_carries_per_shop_data(self, key):
        """The thing a new tool is most likely to get wrong. A shop name, a
        phone number or a currency in a description would give every shop its
        own prefix and silently end prompt caching."""
        blob = json.dumps(tool_definitions(key)).lower()
        for leak in ("snapshot shop", "westside", "+1", "€", "chicago", "madrid"):
            assert leak not in blob, f"{leak!r} leaked into a {key} tool schema"

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_every_tool_is_strict_and_closed(self, key):
        for definition in tool_definitions(key):
            assert definition["strict"] is True
            schema = definition["input_schema"]
            assert schema["additionalProperties"] is False
            assert sorted(schema["required"]) == sorted(schema["properties"])


class TestTheDefaultIsUnchanged:
    """Every pre-existing caller passes no niche, and must get what it always
    got. This is what lets the change land without touching them."""

    def test_no_argument_is_the_full_enabled_set(self):
        assert tool_names() == GLOBAL_ORDER
        assert tool_definitions() == [s.definition() for s in ENABLED_TOOLS]

    @pytest.mark.parametrize("given", ["", "nope", "DENTAL"])
    def test_an_unknown_niche_falls_back_rather_than_emptying(self, given):
        assert tool_names(given) == GLOBAL_ORDER

    def test_tools_for_returns_the_shared_tuple_for_the_default(self):
        assert tools_for() is ENABLED_TOOLS


class TestWhoGetsWhat:
    def test_every_niche_has_an_entry(self):
        assert set(NICHE_TOOLS) == set(NICHES)

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_the_entry_matches_what_the_niche_declares(self, key):
        assert set(tool_names(key)) == set(NICHES[key].tools)

    @pytest.mark.parametrize("key", WITHOUT_JOB_STATUS)
    def test_the_trades_mechanism_is_off_where_it_makes_no_sense(self, key):
        assert "get_job_status" not in tool_names(key)

    @pytest.mark.parametrize("key", WITH_JOB_STATUS)
    def test_it_is_kept_everywhere_else(self, key):
        assert "get_job_status" in tool_names(key)

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_every_niche_can_reach_a_human_and_answer_a_question(self, key):
        names = tool_names(key)
        assert "handoff_to_human" in names
        assert "get_business_info" in names

    def test_no_niche_is_handed_a_tool_that_does_not_exist(self):
        known = {spec.name for spec in tools_mod._SPECS}
        for key, niche in NICHES.items():
            assert set(niche.tools) <= known, f"{key} names a tool with no spec"
