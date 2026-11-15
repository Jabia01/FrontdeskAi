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
    "city": "Madrid",
    "market": "clinics, agencies and law firms in Spain",
    # ---- data protection -------------------------------------------------
    # Selling to a Spanish clinic means handling Art. 9 health data and
    # answering to the AEPD, and selling to an extranjeria firm means handling
    # case files. A GDPR page without a named controller, a contact for data
    # questions and a supervisory authority is not a GDPR page.
    #
    # `eu_representative` is Art. 27, and it is only required of a controller
    # with no establishment in the EU. Which of those we are is precisely the
    # question `legal_name` has not answered yet, so it stays TODO rather than
    # being guessed either way.
    "dpo_email": TODO,               # e.g. "privacy@chichi.ai"
    "supervisory_authority": TODO,   # e.g. "Agencia Espanola de Proteccion de Datos (AEPD)"
    "eu_representative": TODO,       # an Art. 27 representative, if we need one
    "dpa_updated": TODO,             # the version date of the DPA we sign
    # ---- commercial terms ------------------------------------------------
    #: Plans come from config, not from a template. `price` is the rendered
    #: string rather than a symbol glued to a number: Spanish writes "490 €",
    #: with the symbol after the amount and a space in between, and a template
    #: doing `currency ~ price` cannot express that.
    "currency": "€",
    "plans": [
        {
            "key": "starter",
            "name": "Starter",
            "price_monthly": 490,
            "price": "490 €",
            "blurb": "One location, one number, every language.",
        },
        {
            "key": "growth",
            "name": "Growth",
            "price_monthly": 790,
            "price": "790 €",
            "blurb": "More volume, more numbers, priority support.",
        },
    ],
    #: A range, because it depends on how much of the clinic's own material
    #: has to be loaded before it can answer anything.
    "setup_fee": "300-500 €",
    "trial_days": 14,
    #: The IVA position has to be stated somewhere a buyer sees it, and a
    #: terms page that gets it wrong is a contract problem rather than a typo.
    "vat_note": TODO,  # e.g. "Prices exclude 21% IVA."
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
