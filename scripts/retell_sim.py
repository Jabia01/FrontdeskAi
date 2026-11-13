"""Pretend to be Retell, so a phone call is testable without a phone.

    python scripts/retell_sim.py --to +13125557000 --say "what time do you open?"

It does exactly what Retell does, in order: posts the inbound webhook (signed
with the real signature, so the verification path is exercised rather than
bypassed), opens the custom-LLM websocket with the call id appended, reads the
greeting, sends each utterance as a `response_required` with a growing
transcript, then posts `call_ended`.

What it does not do is speech. The point is to prove the bridge, the tenant
resolution, the turn loop and the record - everything that is ours. Retell's own
transcription and voice are Retell's problem and cannot be tested from here
anyway.

Needs the app running and `RETELL_API_KEY` / `RETELL_WS_SECRET` set to whatever
the server is using.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

import httpx
import websockets

sys.path.insert(0, ".")

from app.config import get_settings
from app.gateway.retell_signature import sign


async def post_signed(base: str, path: str, body: dict, api_key: str) -> httpx.Response:
    raw = json.dumps(body).encode()
    async with httpx.AsyncClient(timeout=30.0) as http:
        return await http.post(
            base.rstrip("/") + path,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Retell-Signature": sign(raw, api_key),
            },
        )


async def run(base: str, to: str, frm: str, lines: list[str]) -> int:
    settings = get_settings()
    api_key = settings.retell_api_key
    secret = settings.retell_ws_secret
    if not api_key or not secret:
        print("RETELL_API_KEY and RETELL_WS_SECRET must be set.")
        return 1

    call_id = f"sim_{uuid.uuid4().hex[:16]}"
    print(f"call {call_id}\n")

    # ---- 1. the phone rings -------------------------------------------------
    response = await post_signed(
        base,
        "/webhooks/retell/inbound",
        {
            "event": "call_inbound",
            "call_inbound": {"call_id": call_id, "to_number": to, "from_number": frm},
        },
        api_key,
    )
    if response.status_code != 200:
        print(f"inbound webhook failed: {response.status_code} {response.text}")
        return 1
    decision = response.json().get("call_inbound", {})
    if decision.get("reject"):
        print(
            "the call was DECLINED. Either no shop has a `voice` endpoint for "
            f"{to}, or its status is not `live`."
        )
        return 1
    print(f"accepted -> {decision.get('dynamic_variables', {}).get('shop_name')}\n")

    # ---- 2. the call itself -------------------------------------------------
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{ws_base.rstrip('/')}/ws/retell/{secret}/{call_id}"

    transcript: list[dict] = []
    async with websockets.connect(url) as ws:
        response_id = 0
        while True:
            event = json.loads(await ws.recv())
            if event.get("response_type") == "config":
                continue
            if event.get("response_type") == "response":
                print(f"  chichi > {event['content']}")
                transcript.append({"role": "agent", "content": event["content"]})
                if event.get("transfer_number"):
                    print(f"  [transferring to {event['transfer_number']}]")
                break
            break

        for line in lines:
            print(f"\n  caller > {line}")
            transcript.append({"role": "user", "content": line})
            response_id += 1
            started = time.monotonic()
            await ws.send(
                json.dumps(
                    {
                        "interaction_type": "response_required",
                        "response_id": response_id,
                        "transcript": list(transcript),
                    }
                )
            )
            # Retell concatenates every chunk sent against one response_id and
            # speaks them in order, so an incomplete chunk is a held beat and
            # not a separate turn. Accumulating here is what makes that visible.
            said = ""
            while True:
                event = json.loads(await ws.recv())
                if event.get("response_type") == "ping_pong":
                    await ws.send(
                        json.dumps(
                            {
                                "response_type": "ping_pong",
                                "timestamp": event.get("timestamp"),
                            }
                        )
                    )
                    continue
                if event.get("response_type") != "response":
                    continue
                if event.get("response_id") != response_id:
                    continue
                took = time.monotonic() - started
                said += event.get("content") or ""
                if not event.get("content_complete"):
                    print(f"  chichi > {event['content']!r} (held beat)   [{took:.1f}s]")
                    continue
                print(f"  chichi > {said}   [{took:.1f}s to finish]")
                transcript.append({"role": "agent", "content": event["content"]})
                if event.get("transfer_number"):
                    print(f"  [transferring to {event['transfer_number']}]")
                break

    # ---- 3. the call ends ---------------------------------------------------
    response = await post_signed(
        base,
        "/webhooks/retell/events",
        {
            "event": "call_ended",
            "call": {
                "call_id": call_id,
                "duration_ms": 60_000,
                "transcript_object": transcript,
                "call_analysis": {"call_summary": "Simulated call."},
            },
        },
        api_key,
    )
    print(f"\ncall_ended -> {response.status_code} {response.text}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a Retell phone call.")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--to", required=True, help="the shop's voice endpoint number")
    parser.add_argument("--from", dest="frm", default="+13125551234")
    parser.add_argument(
        "--say",
        action="append",
        default=None,
        help="an utterance; repeat for a multi-turn call",
    )
    args = parser.parse_args()
    lines = args.say or ["hi, what time do you open tomorrow?"]
    sys.exit(asyncio.run(run(args.base, args.to, args.frm, lines)))


if __name__ == "__main__":
    main()
