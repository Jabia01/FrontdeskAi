"""The console: who may reach it, and what saving config actually does."""

from __future__ import annotations

from typing import ClassVar

import pytest
from sqlalchemy import select

from app import repo_admin
from app.core.router import load_shop
from app.db import session_scope
from app.models import (
    AuditLog,
    Channel,
    ChannelEndpoint,
    Client,
    ClientConfig,
    ClientStatus,
)

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, csrf_from, make_admin

# Every page and action behind the login. If a new one is added without being
# listed here, the first test below will not cover it - so add it.
PROTECTED_PAGES = [
    "/admin",
    "/admin/shops/new",
    "/admin/signups",
    "/admin/usage",
    "/admin/audit",
]


class TestTheDoorIsLocked:
    @pytest.mark.parametrize("path", PROTECTED_PAGES)
    async def test_an_anonymous_page_request_redirects_to_the_login(
        self, client, db, path
    ):
        response = await client.get(path)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/admin/login")

    async def test_a_shop_page_redirects_too(self, client, shop_a):
        for path in (
            f"/admin/shops/{shop_a}",
            f"/admin/shops/{shop_a}/config",
            f"/admin/shops/{shop_a}/prompt",
            f"/admin/shops/{shop_a}/conversations",
        ):
            response = await client.get(path)
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/admin/login"), path

    async def test_the_redirect_remembers_where_you_were_going(self, client, shop_a):
        response = await client.get(f"/admin/shops/{shop_a}/config")
        assert f"next=/admin/shops/{shop_a}/config" in response.headers["location"]

    async def test_an_anonymous_post_cannot_change_anything(self, client, shop_a):
        response = await client.post(
            f"/admin/shops/{shop_a}/status", data={"status": "live"}
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/admin/login")
        async with session_scope() as session:
            client_row = await repo_admin.get_client(session, shop_a)
        # Untouched: the fixture leaves shop A live, and nothing moved it.
        assert client_row.status is ClientStatus.live

    async def test_login_rejects_a_wrong_password(self, client, db):
        await make_admin()
        response = await client.post(
            "/admin/login", data={"email": ADMIN_EMAIL, "password": "wrong"}
        )
        assert response.status_code == 401
        assert "do not match" in response.text
        assert "fd_admin" not in response.cookies

    async def test_login_sets_an_httponly_cookie(self, client, db):
        await make_admin()
        response = await client.post(
            "/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 303
        header = response.headers["set-cookie"]
        assert "fd_admin=" in header
        assert "HttpOnly" in header
        assert "SameSite=lax" in header.replace("Lax", "lax")

    async def test_the_next_parameter_cannot_point_off_site(self, client, db):
        """An open redirect from a login page is a phishing tool."""
        await make_admin()
        response = await client.post(
            "/admin/login",
            data={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "next_url": "https://evil.example.com/steal",
            },
        )
        assert response.headers["location"] == "/admin"

    async def test_signing_out_ends_the_session(self, admin_client):
        page = await admin_client.get("/admin")
        response = await admin_client.post(
            "/admin/logout", data={"csrf_token": csrf_from(page.text)}
        )
        assert response.status_code == 303
        after = await admin_client.get("/admin")
        assert after.status_code == 303
        assert after.headers["location"].startswith("/admin/login")


class TestCsrf:
    async def test_a_post_without_a_token_is_refused(self, admin_client, shop_a):
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/status", data={"status": "paused"}
        )
        assert response.status_code == 403
        async with session_scope() as session:
            row = await repo_admin.get_client(session, shop_a)
        assert row.status is ClientStatus.live

    async def test_a_post_with_a_wrong_token_is_refused(self, admin_client, shop_a):
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/status",
            data={"status": "paused", "csrf_token": "not-the-token"},
        )
        assert response.status_code == 403

    async def test_the_token_from_the_page_works(self, admin_client, shop_a):
        page = await admin_client.get(f"/admin/shops/{shop_a}")
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/status",
            data={"status": "paused", "csrf_token": csrf_from(page.text)},
        )
        assert response.status_code == 303


