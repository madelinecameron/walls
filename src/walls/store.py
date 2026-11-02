"""Reports become occurrences of a wall. Every occurrence is kept: a wall is unique, and how
often it comes back is the signal the ranking is built on."""

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from pgvector import Vector
from psycopg.types.json import Jsonb

from .ai import AI, AIError
from .config import Settings
from .db import Connection, Database
from .scrub import scrub

logger = logging.getLogger(__name__)

JUDGMENT = "human_judgment"
REPEAT_WINDOW = timedelta(hours=6)
SWEEP_BATCH = 50
LOCK = "SELECT pg_advisory_xact_lock(hashtext('walls.place'))"


class WallError(Exception):
    """A request that cannot be done, in words the calling agent can act on."""


@dataclass(frozen=True)
class Report:
    action_needed: str
    system: str
    blocker_type: str
    why_blocked: str
    suggested_fix: str | None = None
    repo: str | None = None
    client: str | None = None
    machine: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class Placement:
    wall_id: int
    is_new: bool
    reopened: bool
    score: float | None


def _clean(report: Report) -> Report:
    return replace(
        report,
        action_needed=scrub(report.action_needed.strip()),
        system=scrub(report.system.strip().lower()),
        why_blocked=scrub(report.why_blocked.strip()),
        suggested_fix=scrub(report.suggested_fix.strip()) if report.suggested_fix else None,
    )


def embedding_text(report: Report) -> str:
    return f"{report.system}: {report.action_needed}"


async def _place(
    conn: Connection, report: Report, embedding: list[float], settings: Settings, now: datetime
) -> Placement:
    """Find the wall this report belongs to, or make one. Holds a lock until commit, so two
    reports of the same new wall arriving together cannot each create it.

    Only a wall on the same system attaches automatically. Across systems, similarity cannot
    tell a duplicate from a look-alike (GitHub and GitLab approvals score like paraphrases), so
    those go to review. A merged wall still matches, and its report goes to the merge target,
    so the same wording does not come back as a new wall after a merge."""
    await conn.execute(LOCK)
    vector = Vector(embedding)
    params = {
        "v": vector,
        "system": report.system,
        "judgment": JUDGMENT,
        "is_judgment": report.blocker_type == JUDGMENT,
    }
    cur = await conn.execute(
        """
        SELECT id, merged_into_id, 1 - (embedding <=> %(v)s) AS score
          FROM walls
         WHERE system = %(system)s
           AND (blocker_type = %(judgment)s) = %(is_judgment)s
         ORDER BY embedding <=> %(v)s
         LIMIT 1
        """,
        params,
    )
    same_system = await cur.fetchone()
    if same_system and same_system["score"] >= settings.match_threshold:
        return await _attach(conn, same_system)

    cur = await conn.execute(
        """
        SELECT id, 1 - (embedding <=> %(v)s) AS score
          FROM walls
         WHERE merged_into_id IS NULL
           AND (blocker_type = %(judgment)s) = %(is_judgment)s
         ORDER BY embedding <=> %(v)s
         LIMIT 3
        """,
        params,
    )
    nearest = await cur.fetchall()
    cur = await conn.execute(
        """
        INSERT INTO walls (action_needed, system, blocker_type, embedding, created_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (report.action_needed, report.system, report.blocker_type, vector, now),
    )
    wall_id = (await cur.fetchone())["id"]
    for row in nearest:
        if row["score"] >= settings.review_threshold:
            await conn.execute(
                "INSERT INTO merge_candidates (wall_id, similar_id, score) VALUES (%s, %s, %s)",
                (wall_id, row["id"], row["score"]),
            )
    return Placement(wall_id, True, False, nearest[0]["score"] if nearest else None)


async def _attach(conn: Connection, match: dict[str, Any]) -> Placement:
    wall_id = match["id"]
    target = match["merged_into_id"]
    while target is not None:
        wall_id = target
        cur = await conn.execute("SELECT merged_into_id FROM walls WHERE id = %s", (wall_id,))
        target = (await cur.fetchone())["merged_into_id"]
    cur = await conn.execute(
        """
        UPDATE walls SET status = 'open', reopened_count = reopened_count + 1
         WHERE id = %s AND status = 'resolved'
        RETURNING id
        """,
        (wall_id,),
    )
    reopened = await cur.fetchone() is not None
    return Placement(wall_id, False, reopened, match["score"])


async def _insert_occurrence(
    conn: Connection, report: Report, placement: Placement | None, now: datetime
) -> None:
    await conn.execute(
        """
        INSERT INTO occurrences (wall_id, action_needed, system, blocker_type, why_blocked,
                                 suggested_fix, repo, client, machine, session_id,
                                 match_score, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            placement.wall_id if placement else None,
            report.action_needed,
            report.system,
            report.blocker_type,
            report.why_blocked,
            report.suggested_fix,
            report.repo,
            report.client,
            report.machine,
            report.session_id,
            placement.score if placement else None,
            now,
        ),
    )


