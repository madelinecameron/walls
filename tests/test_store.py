import asyncio
from datetime import timedelta

import pytest

from tests.conftest import NOW
from walls import store
from walls.store import Report, WallError


def vault(**overrides) -> Report:
    fields = {
        "action_needed": "Rotate the Vault token",
        "system": "vault",
        "blocker_type": "credential",
        "why_blocked": "Token is dead",
    }
    return Report(**{**fields, **overrides})


async def occurrences(db):
    async with db.connect() as conn:
        cur = await conn.execute("SELECT * FROM occurrences ORDER BY id")
        return await cur.fetchall()


async def wall(db, wall_id):
    async with db.connect() as conn:
        cur = await conn.execute("SELECT * FROM walls WHERE id = %s", (wall_id,))
        return await cur.fetchone()


async def test_same_wall_twice_is_one_wall_two_occurrences(db, ai, settings):
    first = await store.report_wall(db, ai, settings, vault(), NOW)
    second = await store.report_wall(db, ai, settings, vault(why_blocked="Expired again"), NOW)

    assert first["new_wall"] is True and first["times_seen"] == 1
    assert second["new_wall"] is False and second["times_seen"] == 2
    assert second["wall_id"] == first["wall_id"]
    assert len(await occurrences(db)) == 2


async def test_unrelated_report_is_a_new_wall_with_no_review(db, ai, settings):
    first = await store.report_wall(db, ai, settings, vault(), NOW)
    other = vault(action_needed="Press the reset button on the printer", system="printnode")
    second = await store.report_wall(db, ai, settings, other, NOW)

    assert second["wall_id"] != first["wall_id"]
    async with db.connect() as conn:
        assert await store.pending_reviews(conn) == []


async def test_similar_report_makes_a_new_wall_and_a_review(db, ai, settings):
    first = await store.report_wall(db, ai, settings, vault(), NOW)
    near = vault(action_needed="Rotate the Vault token and restart core services")
    second = await store.report_wall(db, ai, settings, near, NOW)

    assert second["new_wall"] is True
    async with db.connect() as conn:
        reviews = await store.pending_reviews(conn)
    assert [(r["wall_id"], r["similar_id"]) for r in reviews] == [
        (second["wall_id"], first["wall_id"])
    ]


async def test_merge_moves_occurrences_and_closes_the_review(db, ai, settings):
    first = await store.report_wall(db, ai, settings, vault(), NOW)
    near = vault(action_needed="Rotate the Vault token and restart core services")
    second = await store.report_wall(db, ai, settings, near, NOW)

    async with db.connect() as conn:
        merged = await store.merge_walls(conn, second["wall_id"], first["wall_id"], NOW)
        assert await store.pending_reviews(conn) == []
        assert merged["times_seen"] == 2
        top = await store.top_walls(conn, NOW)
    assert [w["id"] for w in top] == [first["wall_id"]]

    again = await store.report_wall(db, ai, settings, near, NOW)
    assert again["wall_id"] != second["wall_id"]


async def test_merged_wall_cannot_be_used_again(db, ai, settings):
    first = await store.report_wall(db, ai, settings, vault(), NOW)
    other = vault(action_needed="Press the reset button", system="printer")
    second = await store.report_wall(db, ai, settings, other, NOW)

    async with db.connect() as conn:
        await store.merge_walls(conn, second["wall_id"], first["wall_id"], NOW)
    with pytest.raises(WallError, match="was merged into"):
        async with db.connect() as conn:
            await store.resolve_wall(conn, second["wall_id"], "done", NOW)
    with pytest.raises(WallError, match="itself"):
        async with db.connect() as conn:
            await store.merge_walls(conn, first["wall_id"], first["wall_id"], NOW)


async def test_keep_separate_closes_the_review_either_way_round(db, ai, settings):
    first = await store.report_wall(db, ai, settings, vault(), NOW)
    near = vault(action_needed="Rotate the Vault token and restart core services")
    second = await store.report_wall(db, ai, settings, near, NOW)

    async with db.connect() as conn:
        await store.keep_separate(conn, first["wall_id"], second["wall_id"], NOW)
        assert await store.pending_reviews(conn) == []
    with pytest.raises(WallError, match="no pending review"):
        async with db.connect() as conn:
            await store.keep_separate(conn, first["wall_id"], second["wall_id"], NOW)


