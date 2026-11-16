"""What a niche changes about the agent.

`VERTICALS` in `app/core/defaults.py` is the *seed* layer: the services, the
handoff keywords and the word for a job that a new shop starts with. An owner
edits all of it afterwards. This module is the *behavioural* layer: which tools
the model is given, which extra hard rules it is held to, and - for a regulated
niche - the advice boundary it must not cross.

The split matters. Seed values live in `client_config` and are editable in the
console. A boundary is a compliance control: if it lived in `client_config`, a
console edit could silently delete the sentence that stops a dental bot giving
clinical advice. So the boundary is derived from the niche at render time and
there is no field for it in the database.

The one escape hatch is `config["niche_notes"]`, which is additive only.

Every vertical has an entry, including the six that predate this module. Those
six get the identity case - the full tool set, no boundary, `regulated=False` -
which is the property that proves this change cannot alter an existing US
tenant's behaviour. `tests/test_niche.py` asserts exactly that.

Imports nothing from `app`, so `app.core.defaults` can build its presets from
these specs without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Every tool that exists today. The six pre-existing verticals get all of them,
#: which is what they already had before niches were a concept.
ALL_TOOLS: tuple[str, ...] = (
    "get_business_info",
    "create_lead",
    "handoff_to_human",
    "check_availability",
    "book_appointment",
    "get_job_status",
)

#: What every new niche starts from. `get_job_status` is deliberately absent: it
#: is the trades "is it ready?" mechanism, a question a clinic never hears and
#: an immigration firm must answer very carefully.
CORE_TOOLS: tuple[str, ...] = (
    "get_business_info",
    "create_lead",
    "handoff_to_human",
    "check_availability",
    "book_appointment",
)


@dataclass(frozen=True, slots=True)
class NicheSpec:
    """One niche. Seed fields feed `defaults.VERTICALS`; the rest feed the agent."""

    key: str
    label: str
    job_noun: str

    # --- seed layer: copied into client_config once, owner-editable afterwards
    services: tuple[str, ...] = ()
    handoff_keywords: tuple[str, ...] = ()
    market_languages: tuple[str, ...] = ("en",)
    currency: str = "$"
    example: str = "READY job 1482"

    # --- behavioural layer: derived at render time, never editable
    tools: tuple[str, ...] = ALL_TOOLS
    rules: tuple[str, ...] = ()
    boundary: str = ""
    regulated: bool = False
    #: Whether the first message says, unprompted, that this is an AI.
    #:
    #: Article 50 of the EU AI Act asks for it, and the three Spanish niches
    #: do it. The US verticals do not, and that is deliberate: they predate
    #: this, their customers are not in scope, and quietly changing what a
    #: garage says to its customers is not ours to do. What *did* change for
    #: everyone is that no shop may now deny being an AI when asked - that was
    #: never defensible anywhere.
    discloses: bool = False
    first_contact_notices: tuple[str, ...] = ()
    escalate_keywords: tuple[str, ...] = ()
    escalate_codes: tuple[str, ...] = ()

    def allows(self, tool: str) -> bool:
        return tool in self.tools


def _legacy(
    key: str,
    label: str,
    job_noun: str,
    services: list[str],
    keywords: list[str],
    example: str,
) -> NicheSpec:
    """A vertical that predates niches: everything on, nothing forbidden."""
    return NicheSpec(
        key=key,
        label=label,
        job_noun=job_noun,
        services=tuple(services),
        handoff_keywords=tuple(keywords),
        example=example,
        tools=ALL_TOOLS,
        regulated=False,
    )


NICHES: dict[str, NicheSpec] = {
    # ------------------------------------------------------------- pre-existing
    "auto": _legacy(
        "auto",
        "Auto repair, tyres, bodywork",
        "vehicle",
        ["Brakes", "Oil change", "Tyres", "Diagnostics"],
        ["tow", "accident", "insurance", "lawyer"],
        "READY 2014 Civic",
    ),
    "salon": _legacy(
        "salon",
        "Salon, barber, nails, beauty",
        "appointment",
        ["Cut", "Colour", "Blow dry"],
        ["complaint", "refund", "allergic", "reaction"],
        "READY Maria",
    ),
    "pet": _legacy(
        "pet",
        "Pet grooming, boarding, daycare",
        "pet",
        ["Full groom", "Bath and tidy", "Nail trim"],
        ["bite", "aggressive", "injury", "sick", "vet"],
        "READY Bella",
    ),
    "trades": _legacy(
        "trades",
        "Plumbing, electrical, HVAC, handyman",
        "job",
        ["Call-out", "Repair", "Installation"],
        ["leak", "flood", "gas", "emergency", "no power"],
        "READY job 1482",
    ),
    "repair": _legacy(
        "repair",
        "Phone, computer and appliance repair",
        "device",
        ["Screen replacement", "Battery replacement", "Diagnostics"],
        ["data loss", "warranty", "insurance", "refund"],
        "READY iPhone 13",
    ),
    "other": _legacy("other", "Something else", "job", [], [], "READY job 1482"),
    # ------------------------------------------------------------------ regulated
    "dental": NicheSpec(
        key="dental",
        label="Dental and aesthetic clinic",
        job_noun="treatment",
        services=(
            "Consultation",
            "Check-up and hygiene",
            "Implants",
            "Veneers",
            "Orthodontics",
        ),
        handoff_keywords=(
            "pain",
            "bleeding",
            "swelling",
            "swollen",
            "infection",
            "abscess",
            "broken tooth",
            "emergency",
        ),
        market_languages=("es", "en", "de", "nl", "fr", "sv", "no", "ru", "uk"),
        currency="€",
        example="READY treatment",
        tools=CORE_TOOLS
        + (
            "price_range",
            "send_deposit_link",
            "capture_travel_dates",
            "recall_reminder",
        ),
        regulated=True,
        discloses=True,
        rules=(
            ("Never diagnose, never suggest what a symptom might be, and never "
            "comment on whether a treatment is suitable for this patient. Those "
            "are decisions only a clinician makes, after seeing them."),
            ("Quote treatment prices only as the 'from' figures the clinic has "
            "listed, and say the real figure depends on the consultation. If a "
            "treatment has no figure listed, say the clinic will confirm it - "
            "never estimate, and never add, scale or combine the listed numbers."),
            ("Do not ask for photographs, medical history, medication lists or "
            "any clinical detail. If the patient volunteers something clinical, "
            "do not repeat it back, and do not carry it into a summary beyond "
            "the treatment they are asking about."),
        ),
        boundary=(
            "You must not give clinical advice of any kind. That includes "
            "diagnosing, interpreting symptoms or photographs, advising on "
            "medication, dosage, anaesthetic or sedation, discussing "
            "contraindications, pregnancy, allergies or medical conditions, "
            "estimating how long a treatment takes or how long healing takes, "
            "and saying whether any treatment is right for this person.\n"
            "If the patient mentions pain, bleeding, swelling, an infection, a "
            "broken or knocked-out tooth, an injury, or a problem after a "
            "procedure, hand off immediately with `handoff_to_human` and say a "
            "member of the clinical team will come back to them. Do not "
            "reassure them, do not triage, and do not suggest anything they "
            "might do in the meantime."
        ),
        escalate_keywords=(
            "pain",
            "hurts",
            "bleeding",
            "swelling",
            "swollen",
            "infection",
            "abscess",
            "broken",
            "knocked out",
            "emergency",
        ),
        escalate_codes=(
            "medical_concern",
            "clinical_advice",
            "price_or_quote",
            # A full-mouth reconstruction is a large, unusual job, and the
            # live eval found the model reaching for this code without the
            # prompt offering it - the tool schema carries the whole list, so
            # it could. Offering it is better than being surprised by it.
            "high_value",
            "complaint",
            "asked_for_human",
            "angry",
            "data_request",
            "not_understood",
        ),
    ),
    "real_estate": NicheSpec(
        key="real_estate",
        label="Real estate agency selling to foreign buyers",
        job_noun="enquiry",
        services=("Viewings", "Buyer registration", "Valuation"),
        handoff_keywords=(
            "offer",
            "negotiate",
            "mortgage",
            "lawyer",
            "notary",
            "tax",
            "nie",
            "residency",
            "golden visa",
        ),
        market_languages=("es", "en", "de", "nl", "fr", "sv", "no", "ru", "uk"),
        currency="€",
        example="READY enquiry",
        tools=CORE_TOOLS
        + ("qualify_buyer", "send_matching_listings", "follow_up_sequence"),
        regulated=True,
        discloses=True,
        rules=(
            ("Never confirm that a property is still available, never confirm or "
            "negotiate a price, and never say where an offer stands. Take the "
            "enquiry and hand it to an agent."),
            ("Describe a property only in the words the agency has given you. "
            "Never state a size, a build year, a licence status, a cedula de "
            "habitabilidad, community fees or anything else that is not in that "
            "text, even if the buyer asks directly."),
        ),
        boundary=(
            "You must not give legal, tax, mortgage or immigration advice. That "
            "includes NIE procedure, non-resident tax, inheritance, community "
            "fees, mortgage eligibility and what a notary will require.\n"
            "You must never say or imply that buying a property grants "
            "residency or a visa. Spain's golden visa has ended, and Portugal's "
            "no longer covers real estate. If a buyer raises residency, say "
            "plainly that buying property does not grant residency and that an "
            "immigration lawyer is the right person to ask, then hand off.\n"
            "Offers, negotiation and anything legal go to a human immediately."
        ),
        escalate_keywords=(
            "offer",
            "negotiate",
            "mortgage",
            "lawyer",
            "notary",
            "tax",
            "nie",
            "residency",
            "golden visa",
            "visa",
        ),
        escalate_codes=(
            "offer_or_negotiation",
            "viewing_logistics",
            "legal_advice",
            "price_or_quote",
            "high_value",
            "asked_for_human",
            "angry",
            "complaint",
            # Arts. 15-21. The GDPR page we publish invites these, so the
            # agent has to have somewhere to put one. It was missing from
            # every niche until the eval library asked for it.
            "data_request",
            "not_understood",
        ),
    ),
    "immigration": NicheSpec(
        key="immigration",
        label="Immigration (extranjeria) law firm",
        job_noun="case",
        services=(
            "Initial consultation",
            "Arraigo",
            "Nationality",
            "TIE renewal",
            "Digital nomad visa",
            "Family reunification",
        ),
        handoff_keywords=(
            "deportation",
            "expulsion",
            "detained",
            "detention",
            "cie",
            "removal",
            "deadline",
            "court",
            "appeal",
        ),
        market_languages=("es", "ar", "fr", "pt", "en", "ru", "uk", "zh", "ur"),
        currency="€",
        example="READY case",
        # `get_job_status` is kept and re-used as case-status updates. This is
        # the one place the trades mechanism transfers cleanly: the firm sets a
        # status, the client asks, and the bot reports only what was set.
        tools=CORE_TOOLS
        + ("get_job_status", "intake_case_type", "document_checklist"),
        regulated=True,
        discloses=True,
        rules=(
            ("Never say what will happen to an application, how likely it is to "
            "succeed, how long it will take, or what a deadline is. Describe "
            "what the firm does, take the facts needed for a consultation, and "
            "offer to book it. Handing off does not wait for that booking: if "
            "you have declined one of those questions, hand off in the same "
            "reply, whether or not they have agreed to an appointment."),
            ("Give a document list only when the firm has provided one for that "
            "case type. Never assemble, extend or infer a list of your own."),
            ("Never ask for a passport number, NIE, case file number or any "
            "other identity document number, and never repeat one back if it "
            "is sent to you."),
            ("When reporting a case status, report only the status the firm has "
            "set, with no interpretation of what it means for the outcome."),
        ),
        boundary=(
            "You must not give legal advice or predict outcomes. That includes "
            "whether someone qualifies, which route is best for them, what will "
            "happen at an appointment, approval chances, processing times and "
            "what any deadline is.\n"
            "Whenever you decline a question because of this boundary, call "
            "`handoff_to_human` in the same reply. Declining is itself an "
            "escalation: someone has asked a question only the firm can "
            "answer, and the firm has to know they asked. Offering a "
            "consultation is not a substitute for that, and neither is waiting "
            "to see whether they accept one. Use `legal_advice` when they "
            "asked for advice, a prediction or a timing, `document_request` "
            "when the firm has given you no list for what they asked about, "
            "and `case_specific` when the question is about their own case or "
            "they have sent you an identity document number. An off-topic "
            "request that has nothing to do with the firm's work is not one of "
            "these: decline it and carry on.\n"
            "If the client mentions an expulsion or removal order, detention or "
            "a CIE, a court date, an appeal, or any deadline within the next "
            "ten days, hand off immediately with `handoff_to_human` and say "
            "someone from the firm will contact them urgently. Treat that as "
            "the most important thing in the conversation: do it before "
            "anything else, and do not try to book an appointment first."
        ),
        first_contact_notices=(
            ("Say once, in the customer's language, that this chat is not legal "
            "advice and does not create a lawyer-client relationship."),
        ),
        escalate_keywords=(
            "deportation",
            "expulsion",
            "detained",
            "detention",
            "cie",
            "removal",
            "deadline",
            "court",
            "appeal",
            "urgent",
        ),
        escalate_codes=(
            "legal_advice",
            "case_specific",
            "urgent_deadline",
            "document_request",
            "high_value",
            "asked_for_human",
            "angry",
            "complaint",
            "data_request",
            "not_understood",
        ),
    ),
}

#: The niches that carry an advice boundary. Named so tests and the console can
#: ask the question without hard-coding the list.
REGULATED: tuple[str, ...] = tuple(
    sorted(key for key, spec in NICHES.items() if spec.regulated)
)

DEFAULT_NICHE = "other"


def niche_for(key: str | None) -> NicheSpec:
    """Never raises. An unknown or missing vertical falls back to `other`."""
    return NICHES.get((key or "").strip()) or NICHES[DEFAULT_NICHE]
