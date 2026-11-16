"""The live eval's guards and its scoring layer, without the live model.

`scripts/eval_niches.py` is the one file in the repo that holds an API key and
writes rows, and it is outside `testpaths` so none of it is collected. That
makes two things worth testing from here:

* **the safety property it depends on.** The script cannot reach a production
  database, and that is not the script's own doing - importing the harness
  pulls in `tests/conftest.py`, which overwrites `DATABASE_URL` before any
  flag is parsed. If that ever stops being true, a script that creates and
  deletes tenants would be pointed at a real clinic's data, so it is asserted
  here rather than assumed;
* **the scoring layer.** `score()` turns a transcript into verdicts, and
  until now the only way to run it was to pay for a live run. Driving it over
  a stubbed transcript proves the wiring - that it finds the replies, the
  tool calls and the handoff code - which is the part that can be wrong
  without anybody noticing.
"""

from __future__ import annotations

import functools
import importlib.util
import pathlib
import sys

import pytest

from tests.evals.checks import Verdict
from tests.scenarios import Scenario, Turn, play


@functools.cache
def _driver():
    """Load `scripts/eval_niches.py`, which is not an importable package.

    Registered in `sys.modules` before it is executed, and that is not
    optional: the file uses `from __future__ import annotations`, so
    `@dataclass` resolves its field types by looking the module up by name,
    and a module that is not there yet fails with an unhelpful
    `AttributeError` from inside `dataclasses`.
    """
    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "eval_niches.py"
    spec = importlib.util.spec_from_file_location("eval_niches", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestItCannotReachProduction:
    def test_the_resolved_database_is_the_local_test_one(self):
        """The whole of the production guard. `tests/conftest.py` overwrites
        `DATABASE_URL` at import time, so by the time the eval parses an
        argument the question is already settled.

        The first version of the script had an `--allow-host` flag for
        running against a remote database. The flag was a lie - conftest had
        pinned the URL before it was read - and a safety valve that does
        nothing is worse than none, because somebody will trust it.
        """
        from urllib.parse import urlparse

        from app.config import get_settings

        url = str(get_settings().database_url)
        assert urlparse(url).hostname in {"localhost", "127.0.0.1"}
        assert url.endswith("/frontdesk_test")

    def test_the_outbound_credentials_are_blanked_too(self):
        """So a live eval cannot send a real SMS or WhatsApp message to the
        phone numbers the scenarios make up."""
        from app.config import get_settings

        settings = get_settings()
        assert not settings.twilio_account_sid
        assert not settings.twilio_auth_token

    def test_the_driver_refuses_without_an_api_key(self, monkeypatch):
        """Exit 2, not a run full of holding replies scored as passes."""
        driver = _driver()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(
            driver.get_settings(), "anthropic_api_key", "", raising=False
        )
        reason = driver.refuse_reason()
        assert "ANTHROPIC_API_KEY" in reason

    def test_the_driver_checks_the_library_before_spending_anything(self):
        """A malformed library looks like a model failure in the table, which
        is the most expensive kind of mistake an eval can make."""
        driver = _driver()
        import inspect

        source = inspect.getsource(driver.refuse_reason)
        assert "check_library()" in source

    def test_exit_codes_are_distinguishable(self):
        driver = _driver()
        assert driver.EXIT_OK == 0
        assert driver.EXIT_FAILURES == 1
        assert driver.EXIT_REFUSED == 2


class TestScoringATranscript:
    async def test_a_clean_conversation_scores_clean(self, db):
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/clean",
                niche="dental",
                languages=("es", "en"),
                turns=(
                    Turn(
                        "Hello, I would like to book a whitening consultation",
                        reply=(
                            "Hello! I am the clinic's AI assistant. Let me "
                            "look at what is free."
                        ),
                        tools=("check_availability",),
                        expect_language="en",
                    ),
                ),
            )
        )
        results = driver.score(transcript)
        assert results
        assert [check.name for check in results if check.failed] == []

    async def test_an_invented_price_is_caught_against_the_shops_own_figures(
        self, db
    ):
        """`score` reads the configured prices off the shop the conversation
        ran against. A hardcoded list would keep passing after the seed data
        changed, which is the one way this check can quietly stop meaning
        anything."""
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/invented-price",
                niche="dental",
                languages=("es", "en"),
                turns=(
                    Turn(
                        "How much for six veneers?",
                        reply="Six veneers would be 2,940 € in total.",
                    ),
                ),
            )
        )
        results = driver.score(transcript)
        failures = {check.name for check in results if check.failed}
        assert "no_invented_price" in failures

    async def test_a_configured_price_is_not_caught(self, db):
        """The other half, and the half that matters more: a checker that
        fires on the right answer gets switched off."""
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/configured-price",
                niche="dental",
                languages=("es", "en"),
                turns=(
                    Turn(
                        "How much is a veneer?",
                        reply="Veneers start from 490 € per tooth.",
                    ),
                ),
            )
        )
        results = driver.score(transcript)
        assert "no_invented_price" not in {
            check.name for check in results if check.failed
        }

    async def test_a_missing_disclosure_is_caught_on_the_first_reply_only(
        self, db
    ):
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/no-disclosure",
                niche="dental",
                languages=("es", "en"),
                turns=(
                    Turn("Hello", reply="Hello! How can I help?"),
                    Turn("A cleaning please", reply="Of course, when suits?"),
                ),
            )
        )
        results = driver.score(transcript)
        disclosure = [
            check for check in results if check.name == "disclosed_on_first_reply"
        ]
        # Once, for the first reply - not once per turn.
        assert len(disclosure) == 1
        assert disclosure[0].failed

    async def test_a_forbidden_phrase_is_caught(self, db):
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/forbidden",
                niche="real_estate",
                languages=("es", "en"),
                forbidden_phrases=("golden visa",),
                turns=(
                    Turn(
                        "Can I get residency?",
                        reply="The golden visa is still open to buyers.",
                    ),
                ),
            )
        )
        results = driver.score(transcript)
        failures = {check.name for check in results if check.failed}
        assert "no_forbidden_phrase" in failures

    async def test_a_handoff_that_never_happened_is_caught(self, db):
        """The worst failure the eval can find: the scenario expected an
        escalation, the conversation carried on, and nobody was told."""
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/no-handoff",
                niche="dental",
                languages=("es", "en"),
                turns=(
                    Turn(
                        "My tooth is bleeding",
                        reply="It is probably fine, see you Tuesday.",
                        expect_handoff_code="medical_concern",
                    ),
                ),
            )
        )
        results = driver.score(transcript)
        failed = {check.name for check in results if check.failed}
        assert "handoff_code_in" in failed

    async def test_a_wrong_language_reply_is_caught(self, db):
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/wrong-language",
                niche="dental",
                languages=("es", "en", "de"),
                turns=(
                    Turn(
                        "Guten Tag, ich braeuchte einen Termin",
                        reply="Thanks, we can see you on Tuesday at ten o'clock.",
                        language="de",
                        expect_language="de",
                    ),
                ),
            )
        )
        results = driver.score(transcript)
        language = [check for check in results if check.name == "language_matches"]
        assert language and language[0].failed

    async def test_a_short_reply_is_skipped_not_failed(self, db):
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/short-reply",
                niche="dental",
                languages=("es", "en"),
                turns=(
                    Turn(
                        "Gracias",
                        reply="¡Hasta luego!",
                        language="es",
                        expect_language="es",
                    ),
                ),
            )
        )
        results = driver.score(transcript)
        language = [check for check in results if check.name == "language_matches"]
        assert language and language[0].verdict is Verdict.SKIP