class TestShopList:
    async def test_it_lists_the_shops(self, admin_client, shop_a, shop_b):
        page = await admin_client.get("/admin")
        assert page.status_code == 200
        assert "Shop A" in page.text
        assert "Shop B" in page.text

    async def test_it_flags_what_needs_attention(self, admin_client, shop_a):
        from app.repo import (
            find_or_create_conversation,
            find_or_create_customer,
            hand_off,
        )

        async with session_scope() as session:
            customer = await find_or_create_customer(session, shop_a, "+13125557777")
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.sms
            )
            await hand_off(session, shop_a, conversation.id, "accident")
        page = await admin_client.get("/admin")
        assert "1 handoff" in page.text


class TestCreatingAShop:
    FORM: ClassVar[dict] = {
        "name": "Northside Brake & Muffler",
        "timezone": "America/Chicago",
        "owner_phone": "(312) 555-0222",
        "enabled_languages": "en, pl",
        "owner_language": "en",
        "sms_number": "+13125550333",
        "whatsapp_id": "+13125550333",
        "voice_number": "",
    }

    async def post(self, admin_client, **overrides):
        page = await admin_client.get("/admin/shops/new")
        data = {**self.FORM, **overrides, "csrf_token": csrf_from(page.text)}
        return await admin_client.post("/admin/shops", data=data)

    async def test_a_new_shop_starts_in_shadow_mode(self, admin_client, db):
        response = await self.post(admin_client)
        assert response.status_code == 303
        assert "/config?created=1" in response.headers["location"]

        async with session_scope() as session:
            row = (
                await session.execute(
                    select(Client).where(Client.name == self.FORM["name"])
                )
            ).scalar_one()
        # Nothing may reach a customer until someone has read the drafts.
        assert row.status is ClientStatus.shadow
        assert row.owner_phone == "+13125550222"

    async def test_english_is_always_in_the_language_list(self, admin_client, db):
        await self.post(admin_client, enabled_languages="es")
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(Client).where(Client.name == self.FORM["name"])
                )
            ).scalar_one()
        assert row.languages == ["es", "en"]

    async def test_it_gets_a_config_row_with_no_prices(self, admin_client, db):
        """With no price ranges the agent refuses to quote, which is the safe
        state for a shop nobody has configured yet."""
        await self.post(admin_client)
        async with session_scope() as session:
            config = (await session.execute(select(ClientConfig))).scalar_one()
        assert config.price_ranges == []
        assert config.services == []
        assert config.hours["monday"] == "8:00-18:00"
        assert config.channel_style["sms"]["max_chars"] == 320

    async def test_the_whatsapp_prefix_is_added(self, admin_client, db):
        """Twilio sends `To` as whatsapp:+1..., and the router matches it
        verbatim, so the prefix is part of the stored id."""
        await self.post(admin_client)
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChannelEndpoint).where(
                            ChannelEndpoint.channel == Channel.whatsapp
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [row.external_id for row in rows] == ["whatsapp:+13125550333"]

    async def test_every_shop_gets_a_simulator_endpoint(self, admin_client, db):
        await self.post(admin_client)
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ChannelEndpoint).where(
                            ChannelEndpoint.channel == Channel.simulator
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].provider == "local"

    async def test_a_bad_owner_number_is_refused(self, admin_client, db):
        response = await self.post(admin_client, owner_phone="nope")
        assert response.status_code == 400
        assert "owner" in response.text.lower()
        async with session_scope() as session:
            assert (await session.execute(select(Client))).scalars().all() == []

    async def test_a_number_already_used_is_refused(self, admin_client, shop_a):
        """A number belongs to exactly one shop, or inbound routing is a
        coin toss."""
        response = await self.post(admin_client, sms_number="+13125550001")
        assert response.status_code == 400
        assert "already mapped to another shop" in response.text
        async with session_scope() as session:
            names = (
                (await session.execute(select(Client.name))).scalars().all()
            )
        assert self.FORM["name"] not in names

    async def test_creating_a_shop_is_audited(self, admin_client, db):
        await self.post(admin_client)
        async with session_scope() as session:
            actions = (
                (await session.execute(select(AuditLog.action))).scalars().all()
            )
        assert "shop.create" in actions
        assert "admin.login" in actions


