"""Everything the public site says that isn't layout.

One file to edit: the demo conversation, the reviews, the numbers, the FAQ.
Nothing here is decided in a template.

Two rules this file exists to enforce:

1. **The demo is real.** `DEMO_SCRIPT` is a conversation that actually happened
   against this system, captured on 12 November 2026. Replace it only with
   another real capture - a fabricated demo is the one lie a visitor can
   actually catch, because they will try it themselves.

2. **Reviews are off until they are real.** `SHOW_REVIEWS` is False and the
   samples below are marked. Invented testimonials on a commercial site are an
   FTC matter, and this site is also read by carrier reviewers during A2P 10DLC
   vetting. Replace the quotes, set every `sample` to False, flip the flag.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------- the demo

#: Captured 12 November 2026 against the real deployment, seeded demo shop
#: "Westside Auto Repair". Turns 1 and 2 are verbatim. The final reply is
#: verbatim apart from one sentence trimmed for length, which the caption on
#: the page discloses.
DEMO_SHOP = "Westside Auto Repair"
DEMO_CAPTION = "A real conversation with a demo shop. Trimmed for length."

DEMO_SCRIPT: list[dict[str, Any]] = [
    {
        "from": "customer",
        "text": "Hola, cuánto cuesta un cambio de aceite?",
        "delay": 600,
    },
    {
        "from": "chichi",
        "text": (
            "Hola! Un cambio de aceite sintético en Westside Auto Repair "
            "cuesta entre $70 y $110 aproximadamente. Son solo estimados; si "
            "quiere un precio exacto, puede pasar por el taller y lo "
            "revisamos. ¿Le gustaría que le ayude a agendar una visita?"
        ),
        "typing": 1500,
        "stamp": "3.2s",
    },
    {
        "from": "customer",
        "text": "Perfecto. Mi Civic 2014 también necesita frenos, puedo llevarlo mañana?",
        "delay": 1400,
    },
    {
        "from": "chichi",
        "text": (
            "Sí, tenemos lugar mañana viernes 13 de noviembre. Le puedo "
            "ofrecer estos horarios: 8:00 am, 8:30 am o 9:00 am. Para frenos, "
            "el precio de pastillas y discos delanteros suele estar entre "
            "$320 y $480 (es un estimado; el precio exacto lo confirmamos al "
            "revisar el carro). ¿Cuál horario le funciona mejor?"
        ),
        "typing": 1800,
        "stamp": "4.2s",
    },
    {
        "from": "customer",
        "text": "La primera opción está bien",
        "delay": 1200,
    },
    {
        "from": "chichi",
        "text": (
            "Listo, quedó agendado su Civic 2014 para frenos el viernes 13 de "
            "noviembre a las 8:00 am. El precio de pastillas y discos "
            "delanteros es un estimado entre $320 y $480, y lo confirmamos al "
            "revisar el carro. ¿Me podría dar su nombre para anotarlo en la "
            "cita?"
        ),
        "typing": 2000,
        "stamp": "6.6s",
        "booked": True,
    },
]

#: What the owner's phone shows while that conversation happens. Real alerts,
#: in the format `core/owner.py` actually sends.
OWNER_ALERT = {
    "code": "2F2K",
    "text": (
        "New booking [2F2K]\n"
        "Civic 2014, front brakes\n"
        "Friday 13 Nov, 8:00 AM\n"
        "Reply OK to confirm, or CHANGE if that will not work."
    ),
}

# ------------------------------------------------------------------- the numbers

#: Every figure measured on the real deployment during the build. If you cannot
#: point at where a number came from, it does not belong on the page.
METRICS: list[dict[str, str]] = [
    {
        "value": "3.2",
        "unit": "s",
        "label": "to the first reply",
        "note": "measured, 12 Nov 2026",
    },
    {
        "value": "24",
        "unit": "/7",
        "label": "including 2am on a Sunday",
        "note": "no rota, no voicemail",
    },
    {
        "value": "$0.0004",
        "unit": "",
        "label": "what a reply costs us",
        "note": "which is why it is flat-rate for you",
    },
    {
        "value": "any",
        "unit": "",
        "label": "language they write in",
        "note": "answered in the same one, first try",
    },
]

# --------------------------------------------------------------------- languages

#: The same question, as customers actually send it. Cycled in the hero.
LANGUAGE_DEMO: list[dict[str, str]] = [
    {"code": "es", "label": "Spanish", "text": "¿A qué hora cierran hoy?"},
    {"code": "en", "label": "English", "text": "What time do you close today?"},
    {"code": "pl", "label": "Polish", "text": "O której dzisiaj zamykacie?"},
    {"code": "zh", "label": "Chinese", "text": "你们今天几点关门？"},
    {"code": "ar", "label": "Arabic", "text": "متى تغلقون اليوم؟"},
    {"code": "uk", "label": "Ukrainian", "text": "О котрій ви сьогодні зачиняєтесь?"},
]

# ----------------------------------------------------------------------- reviews

#: OFF until the quotes are real. See the module docstring.
SHOW_REVIEWS = False

#: Sample copy, written to show the layout. Every one is marked `sample`, and
#: the section refuses to render as genuine while any of them still are.
REVIEWS: list[dict[str, Any]] = [
    {
        "quote": (
            "I used to lose the whole morning to the phone. Now I look up at "
            "lunch and there are three jobs booked that I never touched."
        ),
        "name": "Sample Name",
        "role": "Owner, sample auto shop",
        "trade": "Auto repair",
        "sample": True,
    },
    {
        "quote": (
            "Half my customers write in Spanish. It answers them properly, "
            "first time, and I don't have to be the translator any more."
        ),
        "name": "Sample Name",
        "role": "Owner, sample tyre shop",
        "trade": "Tyres",
        "sample": True,
    },
    {
        "quote": (
            "The two weeks where it just shows you what it would have said - "
            "that's what sold me. I corrected it maybe four times."
        ),
        "name": "Sample Name",
        "role": "Owner, sample grooming salon",
        "trade": "Pet grooming",
        "sample": True,
    },
    {
        "quote": (
            "Missed calls were costing me more than I wanted to admit. Now "
            "they get a text before they've put the phone down."
        ),
        "name": "Sample Name",
        "role": "Owner, sample plumbing company",
        "trade": "Plumbing",
        "sample": True,
    },
    {
        "quote": (
            "No-shows dropped off a cliff once the reminders started going "
            "out. That alone paid for it."
        ),
        "name": "Sample Name",
        "role": "Owner, sample barbershop",
        "trade": "Barber",
        "sample": True,
    },
    {
        "quote": (
            "It knows when to stop, which is the bit I was worried about. "
            "Anything awkward comes straight to my phone."
        ),
        "name": "Sample Name",
        "role": "Owner, sample repair shop",
        "trade": "Device repair",
        "sample": True,
    },
]


def reviews_are_samples() -> bool:
    return any(review.get("sample") for review in REVIEWS)


def visible_reviews() -> list[dict[str, Any]]:
    """Nothing renders publicly while the quotes are invented."""
    if not SHOW_REVIEWS:
        return []
    return REVIEWS


# --------------------------------------------------------------------- features

FEATURES: list[dict[str, str]] = [
    {
        "icon": "languages",
        "title": "Answers in their language",
        "body": (
            "Spanish, Polish, English - whatever they wrote in, answered in "
            "the same one on the first reply. No menus, no 'press 1'."
        ),
    },
    {
        "icon": "phone-missed",
        "title": "Texts back missed calls",
        "body": (
            "A call rings out and the caller has a text within seconds - "
            "before they try the next business on the list."
        ),
    },
    {
        "icon": "calendar",
        "title": "Books real appointments",
        "body": (
            "Only times genuinely free on your calendar. It never holds or "
            "promises a slot it has not checked."
        ),
    },
    {
        "icon": "bell",
        "title": "Reminds them the day before",
        "body": (
            "In their language, with the time you actually agreed. Fewer "
            "no-shows, and not one phone call from you."
        ),
    },
    {
        "icon": "check",
        "title": "Answers 'is it ready yet?'",
        "body": (
            "You text READY once - a car, a dog, a laptop, a suit. They are "
            "told the moment they ask."
        ),
    },
    {
        "icon": "hand",
        "title": "Knows when to stop",
        "body": (
            "It hands over and goes quiet. It will never argue with an angry "
            "customer or quote a price you did not give it."
        ),
    },
    {
        "icon": "sunrise",
        "title": "One morning summary",
        "body": (
            "Yesterday's numbers, today's appointments, what is waiting on "
            "you. One message, your language, your hour."
        ),
    },
    {
        "icon": "hash",
        "title": "Runs on your own number",
        "body": (
            "The number on your signs and your van. Your customers never need "
            "to know anything changed."
        ),
    },
]

# -------------------------------------------------------------------------- faq

FAQ: list[dict[str, str]] = [
    {
        "q": "Does it pretend to be a person?",
        "a": (
            "No. It says it is your assistant, and it hands over to you the "
            "moment someone asks for a human."
        ),
    },
    {
        "q": "What if it doesn't know something?",
        "a": (
            "It says so and passes the question to you. It can only speak "
            "from what you told us - it cannot invent a price, a time or a "
            "promise. That is enforced, not hoped for."
        ),
    },
    {
        "q": "Do I need a computer?",
        "a": (
            "No. Everything reaches you on WhatsApp and you reply there. "
            "There is a dashboard if you want one, but you never have to open "
            "it."
        ),
    },
    {
        "q": "Can I take over a conversation?",
        "a": (
            "Yes. Reply to the alert and it goes quiet on that chat until you "
            "hand it back. Whatever you type is sent on, translated into the "
            "customer's language."
        ),
    },
    {
        "q": "What if I don't like what it says?",
        "a": (
            "That is what the first two weeks are for. It drafts, you approve "
            "or rewrite, and we tune it to sound like you before a single "
            "customer sees a word."
        ),
    },
    {
        "q": "Do I have to change my phone number?",
        "a": "No. It works on the number you already have.",
    },
    {
        "q": "How do I stop it?",
        "a": (
            "Tell us and we switch it off the same day. There is nothing to "
            "uninstall and no notice period to sit through."
        ),
    },
]

TRADES: list[str] = [
    "Auto repair",
    "Tyres & brakes",
    "Salons",
    "Barbers",
    "Pet grooming",
    "Plumbers",
    "Electricians",
    "HVAC",
    "Phone repair",
    "Appliance repair",
    "Tailors",
    "Dry cleaners",
    "Body shops",
    "Nail studios",
    "Garden services",
    "Locksmiths",
]
