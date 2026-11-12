"""One shop's owner must never see another shop's data through the web.

`tests/test_tenant_isolation.py` proves the data layer is safe when it is asked
correctly. These tests prove the web layer asks it correctly: an owner session
carries exactly one `client_id`, and no route lets a request supply its own.
"""

from __future__ import annotations

import inspect
import pathlib
import uuid

import pytest
from sqlalchemy import update

from app.db import session_scope
from app.models import (
    Channel,
    Client,
    ClientStatus,
    Direction,
    SessionKind,
)
from app.repo import add_message, find_or_create_conversation, find_or_create_customer
from app.web import owner as owner_routes
from app.web.auth import request_login_code, start_session, verify_login_code

OWNER_A = "+13125550999"
OWNER_B = "+13125550888"

OWNER_PAGES = [
    "/shop",
    "/shop/conversations",
    "/shop/appointments",
    "/shop/jobs",
    "/shop/calls",
]


async def seed_conversation(client_id, phone: str, body: str):
    async with session_scope() as session:
        customer = await find_or_create_customer(session, client_id, phone)
        conversation = await find_or_create_conversation(
            session, client_id, customer.id, Channel.sms
        )
        await add_message(
            session,
            client_id,
            conversation.id,
            direction=Direction.inbound,
            channel=Channel.sms,
            body=body,
        )
        return conversation.id


async def sign_in_as(http, phone: str) -> None:
    async with session_scope() as session:
        code = await request_login_code(session, phone)
    assert code, f"no code issued for {phone}"
    response = await http.post("/shop/verify", data={"phone": phone, "code": code})
    assert response.status_code == 303, response.text


class TestTheDoorIsLocked:
    @pytest.mark.parametrize("path", OWNER_PAGES)
    async def test_anonymous_requests_are_sent_to_the_login(self, client, db, path):
        response = await client.get(path)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/shop/login")

    async def test_an_admin_cookie_is_not_an_owner_cookie(self, admin_client, shop_a):
        """Two cookie names, two checks. A console session is not a shop
        session even though both belong to people we trust."""
        response = await admin_client.get("/shop")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/shop/login")

    async def test_an_owner_cookie_is_not_an_admin_cookie(self, owner_client):
        response = await owner_client.get("/admin")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/admin/login")

    async def test_an_owner_cannot_reach_the_console_at_all(self, owner_client, shop_b):
        for path in (
            "/admin",
            "/admin/usage",
            "/admin/audit",
            "/admin/signups",
            f"/admin/shops/{shop_b}",
            f"/admin/shops/{shop_b}/config",
            f"/admin/shops/{shop_b}/prompt",
        ):
            response = await owner_client.get(path)
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/admin/login"), path


