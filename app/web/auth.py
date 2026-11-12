"""Authentication for the console and the owner dashboard.

Two kinds of login, one mechanism:

  * **Admin** - our staff, email + password, 12-hour session.
  * **Owner** - a shop owner, a six-digit code texted to the `owner_phone`
    already in their config, 14-day session. No password for them to lose, and
    no email provider needed.

The cookie holds a random 32-byte token; only its SHA-256 is stored, so a
database dump does not hand over live sessions. The two kinds use different
cookie names, so being signed in to the console does not interfere with opening
a shop's own dashboard.

**The isolation rule.** An owner session is bound to exactly one `client_id`,
and an owner request may only ever learn its shop from that session - never
from a path, query string or form field. Every owner route therefore takes
`client_id` from `Principal`, and `tests/test_web_isolation.py` tries to break
that.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import repo_admin
from app.config import get_settings
from app.db import get_session
from app.models import SessionKind
from app.web import phone as phone_util
from app.web.passwords import (
    code_matches,
    hash_code,
    hash_token,
    new_csrf_token,
    new_login_code,
    new_session_token,
    verify_password,
)

log = logging.getLogger(__name__)

ADMIN_COOKIE = "fd_admin"
OWNER_COOKIE = "fd_shop"

ADMIN_TTL = timedelta(hours=12)
OWNER_TTL = timedelta(days=14)

CODE_TTL = timedelta(minutes=10)
#: Six digits is only 10^6, so the attempt limit is what actually protects it.
MAX_CODE_ATTEMPTS = 5
CODE_WINDOW = timedelta(minutes=15)
MAX_CODES_PER_WINDOW = 3

COOKIES = {SessionKind.admin: ADMIN_COOKIE, SessionKind.owner: OWNER_COOKIE}
TTLS = {SessionKind.admin: ADMIN_TTL, SessionKind.owner: OWNER_TTL}
LOGIN_PATHS = {SessionKind.admin: "/admin/login", SessionKind.owner: "/shop/login"}


class NotAuthenticated(Exception):
    """Raised by the dependencies; turned into a redirect by the app handler."""

    def __init__(self, kind: SessionKind, next_url: str = "") -> None:
        self.kind = kind
        self.next_url = next_url
        super().__init__(LOGIN_PATHS[kind])

    @property
    def login_path(self) -> str:
        return LOGIN_PATHS[self.kind]


@dataclass(frozen=True, slots=True)
class Principal:
    kind: SessionKind
    session_id: uuid.UUID
    csrf_token: str
    label: str
    admin_user_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None

    @property
    def is_admin(self) -> bool:
        return self.kind is SessionKind.admin

    @property
    def shop_id(self) -> uuid.UUID:
        """The only sanctioned source of `client_id` for an owner request."""
        if self.client_id is None:
            raise RuntimeError("principal has no client_id")
        return self.client_id


# ------------------------------------------------------------------ session cookies


def _cookie_secure() -> bool:
    return get_settings().public_base_url.lower().startswith("https")


def set_cookie(response: Response, kind: SessionKind, token: str) -> None:
    response.set_cookie(
        COOKIES[kind],
        token,
        max_age=int(TTLS[kind].total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )


def clear_cookie(response: Response, kind: SessionKind) -> None:
    response.delete_cookie(COOKIES[kind], path="/")


async def start_session(
    session: AsyncSession,
    *,
    kind: SessionKind,
    label: str,
    admin_user_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    user_agent: str | None = None,
) -> tuple[str, Principal]:
    """Create a session row and return (raw cookie token, principal)."""
    token = new_session_token()
    csrf = new_csrf_token()
    row = await repo_admin.insert_session(
        session,
        kind=kind,
        token_hash=hash_token(token),
        csrf_token=csrf,
        expires_at=repo_admin.utcnow() + TTLS[kind],
        admin_user_id=admin_user_id,
        client_id=client_id,
        user_agent=user_agent,
    )
    principal = Principal(
        kind=kind,
        session_id=row.id,
        csrf_token=csrf,
        label=label,
        admin_user_id=admin_user_id,
        client_id=client_id,
    )
    return token, principal


async def principal_from_token(
    session: AsyncSession, token: str | None, *, kind: SessionKind
) -> Principal | None:
    if not token:
        return None
    row = await repo_admin.session_by_token_hash(session, hash_token(token))
    if row is None or row.kind is not kind:
        return None

    label = ""
    if kind is SessionKind.admin:
        admin = (
            await repo_admin.get_admin(session, row.admin_user_id)
            if row.admin_user_id
            else None
        )
        # A deactivated or deleted admin's live session must stop working.
        if admin is None or not admin.active:
            await repo_admin.revoke_session(session, row.id)
            return None
        label = admin.email

    await repo_admin.touch_session(session, row.id)
    return Principal(
        kind=row.kind,
        session_id=row.id,
        csrf_token=row.csrf_token,
        label=label,
        admin_user_id=row.admin_user_id,
        client_id=row.client_id,
    )


async def end_session(session: AsyncSession, principal: Principal) -> None:
    await repo_admin.revoke_session(session, principal.session_id)


# --------------------------------------------------------------- admin password login

#: In-process failure counter. It resets on restart and is not shared between
#: workers, which is honest about what it is: a speed bump for an internal
#: console, not a distributed rate limiter. Postgres-backed if we ever run the
#: console on more than one process.
_FAILURES: dict[str, list[float]] = {}
MAX_LOGIN_FAILURES = 10
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _recent_failures(email: str) -> list[float]:
    cutoff = time.monotonic() - LOGIN_LOCKOUT_SECONDS
    kept = [t for t in _FAILURES.get(email, []) if t > cutoff]
    if kept:
        _FAILURES[email] = kept
    else:
        _FAILURES.pop(email, None)
    return kept


def login_locked(email: str) -> bool:
    return len(_recent_failures(email.strip().lower())) >= MAX_LOGIN_FAILURES


def record_login_failure(email: str) -> None:
    key = email.strip().lower()
    _FAILURES.setdefault(key, []).append(time.monotonic())


def clear_login_failures(email: str) -> None:
    _FAILURES.pop(email.strip().lower(), None)


def reset_login_throttle() -> None:
    """Test hook."""
    _FAILURES.clear()


async def authenticate_admin(
    session: AsyncSession, email: str, password: str
) -> uuid.UUID | None:
    """Returns the admin's id, or None. Never says *which* half was wrong."""
    admin = await repo_admin.get_admin_by_email(session, email)
    if admin is None or not admin.active:
        # Spend the same work on a missing account, so the response time does
        # not say whether the address exists.
        verify_password(password, "scrypt$16384$8$1$AAAA$AAAA")
        return None
    if not verify_password(password, admin.password_hash):
        return None
    await repo_admin.touch_admin_login(session, admin.id)
    return admin.id


