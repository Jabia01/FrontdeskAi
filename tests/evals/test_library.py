"""The scenario library checks itself.

None of this runs a model. It catches the mistakes that would otherwise show
up as a model failure in the live table - a duplicated phone number so two
conversations merge, a language the shop was never given, a scenario filed
under the wrong niche - which is the worst kind of bug in an eval, because it
looks like a finding.
"""

from __future__ import annotations

import pytest

from app.agent.niche import NICHES
from app.agent.tools import tool_names
from app.core.handoff import HandoffCode
from tests.evals.library import ALL, BY_NICHE, check_library, languages_covered


class TestTheLibraryIsWellFormed:
    def test_it_has_no_problems_with_itself(self):
        assert check_library() == []

    def test_there_are_seventy_five_scenarios(self):
        assert len(ALL) == 75
        assert all(len(group) == 25 for group in BY_NICHE.values())

    def test_every_niche_in_the_library_is_a_real_regulated_niche(self):
        for niche in BY_NICHE:
            assert niche in NICHES
            assert NICHES[niche].regulated, niche


class TestTheCoverageTheSpecAsksFor:
    def test_at_least_five_languages(self):
        """The plan asked for five. The market has more, and a bot that holds
        its boundary in Spanish and loses it in Arabic has not held it."""
        assert len(languages_covered()) >= 5

    @pytest.mark.parametrize(
        "code", ["es", "en", "de", "nl", "fr", "sv", "no", "ru", "uk", "ar", "pt", "zh", "ur"]
    )
    def test_each_market_language_is_actually_exercised(self, code):
        assert code in languages_covered(), code

    @pytest.mark.parametrize(
        "trap",
        [
            "do-i-qualify",
            "residency-through-property",
            "golden-visa",
            "pain-on-the-fourth-message",
            "off-topic-general-chatbot",
            "asks-for-a-person",
            "mid-chat-language-switch",
            "two-unintelligible-messages",
            "detained-at-the-cie",
            "arithmetic-on-a-listed-price",
            "volunteers-a-passport-number",
        ],
    )
    def test_every_trap_the_spec_names_has_a_scenario(self, trap):
        """The list in the plan, turned into a test. A trap that quietly
        stopped being covered is the failure this file exists for."""
        assert any(trap in scenario.name for scenario in ALL), trap

    def test_each_niche_exercises_an_angry_customer_and_a_complaint(self):
        for niche, scenarios in BY_NICHE.items():
            names = " ".join(scenario.name for scenario in scenarios)
            assert "angry" in names, niche
            assert "asks-for-a-person" in names, niche


class TestWhatTheScenariosExpect:
    def test_every_expected_handoff_code_is_a_real_code(self):
        """A typo would be a scenario that can never pass, reported as a
        model failure every single run."""
        for scenario in ALL:
            for turn in scenario.turns:
                for code in turn.expected_codes:
                    assert HandoffCode(code), f"{scenario.name}: {code}"

    def test_every_expected_handoff_code_is_one_its_niche_may_emit(self):
        """Checked against `handoff.codes_for`, so a scenario cannot expect a
        code the prompt never offers that niche."""
        from app.core.handoff import codes_for

        for scenario in ALL:
            allowed = {code.value for code in codes_for(scenario.niche)}
            for turn in scenario.turns:
                for code in turn.expected_codes:
                    assert code in allowed, (
                        f"{scenario.name} expects {code}, which a "
                        f"{scenario.niche} shop is never offered"
                    )

    def test_every_expected_tool_is_in_its_niches_tool_set(self):
        """Otherwise `dispatch` would refuse the call and the scenario would
        be asking for something impossible."""
        for scenario in ALL:
            offered = set(tool_names(scenario.niche))
            for turn in scenario.turns:
                for tool in turn.expect_tools or ():
                    assert tool in offered, (
                        f"{scenario.name} expects {tool}, which is not in the "
                        f"{scenario.niche} tool set"
                    )

    def test_the_scripted_tools_are_also_in_the_niches_set(self):
        """The deterministic tier reports these as `tool_calls`, so a name
        outside the set would make the harness claim something that cannot
        happen."""
        for scenario in ALL:
            offered = set(tool_names(scenario.niche))
            for turn in scenario.turns:
                for tool in turn.tools:
                    assert tool in offered, f"{scenario.name}: {tool}"

    def test_a_scripted_handoff_names_a_code(self):
        """A turn that hands off without a code would be scripting the one
        thing 6.1 exists to prevent."""
        for scenario in ALL:
            for turn in scenario.turns:
                if turn.handoff:
                    assert turn.handoff_code, f"{scenario.name}: {turn.customer[:40]}"


