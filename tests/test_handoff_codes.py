"""The handoff reason-code taxonomy.

`classify` sits between a customer message and a human being told about it, so
most of what is tested here is that it cannot fail: not that it is clever, but
that no input makes it raise, and that nothing it returns can be a value the
column has never heard of.

The interesting cases are the ambiguous words. "lawyer" means
`legal_or_insurance` to a garage and `legal_advice` to an immigration firm;
"offer" means a negotiation to an estate agency and nothing in particular to a
dentist. One global list with per-niche priority is what makes both true at
once, and those are the tests worth reading.
"""

from __future__ import annotations

import inspect

import pytest

from app.agent.niche import NICHES
from app.core import handoff
from app.core.handoff import HandoffCode


class TestTheListIsClosed:
    def test_every_code_has_a_label(self):
        """A code with no label renders as a bare slug in the console, which is
        how an owner ends up reading `offer_or_negotiation`."""
        missing = [c.value for c in HandoffCode if c not in handoff.LABELS]
        assert not missing

    def test_no_label_is_an_engineer_talking(self):
        """Labels are what an owner reads, so they are sentences rather than
        identifiers - no underscores, and a capital letter to start."""
        for code, text in handoff.LABELS.items():
            assert "_" not in text, code
            assert text[0].isupper(), code

    def test_all_codes_is_the_enum_in_declaration_order(self):
        assert handoff.ALL_CODES == tuple(c.value for c in HandoffCode)

    def test_the_schema_offers_exactly_these(self):
        """The tool's enum and this module must not drift: a code the schema
        allows but `normalise` rejects becomes a silent `other`."""
        from app.agent.tools import BY_NAME

        schema = BY_NAME["handoff_to_human"].input_schema
        assert schema["properties"]["code"]["enum"] == list(handoff.ALL_CODES)
        assert "code" in schema["required"]

    def test_the_column_is_wide_enough_for_the_longest_code(self):
        """`String(32)`. A longer code would be truncated on write and then
        fail to match itself on read."""
        from app.models import Conversation

        width = Conversation.__table__.c.handoff_code.type.length
        assert max(len(c) for c in handoff.ALL_CODES) <= width

    def test_every_code_a_niche_declares_actually_exists(self):
        """`niche.py` writes its codes as strings, so a typo there would be a
        code that is silently never emitted."""
        unknown = {
            (key, name)
            for key, spec in NICHES.items()
            for name in spec.escalate_codes
            if handoff.normalise(name) is None
        }
        assert not unknown


class TestNormalise:
    @pytest.mark.parametrize(
        "raw",
        [
            "medical_concern",
            "MEDICAL_CONCERN",
            "  medical_concern  ",
            "medical-concern",
            "medical concern",
            "HandoffCode.medical_concern",
        ],
    )
    def test_the_shapes_a_model_actually_emits(self, raw):
        assert handoff.normalise(raw) is HandoffCode.MEDICAL_CONCERN

    @pytest.mark.parametrize(
        "raw", ["", None, "   ", "nonsense", "medical", "other_thing", "42"]
    )
    def test_anything_else_is_none_rather_than_an_exception(self, raw):
        assert handoff.normalise(raw) is None


class TestCodesForANiche:
    def test_a_regulated_niche_gets_its_own_short_list(self):
        codes = handoff.codes_for("dental")
        assert HandoffCode.MEDICAL_CONCERN in codes
        assert HandoffCode.CLINICAL_ADVICE in codes
        # Not a dental concern, and listing it would be noise.
        assert HandoffCode.OFFER_OR_NEGOTIATION not in codes
        assert len(codes) < len(HandoffCode)

    @pytest.mark.parametrize("niche", ["auto", "plumbing", "", "nonsense"])
    def test_everything_else_gets_the_universal_set(self, niche):
        """The six US verticals declare no codes, and an unknown vertical is
        not a reason to hand a shop an empty list."""
        assert handoff.codes_for(niche) == handoff.UNIVERSAL_CODES

    @pytest.mark.parametrize("niche", [*NICHES, "", "nonsense"])
    def test_other_is_always_available(self, niche):
        """A list that cannot say "something else" forces a wrong answer."""
        assert HandoffCode.OTHER in handoff.codes_for(niche)

    def test_the_universal_set_is_the_one_a_us_shop_already_lived_by(self):
        """These are the escalations `DISCRETION_RULES` has always described -
        accident, lawyer, insurance, complaint, refund, angry, high-value,
        twice-misunderstood, asked for a person."""
        assert set(handoff.UNIVERSAL_CODES) >= {
            HandoffCode.ASKED_FOR_HUMAN,
            HandoffCode.ANGRY,
            HandoffCode.COMPLAINT,
            HandoffCode.NOT_UNDERSTOOD,
            HandoffCode.LEGAL_OR_INSURANCE,
            HandoffCode.PAYMENT_OR_REFUND,
            HandoffCode.HIGH_VALUE,
        }


