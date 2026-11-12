# Chichi

One multi-tenant backend that answers customers for many small businesses over
WhatsApp and SMS today, and over the phone later, without a rewrite. The AI
becomes their front desk, and the owner only ever touches WhatsApp.

The pilot is low-tech auto shops in Chicago, but nothing in the code is about
cars. What a business does, what it calls the thing a job is about ("vehicle",
"pet", "device", "garment"), and when to fetch a human are **configuration** -
see `app/core/defaults.py`. One codebase runs a garage, a groomer and a
plumber.

**This repository implements Phase 1 (core text loop), Phase 2 (pilot
features), Phase 3 (console, owner dashboard, landing page) and the v1.5
scheduled messages (reminders, daily summary).** A pilot business could run on
it, and a new one can be onboarded without touching SQL.

---

## What works today

A message arrives → the shop is identified → the conversation engine decides
whether the AI should answer → Claude replies in the customer's language, books a
drop-off, or hands off to the owner → the reply goes back out the same channel.
A missed call becomes a text conversation within seconds. Every token and message
is logged for margin tracking. Around that loop: a console for onboarding and
running shops, a read-only dashboard for owners, and a landing page that captures
the next one.

| Area | Status |
| --- | --- |
| Webhook gateway (signature check, dedupe, sub-second ack, enqueue) | done |
| Tenant router (number → shop, cached config, versioned prompt) | done |
| Conversation engine (24h window, human takeover, STOP/HELP, handoff) | done |
| Claude agent (Haiku default, Sonnet escalation, prompt caching) | done |
| Language detection and the four-step fallback order | done |
| SMS + WhatsApp adapters (Twilio) | done, sends stubbed until credentials exist |
| Local simulator channel + chat UI at `/sim` | done |
| Usage and cost accounting per shop | done |
| **Missed-call text-back** | done |
| **Google Calendar booking** (`check_availability`, `book_appointment`) | done, calendar stubbed until credentials exist |
| **Owner alerts and WhatsApp/SMS commands** | done |
| **Shadow mode** (bot drafts, owner approves) | done |
| **"Is my car ready?"** (`get_job_status`) | done |
| **Admin console** (onboarding, config editing, transcripts, usage, audit) | done |
| **Owner dashboard** (read-only; sign in with a texted code) | done |
| **Landing page + signup capture** | done |
| **Appointment reminders** (24h before, in the customer's language) | done |
| **Daily owner summary** (one message a morning, their language) | done |
| **Trade-neutral wording + vertical presets** | done |
| Approved WhatsApp templates | planned - `docs/whatsapp-templates.md` |
| Subscriptions and billing | planned - `docs/subscriptions.md` |
| Voice | v2 — the schema and agent interface are already ready for it |

---

## Setup

Requires Python 3.11+, Docker, and an Anthropic API key.

```bash
# 1. Infrastructure (Postgres on 5434, Redis on 6380)
docker compose up -d

# 2. Python environment
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"    # macOS / Linux

# 3. Configuration
cp .env.example .env
#    then put your key in .env:  ANTHROPIC_API_KEY=sk-ant-...

# 4. Schema and seed data
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m seeds.demo_shop

# 5. An account for the console (prompts for a password)
./.venv/Scripts/python.exe -m seeds.admin_user --email you@example.com
```

The ports are 5434 and 6380 rather than the defaults because other local
projects commonly hold 5432/5433 and 6379.

## Running

Two processes, two terminals:

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
./.venv/Scripts/python.exe -m arq app.queue.worker.WorkerSettings
```

The worker also runs the two cron sweeps - reminders every fifteen minutes, the
daily summary hourly - so it has to be running for those to go out.

Four things to open:

| URL | What it is |
| --- | --- |
| `/sim` | the local chat simulator |
| `/` | the public landing page and signup form |
| `/admin` | the internal console (staff login) |
| `/shop` | a shop owner's dashboard (code texted to their number) |

The simulator goes through the real gateway, router, engine, agent and delivery
path — only the transport is simulated, so no Twilio, Meta or Google account is
needed.

Things worth trying there:

- `What time do you close?` → hours from config
- `Mi carro hace un ruido raro` → Spanish from the first reply, no menu
- `My Civic needs brakes, can I drop it off tomorrow?` → real slots, then a booking
- `I was in an accident and my lawyer wants the invoice` → handoff; the AI goes quiet
- Set the caller to `+4915112345678` and send an empty message → English + German greeting

To act as the **owner**, set the caller number to the shop's `owner_phone`
(`+13125550111` for the seeded Westside shop) and send `HELP`.

To simulate a **missed call**:

```bash
curl -X POST http://localhost:8000/webhooks/sim/call \
  -H 'Content-Type: application/json' \
  -d '{"external_id":"sim-westside","from_number":"+48221234567"}'
```

`GET /health` reports which channels are registered and whether the Claude key,
Twilio credentials and Redis connection are present.

## The console, the dashboard and the landing page

Server-rendered Jinja templates inside the same FastAPI app — no build step, no
second deployment, no CORS, no API tokens. One stylesheet, zero JavaScript
dependencies.

**`/admin` — the internal console.** Staff sign in with an email and password.
It does what used to need `psql`: create a shop and its numbers, edit every
config field, read the exact system prompt Claude is sent for any shop and
channel, flip `shadow` → `live` → `paused`, read any transcript, see Claude spend
by model and by shop, and read an append-only audit log of who changed what.

Create the first account with `python -m seeds.admin_user`. Re-running it for an
existing address resets that password and signs that admin out everywhere.

**`/shop` — the owner dashboard, read-only on purpose.** The owner enters their
mobile number and we text them a six-digit code on their usual channel. They see
today's state: conversations waiting on them, drafts awaiting approval,
appointments, jobs, missed calls and whether each got a text back.

There are no buttons. Everything an owner can *do* — `OK`, `READY 2014 Civic`,
`RESUME`, typing their own reply — is already a text message, and duplicating
those as web actions would mean two code paths for every action and a much larger
attack surface on a session unlocked by an SMS code. `tests/test_web_isolation.py`
asserts the dashboard router exposes nothing but `GET`.

**`/` — the landing page.** What it does, pricing, FAQ and a signup form that
writes a `signups` row. Copy and price live in
`app/web/templates/marketing/landing.html` and are placeholders for you to set.
There is no alerting channel yet, so a new signup shows up in the console's
Signups tab and as a `NEW SIGNUP` line in the log. "Make a shop" on that tab
prefills the wizard and marks the signup converted.

### How the web layer is kept safe

**An owner session is bound to one `client_id`, and no owner route accepts a shop
parameter.** The dashboard reads `principal.shop_id` — the client id stored in
the session row at login. A conversation id in the path is still filtered by
*that* client id, so another shop's id resolves to a 404 rather than to their
transcript. Two structural tests enforce this: no owner route may take a
shop-shaped argument, and `app/web/owner.py` may not import `repo_admin`, the
module holding the queries that can read across tenants.

**Only the hash of a session token is stored.** The cookie holds 32 random bytes;
the database holds its SHA-256. A dump does not hand over live sessions. Admin
and owner use different cookie names, so being in the console does not log you
into a shop.

**Passwords use `hashlib.scrypt`** — memory-hard, in the standard library, no
native wheel to build on Windows. Parameters live in the hash string, so the cost
can be raised later without invalidating anyone. Login codes are hashed too, but
with SHA-256: six digits is only 10⁶, so what actually protects them is a
ten-minute expiry, single use, five attempts and three codes per quarter hour —
and spending 16 MB of memory per guess would just be a denial of service.

**One number owning two shops signs in to the oldest**, with a warning in the
log, because there is no way to guess which one they meant. A shop picker belongs
with whatever multi-shop billing turns out to look like, not in the login.

**The first login step never says whether a number is ours.** A number that owns
no shop, a rate-limited number, and a real owner all render the same "we sent a
code" page. Otherwise the form is a free directory of our clients.

**Every state-changing POST re-checks the session and a CSRF token** and writes an
audit row. `SameSite=Lax` already blocks a cross-site form post from carrying the
cookie; the token is the second lock.

**Saving config does three things together:** write the row, drop that shop's
cached prompt (`core.router.invalidate`), and re-hash `prompt_version`. Miss the
second and the shop answers from a stale prompt for up to a minute. The hash
covers *every* channel's rendered prompt, not just the base one — tone, owner
language and the channel style blocks live in the per-channel rules, so hashing
the base prompt alone would leave the stored version unchanged after a tone edit
while every reply changed.

**Hours are validated with the booking engine's own parser.** `core.booking.parse_hours`
is what decides whether a shop is open, so the console validates with exactly
that function: you cannot save `8-6pm` and leave a shop silently closed all week.

**`repo.py` / `repo_shop.py` / `repo_admin.py`.** The first two are tenant-scoped
and every helper takes `client_id` first, enforced structurally. `repo_admin.py`
deliberately is not — it holds authentication, signups and the console's
cross-tenant rollups — so every one of its callers sits behind an admin session.

## Scheduled messages

Two cron sweeps on the arq worker, not one delayed job per appointment:

```
every 15 min   sweep_reminders   appointments due a reminder -> send once
hourly at :02  sweep_summaries   each business, once its own local hour arrives
```

**Why sweeps.** The obvious design enqueues a delayed job when a booking is
made. It is also the fragile one: the job lives in Redis, so flushing Redis
loses every pending reminder silently, and a booking later moved or cancelled
leaves an orphan job that still fires. A sweep derives what is due from the
database on every run, so nothing can go stale, a cancelled appointment simply
stops being selected, and a worker outage self-heals - the next pass sends
slightly late rather than not at all.

**Reminders** go out `reminder_hours_before` hours ahead (24 by default, `0`
turns them off), in the customer's language, with the exact stored time. The
text is composed in code and then translated - never generated - because a model
asked to write a reminder is a model given the chance to invent a time.
`appointments.reminder_sent_at` marks the **attempt**, so a reminder that could
not be delivered is not retried every fifteen minutes; the owner is alerted
instead. Cancelled appointments and ones the owner flagged with `CHANGE` are
never reminded.

**The daily summary** is one message to the owner each morning at their own
`summary_hour` (7 by default), in their own language: yesterday's numbers,
today's appointments, work ready for collection, and anything waiting on them.
`client_config.last_summary_on` holds the business's own local date, so the
hourly sweep sends exactly one per business per day whatever restarts happen.
**A day with nothing on it is skipped** rather than sent empty - that is how an
owner learns to ignore us.

Only `live` businesses are swept: a business in shadow mode is still onboarding,
and both of these are unprompted messages. `AI_ENABLED=false` stops both, as a
global automation kill switch.

## One codebase, many trades

Nothing in the code is specific to auto shops. What differs between a garage, a
salon, a groomer, a plumber and a repair shop is config:

- **`client_config.vertical`** - which preset the business started from.
  `app/core/defaults.py` has `VERTICALS`: starting services, extra handoff
  keywords, and the word below. Chosen in the console's "Add a business" wizard.
- **`client_config.job_noun`** - the word this business uses for what a job is
  about: `vehicle`, `pet`, `device`, `garment`, `order`, or just `job`. It is
  **load-bearing**, not decoration: it is rendered into the system prompt
  (*"when the customer asks whether their pet is ready"*), into what the owner is
  texted (`READY <pet>`), and into the dashboard's column headers.
- **The `item` column** on `customers`, `appointments`, `leads` and `jobs` holds
  whatever that is. It was called `vehicle` until v1.5; the migration renames it
  rather than dropping and re-adding, so no data is lost.

Regulated trades - medical, dental, legal, financial - are deliberately absent
from the presets. They need advice boundaries and record-keeping rules reviewed
before anyone onboards one, not a preset that makes it look routine.

## Testing

```bash
./.venv/Scripts/python.exe -m pytest
```

479 tests, no API key or network needed — Claude responses are replayed from
recorded payload shapes built as real SDK types, and the calendar runs on an
in-memory stub. The suite uses a separate `frontdesk_test` database, rebuilt from
the models on each run.

| File | Covers |
| --- | --- |
| `test_tenant_isolation.py` | cross-tenant reads and writes return nothing, plus a structural check that every repo helper takes `client_id` first |
| `test_language.py` | the country-code table, the four-step resolution, per-shop overrides |
| `test_prompt.py` | prompt determinism (caching breaks on any byte change), guardrails, strict tool schemas |
| `test_gateway.py` | Twilio signature validation against a known-good fixture, parsing, dedupe |
| `test_window.py` | the WhatsApp 24-hour rule, and that a blocked reply is recorded rather than dropped |
| `test_engine.py` | STOP/HELP, opt-out, takeover, handoff, misunderstanding counter, holding reply |
| `test_agent_fixtures.py` | the agent's request shape and tool loop, replayed offline |
| `test_booking.py` | slot generation against hours, lead time, busy periods and closed days; booking and calendar-failure handling |
| `test_owner_commands.py` | the command grammar and every handler, including draft approval |
| `test_missed_call.py` | which call statuses count, dedupe, channel choice, opt-out, the canned fallback |
| `test_shadow_mode.py` | nothing reaches a customer unapproved; owner routing; the engine's alerts |
| `test_web_forms.py` | config form parsing, password and token hashing, phone normalisation |
| `test_web_auth.py` | sessions, lockout, the code lifecycle, and the database's own check constraint |
| `test_web_admin.py` | every console route is behind the login; CSRF; what a config save actually writes |
| `test_web_isolation.py` | an owner cannot reach another shop's data by any route, plus the structural guards |
| `test_web_marketing.py` | the landing form, honeypot, rate limits, and escaping of what strangers type |
| `test_web_legal.py` | the public site, and every sentence A2P 10DLC registration requires |
| `test_web_dashboard.py` | the data actually reaches the page, and each business's own vocabulary is used |
| `test_reminders.py` | what is due, what is skipped, never twice, and the language it goes out in |
| `test_summary.py` | content, timing, once-per-local-day, and quiet days staying quiet |
| `test_deploy_readiness.py` | the health check's 503 path, credentials from the environment, and that the image ships its templates |

---

## Architecture

```
Twilio / simulator webhook
   │
   ├─ app/gateway/     verify signature, dedupe, persist, ack, enqueue
   │                   (never calls Claude - the provider must not wait)
   ├─ [arq queue]      Redis-backed, retries with backoff
   ├─ app/core/router  receiving number → client_id → shop config (cached)
   ├─ app/core/engine  owner? compliance? takeover? should the AI reply?
   ├─ app/agent/       system prompt (cached) → Claude → tools → structured reply
   └─ app/core/delivery  persist the outbound row, then send via the adapter

app/web/    the console, the owner dashboard and the landing page - same app,
            same port, same database, but never in the message path
```

### Design decisions worth knowing

**One Claude call returns both the reply and the detected language.** Structured
outputs (`output_config.format`) and tools run in the same request, so there is
no separate detection service and no second round trip.

**The system prompt must stay byte-identical.** Prompt caching is a prefix match
over `tools` → `system` → `messages`. `app/agent/prompt.py` is deterministic
(sorted keys, no timestamps) and the enabled tool list is fixed in a fixed
order. `prompt_version` is the sha256 of the rendered text. The seeded shops
render ~950–1200 tokens, comfortably over Haiku 5.5's 512-token cache floor.
**The current date is deliberately *not* in the system prompt** — the model needs
it to turn "tomorrow" into a date, so it goes in the uncached per-turn message
instead, where it cannot move the cache key every day.

**Model routing.** `claude-haiku-5-5` at `effort: "low"` by default (the model
defaults to `medium`, slower and dearer than a front-desk reply needs). Escalates
once to `claude-sonnet-5-5` on a long message, a handoff keyword, an unparseable
reply, a tool loop that will not converge, or a refusal — Haiku 5.5 has no
server-side refusal fallback, so this escalation *is* the fallback.
`temperature`, `top_p` and `budget_tokens` are never sent (all rejected), and
`tool_choice` is never forced (a 400 on both models), so every tool is `strict`
and the prompt names them.

**The customer always gets an answer.** Any failure in the agent — including a
missing credential or a network error — degrades to a holding reply in the
customer's language plus an owner alert. A missed call falls back to a canned
bilingual opener. Never silence.

**Tenant isolation lives in the repository layer.** In `app/repo.py` and
`app/repo_shop.py` every helper takes `client_id` first and filters on it even
when the row id alone would be unique; a test enforces that structurally. The
exceptions are `get_endpoint`, the lookup that *establishes* the tenant, and
`app/repo_admin.py`, whose rows (admins, sessions, signups, audit) belong to no
shop and whose cross-tenant rollups only ever run behind an admin session.
Supabase row-level security comes when this moves off local Postgres — the moment
a browser talks to the database directly, a repository guard is not enough.

**`messages.seq`, not `created_at`, orders a conversation.** Postgres `now()` is
transaction-scoped, so messages written in one transaction share a timestamp and
would come back in arbitrary order. The same column is the `/sim` poll cursor.

**History excludes anything the customer never saw** — blocked sends and
shadow-mode drafts. Replaying those would have the model believe it already said
something it did not.

**`arq`, not RQ or Celery.** Both of those are fork-based and do not run on
Windows. arq is asyncio + Redis and already supports the delayed jobs v1.5 needs
for reminders.

**`tzdata` is a real dependency.** `zoneinfo` has no timezone database on Windows
or on slim Linux images, and every slot calculation is timezone-aware.

### Booking

Candidate slots come from the shop's own opening hours, then anything busy on its
calendar — or already booked in our `appointments` table — is subtracted. Both are
checked so two customers texting at the same moment cannot take the same slot
while the calendar is still catching up.

`check_availability` returns exact `start_at` values and the model must copy one
verbatim into `book_appointment`; `confirm_slot` re-validates it at booking time
and refuses a slot that has since gone. Our row is the source of truth: if the
Google write fails, the booking stands, the note says so, and the owner is told —
rather than the customer being promised a slot that exists nowhere.

A booking also creates a `jobs` row, which is what later lets the owner type
`READY 2014 Civic` and have us find the right customer.

### Owner commands

The owner controls everything by replying on WhatsApp or SMS. Alerts carry a
short code (e.g. `39JM`) so one chat can be addressed out of several.

| Reply | Effect |
| --- | --- |
| `OK` | confirm the booking, or send the shadow-mode draft |
| `CHANGE` | flag a booking that will not work; pauses that chat |
| `READY <item>` | mark the job ready; the customer is told in their language. `<item>` is whatever the trade works on - `READY 2014 Civic`, `READY Bella`, `READY iPhone 13`. The word in `HELP` comes from `job_noun`. |
| `RESUME` | hand the conversation back to the AI |
| `HELP` | list the commands |
| anything else | relayed to the customer, translated into their language |

Any of these can be prefixed with a code: `39JM OK`, `39JM we need another day`.

Two details that matter: codes mix letters and digits and contain no vowels, so
`"2014 Civic is done"` is never misread as code `2014` plus a message, and a code
that does not exist is treated as message text rather than silently eaten.
Approving a draft sends *that row* rather than writing a second one, and writing
your own wording marks the draft `rejected` so a later bare `OK` cannot send
stale text.

Owner alerts go out on WhatsApp while the owner's own 24-hour window is open and
fall back to SMS otherwise (`owner_channel: auto`). The 24-hour rule applies to
the owner too.

### Voice readiness (so v2 is a routing change, not a rewrite)

- `run_agent(...)` takes a conversation plus the new message and returns a reply;
  a voice turn is a transcript in and text out.
- Per-channel style rules live in `client_config.channel_style`; the `voice`
  block already forbids lists, URLs and markdown.
- `Channel` includes `voice`, the `calls` table exists, and each shop already has
  a `voice` endpoint row — the number the shop forwards unanswered calls to.
  Switching voice on means pointing that endpoint at the voice adapter.

---

## Going live with a real shop

Nothing below is needed for local development.

**Twilio**

- [ ] Buy a number; point its SMS webhook at `POST {PUBLIC_BASE_URL}/webhooks/twilio/sms`
- [ ] Point the voice **status callback** at `POST {PUBLIC_BASE_URL}/webhooks/twilio/voice-status`
      (this is what triggers missed-call text-back)
- [ ] Put `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in `.env`. Signature
      validation turns itself on as soon as a token is present, and warns loudly
      in the logs while it is skipped.
- [ ] Set `PUBLIC_BASE_URL` to the exact public URL Twilio is configured with —
      signatures are computed against it, not against the request.
- [ ] Turn on conditional call forwarding on the shop's own line (no answer after
      4 rings, or busy) to the Twilio number
