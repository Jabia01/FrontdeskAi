"""SQLAlchemy models.

Two invariants from the architecture doc:
  1. Every tenant-owned table carries `client_id`, and every query filters on it.
  2. `Channel` already includes `voice`, so the v2 voice adapter needs no migration.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# --------------------------------------------------------------------------- enums


class Channel(str, enum.Enum):
    whatsapp = "whatsapp"
    sms = "sms"
    voice = "voice"          # v2 - present from day one, no migration needed
    simulator = "simulator"  # local dev channel


class Direction(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class ConversationStatus(str, enum.Enum):
    ai = "ai"
    human = "human"
    closed = "closed"


class MessageStatus(str, enum.Enum):
    received = "received"
    queued = "queued"
    sent = "sent"
    failed = "failed"
    blocked = "blocked"  # outside the 24h window with no approved template
    draft = "draft"      # shadow mode: written by the AI, awaiting owner approval
    rejected = "rejected"  # shadow mode: the owner replaced or dropped it


class LanguageSource(str, enum.Enum):
    detected = "detected"
    owner_set = "owner_set"
    inferred = "inferred"


class ClientStatus(str, enum.Enum):
    lead = "lead"
    onboarding = "onboarding"
    shadow = "shadow"
    live = "live"
    paused = "paused"
    churned = "churned"


class CallOutcome(str, enum.Enum):
    missed = "missed"
    answered_ai = "answered_ai"
    answered_human = "answered_human"


class JobStatus(str, enum.Enum):
    in_shop = "in"
    waiting_parts = "waiting_parts"
    ready = "ready"


def _enum(py_enum: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(py_enum, name=name, values_callable=lambda e: [m.value for m in e])


# -------------------------------------------------------------------------- tenants


class Client(Base):
    """One row per shop. Never a fork of the code."""

    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    owner_phone: Mapped[str | None] = mapped_column(String(32))
    plan: Mapped[str] = mapped_column(String(64), default="pilot")
    status: Mapped[ClientStatus] = mapped_column(
        _enum(ClientStatus, "client_status"), default=ClientStatus.onboarding
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    config: Mapped[ClientConfig | None] = relationship(
        back_populates="client", uselist=False
    )


class ClientConfig(Base):
    """Feeds the cached system prompt. Written once, in the owner's language."""

    __tablename__ = "client_config"

    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    business_info: Mapped[dict] = mapped_column(JSONB, default=dict)
    services: Mapped[list] = mapped_column(JSONB, default=list)
    hours: Mapped[dict] = mapped_column(JSONB, default=dict)
    price_ranges: Mapped[list] = mapped_column(JSONB, default=list)
    tone: Mapped[str] = mapped_column(Text, default="")
    handoff_rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    faqs: Mapped[list] = mapped_column(JSONB, default=list)

    # Per-channel style rules - keeps the agent channel-agnostic (voice-ready).
    channel_style: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Language handling
    owner_language: Mapped[str] = mapped_column(String(16), default="en")
    default_customer_language: Mapped[str] = mapped_column(String(16), default="en")
    enabled_languages: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    country_language_overrides: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Booking (Phase 2). The shop shares its calendar with our service account
    # during onboarding and we store the calendar id here - no per-shop OAuth.
    calendar_id: Mapped[str | None] = mapped_column(String(200))
    booking: Mapped[dict] = mapped_column(JSONB, default=dict)

    #: Channel used for missed-call text-back. SMS by default: a missed call has
    #: no open WhatsApp window, so WhatsApp would need an approved template.
    missed_call_channel: Mapped[str] = mapped_column(String(16), default="sms")

    #: Where owner alerts go: "auto" (WhatsApp while their 24h window is open,
    #: else SMS), or pinned to "whatsapp" / "sms".
    owner_channel: Mapped[str] = mapped_column(String(16), default="auto")

    # ---- what kind of business this is -------------------------------------
    # Config only. There is no per-vertical code path, and `other` is a
    # complete, working configuration.
    vertical: Mapped[str] = mapped_column(
        String(32), default="other", server_default=text("'other'"), nullable=False
    )
    #: The word this business uses for what a job is about: "vehicle", "pet",
    #: "order", "garment", "device". Rendered into the prompt and the dashboard,
    #: so one codebase reads naturally to a garage and to a dog groomer.
    job_noun: Mapped[str] = mapped_column(
        String(32), default="job", server_default=text("'job'"), nullable=False
    )
    #: The symbol prices are written in. A column rather than a key inside
    #: `business_info`, because it is rendered into the system prompt next to
    #: every price range - a shop quoting euros in dollars is wrong on every
    #: reply, not just on the pricing page.
    currency: Mapped[str] = mapped_column(
        String(8), default="$", server_default=text("'$'"), nullable=False
    )

    # ---- v1.5: reminders and the daily summary -----------------------------
    #: Hours before the appointment to send a reminder. 0 turns it off.
    reminder_hours_before: Mapped[int] = mapped_column(
        Integer, default=24, server_default=text("24"), nullable=False
    )
    summary_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    #: Hour of the business's own local day to send the summary.
    summary_hour: Mapped[int] = mapped_column(
        Integer, default=7, server_default=text("7"), nullable=False
    )
    #: Local date of the last summary sent - what makes that sweep idempotent.
    last_summary_on: Mapped[date | None] = mapped_column(Date)
    #: Approved WhatsApp template names by purpose, e.g.
    #: {"reminder": "appointment_reminder_v1"}. Without one, a send outside the
    #: 24-hour window is recorded `blocked` rather than delivered.
    templates: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    prompt_version: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client: Mapped[Client] = relationship(back_populates="config")


