"""Retell gateway: two webhooks and one websocket.

The webhooks keep the gateway's standing contract - verify, resolve, persist,
answer fast. The websocket breaks it on purpose, because it *is* the call: there
is nothing slower to defer the work to, so the agent runs here.

Authentication comes in two independent layers, because a voice bridge that
anyone can open is a stranger talking to a shop's customers:

1. A secret in the websocket path. Retell appends the call id to whatever URL we
   register, so the secret sits in front of it and never reaches a query string
   or a log line as a parameter.
2. The call id must match a `calls` row, and only a signature-verified inbound
   webhook writes one. Guessing a call id is therefore not enough.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from app.channels.base import get_adapter
from app.config import get_settings
from app.core import voice
from app.core.voice import CallNotRegistered, CallState, VoiceReply
from app.db import session_scope
from app.gateway.retell_signature import SignatureError, verify
from app.models import CallOutcome, Channel
from app.repo import finish_call

log = logging.getLogger(__name__)

router = APIRouter()

#: Websocket close codes. 1008 is "policy violation", which is what a failed
#: authentication is at this layer.
WS_POLICY = 1008
WS_NORMAL = 1000

#: How long a turn still running when the socket closes gets to finish. Long
#: enough for a booking to commit, short enough that a hung call cannot pin a
#: worker open.
TURN_DRAIN_SECONDS = 5.0

#: How long a caller waits in silence before hearing a held beat. Short enough
#: that it lands before the pause feels like a dead line, long enough that a
#: plain question - which answers in a second or so - is not padded with a
#: pointless "one moment".
FILLER_AFTER_SECONDS = 1.3


# ------------------------------------------------------------------- the webhooks


async def _verified_body(request: Request, signature: str | None) -> dict:
    """Raw body in, parsed body out, 403 unless Retell really sent it."""
    raw = await request.body()
    settings = get_settings()

    if settings.retell_validate_signature and settings.retell_api_key:
        try:
            verify(raw, signature, settings.retell_api_key)
        except SignatureError as exc:
            log.warning("rejected a Retell webhook: %s", exc)
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    else:
        # Loud, because this is the configuration that lets anyone post a call
        # into a shop's history.
        log.warning(
            "Retell signature validation is OFF - set RETELL_API_KEY and "
            "RETELL_VALIDATE_SIGNATURE=true before this takes a real call"
        )

    try:
        parsed = json.loads(raw or b"null")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body must be a JSON object")
    return parsed


@router.post("/webhooks/retell/inbound")
async def retell_inbound(
    request: Request,
    x_retell_signature: str | None = Header(default=None),
) -> dict:
    """Runs while the phone is still ringing.

    Retell waits up to ten seconds for this and retries twice, so it does the
    minimum: resolve the shop, write the call row the websocket will
    authenticate against, and answer. A shop that cannot answer live calls is
    declined here, which sends the caller to whatever fallback the number has -
    their own line - rather than to an AI nobody approved.
    """
    body = await _verified_body(request, x_retell_signature)
    payload = body.get("call_inbound") or {}

    try:
        inbound = get_adapter(Channel.voice).parse_inbound(
            {
                "to_number": str(payload.get("to_number") or ""),
                "from_number": str(payload.get("from_number") or ""),
                "call_id": str(payload.get("call_id") or ""),
            }
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    decision = await voice.register_inbound_call(
        call_id=inbound.provider_msg_id or "",
        to_number=inbound.external_id,
        from_number=inbound.from_number,
    )

    if not decision.accept:
        log.info("declining Retell call %s: %s", inbound.provider_msg_id, decision.reason)
        # Only `reject` goes back. Retell still validates sibling fields on a
        # rejection, so sending dynamic variables alongside it risks turning a
        # clean decline into a validation error.
        return {"call_inbound": {"reject": True}}

    return {
        "call_inbound": {
            "dynamic_variables": decision.dynamic_variables,
            "metadata": decision.metadata,
        }
    }


@router.post("/webhooks/retell/events")
async def retell_events(
    request: Request,
    x_retell_signature: str | None = Header(default=None),
) -> dict:
    """call_started / call_ended / call_analyzed.

    `call_ended` is where a call becomes a record: duration, recording, and the
    transcript as finally corrected by the speech recogniser. `call_analyzed`
    arrives later with Retell's own summary, which is kept because it is the
    thing an owner actually reads in the morning.
    """
    body = await _verified_body(request, x_retell_signature)
    event = str(body.get("event") or "").strip()
    call = body.get("call") or {}
    call_id = str(call.get("call_id") or "").strip()

    if not call_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "event is missing call.call_id")

    if event == "call_started":
        return {"status": "ok"}

    if event not in {"call_ended", "call_analyzed"}:
        log.info("ignoring unhandled Retell event %r", event)
        return {"status": "ignored", "event": event}

    async with session_scope() as session:
        row = await voice.find_call(session, call_id)
        if row is None:
            # A call for a number that is pointed at our agent but not at our
            # inbound webhook. Worth saying out loud: it means a shop's calls
            # are reaching an agent with no tenant behind it.
            log.warning("Retell %s for an unregistered call %s", event, call_id)
            return {"status": "unknown_call"}
        client_id = row.client_id

    transcript = call.get("transcript_object") or []
    spoken = voice.latest_user_text(transcript)
    analysis = call.get("call_analysis") or {}

    duration_ms = call.get("duration_ms")
    duration = int(duration_ms // 1000) if isinstance(duration_ms, (int, float)) else None

    async with session_scope() as session:
        await finish_call(
            session,
            client_id,
            call_id,
            # A call where the caller never said anything usable is a missed
            # call however it ended, and the dashboard should say so.
            outcome=CallOutcome.answered_ai if spoken else CallOutcome.missed,
            duration=duration,
            recording_url=call.get("recording_url") or None,
            summary=analysis.get("call_summary") or None,
        )

    written = await voice.persist_transcript(client_id, call_id, transcript)
    return {"status": "ok", "event": event, "turns_written": written}


# ----------------------------------------------------------------- the websocket


def _secret_ok(candidate: str) -> bool:
    configured = get_settings().retell_ws_secret
    if not configured:
        return False
    return hmac.compare_digest(candidate, configured)


async def _send(ws: WebSocket, payload: dict[str, Any]) -> None:
    await ws.send_text(json.dumps(payload, ensure_ascii=False))


async def _speak(
    ws: WebSocket, state: CallState, response_id: int, reply: VoiceReply
) -> None:
    """One `response` event carrying a whole turn.

    Sent as a single complete chunk rather than streamed. Chichi's reply arrives
    from the model as one validated object - it has to, because the same call
    decides the language and whether to hand off - so there is no earlier moment
    at which a first syllable could honestly be spoken.

    What was spoken is appended to the call's transcript. Retell's next frame
    includes our previous replies, but there is no next frame after the last
    one - so without this the final thing Chichi said is missing from the
    record, which is the turn an owner most wants to read.
    """
    payload: dict[str, Any] = {
        "response_type": "response",
        "response_id": response_id,
        "content": reply.text,
        "content_complete": True,
    }
    if reply.transfer_number:
        payload["transfer_number"] = reply.transfer_number
    if reply.end_call:
        payload["end_call"] = True
    await _send(ws, payload)
    state.transcript = [*state.transcript, {"role": "agent", "content": reply.text}]


@router.websocket("/ws/retell/{secret}/{call_id}")
async def retell_bridge(ws: WebSocket, secret: str, call_id: str) -> None:
    if not _secret_ok(secret):
        # Refused before `accept`, so an unauthenticated peer never gets an
        # open socket to send anything down.
        log.warning("rejected a Retell websocket with a bad secret for call %s", call_id)
        await ws.close(code=WS_POLICY)
        return

    try:
        state = await voice.open_call(call_id)
    except CallNotRegistered as exc:
        log.warning("rejected a Retell websocket: %s", exc)
        await ws.close(code=WS_POLICY)
        return

    await ws.accept()
    log.info(
        "voice call %s connected for %s (%s)",
        call_id,
        state.shop.name,
        state.customer_phone,
    )

    # auto_reconnect makes Retell keep the socket alive through a blip, at the
    # cost of us having to answer its keepalives - see `ping_pong` below.
    await _send(
        ws,
        {
            "response_type": "config",
            "config": {"auto_reconnect": True, "call_details": False},
        },
    )
    await _speak(ws, state, 0, VoiceReply(text=voice.greeting(state.shop)))

    turn: asyncio.Task | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                event = json.loads(raw)
            except ValueError:
                log.warning("ignoring a non-JSON frame on call %s", call_id)
                continue
            if not isinstance(event, dict):
                continue

            kind = str(event.get("interaction_type") or "")

            if kind == "ping_pong":
                await _send(
                    ws,
                    {"response_type": "ping_pong", "timestamp": event.get("timestamp")},
                )
                continue

            if kind == "update_only":
                state.transcript = list(event.get("transcript") or state.transcript)
                continue

            if kind == "call_details":
                continue

            if kind not in {"response_required", "reminder_required"}:
                continue

            response_id = int(event.get("response_id") or 0)
            transcript = list(event.get("transcript") or [])
            # Recorded the moment it arrives, not when the turn finishes. A call
            # that drops mid-answer still has to leave a record of what was
            # said, and the turn that would have stored it is the very thing
            # that gets cancelled.
            if transcript:
                state.transcript = transcript

            # The caller talked over us. Retell has already discarded whatever
            # the old task was going to say, so finishing it would only cost a
            # model call and risk speaking a stale answer.
            if turn is not None and not turn.done():
                turn.cancel()

            if kind == "reminder_required":
                # Deliberately not a model call: a nudge at a silence must be
                # instant, and "are you still there?" needs no thought.
                await _speak(
                    ws, state, response_id, VoiceReply(text=voice.nudge(state.language))
                )
                continue

            turn = asyncio.create_task(
                _run_turn(ws, state, transcript, response_id)
            )

    except WebSocketDisconnect:
        log.info("voice call %s disconnected after %d turns", call_id, state.turns)
    except Exception:
        log.exception("voice bridge failed on call %s", call_id)
        with contextlib.suppress(Exception):
            await ws.close(code=WS_NORMAL)
    finally:
        if turn is not None and not turn.done():
            # Nothing more can be spoken - the socket is gone. But a turn part
            # way through `book_appointment` deserves to finish and commit
            # rather than be torn down mid-transaction, so it gets a moment
            # before it is cancelled outright. This is the opposite of the
            # interruption case above, where discarding the work is the point.
            _, pending = await asyncio.wait({turn}, timeout=TURN_DRAIN_SECONDS)
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        # The socket closing may be the last thing that happens to this call, so
        # the record is written here as well as from `call_ended`. Both are
        # idempotent; whichever arrives first wins.
        with contextlib.suppress(Exception):
            await voice.persist_transcript(
                state.client_id,
                call_id,
                state.transcript,
                conversation_id=state.conversation_id,
                language=state.language,
            )


async def _run_turn(
    ws: WebSocket, state: CallState, transcript: list[dict], response_id: int
) -> None:
    """One turn, in a task so it can be cancelled when the caller interrupts.

    A turn that needs a tool - checking the calendar, usually - takes two model
    round trips and can run to several seconds. On a phone call that silence
    reads as a dropped line, so if the answer is not ready quickly the caller
    gets a held beat first: an incomplete chunk against the same `response_id`,
    which Retell speaks and then follows with the real reply. It is not a
    second turn and it is not recorded as one - it is the "one moment" a human
    receptionist gives without thinking about it.
    """
    thinking = asyncio.create_task(voice.answer(state, transcript))
    try:
        done, _ = await asyncio.wait({thinking}, timeout=FILLER_AFTER_SECONDS)
        if not done:
            with contextlib.suppress(Exception):
                await _send(
                    ws,
                    {
                        "response_type": "response",
                        "response_id": response_id,
                        "content": voice.filler(state.language) + " ",
                        "content_complete": False,
                    },
                )
        reply = await thinking
    except asyncio.CancelledError:
        thinking.cancel()
        raise
    except Exception:
        log.exception("voice turn failed on call %s", state.retell_call_id)
        # The caller is told someone will ring them back, so the owner has to
        # hear about it - that is what `trouble_reply` does before answering.
        reply = await voice.trouble_reply(state)

    with contextlib.suppress(Exception):
        await _speak(ws, state, response_id, reply)
