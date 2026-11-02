"""The weekly DIGEST. A script ranks the walls; a model only writes a suggested fix for a wall
that keeps recurring, and only again once the wall has doubled since the last suggestion."""

import logging
from datetime import datetime, time, timedelta
from typing import Any

import httpx

from . import store
from .ai import AI, AIError
from .config import Settings
from .db import Database

logger = logging.getLogger(__name__)

ITEM_LIMIT = 512
SEND_HOUR = time(7, 0)


def week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _cut(text: str, limit: int = ITEM_LIMIT) -> str:
    if len(text.encode()) <= limit:
        return text
    return text.encode()[: limit - len("…".encode())].decode(errors="ignore") + "…"


def _needs_remedy(wall: dict[str, Any], settings: Settings) -> bool:
    if wall["recent"] < settings.remedy_min_occurrences:
        return False
    return wall["remedy"] is None or wall["total"] >= 2 * (wall["remedy_count"] or 0)


async def refresh_remedies(db: Database, ai: AI, settings: Settings, now: datetime) -> int:
    async with db.connect() as conn:
        walls = await store.top_walls(conn, now, settings.window_days, settings.remedy_limit)
    written = 0
    for wall in walls:
        if not _needs_remedy(wall, settings):
            continue
        try:
            remedy = await ai.remedy(wall)
        except AIError as err:
            logger.warning("no remedy for wall #%d: %s", wall["id"], err)
            continue
        async with db.connect() as conn:
            await store.save_remedy(conn, wall["id"], remedy.model_dump(), wall["total"])
        written += 1
    return written


def _line(wall: dict[str, Any]) -> str:
    text = (
        f"#{wall['id']} {wall['recent']}x [{wall['blocker_type']}] "
        f"{wall['system']}: {wall['action_needed']}"
    )
    remedy = wall["remedy"]
    if remedy:
        text += f" -> {remedy['kind']}, {remedy['effort']}: {remedy['proposal']}"
    return _cut(text)


async def collect(db: Database, settings: Settings, now: datetime) -> dict[str, Any]:
    week_ago = now - timedelta(days=7)
    async with db.connect() as conn:
        top = await store.top_walls(conn, now, settings.window_days, settings.digest_top)
        cur = await conn.execute(
            """
            SELECT
              (SELECT count(*) FROM occurrences) AS occurrences_ever,
              (SELECT count(*) FROM occurrences WHERE created_at >= %(week)s) AS occurrences,
              (SELECT count(*) FROM occurrences WHERE wall_id IS NULL) AS unplaced,
              (SELECT count(*) FROM walls
                WHERE created_at >= %(week)s AND merged_into_id IS NULL) AS new_walls,
              (SELECT count(*) FROM walls
                WHERE status = 'open' AND merged_into_id IS NULL
                  AND blocker_type <> %(judgment)s) AS open_walls,
              (SELECT count(*) FROM walls
                WHERE status = 'open' AND reopened_count > 0
                  AND merged_into_id IS NULL) AS reopened,
              (SELECT count(*) FROM walls
                WHERE resolved_at >= %(week)s AND status = 'resolved') AS resolved,
              (SELECT count(*) FROM occurrences o JOIN walls w ON w.id = o.wall_id
                WHERE o.created_at >= %(week)s AND w.blocker_type = %(judgment)s) AS judgment
            """,
            {"week": week_ago, "judgment": store.JUDGMENT},
        )
        counts = await cur.fetchone()
        reviews = await store.pending_reviews(conn)
    return {"top": top, "counts": counts, "reviews": len(reviews), "since": week_ago}


def compose(data: dict[str, Any]) -> dict[str, Any]:
    counts = data["counts"]
    subject = (
        f"Walls: {counts['open_walls']} open, {counts['new_walls']} new, "
        f"{counts['occurrences']} reports this week"
    )
    heads_up = []
    if counts["occurrences"] == 0:
        heads_up.append("No reports this week. Either no walls, or agents stopped calling it.")
    if counts["reopened"]:
        heads_up.append(f"{counts['reopened']} resolved walls came back; the fix did not hold.")
    if data["reviews"]:
        heads_up.append(f"{data['reviews']} possible duplicates to review: ask for walls_review.")
    if counts["unplaced"]:
        heads_up.append(f"{counts['unplaced']} reports are waiting on embeddings.")
    if counts["judgment"]:
        heads_up.append(f"{counts['judgment']} were questions for you, not counted as walls.")
    handled = [f"{counts['resolved']} walls resolved this week."] if counts["resolved"] else []
    body: dict[str, Any] = {
        "subject": _cut(subject, 256),
        "period": f"since {data['since']:%Y-%m-%d}",
        "needs_you": [_line(wall) for wall in data["top"]],
    }
    if handled:
        body["handled"] = handled
    if heads_up:
        body["heads_up"] = heads_up
    return body


def is_due(now: datetime) -> bool:
    """True from Monday's send time through the end of the ISO week."""
    return now.weekday() > 0 or now.time() >= SEND_HOUR


async def send(settings: Settings, body: dict[str, Any], http: httpx.AsyncClient) -> str:
    response = await http.post(
        settings.mailbox_url.rstrip("/") + "/v1/messages",
        json={"to": [settings.digest_to], "type": "DIGEST", "body": body},
        headers={"Authorization": f"Bearer {settings.mailbox_key}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["message_id"]


async def run_if_due(
    db: Database, ai: AI, settings: Settings, http: httpx.AsyncClient, now: datetime
) -> str | None:
    """Send this week's digest unless it went out already. Returns the mail id when it sends.
    A failure leaves no row, so the next tick tries again."""
    if not is_due(now):
        return None
    week = week_key(now)
    async with db.connect() as conn:
        cur = await conn.execute("SELECT 1 FROM digests WHERE week = %s", (week,))
        if await cur.fetchone():
            return None
        cur = await conn.execute("SELECT 1 FROM occurrences LIMIT 1")
        if await cur.fetchone() is None:
            return None
    await refresh_remedies(db, ai, settings, now)
    body = compose(await collect(db, settings, now))
    mail_id = await send(settings, body, http)
    async with db.connect() as conn:
        await conn.execute(
            "INSERT INTO digests (week, sent_at, mail_id) VALUES (%s, %s, %s)", (week, now, mail_id)
        )
    return mail_id