class TestCrossTenantReads:
    async def test_an_owner_sees_only_their_own_conversations(
        self, client, shop_a, shop_b
    ):
        await seed_conversation(shop_a, "+13125551111", "shop A customer")
        await seed_conversation(shop_b, "+13125552222", "shop B customer")

        await sign_in_as(client, OWNER_A)
        page = await client.get("/shop/conversations")
        assert page.status_code == 200
        assert "555-1111" in page.text
        assert "555-2222" not in page.text
        assert "shop A customer" in page.text
        assert "shop B customer" not in page.text

    async def test_another_shops_conversation_id_is_a_404(
        self, client, shop_a, shop_b
    ):
        """The id comes from the path; the client_id comes from the session."""
        theirs = await seed_conversation(shop_b, "+13125552222", "private")
        await sign_in_as(client, OWNER_A)

        response = await client.get(f"/shop/conversations/{theirs}")
        assert response.status_code == 404
        assert "private" not in response.text

    async def test_a_made_up_conversation_id_is_a_404(self, client, shop_a):
        await sign_in_as(client, OWNER_A)
        response = await client.get(f"/shop/conversations/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_a_malformed_conversation_id_is_rejected(self, client, shop_a):
        await sign_in_as(client, OWNER_A)
        response = await client.get("/shop/conversations/not-a-uuid")
        assert response.status_code == 422

    async def test_each_owner_sees_their_own_shop_name(self, client, shop_a, shop_b):
        await sign_in_as(client, OWNER_A)
        assert "Shop A" in (await client.get("/shop")).text

        await client.post(
            "/shop/logout",
            data={"csrf_token": _csrf(await client.get("/shop"))},
        )
        await sign_in_as(client, OWNER_B)
        page = await client.get("/shop")
        assert "Shop B" in page.text
        assert "Shop A" not in page.text

    async def test_a_query_parameter_cannot_redirect_the_read(
        self, client, shop_a, shop_b
    ):
        """There is no client_id parameter to tamper with - but if someone adds
        one later, this fails."""
        await seed_conversation(shop_b, "+13125552222", "shop B customer")
        await sign_in_as(client, OWNER_A)
        for query in (
            f"?client_id={shop_b}",
            f"?shop={shop_b}",
            f"?client={shop_b}",
        ):
            page = await client.get(f"/shop/conversations{query}")
            assert page.status_code == 200
            assert "shop B customer" not in page.text

    async def test_the_jobs_and_calls_pages_are_scoped_too(
        self, client, shop_a, shop_b
    ):
        from app.models import JobStatus
        from app.repo import log_call, upsert_job

        async with session_scope() as session:
            customer = await find_or_create_customer(
                session, shop_b, "+13125552222"
            )
            await upsert_job(
                session,
                shop_b,
                customer.id,
                "1998 Volvo 940",
                JobStatus.ready,
                "owner",
            )
            await log_call(
                session,
                shop_b,
                customer_id=customer.id,
                provider_call_id="CA-b",
                from_number="+13125552222",
            )
        await sign_in_as(client, OWNER_A)
        assert "1998 Volvo 940" not in (await client.get("/shop/jobs")).text
        assert "555-2222" not in (await client.get("/shop/calls")).text


class TestSessionBinding:
    async def test_a_code_for_one_owner_cannot_open_another_shop(
        self, shop_a, shop_b
    ):
        async with session_scope() as session:
            code = await request_login_code(session, OWNER_A)
        async with session_scope() as session:
            # Right code, wrong number: nothing to verify against.
            assert await verify_login_code(session, OWNER_B, code) is None
        async with session_scope() as session:
            assert await verify_login_code(session, OWNER_A, code) == shop_a

    async def test_a_session_is_pinned_to_the_shop_it_was_made_for(
        self, client, shop_a, shop_b
    ):
        await seed_conversation(shop_b, "+13125552222", "shop B customer")
        async with session_scope() as session:
            token, _ = await start_session(
                session, kind=SessionKind.owner, label=OWNER_A, client_id=shop_a
            )
        client.cookies.set("fd_shop", token)
        page = await client.get("/shop/conversations")
        assert page.status_code == 200
        assert "shop B customer" not in page.text

    async def test_pausing_a_shop_leaves_the_dashboard_readable(
        self, client, shop_a
    ):
        """Paused stops the AI answering; it does not lock the owner out of
        seeing what happened."""
        await sign_in_as(client, OWNER_A)
        async with session_scope() as session:
            await session.execute(
                update(Client)
                .where(Client.id == shop_a)
                .values(status=ClientStatus.paused)
            )
        assert (await client.get("/shop")).status_code == 200


class TestStructuralGuards:
    def test_the_dashboard_cannot_reach_a_cross_tenant_query(self):
        """`repo_admin` holds the queries that read across shops. The module
        that renders shop data must not be able to call one, even by mistake.
        Owner login needs that lookup, which is why it lives elsewhere.

        Checks the imports, not the file text, so the explanation in the
        module docstring does not trip it."""
        import ast

        tree = ast.parse(
            pathlib.Path(inspect.getfile(owner_routes)).read_text(encoding="utf-8")
        )
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        assert not [name for name in imported if "repo_admin" in name], imported

    def test_every_dashboard_route_reads_the_shop_from_the_session(self):
        """No owner route may take a client id as a parameter - there would be
        nothing stopping a browser from choosing a different one."""
        offenders = []
        for name, fn in vars(owner_routes).items():
            if name.startswith("_") or not inspect.iscoroutinefunction(fn):
                continue
            # Only what this module defines - an imported helper is not a route.
            if getattr(fn, "__module__", "") != owner_routes.__name__:
                continue
            params = set(inspect.signature(fn).parameters)
            if params & {"client_id", "shop_id", "client", "shop"}:
                offenders.append(name)
        assert not offenders, (
            f"these owner routes accept a shop parameter: {offenders}"
        )

    def test_the_dashboard_is_read_only(self):
        """Owner-first means actions happen by text. A POST route here would be
        a second code path for something WhatsApp already does."""
        methods = {
            method
            for route in owner_routes.router.routes
            for method in route.methods
        }
        assert methods <= {"GET", "HEAD"}, methods


def _csrf(response) -> str:
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "no csrf token on the page"
    return match.group(1)