async def config_form_data(admin_client, shop_a, **overrides):
    page = await admin_client.get(f"/admin/shops/{shop_a}/config")
    data = {
        "csrf_token": csrf_from(page.text),
        "name": "Shop A",
        "owner_phone": "+13125550999",
        "timezone": "America/Chicago",
        "plan": "pilot",
        "tone": "Friendly and brief.",
        "services": "Brakes\nOil change\nTires",
        "price_ranges": "Front brakes | 300 | 450",
        "business_info": "address | 1200 W Grand Ave",
        "faqs": "Walk-ins? | Yes, before 4pm.",
        "handoff_keywords": "accident, tow, lawyer",
        "handoff_notes": "Anything over $1500.",
        "max_misunderstandings": "2",
        "enabled_languages": "en, es",
        "owner_language": "en",
        "default_customer_language": "en",
        "country_language_overrides": "{}",
        "calendar_id": "",
        "booking": '{"slot_minutes": 30, "duration_minutes": 45}',
        "channel_style": '{"sms": {"max_chars": 320, "rules": "Short."}}',
        "missed_call_channel": "sms",
        "owner_channel": "auto",
    }
    for day in (
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday",
    ):
        data[f"hours_{day}"] = "8:00-18:00"
    data["hours_sunday"] = "closed"
    data.update(overrides)
    return data


class TestSavingConfig:
    async def test_a_valid_save_sticks(self, admin_client, shop_a):
        data = await config_form_data(admin_client, shop_a)
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/config", data=data
        )
        assert response.status_code == 303

        async with session_scope() as session:
            config = (
                await session.execute(
                    select(ClientConfig).where(ClientConfig.client_id == shop_a)
                )
            ).scalar_one()
        assert config.services == ["Brakes", "Oil change", "Tires"]
        assert config.price_ranges == [
            {"service": "Front brakes", "low": 300, "high": 450}
        ]
        assert config.faqs == [{"q": "Walk-ins?", "a": "Yes, before 4pm."}]
        assert config.business_info == {"address": "1200 W Grand Ave"}
        assert config.hours["sunday"] == "closed"
        assert config.handoff_rules["keywords"] == ["accident", "tow", "lawyer"]
        assert config.booking["duration_minutes"] == 45

    async def test_saving_stamps_a_prompt_version(self, admin_client, shop_a):
        """The column existed from Phase 1 and nothing ever wrote it. Now the
        console does, so there is a record of which prompt is live."""
        async with session_scope() as session:
            before = (
                await session.execute(
                    select(ClientConfig.prompt_version).where(
                        ClientConfig.client_id == shop_a
                    )
                )
            ).scalar_one()
        assert before is None

        data = await config_form_data(admin_client, shop_a)
        await admin_client.post(f"/admin/shops/{shop_a}/config", data=data)

        async with session_scope() as session:
            after = (
                await session.execute(
                    select(ClientConfig.prompt_version).where(
                        ClientConfig.client_id == shop_a
                    )
                )
            ).scalar_one()
        assert after and len(after) == 16

    async def test_saving_drops_the_cached_prompt(self, admin_client, shop_a):
        """Without this the shop answers from a stale prompt for a minute."""
        async with session_scope() as session:
            before = await load_shop(session, shop_a)
        assert "Oil change" in before.system_prompt("sms")[0]

        data = await config_form_data(
            admin_client, shop_a, services="Transmission rebuilds only"
        )
        await admin_client.post(f"/admin/shops/{shop_a}/config", data=data)

        async with session_scope() as session:
            after = await load_shop(session, shop_a)
        text = after.system_prompt("sms")[0]
        assert "Transmission rebuilds only" in text
        assert "Oil change" not in text

    async def test_bad_hours_are_refused_and_nothing_is_written(
        self, admin_client, shop_a
    ):
        data = await config_form_data(admin_client, shop_a, hours_tuesday="8-6pm")
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/config", data=data
        )
        assert response.status_code == 400
        assert "not a time range" in response.text
        # What they typed is still in the form.
        assert "8-6pm" in response.text
        async with session_scope() as session:
            config = (
                await session.execute(
                    select(ClientConfig).where(ClientConfig.client_id == shop_a)
                )
            ).scalar_one()
        assert config.services == ["Brakes", "Oil change"]

    async def test_a_backwards_price_range_is_refused(self, admin_client, shop_a):
        data = await config_form_data(
            admin_client, shop_a, price_ranges="Brakes | 450 | 300"
        )
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/config", data=data
        )
        assert response.status_code == 400
        assert "is above the high" in response.text

    async def test_broken_json_is_refused_with_the_field_named(
        self, admin_client, shop_a
    ):
        data = await config_form_data(admin_client, shop_a, booking="{nope}")
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/config", data=data
        )
        assert response.status_code == 400
        assert "Booking" in response.text

    async def test_a_save_records_what_changed(self, admin_client, shop_a):
        data = await config_form_data(admin_client, shop_a, tone="Blunt.")
        await admin_client.post(f"/admin/shops/{shop_a}/config", data=data)
        async with session_scope() as session:
            rows = await repo_admin.recent_audit(session, client_id=shop_a)
        saves = [row for row in rows if row.action == "shop.config"]
        assert saves
        assert "tone" in saves[0].detail["changed"]
        assert saves[0].detail["prompt_version"]


