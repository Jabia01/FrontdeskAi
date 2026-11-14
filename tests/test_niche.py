"""The niche layer, and the promise that it leaves existing tenants alone.

Two kinds of assertion live here. The structural ones keep `NICHES` and
`VERTICALS` from drifting apart. The content ones pin the specific compliance
requirements the three regulated niches were built for - the golden-visa claim,
the no-photographs rule, the urgent-deadline handoff - so that a later reword
cannot quietly drop one.
"""

from __future__ import annotations

import pytest

from app.agent import tools as tools_mod
from app.agent.niche import (
    ALL_TOOLS,
    CORE_TOOLS,
    NICHES,
    REGULATED,
    niche_for,
)
from app.core.defaults import VERTICALS

LEGACY = ("auto", "salon", "pet", "trades", "repair", "other")
NEW = ("dental", "real_estate", "immigration")


class TestTheTwoRegistriesAgree:
    def test_every_vertical_has_a_niche_and_the_reverse(self):
        assert set(VERTICALS) == set(NICHES)

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_the_preset_is_built_from_the_spec(self, key):
        spec, preset = NICHES[key], VERTICALS[key]
        assert preset["label"] == spec.label
        assert preset["job_noun"] == spec.job_noun
        assert tuple(preset["services"]) == spec.services
        assert tuple(preset["handoff_keywords"]) == spec.handoff_keywords

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_every_tool_a_niche_names_actually_exists(self, key):
        known = {spec.name for spec in tools_mod._SPECS}
        assert set(NICHES[key].tools) <= known, (
            f"{key} names a tool that is not in tools._SPECS"
        )

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_a_niche_never_names_the_same_tool_twice(self, key):
        tools = NICHES[key].tools
        assert len(tools) == len(set(tools))


class TestExistingTenantsAreUntouched:
    """The whole safety argument for this change, as six assertions."""

    @pytest.mark.parametrize("key", LEGACY)
    def test_a_legacy_vertical_keeps_every_tool(self, key):
        assert NICHES[key].tools == ALL_TOOLS

    @pytest.mark.parametrize("key", LEGACY)
    def test_a_legacy_vertical_is_unregulated_and_unconstrained(self, key):
        spec = NICHES[key]
        assert spec.regulated is False
        assert spec.boundary == ""
        assert spec.rules == ()
        assert spec.first_contact_notices == ()

    @pytest.mark.parametrize("key", LEGACY)
    def test_a_legacy_vertical_still_prices_in_dollars(self, key):
        assert NICHES[key].currency == "$"


class TestTheRegulatedNiches:
    def test_exactly_the_three_new_niches_are_regulated(self):
        assert REGULATED == tuple(sorted(NEW))

    @pytest.mark.parametrize("key", NEW)
    def test_a_regulated_niche_carries_a_boundary_and_rules(self, key):
        spec = NICHES[key]
        assert spec.regulated is True
        assert len(spec.boundary) > 200, "a boundary this short is not a boundary"
        assert spec.rules, "a regulated niche needs its own hard rules"
        assert spec.escalate_codes, "a regulated niche needs reason codes"
        assert spec.escalate_keywords

    @pytest.mark.parametrize("key", NEW)
    def test_a_regulated_niche_sells_in_euros_and_speaks_the_market(self, key):
        spec = NICHES[key]
        assert spec.currency == "€"
        assert "es" in spec.market_languages
        assert "en" in spec.market_languages
        assert len(spec.market_languages) >= 5

    @pytest.mark.parametrize("key", NEW)
    def test_a_regulated_niche_can_always_reach_a_human(self, key):
        assert "handoff_to_human" in NICHES[key].tools


class TestTheBoundariesSayWhatTheyMustSay:
    def test_dental_refuses_clinical_advice_and_photographs(self):
        spec = NICHES["dental"]
        boundary = spec.boundary.lower()
        assert "clinical advice" in boundary
        assert "diagnos" in boundary
        # Health data: the spec is explicit that photos are not requested.
        assert any("photograph" in rule.lower() for rule in spec.rules)
        assert "medical_concern" in spec.escalate_codes

    def test_dental_hands_off_on_pain_and_bleeding(self):
        boundary = NICHES["dental"].boundary.lower()
        for symptom in ("pain", "bleeding", "swelling", "infection"):
            assert symptom in boundary

    def test_real_estate_denies_that_property_grants_residency(self):
        """Spain's golden visa has ended; Portugal's no longer covers property."""
        boundary = NICHES["real_estate"].boundary.lower()
        assert "golden visa" in boundary
        assert "does not grant residency" in boundary
        assert "portugal" in boundary

    def test_real_estate_refuses_to_negotiate_or_invent_detail(self):
        spec = NICHES["real_estate"]
        assert "negotiat" in spec.boundary.lower()
        assert any("never state a size" in rule.lower() for rule in spec.rules)
        assert "offer_or_negotiation" in spec.escalate_codes

    def test_immigration_refuses_advice_and_outcomes(self):
        spec = NICHES["immigration"]
        boundary = spec.boundary.lower()
        assert "legal advice" in boundary
        assert "predict outcomes" in boundary
        assert "legal_advice" in spec.escalate_codes

    def test_immigration_treats_detention_and_deadlines_as_urgent(self):
        spec = NICHES["immigration"]
        boundary = spec.boundary.lower()
        for trigger in ("expulsion", "detention", "ten days"):
            assert trigger in boundary
        assert "urgent_deadline" in spec.escalate_codes

    def test_immigration_discloses_that_this_is_not_legal_advice(self):
        notices = " ".join(NICHES["immigration"].first_contact_notices).lower()
        assert "not legal advice" in notices
        assert "lawyer-client" in notices

    def test_immigration_never_asks_for_identity_numbers(self):
        rules = " ".join(NICHES["immigration"].rules).lower()
        assert "passport number" in rules
        assert "nie" in rules


class TestTheJobStatusMechanism:
    """Trades "is it ready?" is disabled for two niches and re-used by the third."""

    @pytest.mark.parametrize("key", ("dental", "real_estate"))
    def test_it_is_off_where_the_question_makes_no_sense(self, key):
        assert "get_job_status" not in NICHES[key].tools

    def test_immigration_re_uses_it_for_case_status(self):
        assert "get_job_status" in NICHES["immigration"].tools
        assert NICHES["immigration"].job_noun == "case"

    def test_it_is_untouched_for_every_legacy_vertical(self):
        for key in LEGACY:
            assert "get_job_status" in NICHES[key].tools

    def test_the_core_set_is_a_subset_of_everything(self):
        assert set(CORE_TOOLS) < set(ALL_TOOLS)


class TestLookup:
    @pytest.mark.parametrize("given", ["nope", "", "   ", None, "DENTAL"])
    def test_an_unknown_key_falls_back_rather_than_raising(self, given):
        assert niche_for(given).key == "other"

    @pytest.mark.parametrize("key", sorted(NICHES))
    def test_a_known_key_round_trips(self, key):
        assert niche_for(key).key == key
