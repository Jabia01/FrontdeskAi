# Subscriptions and billing

**Status: plan. No billing code exists yet.**

Written for whoever builds this, and for you to argue with first — the numbers
and the terms at the bottom are decisions only you can make, and everything
else follows from them.

---

## What exists today

- `clients.plan` — a free-text string, currently `"pilot"` for everyone. Not a
  price, not a term, not a state.
- `usage_events` — every Claude call and every message, with cost. This is the
  **cost** side of the margin and it is already accurate per business: the
  console's Usage page reads it.
- `ClientStatus` — `lead → onboarding → shadow → live → paused → churned`.
  Crucially, **`paused` already stops the AI answering**, and the console already
  revokes the owner's dashboard sessions when a business leaves the running
  statuses. The enforcement lever billing needs is built.

There is no revenue side at all: no price, no term, no payment method, no
invoice, nothing that knows whether a business has paid.

---

## The shape of it

One subscription per business. Flat monthly fee, a minimum term, a free trial
that coincides with shadow mode, and non-payment that ends in `paused` rather
than in a surprise.

### Lifecycle, mapped onto the statuses that already exist

| Business status | Billing meaning |
| --- | --- |
| `lead` | A signup from the landing page. No customer in Stripe yet. |
| `onboarding` / `shadow` | **Free trial.** The bot drafts, the owner approves. A Stripe customer and subscription exist with a trial end, so the card is captured up front but nothing is charged. |
| `live` | **Billing.** First invoice on the day the trial ends. |
| `paused` | Service stopped — either the owner asked, or payment failed to the end of its retries. The subscription may still be open. |
| `churned` | Subscription cancelled and the term served. |

Going live is the billing event. That is deliberate: the owner sees two weeks of
drafts before any money moves, which is also the strongest answer to "does it
actually work for my business".

### Schema

Four new tables. `clients` keeps its single `plan` column, repointed at
`plans.code`.

```
plans
  code            text primary key        -- "pilot", "standard"
  name            text
  price_cents     integer                 -- in `currency`
  currency        char(3)                 -- "usd"
  interval        text                    -- "month"
  min_term_months integer                 -- 0 = none
  trial_days      integer
  included_messages integer null          -- null = unmetered
  overage_cents   integer null            -- per message beyond included
  stripe_price_id text null
  active          boolean

subscriptions                             -- one live row per client
  id              uuid pk
  client_id       uuid fk clients unique   -- one per business, enforced
  plan_code       text fk plans
  status          enum: trialing | active | past_due | paused | canceled
  trial_ends_at   timestamptz null
  current_period_start / current_period_end timestamptz
  min_term_ends_at timestamptz null
  cancel_at       timestamptz null         -- requested; effective later
  canceled_at     timestamptz null
  stripe_customer_id / stripe_subscription_id text null
  created_at / updated_at

invoices                                  -- a local mirror, for the console
  id              uuid pk
  client_id       uuid fk clients
  subscription_id uuid fk subscriptions
  stripe_invoice_id text unique
  period_start / period_end timestamptz
  amount_due_cents / amount_paid_cents integer
  currency        char(3)
  status          text                     -- draft|open|paid|void|uncollectible
  hosted_url      text null                -- Stripe's own invoice page
  issued_at / paid_at timestamptz null

payment_events                             -- raw webhook log, append-only
  id              uuid pk
  provider        text                     -- "stripe"
  event_id        text unique              -- idempotency: replays are no-ops
  type            text
  client_id       uuid null fk clients
  payload         jsonb
  received_at     timestamptz
  handled_at      timestamptz null
  error           text null
```

Two details worth not getting wrong:

- **`subscriptions.client_id` is unique.** One business, one subscription. If
  multi-shop owners become real (one phone number already can own two
  businesses — see the owner-login note in the README), that is an account layer
  above this, not a second row here.
- **`payment_events.event_id` is unique**, and every webhook is written there
  *before* it is acted on. Stripe retries and replays; without this, one
  `invoice.paid` retry double-counts.

### Provider: Stripe

Not because it is cheapest, but because of what you do not have to build: SCA
and 3-D Secure, card updates, invoice PDFs, dunning emails, tax if you turn on
Stripe Tax. For a handful of pilot businesses, hand-built billing is weeks of
work and a PCI conversation for no revenue gain.

- **Stripe Checkout** for the first payment method. A hosted page, so no card
  field ever touches this app.
- **Stripe Billing Portal** for the owner to change a card or see invoices. One
  link from the owner dashboard, no UI to build. This is the one place it is
  worth adding a button to the otherwise read-only dashboard — the alternative
  is an owner who cannot fix their own expired card.
