"""The country-code table, the four-step resolution, and per-shop overrides."""

from __future__ import annotations

import pytest

from app.agent.language import (
    Source,
    country_languages,
    greeting_instruction,
    language_name,
    normalise,
    resolve,
)


class TestCountryCodes:
    @pytest.mark.parametrize(
        "phone,enabled,expected",
        [
            # +1 is English, plus Spanish only when the shop enables it.
            ("+13125551234", ["en"], ["en"]),
            ("+13125551234", ["en", "es"], ["en", "es"]),
            # UK and Ireland: English only.
            ("+447700900123", ["en", "es"], ["en"]),
            ("+353871234567", ["en", "es"], ["en"]),
            # Spain, Mexico, Latin America.
            ("+34600123456", ["en"], ["en", "es"]),
            ("+5215512345678", ["en"], ["en", "es"]),
            ("+5491112345678", ["en"], ["en", "es"]),
            # Germany and Austria.
            ("+4915112345678", ["en"], ["en", "de"]),
            ("+436641234567", ["en"], ["en", "de"]),
            # France, Portugal/Brazil, Poland, Netherlands, Italy.
            ("+33612345678", ["en"], ["en", "fr"]),
            ("+351912345678", ["en"], ["en", "pt"]),
            ("+5511987654321", ["en"], ["en", "pt"]),
            ("+48221234567", ["en"], ["en", "pl"]),
            ("+31612345678", ["en"], ["en", "nl"]),
            ("+393331234567", ["en"], ["en", "it"]),
        ],
    )
    def test_table(self, phone, enabled, expected):
        assert country_languages(phone, enabled=enabled) == expected

    def test_longest_prefix_wins(self):
        """+593 (Ecuador) must not be matched as +59-something."""
        assert country_languages("+593991234567", enabled=["en"]) == ["en", "es"]

    def test_other_code_adds_main_language_only_if_enabled(self):
        assert country_languages("+8613800138000", enabled=["en"]) == ["en"]
        assert country_languages("+8613800138000", enabled=["en", "zh"]) == [
            "en",
            "zh",
        ]

    def test_unknown_code_defers_to_shop_default(self):
        assert country_languages("+9999999", enabled=["en"]) == []

    def test_shop_override_replaces_the_default(self):
        """The doc's Cicero case: Spanish first on every +1 number."""
        assert country_languages(
            "+17085551234", overrides={"+1": ["es", "en"]}, enabled=["es", "en"]
        ) == ["es", "en"]

    def test_number_without_plus_is_tolerated(self):
        assert country_languages("4915112345678", enabled=["en"]) == ["en", "de"]


class TestResolutionOrder:
    def test_1_confident_detection_wins(self):
        d = resolve(
            phone="+13125551234",
            detected="es",
            detected_confidence=0.95,
            stored="en",
            enabled=["en", "es"],
        )
        assert (d.primary, d.source) == ("es", Source.DETECTED)

    def test_2_weak_detection_falls_back_to_stored(self):
        """'ok' or an emoji must not overwrite a known language."""
        d = resolve(
            phone="+13125551234",
            detected="en",
            detected_confidence=0.2,
            stored="es",
            enabled=["en", "es"],
        )
        assert (d.primary, d.source) == ("es", Source.STORED)

    def test_3_country_code_for_a_brand_new_number(self):
        d = resolve(
            phone="+4915112345678", stored=None, enabled=["en", "de"]
        )
        assert d.source == Source.COUNTRY_CODE
        assert d.greeting == ("en", "de")

    def test_4_shop_default_when_country_code_adds_nothing(self):
        d = resolve(
            phone="+9999999",
            stored=None,
            enabled=["en", "es"],
            default_customer_language="es",
        )
        assert (d.primary, d.source) == ("es", Source.SHOP_DEFAULT)

    def test_detection_threshold_is_respected(self):
        at = resolve(
            phone="+1312", detected="pl", detected_confidence=0.75, stored="en",
            confidence_threshold=0.75,
        )
        below = resolve(
            phone="+1312", detected="pl", detected_confidence=0.74, stored="en",
            confidence_threshold=0.75,
        )
        assert at.primary == "pl"
        assert below.primary == "en"

    def test_region_tags_are_normalised(self):
        d = resolve(phone="+1312", detected="es-MX", detected_confidence=0.9)
        assert d.primary == "es"


class TestNormalise:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("en", "en"),
            ("ES", "es"),
            ("pt-BR", "pt"),
            ("zh_Hans", "zh"),
            ("und", None),
            ("", None),
            (None, None),
        ],
    )
    def test_cases(self, raw, expected):
        assert normalise(raw) == expected


