"""Drive seventy-five real conversations past the real model and score them.

    python scripts/eval_niches.py                   # all three niches
    python scripts/eval_niches.py --niche dental    # one
    python scripts/eval_niches.py --json out.json   # machine-readable too

This is the thing the whole Phase 7 plan was for. Everything in `tests/`
proves the plumbing with the model stubbed out; this proves the behaviour,
which is the only question a clinic actually cares about: when a patient says
their tooth is bleeding, does it refuse and escalate, or does it help?

**Why it lives in `scripts/` and not in `tests/`.** `pyproject.toml` sets
`testpaths = ["tests"]`, so this file is not collected, and the guard class
`TestTheSuiteCannotCallAProvider` stays true by construction rather than by
remembering to skip a marker. A pytest marker would have put
network-and-API-key code inside the suite, one `-m` flag away from running in
CI.

**It shares `play()` with the deterministic tier.** Not a copy of it - the
same function, with `live=True`. A harness that drove a different code path
from the eval would be proving something about a path nothing uses.

**Safety, and it is not this file's doing.** Importing the harness pulls in
`tests/conftest.py`, which overwrites `DATABASE_URL` with the local
`frontdesk_test` URL and blanks the Twilio and Google credentials, at import
time. So this script cannot reach a production database or send a real
message, whatever the environment says. `refuse_reason` asserts that rather
than trusting it, and the staging tenant it creates per niche is deleted on
the way out.

It needs `ANTHROPIC_API_KEY` and exits 2 without one, rather than silently
scoring the holding reply and calling it a pass.

**Do not run this while pytest is running.** Both use `frontdesk_test`, and
the suite truncates every table between tests - which would delete this
script's shop mid-conversation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

sys.path.insert(0, ".")

from app.agent.niche import niche_for
from app.agent.tools import tool_names
from app.config import get_settings
from tests.evals import checks
from tests.evals.checks import CheckResult, Verdict
from tests.evals.library import BY_NICHE, check_library
from tests.scenarios import Scenario, Transcript, make_niche_shop, play

#: What `tests/conftest.py` is expected to have pinned the host to. Anything
#: else means that pinning is gone, and the one thing this script must never
#: do is create a tenant next to a real clinic's data.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db", "postgres", ""})

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_REFUSED = 2


# ----------------------------------------------------------------- results


@dataclass(slots=True)
class ScenarioResult:
    scenario: Scenario
    results: list[CheckResult] = field(default_factory=list)
    transcript: Transcript | None = None
    cost: float = 0.0
    error: str = ""

    @property
    def failures(self) -> list[CheckResult]:
        return [result for result in self.results if result.failed]

    @property
    def ok(self) -> bool:
        return not self.error and not self.failures

    @property
    def mark(self) -> str:
        if self.error:
            return "ERR "
        if self.failures:
            return "FAIL"
        if any(result.verdict is Verdict.SKIP for result in self.results):
            return "pass*"
        return "pass"


# ----------------------------------------------------------------- scoring


def score(transcript: Transcript) -> list[CheckResult]:
    """Every checker the scenario asked for, over the whole conversation.

    Reads the transcript rather than the model's own claims about itself. The
    reply's language in particular: `detected_language` is the model's opinion
    of what the *customer* wrote, and a bot that detects German correctly and
    answers in English has failed in exactly the way that loses the enquiry.
    """
    scenario = transcript.scenario
    spec = niche_for(scenario.niche)
    allowed_tools = tool_names(scenario.niche)
    results: list[CheckResult] = []

    # Read off the shop the conversation actually ran against, not written in
    # here. A hardcoded list would keep passing after the seed data changed,
    # which is the one way this check can quietly stop meaning anything.
    configured: list[str | int | float] = []
    for played in transcript:
        shop = played.kwargs.get("shop")
        if shop is None:
            continue
        for row in shop.config.get("price_ranges") or []:
            configured.extend(
                value for value in (row.get("low"), row.get("high")) if value
            )
        break

    expected_codes = {
        code for turn in scenario.turns for code in turn.expected_codes
    }
    wanted_tools = [
        tool for turn in scenario.turns for tool in (turn.expect_tools or ())
    ]

    # Every figure the customer has mentioned so far, not just on this turn:
    # a budget given in message one and confirmed back in message three is
    # still the customer's number.
    said_by_customer: list[str] = []

    for played in transcript:
        said_by_customer.append(played.inbound)
        customer_said = " ".join(said_by_customer)
        for reply in played.outbound:
            results.append(
                checks.no_invented_price(
                    reply, allowed=configured, customer_said=customer_said
                )
            )
            results.append(
                checks.no_restricted_advice(reply, niche=scenario.niche)
            )
            if played.turn.expect_language:
                results.append(
                    checks.language_matches(reply, played.turn.expect_language)
                )
            # Phrases forbidden for the whole conversation, plus any this one
            # turn adds.
            banned = tuple(scenario.forbidden_phrases) + tuple(
                played.turn.forbidden_phrases
            )
            said = [
                phrase for phrase in banned if phrase.lower() in reply.lower()
            ]
            results.append(
                CheckResult(
                    "no_forbidden_phrase",
                    Verdict.FAIL if said else Verdict.PASS,
                    ", ".join(said) if said else "clean",
                )
            )
        if played.first_reply and played.outbound:
            results.append(
                checks.disclosed_on_first_reply(
                    played.reply, required=spec.discloses
                )
            )

    results.append(
        checks.correct_tool_calls(
            transcript.tool_calls, allowed=allowed_tools, required=wanted_tools
        )
    )
    if expected_codes:
        results.append(
            checks.handoff_code_in(transcript.handoff_code, expected_codes)
        )
    return results


# ------------------------------------------------------------------ running


async def run_niche(niche: str, *, limit: int = 0) -> list[ScenarioResult]:
    """One shop, all of that niche's scenarios, then the shop is removed.

    One shop rather than one per scenario because the prompt cache is scoped
    to a (shop, channel) pair: twenty-five shops would pay twenty-five cold
    caches to evaluate the same configuration twenty-five times.
    """
    scenarios = BY_NICHE[niche]
    if limit:
        scenarios = scenarios[:limit]

    shop = await make_niche_shop(
        niche, name=f"eval-{niche}-{uuid.uuid4().hex[:6]}"
    )
    print(f"\n{'=' * 72}\n{niche}  ({len(scenarios)} scenarios)\n{'=' * 72}")
    out: list[ScenarioResult] = []
    try:
        for scenario in scenarios:
            started = time.monotonic()
            result = ScenarioResult(scenario=scenario)
            try:
                transcript = await play(scenario, shop=shop, live=True)
                result.transcript = transcript
                result.results = score(transcript)
                result.cost = sum(
                    float(played.result.get("cost") or 0.0) for played in transcript
                )
            except Exception as exc:
                # One scenario blowing up must not cost the other
                # twenty-four. The table says ERR and the run goes on.
                result.error = f"{type(exc).__name__}: {exc}"[:200]
            out.append(result)
            print(
                f"  {result.mark:5} {scenario.name:52} "
                f"${result.cost:.4f}  {time.monotonic() - started:4.1f}s"
            )
            for failure in result.failures:
                print(f"         - {failure.name}: {failure.detail}")
            if result.error:
                print(f"         ! {result.error}")
    finally:
        await _remove_shop(shop.client_id)
    return out


async def _remove_shop(client_id) -> None:
    """Delete the staging tenant. Everything else cascades off `clients`."""
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models import Client

    try:
        async with session_scope() as session:
            await session.execute(delete(Client).where(Client.id == client_id))
    except Exception as exc:
        # Worth saying out loud: a tenant left behind is a row somebody has
        # to find later.
        print(f"\n!! could not remove staging tenant {client_id}: {exc}")


# ------------------------------------------------------------------- output


def print_table(by_niche: dict[str, list[ScenarioResult]]) -> None:
    print(f"\n{'=' * 72}\nsummary\n{'=' * 72}")
    checker_names = (*checks.ALL_CHECKS, "no_forbidden_phrase")
    for niche, results in by_niche.items():
        passed = sum(1 for result in results if result.ok)
        cost = sum(result.cost for result in results)
        print(
            f"\n{niche}: {passed}/{len(results)} scenarios clean  "
            f"(${cost:.4f})"
        )
        for name in checker_names:
            relevant = [
                check
                for result in results
                for check in result.results
                if check.name == name
            ]
            if not relevant:
                continue
            failed = sum(1 for check in relevant if check.failed)
            skipped = sum(
                1 for check in relevant if check.verdict is Verdict.SKIP
            )
            flag = "FAIL" if failed else "ok  "
            print(
                f"  {flag} {name:26} {len(relevant) - failed - skipped:4} pass "
                f"{failed:4} fail {skipped:4} skip"
            )
    total = [result for results in by_niche.values() for result in results]
    clean = sum(1 for result in total if result.ok)
    print(
        f"\n{clean}/{len(total)} scenarios clean, "
        f"${sum(result.cost for result in total):.4f} total"
    )
    print(
        "\npass* means something was skipped - usually a reply too short to "
        "judge the language of.\nA pass means nothing known-bad was found, "
        "not that a human has read it."
    )


def as_json(by_niche: dict[str, list[ScenarioResult]]) -> dict:
    return {
        "niches": {
            niche: [
                {
                    "scenario": result.scenario.name,
                    "ok": result.ok,
                    "error": result.error,
                    "cost": round(result.cost, 6),
                    "handoff_code": (
                        result.transcript.handoff_code if result.transcript else None
                    ),
                    "replies": result.transcript.replies if result.transcript else [],
                    "tool_calls": (
                        result.transcript.tool_calls if result.transcript else []
                    ),
                    "checks": [
                        {
                            "name": check.name,
                            "verdict": check.verdict.value,
                            "detail": check.detail,
                        }
                        for check in result.results
                    ],
                }
                for result in results
            ]
            for niche, results in by_niche.items()
        }
    }


# -------------------------------------------------------------------- guards


def refuse_reason() -> str:
    """Why this run must not happen, or an empty string.

    The database check is an assertion, not a choice. Importing the harness
    pulls in `tests/conftest.py`, which overwrites `DATABASE_URL` with the
    local `frontdesk_test` URL at import time - and blanks the Twilio and
    Google credentials while it is there. So this script *cannot* reach a
    production database or send a real message, whatever the environment says.

    The first version of this had an `--allow-host` flag for running against
    a remote database. That flag was a lie: conftest had already pinned the
    URL before the flag was parsed, so it would have printed a warning and
    written locally anyway. Better to check the guarantee actually holds and
    refuse loudly if somebody ever changes conftest.
    """
    settings = get_settings()
    if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")):
        return (
            "ANTHROPIC_API_KEY is not set. Without it every turn degrades to "
            "the holding reply and the table would score that instead of the "
            "model."
        )
    host = (urlparse(str(settings.database_url)).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        return (
            f"the resolved database host is {host!r}, not a local one. "
            f"`tests/conftest.py` is supposed to pin this to the local test "
            f"database; if it no longer does, this script must not run - it "
            f"creates and deletes tenants."
        )
    problems = check_library()
    if problems:
        return "the scenario library is malformed:\n  " + "\n  ".join(problems)
    return ""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche", action="append", choices=sorted(BY_NICHE), default=None
    )
    parser.add_argument("--json", metavar="PATH", default="")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only the first N scenarios per niche, for a smoke run",
    )
    args = parser.parse_args()

    reason = refuse_reason()
    if reason:
        print(f"refusing to run: {reason}")
        return EXIT_REFUSED

    niches = args.niche or sorted(BY_NICHE)
    by_niche: dict[str, list[ScenarioResult]] = {}
    for niche in niches:
        by_niche[niche] = await run_niche(niche, limit=args.limit)

    print_table(by_niche)

    if args.json:
        # `pathlib` rather than `open`: ruff is right that a blocking write
        # belongs nowhere near an async function, even one that has finished
        # every await it is going to make.
        pathlib.Path(args.json).write_text(
            json.dumps(as_json(by_niche), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    failed = sum(
        1 for results in by_niche.values() for result in results if not result.ok
    )
    return EXIT_FAILURES if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
