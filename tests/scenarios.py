"""A multi-turn conversation harness.

Everything else in this suite tests one turn. A regulated niche fails across
turns: the patient describes a treatment in message one, mentions pain in
message four, and the question is whether the clinic's bot still refuses. So
this file provides a sequencer - a scenario in, a transcript out - and the
tests that use it read as conversations rather than as fixtures.

**Not a conftest.** `tests/conftest.py` is deliberately the only one, and this
is a library to import rather than fixtures to inherit. `play` is an ordinary
async function, so a test calls it, or a loop calls it twenty-five times, and
nothing depends on collection order.

**Two tiers, one shape.** Here the model is a stub and the scenario declares
what it answers, which makes the sequencing deterministic and keeps the suite
off the network - the guard at `tests/test_deploy_readiness.py` says it must
stay that way. `scripts/eval_niches.py` reuses `Scenario` and `Turn` verbatim
against the real model, outside the suite. The same scenario text therefore
describes both runs, and a trap written once is checked in both.

**What tier one can and cannot prove.** It proves the plumbing: that the
conversation advances, that history accumulates, that a handoff pauses the AI
and lands a code on the row, that the shop was handed its own niche's tools
and its own boundary. It cannot prove the model's judgement - whether it
*actually* refuses the clinical question, discloses on its first reply, or
stays in the customer's language. Those are `eval_niches.py`'s job, and
pretending otherwise here would be a green test for an untested property.

Three traps this sequencer takes off the scenario author:

* `provider_msg_id` is unique per turn and per run, owned here. Ingest is
  idempotent on it, so two turns with the same text would silently collapse
  into one and the transcript would be short by a message with no error.
* The shop's endpoints are unique per run, so two scenarios in one test
  session cannot land on each other's `sim-` id.
* Owner alerts are swallowed. They enqueue to Redis, which is not running.
"""

from __future__ import annotations

import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from unittest.mock import patch

from app.agent.niche import niche_for
from app.agent.runner import AgentOutcome, Usage
from app.agent.tools import tool_names
from app.core import engine
from app.core.ingest import ingest
from app.db import session_scope
from app.models import Channel
from app.repo import get_conversation, outbound_since
from app.schemas import AgentReply, InboundMessage

from .conftest import make_shop

# --------------------------------------------------------------- the scenario


@dataclass(frozen=True, slots=True)
class Turn:
    """One customer message and what the model does about it.

    The `reply`/`language`/`tools`/`handoff` fields are the stub's script: in
    tier one they are what the model answers. The `expect_*` fields are the
    assertions, and they are what `eval_niches.py` checks against a real
    model, where the script fields are ignored.

    Frozen, because a scenario is data. A mutable turn is a turn one test can
    edit for the next one.
    """

    customer: str

    # --- tier one: what the stub answers
    reply: str = "..."
    language: str = "en"
    tools: tuple[str, ...] = ()
    handoff: bool = False
    handoff_code: str = ""
    #: False drives the engine's own two-failed-attempts rule, which is the
    #: one handoff path no model opinion reaches.
    understood: bool = True

    # --- tier two: what is checked
    expect_language: str | None = None
    #: One code, or several where more than one is genuinely right. "I am
    #: furious about the third failed crown" is both `angry` and `complaint`,
    #: and a scenario that insists on one of them produces a red cell for a
    #: correct answer - which is how a table stops being read.
    expect_handoff_code: str | tuple[str, ...] | None = None
    expect_tools: tuple[str, ...] | None = None
    expect_handoff: bool | None = None
    #: Phrases that must not appear in the reply to this turn. Scenario-wide
    #: ones live on `Scenario.forbidden_phrases`; these are for a trap that
    #: only matters once, like a price named in answer to one question.
    forbidden_phrases: tuple[str, ...] = ()

    @property
    def expected_codes(self) -> tuple[str, ...]:
        """`expect_handoff_code` as a tuple, whichever way it was written."""
        if not self.expect_handoff_code:
            return ()
        if isinstance(self.expect_handoff_code, str):
            return (self.expect_handoff_code,)
        return tuple(self.expect_handoff_code)