class TestStatusChanges:
    async def test_going_live_is_recorded(self, admin_client, shop_a):
        page = await admin_client.get(f"/admin/shops/{shop_a}")
        await admin_client.post(
            f"/admin/shops/{shop_a}/status",
            data={"status": "live", "csrf_token": csrf_from(page.text)},
        )
        async with session_scope() as session:
            rows = await repo_admin.recent_audit(session, client_id=shop_a)
        assert any(row.action == "shop.status" for row in rows)

    async def test_churning_a_shop_signs_its_owner_out(self, admin_client, shop_a):
        from app.models import SessionKind
        from app.web.auth import principal_from_token, start_session

        async with session_scope() as session:
            token, _ = await start_session(
                session,
                kind=SessionKind.owner,
                label="+13125550999",
                client_id=shop_a,
            )
        page = await admin_client.get(f"/admin/shops/{shop_a}")
        await admin_client.post(
            f"/admin/shops/{shop_a}/status",
            data={"status": "churned", "csrf_token": csrf_from(page.text)},
        )
        async with session_scope() as session:
            assert (
                await principal_from_token(session, token, kind=SessionKind.owner)
            ) is None

    async def test_an_unknown_status_is_rejected(self, admin_client, shop_a):
        page = await admin_client.get(f"/admin/shops/{shop_a}")
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/status",
            data={"status": "exploded", "csrf_token": csrf_from(page.text)},
        )
        assert response.status_code == 400


class TestPromptView:
    async def test_it_shows_what_claude_is_actually_sent(self, admin_client, shop_a):
        page = await admin_client.get(f"/admin/shops/{shop_a}/prompt")
        assert page.status_code == 200
        assert "Never state a firm price" in page.text
        assert "handoff_to_human" in page.text

    async def test_each_channel_renders_differently(self, admin_client, shop_a):
        sms = await admin_client.get(f"/admin/shops/{shop_a}/prompt?channel=sms")
        voice = await admin_client.get(f"/admin/shops/{shop_a}/prompt?channel=voice")
        assert sms.text != voice.text
        assert "Spoken sentences only" in voice.text

    async def test_a_fresh_save_is_not_reported_as_drift(
        self, admin_client, shop_a
    ):
        """`prompt_version` stores the hash of the *base* prompt. The page also
        shows the channel prompt's hash, which includes the channel style block
        - comparing those two reported drift on every single page view."""
        data = await config_form_data(admin_client, shop_a)
        saved = await admin_client.post(f"/admin/shops/{shop_a}/config", data=data)
        assert saved.status_code == 303
        for channel in ("sms", "whatsapp", "voice", "simulator"):
            page = await admin_client.get(
                f"/admin/shops/{shop_a}/prompt?channel={channel}"
            )
            assert "drifted" not in page.text, channel

    async def test_real_drift_is_reported(self, admin_client, shop_a):
        """Config edited behind the console's back - a hand-run UPDATE, or a
        change to the prompt builder itself."""
        from sqlalchemy import update as sql_update

        data = await config_form_data(admin_client, shop_a)
        await admin_client.post(f"/admin/shops/{shop_a}/config", data=data)
        async with session_scope() as session:
            await session.execute(
                sql_update(ClientConfig)
                .where(ClientConfig.client_id == shop_a)
                .values(tone="Changed without saving.")
            )
        page = await admin_client.get(f"/admin/shops/{shop_a}/prompt")
        assert "drifted" in page.text

    async def test_an_unknown_channel_falls_back(self, admin_client, shop_a):
        page = await admin_client.get(
            f"/admin/shops/{shop_a}/prompt?channel=telepathy"
        )
        assert page.status_code == 200


