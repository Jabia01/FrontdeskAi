"""Write or check the rendered system prompt for every vertical.

    python scripts/snapshot_prompts.py            # check, exit 1 on any diff
    python scripts/snapshot_prompts.py --update   # rewrite the snapshots

The prompt is one monolithic template today and is about to be split into
composable blocks. A refactor that is meant to change nothing can quietly
change a sentence, and the only symptom in production is a cold prompt cache
and subtly different replies. So: capture the output first, refactor second,
and read the diff.

`--update` is the deliberate act. Never run it to make a red test green without
reading what moved.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, ".")

from tests.prompt_fixtures import render, vertical_keys

SNAPSHOT_DIR = Path("tests/snapshots")


def path_for(vertical: str) -> Path:
    return SNAPSHOT_DIR / f"prompt_{vertical}.txt"


def update() -> int:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for vertical in vertical_keys():
        target = path_for(vertical)
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        after = render(vertical)
        target.write_text(after, encoding="utf-8")
        state = "unchanged" if before == after else ("new" if not before else "CHANGED")
        print(f"{state:>9}  {target}")
    return 0


def check() -> int:
    failures = 0
    for vertical in vertical_keys():
        target = path_for(vertical)
        after = render(vertical)
        if not target.exists():
            print(f"  MISSING  {target}")
            failures += 1
            continue
        before = target.read_text(encoding="utf-8")
        if before == after:
            print(f"       ok  {target}")
            continue
        failures += 1
        print(f"  CHANGED  {target}")
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{target} (stored)",
            tofile=f"{target} (rendered)",
        )
        sys.stdout.writelines(diff)
    if failures:
        print(
            f"\n{failures} snapshot(s) differ. If the change is intended, run:"
            "\n    python scripts/snapshot_prompts.py --update"
        )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot the rendered system prompt for every vertical."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the snapshots instead of checking them",
    )
    args = parser.parse_args()
    sys.exit(update() if args.update else check())


if __name__ == "__main__":
    main()
