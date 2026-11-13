"""Data access.

Tenant isolation rule: every function that reads or writes tenant-owned rows takes
`client_id` as its first argument and filters on it, even when the row id alone
would be unique. `tests/test_tenant_isolation.py` enforces this by asking for
shop A's rows with shop B's client_id and asserting it gets nothing back.

The one deliberate exception is `get_endpoint`, which is the lookup that
*establishes* the tenant from an inbound number.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Appointment,
    Call,
    CallOutcome,
    Channel,
    ChannelEndpoint,
    Client,
    ClientConfig,
    ClientStatus,
    Conversation,
    ConversationStatus,
    Customer,
    Direction,
    Job,
    JobStatus,
    LanguageSource,
    Lead,
    Message,
    MessageStatus,
    UsageEvent,
)

WHATSAPP_WINDOW = timedelta(hours=24)


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ tenant routing


async def get_endpoint(
    session: AsyncSession, channel: Channel, external_id: str
) -> ChannelEndpoint | None:
    """Resolve an inbound number / phone_number_id to its shop.

    Not client-scoped by design: this is the call that determines the tenant.
    """
    result = await session.execute(
        select(ChannelEndpoint).where(
            ChannelEndpoint.channel == channel,
            ChannelEndpoint.external_id == external_id,
            ChannelEndpoint.active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def get_client_endpoint(
    session: AsyncSession, client_id: uuid.UUID, channel: Channel
) -> ChannelEndpoint | None:
    """The endpoint this shop sends *from* on a given channel."""
    result = await session.execute(
        select(ChannelEndpoint)
        .where(
            ChannelEndpoint.client_id == client_id,
            ChannelEndpoint.channel == channel,
            ChannelEndpoint.active.is_(True),
        )
        .order_by(ChannelEndpoint.external_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_client_with_config(
    session: AsyncSession, client_id: uuid.UUID
) -> Client | None:
    result = await session.execute(
        select(Client)
        .options(selectinload(Client.config))
        .where(Client.id == client_id)
    )
    return result.scalar_one_or_none()


# ----------------------------------------------------------------------- customers


async def find_or_create_customer(
    session: AsyncSession, client_id: uuid.UUID, phone: str
) -> Customer:
    result = await session.execute(
        select(Customer).where(
            Customer.client_id == client_id, Customer.phone == phone
        )
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        customer = Customer(client_id=client_id, phone=phone)
        session.add(customer)
        await session.flush()
    return customer


async def get_customer(
    session: AsyncSession, client_id: uuid.UUID, customer_id: uuid.UUID
) -> Customer | None:
    result = await session.execute(
        select(Customer).where(
            Customer.client_id == client_id, Customer.id == customer_id
        )
    )
    return result.scalar_one_or_none()


async def update_customer_language(
    session: AsyncSession,
    client_id: uuid.UUID,
    customer_id: uuid.UUID,
    language: str,
    confidence: float,
    source: LanguageSource = LanguageSource.detected,
) -> None:
    """Only ever called on a confident detection - see agent/language.py."""
    await session.execute(
        update(Customer)
        .where(Customer.client_id == client_id, Customer.id == customer_id)
        .values(
            language=language, language_confidence=confidence, language_source=source
        )
    )


async def set_customer_opted_out(
    session: AsyncSession, client_id: uuid.UUID, customer_id: uuid.UUID, opted: bool
) -> None:
    await session.execute(
        update(Customer)
        .where(Customer.client_id == client_id, Customer.id == customer_id)
        .values(opted_out_at=utcnow() if opted else None)
    )


# ------------------------------------------------------------------- conversations


async def find_or_create_conversation(
    session: AsyncSession,
    client_id: uuid.UUID,
    customer_id: uuid.UUID,
    channel: Channel,
) -> Conversation:
    """One open conversation per customer per channel."""
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.client_id == client_id,
            Conversation.customer_id == customer_id,
            Conversation.channel == channel,
            Conversation.status != ConversationStatus.closed,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        conversation = Conversation(
            client_id=client_id,
            customer_id=customer_id,
            channel=channel,
            short_code=await _unique_short_code(session, client_id),
        )
        session.add(conversation)
        await session.flush()
    return conversation


#: Excludes vowels (so a code can never spell a word) and look-alike characters.
_CODE_LETTERS = "BCDFGHJKLMNPQRSTVWXZ"
_CODE_DIGITS = "23456789"


def _mint_code(length: int) -> str:
    """Always mixes letters and digits.

    The mix is load-bearing: the command parser only treats a leading token as a
    code if it has both, which is what stops "2014 Civic is done" from being
    read as code 2014 plus the message "Civic is done".
    """
    import secrets

    chars = [secrets.choice(_CODE_LETTERS), secrets.choice(_CODE_DIGITS)]
    pool = _CODE_LETTERS + _CODE_DIGITS
    chars += [secrets.choice(pool) for _ in range(length - 2)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


async def _unique_short_code(
    session: AsyncSession, client_id: uuid.UUID, length: int = 4
) -> str:
    """A short handle the owner can type, unique within the shop."""
    for _ in range(12):
        code = _mint_code(length)
        taken = await session.execute(
            select(Conversation.id).where(
                Conversation.client_id == client_id,
                Conversation.short_code == code,
            )
        )
        if taken.scalar_one_or_none() is None:
            return code
    # Astronomically unlikely; a longer code is better than a collision.
    return await _unique_short_code(session, client_id, length + 1)


async def get_conversation(
    session: AsyncSession, client_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(
            Conversation.client_id == client_id, Conversation.id == conversation_id
        )
    )
    return result.scalar_one_or_none()


async def touch_window(
    session: AsyncSession, client_id: uuid.UUID, conversation_id: uuid.UUID
) -> None:
    """A customer message reopens the 24-hour free-form window."""
    now = utcnow()
    await session.execute(
        update(Conversation)
        .where(
            Conversation.client_id == client_id, Conversation.id == conversation_id
        )
        .values(window_expires_at=now + WHATSAPP_WINDOW, last_message_at=now)
    )


async def hand_off(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    reason: str,
) -> None:
    await session.execute(
        update(Conversation)
        .where(
            Conversation.client_id == client_id, Conversation.id == conversation_id
        )
        .values(
            status=ConversationStatus.human,
            handoff_reason=reason[:200],
            handed_off_at=utcnow(),
        )
    )


async def set_counters(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    misunderstand_count: int | None = None,
    ai_failure_count: int | None = None,
) -> None:
    values: dict = {}
    if misunderstand_count is not None:
        values["misunderstand_count"] = misunderstand_count
    if ai_failure_count is not None:
        values["ai_failure_count"] = ai_failure_count
    if not values:
        return
    await session.execute(
        update(Conversation)
        .where(
            Conversation.client_id == client_id, Conversation.id == conversation_id
        )
        .values(**values)
    )


# ----------------------------------------------------------------------- messages


async def message_exists(session: AsyncSession, provider_msg_id: str) -> bool:
    """Providers resend webhooks - dedupe on the provider's own id."""
    result = await session.execute(
        select(Message.id).where(Message.provider_msg_id == provider_msg_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def add_message(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    direction: Direction,
    channel: Channel,
    body: str | None = None,
    status: MessageStatus = MessageStatus.received,
    media_url: str | None = None,
    transcript: str | None = None,
    provider_msg_id: str | None = None,
    detected_language: str | None = None,
    error: str | None = None,
) -> Message:
    message = Message(
        client_id=client_id,
        conversation_id=conversation_id,
        direction=direction,
        channel=channel,
        body=body,
        status=status,
        media_url=media_url,
        transcript=transcript,
        provider_msg_id=provider_msg_id,
        detected_language=detected_language,
        error=error,
    )
    session.add(message)
    await session.flush()
    return message


async def get_message(
    session: AsyncSession, client_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    result = await session.execute(
        select(Message).where(
            Message.client_id == client_id, Message.id == message_id
        )
    )
    return result.scalar_one_or_none()


async def recent_messages(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    limit: int = 20,
) -> list[Message]:
    """Oldest-first window of the conversation, for the prompt.

    Excludes anything the customer never actually saw: a blocked send, and a
    shadow-mode draft or a draft the owner replaced. Replaying those would have
    the model believe it already said something it did not.
    """
    unseen = (
        MessageStatus.blocked,
        MessageStatus.draft,
        MessageStatus.rejected,
    )
    result = await session.execute(
        select(Message)
        .where(
            Message.client_id == client_id,
            Message.conversation_id == conversation_id,
            Message.status.not_in(unseen),
        )
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def outbound_since(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    after_seq: int | None = None,
    limit: int = 50,
) -> list[Message]:
    """Outbound messages after `after_seq`, oldest first. Used by /sim/poll.

    Keyed on `seq`, not `created_at`: a timestamp cursor would skip or repeat
    messages that share a transaction timestamp.
    """
    stmt = select(Message).where(
        Message.client_id == client_id,
        Message.conversation_id == conversation_id,
        Message.direction == Direction.outbound,
    )
    if after_seq is not None:
        stmt = stmt.where(Message.seq > after_seq)
    stmt = stmt.order_by(Message.seq.asc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def outbound_for_customer_since(
    session: AsyncSession,
    client_id: uuid.UUID,
    customer_id: uuid.UUID,
    after_seq: int | None = None,
    limit: int = 50,
) -> list[Message]:
    """Everything sent to one customer, across every channel.

    The /sim view uses this rather than a single conversation: a missed-call
    text-back lands on SMS while the chat the tester started is on the simulator
    channel, and a debugging view should show both.
    """
    stmt = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Message.client_id == client_id,
            Conversation.customer_id == customer_id,
            Message.direction == Direction.outbound,
            Message.status != MessageStatus.draft,
        )
    )
    if after_seq is not None:
        stmt = stmt.where(Message.seq > after_seq)
    stmt = stmt.order_by(Message.seq.asc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def find_customer_by_phone(
    session: AsyncSession, client_id: uuid.UUID, phone: str
) -> Customer | None:
    result = await session.execute(
        select(Customer).where(
            Customer.client_id == client_id, Customer.phone == phone
        )
    )
    return result.scalar_one_or_none()


async def latest_conversation(
    session: AsyncSession,
    client_id: uuid.UUID,
    customer_id: uuid.UUID,
    channel: Channel,
) -> Conversation | None:
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.client_id == client_id,
            Conversation.customer_id == customer_id,
            Conversation.channel == channel,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def mark_message_status(
    session: AsyncSession,
    client_id: uuid.UUID,
    message_id: uuid.UUID,
    status: MessageStatus,
    *,
    provider_msg_id: str | None = None,
    error: str | None = None,
) -> None:
    values: dict = {"status": status}
    if provider_msg_id is not None:
        values["provider_msg_id"] = provider_msg_id
    if error is not None:
        values["error"] = error
    await session.execute(
        update(Message)
        .where(Message.client_id == client_id, Message.id == message_id)
        .values(**values)
    )


# -------------------------------------------------------------------- leads, usage


async def create_lead(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    customer_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    item: str | None,
    issue: str | None,
    contact: str | None,
) -> Lead:
    lead = Lead(
        client_id=client_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
        item=item,
        issue=issue,
        contact=contact,
    )
    session.add(lead)
    await session.flush()
    return lead


async def latest_job_for_item(
    session: AsyncSession,
    client_id: uuid.UUID,
    customer_id: uuid.UUID,
    item: str | None,
) -> Job | None:
    """The customer's job, matched loosely on item text if one is given."""
    stmt = select(Job).where(
        Job.client_id == client_id, Job.customer_id == customer_id
    )
    if item:
        stmt = stmt.where(Job.item.ilike(f"%{item.strip()}%"))
    stmt = stmt.order_by(Job.updated_at.desc()).limit(1)
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None and item:
        # Fall back to their most recent job regardless of how they named the car.
        return await latest_job_for_item(session, client_id, customer_id, None)
    return job


async def upsert_job(
    session: AsyncSession,
    client_id: uuid.UUID,
    customer_id: uuid.UUID,
    item: str,
    status: JobStatus,
    updated_by: str,
) -> Job:
    """Set a job's status, creating it if the owner names a new item."""
    result = await session.execute(
        select(Job).where(
            Job.client_id == client_id,
            Job.customer_id == customer_id,
            Job.item.ilike(item.strip()),
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        job = Job(
            client_id=client_id,
            customer_id=customer_id,
            item=item.strip(),
            status=status,
            updated_by=updated_by,
        )
        session.add(job)
    else:
        job.status = status
        job.updated_by = updated_by
    await session.flush()
    return job


async def find_job_by_item(
    session: AsyncSession, client_id: uuid.UUID, item: str
) -> Job | None:
    """Any customer's job matching this item - used by the owner's READY."""
    result = await session.execute(
        select(Job)
        .where(Job.client_id == client_id, Job.item.ilike(f"%{item.strip()}%"))
        .order_by(Job.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# --------------------------------------------------------- owner-side lookups


async def find_or_create_owner(
    session: AsyncSession, client_id: uuid.UUID, phone: str
) -> Customer:
    """The owner as a contact on their own business number."""
    customer = await find_or_create_customer(session, client_id, phone)
    if not customer.is_owner:
        customer.is_owner = True
        customer.name = customer.name or "Owner"
        await session.flush()
    return customer


async def conversation_by_short_code(
    session: AsyncSession, client_id: uuid.UUID, code: str
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(
            Conversation.client_id == client_id,
            Conversation.short_code == code.strip().upper(),
        )
    )
    return result.scalar_one_or_none()


async def latest_handoff_conversation(
    session: AsyncSession, client_id: uuid.UUID
) -> Conversation | None:
    """The conversation the owner most recently had handed to them."""
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.client_id == client_id,
            Conversation.status == ConversationStatus.human,
            Conversation.handed_off_at.is_not(None),
        )
        .order_by(Conversation.handed_off_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def set_conversation_status(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    status: ConversationStatus,
) -> None:
    await session.execute(
        update(Conversation)
        .where(
            Conversation.client_id == client_id, Conversation.id == conversation_id
        )
        .values(status=status)
    )


async def pending_draft(
    session: AsyncSession, client_id: uuid.UUID, conversation_id: uuid.UUID
) -> Message | None:
    """The newest draft awaiting owner approval in shadow mode."""
    result = await session.execute(
        select(Message)
        .where(
            Message.client_id == client_id,
            Message.conversation_id == conversation_id,
            Message.status == MessageStatus.draft,
        )
        .order_by(Message.seq.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_pending_draft(
    session: AsyncSession, client_id: uuid.UUID
) -> Message | None:
    result = await session.execute(
        select(Message)
        .where(
            Message.client_id == client_id,
            Message.status == MessageStatus.draft,
        )
        .order_by(Message.seq.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_unconfirmed_appointment(
    session: AsyncSession, client_id: uuid.UUID
) -> Appointment | None:
    result = await session.execute(
        select(Appointment)
        .where(
            Appointment.client_id == client_id,
            Appointment.owner_confirmed_at.is_(None),
            Appointment.status == "booked",
        )
        .order_by(Appointment.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def confirm_appointment(
    session: AsyncSession, client_id: uuid.UUID, appointment_id: uuid.UUID
) -> None:
    await session.execute(
        update(Appointment)
        .where(
            Appointment.client_id == client_id, Appointment.id == appointment_id
        )
        .values(owner_confirmed_at=utcnow())
    )


# --------------------------------------------------------------------- calls


async def call_exists(
    session: AsyncSession, client_id: uuid.UUID, provider_call_id: str
) -> Call | None:
    result = await session.execute(
        select(Call).where(
            Call.client_id == client_id, Call.provider_call_id == provider_call_id
        )
    )
    return result.scalar_one_or_none()


async def get_call(
    session: AsyncSession, client_id: uuid.UUID, call_id: uuid.UUID
) -> Call | None:
    result = await session.execute(
        select(Call).where(Call.client_id == client_id, Call.id == call_id)
    )
    return result.scalar_one_or_none()


async def log_call(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    customer_id: uuid.UUID | None,
    from_number: str,
    provider_call_id: str | None,
    outcome: CallOutcome = CallOutcome.missed,
    duration: int | None = None,
) -> Call:
    call = Call(
        client_id=client_id,
        customer_id=customer_id,
        from_number=from_number,
        provider_call_id=provider_call_id,
        outcome=outcome,
        duration=duration,
    )
    session.add(call)
    await session.flush()
    return call


async def finish_call(
    session: AsyncSession,
    client_id: uuid.UUID,
    provider_call_id: str,
    *,
    outcome: CallOutcome | None = None,
    duration: int | None = None,
    recording_url: str | None = None,
    summary: str | None = None,
) -> None:
    """Fill in what only the end of a call knows.

    Keyed on the provider's call id rather than ours because the caller is a
    webhook that has never seen our primary key. Every field is optional: a
    provider that omits the recording must not blank the duration.
    """
    values: dict = {}
    if outcome is not None:
        values["outcome"] = outcome
    if duration is not None:
        values["duration"] = duration
    if recording_url is not None:
        values["recording_url"] = recording_url
    if summary is not None:
        values["summary"] = summary[:2000]
    if not values:
        return
    await session.execute(
        update(Call)
        .where(Call.client_id == client_id, Call.provider_call_id == provider_call_id)
        .values(**values)
    )


async def mark_texted_back(
    session: AsyncSession, client_id: uuid.UUID, call_id: uuid.UUID
) -> None:
    await session.execute(
        update(Call)
        .where(Call.client_id == client_id, Call.id == call_id)
        .values(texted_back_at=utcnow())
    )


async def record_usage(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    type: str,
    conversation_id: uuid.UUID | None = None,
    model: str | None = None,
    units: int = 1,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cost: float = 0.0,
) -> UsageEvent:
    event = UsageEvent(
        client_id=client_id,
        conversation_id=conversation_id,
        type=type,
        model=model,
        units=units,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cost=cost,
    )
    session.add(event)
    await session.flush()
    return event


async def set_prompt_version(
    session: AsyncSession, client_id: uuid.UUID, prompt_version: str
) -> None:
    await session.execute(
        update(ClientConfig)
        .where(ClientConfig.client_id == client_id)
        .values(prompt_version=prompt_version)
    )


# ------------------------------------------------- phase 3: console-driven writes
#
# Everything the admin console changes about a shop. Same rule as the rest of
# this module: `client_id` first, and it is always in the WHERE clause, so a
# console bug cannot edit the wrong shop's configuration.


async def update_client(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    name: str | None = None,
    timezone: str | None = None,
    owner_phone: str | None = None,
    languages: list[str] | None = None,
    plan: str | None = None,
) -> None:
    values: dict = {}
    if name is not None:
        values["name"] = name
    if timezone is not None:
        values["timezone"] = timezone
    if owner_phone is not None:
        values["owner_phone"] = owner_phone
    if languages is not None:
        values["languages"] = languages
    if plan is not None:
        values["plan"] = plan
    if not values:
        return
    await session.execute(
        update(Client).where(Client.id == client_id).values(**values)
    )


async def set_client_status(
    session: AsyncSession, client_id: uuid.UUID, status: ClientStatus
) -> None:
    await session.execute(
        update(Client).where(Client.id == client_id).values(status=status)
    )


async def update_config(
    session: AsyncSession, client_id: uuid.UUID, **fields
) -> None:
    """Write whole config fields. `updated_at` moves so the console can show it."""
    if not fields:
        return
    fields["updated_at"] = utcnow()
    await session.execute(
        update(ClientConfig)
        .where(ClientConfig.client_id == client_id)
        .values(**fields)
    )


async def list_endpoints(
    session: AsyncSession, client_id: uuid.UUID
) -> list[ChannelEndpoint]:
    result = await session.execute(
        select(ChannelEndpoint)
        .where(ChannelEndpoint.client_id == client_id)
        .order_by(ChannelEndpoint.channel, ChannelEndpoint.external_id)
    )
    return list(result.scalars().all())


async def add_endpoint(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    channel: Channel,
    external_id: str,
    number: str | None = None,
    provider: str = "twilio",
) -> ChannelEndpoint:
    row = ChannelEndpoint(
        client_id=client_id,
        channel=channel,
        external_id=external_id,
        number=number,
        provider=provider,
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def set_endpoint_active(
    session: AsyncSession,
    client_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    active: bool,
) -> None:
    await session.execute(
        update(ChannelEndpoint)
        .where(
            ChannelEndpoint.client_id == client_id,
            ChannelEndpoint.id == endpoint_id,
        )
        .values(active=active)
    )


# --------------------------------------------------- v1.5: reminders and summaries


async def appointments_due_reminder(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    now: datetime,
    until: datetime,
) -> list[tuple[Appointment, Customer]]:
    """Appointments starting inside the window that have not been reminded.

    Only `booked` ones: an appointment the owner flagged with CHANGE, or one
    that was cancelled, must not get a reminder telling the customer to turn up.
    """
    result = await session.execute(
        select(Appointment, Customer)
        .join(Customer, Customer.id == Appointment.customer_id)
        .where(
            Appointment.client_id == client_id,
            Customer.client_id == client_id,
            Appointment.reminder_sent_at.is_(None),
            Appointment.status == "booked",
            Appointment.start_at.is_not(None),
            Appointment.start_at > now,
            Appointment.start_at <= until,
        )
        .order_by(Appointment.start_at)
    )
    return [(appointment, customer) for appointment, customer in result.all()]


async def mark_reminder_sent(
    session: AsyncSession,
    client_id: uuid.UUID,
    appointment_id: uuid.UUID,
    at: datetime | None = None,
) -> None:
    """Marks the attempt, not the delivery.

    A reminder that could not be delivered - the WhatsApp window closed and no
    template is approved - is still marked, because the alternative is retrying
    every fifteen minutes and alerting the owner every time.
    """
    await session.execute(
        update(Appointment)
        .where(
            Appointment.client_id == client_id, Appointment.id == appointment_id
        )
        .values(reminder_sent_at=at or utcnow())
    )


async def appointments_between(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    start: datetime,
    end: datetime,
) -> list[tuple[Appointment, Customer | None]]:
    result = await session.execute(
        select(Appointment, Customer)
        .outerjoin(
            Customer,
            (Customer.id == Appointment.customer_id)
            & (Customer.client_id == client_id),
        )
        .where(
            Appointment.client_id == client_id,
            Appointment.status != "cancelled",
            Appointment.start_at >= start,
            Appointment.start_at < end,
        )
        .order_by(Appointment.start_at)
    )
    return [(appointment, customer) for appointment, customer in result.all()]


async def set_last_summary_on(
    session: AsyncSession, client_id: uuid.UUID, on: date
) -> None:
    await session.execute(
        update(ClientConfig)
        .where(ClientConfig.client_id == client_id)
        .values(last_summary_on=on)
    )


async def last_summary_on(
    session: AsyncSession, client_id: uuid.UUID
) -> date | None:
    """Read straight from the row, never from the tenant-router cache.

    The cache has a 60-second TTL. Reading this through it could let a second
    summary go out for a day already covered, which is exactly the mistake the
    column exists to prevent.
    """
    result = await session.execute(
        select(ClientConfig.last_summary_on).where(
            ClientConfig.client_id == client_id
        )
    )
    return result.scalar_one_or_none()
