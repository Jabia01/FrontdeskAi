"""The console's form parsing, password hashing and phone handling.

All pure functions - no database, no HTTP. This is where a config save would go
wrong quietly, so the error messages are asserted as carefully as the values.
"""

from __future__ import annotations

import pytest

from app.web import forms, phone
from app.web.passwords import (
    code_matches,
    hash_code,
    hash_password,
    hash_token,
    needs_rehash,
    new_csrf_token,
    new_login_code,
    new_session_token,
    verify_password,
)


class TestLists:
    def test_lines_round_trip(self):
        assert forms.lines("Brakes\n\n  Oil change \n") == ["Brakes", "Oil change"]
        assert forms.from_lines(["Brakes", "Oil change"]) == "Brakes\nOil change"

    def test_csv_list_keeps_order_and_drops_duplicates(self):
        assert forms.csv_list(" es , EN , es ") == ["es", "en"]
        assert forms.csv_list("") == []

    def test_csv_list_accepts_newlines_too(self):
        assert forms.csv_list("es\nen") == ["es", "en"]


class TestPriceRanges:
    def test_parses_a_range(self):
        assert forms.price_ranges_from_text("Front brakes | 300 | 450") == [
            {"service": "Front brakes", "low": 300, "high": 450}
        ]

    def test_round_trips(self):
        rows = [{"service": "Oil change", "low": 60, "high": 90}]
        assert forms.price_ranges_from_text(forms.price_ranges_to_text(rows)) == rows

    def test_a_missing_column_names_the_line(self):
        with pytest.raises(forms.FormError, match="Line 2"):
            forms.price_ranges_from_text("Brakes | 300 | 450\nOil change | 60")

    def test_words_instead_of_prices_are_rejected(self):
        with pytest.raises(forms.FormError, match="must both be numbers"):
            forms.price_ranges_from_text("Brakes | cheap | dear")

    def test_a_backwards_range_is_rejected(self):
        """A reversed range would reach the customer as a nonsense quote."""
        with pytest.raises(forms.FormError, match="is above the high"):
            forms.price_ranges_from_text("Brakes | 450 | 300")

    def test_negative_prices_are_rejected(self):
        with pytest.raises(forms.FormError, match="negative"):
            forms.price_ranges_from_text("Brakes | -10 | 50")

    def test_decimals_are_accepted_and_rounded_down(self):
        assert forms.price_ranges_from_text("Wash | 19.99 | 29.99")[0]["low"] == 19


class TestFaqsAndInfo:
    def test_faq_keeps_pipes_in_the_answer(self):
        rows = forms.faqs_from_text("Hours? | Mon-Fri 8-6 | Sat 9-2")
        assert rows == [{"q": "Hours?", "a": "Mon-Fri 8-6 | Sat 9-2"}]

    def test_faq_needs_both_halves(self):
        with pytest.raises(forms.FormError, match="Line 1"):
            forms.faqs_from_text("Just a question with no answer")

    def test_business_info_round_trips(self):
        info = {"address": "1200 W Grand Ave", "payment": "Cards and cash"}
        assert forms.info_from_text(forms.info_to_text(info)) == info


class TestHours:
    def test_a_full_week(self):
        values = {day: "8:00-18:00" for day in forms.WEEKDAYS}
        values["sunday"] = "closed"
        parsed = forms.hours_from_values(values)
        assert parsed["monday"] == "8:00-18:00"
        assert parsed["sunday"] == "closed"

    def test_blank_means_closed(self):
        parsed = forms.hours_from_values({"monday": "  "})
        assert parsed["monday"] == "closed"
        # Every day is present, so the booking engine never reads a missing key.
        assert set(parsed) == set(forms.WEEKDAYS)

    def test_junk_is_refused_rather_than_silently_closing_the_shop(self):
        """The whole reason this uses the booking engine's own parser."""
        with pytest.raises(forms.FormError, match="not a time range"):
            forms.hours_from_values({"monday": "8-6pm"})

    def test_the_error_names_the_day(self):
        with pytest.raises(forms.FormError, match="Thursday"):
            forms.hours_from_values({"thursday": "morning"})