class TestConversationViews:
    async def test_a_transcript_shows_what_was_never_delivered(
        self, admin_client, shop_a
    ):
        """The console must show blocked and draft rows; the agent's own
        history deliberately hides them."""
        from app.models import Direction, MessageStatus
        from app.repo import add_message, find_or_create_conversation, find_or_create_customer

        async with session_scope() as session:
            customer = await find_or_create_customer(session, shop_a, "+13125556666")
            conversation = await find_or_create_conversation(
                session, shop_a, customer.id, Channel.sms
            )
            await add_message(
                session, shop_a, conversation.id,
                direction=Direction.inbound, channel=Channel.sms, body="hello",
            )
            await add_message(
                session, shop_a, conversation.id,
                direction=Direction.outbound, channel=Channel.sms,
                body="never sent", status=MessageStatus.blocked,
            )
            conversation_id = conversation.id

        page = await admin_client.get(
            f"/admin/shops/{shop_a}/conversations/{conversation_id}"
        )
        assert page.status_code == 200
        assert "never sent" in page.text
        assert "never delivered" in page.text

    async def test_an_unknown_conversation_is_a_404_page(self, admin_client, shop_a):
        import uuid

        page = await admin_client.get(
            f"/admin/shops/{shop_a}/conversations/{uuid.uuid4()}"
        )
        assert page.status_code == 404
        assert "<html" in page.text  # an HTML error page, not a JSON blob

    async def test_an_unknown_shop_is_a_404_page(self, admin_client, db):
        import uuid

        page = await admin_client.get(f"/admin/shops/{uuid.uuid4()}")
        assert page.status_code == 404


class TestUsagePage:
    async def test_it_reports_spend_and_cache_share(self, admin_client, shop_a):
        from app.repo import record_usage

        async with session_scope() as session:
            await record_usage(
                session, shop_a, type="claude", model="claude-haiku-5-5",
                input_tokens=100, output_tokens=40,
                cache_read_input_tokens=3000, cost=0.0004,
            )
        page = await admin_client.get("/admin/usage")
        assert page.status_code == 200
        assert "claude-haiku-5-5" in page.text
        assert "$0.0004" in page.text
        # 3000 of 3100 input tokens came from the cache.
        assert "96.8%" in page.text

    async def test_it_warns_when_nothing_is_cached(self, admin_client, shop_a):
        from app.repo import record_usage

        async with session_scope() as session:
            await record_usage(
                session, shop_a, type="claude", model="claude-haiku-5-5",
                input_tokens=3000, output_tokens=40, cost=0.001,
            )
        page = await admin_client.get("/admin/usage")
        assert "Nothing has been read from the prompt cache" in page.text

    async def test_the_window_can_be_changed(self, admin_client, shop_a):
        page = await admin_client.get("/admin/usage?days=7")
        assert "last 7 days" in page.text

    async def test_a_silly_window_is_clamped(self, admin_client, shop_a):
        page = await admin_client.get("/admin/usage?days=99999")
        assert "last 90 days" in page.text


