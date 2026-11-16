"""The console says why a conversation is with a human.

The code was useless while it only existed in the database, and the handover
note was being composed, sent in an alert and then thrown away. This file
covers the four places that now read both, and the one thing that could go
wrong in the filter: a hand-typed `?code=` is a stranger's bytes, so it is
normalised through the same chokepoint the writer uses rather than reaching
the query.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import session_scope
from app.models import Channel
from app.repo import find_or_create_conversation, find_or_create_customer, hand_off

from .conftest import make_shop

CODE_PATH = "/admin/shops/{client_id}/conversations"


async def _handed_off(client_id, phone, *, reason, code, summary=""):
    """One conversation, paused, with a code on it. Returns its id."""
    async with session_scope() as session:
        customer = await find_or_create_customer(session, client_id, phone)
        conversation = await find_or_create_conversation(
            session, client_id, customer.id, Channel.sms
        )
        await hand_off(
            session,
            client_id,
            conversation.id,
            reason,
            code=code,
            summary=summary,
        )
        return conversation.id


@pytest.fixture
async def clinic(db):
    return await make_shop(
        "Clinica Dental",
        vertical="dental",
        job_noun="treatment",
        currency="€",
        sms_number="+34600111001",
        whatsapp_id="whatsapp:+34600111001",
        sim_id="sim-clinic",
    )


class TestTheConversationPage:
    async def test_it_shows_the_code_the_reason_and_the_note(
        self, admin_client, clinic
    ):
        """Three different things. The code is the category, the reason is the
        model's own words, and the summary is the handover note - which the
        console could not show at all until it was stored."""
        conversation_id = await _handed_off(
            clinic,
            "+34600222001",
            reason="patient says the crown came off and it hurts",
            code="medical_concern",
            summary="Crown off a lower molar, in pain since last night.",
        )
        page = await admin_client.get(
            f"/admin/shops/{clinic}/conversations/{conversation_id}"
        )
        assert page.status_code == 200
        assert "Pain, injury or possible infection" in page.text
        assert "the crown came off" in page.text
        assert "Crown off a lower molar" in page.text

    async def test_the_code_links_to_the_others_like_it(
        self, admin_client, clinic
    ):
        """"Show me the rest of these" is the next question after reading one."""
        conversation_id = await _handed_off(
            clinic, "+34600222002", reason="pain", code="medical_concern"
        )
        page = await admin_client.get(
            f"/admin/shops/{clinic}/conversations/{conversation_id}"
        )
        assert "/conversations?code=medical_concern" in page.text

    async def test_an_uncoded_handoff_still_renders(self, admin_client, shop_a):
        """Every handoff that predates this commit has no code, and the page
        must not grow an empty badge or a missing-attribute error over it."""
        conversation_id = await _handed_off(
            shop_a, "+13125558001", reason="owner took over", code=""
        )
        page = await admin_client.get(
            f"/admin/shops/{shop_a}/conversations/{conversation_id}"
        )
        assert page.status_code == 200
        assert "owner took over" in page.text


class TestTheFilter:
    async def test_it_narrows_to_one_code(self, admin_client, clinic):
        await _handed_off(
            clinic, "+34600333001", reason="pain", code="medical_concern"
        )
        await _handed_off(
            clinic, "+34600333002", reason="how much is a veneer", code="price_or_quote"
        )
        page = await admin_client.get(
            CODE_PATH.format(client_id=clinic) + "?code=medical_concern"
        )
        assert page.status_code == 200
        # The `phone` filter groups digits for display, so compare without
        # the spacing rather than guessing which grouping it chose.
        digits = page.text.replace(" ", "").replace(" ", "")
        assert "34600333001" in digits
        assert "34600333002" not in digits

    async def test_it_offers_only_the_codes_this_shop_has_used(
        self, admin_client, clinic
    ):
        """Twenty links, most leading to an empty table, is a worse page than
        one. The counts are the answer to "is this getting worse"."""
        for phone in ("+34600444001", "+34600444002"):
            await _handed_off(clinic, phone, reason="pain", code="medical_concern")
        page = await admin_client.get(CODE_PATH.format(client_id=clinic))
        assert "Pain, injury or possible infection" in page.text
        # Never used by this shop, so never offered.
        assert "Offer or negotiation" not in page.text

    @pytest.mark.parametrize(
        "raw",
        [
            "nonsense",
            "medical_concern'; drop table conversations; --",
            "../../etc/passwd",
            "%00",
            "a" * 500,
        ],
    )
    async def test_a_hand_typed_code_cannot_reach_the_query(
        self, admin_client, clinic, raw
    ):
        """`handoff.normalise` is the gate, so an unrecognised value filters on
        nothing rather than on itself - the page shows everything, which is
        what "no filter" means."""
        await _handed_off(
            clinic, "+34600555001", reason="pain", code="medical_concern"
        )
        page = await admin_client.get(
            CODE_PATH.format(client_id=clinic), params={"code": raw}
        )
        assert page.status_code == 200
        assert "555001" in page.text.replace(" ", "")

    async def test_the_filter_is_a_query_param_and_not_a_path_segment(self):
        """`test_web_isolation.py` holds the rule that the path identifies the
        shop and nothing else. A `/conversations/{code}` segment would be a
        second thing in the path to reason about - and would collide with the
        conversation-id route that is already there."""
        from app.web import admin as admin_routes

        paths = {
            route.path
            for route in admin_routes.router.routes
            if "conversations" in getattr(route, "path", "")
        }
        assert paths == {
            "/admin/shops/{client_id}/conversations",
            "/admin/shops/{client_id}/conversations/{conversation_id}",
        }


class TestTheListAndTheWaitingPanel:
    async def test_the_list_shows_the_code_on_the_row(self, admin_client, clinic):
        await _handed_off(
            clinic, "+34600666001", reason="pain", code="medical_concern"
        )
        page = await admin_client.get(CODE_PATH.format(client_id=clinic))
        assert "Pain, injury or possible infection" in page.text

    async def test_the_shop_page_says_why_each_one_is_waiting(
        self, admin_client, clinic
    ):
        await _handed_off(
            clinic,
            "+34600777001",
            reason="asked whether she can have it while pregnant",
            code="clinical_advice",
        )
        page = await admin_client.get(f"/admin/shops/{clinic}")
        assert "Asked for clinical advice" in page.text


class TestTheOwnersOwnDashboard:
    async def test_the_owner_sees_the_same_words(self, owner_client, shop_a):
        """The owner reads "Asked for a person", not `asked_for_human`. Same
        filter, same labels - there is one vocabulary, not two."""
        await _handed_off(
            shop_a,
            "+13125559001",
            reason="customer asked for a person",
            code="asked_for_human",
        )
        page = await owner_client.get("/shop")
        assert page.status_code == 200
        assert "Asked for a person" in page.text


class TestTheOwnerConversationPage:
    async def test_the_handover_note_is_on_the_page_the_owner_opens(
        self, owner_client, shop_a
    ):
        """The note went out in the WhatsApp alert at the time and was then
        thrown away, so the one page an owner opens *before* picking the
        conversation up could not tell them what it was about."""
        conversation_id = await _handed_off(
            shop_a,
            "+13125559201",
            reason="customer mentioned a lawyer",
            code="legal_or_insurance",
            summary="Says their solicitor will write about the invoice.",
        )
        page = await owner_client.get(f"/shop/conversations/{conversation_id}")
        assert page.status_code == 200
        assert "Lawyer or insurance" in page.text
        assert "their solicitor will write" in page.text


class TestTheUsageRollup:
    async def test_it_counts_handoffs_by_code(self, admin_client, clinic):
        for phone, code in (
            ("+34600888001", "medical_concern"),
            ("+34600888002", "medical_concern"),
            ("+34600888003", "price_or_quote"),
        ):
            await _handed_off(clinic, phone, reason="x", code=code)
        page = await admin_client.get("/admin/usage")
        assert page.status_code == 200
        assert "Why conversations went to a human" in page.text
        assert "Pain, injury or possible infection" in page.text
        assert "medical_concern" in page.text

    async def test_an_empty_window_does_not_divide_by_zero(
        self, admin_client, shop_a
    ):
        """The share column is a percentage of a total that can be zero, and a
        ZeroDivisionError inside Jinja renders as a 500 on the page whose job
        is to tell you something is wrong."""
        page = await admin_client.get("/admin/usage")
        assert page.status_code == 200
        assert "No coded handoffs in this window" in page.text

    async def test_uncoded_handoffs_are_left_out_rather_than_bucketed(
        self, admin_client, shop_a
    ):
        """They are not unclassified escalations - they are handoffs from
        before the code existed. Counting them as "something else" would
        invent a trend."""
        from app import repo_admin
        from app.repo import utcnow

        await _handed_off(shop_a, "+13125559101", reason="old one", code="")
        async with session_scope() as session:
            rows = await repo_admin.handoff_counts(
                session, since=utcnow().replace(year=2000)
            )
        assert rows == []


class TestTheLabelFilter:
    def test_it_never_raises_on_anything_a_template_can_hand_it(self):
        """A filter that raises takes the whole page down, and this one runs on
        a value read straight out of a column."""
        from app.web.templating import templates

        label = templates.env.filters["handoff_label"]
        for value in ("medical_concern", "retired_code", "", None, uuid.uuid4().hex):
            assert isinstance(label(value), str)