async def _times_seen(conn: Connection, wall_id: int) -> int:
    cur = await conn.execute("SELECT count(*) AS n FROM occurrences WHERE wall_id = %s", (wall_id,))
    return (await cur.fetchone())["n"]


async def report_wall(
    db: Database, ai: AI, settings: Settings, report: Report, now: datetime
) -> dict[str, Any]:
    """Record a report. A failed embedding stores it unplaced for the sweep, so an agent that is
    already blocked is never held up or refused by this log."""
    report = _clean(report)
    try:
        embedding = await ai.embed(embedding_text(report))
    except AIError as err:
        logger.warning("embedding failed, queueing the report: %s", err)
        async with db.connect() as conn:
            await _insert_occurrence(conn, report, None, now)
        return {"status": "queued", "note": "Logged; it will be matched to a wall shortly."}

    async with db.connect() as conn:
        placement = await _place(conn, report, embedding, settings, now)
        if report.session_id and not placement.is_new:
            cur = await conn.execute(
                """
                SELECT 1 FROM occurrences
                 WHERE wall_id = %s AND session_id = %s AND created_at >= %s
                 LIMIT 1
                """,
                (placement.wall_id, report.session_id, now - REPEAT_WINDOW),
            )
            if await cur.fetchone():
                return {
                    "status": "already_logged",
                    "wall_id": placement.wall_id,
                    "times_seen": await _times_seen(conn, placement.wall_id),
                }
        await _insert_occurrence(conn, report, placement, now)
        return {
            "status": "logged",
            "wall_id": placement.wall_id,
            "new_wall": placement.is_new,
            "reopened": placement.reopened,
            "times_seen": await _times_seen(conn, placement.wall_id),
        }


async def sweep(db: Database, ai: AI, settings: Settings, now: datetime) -> int:
    """Place reports stored while embeddings were down. Stops at the first failure: the provider
    is still down, and the rest can wait for the next run."""
    async with db.connect() as conn:
        cur = await conn.execute(
            "SELECT * FROM occurrences WHERE wall_id IS NULL ORDER BY id LIMIT %s", (SWEEP_BATCH,)
        )
        rows = await cur.fetchall()
    placed = 0
    for row in rows:
        report = Report(
            row["action_needed"], row["system"], row["blocker_type"], row["why_blocked"]
        )
        try:
            embedding = await ai.embed(embedding_text(report))
        except AIError as err:
            logger.warning("sweep stopped: %s", err)
            break
        async with db.connect() as conn:
            placement = await _place(conn, report, embedding, settings, now)
            await conn.execute(
                "UPDATE occurrences SET wall_id = %s, match_score = %s WHERE id = %s",
                (placement.wall_id, placement.score, row["id"]),
            )
        placed += 1
    return placed


async def top_walls(
    conn: Connection,
    now: datetime,
    window_days: int = 30,
    limit: int = 10,
    status: str = "open",
    blocker_type: str | None = None,
    system: str | None = None,
) -> list[dict[str, Any]]:
    """Walls ranked by occurrences in the window. human_judgment is left out unless asked for by
    name: a question for the human is not friction to remove."""
    cur = await conn.execute(
        """
        SELECT w.id, w.action_needed, w.system, w.blocker_type, w.status, w.resolution,
               w.reopened_count, w.remedy, w.remedy_count,
               count(*) FILTER (WHERE o.created_at >= %(since)s) AS recent,
               count(*) AS total,
               min(o.created_at) AS first_seen,
               max(o.created_at) AS last_seen,
               coalesce(array_agg(DISTINCT o.repo) FILTER (WHERE o.repo IS NOT NULL), '{}') AS repos,
               coalesce(array_agg(DISTINCT o.client) FILTER (WHERE o.client IS NOT NULL), '{}')
                   AS clients,
               coalesce(array_agg(DISTINCT o.why_blocked), '{}') AS reasons,
               coalesce(array_agg(DISTINCT o.suggested_fix)
                   FILTER (WHERE o.suggested_fix IS NOT NULL), '{}') AS suggested_fixes
          FROM walls w
          JOIN occurrences o ON o.wall_id = w.id
         WHERE w.merged_into_id IS NULL
           AND w.status = %(status)s
           AND (%(type)s::text IS NULL OR w.blocker_type = %(type)s)
           AND (%(type)s::text IS NOT NULL OR w.blocker_type <> %(judgment)s)
           AND (%(system)s::text IS NULL OR w.system = %(system)s)
         GROUP BY w.id
        HAVING count(*) FILTER (WHERE o.created_at >= %(since)s) > 0
         ORDER BY recent DESC, last_seen DESC, w.id
         LIMIT %(limit)s
        """,
        {
            "since": now - timedelta(days=window_days),
            "status": status,
            "type": blocker_type,
            "judgment": JUDGMENT,
            "system": system.strip().lower() if system else None,
            "limit": limit,
        },
    )
    return [_listed(row) for row in await cur.fetchall()]