# ------------------------------------------------------------- owner code login


async def request_login_code(session: AsyncSession, raw_phone: str) -> str | None:
    """Text a one-time code to a shop owner.

    Returns the code when it was generated (tests and local dev read it from
    the log), None otherwise. **The caller must render the same page either
    way** - whether the number belongs to a shop, is rate limited, or is not a
    customer of ours at all. Anything else turns this form into a directory of
    which numbers are our clients.
    """
    phone = phone_util.normalise(raw_phone)
    if phone is None:
        return None

    client = await repo_admin.client_by_owner_phone(session, phone)
    if client is None:
        log.info("login code requested for a number that owns no shop")
        return None

    since = repo_admin.utcnow() - CODE_WINDOW
    if await repo_admin.codes_sent_since(session, phone, since) >= MAX_CODES_PER_WINDOW:
        log.warning("login code rate limit hit for client %s", client.id)
        return None

    code = new_login_code()
    # A new code supersedes any older one, so only the latest can be used.
    await repo_admin.expire_codes_for_phone(session, phone)
    await repo_admin.insert_login_code(
        session,
        client_id=client.id,
        phone=phone,
        code_hash=hash_code(code),
        expires_at=repo_admin.utcnow() + CODE_TTL,
    )

    await _send_code(session, client.id, code)
    return code


