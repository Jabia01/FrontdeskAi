"""The signup form's option lists and its cleaners.

No database, no HTTP client, no session. Everything a stranger's submission has
to survive is a pure function of strings, so it is tested as one - which leaves
`test_web_marketing.py` free to be about the route rather than about whether
"ES " is a country.
"""

from __future__ import annotations

from app.agent.language import LANGUAGE_NAMES
from app.agent.niche import NICHES
from app.web import signup_form as sf


class TestTheOptionsAreDerived:
    """The lists are built from the niche specs. A form that offers a niche the
    agent has no template for is a signup nobody can convert."""

    def test_every_regulated_niche_is_offered(self):
        regulated = {k for k, spec in NICHES.items() if spec.regulated}
        assert regulated <= sf.NICHE_KEYS
        assert regulated == {"dental", "real_estate", "immigration"}

    def test_the_us_trade_presets_are_not_offered(self):
        """They still exist and still work. They are not what this page sells,
        and nine options is worse than three and an "other"."""
        assert "auto_repair" not in sf.NICHE_KEYS
        assert "other" in sf.NICHE_KEYS

    def test_a_niche_label_comes_from_the_spec(self):
        labels = dict(sf.NICHE_CHOICES)
        assert labels["dental"] == NICHES["dental"].label

    def test_every_offered_language_is_a_language_the_agent_knows(self):
        for code, name in sf.LANGUAGE_CHOICES:
            assert LANGUAGE_NAMES[code] == name

    def test_the_offered_languages_are_exactly_the_market(self):
        market = {
            code
            for spec in NICHES.values()
            if spec.regulated
            for code in spec.market_languages
        }
        assert set(sf.LANGUAGE_CODES) == market

    def test_spanish_and_english_come_first(self):
        """A checkbox list sorted alphabetically puts Arabic above Spanish on a
        Spanish form."""
        assert [code for code, _ in sf.LANGUAGE_CHOICES][:2] == ["es", "en"]

    def test_spain_and_portugal_lead_the_countries(self):
        assert [code for code, _ in sf.COUNTRY_CHOICES][:2] == ["ES", "PT"]

    def test_every_country_has_a_timezone(self):
        """Otherwise picking one silently prefills the console with the
        default, which for a Greek clinic is Madrid."""
        for code, _ in sf.COUNTRY_CHOICES:
            assert code in sf.TIMEZONE_BY_COUNTRY

    def test_the_crm_list_has_a_blank_first_option(self):
        """"Nothing yet" is the honest answer for most of them, and it has to
        be the one already selected or the form implies otherwise."""
        assert sf.CRM_CHOICES[0][0] == ""


class TestCleanNiche:
    def test_a_known_key_passes(self):
        assert sf.clean_niche("dental") == "dental"

    def test_case_and_space_are_forgiven(self):
        assert sf.clean_niche("  Dental ") == "dental"

    def test_an_unknown_key_is_none_not_an_error(self):
        assert sf.clean_niche("wizardry") is None

    def test_a_us_preset_is_not_accepted_through_the_public_form(self):
        assert sf.clean_niche("auto_repair") is None

    def test_nothing_is_none(self):
        assert sf.clean_niche("") is None
        assert sf.clean_niche(None) is None


class TestCleanCountry:
    def test_it_upper_cases(self):
        assert sf.clean_country("es") == "ES"

    def test_an_unoffered_country_is_none(self):
        """Blank is a legitimate answer, so the field is not required on the
        form - a business in a ninth country leaves it and types their number
        in full. What must not happen is an unvetted two-letter string
        reaching `phonenumbers.parse` as a region."""
        assert sf.clean_country("ZZ") is None
        assert sf.clean_country("DE") is None

    def test_a_country_name_is_not_a_code(self):
        assert sf.clean_country("Spain") is None

    def test_nothing_is_none(self):
        assert sf.clean_country("") is None
        assert sf.clean_country(None) is None


class TestCleanLanguages:
    def test_it_filters_to_the_allowlist(self):
        assert sf.clean_languages(["es", "klingon"]) == ["es"]

    def test_a_language_the_agent_knows_but_the_form_does_not_offer_is_dropped(
        self,
    ):
        """The allowlist is the form's list, not `LANGUAGE_NAMES`."""
        assert "ja" in LANGUAGE_NAMES
        assert sf.clean_languages(["ja"]) == []

    def test_the_result_is_in_the_forms_order_not_the_bodys(self):
        """`enabled_languages` is a priority list. A hand-rolled POST should not
        be able to make a Marbella clinic answer in Urdu first."""
        assert sf.clean_languages(["ur", "en", "es"]) == ["es", "en", "ur"]

    def test_duplicates_collapse(self):
        assert sf.clean_languages(["es", "es", "ES"]) == ["es"]

    def test_nothing_is_an_empty_list(self):
        assert sf.clean_languages([]) == []
        assert sf.clean_languages(None) == []


class TestCleanWebsite:
    def test_a_bare_host_gets_https(self):
        assert sf.clean_website("clinicadental.es") == "https://clinicadental.es"

    def test_an_existing_scheme_is_left_alone(self):
        assert sf.clean_website("http://x.es/a") == "http://x.es/a"
        assert sf.clean_website("HTTPS://X.ES") == "HTTPS://X.ES"

    def test_a_javascript_url_is_refused(self):
        """This string is rendered inside an href on a page an admin is logged
        into, which makes a bad scheme a stored XSS and not a typo."""
        assert sf.clean_website("javascript:alert(1).x") is None
        assert sf.clean_website("data:text/html;base64,x.y") is None

    def test_something_that_is_not_a_host_is_refused(self):
        assert sf.clean_website("our website") is None
        assert sf.clean_website("ask me") is None
        assert sf.clean_website("localhost") is None

    def test_it_is_capped(self):
        assert len(sf.clean_website("a." + "b" * 500)) == 300

    def test_nothing_is_none(self):
        assert sf.clean_website("  ") is None
        assert sf.clean_website(None) is None


class TestResolution:
    def test_a_country_decides_the_phone_region(self):
        assert sf.region_for("ES", "US") == "ES"

    def test_no_country_falls_back_to_the_configured_default(self):
        """The fallback is passed in rather than read here, so a deployment
        selling into Portugal is an environment variable."""
        assert sf.region_for(None, "PT") == "PT"

    def test_a_country_decides_the_timezone(self):
        assert sf.timezone_for("PT", "Europe/Madrid") == "Europe/Lisbon"

    def test_an_unknown_country_keeps_the_fallback(self):
        assert sf.timezone_for("ZZ", "Europe/Madrid") == "Europe/Madrid"
        assert sf.timezone_for(None, "Europe/Madrid") == "Europe/Madrid"
