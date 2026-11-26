from dataclasses import replace

from walls import tokens
from walls.server import prepare


async def stored_hashes(db) -> list[str]:
    async with db.connect() as conn:
        cur = await conn.execute("SELECT hash FROM server_tokens")
        return [row["hash"] for row in await cur.fetchall()]


async def test_first_start_prints_a_token_once_and_stores_only_its_hash(db, settings, capsys):
    check = await prepare(db, settings)
    printed = capsys.readouterr().out
    made = printed.split("bearer token:\n\n    ")[1].split("\n")[0]
    assert await check(made)
    assert made not in await stored_hashes(db)

    again = await prepare(db, settings)
    assert capsys.readouterr().out == ""
    assert await again(made)
    assert not await again("guess")


async def test_rotating_replaces_the_token(db, settings):
    first = await tokens.ensure(db)
    second = await tokens.rotate(db)
    check = tokens.checker(db, settings)
    assert not await check(first)
    assert await check(second)


async def test_a_configured_token_wins_and_nothing_is_stored(db, settings):
    check = await prepare(db, replace(settings, auth_token="from-config"))
    assert await check("from-config")
    assert not await check("other")
    assert await stored_hashes(db) == []


async def test_auth_off_means_no_check(db, settings):
    assert await prepare(db, replace(settings, auth=False)) is None
    assert await stored_hashes(db) == []
