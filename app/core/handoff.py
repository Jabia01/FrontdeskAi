"""Why a conversation went to a human, as a countable value.

`conversations.handoff_reason` holds the model's own words, and those are what
an owner actually reads: "customer says the crown came off and it hurts". That
is the right thing to show and the wrong thing to count. A regulated niche has
to be able to answer "how many clinical questions did it refuse last month"
without grepping free text in three languages.

So every handoff now carries a second value: one code from a closed list.

Three design choices worth stating, because each of them was the alternative:

* **A Python enum over a `String(32)`, not a Postgres enum.** Adding a code to
  a native enum is an `ALTER TYPE` against a live table, which means a new
  niche's codes arrive through a migration. Here they arrive through a code
  change, and `classify` guarantees nothing unknown ever reaches the column.
* **One global list, with per-niche subsets.** A code means the same thing
  everywhere, so counts are comparable across tenants, and the per-niche subset
  exists only to keep the prompt short - it is what the model is *told* to
  pick from, not what it is believed about. A dental shop reporting
  `offer_or_negotiation` is odd, but it is stored as given: the honest record
  of a strange event beats a tidy `other`.
* **`classify` never raises.** It sits on the path between a customer message
  and a human being told about it. A `ValueError` here would turn a mis-typed
  code into a conversation nobody is told about, which is strictly worse than
  an imprecise label.

Imports `app.agent.niche` and nothing else from `app`, so `app.repo` can
normalise on the way to the column without a cycle.
"""

from __future__ import annotations

import re
from enum import StrEnum

from app.agent.niche import NICHES


class HandoffCode(StrEnum):
    """The closed list. Declaration order is the order the console shows."""

    # --- anyone, any niche ------------------------------------------------
    ASKED_FOR_HUMAN = "asked_for_human"
    ANGRY = "angry"
    COMPLAINT = "complaint"
    NOT_UNDERSTOOD = "not_understood"
    UNKNOWN_ANSWER = "unknown_answer"
    PRICE_OR_QUOTE = "price_or_quote"
    HIGH_VALUE = "high_value"
    LEGAL_OR_INSURANCE = "legal_or_insurance"
    PAYMENT_OR_REFUND = "payment_or_refund"
    # --- dental -----------------------------------------------------------
    MEDICAL_CONCERN = "medical_concern"
    CLINICAL_ADVICE = "clinical_advice"
    # --- immigration ------------------------------------------------------
    LEGAL_ADVICE = "legal_advice"
    CASE_SPECIFIC = "case_specific"
    URGENT_DEADLINE = "urgent_deadline"
    # --- real estate ------------------------------------------------------
    OFFER_OR_NEGOTIATION = "offer_or_negotiation"
    VIEWING_LOGISTICS = "viewing_logistics"
    # --- shared -----------------------------------------------------------
    DOCUMENT_REQUEST = "document_request"
    DATA_REQUEST = "data_request"
    TECHNICAL_FAILURE = "technical_failure"
    OTHER = "other"


#: Every code, as plain strings in declaration order. This is what the
#: `handoff_to_human` tool schema offers the model, and it is deliberately the
#: whole list rather than a per-niche one: the tool schemas render into the
#: cached prompt prefix, and a schema that varied by niche would be one more
#: thing that has to stay byte-stable. The *prompt* names the niche's subset.
ALL_CODES: tuple[str, ...] = tuple(code.value for code in HandoffCode)

#: What a shop with no declared codes may emit - the six US verticals, and any
#: niche that has not thought about it yet. These are the escalations that exist
#: in every trade.
UNIVERSAL_CODES: tuple[HandoffCode, ...] = (
    HandoffCode.ASKED_FOR_HUMAN,
    HandoffCode.ANGRY,
    HandoffCode.COMPLAINT,
    HandoffCode.NOT_UNDERSTOOD,
    HandoffCode.UNKNOWN_ANSWER,
    HandoffCode.PRICE_OR_QUOTE,
    HandoffCode.HIGH_VALUE,
    HandoffCode.LEGAL_OR_INSURANCE,
    HandoffCode.PAYMENT_OR_REFUND,
    HandoffCode.TECHNICAL_FAILURE,
    HandoffCode.OTHER,
)

