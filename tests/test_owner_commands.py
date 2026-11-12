"""Owner commands: the grammar, and what each one does.

Translation is stubbed - `test_engine.py` style - so these assert routing and
state, not Claude's wording.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

import pytest

from app.core import owner_commands
from app.core.owner_commands import Kind, parse
from app.core.router import load_shop
from app.db import session_scope
from app.models import (
    Channel,
    ConversationStatus,
    Direction,
    JobStatus,
    MessageStatus,
)
from app.repo import (
    _mint_code,
    add_message,
    find_or_create_conversation,
    find_or_create_customer,
    get_conversation,
    hand_off,
    outbound_since,
    upsert_job,
)

TZ = ZoneInfo("America/Chicago")


class TestParser:
    @pytest.mark.parametrize(
        "text,kind,code,payload",
        [
            ("OK", Kind.ok, None, ""),
            ("ok.", Kind.ok, None, ""),
            ("YES", Kind.ok, None, ""),
            ("B3K9 OK", Kind.ok, "B3K9", ""),
            ("CHANGE", Kind.change, None, ""),
            ("no", Kind.change, None, ""),
            ("RESUME", Kind.resume, None, ""),
            ("B3K9 RESUME", Kind.resume, "B3K9", ""),
            ("HELP", Kind.help, None, ""),
            ("", Kind.help, None, ""),
            ("READY 2014 Civic", Kind.ready, None, "2014 Civic"),
            ("B3K9 READY 2014 Civic", Kind.ready, "B3K9", "2014 Civic"),
            ("we can take it tomorrow", Kind.reply, None, "we can take it tomorrow"),
            ("B3K9 on my way", Kind.reply, "B3K9", "on my way"),
        ],
    )
    def test_grammar(self, text, kind, code, payload):
        result = parse(text)
        assert (result.kind, result.code, result.payload) == (kind, code, payload)

    def test_a_year_is_not_mistaken_for_a_code(self):
        """'2014 Civic is done' must keep its first word."""
        result = parse("2014 Civic is done")
        assert result.code is None
        assert result.payload == "2014 Civic is done"

    def test_all_digit_tokens_are_never_codes(self):
        assert parse("1234 hello").code is None

    def test_generated_codes_are_always_parseable(self):
        """Generation and parsing must agree, or the owner's code never matches."""
        for _ in range(200):
            code = _mint_code(4)
            assert owner_commands.CODE_RE.match(code), code
            assert parse(f"{code} OK").code == code

    def test_codes_never_contain_vowels(self):
        """So a code can never accidentally spell a word."""
        for _ in range(200):
            assert not set(_mint_code(4)) & set("AEIOU")


@pytest.fixture
async def world(shop_a, monkeypatch):
    """A shop, a Spanish customer with a handed-off chat, and a stub translator."""
    translations: list[dict] = []

    async def fake_translate(text, *, to_language, from_language="en"):
        translations.append(
            {"text": text, "to": to_language, "from": from_language}
        )
        if to_language == from_language:
            return text, False
        return f"[{to_language}] {text}", True

    monkeypatch.setattr(owner_commands, "translate", fake_translate)

    async with session_scope() as session:
        shop = await load_shop(session, shop_a, use_cache=False)
        customer = await find_or_create_customer(
            session, shop_a, "+13125559001"
        )
        customer.language = "es"
        conversation = await find_or_create_conversation(
            session, shop_a, customer.id, Channel.sms
        )
        await hand_off(session, shop_a, conversation.id, "customer asked for a person")
        code = conversation.short_code
        ids = (customer.id, conversation.id)

    return {
        "shop": shop,
        "code": code,
        "customer_id": ids[0],
        "conversation_id": ids[1],
        "translations": translations,
    }


async def run(world, text: str) -> dict:
    async with session_scope() as session:
        return await owner_commands.handle(session, world["shop"], text)


