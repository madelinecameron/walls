import argparse
import asyncio
import json
import logging
import secrets
from pathlib import Path

from . import digest, install
from .config import DEFAULT_CONFIG, ConfigError, Settings
from .db import Database, migrate
from .server import serve

CONFIG_TEMPLATE = """\
# walls configuration. Every setting can also come from WALLS_<NAME> in the environment,
# which wins over this file: e.g. WALLS_DATABASE_URL, WALLS_AUTH_TOKEN, WALLS_LLM_KEY.

# Postgres with the pgvector extension.
database_url = "postgresql://walls:walls@localhost:5432/walls"

[server]
host = "127.0.0.1"
port = 8797
# The URL agents use to reach this server; `walls install` hands it to harnesses.
# public_url = "https://walls.example.com/mcp"
# Host headers to accept. Add your public name; "*" turns the check off.
allowed_hosts = ["127.0.0.1:*", "localhost:*"]
# Agents must send "Authorization: Bearer <auth_token>". Remove it to allow anyone.
@AUTH@

[llm]
# Any OpenAI-compatible API: OpenAI, LiteLLM, Ollama, vLLM.
url = "https://api.openai.com/v1"
# key = "..."
# Must return 1536-dimensional vectors.
embedding_model = "text-embedding-3-small"
remedy_model = "gpt-4.1-mini"

[matching]
match_threshold = 0.72
review_threshold = 0.65

[digest]
# Optional. Set webhook_url to POST a weekly digest as JSON. The token is sent as
# "Authorization: Bearer <webhook_token>", and `to` is passed through as ["<to>"].
# webhook_url = "https://hooks.example.com/walls"
# webhook_token = "..."
# to = "you@example.com"
timezone = "UTC"
top = 5
window_days = 30
remedy_min_occurrences = 3
remedy_limit = 10
"""


async def _preview(settings: Settings) -> None:
    db = Database(settings.database_url)
    body = digest.compose(await digest.collect(db, settings, settings.now()))
    print(json.dumps(body, indent=2))


def _init(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise ConfigError(f"{path} exists; pass --force to overwrite it")
    generated = json.dumps(secrets.token_urlsafe(32))
    path.write_text(CONFIG_TEMPLATE.replace("@AUTH@", f"auth_token = {generated}"))
    path.chmod(0o600)
    print(f"Wrote {path} with a new auth token. Fill in database_url and [llm].")


def _install(args: argparse.Namespace, settings: Settings) -> None:
    bearer = settings.auth_token if args.token is None else args.token
    target = install.Target(
        args.url or settings.mcp_url, bearer, args.scope, Path.home(), Path.cwd()
    )
    if args.harness == "print":
        print(install.describe(target))
        return
    for line in install.HARNESSES[args.harness](target):
        print(line)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="walls")
    root.add_argument(
        "--config", type=Path, help="config file (default: $WALLS_CONFIG or ./walls.toml)"
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the MCP server")
    commands.add_parser("migrate", help="apply database migrations")
    commands.add_parser("digest-preview", help="print this week's digest without sending it")

    init = commands.add_parser("init", help="write a starter config file with a new auth token")
    init.add_argument("path", nargs="?", type=Path, default=DEFAULT_CONFIG)
    init.add_argument("--force", action="store_true", help="overwrite an existing file")

    add = commands.add_parser(
        "install", help="connect an agent harness to this server, or print how to"
    )
    add.add_argument("harness", choices=[*install.HARNESSES, "print"])
    add.add_argument("--url", help="the server's MCP URL (default: public_url from config)")
    add.add_argument(
        "--token",
        help="the bearer token (default: auth_token from config)",
    )
    add.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="user: every session on this machine; project: only this directory",
    )
    return root


def main() -> None:
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "init":
        _init(args.path, args.force)
        return
    settings = Settings.load(args.config)
    if args.command == "serve":
        serve(settings)
    elif args.command == "install":
        _install(args, settings)
    else:
        settings.require("database_url")
        if args.command == "migrate":
            print(asyncio.run(migrate(Database(settings.database_url))))
        else:
            asyncio.run(_preview(settings))
