#!/usr/bin/env python3
"""Manage web UI login accounts.

Usage:
  .venv/bin/python scripts/manage_users.py create <username> [--scope SCOPE]
  .venv/bin/python scripts/manage_users.py list
  .venv/bin/python scripts/manage_users.py reset-password <username>

The password is prompted for (hidden) unless PAPER_CHINA_USER_PASSWORD is set.
The first user should use --scope local to inherit the existing library;
later users get their own isolated scope (default: their username).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import async_session, init_db  # noqa: E402
from app.core.users import create_user, hash_password  # noqa: E402
from app.models.user import User  # noqa: E402


def _read_password() -> str:
    password = os.environ.get("PAPER_CHINA_USER_PASSWORD", "")
    if not password:
        password = getpass.getpass("Password: ")
    if not password:
        raise SystemExit("empty password")
    return password


async def _create(username: str, scope: str | None) -> None:
    await init_db()
    async with async_session() as db:
        existing = await db.scalar(select(User).where(User.username == username))
    if existing is not None:
        raise SystemExit(f"user {username!r} already exists")
    user = await create_user(username, _read_password(), scope)
    print(f"created user {user.username} scope={user.access_scope}")


async def _list() -> None:
    await init_db()
    async with async_session() as db:
        rows = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    for user in rows:
        print(f"{user.username}\tscope={user.access_scope}\tcreated={user.created_at}")
    if not rows:
        print("(no users)")


async def _reset_password(username: str) -> None:
    await init_db()
    async with async_session() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"no such user: {username}")
        user.password_hash = hash_password(_read_password())
        await db.commit()
    print(f"password reset for {username}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("username")
    create.add_argument("--scope", default=None)
    sub.add_parser("list")
    reset = sub.add_parser("reset-password")
    reset.add_argument("username")
    args = parser.parse_args()

    if args.command == "create":
        asyncio.run(_create(args.username, args.scope))
    elif args.command == "list":
        asyncio.run(_list())
    elif args.command == "reset-password":
        asyncio.run(_reset_password(args.username))


if __name__ == "__main__":
    main()