- **Webhooks** as the source of truth for state. Never infer "paid" from a
  redirect back from Checkout; the customer can close the tab.

Events to handle, and nothing else at first:

| Event | Effect |
| --- | --- |
| `checkout.session.completed` | Store `stripe_customer_id`, mark `trialing` |
| `customer.subscription.updated` | Sync status, period, `cancel_at` |
| `invoice.paid` | Mirror the invoice, status `active`, clear any pause |
| `invoice.payment_failed` | Status `past_due`, **text the owner** |
| `customer.subscription.deleted` | Status `canceled`; set the business `churned` |

### Non-payment

Stripe retries on its own schedule. Our part is making it visible and then
stopping service:

1. First failure → `past_due`, and the owner gets a WhatsApp/SMS message with
   the Billing Portal link. They are a small business owner; an email they never
   read is not a notice. This reuses `core/owner.send_to_owner`.
2. Stripe's retries run their course (default ~3 weeks).
3. Final failure → business `status = paused`. The AI stops answering, the
   console shows it, and the owner's dashboard sessions are revoked by the code
   that already does that.

**Never let a payment failure silently disable a front desk.** A shop whose
customers are being ignored because a card expired will churn and tell people
why. Every step above is accompanied by a message to the owner.

### Minimum term

The doc calls for a minimum term, which exists to stop a business taking the
two-week trial, the onboarding work and then leaving in month one.

- `min_term_ends_at = first_billing_date + min_term_months`.
- A cancellation request sets `cancel_at = max(current_period_end, min_term_ends_at)` —
  service continues and so does billing until then.
- Record the term in writing at signup. A term the owner did not knowingly agree
  to is a chargeback, not revenue.

Consider being gentler than the contract allows for the first few pilots. Three
businesses that leave happy are worth more than one held to a term.

### Metering

Start flat. One price, unlimited conversations, as the landing page says today.

The margin data is already there: `usage_events` gives Claude spend per business
per day, and the console's Usage page shows cost per message. Watch it for a
month. Only add `included_messages` / `overage_cents` if a real business turns
out to be unprofitable — the measured Haiku cost is a fraction of a cent per
turn, so the carrier fees will matter long before the model does.

What is *not* in `usage_events` and should be before you price overage: Twilio's
per-segment SMS and per-message WhatsApp charges. `usage_events` records a
`message_out` row with a segment count, so the shape is right; it needs the
actual rate applied.

### What the console needs

- Plan picker on the shop page, and the subscription's status as a badge next to
  the business status.
- Trial end, next invoice date, last invoice and its Stripe link.
- A "start billing" action for when a shop goes live, and a link to the Stripe
  customer for everything unusual.
- Audit rows for every billing-relevant change, which `repo_admin.audit`
  already provides.

### Security

- No card data in this database, ever. Only Stripe ids.
- Verify every webhook with `stripe.Webhook.construct_event` and the signing
  secret. An unverified billing webhook endpoint is a way to mark yourself paid.
- `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` in `.env` via
  `app/config.py`, never in a config row — same rule as every other credential
  here.
- The webhook route sits beside the Twilio ones in `app/gateway/`, with the same
  shape: verify, persist, ack fast, do the work in the worker.

---

## Build order

1. `plans` and `subscriptions`, seeded with one plan. Console shows them.
   *Nothing charges yet, and every business is on a manual free plan.*
2. Stripe customer + Checkout + the webhook endpoint with signature
   verification and `payment_events`. A test-mode card end to end.
3. Trial → live billing, the owner's Billing Portal link, invoice mirroring.
4. Dunning: `past_due`, the owner message, and the final `paused`.
5. Minimum-term logic on cancellation.
6. Overage, only if the measured margin says so.

Steps 1–2 are most of the work. Until step 4 exists, non-payment has to be
handled by hand, which is entirely fine at three businesses and not at thirty.

---

## Decisions needed before any of this is built

1. **Price** — the landing page currently says **$249/month** as a placeholder.
2. **Minimum term** — 3, 6, or 12 months? Or none, and compete on being easy to
   leave?
3. **Trial length** — two weeks is what shadow mode and the landing page both
   say. Keep them in step.
4. **Who pays the carrier fees** — absorbed into the monthly price, or passed
   through? Absorbing is simpler to sell and exposes you to a shop with unusual
   volume.
5. **Currency and tax** — USD only at first? Stripe Tax on or off?
6. **What happens at the end of the trial if no card was added** — does the
   business stay in shadow, or stop? Stopping is cleaner; staying is friendlier.
