import asyncio
import hmac
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from . import digest, store
from .ai import AI
from .config import Settings
from .db import Database, migrate
from .store import Report, WallError

logger = logging.getLogger(__name__)

TICK_SECONDS = 300

BlockerType = Literal[
    "permission", "credential", "no_tool", "physical_or_ui", "human_judgment", "network"
]
WallId = Annotated[int, Field(ge=1)]

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

INSTRUCTIONS = """\
A log of walls: every time an agent has to ask the human to do something by hand.

When you must ask the human to act, call wall_report, then tell them in chat as usual. The log
never replaces telling them. Never wait on it and never retry it; if it fails, carry on.

Report: an action only the human could take, such as approving a permission, supplying a
credential, clicking through a web UI or touching hardware. Use blocker_type human_judgment when
you are asking for a decision rather than an action. Do not report ordinary clarifying questions
about the task, or actions you could have done yourself.

Write action_needed as the general action, so the same wall matches next time: "Rotate the Vault
token", not "Please run vault token create on the server for me". Never include secret values.

Use wall_merge, wall_keep_separate and wall_resolve only when the human asks you to, or when you
have just built the fix they asked for.
"""


def _client_name(ctx: Context) -> str | None:
    params = ctx.session.client_params
    if params is None:
        return None
    name = params.client_info.name.lower()
    for known in ("claude", "codex"):
        if known in name:
            return known
    return name[:100]


def _caller(ctx: Context | None) -> tuple[str | None, str | None]:
    """The client name and MCP session id. Absent when a tool is called outside a session."""
    if ctx is None:
        return None, None
    try:
        headers = ctx.headers or {}
        return _client_name(ctx), headers.get("mcp-session-id")
    except (ValueError, AttributeError):
        return None, None


def build_server(db: Database, ai: AI, settings: Settings, lifespan: Any = None) -> MCPServer:
    mcp = MCPServer("walls", instructions=INSTRUCTIONS, lifespan=lifespan)

    @mcp.tool(annotations=WRITE)
    async def wall_report(
        action_needed: Annotated[str, Field(min_length=3, max_length=500)],
        system: Annotated[str, Field(min_length=1, max_length=100)],
        blocker_type: BlockerType,
        why_blocked: Annotated[str, Field(min_length=3, max_length=1000)],
        suggested_fix: Annotated[str | None, Field(max_length=1000)] = None,
        repo: Annotated[str | None, Field(max_length=200)] = None,
        machine: Annotated[str | None, Field(max_length=100)] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Log that you need the human to do something by hand. Call this, then tell them in
        chat as usual.

        action_needed: the general action the human must take, e.g. "Rotate the Vault token".
        system: the product or service involved, one lowercase word if possible, e.g. "vault".
        blocker_type: permission (you lack access), credential (a secret is missing or dead),
        no_tool (no tool can do it), physical_or_ui (needs hardware or a web UI), network (cannot
        reach it), human_judgment (a decision for the human, not an action).
        why_blocked: why you could not do it yourself.
        suggested_fix: the tool or permission that would have removed this wall, if you can tell.
        repo: the repository you are working in. machine: this host's name.
        """
        client, session_id = _caller(ctx)
        report = Report(
            action_needed=action_needed,
            system=system,
            blocker_type=blocker_type,
            why_blocked=why_blocked,
            suggested_fix=suggested_fix,
            repo=repo,
            client=client,
            machine=machine,
            session_id=session_id,
        )
        return await store.report_wall(db, ai, settings, report, settings.now())

    @mcp.tool(annotations=READ_ONLY)
    async def walls_top(
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        days: Annotated[int, Field(ge=1, le=365)] = 30,
        status: Literal["open", "resolved"] = "open",
        blocker_type: BlockerType | None = None,
        system: str | None = None,
    ) -> list[dict[str, Any]]:
        """Walls ranked by how often they were hit in the last `days`, with the fixes suggested
        for each. human_judgment walls appear only if you ask for that blocker_type."""
        async with db.connect() as conn:
            return await store.top_walls(
                conn, settings.now(), days, limit, status, blocker_type, system
            )

    @mcp.tool(annotations=WRITE)
    async def wall_resolve(
        wall_id: WallId, resolution: Annotated[str, Field(min_length=3, max_length=1000)]
    ) -> dict[str, Any]:
        """Mark a wall fixed and say what fixed it. If it is reported again it reopens."""
        return await _guarded(store.resolve_wall, wall_id, resolution, settings.now())

    @mcp.tool(annotations=READ_ONLY)
    async def walls_review(
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> list[dict[str, Any]]:
        """Pairs of walls too similar to call different and not similar enough to merge alone."""
        async with db.connect() as conn:
            return await store.pending_reviews(conn, limit)

    @mcp.tool(annotations=WRITE)
    async def wall_merge(wall_id: WallId, into_wall_id: WallId) -> dict[str, Any]:
        """Merge a duplicate wall into another. Its occurrences move across."""
        return await _guarded(store.merge_walls, wall_id, into_wall_id, settings.now())

    @mcp.tool(annotations=WRITE)
    async def wall_keep_separate(wall_id: WallId, similar_id: WallId) -> dict[str, Any]:
        """Settle a walls_review pair as two different walls."""
        return await _guarded(store.keep_separate, wall_id, similar_id, settings.now())

    async def _guarded(action: Any, *args: Any) -> dict[str, Any]:
        try:
            async with db.connect() as conn:
                return await action(conn, *args)
        except WallError as err:
            raise ToolError(str(err)) from err

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> Response:
        try:
            async with db.connect() as conn:
                await conn.execute("SELECT 1")
        except Exception:
            logger.exception("health check failed")
            return JSONResponse({"status": "database unreachable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return mcp


class BearerAuth:
    """Requires `Authorization: Bearer <token>` on every HTTP request except the health check."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                response = JSONResponse(
                    {"error": "missing or wrong bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, settings: Settings) -> ASGIApp:
    hosts = settings.allowed_hosts
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection="*" not in hosts, allowed_hosts=hosts
    )
    app = server.streamable_http_app(transport_security=security, host=settings.host)
    return BearerAuth(app, settings.auth_token) if settings.auth_token else app


async def tick_loop(db: Database, ai: AI, settings: Settings, http: httpx.AsyncClient) -> None:
    """Places reports queued while embeddings were down, and sends the weekly digest when due."""
    while True:
        try:
            now = settings.now()
            placed = await store.sweep(db, ai, settings, now)
            if placed:
                logger.info("placed %d queued reports", placed)
            if settings.digest_enabled:
                mail_id = await digest.run_if_due(db, ai, settings, http, now)
                if mail_id:
                    logger.info("sent digest %s", mail_id)
        except Exception:
            logger.exception("background tick failed")
        await asyncio.sleep(TICK_SECONDS)


def serve(settings: Settings) -> None:
    settings.require("database_url", "llm_url")
    settings.check_digest()
    if not settings.auth_token:
        logger.warning("no auth_token set: anyone who can reach this server can use it")
    if not settings.digest_enabled:
        logger.info("no Mailbox configured: the weekly digest is off")
    db = Database(settings.database_url)
    ai = AI(settings.llm_url, settings.llm_key, settings.embedding_model, settings.remedy_model)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncGenerator[None]:
        await migrate(db)
        http = httpx.AsyncClient()
        task = asyncio.create_task(tick_loop(db, ai, settings, http))
        try:
            yield
        finally:
            task.cancel()
            await http.aclose()
            await ai.aclose()

    app = http_app(build_server(db, ai, settings, lifespan=lifespan), settings)
    uvicorn.run(app, host=settings.host, port=settings.port)
