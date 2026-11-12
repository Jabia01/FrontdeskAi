"""The WhatsApp 24-hour free-form window.

Inside it, free-form replies send. Outside it, only an approved template may go
out - and with no templates approved in Phase 1, the send is marked `blocked` and
the owner is alerted. It is never dropped silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.channels.twilio_sms import TwilioSmsAdapter
from app.channels.twilio_whatsapp import TwilioWhatsAppAdapter
from app.core.delivery import send_reply
from app.db import session_scope
from app.models import Channel, Direction, MessageStatus
from app.repo import (
    find_or_create_conversation,
    find_or_create_customer,
    get_conversation,
    outbound_since,
    touch_window,
)
from app.schemas import OutboundMessage


def wa_message(window_expires_at, template_name=None) -> OutboundMessage:
    return OutboundMessage(
        channel=Channel.whatsapp,
        provider="twilio",
        external_id="whatsapp:+13125550001",
        to_number="+13125559876",
        body="Your car is ready.",
        window_expires_at=window_expires_at,
        template_name=template_name,
    )


class TestAdapterPolicy:
    async def test_inside_the_window_sends(self):
        future = datetime.now(UTC) + timedelta(hours=3)
        result = await TwilioWhatsAppAdapter().send(wa_message(future))
        assert result.ok is True
        assert result.blocked is False

    async def test_outside_the_window_is_blocked(self):
        past = datetime.now(UTC) - timedelta(minutes=1)
        result = await TwilioWhatsAppAdapter().send(wa_message(past))
        assert result.ok is False
        assert result.blocked is True
        assert "window closed" in result.error

    async def test_no_window_at_all_is_blocked(self):
        result = await TwilioWhatsAppAdapter().send(wa_message(None))
        assert result.blocked is True
        assert "no open 24-hour window" in result.error

    async def test_an_approved_template_is_allowed_outside_the_window(self):
        past = datetime.now(UTC) - timedelta(days=3)
        result = await TwilioWhatsAppAdapter().send(
            wa_message(past, template_name="car_ready")
        )
        assert result.ok is True
        assert result.blocked is False

    async def test_naive_timestamps_are_treated_as_utc(self):
        """A DB value read back without tzinfo must not crash the comparison."""
        naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        result = await TwilioWhatsAppAdapter().send(wa_message(naive_past))
        assert result.blocked is True

    async def test_sms_has_no_window(self):
        """Only WhatsApp carries this rule."""
        result = await TwilioSmsAdapter().send(
            OutboundMessage(
                channel=Channel.sms,
                provider="twilio",
                external_id="+13125550001",
                to_number="+13125559876",
                body="Your car is ready.",
                window_expires_at=None,
            )
        )
        assert result.ok is True

    async def test_whatsapp_numbers_get_the_prefix_on_the_wire(self):
        adapter = TwilioWhatsAppAdapter()
        assert adapter._wire_number("+13125550001") == "whatsapp:+13125550001"
        # Idempotent - an already-prefixed value is not double-prefixed.
        assert (
            adapter._wire_number("whatsapp:+13125550001")
            == "whatsapp:+13125550001"
        )

    async def test_sms_segment_count_is_reported(self):
        long_body = "x" * 350
        result = await TwilioSmsAdapter().send(
            OutboundMessage(
                channel=Channel.sms,
                provider="twilio",
                external_id="+13125550001",
                to_number="+13125559876",
                body=long_body,
            )
        )
        assert result.segments == 3  # 350 chars -> 3 billable segments


class TestDeliveryRecordsTheOutcome:
    @pytest.fixture
    async def convo(self, shop_a):
        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125559876"
            )
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.whatsapp
            )
            return shop_a, conversation.id, customer.phone

    async def test_sent_inside_the_window(self, convo):
        client_id, conversation_id, phone = convo
        async with session_scope() as session:
            await touch_window(session, client_id, conversation_id)
        async with session_scope() as session:
            conversation = await get_conversation(
                session, client_id, conversation_id
            )
            result, _ = await send_reply(
                session,
                client_id=client_id,
                conversation=conversation,
                to_number=phone,
                body="We close at 6pm.",
                channel=Channel.whatsapp,
            )
            assert result.ok

        async with session_scope() as session:
            rows = await outbound_since(session, client_id, conversation_id)
        assert [r.status for r in rows] == [MessageStatus.sent]

    async def test_blocked_outside_the_window_is_persisted_not_dropped(self, convo):
        client_id, conversation_id, phone = convo
        # Window left as NULL - no inbound message has opened it.
        async with session_scope() as session:
            conversation = await get_conversation(
                session, client_id, conversation_id
            )
            result, _ = await send_reply(
                session,
                client_id=client_id,
                conversation=conversation,
                to_number=phone,
                body="Your car is ready.",
                channel=Channel.whatsapp,
            )
            assert result.blocked is True

        async with session_scope() as session:
            rows = await outbound_since(session, client_id, conversation_id)

        assert len(rows) == 1, "a blocked reply must still be recorded"
        assert rows[0].status == MessageStatus.blocked
        assert rows[0].error and "window" in rows[0].error
        assert rows[0].direction == Direction.outbound

    async def test_blocked_messages_are_excluded_from_prompt_history(self, convo):
        """A reply the customer never saw must not look like one we sent."""
        from app.repo import recent_messages

        client_id, conversation_id, phone = convo
        async with session_scope() as session:
            conversation = await get_conversation(
                session, client_id, conversation_id
            )
            await send_reply(
                session,
                client_id=client_id,
                conversation=conversation,
                to_number=phone,
                body="Never delivered.",
                channel=Channel.whatsapp,
            )
        async with session_scope() as session:
            history = await recent_messages(session, client_id, conversation_id)
        assert history == []

    async def test_missing_endpoint_raises(self, shop_a):
        from app.core.delivery import NoEndpointError

        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_a, "+13125558888"
            )
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.voice
            )
        async with session_scope() as session:
            conversation = await get_conversation(
                session, shop_a, conversation.id
            )
            with pytest.raises(NoEndpointError):
                await send_reply(
                    session,
                    client_id=shop_a,
                    conversation=conversation,
                    to_number="+13125558888",
                    body="hi",
                    channel=Channel.voice,  # no voice endpoint seeded
                )