def _listed(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["reasons"] = row["reasons"][:3]
    out["suggested_fixes"] = row["suggested_fixes"][:3]
    for key in ("first_seen", "last_seen"):
        out[key] = row[key].isoformat()
    return out


async def _live_wall(conn: Connection, wall_id: int) -> dict[str, Any]:
    cur = await conn.execute(
        "SELECT id, status, merged_into_id FROM walls WHERE id = %s FOR UPDATE", (wall_id,)
    )
    wall = await cur.fetchone()
    if wall is None:
        raise WallError(f"no wall #{wall_id}")
    if wall["merged_into_id"] is not None:
        raise WallError(f"wall #{wall_id} was merged into #{wall['merged_into_id']}; use that one")
    return wall


async def resolve_wall(conn: Connection, wall_id: int, resolution: str, now: datetime) -> dict:
    await _live_wall(conn, wall_id)
    await conn.execute(
        """
        UPDATE walls SET status = 'resolved', resolution = %s, resolved_at = %s WHERE id = %s
        """,
        (scrub(resolution.strip()), now, wall_id),
    )
    return {"wall_id": wall_id, "status": "resolved"}


async def pending_reviews(conn: Connection, limit: int = 20) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT a.id AS wall_id, a.system AS wall_system, a.action_needed AS wall_action,
               b.id AS similar_id, b.system AS similar_system, b.action_needed AS similar_action,
               c.score
          FROM merge_candidates c
          JOIN walls a ON a.id = c.wall_id
          JOIN walls b ON b.id = c.similar_id
         WHERE c.status = 'pending'
           AND a.merged_into_id IS NULL
           AND b.merged_into_id IS NULL
         ORDER BY c.score DESC, c.id
         LIMIT %s
        """,
        (limit,),
    )
    return list(await cur.fetchall())


async def merge_walls(conn: Connection, wall_id: int, into_id: int, now: datetime) -> dict:
    if wall_id == into_id:
        raise WallError("a wall cannot be merged into itself")
    await conn.execute(LOCK)
    await _live_wall(conn, wall_id)
    await _live_wall(conn, into_id)
    await conn.execute("UPDATE occurrences SET wall_id = %s WHERE wall_id = %s", (into_id, wall_id))
    await conn.execute("UPDATE walls SET merged_into_id = %s WHERE id = %s", (into_id, wall_id))
    await conn.execute(
        """
        UPDATE merge_candidates SET status = 'merged', decided_at = %s
         WHERE status = 'pending' AND (wall_id = %s OR similar_id = %s)
        """,
        (now, wall_id, wall_id),
    )
    return {
        "wall_id": into_id,
        "merged": wall_id,
        "times_seen": await _times_seen(conn, into_id),
    }


async def keep_separate(conn: Connection, wall_id: int, similar_id: int, now: datetime) -> dict:
    cur = await conn.execute(
        """
        UPDATE merge_candidates SET status = 'kept_separate', decided_at = %s
         WHERE status = 'pending'
           AND ((wall_id = %s AND similar_id = %s) OR (wall_id = %s AND similar_id = %s))
        RETURNING id
        """,
        (now, wall_id, similar_id, similar_id, wall_id),
    )
    if await cur.fetchone() is None:
        raise WallError(f"no pending review between #{wall_id} and #{similar_id}")
    return {"wall_id": wall_id, "similar_id": similar_id, "status": "kept_separate"}


async def save_remedy(conn: Connection, wall_id: int, remedy: dict, seen: int) -> None:
    await conn.execute(
        "UPDATE walls SET remedy = %s, remedy_count = %s WHERE id = %s",
        (Jsonb(remedy), seen, wall_id),
    )