#: How the console says it. Written for an owner, not for an engineer: the
#: label is the sentence they would use themselves.
LABELS: dict[HandoffCode, str] = {
    HandoffCode.ASKED_FOR_HUMAN: "Asked for a person",
    HandoffCode.ANGRY: "Angry customer",
    HandoffCode.COMPLAINT: "Complaint",
    HandoffCode.NOT_UNDERSTOOD: "Could not understand them",
    HandoffCode.UNKNOWN_ANSWER: "No answer in the business information",
    HandoffCode.PRICE_OR_QUOTE: "Wanted a price or a quote",
    HandoffCode.HIGH_VALUE: "Large or unusual job",
    HandoffCode.LEGAL_OR_INSURANCE: "Lawyer or insurance",
    HandoffCode.PAYMENT_OR_REFUND: "Payment or refund",
    HandoffCode.MEDICAL_CONCERN: "Pain, injury or possible infection",
    HandoffCode.CLINICAL_ADVICE: "Asked for clinical advice",
    HandoffCode.LEGAL_ADVICE: "Asked for legal advice",
    HandoffCode.CASE_SPECIFIC: "Asked about their own case",
    HandoffCode.URGENT_DEADLINE: "Urgent - detention, court or a deadline",
    HandoffCode.OFFER_OR_NEGOTIATION: "Offer or negotiation",
    HandoffCode.VIEWING_LOGISTICS: "Viewing arrangements",
    HandoffCode.DOCUMENT_REQUEST: "Asked for documents",
    HandoffCode.DATA_REQUEST: "Data protection request",
    HandoffCode.TECHNICAL_FAILURE: "Something broke",
    HandoffCode.OTHER: "Something else",
}


# --------------------------------------------------------------------------
# Inferring a code from free text
#
# Needed because two of the three handoff paths pre-date codes entirely: the
# model can set `handoff: true` in its structured reply with nothing but a
# phrase, and the engine's own "could not understand them twice" path has no
# model involvement at all. Rather than leave those as `other`, the phrase is
# read.
#
# This is a fallback, not the mechanism. When the model calls
# `handoff_to_human` it names the code itself and none of this runs.
#
# A pattern ending in `*` matches a prefix, so `hinch*` catches "hinchazon"
# and "hinchado". Everything else is matched on word boundaries, which is not
# fussiness: "denied" contains "nie", and plain substring matching turned
# "the application was denied" into a Spanish ID-number escalation.
# --------------------------------------------------------------------------


def _compile(pattern: str) -> re.Pattern[str]:
    if pattern.endswith("*"):
        return re.compile(r"\b" + re.escape(pattern[:-1]), re.IGNORECASE)
    return re.compile(r"\b" + re.escape(pattern) + r"\b", re.IGNORECASE)


def _table(
    rows: tuple[tuple[HandoffCode, tuple[str, ...]], ...],
) -> tuple[tuple[HandoffCode, tuple[re.Pattern[str], ...]], ...]:
    return tuple((code, tuple(_compile(p) for p in pats)) for code, pats in rows)


