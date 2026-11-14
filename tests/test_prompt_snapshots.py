"""The rendered prompt must not move unless someone meant it to.

`tests/test_prompt.py` asserts that particular sentences are present. That
catches a deleted rule; it does not catch a reworded one, a reordered section,
or a block that leaks from one vertical into another. These snapshots catch all
three, which is what makes it safe to take the monolithic template apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.prompt_fixtures import CHANNELS, render, vertical_keys

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
UPDATE_HINT = "run: python scripts/snapshot_prompts.py --update"


@pytest.mark.parametrize("vertical", vertical_keys())
class TestThePromptIsUnchanged:
    def test_the_snapshot_exists(self, vertical):
        target = SNAPSHOT_DIR / f"prompt_{vertical}.txt"
        assert target.exists(), f"no snapshot for {vertical!r} - {UPDATE_HINT}"

    def test_the_rendered_prompt_matches_the_snapshot(self, vertical):
        target = SNAPSHOT_DIR / f"prompt_{vertical}.txt"
        stored = target.read_text(encoding="utf-8")
        assert render(vertical) == stored, (
            f"the system prompt for {vertical!r} changed.\n"
            f"If that was deliberate, {UPDATE_HINT} and read the diff."
        )

    def test_rendering_twice_gives_the_same_bytes(self, vertical):
        # The determinism contract in app/agent/prompt.py, asserted per vertical
        # rather than for one hand-written shop.
        assert render(vertical) == render(vertical)


class TestTheSnapshotSetIsComplete:
    def test_there_is_one_snapshot_per_vertical(self):
        on_disk = {p.stem.removeprefix("prompt_") for p in SNAPSHOT_DIR.glob("prompt_*.txt")}
        assert on_disk == set(vertical_keys()), (
            "a vertical was added or removed without its snapshot - " + UPDATE_HINT
        )

    def test_every_channel_appears_in_every_snapshot(self):
        for vertical in vertical_keys():
            body = (SNAPSHOT_DIR / f"prompt_{vertical}.txt").read_text(encoding="utf-8")
            for channel in CHANNELS:
                assert f"=== channel: {channel}" in body, (
                    f"{vertical} snapshot is missing the {channel} section"
                )
