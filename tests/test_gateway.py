"""Gateway: Twilio signature validation, inbound parsing, and dedupe."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.channels.twilio_sms import TwilioSmsAdapter
from app.channels.twilio_whatsapp import TwilioWhatsAppAdapter
from app.config import get_settings
from app.core.ingest import UnknownEndpointError, ingest
from app.db import session_scope
from app.gateway.signature import canonical_url, validate_twilio_signature
from app.models import Channel
from app.repo import recent_messages

# A real signature, computed once with twilio's own RequestValidator. If our
# canonical-URL construction or param handling drifts, this stops matching.
FIXTURE_TOKEN = "test_auth_token_32_chars_exactly!"
FIXTURE_BASE = "https://frontdesk.example.com"
FIXTURE_PATH = "/webhooks/twilio/sms"
FIXTURE_PARAMS = {
    "MessageSid": "SM123",
    "From": "+13125559876",
    "To": "+13125550001",
    "Body": "What are your hours?",
    "NumMedia": "0",
}
FIXTURE_SIGNATURE = "TY3Jg3zWP8BrfCGjvCuZiP+HpxI="


@pytest.fixture
def twilio_env(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", FIXTURE_TOKEN)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", FIXTURE_BASE)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestSignature:
    def test_canonical_url_uses_the_public_base(self, twilio_env):
        assert canonical_url(FIXTURE_PATH) == FIXTURE_BASE + FIXTURE_PATH
        assert canonical_url("webhooks/x") == FIXTURE_BASE + "/webhooks/x"

    def test_known_good_signature_passes(self, twilio_env):
        assert validate_twilio_signature(
            FIXTURE_PATH, FIXTURE_PARAMS, FIXTURE_SIGNATURE
        )

    def test_tampered_body_fails(self, twilio_env):
        tampered = {**FIXTURE_PARAMS, "Body": "Send me your bank details"}
        assert not validate_twilio_signature(
            FIXTURE_PATH, tampered, FIXTURE_SIGNATURE
        )

    def test_missing_signature_fails(self, twilio_env):
        assert not validate_twilio_signature(FIXTURE_PATH, FIXTURE_PARAMS, None)

    def test_wrong_path_fails(self, twilio_env):
        assert not validate_twilio_signature(
            "/webhooks/twilio/whatsapp", FIXTURE_PARAMS, FIXTURE_SIGNATURE
        )

    def test_skipped_when_no_token_configured(self, monkeypatch):
        """Phase 1 reality: no Twilio account yet, so validation cannot run."""
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
        monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "true")
        get_settings.cache_clear()
        assert validate_twilio_signature(FIXTURE_PATH, FIXTURE_PARAMS, None)
        get_settings.cache_clear()

    def test_explicitly_disabled(self, monkeypatch):
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", FIXTURE_TOKEN)
        monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "false")
        get_settings.cache_clear()
        assert validate_twilio_signature(FIXTURE_PATH, FIXTURE_PARAMS, "garbage")
        get_settings.cache_clear()


class TestInboundParsing:
    def test_sms(self):
        inbound = TwilioSmsAdapter().parse_inbound(FIXTURE_PARAMS)
        assert inbound.channel == Channel.sms
        assert inbound.external_id == "+13125550001"
        assert inbound.from_number == "+13125559876"
        assert inbound.body == "What are your hours?"
        assert inbound.provider_msg_id == "SM123"
        assert inbound.media_url is None

    def test_whatsapp_strips_the_prefix_from_the_customer_number(self):
        """Phone is the identity across channels, so it must not carry a prefix."""
        inbound = TwilioWhatsAppAdapter().parse_inbound(
            {
                "MessageSid": "SM456",
                "From": "whatsapp:+13125559876",
                "To": "whatsapp:+13125550001",
                "Body": "hola",
                "NumMedia": "0",
            }
        )
        assert inbound.from_number == "+13125559876"
        # The endpoint id is matched verbatim, so the prefix stays here.
        assert inbound.external_id == "whatsapp:+13125550001"

    def test_media_is_picked_up(self):
        inbound = TwilioSmsAdapter().parse_inbound(
            {
                **FIXTURE_PARAMS,
                "NumMedia": "1",
                "MediaUrl0": "https://api.twilio.com/media/abc",
            }
        )
        assert inbound.media_url == "https://api.twilio.com/media/abc"

    def test_missing_to_is_rejected(self):
        with pytest.raises(ValueError, match="To"):
            TwilioSmsAdapter().parse_inbound({"From": "+1312", "Body": "hi"})

    def test_missing_from_is_rejected(self):
        with pytest.raises(ValueError, match="From"):
            TwilioSmsAdapter().parse_inbound({"To": "+1312", "Body": "hi"})

    def test_text_prefers_body_then_transcript(self):
        inbound = TwilioSmsAdapter().parse_inbound({**FIXTURE_PARAMS, "Body": "  hi  "})
        assert inbound.text == "hi"


class TestIngestAndDedupe:
    async def test_dedupe_on_provider_message_id(self, shop_a):
        payload = {
            "MessageSid": "SM-dup-1",
            "From": "+13125559876",
            "To": "+13125550001",
            "Body": "hello",
            "NumMedia": "0",
        }
        inbound = TwilioSmsAdapter().parse_inbound(payload)

        first = await ingest(inbound)
        second = await ingest(inbound)

        assert first.duplicate is False
        assert first.message_id is not None
        assert second.duplicate is True
        assert second.message_id is None
        assert second.conversation_id == first.conversation_id

        async with session_scope() as session:
            stored = await recent_messages(
                session, shop_a, first.conversation_id
            )
        assert len(stored) == 1

    async def test_window_is_opened_by_an_inbound_message(self, shop_a):
        from app.repo import get_conversation

        inbound = TwilioWhatsAppAdapter().parse_inbound(
            {
                "MessageSid": "SM-win-1",
                "From": "whatsapp:+13125559876",
                "To": "whatsapp:+13125550001",
                "Body": "hola",
                "NumMedia": "0",
            }
        )
        result = await ingest(inbound)
        async with session_scope() as session:
            conversation = await get_conversation(
                session, shop_a, result.conversation_id
            )
        assert conversation.window_expires_at is not None

    async def test_unknown_endpoint_is_rejected(self, shop_a):
        inbound = TwilioSmsAdapter().parse_inbound(
            {
                "MessageSid": "SM-unknown",
                "From": "+13125559876",
                "To": "+19998887777",
                "Body": "hi",
                "NumMedia": "0",
            }
        )
        with pytest.raises(UnknownEndpointError):
            await ingest(inbound)

    async def test_same_customer_across_channels_is_one_customer(self, shop_a):
        """Phone is the identity; SMS and WhatsApp share the customer row."""
        from app.repo import find_customer_by_phone

        sms = TwilioSmsAdapter().parse_inbound(
            {
                "MessageSid": "SM-x1",
                "From": "+13125559876",
                "To": "+13125550001",
                "Body": "hi",
                "NumMedia": "0",
            }
        )
        wa = TwilioWhatsAppAdapter().parse_inbound(
            {
                "MessageSid": "SM-x2",
                "From": "whatsapp:+13125559876",
                "To": "whatsapp:+13125550001",
                "Body": "hola",
                "NumMedia": "0",
            }
        )
        a = await ingest(sms)
        b = await ingest(wa)

        assert a.customer_id == b.customer_id
        # ...but separate conversations, one per channel.
        assert a.conversation_id != b.conversation_id

        async with session_scope() as session:
            assert (
                await find_customer_by_phone(session, shop_a, "+13125559876")
            ) is not None


class TestWebhookEndpoints:
    @pytest.fixture
    async def client(self):
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http

    async def test_simulator_roundtrip_and_dedupe(self, client, shop_a, monkeypatch):
        sent = []

        async def fake_enqueue(client_id, message_id):
            sent.append((client_id, message_id))
            return "job-1"

        monkeypatch.setattr(
            "app.gateway.routes.enqueue_handle_inbound", fake_enqueue
        )

        body = {
            "external_id": "sim-a",
            "from_number": "+13125554444",
            "body": "hello",
            "provider_msg_id": "sim-fixed-1",
        }
        first = await client.post("/webhooks/sim", json=body)
        second = await client.post("/webhooks/sim", json=body)

        assert first.status_code == 200
        assert first.json()["status"] == "queued"
        assert second.json()["status"] == "duplicate"
        assert len(sent) == 1  # the duplicate must not be queued

    async def test_simulator_unknown_shop_is_404(self, client, shop_a):
        res = await client.post(
            "/webhooks/sim",
            json={"external_id": "sim-nope", "from_number": "+1312", "body": "x"},
        )
        assert res.status_code == 404

    async def test_simulator_requires_a_from_number(self, client, shop_a):
        res = await client.post(
            "/webhooks/sim", json={"external_id": "sim-a", "body": "x"}
        )
        assert res.status_code == 400

    async def test_twilio_sms_rejects_a_bad_signature(
        self, client, shop_a, twilio_env
    ):
        res = await client.post(
            "/webhooks/twilio/sms",
            data=FIXTURE_PARAMS,
            headers={"X-Twilio-Signature": "obviously-wrong"},
        )
        assert res.status_code == 403

    async def test_poll_rejects_a_bad_cursor(self, client, shop_a):
        res = await client.get(
            "/webhooks/sim/poll",
            params={
                "external_id": "sim-a",
                "from_number": "+13125554444",
                "after": "not-a-date",
            },
        )
        assert res.status_code == 400

    async def test_health(self, client):
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