class TestANamedCodeWins:
    def test_the_model_s_own_code_beats_the_text(self):
        """When `handoff_to_human` names a code, nothing is inferred - the
        text table is a fallback for the paths that have no code at all."""
        assert (
            handoff.classify("customer is furious", code="price_or_quote")
            is HandoffCode.PRICE_OR_QUOTE
        )

    def test_a_code_from_outside_the_niche_is_kept_not_tidied_away(self):
        """A dental shop reporting `offer_or_negotiation` is strange, and the
        honest record of a strange event is worth more than a tidy `other`."""
        assert (
            handoff.classify("", code="offer_or_negotiation", niche="dental")
            is HandoffCode.OFFER_OR_NEGOTIATION
        )

    def test_an_unknown_code_falls_through_to_the_text(self):
        assert (
            handoff.classify("the tooth is bleeding", code="made_up", niche="dental")
            is HandoffCode.MEDICAL_CONCERN
        )


class TestReadingTheFreeText:
    @pytest.mark.parametrize(
        ("niche", "text", "expected"),
        [
            # --- dental: pain outranks everything, including a price question
            ("dental", "her tooth hurts and she wants a price", "medical_concern"),
            ("dental", "patient says the gum is bleeding", "medical_concern"),
            ("dental", "le duele mucho la muela", "medical_concern"),
            ("dental", "asked if she can have it while pregnant", "clinical_advice"),
            ("dental", "wants to know the anaesthetic used", "clinical_advice"),
            ("dental", "sent a photo of her teeth", "clinical_advice"),
            ("dental", "asked how much a veneer costs", "price_or_quote"),
            # --- real estate
            ("real_estate", "buyer wants to put in an offer", "offer_or_negotiation"),
            ("real_estate", "asked about the golden visa", "legal_advice"),
            ("real_estate", "wants to know about mortgage options", "legal_advice"),
            ("real_estate", "asked to arrange a viewing", "viewing_logistics"),
            # --- immigration: detention outranks everything
            ("immigration", "her husband is detained at the CIE", "urgent_deadline"),
            ("immigration", "has a court date next week", "urgent_deadline"),
            ("immigration", "asked whether he qualifies for arraigo", "legal_advice"),
            ("immigration", "wants an update on my case", "case_specific"),
            ("immigration", "asked which documents to bring", "document_request"),
            # --- the universal table, for a shop with no niche opinion
            ("auto", "customer asked for a person", "asked_for_human"),
            ("auto", "customer is angry about the delay", "angry"),
            ("auto", "wants to make a complaint", "complaint"),
            ("auto", "mentioned their insurance", "legal_or_insurance"),
            ("auto", "asked for a refund", "payment_or_refund"),
            ("auto", "could not understand the customer twice", "not_understood"),
            ("auto", "unusually large job, a whole fleet", "high_value"),
            ("auto", "asked us to delete my data", "data_request"),
        ],
    )
    def test_it_reads_the_reason(self, niche, text, expected):
        assert handoff.classify(text, niche=niche).value == expected

    def test_the_same_word_means_different_things_in_different_niches(self):
        """This is the whole argument for one global list with per-niche
        priority rather than per-niche lists: "lawyer" is an insurance matter
        to a garage and the core of the service to an immigration firm."""
        text = "the customer mentioned their lawyer"
        assert handoff.classify(text, niche="auto") is HandoffCode.LEGAL_OR_INSURANCE
        assert (
            handoff.classify(text, niche="immigration") is HandoffCode.LEGAL_ADVICE
        )

    def test_a_word_inside_another_word_does_not_count(self):
        """The bug this exists for: plain substring matching turned "the
        application was denied" into a NIE escalation, because "denied"
        contains "nie"."""
        assert (
            handoff.classify("the application was denied", niche="immigration")
            is not HandoffCode.CASE_SPECIFIC
        )

    def test_accented_spanish_is_matched_on_the_stem(self):
        """Patterns ending in `*` are prefixes, which is how one entry covers
        hinchazon, hinchado and hinchada without three."""
        for text in ["tiene hinchazon", "la cara hinchada", "esta hinchado"]:
            assert handoff.classify(text, niche="dental") is (
                HandoffCode.MEDICAL_CONCERN
            ), text

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "asdfghjkl", "the customer said hello", "ok", "12345", "🙂"],
    )
    def test_nothing_recognisable_is_other(self, text):
        assert handoff.classify(text) is HandoffCode.OTHER