async def test_report_of_a_resolved_wall_reopens_it(db, ai, settings):
    first = await store.report_wall(db, ai, settings, vault(), NOW)
    async with db.connect() as conn:
        await store.resolve_wall(conn, first["wall_id"], "Added a renewal timer", NOW)

    again = await store.report_wall(db, ai, settings, vault(), NOW)

    assert again["wall_id"] == first["wall_id"] and again["reopened"] is True
    row = await wall(db, first["wall_id"])
    assert row["status"] == "open" and row["reopened_count"] == 1


async def test_same_session_repeat_is_not_counted_until_the_window_passes(db, ai, settings):
    report = vault(session_id="s1")
    await store.report_wall(db, ai, settings, report, NOW)

    repeat = await store.report_wall(db, ai, settings, report, NOW + timedelta(minutes=5))
    assert repeat["status"] == "already_logged" and repeat["times_seen"] == 1

    other = await store.report_wall(db, ai, settings, vault(session_id="s2"), NOW)
    assert other["times_seen"] == 2

    later = await store.report_wall(db, ai, settings, report, NOW + store.REPEAT_WINDOW * 2)
    assert later["status"] == "logged" and later["times_seen"] == 3


async def test_judgment_questions_stay_apart_from_action_walls(db, ai, settings):
    action = await store.report_wall(db, ai, settings, vault(), NOW)
    question = await store.report_wall(db, ai, settings, vault(blocker_type="human_judgment"), NOW)

    assert question["wall_id"] != action["wall_id"]
    async with db.connect() as conn:
        assert await store.pending_reviews(conn) == []
        default = await store.top_walls(conn, NOW)
        asked = await store.top_walls(conn, NOW, blocker_type="human_judgment")
    assert [w["id"] for w in default] == [action["wall_id"]]
    assert [w["id"] for w in asked] == [question["wall_id"]]


async def test_embedding_outage_queues_the_report_and_the_sweep_places_it(
    db, ai, settings, litellm
):
    existing = await store.report_wall(db, ai, settings, vault(), NOW)

    litellm.embeddings_down = True
    queued = await store.report_wall(db, ai, settings, vault(), NOW)
    assert queued["status"] == "queued"
    assert await store.sweep(db, ai, settings, NOW) == 0
    assert (await occurrences(db))[1]["wall_id"] is None

    litellm.embeddings_down = False
    assert await store.sweep(db, ai, settings, NOW) == 1
    assert (await occurrences(db))[1]["wall_id"] == existing["wall_id"]


async def test_secrets_never_reach_the_provider_or_the_database(db, ai, settings, litellm):
    token = "ghp_abcdefghijklmnopqrstuvwxyz012345"
    await store.report_wall(
        db,
        ai,
        settings,
        vault(
            action_needed=f"Replace {token} in the runner",
            why_blocked=f"password={token}",
            suggested_fix=f"Use {token} from Vault",
        ),
        NOW,
    )

    assert token not in " ".join(litellm.embed_inputs)
    row = (await occurrences(db))[0]
    for field in ("action_needed", "why_blocked", "suggested_fix"):
        assert token not in row[field]
    assert token not in (await wall(db, row["wall_id"]))["action_needed"]


async def test_concurrent_reports_of_a_new_wall_make_one_wall(db, ai, settings):
    results = await asyncio.gather(
        *(store.report_wall(db, ai, settings, vault(), NOW) for _ in range(8))
    )

    assert len({r["wall_id"] for r in results}) == 1
    assert sum(r["new_wall"] for r in results) == 1


async def test_ranking_counts_the_window_and_filters(db, ai, settings):
    old = vault(action_needed="Approve the deploy in the dashboard", system="render")
    await store.report_wall(db, ai, settings, old, NOW - timedelta(days=60))
    for _ in range(2):
        await store.report_wall(db, ai, settings, vault(), NOW)
    other = vault(action_needed="Press the reset button", system="printer")
    await store.report_wall(db, ai, settings, other, NOW)

    async with db.connect() as conn:
        top = await store.top_walls(conn, NOW)
        only = await store.top_walls(conn, NOW, system="Printer")
        wide = await store.top_walls(conn, NOW, window_days=90)

    assert [w["system"] for w in top] == ["vault", "printer"]
    assert top[0]["recent"] == 2
    assert [w["system"] for w in only] == ["printer"]
    assert len(wide) == 3