#: Checked before the universal table, so a niche can claim a word the whole
#: product also uses. "lawyer" is `legal_or_insurance` to a garage and
#: `legal_advice` to an immigration firm, and both are right.
#:
#: Order inside a niche is priority order. Dental puts pain before clinical
#: questions because a patient in pain is the urgent one; immigration puts
#: detention before everything.
_NICHE_PATTERNS: dict[
    str, tuple[tuple[HandoffCode, tuple[re.Pattern[str], ...]], ...]
] = {
    "dental": _table(
        (
            (
                HandoffCode.MEDICAL_CONCERN,
                (
                    "pain", "hurts", "hurting", "bleed*", "swell*", "swollen",
                    "infect*", "abscess", "broken", "knocked out", "injur*",
                    "trauma", "emergency", "dolor", "duele", "sangr*",
                    "hinch*", "infecc*", "urgenc*", "roto",
                ),
            ),
            (
                HandoffCode.CLINICAL_ADVICE,
                (
                    "diagnos*", "medicat*", "medicin*", "dosage", "dose",
                    "anaesthe*", "anesthe*", "sedat*", "contraindicat*",
                    "pregnan*", "allerg*", "symptom*", "suitable", "suitability",
                    "clinical", "medical history", "photo*", "x-ray", "xray",
                    "healing", "heal", "recovery", "medicac*", "embaraz*",
                    "alergi*", "sintoma*", "clinic*", "foto*",
                ),
            ),
        )
    ),
    "real_estate": _table(
        (
            (
                HandoffCode.OFFER_OR_NEGOTIATION,
                (
                    "offer", "offered", "negotiat*", "counter-offer",
                    "counter offer", "oferta", "negoci*", "puja",
                ),
            ),
            (
                HandoffCode.LEGAL_ADVICE,
                (
                    "mortgage", "notary", "nie", "tax", "taxes", "residency",
                    "golden visa", "visa", "inheritance", "conveyanc*",
                    "hipotec*", "notario", "impuesto*", "residenc*",
                    "herencia",
                ),
            ),
            (
                HandoffCode.VIEWING_LOGISTICS,
                (
                    "viewing", "viewings", "visit", "visiting", "key", "keys",
                    "visita", "ver el piso", "ver la casa", "llave*",
                ),
            ),
        )
    ),
    "immigration": _table(
        (
            (
                HandoffCode.URGENT_DEADLINE,
                (
                    "expulsion", "removal order", "deport*", "detain*",
                    "detention", "cie", "court", "hearing", "appeal",
                    "deadline", "urgent", "expulsi*", "detenid*", "plazo",
                    "urgente", "juzgado", "recurso",
                ),
            ),
            (
                HandoffCode.CASE_SPECIFIC,
                (
                    "my case", "their case", "case number", "case file",
                    "file number", "passport", "nie", "tie", "expediente",
                    "mi caso", "pasaporte",
                ),
            ),
            (
                HandoffCode.LEGAL_ADVICE,
                (
                    "qualify", "qualifies", "eligib*", "chances", "odds",
                    "approval", "approved", "rejected", "denied", "how long",
                    "which route", "best route", "advice", "advise", "lawyer",
                    "abogad*", "califica", "asesor*", "probabilidad*",
                    "cuanto tarda",
                ),
            ),
            (
                HandoffCode.DOCUMENT_REQUEST,
                (
                    "document*", "checklist", "paperwork", "papers",
                    "certificate", "documentac*", "papeles", "certificado",
                ),
            ),
        )
    ),
}

