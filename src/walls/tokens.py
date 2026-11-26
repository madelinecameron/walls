"""The bearer token agents send. A token set in the config wins; otherwise the server makes one
on first start. Only its hash is stored, so nothing on the server can print it again: a lost
token is replaced, never read back."""

import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable

from .config import Settings
from .db import Database

NAME = "auth_token"

Check = Callable[[str], Awaitable[bool]]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def ensure(db: Database) -> str | None:
    """Makes the token if there is none. Returns it only when it was just made."""
    fresh = secrets.token_urlsafe(32)
    async with db.connect() as conn:
        cur = await conn.execute(
            "INSERT INTO server_tokens (name, hash) VALUES (%s, %s)"
            " ON CONFLICT (name) DO NOTHING RETURNING name",
            (NAME, _hash(fresh)),
        )
        return fresh if await cur.fetchone() else None


async def rotate(db: Database) -> str:
    """Replaces the token. The old one stops working at once."""
    fresh = secrets.token_urlsafe(32)
    async with db.connect() as conn:
        await conn.execute(
            "INSERT INTO server_tokens (name, hash) VALUES (%s, %s)"
            " ON CONFLICT (name) DO UPDATE SET hash = EXCLUDED.hash, created_at = now()",
            (NAME, _hash(fresh)),
        )
    return fresh


async def matches(db: Database, presented: str) -> bool:
    async with db.connect() as conn:
        cur = await conn.execute(
            "SELECT 1 FROM server_tokens WHERE name = %s AND hash = %s",
            (NAME, _hash(presented)),
        )
        return await cur.fetchone() is not None


def checker(db: Database, settings: Settings) -> Check | None:
    """How a presented token is checked, or None when auth is off."""
    if not settings.auth:
        return None
    if settings.auth_token:
        expected = settings.auth_token.encode()

        async def configured(presented: str) -> bool:
            return hmac.compare_digest(presented.encode(), expected)

        return configured

    async def stored(presented: str) -> bool:
        return await matches(db, presented)

    return stored
