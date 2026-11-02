"""The real HTTP transport, because three things exist only there: the client's name, the MCP
session id, and the Host check that decides whether the tailnet name is accepted."""

import asyncio
import socket

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Implementation

from walls.server import build_server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def url(db, ai, settings):
    port = free_port()
    app = build_server(db, ai, settings).streamable_http_app(
        transport_security=TransportSecuritySettings(
            allowed_hosts=["walls.example.ts.net", "127.0.0.1:*"]
        )
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


async def report(url: str, client_name: str, times: int = 1) -> list:
    results = []
    async with streamable_http_client(f"{url}/mcp") as streams:
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
            f"{url}/mcp", json=initialize, headers={**accept, "Host": "walls.example.ts.net"}
        )
    assert wrong.status_code == 421
    assert right.status_code != 421
