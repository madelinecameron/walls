from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg.rows import DictRow, dict_row

Connection = psycopg.AsyncConnection[DictRow]


class Database:
    def __init__(self, url: str) -> None:
        self.url = url

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Connection]:
        """One transaction: commits when the block exits cleanly, rolls back on error."""
        conn = await psycopg.AsyncConnection.connect(self.url, row_factory=dict_row)
        async with conn:
            await register_vector_async(conn)
            await conn.execute("BEGIN")
            yield conn


def migration_files() -> list[tuple[str, str]]:
    folder = resources.files("walls") / "migrations"
    files = sorted(f for f in folder.iterdir() if f.name.endswith(".sql"))
    return [(f.name.removesuffix(".sql"), f.read_text()) for f in files]


async def migrate(db: Database) -> list[str]:
    """Apply migrations not yet in schema_migrations, in name order. Returns the applied ones."""
    applied = []
    async with db.connect() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('walls.migrate'))")
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cur = await conn.execute("SELECT version FROM schema_migrations")
        done = {row["version"] for row in await cur.fetchall()}
        for version, sql in migration_files():
            if version in done:
                continue
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
    return applied
