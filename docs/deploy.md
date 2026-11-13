# Deploying this, and connecting a real business to it

Written for whoever runs the deploy — probably you, once. Follow it in order;
the ordering is the point, because three steps involve waiting on other
companies and they should be started on day one.

**Target:** one Render service, Supabase for Postgres, Upstash for Redis.
About **$7/month** all in, because the only paid piece is the one always-on
instance.

`Dockerfile` and `render.yaml` are committed, and the image has been built and
run locally against the real Postgres and Redis: it serves every page, starts
the queue worker in-process with both cron sweeps, answered a real Spanish
message end to end through Claude in twelve seconds, and a wrong `DATABASE_URL`
correctly fails its health check.

**One service runs both halves.** The web process also runs the queue worker
and the two cron sweeps — see `app/queue/embedded.py` for why that is safe
(both halves are pure async I/O, so the worker's waiting never delays a webhook
acknowledgement) and what it costs (a crash takes both down, and they scale
together). The last section here is how to split them apart again.

---

## Start these clocks today

None of these is code, and each takes days you cannot compress. Everything else
here takes an afternoon.

| Thing | Typical wait | Blocks |
| --- | --- | --- |
| **10DLC brand + campaign registration** (Twilio, for US SMS) | days to ~2 weeks | *all* SMS to US numbers |
| **WhatsApp Business verification** | days | WhatsApp entirely |
| **WhatsApp template approval** | hours to days, after verification | reminders and "it's ready" outside the 24-hour window — see `whatsapp-templates.md` |

Until 10DLC clears, SMS to US numbers is either blocked or heavily filtered. Do
not plan a launch date around a clear run.

---

## 1. Supabase: the database

1. Create a project. Pick the region nearest your Render region (`ohio` in the
   blueprint → `us-east-1`). Save the database password.
2. **Connect → Connection string → copy the "Session pooler" one**, port `5432`,
   host `aws-...pooler.supabase.com`, user `postgres.<project-ref>`.
3. **Change the scheme** from `postgresql://` to `postgresql+asyncpg://`. This
   is the single most common way to lose an hour here: SQLAlchemy picks its
   driver from the scheme, and the dashboard gives you the psycopg form.
4. URL-encode anything exotic in the password (`@`, `/`, `#`, `?`).

Final shape:

```
postgresql+asyncpg://postgres.abcdefgh:PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres
```

### Why the session pooler and not the other two

| Option | Verdict |
| --- | --- |
| **Session pooler**, port 5432 | **Use this.** IPv4 on every plan, and it supports prepared statements, which asyncpg and SQLAlchemy both use by default. |
| Direct connection, `db.<ref>.supabase.co:5432` | IPv6-only unless you buy the IPv4 add-on. Render's outbound is IPv4, so this usually fails with a confusing DNS or timeout error. |
| Transaction pooler, port 6543 | Meant for serverless. It hands a different backend to each transaction, so prepared statements break *intermittently under load* — the worst failure mode to debug. If you must use it, set `DB_DISABLE_PREPARED_STATEMENTS=true`, which turns off both SQLAlchemy's cache and asyncpg's. |

### Row-level security: read this before assuming you have it

The architecture doc defers tenant isolation to Supabase RLS. **Moving to
Supabase does not give you that.** The connection above authenticates as
`postgres`, which is a superuser and **bypasses every RLS policy**. Today the
isolation guarantee is still exactly what it was locally: the repository layer,
plus the tests that try to read across tenants.

Getting real RLS is its own piece of work — a restricted application role, a
policy per tenant-owned table, and a session variable carrying the current
`client_id`. Worth doing before anyone outside your team can run SQL, and worth
not pretending you have in the meantime.

---

## 2. Upstash: Redis for the queue

Free tier, and the queue is the only thing using it.

1. Create a database at upstash.com — same region as Render (`us-east-1`).
2. Copy the **`rediss://`** URL from its details page. Note the two s's: it is
   TLS, which `arq` handles without any extra configuration.
3. Keep it for the next step.

The queue holds nothing you cannot lose — a job is one reply waiting to be
written — so Redis needs no backups. If it is briefly unreachable, arq retries
and the engine's holding-reply path means the customer still gets an answer.

## 3. Render: the service

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo. It reads `render.yaml` and
   proposes `frontdesk-web`, `frontdesk-worker` and `frontdesk-redis`.
