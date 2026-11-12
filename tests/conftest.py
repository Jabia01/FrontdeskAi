"""Test fixtures.

Tests run against a dedicated `frontdesk_test` database on the same local
Postgres, created on first use and truncated between tests. Postgres (not
SQLite) because the schema uses JSONB, ARRAY and native enums.
"""

from __future__ import annotations

import os
import uuid

import pytest

# Point the app at the test database before anything reads settings.
#
# The parts come from the environment so the same suite runs against the local
# docker-compose Postgres (the defaults) and against a CI service container on
# the standard port, without CI having to override DATABASE_URL - which this
# module sets itself a few lines down.
_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = os.environ.get("TEST_PG_PORT", "5434")
_PG_USER = os.environ.get("TEST_PG_USER", "frontdesk")
_PG_PASSWORD = os.environ.get("TEST_PG_PASSWORD", "frontdesk")

_ADMIN_URL = f"postgresql://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/postgres"
_TEST_DB = os.environ.get("TEST_PG_DB", "frontdesk_test")
_TEST_URL = (
    f"postgresql+asyncpg://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_TEST_DB}"
)

os.environ["DATABASE_URL"] = _TEST_URL
os.environ.setdefault("TWILIO_VALIDATE_SIGNATURE", "false")
os.environ.setdefault("AI_ENABLED", "true")

from app.config import get_settings
from app.db import dispose_engine, get_engine, session_scope
from app.models import (
    Base,
    Channel,
    ChannelEndpoint,
    Client,
    ClientConfig,
    ClientStatus,
)

get_settings.cache_clear()

# Register the channel adapters once for the whole suite, so no test module
# can fail for having forgotten to import them.
from app.channels.base import load_adapters

load_adapters()


async def _ensure_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(_ADMIN_URL)
    try:
        exists = await conn.fetchval(
            "select 1 from pg_database where datname = $1", _TEST_DB
        )
        if not exists:
            await conn.execute(f'create database "{_TEST_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def _schema():
    """Created once per session. Not autouse - pure-logic tests need no database.

    The schema is dropped and rebuilt from the models so it can never drift from
    them (`create_all` alone would not add a column to an existing table).
    """
    from sqlalchemy import text

    await _ensure_database()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("drop schema public cascade"))
        await conn.execute(text("create schema public"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture
async def db(_schema):
    """Request this (directly or via shop_a / shop_b) for a clean database."""
    from sqlalchemy import text

    from app.core.router import invalidate

    engine = get_engine()
    tables = ", ".join(f'"{t}"' for t in Base.metadata.tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"truncate {tables} restart identity cascade"))
    invalidate()
    yield
    invalidate()


async def make_shop(
    name: str,
    *,
    enabled_languages: list[str] | None = None,
    owner_language: str = "en",
    default_customer_language: str = "en",
    country_language_overrides: dict | None = None,
    sms_number: str | None = None,
    whatsapp_id: str | None = None,
    sim_id: str | None = None,
    handoff_keywords: list[str] | None = None,
    owner_phone: str = "+13125550999",
) -> uuid.UUID:
    """Create a shop with config and endpoints. Returns its client_id."""
    suffix = uuid.uuid4().hex[:8]
    async with session_scope() as session:
        client = Client(
            name=name,
            timezone="America/Chicago",
            languages=enabled_languages or ["en"],
            owner_phone=owner_phone,
            plan="pilot",
            status=ClientStatus.live,
        )
        session.add(client)
        await session.flush()

        session.add(
            ClientConfig(
                client_id=client.id,
                business_info={"address": "1 Test St", "phone": "+13125550100"},
                services=["Brakes", "Oil change"],
                hours={"monday": "8:00-18:00", "sunday": "closed"},
                price_ranges=[{"service": "Front brakes", "low": 300, "high": 450}],
                tone="Friendly and brief.",
                handoff_rules={
                    "keywords": handoff_keywords
                    if handoff_keywords is not None
                    else ["accident", "lawyer", "tow", "refund"],
                    "max_misunderstandings": 2,
                },
                faqs=[{"q": "Appointment needed?", "a": "Walk-ins welcome."}],
                channel_style={
                    "sms": {"max_chars": 320, "rules": "Short."},
                    "whatsapp": {"max_chars": 700, "rules": "Short paragraphs."},
                    "simulator": {"max_chars": 700, "rules": "Short paragraphs."},
                    "voice": {"max_chars": 240, "rules": "Spoken sentences only."},
                },
                owner_language=owner_language,
                default_customer_language=default_customer_language,
                enabled_languages=enabled_languages or ["en"],
                country_language_overrides=country_language_overrides or {},
            )
        )

        endpoints = [
            (Channel.sms, sms_number or f"+1312555{suffix[:4]}"),
            (Channel.whatsapp, whatsapp_id or f"whatsapp:+1312555{suffix[:4]}"),
            (Channel.simulator, sim_id or f"sim-{suffix}"),
        ]
        for channel, external_id in endpoints:
            session.add(
                ChannelEndpoint(
                    client_id=client.id,
                    channel=channel,
                    provider="twilio" if channel != Channel.simulator else "local",
                    external_id=external_id,
                    number=external_id.replace("whatsapp:", "").replace(
                        f"sim-{suffix}", "+13125550100"
                    ),
                    active=True,
                )
            )

        return client.id


@pytest.fixture
async def shop_a(db) -> uuid.UUID:
    return await make_shop(
        "Shop A",
        enabled_languages=["en", "es"],
        sms_number="+13125550001",
        whatsapp_id="whatsapp:+13125550001",
        sim_id="sim-a",
    )


@pytest.fixture
async def shop_b(db) -> uuid.UUID:
    return await make_shop(
        "Shop B",
        enabled_languages=["en"],
        # A different owner from shop A, or an owner login could not tell the
        # two apart.
        owner_phone="+13125550888",
        sms_number="+13125550002",
        whatsapp_id="whatsapp:+13125550002",
        sim_id="sim-b",
    )


# --------------------------------------------------------------- phase 3: the web
#
# ASGITransport does not run the app's lifespan, which is fine: the adapters are
# registered at import time above, and nothing else in the lifespan is needed to
# serve a page.

ADMIN_EMAIL = "staff@example.com"
ADMIN_PASSWORD = "console-password-123"


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        yield http


@pytest.fixture(autouse=True)
def _reset_web_throttles():
    """The login and signup limiters are in-process, so they leak between
    tests unless they are cleared."""
    from app.web.auth import reset_login_throttle
    from app.web.marketing import reset_signup_throttle

    reset_login_throttle()
    reset_signup_throttle()
    yield


async def make_admin(
    email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD
) -> uuid.UUID:
    from app import repo_admin
    from app.web.passwords import hash_password

    async with session_scope() as session:
        admin = await repo_admin.create_admin(
            session, email=email, password_hash=hash_password(password), name="Staff"
        )
        return admin.id


def csrf_from(html: str) -> str:
    """Pull a CSRF token out of a rendered page, the way a browser would."""
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token on the page"
    return match.group(1)


@pytest.fixture
async def admin_client(client, db):
    """A signed-in console session."""
    await make_admin()
    response = await client.post(
        "/admin/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 303, response.text
    return client


@pytest.fixture
async def owner_client(client, shop_a):
    """A signed-in owner session for shop A."""
    from app.web.auth import request_login_code

    async with session_scope() as session:
        code = await request_login_code(session, "+13125550999")
    assert code, "no login code was issued"
    response = await client.post(
        "/shop/verify", data={"phone": "+13125550999", "code": code}
    )
    assert response.status_code == 303, response.text
    return client