@dataclass(frozen=True, slots=True)
class Scenario:
    """One conversation, start to finish."""

    name: str
    niche: str
    languages: tuple[str, ...] = ("es", "en")
    #: A Spanish mobile by default, because Spain is the first market and the
    #: country code is what `app.agent.language` infers from.
    customer_phone: str = "+34600111222"
    turns: tuple[Turn, ...] = ()
    #: Checked by `eval_niches.py` against the real model. Tier one records
    #: which turn was the first reply but cannot judge what was said in it.
    expect_disclosure_in_first_reply: bool = True
    #: Must appear in no reply anywhere in the conversation. This is where a
    #: niche's standing traps go - an invented price, "golden visa", a
    #: diagnosis.
    forbidden_phrases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A typo in a niche key would otherwise run the whole scenario against
        # `other` - the unregulated, full-tool-set shop - and pass.
        if niche_for(self.niche).key != self.niche:
            raise ValueError(
                f"{self.name}: {self.niche!r} is not a niche; "
                f"it would silently run as 'other'"
            )


# -------------------------------------------------------------- the transcript


@dataclass(slots=True)
class PlayedTurn:
    """What happened on one turn, from four different vantage points."""

    index: int
    turn: Turn
    inbound: str
    #: Everything the shop sent in response to this message. Usually one, but
    #: a handoff on a shadow-mode shop drafts instead of sending, and nothing
    #: here assumes a count.
    outbound: list[str]
    tool_calls: list[str]
    status: str
    handoff: bool
    handoff_code: str | None
    handoff_summary: str | None
    conversation_status: str
    #: Exactly the keyword arguments `run_agent` was called with. This is how
    #: the harness can say what the shop was handed without a model: the tool
    #: set, the system prompt, the history, the channel.
    kwargs: dict = field(default_factory=dict)
    #: The engine's own return value for this turn.
    result: dict = field(default_factory=dict)

    @property
    def reply(self) -> str:
        """The reply, for the usual case of exactly one."""
        return self.outbound[0] if self.outbound else ""

    @property
    def first_reply(self) -> bool:
        """Whether this was the business's first word to this customer.

        Delegates to the runner's own predicate rather than restating it. The
        first version of this property restated it as "history is empty",
        which is what the runner itself said - and the harness's very first
        run showed that to be false on every turn, because the engine ingests
        the inbound message before it reads the history. The disclosure that
        rides on this flag had therefore never fired on the text path. Calling
        the real function is what keeps the two from drifting apart again.
        """
        from app.agent.runner import _is_first_reply

        return _is_first_reply(self.kwargs.get("history") or [])

    @property
    def tools_offered(self) -> list[str]:
        """The tool set this shop was actually handed, in prefix order."""
        shop = self.kwargs.get("shop")
        if shop is None:
            return []
        return tool_names(shop.config.get("vertical") or "")

    @property
    def system_prompt(self) -> str:
        """The real cached prefix for this shop and channel.

        Which makes "does a dental shop carry its boundary" a question this
        harness can answer with no model at all.
        """
        shop = self.kwargs.get("shop")
        if shop is None:
            return ""
        return shop.system_prompt(self.kwargs.get("channel") or "simulator")[0]


@dataclass(slots=True)
class Transcript:
    scenario: Scenario
    client_id: uuid.UUID
    conversation_id: uuid.UUID | None
    turns: list[PlayedTurn] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.turns)

    def __getitem__(self, index: int) -> PlayedTurn:
        return self.turns[index]

    def __iter__(self):
        return iter(self.turns)

    @property
    def replies(self) -> list[str]:
        return [body for played in self.turns for body in played.outbound]

    @property
    def tool_calls(self) -> list[str]:
        return [name for played in self.turns for name in played.tool_calls]

    @property
    def handoff_code(self) -> str | None:
        """The code on the row at the end. There is one per conversation."""
        return self.turns[-1].handoff_code if self.turns else None

    @property
    def conversation_status(self) -> str:
        return self.turns[-1].conversation_status if self.turns else ""


# ---------------------------------------------------------------- the stub


def _outcome(turn: Turn) -> AgentOutcome:
    """One turn's script as the thing `run_agent` would have returned."""
    return AgentOutcome(
        reply=AgentReply(
            reply=turn.reply,
            detected_language=turn.language,
            # Above the confidence threshold, so a scenario that declares a
            # language is one where the language is actually recorded. A
            # scenario testing low confidence sets it through `language`
            # being `und`, which the engine treats separately.
            language_confidence=0.95,
            handoff=turn.handoff,
            handoff_reason=turn.customer[:120] if turn.handoff else "",
            handoff_code=turn.handoff_code,
            understood=turn.understood,
        ),
        model="claude-haiku-5-5",
        tool_calls=list(turn.tools),
        usage=Usage(input_tokens=800, output_tokens=40, cost=0.0002),
        handoff_requested=turn.handoff,
        handoff_reason=turn.customer[:120] if turn.handoff else "",
        handoff_summary=f"scenario turn {turn.customer[:80]}" if turn.handoff else "",
        handoff_code=turn.handoff_code,
    )