class TestReplyRelay:
    async def test_relays_translated_into_the_customers_language(self, world):
        result = await run(world, f"{world['code']} we can take it tomorrow at 9")

        assert result["status"] == "relayed"
        assert result["translated"] is True
        assert world["translations"][-1]["to"] == "es"
        assert world["translations"][-1]["from"] == "en"

        async with session_scope() as session:
            rows = await outbound_since(
                session, world["shop"].client_id, world["conversation_id"]
            )
        assert rows[-1].body == "[es] we can take it tomorrow at 9"
        assert rows[-1].status == MessageStatus.sent

    async def test_without_a_code_it_uses_the_latest_handoff(self, world):
        result = await run(world, "sorry for the delay")
        assert result["status"] == "relayed"

    async def test_an_unknown_code_is_treated_as_message_text(self, world):
        """No words may be eaten by a code that does not exist."""
        result = await run(world, "X9ZZ is the part we ordered")
        assert result["status"] == "relayed"
        assert world["translations"][-1]["text"] == "X9ZZ is the part we ordered"

    async def test_relay_marks_the_conversation_as_owner_driven(self, world):
        await run(world, f"{world['code']} on my way")
        async with session_scope() as session:
            conversation = await get_conversation(
                session, world["shop"].client_id, world["conversation_id"]
            )
        assert conversation.status == ConversationStatus.human

    async def test_no_target_is_reported_rather_than_guessed(self, shop_b, monkeypatch):
        async def fake_translate(text, **kwargs):
            return text, False

        monkeypatch.setattr(owner_commands, "translate", fake_translate)
        async with session_scope() as session:
            shop = await load_shop(session, shop_b, use_cache=False)
            result = await owner_commands.handle(session, shop, "hello there")
        assert result["status"] == "no_target"
        assert "code" in result["reply_to_owner"]


class TestResume:
    async def test_hands_the_chat_back_to_the_ai(self, world):
        result = await run(world, f"{world['code']} RESUME")
        assert result["status"] == "resumed"
        async with session_scope() as session:
            conversation = await get_conversation(
                session, world["shop"].client_id, world["conversation_id"]
            )
        assert conversation.status == ConversationStatus.ai


class TestReady:
    async def test_tells_the_customer_in_their_language(self, world):
        async with session_scope() as session:
            await upsert_job(
                session,
                world["shop"].client_id,
                world["customer_id"],
                "2016 Toyota Corolla",
                JobStatus.in_shop,
                updated_by="booking",
            )

        result = await run(world, "READY 2016 Toyota Corolla")
        assert result["status"] == "ready_sent"

        async with session_scope() as session:
            rows = await outbound_since(
                session, world["shop"].client_id, world["conversation_id"]
            )
        assert "[es]" in rows[-1].body
        assert "ready" in world["translations"][-1]["text"].lower()

    async def test_sets_the_job_status(self, world):
        from sqlalchemy import select

        from app.models import Job

        async with session_scope() as session:
            await upsert_job(
                session,
                world["shop"].client_id,
                world["customer_id"],
                "2016 Toyota Corolla",
                JobStatus.in_shop,
                updated_by="booking",
            )
        await run(world, "READY 2016 Toyota Corolla")

        async with session_scope() as session:
            job = (await session.execute(select(Job))).scalars().one()
        assert job.status == JobStatus.ready
        assert job.updated_by == "owner"

    async def test_without_an_item_it_asks(self, world):
        result = await run(world, "READY")
        assert result["status"] == "ready_needs_item"
        # The question uses whatever this business calls a job. The fixture
        # leaves `job_noun` unset, so it falls back to the neutral default.
        assert "Which job?" in result["reply_to_owner"]

    async def test_the_question_uses_the_business_own_word(self, world):
        """A groomer must not be asked "which job?" about a dog."""
        world["shop"].config["job_noun"] = "pet"
        try:
            result = await run(world, "READY")
        finally:
            world["shop"].config.pop("job_noun")
        assert "Which pet?" in result["reply_to_owner"]
        assert "Bella" in result["reply_to_owner"]

    async def test_unknown_item_is_reported_not_guessed(self, world):
        result = await run(world, "READY 1998 Delorean")
        assert result["status"] == "ready_no_job"
        assert "no job on file" in result["reply_to_owner"]


