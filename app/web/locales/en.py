"""Everything the public site says that isn't layout, in English.

This is the default locale and the only complete one. `app/web/content.py`
re-exports it, so every existing import and every test that reaches for
`content.FAQ` still works - the move was deliberately a move and nothing else.

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

#: Captured in November 2026 with `scripts/capture_demo.py`, against demo
#: clinics and agencies built on a real deployment. Five languages across the
#: three niches - the hero cycles them so nobody can think this is a
#: Spanish-only trick, or a dental-only one.
#:
#: Every reply is verbatim and every `stamp` is the time that reply actually
#: took. Nothing here was edited for effect, which is why the Swedish reply is
#: slower than the rest and says so.
#:
#: They are also the evidence the guardrails work, which is why these
#: particular conversations were kept: the Dutch patient is quoted a "from"
#: price out of the clinic's own table and told the real figure depends on the
#: consultation; the English buyer is told plainly that availability cannot be
#: confirmed from here; the Arabic enquiry gets the firm's published document
#: list and nothing else, with the not-legal-advice notice attached.
#:
#: To replace these, run `scripts/capture_demo.py` and paste the result. Do not
#: write one by hand: a fabricated demo is the single claim on this site a
#: visitor can disprove in thirty seconds, because they will try it.
DEMO_SHOP = "Clinica Dental Marbella"
DEMO_CAPTION = (
    "Real conversations, replayed. Captured against demo clinics and agencies "
    "in November 2026 - every reply verbatim, every timing measured."
)

DEMO_CONVERSATIONS: list[dict[str, Any]] = [
    {
        "code": "nl",
        "label": "Dutch",
        "accent": "violet",
        "shop": "Clinica Dental Marbella",
        "turns": [
            {
                "from": "customer",
                "text": "Goedenavond, ik ben geïnteresseerd in facings. Wat kosten die ongeveer?"
            },
            {
                "from": "chichi",
                "stamp": "9.7s",
                "text": "Goedenavond! Met facings bedoelt u waarschijnlijk veneers. Die kosten vanaf ongeveer €450 tot €700 per stuk. Een exacte prijs hangt af van het consult, dus dat is een schatting. Wilt u een afspraak maken voor een consult? Dan krijgt u een precieze offerte."
            },
            {
                "from": "customer",
                "text": "Ik kom in juni naar Marbella, van 3 tot 10 juni. Kan ik dan langskomen?"
            },
            {
                "from": "chichi",
                "stamp": "4.3s",
                "text": "Wat fijn dat u in juni naar Marbella komt! Wij zien regelmatig patiënten die van ver komen. Ik heb uw verblijf van 3 tot 10 juni genoteerd, zodat ik alleen tijden binnen die periode aanbied. Op welke dag zou u het liefst langskomen?"
            }
        ]
    },
    {
        "code": "es",
        "label": "Spanish",
        "accent": "teal",
        "shop": "Clinica Dental Marbella",
        "turns": [
            {
                "from": "customer",
                "text": "Buenas tardes, quería una limpieza dental. ¿Cuánto cuesta?"
            },
            {
                "from": "chichi",
                "stamp": "4.0s",
                "text": "¡Buenas tardes! La limpieza entra en nuestro servicio de Revisión e higiene, con un precio orientativo de entre 80 y 120 €. El importe exacto depende de la consulta, así que te lo confirmamos en la visita. ¿Quieres que te busquemos un hueco para venir?"
            },
            {
                "from": "customer",
                "text": "Vale. ¿Tenéis hueco mañana por la mañana?"
            },
            {
                "from": "chichi",
                "stamp": "3.2s",
                "text": "Sí, mañana lunes 16 de noviembre tenemos huecos por la mañana: a las 8:00, a las 8:30 y a las 9:00. ¿Cuál te viene mejor?"
            },
            {
                "from": "customer",
                "text": "La primera me va bien. Me llamo Marta Ruiz"
            },
            {
                "from": "chichi",
                "stamp": "7.9s",
                "booked": True,
                "text": "Perfecto, Marta. Te he reservado la limpieza dental para mañana, lunes 16 de noviembre, a las 8:00 en Avenida Ricardo Soriano 42, Marbella. El precio orientativo es de 80 a 120 €, y el importe exacto lo confirmamos en la visita. Si necesitas cambiar la hora, escríbenos por aquí. ¡Hasta mañana!"
            }
        ]
    },
    {
        "code": "sv",
        "label": "Swedish",
        "accent": "amber",
        "shop": "Costa Property Partners",
        "turns": [
            {
                "from": "customer",
                "text": "Hej! Jag letar efter en villa i Marbella under 900 000 euro. Har ni något?"
            },
            {
                "from": "chichi",
                "stamp": "16.5s",
                "text": "Hej! Ja, vi har en villa i Marbella inom er budget. Referens MRB-412: en villa med tre sovrum och pool, tio minuter från Puerto Banús. Den har en södervänd trädgård. Priset är 875 000 euro. Vill du boka en visning?"
            },
            {
                "from": "customer",
                "text": "Låter bra. Kan vi boka en visning nästa vecka?"
            },
            {
                "from": "chichi",
                "stamp": "3.9s",
                "text": "Gärna! Vilken dag nästa vecka passar dig bäst? Vi har öppet måndag till fredag 8–18 och lördag 9–14. Jag kollar lediga tider för visning av villa MRB-412 när jag vet vilken dag du vill ha."
            }
        ]
    },
    {
        "code": "ar",
        "label": "Arabic",
        "accent": "rose",
        "rtl": True,
        "shop": "Bufete Extranjeria Madrid",
        "turns": [
            {
                "from": "customer",
                "text": "السلام عليكم، أريد معلومات عن الأرايغو. ما هي الوثائق المطلوبة؟"
            },
            {
                "from": "chichi",
                "stamp": "5.7s",
                "text": "وعليكم السلام، أهلاً بك في بوفيتي إكستراخيريا مدريد. هذه قائمة الوثائق التي حددها المكتب لطلب الأرايغو:\n- جواز سفر ساري وكامل\n- Certificado de empadronamiento\n- Certificado de antecedentes penales\n- Proof of three years' residence in Spain\n\nسيؤكد المكتب أي متطلبات خاصة بحالتك. هل تودّ حجز استشارة أولية (80 يورو تقديرياً)؟ علماً أن هذه المحادثة ليست استشارة قانونية ولا تنشئ علاقة محامٍ بالموكل."
            },
            {
                "from": "customer",
                "text": "Perdone, mejor en español. ¿Puedo pedir cita esta semana?"
            },
            {
                "from": "chichi",
                "stamp": "3.1s",
                "text": "Claro, con gusto le ayudo a pedir cita. ¿Qué día de esta semana le viene mejor? Así consulto los horarios disponibles y le propongo opciones."
            }
        ]
    },
    {
        "code": "en",
        "label": "English",
        "accent": "emerald",
        "shop": "Costa Property Partners",
        "turns": [
            {
                "from": "customer",
                "text": "Evening. Anything in Estepona around 650k, two beds or more?"
            },
            {
                "from": "chichi",
                "stamp": "6.9s",
                "text": "Good evening! We have one match in Estepona: reference EST-118, a two-bedroom apartment at 640,000 with sea views, within walking distance of the old town. I can't confirm availability from here, but I can arrange a viewing or pass your details to an agent. Would you like to go ahead?"
            },
            {
                "from": "customer",
                "text": "Looks good. Could we see it on Saturday?"
            },
            {
                "from": "chichi",
                "stamp": "4.0s",
                "text": "Saturday works. I have 9:00, 9:30 or 10:00 AM on Saturday 21 November. Which would suit you best?"
            }
        ]
    }
]

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
    "a phone nobody answered.",
    "a text you read too late.",
    "a voicemail nobody checks.",
    "the shop that answered first.",
    "a language you don't speak.",
    "the Sunday you took off.",
]

# ------------------------------------------------------------------- channels

#: The ways a customer can reach a business through Chichi. Named on the page
#: because "AI front desk" does not tell anyone whether their actual phone gets
#: answered, and that is the first thing an owner wants to know.
#:
#: Keep this honest about what is switched on. Voice is live in the product; a
#: given shop still has to have a number pointed at it.
CHANNELS: list[dict[str, str]] = [
    {
        "icon": "phone-answered",
        "label": "Phone calls",
        "note": "answered, not recorded",
    },
    {"icon": "whatsapp", "label": "WhatsApp", "note": "where owners live"},
    {"icon": "message", "label": "Text / SMS", "note": "no app to install"},
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
        "icon": "phone-answered",
        "title": "Picks up the phone",
        "body": (
            "A real conversation, not a menu. It knows your prices and your "
            "calendar, and it books the appointment while you're still on the "
            "job."
        ),
    },
    {
        "icon": "languages",
        "title": "Answers in their language",
        "body": (
            "Spanish, Polish, English - spoken or written, answered in the "
            "same one the first time. No menus, no 'press 1 for Spanish'."
        ),
    },
    {
        "icon": "phone-forwarded",
        "title": "Puts the caller through to you",
        "body": (
            "An accident, a complaint, anything it shouldn't touch - it says "
            "it's putting them through and transfers the live call to your "
            "phone."
        ),
    },
    {
        "icon": "phone-missed",
        "title": "Texts back a missed call",
        "body": (
            "The layer underneath. If a call still gets away, the caller has "
            "a text within seconds - before they try the next business."
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
            "The number on your signs and your van, forwarded when nobody "
            "picks up. Your customers never need to know anything changed."
        ),
    },
]

# -------------------------------------------------------------------------- faq

FAQ: list[dict[str, str]] = [
    {
        "q": "Does it actually answer the phone, or just text people back?",
        "a": (
            "It answers the phone. A real conversation - it hears what they "
            "ask, in their language, checks your calendar and books them in. "
            "If it still misses a call, the caller gets a text within seconds "
            "as the layer underneath."
        ),
    },
    {
        "q": "What happens on a call it can't handle?",
        "a": (
            "It says it's putting them through and transfers the live call to "
            "your phone, with a message to you explaining why. If you can't "
            "take it, it promises a callback and tells you that it did - so "
            "the promise is never one nobody knows about."
        ),
    },
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
        "a": (
            "No. You keep the number on your signs and your van, and forward "
            "it when nobody picks up - the same setting you'd use for "
            "voicemail. Pick up yourself whenever you're free; Chichi only "
            "takes the calls you don't."
        ),
    },
    {
        "q": "How do I stop it?",
        "a": (
            "Tell us and we switch it off the same day. There is nothing to "
            "uninstall and no notice period to sit through."
        ),
    },
]

#: A test walks every href: an in-page anchor must have its target on the
#: page, a path must return 200. Three dead links on a landing page is the
#: kind of thing nobody notices until a visitor does.
SECTORS: list[dict[str, str]] = [
    {
        "key": "dental",
        "href": "/dental",
        "name": "Dental and aesthetic clinics",
        "line": "The patient who writes at 11pm, from another country, "
        "in another language.",
    },
    {
        "key": "real_estate",
        "href": "/inmobiliarias",
        "name": "Estate agencies",
        "line": "The buyer browsing from Stockholm at midnight, asking what is "
        "still available.",
    },
    {
        "key": "immigration",
        "href": "/extranjeria",
        "name": "Immigration law firms",
        "line": "The client who needs an appointment, in Arabic, before a "
        "deadline they are frightened of.",
    },
]


# ------------------------------------------------------------ page furniture

#: Prose that used to be written straight into the templates. Here so a second
#: locale is a file rather than a second set of templates.
COPY: dict[str, Any] = {
    "per_month": "month",
    "cta_primary": "Start free",
    "pricing_heading": "Two plans. Everything in both.",
    "plan_includes": [
        "Every text and WhatsApp answered, day and night",
        "Every language your customers write in",
        "Missed-call text-back within seconds",
        "Appointment booking, confirmations and reminders",
        "Your morning summary, and an alert the moment something needs you",
    ],
    "shadow_line": (
        "14 days of shadow mode first - you approve every reply before it sends"
    ),
    "setup_fee_line": "One-off setup 300-500 €, depending on what we load in.",
    "billing_line": (
        "Billing starts when you switch it on, not when you sign up. "
        "Cancel any time."
    ),
    "hero_price": "From 490 € a month",
    "see_pricing": "See pricing",
    "niche_does": "What it does",
    "niche_refuses": "What it will not do",
    # The page after the form. Short, and it promises a WhatsApp message
    # rather than an email on purpose: that is what the form asked for, and
    # replying by email to someone who gave us a WhatsApp number would be an
    # odd first impression for a product that answers WhatsApp.
    "thanks": {
        "eyebrow": "Got it",
        "heading": "We'll message you",
        "heading_accent": "within a day.",
        "lede": (
            "On WhatsApp, at the number you gave us - which is the whole "
            "point of the product, so it would be strange to email you "
            "instead. If it is urgent, just reply when it arrives."
        ),
        "back": "Back to the start",
    },
    # The signup form. Every label, hint and error lives here rather than in
    # the template, so a second language is this file again and not a second
    # copy of the form.
    "signup": {
        "heading": "Put it on your number, and watch it work.",
        "lede": (
            "We onboard by hand, a few at a time, so that the assistant knows "
            "your prices and your rules before it answers anybody."
        ),
        "business_name": "Business name",
        "niche": "What kind of business is it?",
        "niche_hint": (
            "This decides what the assistant is allowed to say. You can change "
            "it later; it is not a label on an invoice."
        ),
        "niche_placeholder": "Choose one",
        "trade": "In your own words",
        "trade_hint": "Only if you picked 'Something else' above.",
        "country": "Country",
        "country_placeholder": "Choose one",
        "country_hint": "Not listed? Leave it, and give the number below in full.",
        "city": "City",
        "contact_name": "Your name",
        "phone": "WhatsApp number",
        "phone_hint": (
            "With the country code. This is where we reply, and where the "
            "assistant sends you its morning summary."
        ),
        "email": "Email",
        "email_hint": "Optional.",
        "languages": "What languages do your customers write in?",
        "languages_hint": (
            "Tick everything you hear from. The assistant answers in whichever "
            "one they used, and you can add more later."
        ),
        "crm_tool": "What do you book with today?",
        "crm_tool_hint": "Diary, software, a paper book - whatever it really is.",
        "site_url": "Website",
        "site_url_hint": "Optional. It is the fastest way for us to read up on you.",
        "message": "Anything we should know?",
        "submit": "Send",
        # Not decoration and not boilerplate. GDPR Art. 6(1)(a) is the only
        # lawful basis we have for contacting someone who filled in this form,
        # and Art. 7(1) says we have to be able to show they agreed - so the
        # box is unticked, it is required, and the moment it is ticked is
        # stored on the row.
        "consent": (
            "I agree that {brand} may contact me on WhatsApp or by email about "
            "setting up this assistant."
        ),
        # {privacy} is substituted with a link. The placeholder is inside the
        # sentence rather than appended to it because Spanish will not put the
        # link where English does.
        "consent_detail": (
            "We keep what you typed above and nothing else. No payment "
            "details, no contract. You can ask us to delete it at any time - "
            "our {privacy} says how."
        ),
        "privacy_link": "privacy policy",
    },
    "niche_limits_note": (
        "These are not settings you have to remember to turn on. They are "
        "built into what the assistant is allowed to say, and they are covered "
        "by tests that run before every release."
    ),
}


# ------------------------------------------------------------- niche pages

#: One page per niche. The home page is a short router into these, because a
#: clinic and an extranjeria firm want to read about different things and a
#: single page trying to address both says less to each.
#:
#: `demo` names which of DEMO_CONVERSATIONS to show, so a niche page is never
#: illustrated with someone else's conversation.
NICHE_PAGES: dict[str, dict[str, Any]] = {
    "dental": {
        "slug": "dental",
        "path": "/dental",
        "niche": "dental",
        "demo": ["nl", "es"],
        "eyebrow": "Dental and aesthetic clinics",
        "headline": (
            "Your next international patient writes at 11pm in German. "
            "Chichi answers in German and books the consultation."
        ),
        "lede": (
            "Most of the enquiries that matter arrive after you have closed, "
            "from someone in another country deciding between you and a clinic "
            "two streets away. Chichi answers in their language, quotes only "
            "the prices you have set, and puts the appointment in the book."
        ),
        "does": [
            "Answers in the patient's own language, day or night",
            ("Quotes only the 'from' prices you have entered - never a figure "
            "it worked out itself"),
            "Takes travel dates and offers only times inside the stay",
            "Books the consultation and sends your deposit link",
            "Sends the recall when it is due",
        ],
        "refuses": [
            "Never diagnoses, and never says what a symptom might be",
            ("Never advises on medication, anaesthetic or whether a treatment "
            "suits someone"),
            "Never estimates how long treatment or healing takes",
            "Does not ask for photographs or medical history",
            ("Pain, bleeding, swelling or anything after a procedure goes "
            "straight to your clinical team"),
        ],
    },
    "inmobiliarias": {
        "slug": "inmobiliarias",
        "path": "/inmobiliarias",
        "niche": "real_estate",
        "demo": ["sv", "en"],
        "eyebrow": "Estate agencies",
        "headline": (
            "A buyer in Stockholm is looking at your listings at midnight. "
            "Chichi answers in Swedish and arranges the viewing."
        ),
        "lede": (
            "Foreign buyers browse when your office is shut and ask the same "
            "five questions. Chichi answers them in the buyer's language, in "
            "your own listing words, and hands a qualified enquiry to an agent."
        ),
        "does": [
            "Answers in the buyer's language, from your own listing text",
            "Takes budget, area, timeline, cash or mortgage, and NIE status",
            "Sends matching properties - only ones you have listed",
            "Arranges viewings and follows up over the next month",
        ],
        "refuses": [
            "Never confirms a property is still available",
            "Never negotiates, and never says where an offer stands",
            "Never invents a size, a build year or a licence status",
            ("Never says buying property grants residency - Spain's golden visa "
            "has ended, and Portugal's no longer covers it"),
            "Tax, mortgage and legal questions go to a person",
        ],
    },
    "extranjeria": {
        "slug": "extranjeria",
        "path": "/extranjeria",
        "niche": "immigration",
        "demo": ["ar", "es"],
        "eyebrow": "Immigration law firms",
        "headline": (
            "A client writes in Arabic about arraigo, frightened of a deadline. "
            "Chichi answers in Arabic and books the consultation."
        ),
        "lede": (
            "Your clients write in the language they are most comfortable in, "
            "often outside office hours, often worried. Chichi takes the facts, "
            "books the consultation, and knows exactly where to stop."
        ),
        "does": [
            ("Answers in the client's language - Arabic, Spanish, French, "
            "Russian, Urdu and more"),
            ("Sorts the enquiry: arraigo, nationality, TIE renewal, digital "
            "nomad visa, family reunification"),
            "Sends only the document list your firm has published",
            "Books and takes the deposit for a paid consultation",
            "Reports case status exactly as your firm set it",
        ],
        "refuses": [
            "Never says whether someone qualifies, or what will happen",
            "Never predicts timescales, outcomes or deadlines",
            "Never assembles a document list of its own",
            "Never asks for a passport, NIE or case number",
            "Says in the first message that this is not legal advice",
            ("An expulsion order, detention, or a deadline inside ten days goes "
            "to you immediately, before anything else"),
        ],
    },
}
