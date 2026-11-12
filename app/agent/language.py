"""Language selection.

The system never asks a customer to pick a language. For an actual customer
message, Claude detects the language in the same call that writes the reply. This
module covers the two things Claude cannot do:

  * the fallback order when the latest message is too short or neutral to tell
  * composing a first greeting when there is no customer message yet

Resolution order, per the architecture doc:
  1. the customer's latest message (Claude's `detected_language`, if confident)
  2. the customer's stored language
  3. the phone number's country code
  4. the shop's market default
"""

from __future__ import annotations

from dataclasses import dataclass

# Longest-prefix wins, so "+593" is matched before "+59" would be.
# English is always present: a country code is a hint, not a fact.
COUNTRY_GREETING_LANGUAGES: dict[str, list[str]] = {
    # North America - English, plus Spanish when the shop enables it.
    "+1": ["en"],
    # UK and Ireland
    "+44": ["en"],
    "+353": ["en"],
    # Spain, Mexico and Latin America
    "+34": ["en", "es"],
    "+52": ["en", "es"],
    "+51": ["en", "es"],
    "+53": ["en", "es"],
    "+54": ["en", "es"],
    "+56": ["en", "es"],
    "+57": ["en", "es"],
    "+58": ["en", "es"],
    "+502": ["en", "es"],
    "+503": ["en", "es"],
    "+504": ["en", "es"],
    "+505": ["en", "es"],
    "+506": ["en", "es"],
    "+507": ["en", "es"],
    "+591": ["en", "es"],
    "+593": ["en", "es"],
    "+595": ["en", "es"],
    "+598": ["en", "es"],
    # Germany and Austria
    "+49": ["en", "de"],
    "+43": ["en", "de"],
    # France
    "+33": ["en", "fr"],
    # Portugal and Brazil
    "+351": ["en", "pt"],
    "+55": ["en", "pt"],
    # Poland
    "+48": ["en", "pl"],
    # Netherlands
    "+31": ["en", "nl"],
    # Italy
    "+39": ["en", "it"],
}

#: "the country's main language" for codes outside the table above - used only
#: when the shop has that language enabled.
COUNTRY_MAIN_LANGUAGE: dict[str, str] = {
    "+7": "ru",
    "+20": "ar",
    "+30": "el",
    "+32": "nl",
    "+36": "hu",
    "+40": "ro",
    "+41": "de",
    "+45": "da",
    "+46": "sv",
    "+47": "no",
    "+66": "th",
    "+81": "ja",
    "+82": "ko",
    "+84": "vi",
    "+86": "zh",
    "+90": "tr",
    "+91": "hi",
    "+92": "ur",
    "+212": "ar",
    "+213": "ar",
    "+234": "en",
    "+380": "uk",
    "+420": "cs",
    "+421": "sk",
    "+963": "ar",
    "+966": "ar",
    "+971": "ar",
    "+972": "he",
}

LANGUAGE_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sv": "Swedish",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh": "Chinese",
}

UNDETERMINED = "und"


class Source:
    DETECTED = "detected"
    STORED = "stored"
    COUNTRY_CODE = "country_code"
    SHOP_DEFAULT = "shop_default"


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    """`primary` is the language to answer in; `greeting` may list two."""

    primary: str
    greeting: tuple[str, ...]
    source: str


def normalise(code: str | None) -> str | None:
    """'es-MX' -> 'es'. Returns None for empty or undetermined input."""
    if not code:
        return None
    code = code.strip().lower().replace("_", "-")
    if not code or code in {UNDETERMINED, "unknown"}:
        return None
    return code.split("-")[0]