class ChannelEndpoint(Base):
    """Maps an incoming number / phone_number_id to a shop. The tenant router's index."""

    __tablename__ = "channel_endpoints"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_endpoint_channel_external"),
        Index("ix_endpoint_lookup", "channel", "external_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="twilio")
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    number: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ------------------------------------------------------------------- conversations


class Customer(Base):
    """Phone is the identity across channels."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("client_id", "phone", name="uq_customer_client_phone"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    #: Whatever this business works on: a vehicle, a pet, a device, a
    #: garment, an order number. `client_config.job_noun` is the word this
    #: particular business uses for it.
    item: Mapped[str | None] = mapped_column(String(200))

    language: Mapped[str | None] = mapped_column(String(16))
    language_confidence: Mapped[float | None] = mapped_column(Float)
    language_source: Mapped[LanguageSource | None] = mapped_column(
        _enum(LanguageSource, "language_source")
    )

    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The shop owner messaging their own business number. Their inbound messages
    #: are commands, not customer conversations.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conv_client_customer", "client_id", "customer_id"),
        UniqueConstraint("client_id", "short_code", name="uq_conv_client_short_code"),
        # Partial: only a handed-off conversation has a code, and those are a
        # small fraction of the table. Counting escalations by reason is the
        # one query a regulated niche runs often.
        Index(
            "ix_conv_handoff_code",
            "client_id",
            "handoff_code",
            postgresql_where=text("handoff_code IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"), nullable=False)
    status: Mapped[ConversationStatus] = mapped_column(
        _enum(ConversationStatus, "conversation_status"), default=ConversationStatus.ai
    )
    window_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Short handle the owner uses on WhatsApp, e.g. "A7Q2". Unique per shop, so
    #: the owner can address one conversation out of several: "A7Q2 ready tomorrow".
    short_code: Mapped[str | None] = mapped_column(String(8))

    # "Hand off after two failed attempts to understand"
    misunderstand_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_failure_count: Mapped[int] = mapped_column(Integer, default=0)

    #: What the model said, in its own words. Free text on purpose - it quotes
    #: the customer and is the thing an owner actually reads.
    handoff_reason: Mapped[str | None] = mapped_column(String(200))
    #: The same escalation, categorised. Free text cannot be counted, and a
    #: regulated niche has to be able to answer "how often did it hand off a
    #: clinical question this month". Values come from `app.core.handoff`;
    #: a plain String rather than a Postgres enum, so a new code is a code
    #: change and not an `ALTER TYPE` on a live table.
    handoff_code: Mapped[str | None] = mapped_column(String(32))
    #: The handover note written for the owner. Composed today and sent in the
    #: alert, then thrown away - so the console could show that a conversation
    #: is with a human but never why.
    handoff_summary: Mapped[str | None] = mapped_column(Text)
    handed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("provider_msg_id", name="uq_message_provider_msg_id"),
        UniqueConstraint("seq", name="uq_message_seq"),
        Index("ix_message_conv_seq", "conversation_id", "seq"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    #: Monotonic insertion order. `created_at` cannot be used for ordering:
    #: Postgres `now()` is transaction-scoped, so two messages written in one
    #: transaction share a timestamp and would come back in arbitrary order.
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[Direction] = mapped_column(
        _enum(Direction, "direction"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        _enum(MessageStatus, "message_status"), default=MessageStatus.received
    )
    body: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)  # used by voice in v2
    provider_msg_id: Mapped[str | None] = mapped_column(String(128))
    detected_language: Mapped[str | None] = mapped_column(String(16))
    cost: Mapped[float | None] = mapped_column(Numeric(10, 6))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ------------------------------------------------------- phase 2+ tables (unused now)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        # The reminder sweep's index. Partial, because once an appointment has
        # been reminded it never needs to be considered again.
        Index(
            "ix_appointment_reminder_due",
            "client_id",
            "start_at",
            postgresql_where=text("reminder_sent_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service: Mapped[str | None] = mapped_column(String(200))
    item: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    calendar_event_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    #: Set when the owner replies OK to the booking alert.
    #: Set once the reminder has gone out. The sweep is idempotent on it, so a
    #: worker restart cannot send a second reminder for one appointment.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    owner_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Lead(Base):
    """What the customer needs, and how to reach them, when no time has been
    agreed yet."""

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    item: Mapped[str | None] = mapped_column(String(200))
    issue: Mapped[str | None] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScheduledMessage(Base):
    """One outbound message that is due later.

    The same argument as `app/core/reminders.py` makes, generalised. The
    obvious implementation of "text them in six months" is a delayed job, and
    it is the fragile one: the job lives in Redis, so a flush loses every
    pending recall silently, and a customer who books in the meantime leaves an
    orphan that still fires.

    So this is a row, and a sweep decides what is due. Nothing is held in
    memory, a worker outage self-heals on the next pass, and cancelling is an
    UPDATE rather than a hunt through a queue.

    `body` is not stored. The text is composed from a template at send time, in
    the customer's language, exactly as a reminder is - a message written
    months ahead and stored verbatim is a message that will eventually be sent
    after the business changed its name, its prices or its mind.
    """

    __tablename__ = "scheduled_messages"
    __table_args__ = (
        # The sweep's index. Partial, because a message that has been sent or
        # cancelled is never considered again, and those accumulate forever
        # while the pending set stays small.
        Index(
            "ix_scheduled_due",
            "client_id",
            "due_at",
            postgresql_where=text("sent_at IS NULL AND cancelled_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    #: Which template composes it: "recall" or "follow_up".
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: What it is about, in the customer's own words - "the villa in Marbella",
    #: "your check-up". Echoed into the template, never used as the whole
    #: message, so a stored string cannot become an unreviewed outbound text.
    topic: Mapped[str | None] = mapped_column(String(200))
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Marks the *attempt*, like `appointments.reminder_sent_at`. A message
    #: that could not be delivered is still marked: the alternative is retrying
    #: every quarter of an hour forever.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set when the reason for the message goes away - they booked, they opted
    #: out, the owner called it off.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Job(Base):
    """Powers "is it ready yet?". Status set by the owner over WhatsApp."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    item: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.in_shop
    )
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Call(Base):
    """v1 logs missed calls; v2 adds AI-answered calls."""

    __tablename__ = "calls"
    __table_args__ = (
        UniqueConstraint("provider_call_id", name="uq_call_provider_call_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    #: Twilio CallSid - call status webhooks are resent, so this dedupes them.
    provider_call_id: Mapped[str | None] = mapped_column(String(128))
    #: When the missed-call text-back went out. NULL means it has not been sent.
    texted_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    from_number: Mapped[str | None] = mapped_column(String(32))
    outcome: Mapped[CallOutcome] = mapped_column(
        _enum(CallOutcome, "call_outcome"), default=CallOutcome.missed
    )
    duration: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UsageEvent(Base):
    """Claude tokens, messages and minutes - billing and gross margin per shop."""

    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_client_at", "client_id", "at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    units: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ----------------------------------------------------------------- phase 3: web
#
# The console, the owner dashboard and the landing page. None of these tables is
# touched by the message loop, and only `signups` is reachable without a login.


class SessionKind(str, enum.Enum):
    admin = "admin"    # our staff, in the console
    owner = "owner"    # a shop owner, in their own dashboard


class SignupStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    converted = "converted"
    rejected = "rejected"


class AdminUser(Base):
    """Our own staff. Not a tenant - an admin is not scoped to a client."""

    __tablename__ = "admin_users"
    __table_args__ = (UniqueConstraint("email", name="uq_admin_users_email"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    #: scrypt$n$r$p$salt$hash - see app/web/passwords.py
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """A logged-in browser session.

    The cookie carries a random token; only its SHA-256 is stored, so a database
    leak does not hand over live sessions. An owner session is bound to exactly
    one `client_id` and that is the only place a request may learn which shop it
    is allowed to read - never the URL.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        # Exactly one subject. A session that is both, or neither, is a bug we
        # would rather have the database refuse than discover in a route.
        CheckConstraint(
            "(kind = 'admin' and admin_user_id is not null and client_id is null)"
            " or (kind = 'owner' and client_id is not null"
            " and admin_user_id is null)",
            name="ck_auth_session_subject",
        ),
        Index("ix_auth_session_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    kind: Mapped[SessionKind] = mapped_column(
        _enum(SessionKind, "session_kind"), nullable=False
    )
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE")
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE")
    )
    #: Rendered into every form and required back on every POST.
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class LoginCode(Base):
    """A one-time code texted to a shop owner.

    Hashed like a password, single-use, short-lived, and attempt-limited: six
    digits is only 10^6, so unlimited guesses would be a trivial break.
    """

    __tablename__ = "login_codes"
    __table_args__ = (Index("ix_login_code_phone_at", "phone", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Signup(Base):
    """A prospective *shop* from the landing page.

    Deliberately not the same thing as `leads`, which is a customer of a shop
    who wants a callback. A signup has no `client_id` until we convert it, at
    which point it points at the client the console created.
    """

    __tablename__ = "signups"
    __table_args__ = (Index("ix_signup_status_at", "status", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120))
    #: What they say they do, in their own words - "brakes and tyres", "dog
    #: grooming". Free text, and still useful: it is what a prospect says when
    #: none of the niches below fits them.
    trade: Mapped[str | None] = mapped_column(String(120))
    #: A key in `app.agent.niche.NICHES`, chosen from the form's list. This is
    #: the one that decides which prompt, tool set and boundary block the shop
    #: gets on conversion, so it is a separate column from `trade` rather than
    #: something a human reads out of prose.
    niche: Mapped[str | None] = mapped_column(String(32))
    #: ISO-3166 alpha-2, upper case. Picks the timezone and the region a phone
    #: number typed without a country code is parsed in.
    country: Mapped[str | None] = mapped_column(String(2))
    #: The languages their customers write in - seeds `enabled_languages`.
    languages: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    #: Doctoralia, Gesden, Inmovilla... what we would have to talk to.
    crm_tool: Mapped[str | None] = mapped_column(String(64))
    #: Their real website. Not to be confused with the form's `website` field,
    #: which is a honeypot and is never stored.
    website_url: Mapped[str | None] = mapped_column(String(300))
    #: When they ticked the consent box. GDPR Art. 7(1) makes demonstrating
    #: consent the controller's problem, and a validated-then-discarded
    #: checkbox demonstrates nothing.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        String(64), default="landing", server_default=text("'landing'"), nullable=False
    )
    status: Mapped[SignupStatus] = mapped_column(
        _enum(SignupStatus, "signup_status"),
        default=SignupStatus.new,
        server_default=text("'new'"),
        nullable=False,
    )
    #: Set when a signup becomes a shop.
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Who changed what.

    A console that can edit any shop's prompt and flip it live needs a record of
    who did it. Append-only: nothing in the app updates or deletes a row here.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_client_at", "client_id", "at"),
        Index("ix_audit_at", "at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_kind: Mapped[SessionKind] = mapped_column(
        _enum(SessionKind, "session_kind"), nullable=False
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