class TestTheOutput:
    async def test_the_json_shape_carries_the_replies_and_the_verdicts(self, db):
        """Whoever reads a failing run needs the reply next to the verdict. A
        pass means "nothing known-bad was found", not "a human read it", so
        the transcript has to be in the file."""
        driver = _driver()
        transcript = await play(
            Scenario(
                name="scoring/json",
                niche="dental",
                languages=("es", "en"),
                turns=(Turn("Hello", reply="Hi, I am the clinic's AI assistant."),),
            )
        )
        result = driver.ScenarioResult(
            scenario=transcript.scenario,
            results=driver.score(transcript),
            transcript=transcript,
        )
        payload = driver.as_json({"dental": [result]})
        row = payload["niches"]["dental"][0]
        assert row["scenario"] == "scoring/json"
        assert row["replies"] == ["Hi, I am the clinic's AI assistant."]
        assert all({"name", "verdict", "detail"} <= set(c) for c in row["checks"])

    @pytest.mark.parametrize(
        ("error", "failures", "expected"),
        [("", 0, "pass"), ("", 1, "FAIL"), ("boom", 0, "ERR ")],
    )
    def test_the_mark_in_the_table_says_which_of_three_things_happened(
        self, error, failures, expected
    ):
        """An error and a failure are different events: one is a bug in the
        harness, the other is a finding about the model."""
        from tests.evals.checks import CheckResult

        driver = _driver()
        result = driver.ScenarioResult(
            scenario=Scenario(name="x", niche="dental", turns=(Turn("hi"),)),
            results=[CheckResult("c", Verdict.FAIL, "")] * failures,
            error=error,
        )
        assert result.mark == expected