3. It will prompt for every `sync: false` value. Fill in:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the Supabase session-pooler string from step 1 |
   | `REDIS_URL` | the Upstash `rediss://` URL from step 2 |
   | `ANTHROPIC_API_KEY` | your key |
   | `PUBLIC_BASE_URL` | **leave as a placeholder for now** — see below |
   | `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | blank until step 5; sends stay stubbed |
   | `GOOGLE_CREDENTIALS_JSON` | blank until step 6; the calendar stays an in-memory stub |
   | `RETELL_API_KEY` / `RETELL_WS_SECRET` | blank until step 8; voice stays off and `/health` says so |

4. Deploy. The web service runs `alembic upgrade head` as its pre-deploy step,
   so the schema is created on first boot. Watch the log for
   `Running upgrade  -> ...`.
5. **Now set `PUBLIC_BASE_URL`** to the URL Render just gave you —
   `https://frontdesk-web.onrender.com`, no trailing slash — on **both** the web
   service and the worker, and redeploy. This is a genuine chicken-and-egg: you
   cannot know the URL before the first deploy, and Twilio signature validation
   is computed against this exact string rather than against the incoming
   request. A mismatch rejects every webhook with a 403.
6. Check `https://<your-url>/health`. You want `"status":"ok"`,
   `"database":"ok"` and `"worker":{"embedded":true,"alive":true}`. A 503 means
   either the database string is wrong or the worker died — and the deploy will
   have failed rather than served broken pages, which is deliberate.
7. Create your console account. Render → `frontdesk-web` → **Shell**:
   ```
   python -m seeds.admin_user --email you@example.com
   ```
   Do **not** run `seeds.demo_shop` in production. Those are two fictional
   Chicago garages for local development; a real business is created through the
   console's "Add a business" wizard.

### What the blueprint deliberately does

- **Not the `free` plan.** A free Render service sleeps after inactivity and
  takes tens of seconds to wake. Twilio times out long before that, so the
  first customer text of a quiet morning would simply be lost. This is the one
  cost that cannot be cut.
- **One instance** (`WEB_CONCURRENCY=1`). Three things here are in-process: the
  tenant-router config cache, the admin login throttle, and now the queue
  worker itself. A second instance would run a second copy of all three —
  including the cron sweeps, which would mean two reminders per appointment.
  **Scaling out means splitting the worker out first**, not raising this
  number.
- **`--proxy-headers`.** Behind Render's proxy every request would otherwise
  appear to come from the proxy, and the signup form's per-address rate limit
  would treat all visitors as one.
- **`maxmemoryPolicy: noeviction`** on Redis. An evicted job is a customer who
  never got an answer; failing loudly and letting arq retry is better.
- **The worker gets the same credentials as the web service.** It answers
  customers and runs both cron sweeps. A credential only the web service has
  produces failures that happen only in the background, where nobody looks.

---

## 4. First real business

Through the console, not SQL:

1. `/admin` → **Add a business**. Name, owner's mobile, timezone, kind of
   business, languages. It is created in **shadow mode**.
2. Fill in its config: services, hours, price ranges, tone, handoff keywords.
   **With no price ranges the agent refuses to quote any number at all**, which
   is the right default — but it also means a half-filled business sounds
   evasive. Finish the page.
3. Read `/admin/shops/<id>/prompt` before going anywhere near a customer. That
   is literally what Claude will be sent. Check the hours, check the price
   ranges, check it reads like this business.
4. Leave it in shadow for the agreed trial. The owner approves every reply on
   WhatsApp; you both see exactly what it would have said.
5. Flip it to **live** when the drafts stop needing changes.

---

## 5. Twilio

1. Buy a number in the business's area code.
2. Point the number's **messaging webhook** at
   `POST https://<your-url>/webhooks/twilio/sms`.
3. Point the **voice status callback** at
   `POST https://<your-url>/webhooks/twilio/voice-status`. This is what triggers
   missed-call text-back; without it that feature silently never fires.
