"""Create or reset a console admin account.

    python -m seeds.admin_user
    python -m seeds.admin_user --email you@example.com
    python -m seeds.admin_user --email you@example.com --password ... --name "You"

With no `--password` it prompts, and the typing is hidden. Passing one on the
command line puts it in your shell history, so the prompt is the better habit.

An existing address is updated rather than duplicated, which makes this the
password reset too. Resetting also signs that admin out everywhere, because a
reset usually means something went wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app import repo_admin
from app.db import dispose_engine, session_scope
from app.web.passwords import hash_password

MIN_LENGTH = 12


def _read_password() -> str:
    first = getpass.getpass("Password: ")
    if len(first) < MIN_LENGTH:
        print(f"Too short - use at least {MIN_LENGTH} characters.")
        sys.exit(1)
    if first != getpass.getpass("Again: "):
        print("Those do not match.")
        sys.exit(1)
    return first


async def upsert(email: str, password: str, name: str | None) -> str:
    async with session_scope() as session:
        existing = await repo_admin.get_admin_by_email(session, email)
        if existing is not None:
            await repo_admin.set_admin_password(
                session, existing.id, hash_password(password)
            )
            # A password reset should not leave old browsers signed in.
            await repo_admin.revoke_sessions_for_admin(session, existing.id)
            return f"Password reset for {existing.email}, and all its sessions revoked."
        admin = await repo_admin.create_admin(
            session, email=email, password_hash=hash_password(password), name=name
        )
        return f"Created {admin.email}. Sign in at /admin/login."


async def main() -> None:
    parser = argparse.ArgumentParser(description="Create or reset a console admin.")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--name")
    args = parser.parse_args()

    email = (args.email or input("Email: ")).strip().lower()
    if "@" not in email:
        print("That does not look like an email address.")
        sys.exit(1)
    password = args.password or _read_password()
    if len(password) < MIN_LENGTH:
        print(f"Too short - use at least {MIN_LENGTH} characters.")
        sys.exit(1)

    try:
        print(await upsert(email, password, args.name))
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