class TestSignupsPage:
    async def test_a_signup_can_be_marked_contacted(self, admin_client, client, db):
        await client.post(
            "/signup",
            data={"business_name": "Cicero Tire", "phone": "+13125550155"},
        )
        page = await admin_client.get("/admin/signups")
        assert "Cicero Tire" in page.text

        async with session_scope() as session:
            signups = await repo_admin.list_signups(session)
        response = await admin_client.post(
            f"/admin/signups/{signups[0].id}",
            data={"status": "contacted", "csrf_token": csrf_from(page.text)},
        )
        assert response.status_code == 303
        async with session_scope() as session:
            updated = await repo_admin.get_signup(session, signups[0].id)
        assert updated.status.value == "contacted"
        assert updated.contacted_at is not None

    async def test_converting_a_signup_links_it_to_the_new_shop(
        self, admin_client, client, db
    ):
        await client.post(
            "/signup",
            data={"business_name": "Cicero Tire", "phone": "+13125550155"},
        )
        async with session_scope() as session:
            signup = (await repo_admin.list_signups(session))[0]

        page = await admin_client.get(f"/admin/shops/new?signup={signup.id}")
        assert "Cicero Tire" in page.text
        await admin_client.post(
            "/admin/shops",
            data={
                "name": "Cicero Tire & Brake",
                "timezone": "America/Chicago",
                "owner_phone": "+13125550155",
                "enabled_languages": "es, en",
                "owner_language": "es",
                "signup_id": str(signup.id),
                "csrf_token": csrf_from(page.text),
            },
        )
        async with session_scope() as session:
            updated = await repo_admin.get_signup(session, signup.id)
        assert updated.status.value == "converted"
        assert updated.client_id is not None


class TestPartialConfigPosts:
    """A field the request did not mention must not be reset to a default."""

    async def test_omitting_the_summary_toggle_keeps_it_on(
        self, admin_client, shop_a
    ):
        data = await config_form_data(admin_client, shop_a)
        data.pop("summary_enabled", None)
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/config", data=data
        )
        assert response.status_code == 303
        async with session_scope() as session:
            config = (
                await session.execute(
                    select(ClientConfig).where(ClientConfig.client_id == shop_a)
                )
            ).scalar_one()
        assert config.summary_enabled is True

    async def test_the_form_can_still_turn_it_off(self, admin_client, shop_a):
        data = await config_form_data(admin_client, shop_a)
        data["summary_enabled"] = ""
        await admin_client.post(f"/admin/shops/{shop_a}/config", data=data)
        async with session_scope() as session:
            config = (
                await session.execute(
                    select(ClientConfig).where(ClientConfig.client_id == shop_a)
                )
            ).scalar_one()
        assert config.summary_enabled is False

    async def test_reminders_and_the_summary_hour_round_trip(
        self, admin_client, shop_a
    ):
        data = await config_form_data(admin_client, shop_a)
        data["reminder_hours_before"] = "48"
        data["summary_hour"] = "6"
        data["job_noun"] = "Pet"
        data["vertical"] = "pet"
        await admin_client.post(f"/admin/shops/{shop_a}/config", data=data)
        async with session_scope() as session:
            config = (
                await session.execute(
                    select(ClientConfig).where(ClientConfig.client_id == shop_a)
                )
            ).scalar_one()
        assert config.reminder_hours_before == 48
        assert config.summary_hour == 6
        assert config.job_noun == "pet"
        assert config.vertical == "pet"

    async def test_zero_turns_reminders_off_rather_than_being_rejected(
        self, admin_client, shop_a
    ):
        data = await config_form_data(admin_client, shop_a)
        data["reminder_hours_before"] = "0"
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/config", data=data
        )
        assert response.status_code == 303
        async with session_scope() as session:
            config = (
                await session.execute(
                    select(ClientConfig).where(ClientConfig.client_id == shop_a)
                )
            ).scalar_one()
        assert config.reminder_hours_before == 0

    async def test_an_out_of_range_summary_hour_is_refused(
        self, admin_client, shop_a
    ):
        data = await config_form_data(admin_client, shop_a)
        data["summary_hour"] = "99"
        response = await admin_client.post(
            f"/admin/shops/{shop_a}/config", data=data
        )
        assert response.status_code == 400
        assert "between 0 and 23" in response.text
