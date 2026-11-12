# WhatsApp templates: what to submit, and what to build

**Status: plan. Nothing here is submitted or built yet.**

Written for whoever does the Meta/Twilio submission and the small code change
that follows it. Read the first section even if you are only doing the
paperwork — it explains why a reminder sometimes cannot be delivered at all.

---

## Why this blocks things

WhatsApp only lets a business send free-form text inside a **24-hour customer
service window**, and that window opens *only* when the customer messages the
business. Outside it, the single thing you may send is a **template** Meta has
pre-approved.

The system already enforces this: `app/channels/twilio_whatsapp.py` refuses a
free-form send past `conversations.window_expires_at`, records the message as
`blocked`, and alerts the owner. Nothing is silently dropped. But blocked is
still not delivered, and three features run straight into it:

| Feature | Window state | Today |
| --- | --- | --- |
| **Appointment reminder** (24h before) | Almost always **closed** — the customer booked days ago | Blocked on WhatsApp, owner alerted. Works on SMS. |
| **"It's ready"** (`READY` command) | Usually closed — the job took a day | Blocked on WhatsApp, owner told to call them. Works on SMS. |
| **Missed-call text-back** | **Always closed** — a phone call does not open a WhatsApp window | This is why `missed_call_channel` defaults to `sms`. |

So on WhatsApp, every scheduled or unprompted message this product sends needs a
template. Inside a live conversation nothing changes — the agent's replies are
free-form and always will be.

**The cheap interim answer is SMS.** SMS has no window, 10DLC is already
registered, and the adapters already exist. A shop can run reminders and
"it's ready" over SMS from day one and WhatsApp for conversations. The only cost
is per-segment carrier fees. Submit the templates anyway — approval takes days
you do not control — but do not let it block the pilot.

---

## What already exists

- `client_config.templates` — a JSONB map of purpose to approved template name,
  editable in the console: `{"reminder": "appointment_reminder_v1"}`.
- `OutboundMessage.template_name` flows from `core/delivery.py` through to the
  adapter, and `_check_policy` lets a send through a closed window when a
  template name is present.
- `app/core/reminders.py` passes `templates["reminder"]` on every reminder.

## What still has to be built

One change, perhaps an afternoon. Twilio does not send a template by *name*; it
sends a **Content SID** plus ordered variables:

```python
client.messages.create(
    content_sid="HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    content_variables=json.dumps({"1": "Rosa", "2": "Thursday 12 Nov, 9:00 AM"}),
    from_=..., to=...,
)
```

So:

1. Add `content_sid: str | None` and `variables: dict[str, str]` to
   `OutboundMessage` in `app/schemas.py`.
2. In `app/channels/twilio_whatsapp.py`, when `content_sid` is set, send through
   `content_sid` + `content_variables` instead of `body`. Keep `body` populated
   anyway — it is what the transcript, the console and the owner's dashboard
   show, and it is what the customer will actually read.
3. Store the SID, not just the name, in config:
   `{"reminder": {"name": "appointment_reminder_v1", "sid": "HX...", "variables": ["name", "when"]}}`.
   The console's template field is already a JSON textarea, so this needs no
   schema change.
4. Have `reminders.send_one` and the `READY` handler build the variable map in
   the same order the template declares.

**Do not let the model fill template variables.** Compose them in code, as
`reminders.compose` already does. A template with a hallucinated time is worse
than no reminder, and it is the one message the customer will act on.

---

## The templates to submit

Three, all **UTILITY** category. Category matters: UTILITY covers a transaction
the customer initiated (an appointment they booked, a job they brought in).
Anything promotional is MARKETING, costs more, and needs separate opt-in.
Mis-filing a marketing message as UTILITY is a common rejection reason.

Submit each in **English and Spanish** at minimum; add Polish if the pilot's
Cicero-style shops need it. Each language is a separate submission against the
same template name.

### 1. `appointment_reminder_v1`

```
Hi {{1}}, a reminder of your appointment at {{2}} on {{3}}. Reply to this
message if you need to change it.
```
- `{{1}}` customer name (or "there" when unknown — never leave a variable empty)
- `{{2}}` business name
- `{{3}}` local date and time, already formatted by `core.booking.label_at`

Spanish:
```
Hola {{1}}, le recordamos su cita en {{2}} el {{3}}. Responda a este mensaje si
necesita cambiarla.
```

### 2. `job_ready_v1`

```
Hi {{1}}, your {{2}} is ready for collection at {{3}}. Reply to this message if
you have any questions.
```
- `{{2}}` is the `item` — a vehicle, a pet, a device. Trade-neutral on purpose:
  one template serves every kind of business on the platform.

### 3. `missed_call_followup_v1`

```
Hi, we missed your call to {{1}}. Reply to this message and we will help you
right away.
```
Only needed if a business wants missed-call text-back on WhatsApp rather than
SMS. Worth submitting: for a shop whose customers all use WhatsApp, this is the
highest-value message the product sends.

### Rules the content has to follow

- No variable may be empty, start or end the message, or sit next to another
  variable. Rejected automatically.
- No URL shorteners. No all-caps words. No "click here".
- Keep a free-form sentence inviting a reply — it is what reopens the 24-hour
  window, after which the agent can answer normally.
- Do not include the price or anything negotiable.

---

## Submission steps

1. Twilio Console → **Messaging → Content Template Builder → Create new**.
2. Pick **Text** (none of these need media or buttons), category **UTILITY**,
   language, and paste the body with `{{1}}`-style placeholders.
3. Add a sample value for every variable — a submission without samples is
   rejected without review.
4. Submit for WhatsApp approval. Typically minutes to a few hours; allow 48.
5. When approved, copy the **Content SID** (`HX...`).
6. In the console: the shop's config page → *Approved WhatsApp template names* →
   add the purpose, name and SID.
7. Verify: set a test appointment 23 hours out with a closed window, run the
   reminder sweep, and check the message is `sent` rather than `blocked`.

## Opt-in, which is the part people skip

Meta requires the business to hold evidence that the customer agreed to be
contacted. For this product the evidence is natural and already stored: the
customer texted the business first, and `messages` holds it with a timestamp.
That is sufficient for UTILITY messages about their own appointment or job.

What to be careful about:

- **Never send a template to a number that only ever called.** For a missed
  call there is no written opt-in at all. SMS is the right channel there, and
  10DLC rules already cover it.
- `customers.opted_out_at` is honoured on every path, including reminders.
  Keep it that way: a STOP must stop templates too.
- Keep the data. The `messages` table is the opt-in audit trail, so do not add a
  retention policy that deletes inbound messages without keeping proof of first
  contact.

## Once approved

- Fill in `templates` for each shop that has WhatsApp.
- Consider the fallback: if a WhatsApp template send fails and the shop also has
  an SMS endpoint, sending the same text over SMS is better than not reaching
  the customer. That is a ten-line change in `core/delivery.py` and worth doing
  — but make it explicit per shop, because SMS costs per segment.
- Re-check the owner path separately. Owner alerts and the daily summary go out
  the same way, and `owner_channel: auto` already falls back to SMS when the
  owner's own window is closed. That is why the 7 AM summary works today
  without any template at all.