#: The fallback table, read in order. Deliberately last: a niche gets first
#: claim on any word it has an opinion about.
_UNIVERSAL_PATTERNS = _table(
    (
        (
            HandoffCode.ASKED_FOR_HUMAN,
            (
                "asked for a person", "asked for a human", "wants a human",
                "wants a person", "speak to a human", "speak to someone",
                "talk to a human", "talk to someone", "real person",
                "hablar con", "una persona",
            ),
        ),
        (
            HandoffCode.NOT_UNDERSTOOD,
            (
                "could not understand", "did not understand", "not understood",
                "cannot understand", "unintelligible", "unclear",
                "no entiendo", "no se entiende",
            ),
        ),
        (
            HandoffCode.ANGRY,
            (
                "angry", "furious", "abusive", "shouting", "rude", "upset",
                "enfadad*", "molest*", "cabread*",
            ),
        ),
        (
            HandoffCode.COMPLAINT,
            ("complaint", "complain*", "unhappy", "quej*", "reclamac*"),
        ),
        (
            HandoffCode.PAYMENT_OR_REFUND,
            (
                "refund", "chargeback", "payment", "invoice", "billing",
                "overcharg*", "reembols*", "factura", "pago", "cobro",
            ),
        ),
        (
            HandoffCode.LEGAL_OR_INSURANCE,
            (
                "lawyer", "attorney", "solicitor", "insurance", "insurer",
                "liability", "claim", "abogad*", "seguro", "aseguradora",
            ),
        ),
        (
            HandoffCode.PRICE_OR_QUOTE,
            (
                "price", "prices", "quote", "quotation", "estimate", "cost",
                "how much", "discount", "precio*", "presupuesto", "cuanto",
                "descuento",
            ),
        ),
        (
            HandoffCode.DATA_REQUEST,
            (
                "gdpr", "rgpd", "data protection", "my data", "delete my",
                "erasure", "privacy", "subject access", "proteccion de datos",
                "mis datos",
            ),
        ),
        (
            HandoffCode.DOCUMENT_REQUEST,
            ("document*", "paperwork", "documentac*", "papeles"),
        ),
        (
            HandoffCode.TECHNICAL_FAILURE,
            (
                "could not answer", "could not reply", "technical", "timeout",
                "timed out", "api error", "exception", "crashed",
                "assistant failed",
            ),
        ),
        (
            HandoffCode.UNKNOWN_ANSWER,
            (
                "cannot answer", "do not know", "does not know", "don't know",
                "no information", "not configured", "nothing configured",
                "outside what", "no lo se", "no tengo esa informacion",
            ),
        ),
        (
            HandoffCode.HIGH_VALUE,
            (
                "large job", "big job", "unusually large", "unusual job",
                "high value", "high-value", "bulk", "fleet", "commercial",
            ),
        ),
    )
)


def normalise(raw: str | None) -> HandoffCode | None:
    """A string to a code, or `None`. Never raises.

    Tolerates the shapes a model actually emits: upper case, spaces or hyphens
    for underscores, and the enum's own repr if something ever round-trips
    through `str()`.
    """
    if not raw:
        return None
    text = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    text = text.removeprefix("handoffcode.")
    try:
        return HandoffCode(text)
    except ValueError:
        return None


def codes_for(niche: str = "") -> tuple[HandoffCode, ...]:
    """The codes a niche is told to choose from.

    Not a constraint on what is stored - `classify` accepts any known code
    from any shop - but what the prompt lists, which is what the model will
    almost always pick from.
    """
    spec = NICHES.get((niche or "").strip())
    if spec is None or not spec.escalate_codes:
        return UNIVERSAL_CODES
    declared = tuple(
        code for code in (normalise(name) for name in spec.escalate_codes) if code
    )
    # `other` is always available: a niche list that cannot express "something
    # else" forces a wrong answer rather than an honest one.
    if HandoffCode.OTHER not in declared:
        declared += (HandoffCode.OTHER,)
    return declared


def classify(
    text: str = "", *, code: str | None = None, niche: str = ""
) -> HandoffCode:
    """The one place a handoff code is decided. Never raises.

    `code` is what the model named, and it wins whenever it is a code we know.
    `text` is the free-text reason, read only as a fallback. Anything else is
    `other`, which is a truthful answer: we escalated and cannot say why.
    """
    named = normalise(code)
    if named is not None:
        return named

    haystack = (text or "").strip()
    if not haystack:
        return HandoffCode.OTHER

    tables = _NICHE_PATTERNS.get((niche or "").strip(), ())
    for table in (tables, _UNIVERSAL_PATTERNS):
        for candidate, patterns in table:
            if any(pattern.search(haystack) for pattern in patterns):
                return candidate
    return HandoffCode.OTHER


def label(code: str | None) -> str:
    """For display. An unrecognised value is shown as itself rather than
    hidden, so a code that outlived its label is visible instead of blank."""
    known = normalise(code)
    if known is not None:
        return LABELS[known]
    return str(code or "")
