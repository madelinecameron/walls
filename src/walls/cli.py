import argparse
import asyncio
import json
import logging

from . import digest
from .config import Settings
from .db import Database, migrate
from .server import serve


async def _preview(settings: Settings) -> None:
    db = Database(settings.database_url)
    body = digest.compose(await digest.collect(db, settings, settings.now()))
    print(json.dumps(body, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="walls")
    parser.add_argument(
        "command",
        choices=["serve", "migrate", "digest-preview"],
        help="serve the MCP, apply migrations, or print this week's digest without sending it",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    if args.command == "serve":
        serve(settings)
        return
    settings.require("database_url")
    if args.command == "migrate":
        print(asyncio.run(migrate(Database(settings.database_url))))
    else:
        asyncio.run(_preview(settings))