class TestItCannotFail:
    @pytest.mark.parametrize(
        "text", ["", "a" * 5000, "\x00\x01", "[]{}()*+?", "ñ" * 100, "\n\n\t"]
    )
    def test_no_input_raises(self, text):
        assert handoff.classify(text) in set(HandoffCode)

    def test_no_argument_at_all_raises_nothing(self):
        assert handoff.classify() is HandoffCode.OTHER

    @pytest.mark.parametrize("code", [None, "", 0, 1, [], {}])
    def test_a_junk_code_raises_nothing(self, code):
        assert handoff.classify("hello", code=code) is HandoffCode.OTHER

    def test_the_signature_is_the_one_every_caller_uses(self):
        """`code` and `niche` are keyword-only so the positional argument can
        only ever be the free text - the thing every call site has."""
        params = inspect.signature(handoff.classify).parameters
        assert list(params) == ["text", "code", "niche"]
        assert params["code"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["niche"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_a_returned_value_is_always_storable(self):
        """Every code fits the column and is a plain string to the driver,
        because `StrEnum` is a `str`."""
        for code in HandoffCode:
            assert isinstance(code.value, str)
            assert len(code.value) <= 32


class TestLabelsForTheConsole:
    def test_a_known_code_is_shown_in_words(self):
        assert handoff.label("medical_concern") == "Pain, injury or possible infection"

    def test_an_unknown_code_is_shown_as_itself(self):
        """A code that outlived its label should be visible, not blank - a
        blank cell reads as "no handoff" when there was one."""
        assert handoff.label("retired_code") == "retired_code"

    @pytest.mark.parametrize("raw", [None, ""])
    def test_nothing_is_nothing(self, raw):
        assert handoff.label(raw) == ""


class TestThePromptNamesTheNichesCodes:
    def test_a_regulated_niche_is_shown_its_own_list(self):
        from app.agent.prompt import build_system_prompt

        text = build_system_prompt(
            {"name": "Clinica", "config": {"vertical": "dental"}}, "whatsapp"
        )
        assert "## Handoff codes" in text
        assert "`medical_concern`" in text
        assert "`offer_or_negotiation`" not in text

    def test_a_us_vertical_is_shown_the_universal_list(self):
        """Every shop hands off, so every shop gets a list. The trades get the
        escalations their rules already describe, and no clinical codes."""
        from app.agent.prompt import build_system_prompt

        text = build_system_prompt(
            {"name": "Westside", "config": {"vertical": "auto"}}, "sms"
        )
        assert "`legal_or_insurance`" in text
        assert "`clinical_advice`" not in text

    def test_no_niche_leaks_another_niches_codes(self):
        from app.agent.prompt import build_system_prompt

        for key in NICHES:
            text = build_system_prompt(
                {"name": "X", "config": {"vertical": key}}, "sms"
            )
            block = text[text.index("## Handoff codes") :]
            block = block[: block.index("## Language")]
            offered = {
                code.value for code in HandoffCode if f"`{code.value}`" in block
            }
            assert offered == {c.value for c in handoff.codes_for(key)}, key
