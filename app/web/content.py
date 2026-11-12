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
#: "Westside Auto Repair". Four languages, four real conversations - the hero
#: cycles them so nobody can think this is a Spanish-only trick.
#:
#: Every reply is verbatim and every `stamp` is the time that reply actually
#: took. One sentence was trimmed from the end of the Spanish conversation for
#: length, which the caption on the page discloses.
#:
#: To replace these, run a conversation against a real deployment and paste the
#: result. Do not write one by hand: a fabricated demo is the single claim on
#: this site a visitor can disprove in thirty seconds.
DEMO_SHOP = "Westside Auto Repair"
DEMO_CAPTION = (
    "Real conversations, replayed. Captured against a demo shop on "
    "12 November 2026 and trimmed for length."
)

DEMO_CONVERSATIONS: list[dict[str, Any]] = [
    {
        "code": "es",
        "label": "Spanish",
        "accent": "violet",
        "shop": "Westside Auto Repair",
        "turns": [
            {
                "from": "customer",
                "text": "Hola, cuanto cuesta un cambio de aceite?",
            },
            {
                "from": "chichi",
                "text": "Hola! Un cambio de aceite sintético en Westside Auto Repair cuesta entre $70 y $110 aproximadamente. Son solo estimados; si quiere un precio exacto, puede pasar por el taller y lo revisamos. ¿Le gustaría que le ayude a agendar una visita?",
                "stamp": "3.2s",
            },
            {
                "from": "customer",
                "text": "Perfecto. Mi Civic 2014 tambien necesita frenos, puedo llevarlo manana?",
            },
            {
                "from": "chichi",
                "text": "Sí, tenemos lugar mañana viernes 13 de noviembre. Le puedo ofrecer estos horarios: 8:00 am, 8:30 am o 9:00 am. Para frenos, el precio de pastillas y discos delanteros suele estar entre $320 y $480 (es un estimado; el precio exacto lo confirmamos al revisar el carro). ¿Cuál horario le funciona mejor?",
                "stamp": "4.2s",
            },
            {
                "from": "customer",
                "text": "La primera opcion esta bien",
            },
            {
                "from": "chichi",
                "text": "Listo, quedó agendado su Civic 2014 para frenos el viernes 13 de noviembre a las 8:00 am. El precio de pastillas y discos delanteros es un estimado entre $320 y $480, y lo confirmamos al revisar el carro. ¿Me podría dar su nombre para anotarlo en la cita?",
                "stamp": "6.6s",
                "booked": True,
            },
        ],
    },
    {
        "code": "en",
        "label": "English",
        "accent": "teal",
        "shop": "Westside Auto Repair",
        "turns": [
            {
                "from": "customer",
                "text": "hi, how much for front brakes on a 2016 Corolla?",
            },
            {
                "from": "chichi",
                "text": "Hi! For front brake pads and rotors, our estimate is $320 to $480. That's a range, not a firm price, since the exact figure depends on the car and what the brakes need. If you'd like an exact quote, you can drop it by the shop at 2431 W Chicago Ave, or I can check a drop-off time for you. Would you like me to do that?",
                "stamp": "3.2s",
            },
            {
                "from": "customer",
                "text": "ok can i bring it in tomorrow?",
            },
            {
                "from": "chichi",
                "text": "Yes, we have openings tomorrow, Friday. I can offer 8:30 AM, 9:00 AM, or 9:30 AM for a drop-off. Which one works for you?",
                "stamp": "5.6s",
            },
            {
                "from": "customer",
                "text": "the earliest one works",
            },
            {
                "from": "chichi",
                "text": "You're booked for Friday 13 Nov at 8:30 AM to drop off your 2016 Corolla for front brakes. The shop is at 2431 W Chicago Ave, and there's free parking in the lot behind the building, entered from the alley. Could I get your name so the shop knows who to expect?",
                "stamp": "8.9s",
                "booked": True,
            },
        ],
    },
    {
        "code": "pl",
        "label": "Polish",
        "accent": "amber",
        "shop": "Westside Auto Repair",
        "turns": [
            {
                "from": "customer",
                "text": "dzien dobry, ile kosztuje wymiana oleju?",
            },
            {
                "from": "chichi",
                "text": "Dzień dobry! Wymiana oleju syntetycznego kosztuje zwykle od $70 do $110. To tylko orientacyjne szacunki, a dokładną wycenę podamy po wizycie w warsztacie. Czy chce Pan/Pani, żebym sprawdził/a wolne terminy?",
                "stamp": "3.4s",
            },
            {
                "from": "customer",
                "text": "a czy moge przyjechac jutro rano?",
            },
            {
                "from": "chichi",
                "text": "Tak, jutro (piątek) jesteśmy otwarci od 8:00 do 18:00. Wymiana oleju nie wymaga wcześniejszego umówienia, można przyjechać bez zapisu. Czy chce Pan/Pani, żebym sprawdził/a dla Pana/Pani konkretną godzinę na wizytę dla diagnostyki lub hamulców?",
                "stamp": "3.1s",
            },
            {
                "from": "customer",
                "text": "pierwsza godzina pasuje",
            },
            {
                "from": "chichi",
                "text": "Świetnie! Jutro otwieramy o 8:00, więc pierwsza godzina to 8:00. Na wymianę oleju nie trzeba się umawiać, wystarczy przyjechać. Czy chodzi tylko o olej, czy także o diagnostykę lub hamulce? Wtedy sprawdzę dla Pana/Pani konkretny termin.",
                "stamp": "4.2s",
            },
        ],
    },
    {
        "code": "ar",
        "label": "Arabic",
        "accent": "rose",
        "shop": "Westside Auto Repair",
        "rtl": True,
        "turns": [
            {
                "from": "customer",
                "text": "مرحبا، كم سعر تغيير الزيت؟",
            },
            {
                "from": "chichi",
                "text": "مرحبا! تغيير الزيت الصناعي (سينثتيك) يتراوح سعره تقريبا بين 70 و110 دولار. هذه أسعار تقديرية فقط، وإذا أردت سعرا دقيقا يمكنك المرور بالمحل وسنحدد لك السعر بعد الفحص. هل تريد معرفة مواعيد العمل أو حجز موعد؟",
                "stamp": "3.2s",
            },
            {
                "from": "customer",
                "text": "هل يمكنني إحضار السيارة غدا؟",
            },
            {
                "from": "chichi",
                "text": "نعم، يمكنك الحضور غدا الجمعة. المواعيد المتاحة لتغيير الزيت: الساعة 9:00 صباحا، أو 9:30 صباحا، أو 10:00 صباحا. أيها يناسبك؟ وإذا كنت تفضل الحضور بدون موعد فالمحل يستقبل تغيير الزيت بدون حجز أيضا.",
                "stamp": "4.4s",
            },
            {
                "from": "customer",
                "text": "الموعد الأول مناسب",
            },
            {
                "from": "chichi",
                "text": "ممتاز، الجمعة 13 نوفمبر الساعة 9:00 صباحا متاح. قبل تأكيد الحجز، ما اسمك من فضلك؟",
                "stamp": "5.6s",
            },
        ],
    },
]

#: What the owner's phone shows while that conversation happens. Real alerts,
#: in the format `core/owner.py` actually sends.
OWNER_ALERT = {
    "code": "2F2K",
    "title": "New booking [2F2K]",
    "body": (
        "Civic 2014, front brakes\n"
        "Friday 13 Nov, 8:00 AM\n"
        "Reply OK to confirm, or CHANGE if that won't work."
    ),
}


# -------------------------------------------------------------- the headline

#: The stem holds still and the ending rotates, so the sentence stays readable
#: while the claim widens: there is more than one way to lose a customer, and
#: the product answers all of them.
#:
#: The first is the strongest, because plenty of visitors will only ever see
#: one. Keep them close in length - the page reserves the height of the tallest
#: so nothing below ever shifts, but wildly uneven endings leave a visible gap.
HEADLINE_STEM = "Never lose another customer to"
HEADLINE_TAILS: list[str] = [
    "a phone you couldn't reach.",
    "a text you read too late.",
    "a voicemail nobody checks.",
    "the shop that answered first.",
    "a language you don't speak.",
    "the Sunday you took off.",
]

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
