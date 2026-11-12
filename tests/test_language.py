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
