"""Who we are, in one place.

Every public page reads from here: the marketing site, the legal pages, the
console header, the owner dashboard. Changing the company name, the support
address or the price is one edit, not a hunt through twenty templates.

**The TODO values are load-bearing.** Twilio's A2P 10DLC campaign registration
requires a public privacy policy and terms URL, and the contact details on those
pages must match what is submitted in the brand registration. A placeholder left
here becomes a rejected campaign, which is why `placeholders()` exists and why
the console shows a warning while any remain.
"""

from __future__ import annotations

from typing import Any

#: Marks a value nobody has filled in yet. Rendered visibly rather than hidden,
#: so a half-finished legal page cannot quietly go live looking finished.
TODO = "[to be completed]"

BRAND: dict[str, Any] = {
    # ---- identity -------------------------------------------------------
    "name": "Chichi",
    "tagline": "The front desk that never misses a customer",
    "description": (
        "Chichi answers your customers' texts and WhatsApp messages in their "
        "own language, books appointments, and tells you what needs you."
    ),
    # ---- the legal entity behind it -------------------------------------
    # These appear on the privacy policy and terms, and must match the A2P
    # 10DLC brand registration exactly.
    "legal_name": TODO,           # e.g. "Chichi Technologies LLC"
    "support_email": TODO,        # e.g. "support@chichi.ai"
    "support_phone": TODO,        # the number a customer can reach a human on
    "address": TODO,              # a real postal address; 10DLC vetting wants one
    "jurisdiction": TODO,         # e.g. "the State of Illinois, United States"
    # ---- where we operate -----------------------------------------------
    "city": "Chicago",
    "market": "small businesses in Chicago",
    # ---- commercial terms ------------------------------------------------
    "price_monthly": 249,
    "currency": "$",
    "trial_days": 14,
    # ---- dates on the legal pages ---------------------------------------
    "legal_updated": "12 November 2026",
}


def placeholders() -> list[str]:
    """Which brand values are still unset.

    The console shows this, and `app/main.py` logs it at startup, because the
    failure mode otherwise is a privacy policy that reads fine until a carrier
    reviewer reaches the contact section.
    """
    return sorted(key for key, value in BRAND.items() if value == TODO)


def get(key: str) -> Any:
    return BRAND.get(key, "")