- [ ] 10DLC brand and campaign registration for US SMS. STOP and HELP are already
      handled automatically.

**WhatsApp**

- [ ] Verified WhatsApp Business number, webhook pointed at `/webhooks/twilio/whatsapp`
- [ ] Submit the reminder and "car ready" templates for approval. Until a template
      is approved, any reply outside the 24-hour window is recorded as `blocked`
      and the owner is alerted.

**Google Calendar** (one service account for all shops — no per-shop OAuth)

- [ ] Create a Google Cloud project and enable the Google Calendar API
- [ ] Create a **service account** and download its JSON key
- [ ] Set `GOOGLE_CREDENTIALS_PATH` in `.env` to that file's path
- [ ] For each shop: share its calendar with the service account's email address,
      with "Make changes to events" permission
- [ ] Put that calendar's id in `client_config.calendar_id`

With `GOOGLE_CREDENTIALS_PATH` unset, bookings still work end to end against an
in-memory calendar — they just will not appear in a real Google Calendar.

**The console**

- [ ] `python -m seeds.admin_user --email you@example.com` for each staff member
- [ ] Serve it over HTTPS and set `PUBLIC_BASE_URL` to the `https://` URL — the
      `Secure` flag on the session cookie is derived from it, so over plain HTTP
      the cookie is sent in the clear
