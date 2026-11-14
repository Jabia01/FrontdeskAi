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


# --------------------------------------------------------------- handler tests


#: The clinic the dental handler tests run against.
DENTAL_CONFIG = {
    "currency": "€",
    "price_ranges": [
        {"service": "Veneers", "low": 400, "high": 700},
        {"service": "Check-up", "low": 60, "high": 60},
        {"service": "Implants", "low": None, "high": None},
    ],
}


class TestPriceRange:
    async def test_it_quotes_a_from_figure_in_the_shops_currency(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = json.loads(
            await tools_mod._price_range(c["tool_ctx"], "veneers")
        )
        assert out["from"] == "€400"
        assert out["listed_upper"] == "€700"
        assert "from" in out["instruction"].lower()

    async def test_it_matches_loosely_on_the_customers_wording(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = json.loads(
            await tools_mod._price_range(c["tool_ctx"], "  VENEERS  ")
        )
        assert out["service"] == "Veneers"

    async def test_a_single_figure_has_no_upper_bound(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = json.loads(
            await tools_mod._price_range(c["tool_ctx"], "check-up")
        )
        assert out["from"] == "€60"
        assert "listed_upper" not in out

    async def test_an_unlisted_service_is_refused_not_estimated(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = await tools_mod._price_range(c["tool_ctx"], "whitening")
        assert "no price is listed" in out.lower()
        assert "do not estimate" in out.lower()
        # It still says what *is* priced, so the agent can redirect.
        assert "Veneers" in out

    async def test_a_service_with_no_figure_is_refused(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = await tools_mod._price_range(c["tool_ctx"], "implants")
        assert "no figure set" in out.lower()
        assert "do not estimate" in out.lower()

    async def test_a_shop_with_no_prices_at_all_is_refused(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        c["shop"].config["price_ranges"] = []
        out = await tools_mod._price_range(c["tool_ctx"], "veneers")
        assert "has not listed any prices" in out.lower()

    async def test_it_never_emits_a_number_the_business_did_not_type(
        self, niche_ctx
    ):
        """The whole point of the tool. 400 and 700 are listed; 550, 1100 and
        any other arithmetic on them are not."""
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = await tools_mod._price_range(c["tool_ctx"], "veneers")
        for invented in ("550", "1100", "300", "140"):
            assert invented not in out


class TestSendDepositLink:
    async def test_it_refuses_when_none_is_configured(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = await tools_mod._send_deposit_link(c["tool_ctx"], "veneers")
        assert "do not invent a link" in out.lower()
        assert "http" not in out.lower()

    async def test_it_returns_the_configured_link_verbatim(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        url = "https://clinic.example/pay/deposit"
        c["shop"].config["business_info"] = {"deposit_url": url}
        out = json.loads(
            await tools_mod._send_deposit_link(c["tool_ctx"], "veneers")
        )
        assert out["url"] == url
        assert "exactly as written" in out["instruction"]


class TestCaptureTravelDates:
    async def test_it_saves_a_lead_without_clinical_detail(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = await tools_mod._capture_travel_dates(
            c["tool_ctx"], "3 June", "10 June", "veneers"
        )
        assert "saved" in out.lower()

        from sqlalchemy import select

        from app.models import Lead

        rows = (
            await c["session"].execute(
                select(Lead).where(Lead.client_id == c["shop"].client_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert "3 June" in rows[0].issue and "10 June" in rows[0].issue
        assert rows[0].item == "veneers"

    async def test_it_asks_again_when_no_dates_were_given(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = await tools_mod._capture_travel_dates(
            c["tool_ctx"], "", "", "veneers"
        )
        assert "no dates given" in out.lower()

    async def test_it_does_not_promise_the_treatment_fits_the_trip(self, niche_ctx):
        c = niche_ctx("dental", **DENTAL_CONFIG)
        out = await tools_mod._capture_travel_dates(
            c["tool_ctx"], "3 June", "10 June", "implants"
        )
        assert "do not promise" in out.lower()


@pytest.fixture
async def niche_ctx(shop_a):
    """Factory: a ToolContext for a shop configured as any niche."""
    from app.agent.tools import ToolContext
    from app.core.router import load_shop
    from app.db import session_scope
    from app.models import Channel
    from app.repo import find_or_create_conversation, find_or_create_customer

    async with session_scope() as session:
        customer = await find_or_create_customer(session, shop_a, "+34600333444")
        conversation = await find_or_create_conversation(
            session, shop_a, customer.id, Channel.simulator
        )
        customer_id, conversation_id = customer.id, conversation.id

    async with session_scope() as session:
        shop = await load_shop(session, shop_a, use_cache=False)

        def make(vertical: str, **config):
            shop.config["vertical"] = vertical
            shop.config.update(config)
            return {
                "shop": shop,
                "session": session,
                "tool_ctx": ToolContext(
                    session=session,
                    shop=shop,
                    conversation_id=conversation_id,
                    customer_id=customer_id,
                ),
            }

        yield make


async def _leads(session, client_id):
    from sqlalchemy import select

    from app.models import Lead

    rows = await session.execute(select(Lead).where(Lead.client_id == client_id))
    return rows.scalars().all()


class TestQualifyBuyer:
    async def test_it_saves_what_was_given_and_names_what_is_missing(
        self, niche_ctx
    ):
        c = niche_ctx("real_estate")
        out = json.loads(
            await tools_mod._qualify_buyer(
                c["tool_ctx"], "900000", "Marbella", "", "mortgage", ""
            )
        )
        assert sorted(out["saved"]) == ["area", "budget", "funding"]
        assert sorted(out["still_missing"]) == ["nie", "timeline"]
        rows = await _leads(c["session"], c["shop"].client_id)
        assert len(rows) == 1
        assert "Marbella" in rows[0].issue and "900000" in rows[0].issue

    async def test_it_refuses_to_advise_on_nie_or_tax(self, niche_ctx):
        c = niche_ctx("real_estate")
        out = await tools_mod._qualify_buyer(
            c["tool_ctx"], "", "Marbella", "", "", "no idea what that is"
        )
        assert "do not advise" in out.lower()
        assert "residency" in out.lower()

    async def test_nothing_to_save_asks_rather_than_writing_an_empty_lead(
        self, niche_ctx
    ):
        c = niche_ctx("real_estate")
        out = await tools_mod._qualify_buyer(c["tool_ctx"], "", "", "", "", "")
        assert "nothing to save" in out.lower()
        assert await _leads(c["session"], c["shop"].client_id) == []


LISTINGS = [
    {
        "reference": "MRB-1",
        "summary": "Three-bed villa with a pool.",
        "price": "850000",
        "area": "Marbella",
        "bedrooms": "3",
    },
    {
        "reference": "EST-9",
        "summary": "Two-bed apartment near the port.",
        "price": "1200000",
        "area": "Estepona",
        "bedrooms": "2",
    },
]


class TestSendMatchingListings:

    async def test_it_returns_the_agencys_own_words(self, niche_ctx):
        c = niche_ctx("real_estate", business_info={"listings": LISTINGS})
        out = json.loads(
            await tools_mod._send_matching_listings(
                c["tool_ctx"], "Marbella", "900000", ""
            )
        )
        assert [item["reference"] for item in out["listings"]] == ["MRB-1"]
        assert out["listings"][0]["summary"] == "Three-bed villa with a pool."
        assert "do not confirm availability" in out["instruction"].lower()

    async def test_it_respects_the_budget_ceiling(self, niche_ctx):
        c = niche_ctx("real_estate", business_info={"listings": LISTINGS})
        out = await tools_mod._send_matching_listings(c["tool_ctx"], "", "900000", "")
        assert "EST-9" not in out

    async def test_no_match_refuses_rather_than_describing_something(
        self, niche_ctx
    ):
        c = niche_ctx("real_estate", business_info={"listings": LISTINGS})
        out = await tools_mod._send_matching_listings(
            c["tool_ctx"], "Valencia", "", ""
        )
        assert "do not describe a property" in out.lower()
        assert "invent a reference" in out.lower()

    async def test_an_agency_with_no_listings_configured_is_refused(self, niche_ctx):
        c = niche_ctx("real_estate", business_info={})
        out = await tools_mod._send_matching_listings(c["tool_ctx"], "Marbella", "", "")
        assert "none is configured" in out.lower()


class TestIntakeCaseType:
    async def test_it_records_the_matter_without_endorsing_the_route(
        self, niche_ctx
    ):
        c = niche_ctx("immigration")
        out = json.loads(
            await tools_mod._intake_case_type(
                c["tool_ctx"], "arraigo", "Been here three years, working."
            )
        )
        assert out["case_type"] == "arraigo"
        for forbidden in ("qualify", "how long", "which route"):
            assert forbidden in out["instruction"].lower()
        rows = await _leads(c["session"], c["shop"].client_id)
        assert rows[0].item == "arraigo"

    async def test_an_unknown_type_becomes_other_rather_than_failing(
        self, niche_ctx
    ):
        c = niche_ctx("immigration")
        out = json.loads(
            await tools_mod._intake_case_type(c["tool_ctx"], "asylum-ish", "...")
        )
        assert out["case_type"] == "other"


class TestDocumentChecklist:
    async def test_it_sends_only_a_list_the_firm_published(self, niche_ctx):
        c = niche_ctx(
            "immigration",
            business_info={
                "document_checklists": {"arraigo": ["Passport", "Padrón", "Contract"]}
            },
        )
        out = json.loads(
            await tools_mod._document_checklist(c["tool_ctx"], "arraigo")
        )
        assert out["documents"] == ["Passport", "Padrón", "Contract"]
        assert "do not add to it" in out["instruction"].lower()

    async def test_an_unpublished_type_gets_no_list_at_all(self, niche_ctx):
        c = niche_ctx(
            "immigration",
            business_info={"document_checklists": {"arraigo": ["Passport"]}},
        )
        out = await tools_mod._document_checklist(c["tool_ctx"], "nationality")
        assert "has not published" in out.lower()
        assert "do not list documents" in out.lower()
        assert "partial list" in out.lower()

    async def test_a_firm_with_no_checklists_gets_nothing(self, niche_ctx):
        c = niche_ctx("immigration", business_info={})
        out = await tools_mod._document_checklist(c["tool_ctx"], "arraigo")
        assert "has not published" in out.lower()
