"""The options on the public signup form, and the cleaning of what comes back.

Separate from `app/web/marketing.py` for two reasons.

The first is that these lists are *derived*, not typed out. The niches come from
`app.agent.niche.NICHES` and the languages from the union of what those niches
say their market writes in, so adding a fourth niche adds a form option and its
languages without anyone remembering to. A form that offers a niche the agent
has no template for is a signup that cannot be converted.

The second is testability. Everything a submission has to survive is a pure
function of strings here, which means the validation can be tested without a
database, an HTTP client or a session - and the handful of tests that do go
through the route are then about the route, not about whether "ES " is a
country.

Every cleaner follows the same contract: it never raises, it never echoes its
input back, and it returns `None` or `[]` for anything it does not recognise.
A stranger controls every byte that reaches these functions.
"""

from __future__ import annotations

from app.agent.language import LANGUAGE_NAMES
from app.agent.niche import NICHES

#: The niches the form offers, in the order they are shown. Only the regulated
#: ones: the six US trade presets still exist and still work, but they are not
#: what this page is selling, and listing nine options to a clinic is worse than
#: listing three and an "other".
NICHE_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (key, spec.label) for key, spec in NICHES.items() if spec.regulated
) + (("other", "Something else"),)

NICHE_KEYS: frozenset[str] = frozenset(key for key, _ in NICHE_CHOICES)

#: Where the business is, which is not the same question as where its customers
#: are from - that is what `languages` is for. Spain and Portugal lead because
#: they are the market; the rest are places the same pitch works.
#:
#: Short, and the field is therefore *not* required on the form. A list of two
#: hundred countries is a worse question, and a required list of eight is a
#: closed door to the ninth - a clinic in Germany would have had nothing to
#: pick and no way to send the form. Blank is a legitimate answer: the only
#: thing the country buys us is reading a number typed without a country code,
#: and the form asks for the country code anyway.
COUNTRY_CHOICES: tuple[tuple[str, str], ...] = (
    ("ES", "Spain"),
    ("PT", "Portugal"),
    ("FR", "France"),
    ("IT", "Italy"),
    ("GR", "Greece"),
    ("GB", "United Kingdom"),
    ("IE", "Ireland"),
    ("US", "United States"),
)

COUNTRY_CODES: frozenset[str] = frozenset(code for code, _ in COUNTRY_CHOICES)

#: Country to timezone, for prefilling the console when a signup is converted.
#: One zone per country, which is wrong for France and the US and right for the
#: two countries that matter - and the console's own dropdown is still there to
#: correct it. Guessing badly in an editable field beats making someone scroll
#: a list of six hundred zones.
TIMEZONE_BY_COUNTRY: dict[str, str] = {
    "ES": "Europe/Madrid",
    "PT": "Europe/Lisbon",
    "FR": "Europe/Paris",
    "IT": "Europe/Rome",
    "GR": "Europe/Athens",
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "US": "America/Chicago",
}

#: Spanish first, English second, then the foreign cohorts in roughly the order
#: a Costa del Sol clinic and an extranjeria firm meet them. Built from the
#: niches so it cannot drift from what the agent is actually configured to
#: handle, then ordered deliberately: a checkbox list sorted alphabetically puts
#: Arabic above Spanish on a Spanish form.
_LANGUAGE_ORDER: tuple[str, ...] = (
    "es",
    "en",
    "de",
    "nl",
    "fr",
    "sv",
    "no",
    "ru",
    "uk",
    "ar",
    "pt",
    "zh",
    "ur",
)

_MARKET_LANGUAGES: frozenset[str] = frozenset(
    code
    for spec in NICHES.values()
    if spec.regulated
    for code in spec.market_languages
)

LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (code, LANGUAGE_NAMES[code])
    for code in _LANGUAGE_ORDER
    if code in _MARKET_LANGUAGES and code in LANGUAGE_NAMES
) + tuple(
    # Anything a niche declares that the order above forgot, so the list is
    # always the whole market rather than silently the part someone listed.
    (code, LANGUAGE_NAMES[code])
    for code in sorted(_MARKET_LANGUAGES - set(_LANGUAGE_ORDER))
    if code in LANGUAGE_NAMES
)

LANGUAGE_CODES: frozenset[str] = frozenset(code for code, _ in LANGUAGE_CHOICES)

#: What we would have to talk to. Named rather than free text because the answer
#: decides whether onboarding is an afternoon or a fortnight, and "our system"
#: typed into a box tells us nothing.
CRM_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "Not sure / nothing yet"),
    ("doctoralia", "Doctoralia"),
    ("gesden", "Gesden"),
    ("clinic_cloud", "Clinic Cloud"),
    ("inmovilla", "Inmovilla"),
    ("resales_online", "Resales Online"),
    ("google_calendar", "Google Calendar"),
    ("other", "Something else"),
)

CRM_KEYS: frozenset[str] = frozenset(key for key, _ in CRM_CHOICES if key)


def clean_niche(raw: str | None) -> str | None:
    """A known niche key, or None. Never the raw string."""
    key = (raw or "").strip().lower()
    return key if key in NICHE_KEYS else None


def clean_country(raw: str | None) -> str | None:
    code = (raw or "").strip().upper()
    return code if code in COUNTRY_CODES else None


def clean_languages(raw: list[str] | None) -> list[str]:
    """The submitted codes, filtered to the allowlist and put back in the
    form's own order.

    Order matters downstream: `enabled_languages` is a priority list, and the
    browser sends checkboxes in document order anyway - but a hand-rolled POST
    does not, and the shop should not end up answering in Urdu first because
    someone shuffled the body.
    """
    sent = {(code or "").strip().lower() for code in (raw or [])}
    return [code for code, _ in LANGUAGE_CHOICES if code in sent]


def clean_crm(raw: str | None) -> str | None:
    key = (raw or "").strip().lower()
    return key if key in CRM_KEYS else None


def clean_website(raw: str | None) -> str | None:
    """A plausible http(s) URL, or None.

    Most people type `clinicadental.es` with no scheme, so one is added. The
    scheme check is not cosmetic: this string is rendered as a link on the
    console's signups page, and `javascript:` in an href is a stored XSS on a
    page an admin is logged into.
    """
    text = (raw or "").strip()
    if not text or " " in text or "." not in text:
        return None
    lowered = text.lower()
    if not lowered.startswith(("http://", "https://")):
        if ":" in text.split("/", 1)[0]:
            # Some other scheme, or a port on a bare host. Either way, not
            # something to guess at.
            return None
        text = "https://" + text
    return text[:300]


def region_for(country: str | None, fallback: str) -> str:
    """The region a number typed without a country code is parsed in.

    `fallback` is the configured default rather than a literal, so a
    deployment selling into Portugal is an environment variable.
    """
    return country or fallback


def timezone_for(country: str | None, fallback: str) -> str:
    return TIMEZONE_BY_COUNTRY.get(country or "", fallback)