class TestJson:
    def test_empty_is_an_empty_object(self):
        assert forms.json_object("", label="Booking") == {}

    def test_bad_json_names_the_line(self):
        with pytest.raises(forms.FormError, match="invalid JSON on line"):
            forms.json_object('{"a": 1,\n"b"}', label="Booking")

    def test_a_list_is_not_an_object(self):
        with pytest.raises(forms.FormError, match="expected a JSON object"):
            forms.json_object("[1, 2]", label="Booking")

    def test_dump_is_stable(self):
        assert forms.json_dump({"b": 1, "a": 2}) == forms.json_dump({"a": 2, "b": 1})


class TestNumbers:
    def test_defaults_when_blank(self):
        assert forms.positive_int("", label="x", default=2, maximum=10) == 2

    def test_rejects_out_of_range(self):
        with pytest.raises(forms.FormError, match="between 1 and 10"):
            forms.positive_int("99", label="Hand off after", default=2, maximum=10)

    def test_rejects_words(self):
        with pytest.raises(forms.FormError, match="whole number"):
            forms.positive_int("two", label="x", default=2, maximum=10)


def test_changed_fields_lists_only_real_changes():
    before = {"tone": "a", "services": ["x"], "hours": {}}
    after = {"tone": "b", "services": ["x"], "faqs": []}
    assert forms.changed_fields(before, after) == ["faqs", "hours", "tone"]


class TestPasswords:
    def test_hash_and_verify(self):
        stored = hash_password("console-password-123")
        assert verify_password("console-password-123", stored)
        assert not verify_password("console-password-124", stored)

    def test_two_hashes_of_one_password_differ(self):
        """Per-password salt, or identical passwords would be visible."""
        assert hash_password("same") != hash_password("same")

    def test_an_empty_password_is_refused(self):
        with pytest.raises(ValueError, match="must not be empty"):
            hash_password("")

    def test_a_corrupt_hash_fails_instead_of_raising(self):
        """A 500 on a corrupt row would tell an attacker the account exists."""
        for broken in ("", "nonsense", "scrypt$x$y$z$q$r", "bcrypt$1$2$3$4$5"):
            assert verify_password("anything", broken) is False

    def test_needs_rehash_spots_weaker_parameters(self):
        assert needs_rehash(hash_password("x", n=2**12)) is True
        assert needs_rehash(hash_password("x")) is False
        assert needs_rehash("garbage") is True


class TestCodesAndTokens:
    def test_a_login_code_is_six_digits(self):
        for _ in range(50):
            code = new_login_code()
            assert len(code) == 6
            assert code.isdigit()

    def test_code_matching_is_by_hash(self):
        code = new_login_code()
        stored = hash_code(code)
        assert code_matches(code, stored)
        assert not code_matches("000000" if code != "000000" else "111111", stored)

    def test_session_tokens_are_long_and_unique(self):
        tokens = {new_session_token() for _ in range(100)}
        assert len(tokens) == 100
        assert all(len(token) >= 40 for token in tokens)

    def test_token_hash_is_stable_and_not_the_token(self):
        token = new_session_token()
        assert hash_token(token) == hash_token(token)
        assert token not in hash_token(token)
        assert len(hash_token(token)) == 64

    def test_csrf_tokens_are_unique(self):
        assert len({new_csrf_token() for _ in range(50)}) == 50


class TestPhone:
    @pytest.mark.parametrize(
        "raw",
        ["+13125550111", "3125550111", "(312) 555-0111", "312 555 0111", "312-555-0111"],
    )
    def test_everything_an_owner_might_type_normalises(self, raw):
        assert phone.normalise(raw) == "+13125550111"

    def test_an_international_number_keeps_its_country(self):
        assert phone.normalise("+48 22 123 45 67") == "+48221234567"

    @pytest.mark.parametrize("raw", ["", None, "   ", "hello", "123", "+1"])
    def test_junk_is_none_not_an_exception(self, raw):
        assert phone.normalise(raw) is None

    def test_display_is_readable(self):
        assert phone.display("+13125550111") == "+1 312-555-0111"
        assert phone.display(None) == "-"

    def test_mask_shows_only_the_last_four(self):
        masked = phone.mask("+13125550111")
        assert masked.endswith("0111")
        assert "312555" not in masked
