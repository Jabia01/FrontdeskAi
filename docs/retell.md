# Voice: Retell

Chichi answers the phone through [Retell](https://retellai.com). Retell owns the
telephony, the speech recognition and the voice. Chichi owns the thinking.

## Why this split

Retell can run the whole thing - its own LLM, its own prompt, its own tools -
and that was the option not taken. Two reasons.

**One brain.** The prompt, the prices, the hours, the booking rules, the handoff
keywords and the language policy already live in `client_config` and
`app/agent/`. Pushing them into a Retell agent config would create a second
place to keep them right, per shop, forever. With the custom-LLM websocket the
same `run_agent` that answers a text answers a call, and a voice call lands in
`conversations` and `messages` like any other channel - so the dashboard, the
daily summary and the reminder sweep needed no new code at all.

**It is cheaper.** Letting Retell's LLM drive costs about $0.11/min. Custom LLM
costs voice infra ($0.055) + TTS ($0.015) + telephony ($0.015) = **$0.085/min**,
and Chichi's own Claude call is a fraction of a cent per turn. Retell charges
nothing extra for custom LLM.

Retell charges $2/month per number and gives $10 of trial credit, so the first
calls cost nothing.

## How a call flows

```
caller dials the shop's forwarded number
        |
        v
[1] POST /webhooks/retell/inbound          <- the phone is still ringing
        resolve to_number -> channel_endpoints(voice) -> client_id
        refuse unless the shop's status is `live`
        write a `calls` row keyed on Retell's call_id
        answer with dynamic_variables + metadata
        |
        v
[2] WS  /ws/retell/<secret>/<call_id>      <- the call itself
        authenticate: secret in the path AND the call_id must match [1]
        speak the greeting immediately (no model call)
        per turn: run_agent(channel="voice") -> one `response` event
        |
        v
[3] POST /webhooks/retell/events           <- call_ended / call_analyzed
        fill in duration, recording, Retell's summary
        write the corrected transcript into `messages`
```

Step 1 is what makes multi-tenancy work. It runs before the call connects and
Retell gives us the call id there, so the websocket in step 2 authenticates
against a row we wrote rather than against metadata handed back to us.

## Setting it up

You need: a Retell account, a number, and this service deployed somewhere Retell
can reach (websockets included - Render supports them).

**1. Generate the websocket secret.**

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it as `RETELL_WS_SECRET`, along with `RETELL_API_KEY` from Retell's
dashboard. Both are `sync: false` in `render.yaml`, so Render prompts for them.

**2. Pick a voice, and a voice model.**

```bash
.venv/Scripts/python.exe -m seeds.retell_agent --list-voices
```

This is the one real constraint of the integration, and it is worth being
precise about because Retell's API is less helpful here than you would expect:

- A Retell **voice object carries no language list at all** - only an `accent`.
  So you cannot read language coverage off the voice, and Retell publishes no
  per-model language table either.
- Coverage is a property of the **provider and its `voice_model`**.
  `RETELL_VOICE_MODEL=eleven_multilingual_v2` (ElevenLabs) is the safe
  multilingual choice. It costs **$0.060/min** of TTS instead of $0.015, which
  takes a call from $0.085/min to $0.13/min.
- Leaving `RETELL_VOICE_MODEL` blank uses Retell's default for that voice -
  cheapest, and usually English-first.
- Retell also cannot stitch coverage together across providers mid-call, so the
  set in `enabled_languages` is a promise one provider has to keep alone.

The honest end of this is a test call in each language you serve. The listing
prints a `preview_audio_url` per voice, which is the fastest way to hear one
before committing.

**3. Create the agent.**

```bash
python -m seeds.retell_agent --create
```

One agent serves every tenant - the shop is resolved per call. It prints the
agent id; set it as `RETELL_AGENT_ID`.

The agent's language list is the **union of every shop's `enabled_languages`**,
read from the database. So after onboarding a shop that speaks a new language,
re-push it:

```bash
python -m seeds.retell_agent --update
```

**4. Point a number at it.**

```bash
python -m seeds.retell_agent --point-number +13125551234
```

**5. Map that number to a shop** in the console (Endpoints -> add a `voice`
endpoint with the same number). Without this the inbound webhook cannot find a
tenant and the call is declined. The number you register here is the *Retell*
number - the one whose `to_number` the webhook carries - even if the shop
forwards to it through Twilio.

**6. Check it.**

```bash
python -m seeds.retell_agent --show
```

This reads the agent back from Retell and shouts if the websocket URL it has
differs from the one this service would serve - which is what happens after
`PUBLIC_BASE_URL` changes and nobody re-pushed.

`/health` reports `voice.configured` and whether signature validation is on.

## How the shop's phone should be wired

Point the shop's existing line to forward to the Retell number **on no answer**.
A human picks up if they are free; Chichi catches everything else. That is the
same forwarding the missed-call text-back already used, so switching a shop to
voice is a change of forward target, not a new thing for them to learn.

Missed-call text-back stays as the layer underneath: if Retell declines the call
(the shop is not `live`) or never answers, the Twilio call-status webhook still
texts the caller back.

## What voice deliberately does not do

**Shadow mode.** Shadow mode means the owner approves each reply before it goes
out. There is no approving anything in the second and a half before a caller
hears silence, so a shop that is not `live` has its calls **declined** - they
fall through to the shop's own line rather than reaching an AI the owner has not
signed off. `ANSWERABLE` in `app/core/voice.py` is the whole rule.

**Out-of-band sends.** The voice adapter's `send()` always refuses, with
`blocked=True`. A spoken reply goes down the live websocket; there is no later
API call that could deliver one. A shop misconfigured with
`missed_call_channel: voice` therefore shows up as blocked in the dashboard
rather than as quietly fine.

**Streaming a half-finished sentence.** Chichi's reply arrives from the model as
one validated object - it has to, because the same call decides the language and
whether to hand off - so there is no earlier moment at which a first syllable
could honestly be spoken. One `response` event carries the whole turn.

## Escalation

On text, a handoff means "a human will reply". On a live call we can do better:
`escalate()` **transfers the caller to `clients.owner_phone`**. With no owner
number it promises a callback instead, and because that is a commitment rather
than an apology, the owner alert is sent by the same function - every escalating
path goes through it, so neither can be forgotten. One alert per call, however
many times a turn escalates.

## Security

Two independent layers, because a voice bridge anyone can open is a stranger
talking to a shop's customers:

1. `RETELL_WS_SECRET` sits in the websocket path, in front of the call id.
2. The call id must match a `calls` row, and only a signature-verified inbound
   webhook writes one.

Both webhooks verify `X-Retell-Signature` (HMAC-SHA256 over the body plus the
header's own timestamp, keyed with the API key, inside a five-minute window,
compared in constant time). One wrinkle is documented in
`app/gateway/retell_signature.py`: Retell's docs sign the raw body while their
SDK examples sign a re-serialisation of it, so verification accepts either
canonical form of the *same parsed body*. Changing a value still fails.

Nothing shop-specific is pushed to Retell beyond the shop's name and timezone -
`test_it_never_ships_the_prompt_or_the_prices_to_retell` pins that.

## Costs to watch

Voice is the expensive channel: a three-minute call is roughly 25c of Retell,
against a fraction of a cent for a text conversation. Two guards are in place -
`RETELL_MAX_CALL_SECONDS` (default 600) caps a runaway call, and
`end_call_after_silence_ms` hangs up on a line nobody is on. Every turn's Claude
cost is written to `usage_events`, so gross margin per shop stays visible.

## Testing without a phone

`tests/test_voice.py` drives the real `retell_bridge` through a scripted fake
socket, so the protocol handling - greeting, keepalives, interruption,
transfers, the record written on disconnect - is covered without a call.

For an end-to-end check against a running server, `scripts/retell_sim.py` speaks
the websocket protocol as Retell would, signing its webhooks with the real
verification path rather than bypassing it.
