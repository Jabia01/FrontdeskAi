# Deploying this, and connecting a real business to it

Written for whoever runs the deploy — probably you, once. Follow it in order;
the ordering is the point, because three steps involve waiting on other
companies and they should be started on day one.

**Target:** Render for the two processes and Redis, Supabase for Postgres.
`Dockerfile` and `render.yaml` are committed, and the image has been built and
run locally against the real Postgres — migrations, the worker and the web
service all start, and a wrong `DATABASE_URL` correctly fails the health check.

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

## 2. Render: the two processes

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo. It reads `render.yaml` and
   proposes `frontdesk-web`, `frontdesk-worker` and `frontdesk-redis`.
3. It will prompt for every `sync: false` value. Fill in:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the Supabase string from step 1 |
   | `ANTHROPIC_API_KEY` | your key |
   | `PUBLIC_BASE_URL` | **leave as a placeholder for now** — see below |
   | `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | blank until step 4; sends stay stubbed |
   | `GOOGLE_CREDENTIALS_JSON` | blank until step 5; the calendar stays an in-memory stub |

   `REDIS_URL` is wired automatically from the Key Value service.

4. Deploy. The web service runs `alembic upgrade head` as its pre-deploy step,
   so the schema is created on first boot. Watch the log for
   `Running upgrade  -> ...`.
5. **Now set `PUBLIC_BASE_URL`** to the URL Render just gave you —
   `https://frontdesk-web.onrender.com`, no trailing slash — on **both** the web
   service and the worker, and redeploy. This is a genuine chicken-and-egg: you
   cannot know the URL before the first deploy, and Twilio signature validation
   is computed against this exact string rather than against the incoming
   request. A mismatch rejects every webhook with a 403.
6. Check `https://<your-url>/health`. You want `"status":"ok"` and
   `"database":"ok"`. A 503 means the database string is wrong — and the deploy
   will have failed rather than served broken pages, which is deliberate.
7. Create your console account. Render → `frontdesk-web` → **Shell**:
   ```
   python -m seeds.admin_user --email you@example.com
   ```
   Do **not** run `seeds.demo_shop` in production. Those are two fictional
   Chicago garages for local development; a real business is created through the
   console's "Add a business" wizard.

### What the blueprint deliberately does

- **One web instance** (`WEB_CONCURRENCY=1`). The tenant-router config cache and
  the admin login throttle are both in-process, so a second instance holds its
  own copy of each. Neither is worth moving to Redis for a pilot; both are
  documented where they live. If you scale out, move the login throttle first —
  the config cache being per-instance only means a config edit takes up to a
  minute longer to apply on one of them.
- **`--proxy-headers`.** Behind Render's proxy every request would otherwise
  appear to come from the proxy, and the signup form's per-address rate limit
  would treat all visitors as one.
- **`maxmemoryPolicy: noeviction`** on Redis. An evicted job is a customer who
  never got an answer; failing loudly and letting arq retry is better.
- **The worker gets the same credentials as the web service.** It answers
  customers and runs both cron sweeps. A credential only the web service has
  produces failures that happen only in the background, where nobody looks.

---

## 3. First real business

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

## 4. Twilio

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

## 5. Google Calendar

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

## 6. WhatsApp

See `docs/whatsapp-templates.md`. The short version: conversations work the
moment the sender is verified, but anything *we* initiate outside the 24-hour
window — reminders, "it's ready", missed-call text-back — needs an approved
template and a small code change. Until then those go out over SMS, which has
no window.

---

## When it breaks

- **Logs:** Render → service → Logs. Both processes log to stdout with
  `PYTHONUNBUFFERED=1`, so nothing is held back in a buffer.
- **A rollback is one click** on Render (Deploys → Rollback). It rolls back the
  *code*. It does **not** roll back a migration, and migrations here are written
  so that the previous code keeps working against the new schema — but a
  rollback across a destructive migration is still a restore-from-backup job,
  not a click. Supabase takes daily backups; check they are on.
- **No reminders going out?** The worker is what runs the sweeps. Check it is
  running at all, then `/admin/audit` and the worker log.
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
| Render web (`0.5c-512mb`) | ~$7 |
| Render worker (`0.5c-512mb`) | ~$7 |
| Render Key Value (`256mb`) | ~$10 |
| Supabase | free tier is enough for a pilot; ~$25 for Pro |
| Twilio number | ~$1-2, plus per-message |
| Claude | measured at ~$0.0004 per Haiku turn |

So roughly **$25-35/month plus usage** to run the whole platform for the first
few businesses. The landing page's placeholder price is $249/month per
business.

## Still manual after this

- Billing. Nothing charges anyone — see `docs/subscriptions.md`.
- Row-level security, as above.
- No CI. Tests run locally; nothing stops a red build from deploying. A GitHub
  Action running `pytest` and `ruff` on push is twenty lines and worth having
  before a second person touches this.