# ----------------------------------------------------------------- the player


@dataclass(frozen=True, slots=True)
class ShopHandle:
    """A shop a scenario can run on, and the endpoint to reach it.

    Exists so the live eval can create *one* shop per niche and run its
    twenty-five scenarios against it. Prompt caching is scoped to a (shop,
    channel) pair, so a shop per scenario would pay seventy-five cold caches
    to learn nothing - and the thing being evaluated is one clinic's
    configuration, not seventy-five of them.
    """

    client_id: uuid.UUID
    sim_id: str


#: What each niche's shop is seeded with. Deliberately sparse: two of the
#: three price ranges are "from" figures and one treatment is left with no
#: range at all, because "a service the business never priced" is the case
#: hard rule 2 exists for and an eval where every price is configured would
#: never reach it.
#:
#: Written here rather than taken from `defaults.VERTICALS` because these are
#: evaluation fixtures - a change to the seed data for real new shops should
#: not silently change what the eval is asking about.
#: A full working week. `make_shop` opens on Monday and closes on Sunday and
#: says nothing about the other five days, which is right for the tests that
#: assert against it - and made every booking scenario in the first live run
#: impossible. Asked for a Tuesday, the agent correctly answered that it had
#: no Tuesday openings and offered the Monday it did have, and the eval
#: recorded that as a failure to call `book_appointment`.
#:
#: The agent was right and the fixture was wrong, which is the more dangerous
#: way round: a shop with one open day makes the agent look broken.
NICHE_HOURS: dict[str, str] = {
    "monday": "9:00-18:00",
    "tuesday": "9:00-18:00",
    "wednesday": "9:00-18:00",
    "thursday": "9:00-18:00",
    "friday": "9:00-15:00",
    "saturday": "closed",
    "sunday": "closed",
}

NICHE_PRICES: dict[str, list[dict]] = {
    "dental": [
        {"service": "Veneers", "low": 490, "high": 490},
        {"service": "Whitening", "low": 300, "high": 450},
    ],
    "real_estate": [{"service": "Valuation", "low": 0, "high": 0}],
    "immigration": [{"service": "Initial consultation", "low": 90, "high": 90}],
}


async def make_niche_shop(
    niche: str, *, languages: tuple[str, ...] = (), name: str = ""
) -> ShopHandle:
    """One shop configured as the niche says, with endpoints unique per run.

    Seeded with the niche's own services and prices. The default `make_shop`
    sells brakes and oil changes, which is right for every test that asserts
    against it and wrong for an eval: a dental clinic whose configured
    services are "Brakes" and "Oil change" is not a dental clinic, and asking
    it about veneers would be measuring the wrong shop.
    """
    spec = niche_for(niche)
    nonce = uuid.uuid4().hex[:8]
    client_id = await make_shop(
        name or f"{spec.label} ({nonce})",
        vertical=spec.key,
        job_noun=spec.job_noun,
        currency=spec.currency,
        services=list(spec.services),
        price_ranges=NICHE_PRICES.get(spec.key, []),
        hours=NICHE_HOURS,
        enabled_languages=list(languages or spec.market_languages),
        # Unique per run. Two scenarios in one session must not be able to
        # land on each other's endpoints, and `make_shop`'s own defaults are
        # unique by a suffix it does not share with the caller.
        sms_number=f"+3460000{nonce[:4]}",
        whatsapp_id=f"whatsapp:+3460000{nonce[:4]}",
        sim_id=f"sim-{nonce}",
        handoff_keywords=list(spec.handoff_keywords),
    )
    return ShopHandle(client_id=client_id, sim_id=f"sim-{nonce}")


