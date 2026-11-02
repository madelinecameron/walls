"""Tests run against a real Postgres with pgvector, named by WALLS_TEST_DATABASE_URL. Without it
every database test fails rather than skips, so a green run always means they ran."""

import hashlib
import json
import os
import re
from datetime import UTC, datetime

import httpx
import psycopg
import pytest

from walls.ai import AI
from walls.config import Settings
from walls.db import Database, migrate

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
DIM = 1536


def word_embedding(text: str) -> list[float]:
    """Bag of words hashed into the vector: texts sharing words score high, others near zero, so
    tests can reason about similarity without a provider."""
    vector = [0.0] * DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = sum(x * x for x in vector) ** 0.5 or 1.0
    return [x / norm for x in vector]


class FakeLiteLLM:
    def __init__(self) -> None:
        self.embeddings_down = False
        self.remedy_answer = (
            '{"kind": "permission", "proposal": "Grant renew on the agent policy.", '
            '"effort": "small", "risk": "Token lives longer."}'
        )
        self.embed_inputs: list[str] = []
        self.remedy_calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            if self.embeddings_down:
                return httpx.Response(503, json={"error": "down"})
            self.embed_inputs.append(body["input"])
            return httpx.Response(
                200, json={"data": [{"embedding": word_embedding(body["input"])}]}
            )
        self.remedy_calls.append(body["messages"][1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": self.remedy_answer}}]})


@pytest.fixture
def litellm() -> FakeLiteLLM:
    return FakeLiteLLM()


@pytest.fixture
def ai(litellm: FakeLiteLLM) -> AI:
    http = httpx.AsyncClient(transport=httpx.MockTransport(litellm))
    return AI("http://litellm", "key", "embed", "remedy", http=http)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        match_threshold=0.8,
        review_threshold=0.5,
        mailbox_url="http://mailbox",
        mailbox_key="key",
    )


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("WALLS_TEST_DATABASE_URL")
    if not url:
        pytest.fail("set WALLS_TEST_DATABASE_URL to a Postgres with pgvector")
    return url


@pytest.fixture
async def db(database_url: str) -> Database:
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as conn:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION vector")
    database = Database(database_url)
    await migrate(database)
    return database
