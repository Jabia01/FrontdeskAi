"""Data access for the web layer's *non-tenant* tables.

`app/repo.py` and `app/repo_shop.py` both enforce the rule that every helper
takes `client_id` first. This module deliberately does not, because nothing it
touches belongs to a shop:

  * `admin_users`, `auth_sessions`, `login_codes` - authentication
  * `signups` - prospective shops from the landing page, before a client exists
  * `audit_log` - append-only record of who changed what
  * the cross-tenant rollups the console's shop list and usage page are made of

Because these functions can read across tenants, every caller must be a
trusted, non-tenant context: an admin session in the console, or the scheduled
sweeps in `app/queue/tasks.py`, which use `live_client_ids` to find the
businesses and then do all per-business work through `client_id`-scoped helpers.
`tests/test_web_isolation.py` asserts that `app/web/owner.py` never imports this
module, so an owner request cannot reach a cross-tenant query even by accident.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Numeric, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AdminUser,
    AuditLog,
    AuthSession,
    Client,
    ClientStatus,
    Conversation,
    ConversationStatus,
    LoginCode,
    Message,
    MessageStatus,
    SessionKind,
    Signup,
    SignupStatus,
    UsageEvent,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------- admin users


async def get_admin_by_email(session: AsyncSession, email: str) -> AdminUser | None:
    result = await session.execute(
        select(AdminUser).where(AdminUser.email == email.strip().lower())
    )
    return result.scalar_one_or_none()


async def get_admin(
    session: AsyncSession, admin_user_id: uuid.UUID
) -> AdminUser | None:
    result = await session.execute(
        select(AdminUser).where(AdminUser.id == admin_user_id)
    )
    return result.scalar_one_or_none()


async def create_admin(
    session: AsyncSession, *, email: str, password_hash: str, name: str | None = None
) -> AdminUser:
    admin = AdminUser(
        email=email.strip().lower(), password_hash=password_hash, name=name
    )
    session.add(admin)
    await session.flush()
    return admin


async def set_admin_password(
    session: AsyncSession, admin_user_id: uuid.UUID, password_hash: str
) -> None:
    await session.execute(
        update(AdminUser)
        .where(AdminUser.id == admin_user_id)
        .values(password_hash=password_hash)
    )


async def touch_admin_login(session: AsyncSession, admin_user_id: uuid.UUID) -> None:
    await session.execute(
        update(AdminUser)
        .where(AdminUser.id == admin_user_id)
        .values(last_login_at=utcnow())
    )


async def list_admins(session: AsyncSession) -> list[AdminUser]:
    result = await session.execute(select(AdminUser).order_by(AdminUser.email))
    return list(result.scalars().all())


# ---------------------------------------------------------------------- sessions


async def insert_session(
    session: AsyncSession,
    *,
    kind: SessionKind,
    token_hash: str,
    csrf_token: str,
    expires_at: datetime,
    admin_user_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    user_agent: str | None = None,
) -> AuthSession:
    row = AuthSession(
        kind=kind,
        token_hash=token_hash,
        csrf_token=csrf_token,
        expires_at=expires_at,
        admin_user_id=admin_user_id,
        client_id=client_id,
        user_agent=(user_agent or "")[:300] or None,
    )
    session.add(row)
    await session.flush()
    return row


async def session_by_token_hash(
    session: AsyncSession, token_hash: str
) -> AuthSession | None:
    """Live sessions only - expired and revoked rows are not returned."""
    result = await session.execute(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
    )
    return result.scalar_one_or_none()


async def touch_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id)
        .values(last_seen_at=utcnow())
    )


async def revoke_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_sessions_for_client(
    session: AsyncSession, client_id: uuid.UUID
) -> None:
    """Used when a shop is paused or churned - their dashboard logins stop."""
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.client_id == client_id, AuthSession.revoked_at.is_(None)
        )
        .values(revoked_at=utcnow())
    )


async def revoke_sessions_for_admin(
    session: AsyncSession, admin_user_id: uuid.UUID
) -> None:
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.admin_user_id == admin_user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )


async def purge_expired_sessions(session: AsyncSession) -> int:
    """Housekeeping. Expired rows are already unusable; this just clears them."""
    from sqlalchemy import delete

    cutoff = utcnow() - timedelta(days=30)
    result = await session.execute(
        delete(AuthSession).where(AuthSession.expires_at < cutoff)
    )
    return result.rowcount or 0


# -------------------------------------------------------------------- login codes


async def insert_login_code(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    phone: str,
    code_hash: str,
    expires_at: datetime,
) -> LoginCode:
    row = LoginCode(
        client_id=client_id,
        phone=phone,
        code_hash=code_hash,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row


async def active_login_code(session: AsyncSession, phone: str) -> LoginCode | None:
    """The newest unconsumed, unexpired code for this number."""
    result = await session.execute(
        select(LoginCode)
        .where(
            LoginCode.phone == phone,
            LoginCode.consumed_at.is_(None),
            LoginCode.expires_at > utcnow(),
        )
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def bump_code_attempts(session: AsyncSession, code_id: uuid.UUID) -> int:
    result = await session.execute(
        update(LoginCode)
        .where(LoginCode.id == code_id)
        .values(attempts=LoginCode.attempts + 1)
        .returning(LoginCode.attempts)
    )
    return int(result.scalar_one())


async def consume_login_code(session: AsyncSession, code_id: uuid.UUID) -> None:
    await session.execute(
        update(LoginCode)
        .where(LoginCode.id == code_id, LoginCode.consumed_at.is_(None))
        .values(consumed_at=utcnow())
    )


async def expire_codes_for_phone(session: AsyncSession, phone: str) -> None:
    """A new code supersedes the old ones, so only one is ever valid."""
    await session.execute(
        update(LoginCode)
        .where(LoginCode.phone == phone, LoginCode.consumed_at.is_(None))
        .values(consumed_at=utcnow())
    )


async def codes_sent_since(
    session: AsyncSession, phone: str, since: datetime
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(LoginCode)
        .where(LoginCode.phone == phone, LoginCode.created_at >= since)
    )
    return int(result.scalar_one())


# ----------------------------------------------------------------------- signups


async def create_signup(
    session: AsyncSession,
    *,
    business_name: str,
    phone: str,
    contact_name: str | None = None,
    email: str | None = None,
    city: str | None = None,
    trade: str | None = None,
    message: str | None = None,
    source: str = "landing",
) -> Signup:
    row = Signup(
        business_name=business_name,
        phone=phone,
        contact_name=contact_name,
        email=email,
        city=city,
        trade=trade,
        message=message,
        source=source,
    )
    session.add(row)
    await session.flush()
    return row


async def signups_with_phone_since(
    session: AsyncSession, phone: str, since: datetime
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Signup)
        .where(Signup.phone == phone, Signup.created_at >= since)
    )
    return int(result.scalar_one())


async def list_signups(
    session: AsyncSession, *, status: SignupStatus | None = None, limit: int = 200
) -> list[Signup]:
    query = select(Signup).order_by(Signup.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Signup.status == status)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_signup(session: AsyncSession, signup_id: uuid.UUID) -> Signup | None:
    result = await session.execute(select(Signup).where(Signup.id == signup_id))
    return result.scalar_one_or_none()


async def update_signup(
    session: AsyncSession,
    signup_id: uuid.UUID,
    *,
    status: SignupStatus | None = None,
    notes: str | None = None,
    client_id: uuid.UUID | None = None,
) -> None:
    values: dict = {}
    if status is not None:
        values["status"] = status
        if status is SignupStatus.contacted:
            values["contacted_at"] = utcnow()
    if notes is not None:
        values["notes"] = notes
    if client_id is not None:
        values["client_id"] = client_id
    if not values:
        return
    await session.execute(
        update(Signup).where(Signup.id == signup_id).values(**values)
    )


async def count_new_signups(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Signup)
        .where(Signup.status == SignupStatus.new)
    )
    return int(result.scalar_one())


# --------------------------------------------------------------------- audit log


async def audit(
    session: AsyncSession,
    *,
    actor: str,
    actor_kind: SessionKind,
    action: str,
    client_id: uuid.UUID | None = None,
    target: str | None = None,
    detail: dict | None = None,
) -> AuditLog:
    row = AuditLog(
        actor=actor[:200],
        actor_kind=actor_kind,
        action=action[:64],
        client_id=client_id,
        target=(target or "")[:200] or None,
        detail=detail or {},
    )
    session.add(row)
    await session.flush()
    return row


async def recent_audit(
    session: AsyncSession, *, client_id: uuid.UUID | None = None, limit: int = 50
) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
    if client_id is not None:
        query = query.where(AuditLog.client_id == client_id)
    result = await session.execute(query)
    return list(result.scalars().all())


# ------------------------------------------------------- cross-tenant console reads


async def list_clients(session: AsyncSession) -> list[Client]:
    result = await session.execute(
        select(Client)
        .options(selectinload(Client.config))
        .order_by(Client.created_at.desc())
    )
    return list(result.scalars().all())


async def get_client(session: AsyncSession, client_id: uuid.UUID) -> Client | None:
    result = await session.execute(
        select(Client).options(selectinload(Client.config)).where(
            Client.id == client_id
        )
    )
    return result.scalar_one_or_none()


async def client_by_owner_phone(
    session: AsyncSession, phone: str
) -> Client | None:
    """Owner login: which shop does this number own?

    Only a shop that is actually running can be signed into - a lead, an
    onboarding shop or a churned one has no dashboard.

    One number owning two shops is possible and we cannot guess which one they
    meant, so the oldest wins and it is logged. A real shop-picker belongs with
    whatever multi-shop billing looks like, not here.
    """
    result = await session.execute(
        select(Client)
        .where(
            Client.owner_phone == phone,
            Client.status.in_(
                (ClientStatus.shadow, ClientStatus.live, ClientStatus.paused)
            ),
        )
        .order_by(Client.created_at)
    )
    matches = list(result.scalars().all())
    if len(matches) > 1:
        logging.getLogger(__name__).warning(
            "owner number owns %s shops; signing in to the oldest", len(matches)
        )
    return matches[0] if matches else None


async def message_counts_since(
    session: AsyncSession, since: datetime
) -> dict[uuid.UUID, dict[str, int]]:
    """{client_id: {"in": n, "out": n}} - one query for the whole shop list."""
    result = await session.execute(
        select(Message.client_id, Message.direction, func.count())
        .where(Message.created_at >= since)
        .group_by(Message.client_id, Message.direction)
    )
    out: dict[uuid.UUID, dict[str, int]] = {}
    for client_id, direction, count in result.all():
        bucket = out.setdefault(client_id, {"in": 0, "out": 0})
        bucket["in" if direction.value == "inbound" else "out"] = int(count)
    return out


async def cost_since(
    session: AsyncSession, since: datetime
) -> dict[uuid.UUID, float]:
    result = await session.execute(
        select(UsageEvent.client_id, func.sum(cast(UsageEvent.cost, Numeric(14, 6))))
        .where(UsageEvent.at >= since)
        .group_by(UsageEvent.client_id)
    )
    return {client_id: float(total or 0) for client_id, total in result.all()}


async def attention_counts(
    session: AsyncSession,
) -> dict[uuid.UUID, dict[str, int]]:
    """Per shop: conversations waiting on a human, and drafts waiting on an OK."""
    out: dict[uuid.UUID, dict[str, int]] = {}

    handoffs = await session.execute(
        select(Conversation.client_id, func.count())
        .where(Conversation.status == ConversationStatus.human)
        .group_by(Conversation.client_id)
    )
    for client_id, count in handoffs.all():
        out.setdefault(client_id, {"handoffs": 0, "drafts": 0})["handoffs"] = int(
            count
        )

    drafts = await session.execute(
        select(Message.client_id, func.count())
        .where(Message.status == MessageStatus.draft)
        .group_by(Message.client_id)
    )
    for client_id, count in drafts.all():
        out.setdefault(client_id, {"handoffs": 0, "drafts": 0})["drafts"] = int(count)

    return out


async def usage_by_model(session: AsyncSession, *, since: datetime) -> list[dict]:
    """Platform-wide Claude spend, split by model. The margin number."""
    result = await session.execute(
        select(
            UsageEvent.model,
            func.count().label("calls"),
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(UsageEvent.cache_read_input_tokens),
            func.sum(UsageEvent.cache_creation_input_tokens),
            func.sum(cast(UsageEvent.cost, Numeric(14, 6))),
        )
        .where(UsageEvent.at >= since, UsageEvent.type == "claude")
        .group_by(UsageEvent.model)
        .order_by(func.sum(cast(UsageEvent.cost, Numeric(14, 6))).desc())
    )
    rows = []
    for model, calls, tin, tout, cache_read, cache_write, cost in result.all():
        rows.append(
            {
                "model": model or "unknown",
                "calls": int(calls),
                "input_tokens": int(tin or 0),
                "output_tokens": int(tout or 0),
                "cache_read": int(cache_read or 0),
                "cache_write": int(cache_write or 0),
                "cost": float(cost or 0),
            }
        )
    return rows


async def platform_totals(session: AsyncSession, *, since: datetime) -> dict:
    shops = await session.execute(
        select(Client.status, func.count()).group_by(Client.status)
    )
    by_status = {status.value: int(count) for status, count in shops.all()}

    messages = await session.execute(
        select(func.count()).select_from(Message).where(Message.created_at >= since)
    )
    cost = await session.execute(
        select(func.sum(cast(UsageEvent.cost, Numeric(14, 6)))).where(
            UsageEvent.at >= since
        )
    )
    return {
        "shops_by_status": by_status,
        "shops_total": sum(by_status.values()),
        "messages": int(messages.scalar_one() or 0),
        "cost": float(cost.scalar_one() or 0),
    }


# -------------------------------------------------------------- creating a tenant


async def create_client_with_config(
    session: AsyncSession,
    *,
    name: str,
    timezone: str,
    owner_phone: str,
    languages: list[str],
    plan: str = "pilot",
    status: ClientStatus = ClientStatus.shadow,
    config: dict,
) -> Client:
    """Provision a shop: one `clients` row and one `client_config` row.

    Not client-scoped for the obvious reason - this is the call that *creates*
    the tenant, the mirror image of `repo.get_endpoint`. New shops start in
    `shadow` so the bot drafts and the owner approves before anything reaches a
    customer, which is the onboarding step the architecture doc specifies.
    """
    from app.models import ClientConfig

    client = Client(
        name=name,
        timezone=timezone,
        owner_phone=owner_phone,
        languages=languages,
        plan=plan,
        status=status,
    )
    session.add(client)
    await session.flush()
    session.add(ClientConfig(client_id=client.id, **config))
    await session.flush()
    return client


async def live_client_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Every business the scheduled sweeps should consider.

    Cross-tenant by nature - a scheduler has to know who exists. Called only
    from `app/queue/tasks.py`, which then does all per-business work through
    `client_id`-scoped helpers.
    """
    result = await session.execute(
        select(Client.id)
        .where(Client.status == ClientStatus.live)
        .order_by(Client.created_at)
    )
    return list(result.scalars().all())
