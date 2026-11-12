"""Read models for one shop, for the console's shop pages and the owner dashboard.

Same rule as `app/repo.py`: **every** helper takes `client_id` as its first
argument and filters on it, and `tests/test_tenant_isolation.py` enforces that
structurally across both modules. These are read-only presentation queries - the
message loop does not use any of them.

Kept separate from `app/repo.py` only to stop that module growing without bound;
the isolation guarantee is identical.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    Call,
    Conversation,
    ConversationStatus,
    Customer,
    Direction,
    Job,
    JobStatus,
    Message,
    MessageStatus,
    UsageEvent,
)


@dataclass(slots=True)
class ConversationRow:
    """A conversation plus the bits of the customer the page displays."""

    conversation: Conversation
    customer: Customer
    last_body: str | None
    message_count: int


@dataclass(slots=True)
class DraftRow:
    message: Message
    conversation: Conversation
    customer: Customer


# ------------------------------------------------------------------ conversations


async def conversation_page(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    status: ConversationStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationRow]:
    last_body = (
        select(Message.body)
        .where(Message.conversation_id == Conversation.id)
        .order_by(Message.seq.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    counted = (
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )
    query = (
        select(Conversation, Customer, last_body, counted)
        .join(Customer, Customer.id == Conversation.customer_id)
        .where(Conversation.client_id == client_id, Customer.client_id == client_id)
        .order_by(
            Conversation.last_message_at.desc().nullslast(),
            Conversation.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        query = query.where(Conversation.status == status)
    result = await session.execute(query)
    return [
        ConversationRow(
            conversation=conversation,
            customer=customer,
            last_body=body,
            message_count=int(count or 0),
        )
        for conversation, customer, body, count in result.all()
    ]


async def conversation_with_customer(
    session: AsyncSession, client_id: uuid.UUID, conversation_id: uuid.UUID
) -> tuple[Conversation, Customer] | None:
    result = await session.execute(
        select(Conversation, Customer)
        .join(Customer, Customer.id == Conversation.customer_id)
        .where(
            Conversation.client_id == client_id,
            Conversation.id == conversation_id,
            Customer.client_id == client_id,
        )
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def full_transcript(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    limit: int = 300,
) -> list[Message]:
    """Everything, oldest first - including rows the customer never saw.

    Unlike `repo.recent_messages`, this deliberately keeps `blocked`, `draft`
    and `rejected` messages: when the owner is looking at a transcript, "this
    reply never reached them" is the single most useful thing on the page.
    """
    result = await session.execute(
        select(Message)
        .where(
            Message.client_id == client_id,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    return list(reversed(list(result.scalars().all())))


async def open_handoffs(
    session: AsyncSession, client_id: uuid.UUID, *, limit: int = 50
) -> list[ConversationRow]:
    return await conversation_page(
        session, client_id, status=ConversationStatus.human, limit=limit
    )


async def pending_drafts(
    session: AsyncSession, client_id: uuid.UUID, *, limit: int = 50
) -> list[DraftRow]:
    result = await session.execute(
        select(Message, Conversation, Customer)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(Customer, Customer.id == Conversation.customer_id)
        .where(
            Message.client_id == client_id,
            Message.status == MessageStatus.draft,
            Conversation.client_id == client_id,
        )
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    return [
        DraftRow(message=message, conversation=conversation, customer=customer)
        for message, conversation, customer in result.all()
    ]


# -------------------------------------------------------- appointments, jobs, calls


async def appointments_from(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    since: datetime | None = None,
    limit: int = 100,
) -> list[tuple[Appointment, Customer | None]]:
    query = (
        select(Appointment, Customer)
        .outerjoin(
            Customer,
            (Customer.id == Appointment.customer_id)
            & (Customer.client_id == client_id),
        )
        .where(Appointment.client_id == client_id)
        .order_by(Appointment.start_at.asc().nullslast())
        .limit(limit)
    )
    if since is not None:
        query = query.where(Appointment.start_at >= since)
    result = await session.execute(query)
    return [(appointment, customer) for appointment, customer in result.all()]


async def jobs_overview(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    status: JobStatus | None = None,
    limit: int = 100,
) -> list[tuple[Job, Customer | None]]:
    query = (
        select(Job, Customer)
        .outerjoin(
            Customer,
            (Customer.id == Job.customer_id) & (Customer.client_id == client_id),
        )
        .where(Job.client_id == client_id)
        .order_by(Job.updated_at.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(Job.status == status)
    result = await session.execute(query)
    return [(job, customer) for job, customer in result.all()]


async def recent_calls(
    session: AsyncSession, client_id: uuid.UUID, *, limit: int = 50
) -> list[tuple[Call, Customer | None]]:
    result = await session.execute(
        select(Call, Customer)
        .outerjoin(
            Customer,
            (Customer.id == Call.customer_id) & (Customer.client_id == client_id),
        )
        .where(Call.client_id == client_id)
        .order_by(Call.created_at.desc())
        .limit(limit)
    )
    return [(call, customer) for call, customer in result.all()]


# -------------------------------------------------------------------- aggregates


async def counts_since(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    since: datetime,
    until: datetime | None = None,
) -> dict:
    """Headline numbers for one business over a window.

    `until` is exclusive and optional: the console wants "the last 30 days", the
    daily summary wants "yesterday, and not a minute of today".
    """
    def window(column):
        bounds = [column >= since]
        if until is not None:
            bounds.append(column < until)
        return bounds

    messages = await session.execute(
        select(Message.direction, func.count())
        .where(Message.client_id == client_id, *window(Message.created_at))
        .group_by(Message.direction)
    )
    inbound = outbound = 0
    for direction, count in messages.all():
        if direction is Direction.inbound:
            inbound = int(count)
        else:
            outbound = int(count)

    conversations = await session.execute(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.client_id == client_id, *window(Conversation.created_at))
    )
    appointments = await session.execute(
        select(func.count())
        .select_from(Appointment)
        .where(Appointment.client_id == client_id, *window(Appointment.created_at))
    )
    missed = await session.execute(
        select(func.count())
        .select_from(Call)
        .where(Call.client_id == client_id, *window(Call.created_at))
    )
    texted_back = await session.execute(
        select(func.count())
        .select_from(Call)
        .where(
            Call.client_id == client_id,
            *window(Call.created_at),
            Call.texted_back_at.is_not(None),
        )
    )
    cost = await session.execute(
        select(func.sum(cast(UsageEvent.cost, Numeric(14, 6)))).where(
            UsageEvent.client_id == client_id, *window(UsageEvent.at)
        )
    )
    blocked = await session.execute(
        select(func.count())
        .select_from(Message)
        .where(
            Message.client_id == client_id,
            *window(Message.created_at),
            Message.status == MessageStatus.blocked,
        )
    )
    return {
        "inbound": inbound,
        "outbound": outbound,
        "conversations": int(conversations.scalar_one() or 0),
        "appointments": int(appointments.scalar_one() or 0),
        "calls": int(missed.scalar_one() or 0),
        "texted_back": int(texted_back.scalar_one() or 0),
        "blocked": int(blocked.scalar_one() or 0),
        "cost": float(cost.scalar_one() or 0),
    }


async def languages_seen(
    session: AsyncSession, client_id: uuid.UUID
) -> list[tuple[str, int]]:
    """Which languages this shop's customers actually write in."""
    result = await session.execute(
        select(Customer.language, func.count())
        .where(Customer.client_id == client_id, Customer.language.is_not(None))
        .group_by(Customer.language)
        .order_by(func.count().desc())
    )
    return [(language, int(count)) for language, count in result.all()]


async def usage_by_day(
    session: AsyncSession, client_id: uuid.UUID, *, since: datetime
) -> list[tuple[str, int, float]]:
    """(day, claude calls, cost) oldest first - the console's spend sparkline."""
    day = func.date_trunc("day", UsageEvent.at)
    result = await session.execute(
        select(day, func.count(), func.sum(cast(UsageEvent.cost, Numeric(14, 6))))
        .where(
            UsageEvent.client_id == client_id,
            UsageEvent.at >= since,
            UsageEvent.type == "claude",
        )
        .group_by(day)
        .order_by(day)
    )
    return [
        (stamp.strftime("%Y-%m-%d"), int(calls), float(cost or 0))
        for stamp, calls, cost in result.all()
    ]
