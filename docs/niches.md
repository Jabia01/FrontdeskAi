# Niches

A *vertical* is what a shop starts with: services, handoff keywords, the word it
uses for a job. An owner edits all of it. A *niche* is what the agent is held
to: which tools it gets, which extra hard rules it obeys, and - for a regulated
niche - the advice boundary it may not cross.

Both come from `app/agent/niche.py`. `VERTICALS` in `app/core/defaults.py` is
derived from the same specs, so the seed layer and the behavioural layer cannot
drift apart.

## Why the split

Seed values live in `client_config` and are editable in the console. A boundary
is a compliance control. If it lived in `client_config`, a console edit could
silently delete the sentence that stops a dental bot giving clinical advice -
and nothing would fail, nothing would alert, and the next patient asking about
their swollen jaw would get an answer instead of a clinician.

So the boundary is derived from the niche at render time and there is no field
for it in the database. The only per-tenant escape hatch is
`config["niche_notes"]`, which is **additive only**: it can add a sentence, it
cannot remove one.

## The unregulated verticals

`auto`, `salon`, `pet`, `trades`, `repair`, `other`.

These predate niches and get the identity case: every tool, no boundary, no
extra rules, `regulated=False`, prices in `$`. This is asserted directly in
`tests/test_niche.py::TestExistingTenantsAreUntouched`, and the prompt snapshots
in `tests/snapshots/` prove their rendered prompts are byte-identical to what
they were before niches existed.

---

## dental — Dental and aesthetic clinic

Clinics in Spain treating foreign patients. The enquiry that matters arrives at
23:40 in German, and the clinic is closed.

**Job noun** `treatment` · **Currency** `€`
**Languages** es, en, de, nl, fr, sv, no, ru, uk

**Tools** `get_business_info`, `create_lead`, `handoff_to_human`,
`check_availability`, `book_appointment`.
No `get_job_status` - "is it ready?" is not a question a clinic hears.

**What it refuses**

- Any clinical advice: diagnosing, interpreting symptoms or photographs,
  medication, dosage, anaesthetic, sedation, contraindications, pregnancy,
  allergies, conditions.
- How long a treatment takes, or how long healing takes.
- Whether a treatment is right for this person.
- Any price that is not a "from" figure the clinic itself listed. It may not
  add, scale or combine the listed numbers.

**Data minimisation.** It does not ask for photographs, medical history or
medication lists. If a patient volunteers something clinical, it does not repeat
it back and does not carry it into a summary beyond the treatment of interest.
Health data is Art. 9 special-category data; the cheapest way to handle it
safely is not to collect it.

**Immediate handoff** pain, bleeding, swelling, infection, abscess, a broken or
knocked-out tooth, injury, or any problem after a procedure. It does not
reassure, triage or suggest anything in the meantime.
Codes: `medical_concern`, `clinical_advice`.

---

## real_estate — Agency selling to foreign buyers

**Job noun** `enquiry` · **Currency** `€`
**Languages** es, en, de, nl, fr, sv, no, ru, uk

**Tools** same five as dental.

**What it refuses**

- Confirming a property is still available, confirming or negotiating a price,
  or saying where an offer stands.
- Any detail not in the agency's own listing text: size, build year, licence
  status, *cédula de habitabilidad*, community fees.
- Legal, tax, mortgage or immigration advice - NIE procedure, non-resident tax,
  inheritance, mortgage eligibility, what a notary will require.

**The residency rule.** It must never say or imply that buying property grants
residency or a visa. **Spain's golden visa has ended, and Portugal's no longer
covers real estate.** A buyer who raises it is told plainly that property
purchase does not grant residency, pointed at an immigration lawyer, and handed
off. This is the single most likely way for this niche to mislead someone
expensively, so it is pinned by a test.

Codes: `offer_or_negotiation`, `viewing_logistics`, `legal_advice`.

---

## immigration — Extranjería law firm

**Job noun** `case` · **Currency** `€`
**Languages** es, ar, fr, pt, en, ru, uk, zh, ur

**Tools** the five, **plus `get_job_status`**, re-used as case-status updates.
This is the one place the trades mechanism transfers cleanly: the firm sets a
status, the client asks, and the bot reports only what was set - with no
interpretation of what it means for the outcome.

**What it refuses**

- Whether someone qualifies, which route is best, what will happen at an
  appointment, approval chances, processing times, what any deadline is.
- Any document list the firm has not provided for that case type. It may not
  assemble, extend or infer one.
- Asking for, or repeating back, a passport number, NIE or case file number.

The only shape it is allowed: describe what the firm does, take the facts needed
for a consultation, book it, hand off.

**Urgent handoff, before anything else.** An expulsion or removal order,
detention or a CIE, a court date, an appeal, or any deadline inside ten days.
It does not try to book an appointment first.
Codes: `legal_advice`, `case_specific`, `urgent_deadline`, `document_request`.

**First contact** carries an extra notice, translated: this chat is not legal
advice and does not create a lawyer-client relationship.

---

## Adding a niche

Not by copying a dict entry. A regulated niche needs:

1. a boundary written by someone who knows the regulated area;
2. a tool set reduced to what it can safely use;
3. reason codes that make escalations countable;
4. scenarios in the live eval suite, including the traps it is most likely to
   fail - and a passing run before anyone is onboarded.

What remains deliberately out of scope: anything that would have the agent
diagnose, prescribe, or give financial, investment or tax advice.

**This document is engineering's description of the controls. It is not legal
advice and it is not a sign-off.** Whether these controls are sufficient for a
Spanish clinic handling Art. 9 data, or for an extranjería firm where
unauthorised practice is a *colegio* matter, is a question for Spanish counsel.
