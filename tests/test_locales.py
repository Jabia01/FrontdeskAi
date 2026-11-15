"""The locale registry, and the gate that stops a half-translated site shipping.

The expensive mistake this guards against is a Spanish page that is nine-tenths
Spanish. A visitor reads the English paragraph as a bug in the product, not a
gap in the copy - and on a legal page it is worse than a bug.
"""

from __future__ import annotations

import pytest

from app.web import content, locales


class TestTheRegistry:
    def test_english_is_the_default_and_is_present(self):
        assert locales.DEFAULT_LOCALE == "en"
        assert "en" in locales.LOCALES

    @pytest.mark.parametrize("given", ["nope", "", None, "EN-GB", "de"])
    def test_an_unknown_locale_serves_the_default_rather_than_failing(self, given):
        """A bad path should give a visitor English, not a 404 on a marketing
        page."""
        assert locales.get(given) is locales.LOCALES["en"]

    @pytest.mark.parametrize("given", ["EN", "en", " en "])
    def test_lookup_is_forgiving_about_case_and_space(self, given):
        assert locales.get(given) is locales.LOCALES["en"]

    def test_is_known_distinguishes_a_real_locale_from_a_fallback(self):
        assert locales.is_known("en") is True
        assert locales.is_known("de") is False
        assert locales.is_known(None) is False


class TestEveryLocaleIsComplete:
    @pytest.mark.parametrize("code", sorted(locales.LOCALES))
    def test_it_defines_every_required_key(self, code):
        module = locales.get(code)
        for key in locales.REQUIRED_KEYS:
            assert hasattr(module, key), f"{code} is missing {key}"

    @pytest.mark.parametrize("code", sorted(locales.LOCALES))
    def test_no_required_value_is_empty(self, code):
        module = locales.get(code)
        for key in locales.REQUIRED_KEYS:
            value = getattr(module, key)
            if isinstance(value, bool):
                continue
            assert value or value == [], f"{code}.{key} is empty"

    def test_english_is_publishable(self):
        assert locales.missing("en") == []
        assert locales.is_publishable("en") is True

    def test_an_untranslated_string_blocks_publication(self, monkeypatch):
        """The gate itself, exercised - otherwise it is a function nobody has
        ever seen return True."""
        monkeypatch.setattr(
            locales.en, "HEADLINE_STEM", f"{locales.TRANSLATION_TODO} Never lose"
        )
        assert "HEADLINE_STEM" in locales.missing("en")
        assert locales.is_publishable("en") is False

    def test_it_finds_a_todo_nested_in_a_list_of_dicts(self, monkeypatch):
        monkeypatch.setattr(
            locales.en,
            "FAQ",
            [{"q": "Why?", "a": f"{locales.TRANSLATION_TODO} Because."}],
        )
        assert "FAQ" in locales.missing("en")

    def test_an_unknown_locale_is_never_publishable(self):
        assert locales.missing("de") == sorted(locales.REQUIRED_KEYS)
        assert locales.is_publishable("de") is False


class TestTheFrontDoorStillWorks:
    """`content` is what the tests and the templates have always imported. The
    move to locale modules was meant to be invisible to both."""

    @pytest.mark.parametrize("key", locales.REQUIRED_KEYS)
    def test_every_key_is_re_exported_from_the_default_locale(self, key):
        assert getattr(content, key) is getattr(locales.en, key)

    def test_visible_reviews_reads_the_patchable_flag(self, monkeypatch):
        """`test_web_site.py` patches `content.SHOW_REVIEWS`, so that name has
        to stay a module-level value here, not a re-read of the locale."""
        assert content.visible_reviews() == []
        monkeypatch.setattr(content, "SHOW_REVIEWS", True)
        assert content.visible_reviews() == list(content.REVIEWS)

    def test_reviews_are_still_marked_as_samples(self):
        assert content.reviews_are_samples() is True