class TestOkAndChange:
    async def test_ok_confirms_an_unconfirmed_booking(self, world):
        from app.core import booking
        from app.integrations.calendar import StubCalendarClient, set_calendar_client

        set_calendar_client(StubCalendarClient())
        try:
            async with session_scope() as session:
                slot = booking.Slot(
                    start=datetime(2026, 11, 12, 15, 0, tzinfo=UTC),
                    end=datetime(2026, 11, 12, 15, 30, tzinfo=UTC),
                )
                await booking.create_booking(
                    session,
                    world["shop"],
                    customer_id=world["customer_id"],
                    conversation_id=world["conversation_id"],
                    slot=slot,
                    service="Brakes",
                    item="2014 Civic",
                    customer_name="Maria",
                )

            result = await run(world, "OK")
            assert result["status"] == "booking_confirmed"

            from sqlalchemy import select

            from app.models import Appointment

            async with session_scope() as session:
                appointment = (
                    await session.execute(select(Appointment))
                ).scalars().one()
            assert appointment.owner_confirmed_at is not None
        finally:
            set_calendar_client(None)

    async def test_ok_with_nothing_pending(self, world):
        result = await run(world, "OK")
        assert result["status"] == "nothing_to_confirm"

    async def test_change_with_nothing_pending(self, world):
        result = await run(world, "CHANGE")
        assert result["status"] == "nothing_to_change"


class TestShadowDrafts:
    @pytest.fixture
    async def draft(self, world):
        async with session_scope() as session:
            message = await add_message(
                session,
                world["shop"].client_id,
                world["conversation_id"],
                direction=Direction.outbound,
                channel=Channel.sms,
                body="La alineacion cuesta entre $80 y $120.",
                status=MessageStatus.draft,
                detected_language="es",
            )
            return message.id

    async def test_ok_sends_the_draft_in_place(self, world, draft):
        """One approved draft must produce exactly one delivered message."""
        result = await run(world, f"{world['code']} OK")
        assert result["status"] == "draft_sent"

        async with session_scope() as session:
            rows = await outbound_since(
                session, world["shop"].client_id, world["conversation_id"]
            )
        sent = [r for r in rows if r.status == MessageStatus.sent]
        assert len(sent) == 1, "approving a draft must not create a second row"
        assert sent[0].id == draft

    async def test_the_owners_own_wording_supersedes_the_draft(self, world, draft):
        """Otherwise a later bare OK would send stale text."""
        result = await run(world, f"{world['code']} Si, 60 dolares montada")
        assert result["status"] == "relayed"

        async with session_scope() as session:
            rows = await outbound_since(
                session, world["shop"].client_id, world["conversation_id"]
            )
        by_id = {r.id: r for r in rows}
        assert by_id[draft].status == MessageStatus.rejected
        assert any(
            r.status == MessageStatus.sent and "60 dolares" in (r.body or "")
            for r in rows
        )

    async def test_a_rejected_draft_is_not_resent_by_a_later_ok(self, world, draft):
        await run(world, f"{world['code']} my own words")
        result = await run(world, f"{world['code']} OK")
        assert result["status"] != "draft_sent"


def test_help_lists_every_command():
    class Fake:
        config: ClassVar[dict] = {}

    text = owner_commands.help_text(Fake())
    for command in ("OK", "CHANGE", "READY", "RESUME"):
        assert command in text


def test_help_speaks_the_business_own_word():
    class Garage:
        config: ClassVar[dict] = {"job_noun": "vehicle"}

    class Groomer:
        config: ClassVar[dict] = {"job_noun": "pet"}

    assert "READY <vehicle>" in owner_commands.help_text(Garage())
    assert "READY <pet>" in owner_commands.help_text(Groomer())


class TestOwnerIdentification:
    def test_matches_the_configured_owner_number(self, shop_a):
        class Fake:
            owner_phone = "+13125550999"

        assert owner_commands.is_owner_number(Fake(), "+13125550999")
        assert not owner_commands.is_owner_number(Fake(), "+13125551111")

    def test_no_owner_phone_matches_nobody(self):
        class Fake:
            owner_phone = None

        assert not owner_commands.is_owner_number(Fake(), "+13125550999")
        assert not owner_commands.is_owner_number(Fake(), "")
