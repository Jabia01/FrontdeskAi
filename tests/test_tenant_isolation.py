"""One shop's data must never be reachable with another shop's client_id.

Every read in `app.repo` takes `client_id` first. These tests seed two shops and
then ask for shop A's rows while passing shop B's id - every one must come back
empty. A regression here is the worst class of bug this product can have.
"""

from __future__ import annotations

import inspect
import uuid
from typing import ClassVar

import pytest

from app import repo, repo_shop
from app.db import session_scope
from app.models import Channel, Direction


@pytest.fixture
async def seeded(shop_a, shop_b):
    """Give each shop a customer, a conversation, a message, a lead and usage."""
    out = {}
    async with session_scope() as session:
        for label, client_id, phone in (
            ("a", shop_a, "+13125551111"),
            ("b", shop_b, "+13125552222"),
        ):
            customer = await repo.find_or_create_customer(session, client_id, phone)
            conversation = await repo.find_or_create_conversation(
                session, client_id, customer.id, Channel.sms
            )
            message = await repo.add_message(
                session,
                client_id,
                conversation.id,
                direction=Direction.inbound,
                channel=Channel.sms,
                body=f"hello from {label}",
                provider_msg_id=f"msg-{label}",
            )
            lead = await repo.create_lead(
                session,
                client_id,
                customer_id=customer.id,
                conversation_id=conversation.id,
                item="2014 Civic",
                issue="brakes",
                contact="Juan",
            )
            await repo.record_usage(
                session, client_id, type="claude", conversation_id=conversation.id
            )
            out[label] = {
                "client_id": client_id,
                "customer_id": customer.id,
                "conversation_id": conversation.id,
                "message_id": message.id,
                "lead_id": lead.id,
            }
    return out