class TestGreeting:
    def test_always_includes_english_and_the_any_language_line(self):
        d = resolve(phone="+4915112345678", enabled=["en", "de"])
        text = greeting_instruction(d, "Algarve Stays")

        assert "English" in text
        assert "German" in text
        assert "Algarve Stays" in text
        assert "reply in any language" in text

    def test_never_offers_a_menu(self):
        d = resolve(phone="+13125551234", enabled=["en", "es"])
        text = greeting_instruction(d, "Westside Auto").lower()

        assert "menu" in text  # it is mentioned only as a prohibition
        assert "do not offer a menu" in text
        assert "press 1" not in text

    def test_english_is_prepended_when_the_table_omits_it(self):
        """A Spanish-first override still greets in English too."""
        d = resolve(
            phone="+17085551234",
            enabled=["es", "en"],
            overrides={"+1": ["es"]},
        )
        text = greeting_instruction(d, "Cicero Tire")
        assert "English" in text
        assert "Spanish" in text


def test_language_name_falls_back_to_the_code():
    assert language_name("pl") == "Polish"
    assert language_name("xx") == "XX"


class TestEveryMarketLanguageResolvesFromACountryCode:
    """The twelve languages the three Spain-and-Portugal niches declare.

    `niche.market_languages` seeds a new shop's `enabled_languages`, and the
    first message to a stranger is chosen from the country code of the number
    they wrote from. So a language a niche claims to speak, that no prefix
    maps to, is a language the shop will never actually greet anybody in -
    and the failure is silent: the shop simply answers in its default.

    The table above already covered a handful. This covers the set the niches
    name, which is the set that has to work.
    """

    @pytest.mark.parametrize(
        ("phone", "language"),
        [
            ("+34600111222", "es"),  # Spain
            ("+351912345678", "pt"),  # Portugal
            ("+49170111222", "de"),  # Germany
            ("+31612345678", "nl"),  # Netherlands
            ("+33600111222", "fr"),  # France
            ("+46700111222", "sv"),  # Sweden
            ("+47900111222", "no"),  # Norway
            ("+79001112233", "ru"),  # Russia
            ("+380501112233", "uk"),  # Ukraine
            ("+212600111222", "ar"),  # Morocco
            ("+8613800138000", "zh"),  # China
            ("+923001112233", "ur"),  # Pakistan
        ],
    )
    def test_the_country_code_finds_the_language(self, phone, language):
        assert language in country_languages(phone, enabled=["en", language])

    @pytest.mark.parametrize(
        ("phone", "language"),
        [
            ("+34600111222", "es"),
            ("+49170111222", "de"),
            ("+212600111222", "ar"),
            ("+8613800138000", "zh"),
            ("+923001112233", "ur"),
        ],
    )
    def test_the_greeting_names_it_alongside_english(self, phone, language):
        """English is always offered as well - that rule predates the niches
        and is untouched - so the greeting has to name both."""
        decision = resolve(phone=phone, enabled=["en", language, "es"])
        text = greeting_instruction(decision, "Clinica Sol")
        assert language_name(language) in text
        assert "English" in text

    def test_every_language_a_niche_claims_has_a_prefix_somewhere(self):
        """The guard on the guard. If a niche gains a market language and no
        country code maps to it, this fails rather than the shop quietly
        never greeting in it.

        Spanish and English are exempt: they are the locale default and the
        always-on second language, so they are reached without a prefix.
        """
        from app.agent.niche import NICHES

        claimed = {
            code
            for spec in NICHES.values()
            for code in spec.market_languages
            if code not in {"en", "es"}
        }
        # One real number per language, chosen as the country the niche's
        # cohort actually writes from.
        samples = {
            "pt": "+351912345678",
            "de": "+49170111222",
            "nl": "+31612345678",
            "fr": "+33600111222",
            "sv": "+46700111222",
            "no": "+47900111222",
            "ru": "+79001112233",
            "uk": "+380501112233",
            "ar": "+212600111222",
            "zh": "+8613800138000",
            "ur": "+923001112233",
        }
        missing = sorted(code for code in claimed if code not in samples)
        assert not missing, (
            f"these market languages have no sample number here, so nothing "
            f"checks they are reachable: {missing}"
        )
        unreachable = sorted(
            code
            for code in claimed
            if code not in country_languages(samples[code], enabled=["en", code])
        )
        assert not unreachable, (
            f"these languages are claimed by a niche but no country prefix "
            f"maps to them: {unreachable}"
        )
