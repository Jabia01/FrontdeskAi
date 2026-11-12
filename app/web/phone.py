"""Phone number normalisation for the web forms.

Owners type their number however they like - "312 555 0111", "(312) 555-0111",
"+1 312 555 0111". All of those have to resolve to the single E.164 string that
`clients.owner_phone` holds, or the login silently fails for a correct number.
"""

from __future__ import annotations

import phonenumbers

DEFAULT_REGION = "US"


def normalise(raw: str | None, *, region: str = DEFAULT_REGION) -> str | None:
    """E.164, or None when the input is not a valid number.

    `region` is only a fallback for numbers typed without a country code; a
    number starting with + is parsed as given.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = phonenumbers.parse(text, None if text.startswith("+") else region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def display(raw: str | None) -> str:
    """Pretty form for a page. Falls back to the raw string unchanged."""
    if not raw:
        return "-"
    try:
        parsed = phonenumbers.parse(raw, None)
    except phonenumbers.NumberParseException:
        return raw
    return phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )


def mask(raw: str | None) -> str:
    """Last four digits only - for "we sent a code to ...0111"."""
    if not raw:
        return "your number"
    digits = [c for c in raw if c.isdigit()]
    if len(digits) < 4:
        return "your number"
    return "••• " + "".join(digits[-4:])