class TestCrossTenantReadsReturnNothing:
    async def test_customer(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            assert (
                await repo.get_customer(
                    session, a["client_id"], a["customer_id"]
                )
            ) is not None
            # Shop B asking for shop A's customer:
            assert (
                await repo.get_customer(
                    session, b["client_id"], a["customer_id"]
                )
            ) is None

    async def test_conversation(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            assert (
                await repo.get_conversation(
                    session, a["client_id"], a["conversation_id"]
                )
            ) is not None
            assert (
                await repo.get_conversation(
                    session, b["client_id"], a["conversation_id"]
                )
            ) is None

    async def test_message(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            assert (
                await repo.get_message(session, a["client_id"], a["message_id"])
            ) is not None
            assert (
                await repo.get_message(session, b["client_id"], a["message_id"])
            ) is None

    async def test_recent_messages(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            mine = await repo.recent_messages(
                session, a["client_id"], a["conversation_id"]
            )
            theirs = await repo.recent_messages(
                session, b["client_id"], a["conversation_id"]
            )
            assert [m.body for m in mine] == ["hello from a"]
            assert theirs == []

    async def test_outbound_since(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            await repo.add_message(
                session,
                a["client_id"],
                a["conversation_id"],
                direction=Direction.outbound,
                channel=Channel.sms,
                body="reply to a",
            )
        async with session_scope() as session:
            assert len(
                await repo.outbound_since(
                    session, a["client_id"], a["conversation_id"]
                )
            ) == 1
            assert (
                await repo.outbound_since(
                    session, b["client_id"], a["conversation_id"]
                )
            ) == []

    async def test_find_customer_by_phone(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            assert (
                await repo.find_customer_by_phone(
                    session, a["client_id"], "+13125551111"
                )
            ) is not None
            assert (
                await repo.find_customer_by_phone(
                    session, b["client_id"], "+13125551111"
                )
            ) is None

    async def test_endpoint_lookup_is_scoped_per_channel(self, shop_a, shop_b):
        async with session_scope() as session:
            a_ep = await repo.get_endpoint(session, Channel.sms, "+13125550001")
            b_ep = await repo.get_endpoint(session, Channel.sms, "+13125550002")
            assert a_ep is not None and a_ep.client_id == shop_a
            assert b_ep is not None and b_ep.client_id == shop_b
            assert a_ep.client_id != b_ep.client_id

    async def test_client_endpoint_is_client_scoped(self, shop_a, shop_b):
        async with session_scope() as session:
            a_ep = await repo.get_client_endpoint(session, shop_a, Channel.sms)
            b_ep = await repo.get_client_endpoint(session, shop_b, Channel.sms)
            assert a_ep.external_id == "+13125550001"
            assert b_ep.external_id == "+13125550002"


class TestCrossTenantWritesDoNothing:
    async def test_handoff_cannot_touch_another_shop(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            await repo.hand_off(
                session, b["client_id"], a["conversation_id"], "not mine"
            )
        async with session_scope() as session:
            conversation = await repo.get_conversation(
                session, a["client_id"], a["conversation_id"]
            )
            assert conversation.status.value == "ai"
            assert conversation.handoff_reason is None

    async def test_language_update_cannot_touch_another_shop(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            await repo.update_customer_language(
                session, b["client_id"], a["customer_id"], "es", 0.99
            )
        async with session_scope() as session:
            customer = await repo.get_customer(
                session, a["client_id"], a["customer_id"]
            )
            assert customer.language is None

    async def test_counters_cannot_be_moved_across_tenants(self, seeded):
        a, b = seeded["a"], seeded["b"]
        async with session_scope() as session:
            await repo.set_counters(
                session, b["client_id"], a["conversation_id"], misunderstand_count=5
            )
        async with session_scope() as session:
            conversation = await repo.get_conversation(
                session, a["client_id"], a["conversation_id"]
            )
            assert conversation.misunderstand_count == 0


class TestRepoSignatures:
    """Structural guard: no tenant-scoped helper may forget `client_id`.

    Covers `app.repo` and `app.repo_shop`, the two modules that read and write
    one shop's rows. `app.repo_admin` is deliberately outside this rule - it
    holds authentication, landing-page signups and the console's cross-tenant
    rollups, none of which belong to a shop. What keeps that safe is that every
    one of its callers sits behind an admin session, plus the guard in
    `tests/test_web_isolation.py` that the owner dashboard cannot import it.
    """

    # `get_endpoint` is the deliberate exception - it establishes the tenant.
    EXEMPT: ClassVar[set[str]] = {"get_endpoint", "message_exists", "utcnow"}
    MODULES: ClassVar[tuple] = (repo, repo_shop)

    def test_every_public_helper_takes_client_id_first(self):
        offenders = []
        for module in self.MODULES:
            for name, fn in vars(module).items():
                if name.startswith("_") or name in self.EXEMPT:
                    continue
                if not inspect.iscoroutinefunction(fn):
                    continue
                # Imported helpers belong to whichever module defined them.
                if getattr(fn, "__module__", "") != module.__name__:
                    continue
                params = list(inspect.signature(fn).parameters)
                # (session, client_id, ...)
                if len(params) < 2 or params[1] != "client_id":
                    offenders.append(
                        f"{module.__name__}.{name}({', '.join(params)})"
                    )
        assert not offenders, (
            "these repo helpers do not take client_id as their first real "
            f"argument: {offenders}"
        )

    def test_the_dashboard_queries_are_covered_by_that_rule(self):
        """Guard the guard: if repo_shop were dropped from MODULES, the Phase 3
        read models would silently stop being checked."""
        assert repo_shop in self.MODULES
        checked = [
            name
            for name, fn in vars(repo_shop).items()
            if inspect.iscoroutinefunction(fn)
            and getattr(fn, "__module__", "") == repo_shop.__name__
        ]
        assert len(checked) >= 8, checked


async def test_nonexistent_client_reads_are_empty_not_errors(shop_a):
    stranger = uuid.uuid4()
    async with session_scope() as session:
        assert await repo.get_customer(session, stranger, uuid.uuid4()) is None
        assert await repo.recent_messages(session, stranger, uuid.uuid4()) == []
