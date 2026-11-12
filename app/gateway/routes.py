"""Webhook gateway.

Contract for every route here: verify, dedupe, persist, acknowledge in well under
a second, and let the worker do the slow work. No route in this module ever
calls Claude or sends a reply.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.channels.base import get_adapter
from app.channels.twilio_base import strip_channel_prefix
from app.core.ingest import UnknownEndpointError, ingest
from app.core.missed_call import is_missed, record_missed_call
from app.db import session_scope
from app.gateway.signature import validate_twilio_signature
from app.models import Channel
from app.queue.client import enqueue_handle_inbound, enqueue_missed_call
from app.repo import (
    find_customer_by_phone,
    get_endpoint,
    latest_conversation,
    outbound_for_customer_since,
)

log = logging.getLogger(__name__)

router = APIRouter()

TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


async def _accept(channel: Channel, payload: dict) -> dict:
    """Shared tail of every inbound route."""
    adapter = get_adapter(channel)
    try:
        inbound = adapter.parse_inbound(payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    try:
        result = await ingest(inbound)
    except UnknownEndpointError as exc:
        # 404 rather than 500: the provider is fine, our mapping is missing.
        log.warning("%s", exc)
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    if result.duplicate:
        return {"status": "duplicate", "conversation_id": str(result.conversation_id)}

    assert result.message_id is not None
    await enqueue_handle_inbound(result.client_id, result.message_id)
    return {
        "status": "queued",
        "conversation_id": str(result.conversation_id),
        "message_id": str(result.message_id),
    }


# ----------------------------------------------------------------------- twilio


@router.post("/webhooks/twilio/sms", include_in_schema=True)
async def twilio_sms(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not validate_twilio_signature(
        "/webhooks/twilio/sms", form, x_twilio_signature
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid Twilio signature")

    await _accept(Channel.sms, form)
    # Empty TwiML: we reply asynchronously via the REST API, not in this response.
    return Response(content=TWIML_EMPTY, media_type="application/xml")


@router.post("/webhooks/twilio/whatsapp")
async def twilio_whatsapp(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not validate_twilio_signature(
        "/webhooks/twilio/whatsapp", form, x_twilio_signature
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid Twilio signature")

    await _accept(Channel.whatsapp, form)
    return Response(content=TWIML_EMPTY, media_type="application/xml")


@router.post("/webhooks/twilio/voice-status")
async def twilio_voice_status(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    """Call status callback - the trigger for missed-call text-back.

    The shop's own line forwards here on no-answer or busy. In v1 this number
    never answers: it logs the call and texts the caller back within seconds.
    """
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not validate_twilio_signature(
        "/webhooks/twilio/voice-status", form, x_twilio_signature
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid Twilio signature")

    to = (form.get("To") or "").strip()
    from_number = strip_channel_prefix(form.get("From") or "")
    call_status = form.get("CallStatus") or ""
    call_sid = (form.get("CallSid") or "").strip() or None

    if not to or not from_number:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing To or From")

    if not is_missed(call_status, form.get("CallDuration")):
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    async with session_scope() as session:
        # The forwarding number is registered as the shop's voice endpoint, which
        # is also what the v2 voice adapter will answer on.
        endpoint = await get_endpoint(session, Channel.voice, to)
        if endpoint is None:
            endpoint = await get_endpoint(session, Channel.sms, to)
        if endpoint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no shop mapped to {to}"
            )
        client_id = endpoint.client_id

    logged = await record_missed_call(client_id, from_number, call_sid)
    if logged["status"] != "duplicate":
        await enqueue_missed_call(client_id, uuid.UUID(logged["call_id"]))
    else:
        log.info("duplicate call status webhook ignored: %s", call_sid)

    return Response(content=TWIML_EMPTY, media_type="application/xml")


# -------------------------------------------------------------------- simulator


@router.post("/webhooks/sim")
async def simulator_inbound(payload: dict) -> dict:
    """Local dev entry point. Same path through the core as a real webhook."""
    return await _accept(Channel.simulator, {k: str(v) for k, v in payload.items()})


@router.post("/webhooks/sim/call")
async def simulator_missed_call(payload: dict) -> dict:
    """Simulate an unanswered call, so text-back is testable without Twilio.

    Takes the shop's simulator `external_id` and resolves the shop from it, then
    runs exactly the same record-and-enqueue path as the Twilio callback.
    """
    external_id = str(payload.get("external_id") or "").strip()
    from_number = str(payload.get("from_number") or "").strip()
    if not external_id or not from_number:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "external_id and from_number are required"
        )

    async with session_scope() as session:
        endpoint = await get_endpoint(session, Channel.simulator, external_id)
        if endpoint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no shop mapped to {external_id}"
            )
        client_id = endpoint.client_id

    logged = await record_missed_call(
        client_id, from_number, str(payload.get("call_sid") or "") or None
    )
    if logged["status"] == "duplicate":
        return {"status": "duplicate", "call_id": logged["call_id"]}

    await enqueue_missed_call(client_id, uuid.UUID(logged["call_id"]))
    return {"status": "queued", "call_id": logged["call_id"]}


@router.get("/webhooks/sim/poll")
async def simulator_poll(
    external_id: str, from_number: str, after: str | None = None
) -> dict:
    """Outbound messages for this caller, newer than the `after` cursor.

    Reads the persisted rows rather than process memory, because the worker that
    sends the reply is a different process from the one serving this request.
    """
    cursor: int | None = None
    if after:
        try:
            cursor = int(after)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"bad 'after' cursor: {after!r}"
            ) from None

    async with session_scope() as session:
        endpoint = await get_endpoint(session, Channel.simulator, external_id)
        if endpoint is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no shop mapped to {external_id}"
            )

        customer = await find_customer_by_phone(
            session, endpoint.client_id, from_number
        )
        if customer is None:
            # Same shape as a populated response, so clients need no special case.
            return {
                "messages": [],
                "cursor": cursor,
                "conversation_status": "ai",
                "short_code": None,
            }

        conversation = await latest_conversation(
            session, endpoint.client_id, customer.id, Channel.simulator
        )

        # Across channels on purpose: a missed-call text-back goes out on SMS.
        rows = await outbound_for_customer_since(
            session, endpoint.client_id, customer.id, cursor
        )

    return {
        "messages": [
            {
                "body": row.body,
                "status": row.status.value,
                "channel": row.channel.value,
                "language": row.detected_language,
                "error": row.error,
                "at": row.created_at.isoformat(),
                "seq": row.seq,
            }
            for row in rows
        ],
        "cursor": rows[-1].seq if rows else cursor,
        "conversation_status": (
            conversation.status.value if conversation is not None else "ai"
        ),
        "short_code": conversation.short_code if conversation is not None else None,
    }