4. Put `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in both Render services.
   Signature validation switches itself on as soon as a token is present, and
   warns loudly in the log while it is skipped.
5. In the console, add the number as an endpoint on that business: SMS, and
   `voice` for the same number.
6. Ask the business to set **conditional call forwarding** on their own line —
   no answer after four rings, and busy — to the Twilio number.
7. 10DLC brand and campaign registration (see the clocks above). `STOP` and
   `HELP` are already handled automatically, which is one of the compliance
   boxes.

**The first real message is the moment of truth.** Everything up to here has
only ever run against a stub. Send one text from your own phone to the business
number and watch the worker log. Then check, in the console transcript, that the
inbound was stored, the reply was `sent` and not `blocked`, and the cost landed
in Usage.

---

## 6. Google Calendar

One service account for all businesses; no owner ever sees an OAuth screen.

1. Create a Google Cloud project, enable the **Google Calendar API**.
2. Create a **service account**, then a **JSON key** for it.
3. Paste the entire JSON file as `GOOGLE_CREDENTIALS_JSON` on both Render
   services. (`GOOGLE_CREDENTIALS_PATH` also works and is what local development
   uses, but a managed platform has no persistent disk to put a file on.)
4. For each business: share its calendar with the service account's email
   address, permission **"Make changes to events"**.
5. Put the calendar's id in the business's config.

With neither credential set, bookings still work end to end against an
in-memory calendar — they simply never appear in a real one. That is a fine
state to pilot in, and it is how every test runs.

---

## 7. WhatsApp

See `docs/whatsapp-templates.md`. The short version: conversations work the
moment the sender is verified, but anything *we* initiate outside the 24-hour
window — reminders, "it's ready", missed-call text-back — needs an approved
template and a small code change. Until then those go out over SMS, which has
no window.

---

## 8. Voice

See `docs/retell.md`. Retell answers the phone; Chichi stays the brain over a
custom-LLM websocket, so the same prompt, tools and booking rules that answer a
text answer a call, and the call lands in `conversations` and `messages` like
any other channel.

The short version: set `RETELL_API_KEY` and a generated `RETELL_WS_SECRET`, pick
a voice that covers every language your shops serve, run
`python -m seeds.retell_agent --create`, point a number at it, and add that
number as a `voice` endpoint for the shop in the console. `--show` reads the
agent back and shouts if its websocket URL no longer matches `PUBLIC_BASE_URL` -
which is what happens after this service moves and nobody re-pushed.

Voice needs the shop's status to be `live`. Shadow mode means the owner approves
each reply, and there is no approving anything inside a caller's patience, so a
shop that is not live has its calls declined and they fall back to the shop's own
line.

---

## When it breaks

- **Logs:** Render → service → Logs. Both processes log to stdout with
  `PYTHONUNBUFFERED=1`, so nothing is held back in a buffer.
- **A rollback is one click** on Render (Deploys → Rollback). It rolls back the
  *code*. It does **not** roll back a migration, and migrations here are written
  so that the previous code keeps working against the new schema — but a
  rollback across a destructive migration is still a restore-from-backup job,
  not a click. Supabase takes daily backups; check they are on.
- **No replies and no reminders at all?** The worker is what produces both.
  `/health` reports `worker.alive`; if it is false the service is returning 503
  and should already be restarting. The log line to look for at boot is
  `queue worker started in-process`.
- **Every webhook 403?** `PUBLIC_BASE_URL` does not match what Twilio is
  calling. It is compared as an exact string.
- **`prepared statement does not exist`, intermittently?** You are on the
  transaction pooler. Set `DB_DISABLE_PREPARED_STATEMENTS=true` or move to the
  session pooler.
- **A page 500s right after a deploy** but the API is fine? Templates missing
  from the image. The Dockerfile installs editable from the source tree
  specifically to prevent that, and `tests/test_deploy_readiness.py` asserts it.

## Roughly what it costs

| | Monthly |
| --- | --- |
| Render, one service (`0.5c-512mb`) | ~$7 |
| Upstash Redis | free tier |
| Supabase | free tier is enough for a pilot; ~$25 for Pro |
| Twilio number | ~$1-2, plus per-message |
| Retell number | $2, and only once voice is switched on |
| Claude | measured at ~$0.0004 per Haiku turn |

Voice is the one channel with a real per-minute cost: **$0.085/min** (Retell's
voice infra $0.055 + TTS $0.015 + telephony $0.015) on top of a fraction of a
cent of Claude. A three-minute call is therefore about 25c, against effectively
nothing for a text conversation - worth knowing before pricing a business that
takes a hundred calls a week. Retell gives $10 of trial credit, so the first
calls cost nothing.

So roughly **$8-10/month plus usage** to run the whole platform for the first
few businesses. The landing page's placeholder price is $249/month per
business.

## Still manual after this

- Billing. Nothing charges anyone — see `docs/subscriptions.md`.
- Row-level security, as above.
- No CI. Tests run locally; nothing stops a red build from deploying. A GitHub
  Action running `pytest` and `ruff` on push is twenty lines and worth having
  before a second person touches this.

---

## When to split the worker out again

The single service is a cost decision for a pilot, not an architecture. Split
them when any of these becomes true:

- You want more than one web instance (for availability, or for load).
- A slow or stuck job starts delaying webhook acknowledgements — watch for
  Twilio retries in the log.
- The worker's crashes start taking the webhook endpoint down with them often
  enough to notice.

Splitting is configuration, not a rewrite, because `app/queue/worker.py` never
stopped being a standalone entrypoint:

1. Set `RUN_WORKER_IN_PROCESS=false` on the web service.
2. Add a second Render service to `render.yaml`, `type: worker`, same
   `dockerfilePath`, with `dockerCommand: python -m arq app.queue.worker.WorkerSettings`.
3. Give it **the same environment variables** as the web service. A credential
   the web service has and the worker does not produces failures that only
   happen in the background, where nobody is looking.
4. Deploy. Nothing in the application code changes.

The git history has the two-service blueprint if you want to copy it back
verbatim: it is the version of `render.yaml` in the initial commit.