- [ ] The login throttle is per-process, so behind more than one worker move it
      to Postgres or put a rate limit in front

**Per shop** — all of this is the console's "Add a shop" wizard now

- [ ] Create the shop: name, owner's mobile, timezone, languages, numbers
- [ ] Fill in services, hours, price ranges, tone and handoff rules on its config
      page. With no price ranges the agent refuses to quote any number, which is
      the right default for a shop nobody has configured yet.
- [ ] Read `/admin/shops/{id}/prompt` before going live — it is exactly what
      Claude will be sent
- [ ] It starts in `shadow`, so the bot drafts and the owner approves. Flip it to
      `live` when the drafts look right.

## Deploying

`Dockerfile` and `render.yaml` are committed: **one Render service** (~$7/mo),
Postgres from Supabase, Redis from Upstash - both on free tiers at pilot scale.
**`docs/deploy.md` is the runbook** - follow it in order, because three of its
steps are waits on other companies.

The single service runs the queue worker and both cron sweeps *inside* the web
process (`RUN_WORKER_IN_PROCESS=true`). That is a cost decision, not an
architecture: two always-on services cost twice as much, and both halves of
this app are pure async I/O, so the worker's waiting never delays a webhook's
acknowledgement - the one latency that matters, because Twilio times out. What
it costs is shared fate: a crash takes both halves down, and they scale
together. `app/queue/worker.py` is still a standalone entrypoint, so splitting
them again is four lines of config - the last section of `docs/deploy.md`.