async def _send_code(session: AsyncSession, client_id: uuid.UUID, code: str) -> None:
    """Deliver through the owner's normal channel.

    Reuses `core.owner.send_to_owner`, so the code respects `owner_channel` and
    the WhatsApp 24-hour window, and falls back to the simulator in local dev.
    A delivery failure is logged, not raised: the code is already stored, and
    in development the log line is how you read it.
    """
    from app.core.owner import send_to_owner
    from app.core.router import load_shop

    body = (
        f"Your code is {code}. It expires in "
        f"{int(CODE_TTL.total_seconds() // 60)} minutes. "
        f"If you did not ask to sign in, ignore this message."
    )
    try:
        shop = await load_shop(session, client_id)
        ok, detail = await send_to_owner(session, shop, body)
        if not ok:
            log.error("could not text the login code: %s", detail)
    except Exception as exc:
        log.error("could not text the login code: %s", exc)
    # Visible in `arq`/uvicorn logs so local development needs no SMS at all.
    log.info("login code for client %s: %s", client_id, code)


async def verify_login_code(
    session: AsyncSession, raw_phone: str, code: str
) -> uuid.UUID | None:
    """Returns the `client_id` on success.

    Must not raise on a bad code: the attempt counter has to survive the
    request, and an exception would roll the increment back.
    """
    phone = phone_util.normalise(raw_phone)
    if phone is None:
        return None

    row = await repo_admin.active_login_code(session, phone)
    if row is None:
        return None

    attempts = await repo_admin.bump_code_attempts(session, row.id)
    if attempts > MAX_CODE_ATTEMPTS:
        await repo_admin.consume_login_code(session, row.id)
        log.warning("login code burned after %s attempts", attempts)
        return None

    if not code_matches((code or "").strip(), row.code_hash):
        return None

    await repo_admin.consume_login_code(session, row.id)
    return row.client_id


# ----------------------------------------------------------- FastAPI dependencies


def _next_url(request: Request) -> str:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return target


async def admin_principal(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal:
    principal = await principal_from_token(
        session, request.cookies.get(ADMIN_COOKIE), kind=SessionKind.admin
    )
    if principal is None:
        raise NotAuthenticated(SessionKind.admin, _next_url(request))
    return principal


async def owner_principal(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal:
    principal = await principal_from_token(
        session, request.cookies.get(OWNER_COOKIE), kind=SessionKind.owner
    )
    if principal is None:
        raise NotAuthenticated(SessionKind.owner, _next_url(request))
    return principal


async def maybe_admin(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal | None:
    """For pages that render differently when signed in, but do not require it."""
    return await principal_from_token(
        session, request.cookies.get(ADMIN_COOKIE), kind=SessionKind.admin
    )


async def _check_csrf(request: Request, principal: Principal) -> None:
    """Double-submit check on every state-changing POST.

    SameSite=Lax already blocks a cross-site form POST from carrying the
    cookie; this is the second lock. Starlette caches the parsed form on the
    request, so reading it here does not consume the body for the handler.
    """
    form = await request.form()
    supplied = str(form.get("csrf_token") or "")
    import hmac

    if not supplied or not hmac.compare_digest(supplied, principal.csrf_token):
        log.warning("csrf failure on %s", request.url.path)
        raise HTTPException(status_code=403, detail="Form expired - please try again.")


async def admin_post(
    request: Request, principal: Principal = Depends(admin_principal)
) -> Principal:
    await _check_csrf(request, principal)
    return principal


async def owner_post(
    request: Request, principal: Principal = Depends(owner_principal)
) -> Principal:
    await _check_csrf(request, principal)
    return principal
