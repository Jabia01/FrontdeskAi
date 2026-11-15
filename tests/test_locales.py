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


class TestSpanishMatchesEnglishStructurally:
    """The Spanish page is the one Spain reads, so these are not nice-to-haves.

    A key present in `en.COPY` and missing in `es.COPY` renders an empty
    heading on a live page - Jinja prints nothing for an undefined attribute
    rather than raising - so the only way to catch it is to compare the two
    shapes. That is what this class does, one level deep and then recursively.
    """

    def _shape(self, value):
        """The structure of a copy value, with the strings thrown away."""
        if isinstance(value, dict):
            return {k: self._shape(v) for k, v in sorted(value.items())}
        if isinstance(value, list):
            return [self._shape(v) for v in value]
        return type(value).__name__

    @pytest.mark.parametrize(
        "code", sorted(c for c in locales.LOCALES if c != locales.DEFAULT_LOCALE)
    )
    def test_the_copy_dict_has_exactly_the_same_shape(self, code):
        assert self._shape(locales.get(code).COPY) == self._shape(
            locales.en.COPY
        )

    @pytest.mark.parametrize(
        "code", sorted(c for c in locales.LOCALES if c != locales.DEFAULT_LOCALE)
    )
    def test_the_niche_pages_are_the_same_pages(self, code):
        """Same slugs and the same paths in every language: /dental and
        /inmobiliarias are already the words a Spanish visitor expects, and a
        translated URL would break every link anyone has shared."""
        other = locales.get(code).NICHE_PAGES
        assert set(other) == set(locales.en.NICHE_PAGES)
        for slug, page in other.items():
            english = locales.en.NICHE_PAGES[slug]
            assert page["path"] == english["path"]
            assert page["niche"] == english["niche"]
            assert page["demo"] == english["demo"]

    def test_the_captured_conversations_are_not_translated(self):
        """A real capture is evidence. The Dutch patient wrote in Dutch and was
        answered in Dutch; rewriting either side would turn evidence into
        advertising, and `en.py` makes that a standing rule."""
        for spanish, english in zip(
            locales.get("es").DEMO_CONVERSATIONS,
            locales.en.DEMO_CONVERSATIONS,
            strict=True,
        ):
            assert spanish["turns"] == english["turns"]
            assert spanish["code"] == english["code"]

    def test_only_the_chip_label_is_translated(self):
        spanish = {c["code"]: c["label"] for c in locales.get("es").DEMO_CONVERSATIONS}
        assert spanish["nl"] == "Neerlandés"
        assert spanish["ar"] == "Árabe"

    def test_the_language_demo_sentences_are_not_translated(self):
        """Each one is how a customer writing in that language actually asks
        "what time do you close today?"."""
        for spanish, english in zip(
            locales.get("es").LANGUAGE_DEMO,
            locales.en.LANGUAGE_DEMO,
            strict=True,
        ):
            assert spanish["text"] == english["text"]

    def test_the_measured_numbers_are_the_measured_numbers(self):
        """Only the decimal separator moves - Spanish writes 3,2."""
        assert [m["value"] for m in locales.get("es").METRICS][:2] == ["3,2", "24"]
        assert [m["value"] for m in locales.en.METRICS][:2] == ["3.2", "24"]

    def test_reviews_are_off_in_every_locale(self):
        """The rule is a product decision, not a per-language one."""
        for code in locales.LOCALES:
            module = locales.get(code)
            assert module.SHOW_REVIEWS is False, code
            assert all(r.get("sample") for r in module.REVIEWS), code

    def test_the_headline_endings_obey_the_same_layout_contract(self):
        """The page reserves the height of the tallest ending so nothing below
        it shifts. Wildly uneven endings leave a visible gap instead."""
        for code in locales.LOCALES:
            tails = locales.get(code).HEADLINE_TAILS
            assert tails, code
            for tail in tails:
                assert tail.endswith("."), (code, tail)
                assert tail[0].islower(), (code, tail)
            lengths = [len(t) for t in tails]
            assert max(lengths) - min(lengths) <= 14, (code, lengths)

    def test_spanish_is_actually_accented(self):
        """An unaccented Spanish marketing page reads as illiterate, and the
        shell this was written through silently mangles non-ASCII - so assert
        the accents survived rather than trusting the toolchain."""
        copy = locales.get("es").COPY
        assert "Cómo funciona" == copy["nav"]["how"]
        assert "¿Qué tipo de negocio es?" == copy["signup"]["niche"]
        assert "mañana" in locales.get("es").METRICS[1]["label"]
