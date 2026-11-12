"""Sessions, admin passwords and owner one-time codes, against the database."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app import repo_admin
from app.db import session_scope
from app.models import AdminUser, AuthSession, Client, ClientStatus, SessionKind
from app.web import auth

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, make_admin

OWNER_PHONE = "+13125550999"


class TestAdminPassword:
    async def test_the_right_password_authenticates(self, db):
        admin_id = await make_admin()
        async with session_scope() as session:
            assert (
                await auth.authenticate_admin(session, ADMIN_EMAIL, ADMIN_PASSWORD)
            ) == admin_id

    async def test_the_wrong_password_does_not(self, db):
        await make_admin()
        async with session_scope() as session:
            assert (
                await auth.authenticate_admin(session, ADMIN_EMAIL, "nope")
            ) is None

    async def test_an_unknown_address_does_not(self, db):
        async with session_scope() as session:
            assert (
                await auth.authenticate_admin(session, "nobody@example.com", "x")
            ) is None

    async def test_a_deactivated_admin_cannot_sign_in(self, db):
        admin_id = await make_admin()
        async with session_scope() as session:
            await session.execute(
                update(AdminUser).where(AdminUser.id == admin_id).values(active=False)
            )
        async with session_scope() as session:
            assert (
                await auth.authenticate_admin(session, ADMIN_EMAIL, ADMIN_PASSWORD)
            ) is None

    async def test_the_email_is_case_insensitive(self, db):
        admin_id = await make_admin()
        async with session_scope() as session:
            assert (
                await auth.authenticate_admin(
                    session, "  STAFF@Example.com ", ADMIN_PASSWORD
                )
            ) == admin_id

    async def test_a_successful_login_is_recorded(self, db):
        admin_id = await make_admin()
        async with session_scope() as session:
            await auth.authenticate_admin(session, ADMIN_EMAIL, ADMIN_PASSWORD)
        async with session_scope() as session:
            admin = await repo_admin.get_admin(session, admin_id)
        assert admin.last_login_at is not None

    def test_lockout_after_repeated_failures(self):
        auth.reset_login_throttle()
        assert not auth.login_locked("victim@example.com")
        for _ in range(auth.MAX_LOGIN_FAILURES):
            auth.record_login_failure("victim@example.com")
        assert auth.login_locked("victim@example.com")
        # Another account is unaffected.
        assert not auth.login_locked("someone-else@example.com")
        auth.clear_login_failures("victim@example.com")
        assert not auth.login_locked("victim@example.com")


class TestSessions:
    async def test_a_token_round_trips_to_a_principal(self, db):
        admin_id = await make_admin()
        async with session_scope() as session:
            token, created = await auth.start_session(
                session, kind=SessionKind.admin, label=ADMIN_EMAIL,
                admin_user_id=admin_id,
            )
        async with session_scope() as session:
            loaded = await auth.principal_from_token(
                session, token, kind=SessionKind.admin
            )
        assert loaded is not None
        assert loaded.session_id == created.session_id
        assert loaded.csrf_token == created.csrf_token
        assert loaded.label == ADMIN_EMAIL

    async def test_only_the_hash_is_stored(self, db):
        """A database dump must not hand over live sessions."""
        admin_id = await make_admin()
        async with session_scope() as session:
            token, _ = await auth.start_session(
                session, kind=SessionKind.admin, label=ADMIN_EMAIL,
                admin_user_id=admin_id,
            )
        async with session_scope() as session:
            hashes = (
                (await session.execute(select(AuthSession.token_hash)))
                .scalars()
                .all()
            )
        assert token not in hashes
        assert all(len(value) == 64 for value in hashes)

    async def test_a_wrong_token_is_nothing(self, db):
        async with session_scope() as session:
            assert (
                await auth.principal_from_token(
                    session, "not-a-token", kind=SessionKind.admin
                )
            ) is None
            assert (
                await auth.principal_from_token(session, None, kind=SessionKind.admin)
            ) is None

    async def test_an_owner_token_is_not_an_admin_token(self, shop_a):
        """The two cookies are separate; so is the check on them."""
        async with session_scope() as session:
            token, _ = await auth.start_session(
                session, kind=SessionKind.owner, label=OWNER_PHONE, client_id=shop_a
            )
        async with session_scope() as session:
            assert (
                await auth.principal_from_token(
                    session, token, kind=SessionKind.admin
                )
            ) is None
            assert (
                await auth.principal_from_token(
                    session, token, kind=SessionKind.owner
                )
            ) is not None

    async def test_revoking_ends_it(self, shop_a):
        async with session_scope() as session:
            token, principal = await auth.start_session(
                session, kind=SessionKind.owner, label=OWNER_PHONE, client_id=shop_a
            )
        async with session_scope() as session:
            await auth.end_session(session, principal)
        async with session_scope() as session:
            assert (
                await auth.principal_from_token(
                    session, token, kind=SessionKind.owner
                )
            ) is None

    async def test_an_expired_session_is_dead(self, shop_a):
        async with session_scope() as session:
            token, principal = await auth.start_session(
                session, kind=SessionKind.owner, label=OWNER_PHONE, client_id=shop_a
            )
            await session.execute(
                update(AuthSession)
                .where(AuthSession.id == principal.session_id)
                .values(expires_at=repo_admin.utcnow() - timedelta(seconds=1))
            )
        async with session_scope() as session:
            assert (
                await auth.principal_from_token(
                    session, token, kind=SessionKind.owner
                )
            ) is None

    async def test_deactivating_an_admin_kills_their_live_session(self, db):
        admin_id = await make_admin()
        async with session_scope() as session:
            token, _ = await auth.start_session(
                session, kind=SessionKind.admin, label=ADMIN_EMAIL,
                admin_user_id=admin_id,
            )
            await session.execute(
                update(AdminUser).where(AdminUser.id == admin_id).values(active=False)
            )
        async with session_scope() as session:
            assert (
                await auth.principal_from_token(
                    session, token, kind=SessionKind.admin
                )
            ) is None

    async def test_pausing_a_shop_revokes_its_owner_sessions(self, shop_a, shop_b):
        async with session_scope() as session:
            token_a, _ = await auth.start_session(
                session, kind=SessionKind.owner, label=OWNER_PHONE, client_id=shop_a
            )
            token_b, _ = await auth.start_session(
                session, kind=SessionKind.owner, label="+13125550888",
                client_id=shop_b,
            )
        async with session_scope() as session:
            await repo_admin.revoke_sessions_for_client(session, shop_a)
        async with session_scope() as session:
            assert (
                await auth.principal_from_token(
                    session, token_a, kind=SessionKind.owner
                )
            ) is None
            # The neighbour is untouched.
            assert (
                await auth.principal_from_token(
                    session, token_b, kind=SessionKind.owner
                )
            ) is not None

    async def test_the_database_refuses_a_session_with_two_subjects(self, shop_a):
        """The check constraint, not the application, is the last word."""
        from sqlalchemy.exc import IntegrityError

        admin_id = await make_admin()
        with pytest.raises(IntegrityError):
            async with session_scope() as session:
                session.add(
                    AuthSession(
                        kind=SessionKind.admin,
                        token_hash="a" * 64,
                        csrf_token="x",
                        expires_at=repo_admin.utcnow() + timedelta(hours=1),
                        admin_user_id=admin_id,
                        client_id=shop_a,
                    )
                )

    async def test_the_database_refuses_a_session_with_no_subject(self, db):
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            async with session_scope() as session:
                session.add(
                    AuthSession(
                        kind=SessionKind.owner,
                        token_hash="b" * 64,
                        csrf_token="x",
                        expires_at=repo_admin.utcnow() + timedelta(hours=1),
                    )
                )


class TestOwnerCodes:
    async def test_a_real_owner_gets_a_code(self, shop_a):
        async with session_scope() as session:
            code = await auth.request_login_code(session, OWNER_PHONE)
        assert code and len(code) == 6
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, code) == shop_a

    async def test_a_messily_typed_number_still_works(self, shop_a):
        async with session_scope() as session:
            code = await auth.request_login_code(session, "(312) 555-0999")
        assert code
        async with session_scope() as session:
            assert (
                await auth.verify_login_code(session, "312 555 0999", code) == shop_a
            )

    async def test_an_unknown_number_gets_nothing_and_stores_nothing(self, shop_a):
        """No enumeration: the caller renders the same page either way."""
        from app.models import LoginCode

        async with session_scope() as session:
            assert await auth.request_login_code(session, "+13125559999") is None
        async with session_scope() as session:
            rows = (await session.execute(select(LoginCode))).scalars().all()
        assert rows == []

    async def test_an_invalid_number_is_rejected_quietly(self, shop_a):
        async with session_scope() as session:
            assert await auth.request_login_code(session, "hello") is None

    async def test_the_wrong_code_fails(self, shop_a):
        async with session_scope() as session:
            code = await auth.request_login_code(session, OWNER_PHONE)
        wrong = "000000" if code != "000000" else "111111"
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, wrong) is None

    async def test_a_code_is_single_use(self, shop_a):
        async with session_scope() as session:
            code = await auth.request_login_code(session, OWNER_PHONE)
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, code) == shop_a
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, code) is None

    async def test_a_new_code_supersedes_the_old_one(self, shop_a):
        async with session_scope() as session:
            first = await auth.request_login_code(session, OWNER_PHONE)
        async with session_scope() as session:
            second = await auth.request_login_code(session, OWNER_PHONE)
        assert first != second
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, first) is None
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, second) == shop_a

    async def test_guessing_is_capped(self, shop_a):
        """Six digits is 10^6, so the attempt limit is the actual protection."""
        async with session_scope() as session:
            code = await auth.request_login_code(session, OWNER_PHONE)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(auth.MAX_CODE_ATTEMPTS):
            async with session_scope() as session:
                assert await auth.verify_login_code(session, OWNER_PHONE, wrong) is None
        # The code is burned now - even the right one no longer works.
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, code) is None

    async def test_an_expired_code_fails(self, shop_a):
        from app.models import LoginCode

        async with session_scope() as session:
            code = await auth.request_login_code(session, OWNER_PHONE)
            await session.execute(
                update(LoginCode).values(
                    expires_at=repo_admin.utcnow() - timedelta(seconds=1)
                )
            )
        async with session_scope() as session:
            assert await auth.verify_login_code(session, OWNER_PHONE, code) is None

    async def test_requests_are_rate_limited(self, shop_a):
        issued = []
        for _ in range(auth.MAX_CODES_PER_WINDOW + 2):
            async with session_scope() as session:
                issued.append(await auth.request_login_code(session, OWNER_PHONE))
        assert len([c for c in issued if c]) == auth.MAX_CODES_PER_WINDOW
        assert issued[-1] is None

    async def test_a_churned_shop_cannot_be_signed_into(self, shop_a):
        async with session_scope() as session:
            await session.execute(
                update(Client)
                .where(Client.id == shop_a)
                .values(status=ClientStatus.churned)
            )
        async with session_scope() as session:
            assert await auth.request_login_code(session, OWNER_PHONE) is None

    async def test_a_shadow_shop_can_be_signed_into(self, shop_a):
        """Shadow mode is onboarding - that is exactly when an owner looks."""
        async with session_scope() as session:
            await session.execute(
                update(Client)
                .where(Client.id == shop_a)
                .values(status=ClientStatus.shadow)
            )
        async with session_scope() as session:
            assert await auth.request_login_code(session, OWNER_PHONE) is not None


async def test_principal_shop_id_refuses_to_guess(db):
    principal = auth.Principal(
        kind=SessionKind.admin,
        session_id=uuid.uuid4(),
        csrf_token="x",
        label="staff",
        admin_user_id=uuid.uuid4(),
    )
    with pytest.raises(RuntimeError):
        _ = principal.shop_id


async def test_expired_sessions_can_be_purged(shop_a):
    from datetime import timedelta as delta

    async with session_scope() as session:
        _, principal = await auth.start_session(
            session, kind=SessionKind.owner, label=OWNER_PHONE, client_id=shop_a
        )
        await session.execute(
            update(AuthSession)
            .where(AuthSession.id == principal.session_id)
            .values(expires_at=repo_admin.utcnow() - delta(days=60))
        )
    async with session_scope() as session:
        assert await repo_admin.purge_expired_sessions(session) == 1


def test_password_hashing_is_used_for_admins_only():
    """Guard against someone "simplifying" the owner code to use scrypt: a
    six-digit code is cheap to hash and expensive to brute-force only because
    of the attempt limit, and 16 MB per guess would be a denial of service."""
    import inspect

    source = inspect.getsource(auth.verify_login_code)
    assert "scrypt" not in source
    assert "code_matches" in source