```bash
docker build -t frontdesk .
docker run -p 8000:8000   -e DATABASE_URL=postgresql+asyncpg://...   -e ANTHROPIC_API_KEY=... frontdesk
```

Three things in there are worth knowing even if you never deploy:

- **The image installs the project editable and runs from the source tree.** A
  normal `pip install .` copies only the modules named in
  `[tool.setuptools] packages`, leaving `app/web/templates` and
  `app/web/static` out - the console would start and then 500 on its first
  page. `tests/test_deploy_readiness.py` asserts this stays right.
- **`/health` queries the database and returns 503 when it cannot.** The
  platform's health check is the only thing that can turn a wrong
  `DATABASE_URL` into a failed deploy rather than into every webhook 500ing one
  at a time.
- **A dead worker makes the service unhealthy on purpose.** With the worker
  in-process, its death would otherwise be silent: replies queue up and nobody
  is told. `/health` reports it, which makes the platform restart the service -
  noisy recovery in preference to quiet failure.
- **`PUBLIC_BASE_URL` is compared as an exact string.** Twilio signatures are
  computed against it, not against the incoming request, so it has to match
  what Twilio is configured with - `https`, no trailing slash. A mismatch
  rejects every webhook with a 403.

CI runs lint, the migration round-trip, the suite against a real Postgres, and
builds the image and boots it. Render auto-deploys on commit, so that workflow
is the only thing between a red test run and production - make it a required
check on the default branch.