async def play(
    scenario: Scenario,
    *,
    shop: ShopHandle | None = None,
    shadow: bool = False,
    live: bool = False,
) -> Transcript:
    """Run one scenario end to end and read back what landed.

    `shop` reuses an existing one, which is how the live eval keeps one warm
    cache per niche. Without it a shop is created for this scenario alone,
    which is what a test wants: it is then impossible for one test's shop to
    be another's.

    `live` is the whole reason this function is shared with
    `scripts/eval_niches.py`. Left False the model is a stub and the scenario
    declares its answers; set True the real `run_agent` runs and the scenario's
    script fields are ignored. Everything else - the shop, the ingest, the
    engine, the tools, the handoff persistence - is the same code in both,
    which is the point. A harness that drove a different path from the eval
    would be testing a path nothing uses.

    The shop is left behind when this function created it: the `db` fixture
    truncates between tests, which is cheaper than tearing down by hand and
    cannot leak a row into the next test by forgetting one. A caller that
    passed its own `shop` owns it.
    """
    if shop is None:
        shop = await make_niche_shop(scenario.niche, languages=scenario.languages)
    client_id = shop.client_id
    # Deliberately *not* derived from the shop. Two scenarios sharing one
    # shop - which is how the live eval runs - would otherwise generate the
    # same `provider_msg_id`s, and ingest would dedupe the second scenario's
    # turns away into the first scenario's rows.
    run_id = uuid.uuid4().hex[:10]
    if shadow:
        await _set_shadow(client_id)

    transcript = Transcript(
        scenario=scenario, client_id=client_id, conversation_id=None
    )
    seen: list[dict] = []
    script = list(scenario.turns)

    async def fake_run_agent(**kwargs):
        seen.append(kwargs)
        # Indexed by call, not popped: a turn the engine short-circuits
        # before the agent (a control keyword, an owner reply) would
        # otherwise shift every later turn's script onto the wrong message.
        index = len(seen) - 1
        return _outcome(script[index] if index < len(script) else script[-1])

    async def swallow_alert(client_id, body):
        return "scenario"

    async with AsyncExitStack() as stack:
        if not live:
            stack.enter_context(patch.object(engine, "run_agent", fake_run_agent))
        else:
            # The real agent runs, so nothing records the kwargs. Wrap rather
            # than replace, so `PlayedTurn.kwargs` - and the tool-set and
            # prompt properties that read it - work identically in both tiers.
            real_run_agent = engine.run_agent

            async def recording_run_agent(**kwargs):
                seen.append(kwargs)
                return await real_run_agent(**kwargs)

            stack.enter_context(
                patch.object(engine, "run_agent", recording_run_agent)
            )
        # Owner alerts enqueue to Redis, which is not running under the test
        # suite and is not something a read-only eval should be writing to.
        stack.enter_context(
            patch("app.queue.client.enqueue_owner_alert", swallow_alert)
        )

        cursor: int | None = None
        for index, turn in enumerate(script):
            before = len(seen)
            result = await ingest(
                InboundMessage(
                    channel=Channel.simulator,
                    provider="local",
                    external_id=shop.sim_id,
                    from_number=scenario.customer_phone,
                    body=turn.customer,
                    # Owned here, unique per turn and per run. Ingest is
                    # idempotent on this, so two turns with the same text
                    # would collapse into one and the transcript would be
                    # short by a message with nothing raised.
                    provider_msg_id=f"{run_id}-{index}",
                )
            )
            assert result.message_id is not None, (
                f"{scenario.name} turn {index}: ingest deduplicated a message "
                f"the sequencer generated - the id is not unique"
            )
            transcript.conversation_id = result.conversation_id
            engine_result = await engine.handle_inbound_message(
                client_id, result.message_id
            )

            async with session_scope() as session:
                conversation = await get_conversation(
                    session, client_id, result.conversation_id
                )
                fresh = await outbound_since(
                    session, client_id, result.conversation_id, after_seq=cursor
                )
                if fresh:
                    cursor = fresh[-1].seq
                transcript.turns.append(
                    PlayedTurn(
                        index=index,
                        turn=turn,
                        inbound=turn.customer,
                        outbound=[message.body for message in fresh],
                        tool_calls=list(engine_result.get("tool_calls") or []),
                        status=str(engine_result.get("status") or ""),
                        handoff=bool(engine_result.get("handoff")),
                        handoff_code=conversation.handoff_code,
                        handoff_summary=conversation.handoff_summary,
                        conversation_status=conversation.status.value,
                        # The kwargs for *this* turn, if the agent ran at all.
                        # A turn the engine short-circuited has none, and an
                        # empty dict is the honest answer rather than the
                        # previous turn's.
                        kwargs=seen[before] if len(seen) > before else {},
                        result=engine_result,
                    )
                )

    return transcript


async def _set_shadow(client_id: uuid.UUID) -> None:
    """Flip a shop into shadow mode, where replies are drafted not sent.

    Worth being able to drive from a scenario: shadow is how a real clinic's
    first fortnight runs, so "does it still refuse while drafting" is a
    question somebody will ask.
    """
    from sqlalchemy import update

    from app.core.router import invalidate
    from app.models import Client, ClientStatus

    async with session_scope() as session:
        await session.execute(
            update(Client)
            .where(Client.id == client_id)
            .values(status=ClientStatus.shadow)
        )
    # The router caches a shop context, and a status read from a stale one
    # would have the scenario running live while the test believes it is
    # drafting.
    invalidate(client_id)
