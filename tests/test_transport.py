"""The real HTTP transport, because three things exist only there: the client's name, the MCP
session id, the Host check, and the bearer token."""

import asyncio
import socket
from dataclasses import replace

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import Implementation

from walls.server import build_server, http_app

TOKEN = "test-token-0123456789"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def start(db, ai, settings):
    port = free_port()
    app = http_app(build_server(db, ai, settings), settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task, f"http://127.0.0.1:{port}"


@pytest.fixture
async def url(db, ai, settings):
    hosts = ["walls.example.com", "127.0.0.1:*"]
    server, task, address = await start(db, ai, replace(settings, allowed_hosts=hosts))
    yield address
    server.should_exit = True
    await task


@pytest.fixture
async def guarded_url(db, ai, settings):
    server, task, address = await start(db, ai, replace(settings, auth_token=TOKEN))
    yield address
    server.should_exit = True
    await task


async def report(
    url: str, client_name: str, times: int = 1, headers: dict[str, str] | None = None
) -> list:
    results = []
    http = create_mcp_http_client(headers=headers)
    async with http, streamable_http_client(f"{url}/mcp", http_client=http) as streams:
        info = Implementation(name=client_name, version="1")
        async with ClientSession(streams[0], streams[1], client_info=info) as session:
            await session.initialize()
            for _ in range(times):
                result = await session.call_tool(
                    "wall_report",
                    {
                        "action_needed": "Rotate the Vault token",
                        "system": "vault",
                        "blocker_type": "credential",
                        "why_blocked": "Token is dead",
                    },
                )
                results.append(result.structured_content)
    return results


async def stored_callers(db) -> list[tuple]:
    async with db.connect() as conn:
        cur = await conn.execute("SELECT client, session_id FROM occurrences ORDER BY id")
        return [(row["client"], row["session_id"]) for row in await cur.fetchall()]


async def test_client_and_session_are_recorded(url, db):
    await report(url, "claude-code")
    await report(url, "codex-mcp-client")

    rows = await stored_callers(db)
    assert [client for client, _ in rows] == ["claude", "codex"]
    assert rows[0][1] and rows[1][1] and rows[0][1] != rows[1][1]


async def test_a_repeat_in_one_session_counts_once(url, db):
    first, second = await report(url, "claude-code", times=2)

    assert first["status"] == "logged"
    assert second["status"] == "already_logged"
    assert len(await stored_callers(db)) == 1


async def test_healthz_and_host_check(url):
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"{url}/healthz")).json() == {"status": "ok"}
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        accept = {"Accept": "application/json, text/event-stream"}
        wrong = await http.post(
            f"{url}/mcp", json=initialize, headers={**accept, "Host": "evil.example"}
        )
        right = await http.post(
            f"{url}/mcp", json=initialize, headers={**accept, "Host": "walls.example.com"}
        )
    assert wrong.status_code == 421
    assert right.status_code != 421


PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
ACCEPT = {"Accept": "application/json, text/event-stream"}


async def test_a_token_is_required_when_one_is_set(guarded_url):
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"{guarded_url}/healthz")).status_code == 200
        missing = await http.post(f"{guarded_url}/mcp", json=PING, headers=ACCEPT)
        wrong = await http.post(
            f"{guarded_url}/mcp", json=PING, headers={**ACCEPT, "Authorization": "Bearer nope"}
        )
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401


async def test_a_client_with_the_token_gets_through(guarded_url, db):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    (result,) = await report(guarded_url, "claude-code", headers=headers)
    assert result["status"] == "logged"


async def test_no_token_means_no_check(url):
    async with httpx.AsyncClient() as http:
        response = await http.post(f"{url}/mcp", json=PING, headers=ACCEPT)
    assert response.status_code != 401
