"""Recompute every shop's stored `prompt_version` after a code-side prompt edit.

    python scripts/reprompt.py --dry-run     # show what would move
    python scripts/reprompt.py               # write it

`client_config.prompt_version` is the sha256 of a shop's whole rendered prompt
across every channel. The console recomputes and stores it on each config save
(`app/web/admin.py:727-731`), which covers the case where an *owner* changes
something.

Nothing covered the other case. When the prompt is edited in code - a reworded
rule, a new per-niche block - every shop's rendered prompt moves and every
stored version is silently stale. The console's shop page then reports drift
for shops that are perfectly fine, and the one signal that tells you a prompt
changed unexpectedly stops meaning anything.

So this is part of shipping a prompt change, not an optional tidy-up. Run it
after deploying one.

It writes `prompt_version` and nothing else. It does not touch the prompt, the
config, or any customer-visible state, and `--dry-run` is read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from app import repo, repo_admin
from app.agent.prompt import config_prompt_version
from app.core.router import invalidate, load_shop
from app.db import dispose_engine, session_scope
from app.models import Channel

#: Every channel a shop could answer on. The stored version hashes all of them,
#: so it must not depend on which ones this shop happens to have configured.
CHANNELS = sorted(channel.value for channel in Channel)


async def run(dry_run: bool) -> int:
    moved = 0
    async with session_scope() as session:
        clients = await repo_admin.list_clients(session)
        if not clients:
            print("no shops")
            return 0

        for client in clients:
            invalidate(client.id)
            shop = await load_shop(session, client.id, use_cache=False)
            version = config_prompt_version(
                {"name": shop.name, "timezone": shop.timezone, "config": shop.config},
                CHANNELS,
            )
            stored = client.config.prompt_version if client.config else None
            if stored == version:
                print(f"    same  {client.name}  {version}")
                continue

            moved += 1
            arrow = f"{stored or '(none)'} -> {version}"
            if dry_run:
                print(f"   would  {client.name}  {arrow}")
            else:
                await repo.set_prompt_version(session, client.id, version)
                print(f" written  {client.name}  {arrow}")

    verb = "would move" if dry_run else "moved"
    print(f"\n{moved} of {len(clients)} {verb}.")
    if dry_run and moved:
        print("Re-run without --dry-run to write them.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recompute every shop's stored prompt_version."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would change and write nothing",
    )
    args = parser.parse_args()
    try:
        return await run(args.dry_run)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