## Repository layout

```
app/
  main.py          FastAPI app, lifespan, /health, /sim
  config.py        settings (pydantic-settings, reads .env)
  models.py        all 16 tables; Channel already includes `voice`
  repo.py          data access - client_id first, always
  repo_shop.py     read models for one shop (dashboard + console pages)
  repo_admin.py    admins, sessions, signups, audit, cross-tenant rollups
  schemas.py       InboundMessage / OutboundMessage / AgentReply + JSON schema
  channels/        one adapter per channel, same two jobs each
  gateway/         webhook routes and Twilio signature validation
  core/
    ingest.py        the fast path: resolve, dedupe, persist, enqueue
    router.py        tenant router and config cache
    engine.py        who answers, when
    booking.py       slot generation and booking
    delivery.py      outbound sends, recorded either way
    missed_call.py   call logging and text-back
    owner.py         owner alert composition and routing
    owner_commands.py the owner's reply grammar and handlers
    reminders.py     the appointment-reminder sweep
    summary.py       the daily owner summary
    defaults.py      vertical presets and what a new business starts with
  web/
    brand.py         the brand and legal details, in one place
    auth.py          sessions, passwords, one-time codes, the FastAPI guards
    admin.py         the console
    owner.py         the owner dashboard (read-only)
    owner_auth.py    the owner's code login, kept apart from the dashboard
    marketing.py     landing page and signup form
    forms.py         config form <-> JSONB, with the error messages
    templating.py    Jinja environment and filters
    templates/       base, admin/, shop/, marketing/
    static/app.css   one stylesheet, light and dark
  agent/           prompt builder, tool registry, language, translate, Claude loop
  integrations/    Google Calendar (service account + in-memory stub)
  queue/           arq worker, tasks, cron sweeps, enqueue helpers
  sim/static/      the local chat UI
seeds/             two pilot shops (Chicago + Cicero), and admin_user.py
docs/
  deploy.md        the production runbook (Render + Supabase)
  whatsapp-templates.md   plan: what to submit and the code it needs
  subscriptions.md plan: schema, Stripe, and the decisions you owe
Dockerfile         one image for both processes
render.yaml        web + worker + Redis blueprint
.github/workflows  CI: lint, migrations, tests, and an image boot test
migrations/        Alembic
tests/
```