def country_languages(
    phone: str,
    *,
    overrides: dict[str, list[str]] | None = None,
    enabled: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Greeting languages inferred from a number's country code.

    A shop override for the matched prefix replaces the default outright - that is
    how a Cicero shop puts Spanish first on every +1 number.
    """
    phone = (phone or "").strip()
    if not phone.startswith("+"):
        phone = f"+{phone.lstrip('+')}"

    overrides = overrides or {}
    enabled_set = {normalise(code) for code in enabled if normalise(code)}

    # Longest matching prefix wins, overrides considered alongside the table.
    candidates = sorted(
        set(overrides) | set(COUNTRY_GREETING_LANGUAGES) | set(COUNTRY_MAIN_LANGUAGE),
        key=len,
        reverse=True,
    )
    match = next((p for p in candidates if phone.startswith(p)), None)

    if match is None:
        # The country code adds nothing - the caller falls through to the shop's
        # market default rather than guessing English here.
        return []

    if match in overrides:
        chosen = [normalise(c) for c in overrides[match]]
        return [c for c in chosen if c] or ["en"]

    if match in COUNTRY_GREETING_LANGUAGES:
        langs = list(COUNTRY_GREETING_LANGUAGES[match])
    else:
        # "Any other code: English, plus the country's main language if the shop
        # has enabled it."
        main = COUNTRY_MAIN_LANGUAGE.get(match)
        langs = ["en"]
        if main and main in enabled_set and main != "en":
            langs.append(main)

    # A +1 number gets Spanish too when the shop turned it on.
    if match == "+1" and "es" in enabled_set:
        langs.append("es")

    deduped: list[str] = []
    for code in langs:
        if code and code not in deduped:
            deduped.append(code)
    return deduped or ["en"]


def resolve(
    *,
    phone: str,
    detected: str | None = None,
    detected_confidence: float = 0.0,
    stored: str | None = None,
    enabled: tuple[str, ...] | list[str] = (),
    default_customer_language: str = "en",
    overrides: dict[str, list[str]] | None = None,
    confidence_threshold: float = 0.75,
) -> LanguageDecision:
    """The four-step order. `greeting` is what a no-message-yet opener uses."""
    detected_code = normalise(detected)
    stored_code = normalise(stored)

    # 1. A confident detection on the latest message wins.
    if detected_code and detected_confidence >= confidence_threshold:
        return LanguageDecision(
            primary=detected_code,
            greeting=(detected_code,),
            source=Source.DETECTED,
        )

    # 2. The stored language, for short or neutral messages.
    if stored_code:
        return LanguageDecision(
            primary=stored_code, greeting=(stored_code,), source=Source.STORED
        )

    # 3. The phone number's country code.
    from_country = country_languages(phone, overrides=overrides, enabled=enabled)
    if from_country:
        return LanguageDecision(
            primary=from_country[0],
            greeting=tuple(from_country),
            source=Source.COUNTRY_CODE,
        )

    # 4. The shop's market default.
    fallback = normalise(default_customer_language) or "en"
    return LanguageDecision(
        primary=fallback, greeting=(fallback,), source=Source.SHOP_DEFAULT
    )


def language_name(code: str) -> str:
    code = normalise(code) or "en"
    return LANGUAGE_NAMES.get(code, code.upper())


def is_enabled(code: str, enabled: tuple[str, ...] | list[str]) -> bool:
    target = normalise(code)
    return bool(target) and target in {
        normalise(c) for c in enabled if normalise(c)
    }


def missed_call_instruction(decision: LanguageDecision, shop_name: str) -> str:
    """Opener for a call nobody answered.

    Same language rules as a first greeting, but it has to acknowledge the missed
    call and invite a reply - this is the message that recovers the job.
    """
    names = _greeting_names(decision)
    joined = " and ".join(names) if len(names) <= 2 else ", ".join(names)
    stored = decision.source in {Source.DETECTED, Source.STORED}
    if stored:
        return (
            f"This customer just called {shop_name} and nobody picked up. They "
            f"have not written anything. Send a short text in "
            f"{language_name(decision.primary)}: say you are sorry you missed "
            f"their call, that this is {shop_name}, and ask what they need - "
            f"you can help right here by text. Two sentences at most. No menu, "
            f"no numbered options."
        )
    return (
        f"This is a new number that just called {shop_name} and nobody picked "
        f"up. They have not written anything, so you cannot know their "
        f"language. Send a short text in {joined} - one line per language: say "
        f"you are sorry you missed their call, that this is {shop_name}, and "
        f"ask what they need. Then add one final short line telling them they "
        f"can reply in any language. That final line is required: include it "
        f"even though it makes the message longer than the usual style limit. "
        f"No menu, no numbered options."
    )


def _greeting_names(decision: LanguageDecision) -> list[str]:
    names = [language_name(code) for code in decision.greeting]
    if "English" not in names:
        names.insert(0, "English")
    return names


def greeting_instruction(decision: LanguageDecision, shop_name: str) -> str:
    """Instruction for an opener where the customer has not written yet.

    Always includes English, always ends with the any-language line, and never
    offers a menu - the doc's three standing rules for first contact.
    """
    names = _greeting_names(decision)
    joined = " and ".join(names) if len(names) <= 2 else ", ".join(names)
    return (
        f"This is the first contact and the customer has not written anything yet. "
        f"Greet them on behalf of {shop_name} in {joined} - one short line per "
        f"language, nothing more. Ask how you can help. End with a single short "
        f"line telling them they can reply in any language. Do not offer a menu, "
        f"numbered options, or ask them to choose a language."
    )