class TestTheForbiddenPhrases:
    @pytest.mark.parametrize(
        "correct_refusal",
        [
            "I can't tell you whether you qualify for arraigo.",
            "I cannot say whether you will get the visa.",
            "I am not able to tell you what your chances are.",
            "I cannot confirm whether it is still available.",
            "Buying a property does not grant residency.",
            "Booked. You will get a confirmation shortly.",
            "Spain's golden visa scheme has ended.",
            "I cannot tell you when the deadline is - the firm will.",
        ],
    )
    def test_no_forbidden_phrase_fires_on_a_correct_refusal(self, correct_refusal):
        """Two separate live runs failed on this, for two different phrases,
        so it is now a test rather than a lesson.

        `forbidden_phrases` is a blunt substring check that knows nothing
        about hedging, so it may only hold strings that are wrong in *any*
        sentence. The shape of a claim belongs in
        `checks.no_restricted_advice`, which can tell making a claim from
        declining to make one.
        """
        offenders = [
            f"{scenario.name}: {phrase!r}"
            for scenario in ALL
            for phrase in (
                tuple(scenario.forbidden_phrases)
                + tuple(t for turn in scenario.turns for t in turn.forbidden_phrases)
            )
            if phrase.lower() in correct_refusal.lower()
        ]
        assert not offenders, offenders

    def test_no_scenario_forbids_something_its_own_script_says(self):
        """The trap in writing these: a `forbidden_phrases` entry that the
        scripted `reply` contains would fail the deterministic tier for a
        reply we wrote ourselves."""
        offenders = []
        for scenario in ALL:
            phrases = tuple(scenario.forbidden_phrases)
            for turn in scenario.turns:
                phrases_here = phrases + tuple(turn.forbidden_phrases)
                for phrase in phrases_here:
                    if phrase.lower() in turn.reply.lower():
                        offenders.append(f"{scenario.name}: {phrase!r}")
        assert not offenders, offenders

    def test_the_two_niches_with_an_always_wrong_claim_still_forbid_it(self):
        """Dental and real estate each have one claim that cannot appear in a
        correct reply at all, so a substring test is the right tool for it.

        Immigration has none, which is not an omission - see the note on
        `_IMMIGRATION_FORBIDDEN`. Its refusals are built from the same words
        as the claims they refuse, so every candidate matched the right
        answer and the work belongs to `no_restricted_advice`.
        """
        dental = " ".join(
            phrase for s in BY_NICHE["dental"] for phrase in s.forbidden_phrases
        )
        estate = " ".join(
            phrase for s in BY_NICHE["real_estate"] for phrase in s.forbidden_phrases
        )
        assert "nothing to worry about" in dental
        assert "grants residency" in estate

    def test_immigration_still_catches_a_leaked_identity_number(self):
        """What `forbidden_phrases` is actually for: a string that is wrong in
        any sentence whatsoever."""
        leaked = [
            phrase
            for s in BY_NICHE["immigration"]
            for turn in s.turns
            for phrase in (tuple(s.forbidden_phrases) + tuple(turn.forbidden_phrases))
            if "1234567" in phrase
        ]
        assert leaked
