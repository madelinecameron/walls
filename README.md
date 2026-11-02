# walls

A log of every time an agent has to ask a human to do something by hand. It shows which requests
recur, and what tool or permission would remove them.

Agents call `wall_report` through MCP, then tell the human in chat as usual. Similar reports attach
to one wall, so each wall is unique and still has a count. Once a week the top walls go out as a
Mailbox `DIGEST`.

## Tools

| Tool | Purpose |
|---|---|
| `wall_report` | Log a wall. Never blocks: if embeddings are down, it queues the report. |
| `walls_top` | Walls ranked by occurrences in a window, with suggested fixes. |
| `wall_resolve` | Mark a wall fixed. A later report of it reopens it. |
| `walls_review` | Pairs of walls that may be duplicates. |
| `wall_merge` | Merge a duplicate wall into another. |
| `wall_keep_separate` | Settle a review pair as two walls. |

## How matching works

The server scrubs anything that looks like a secret, then embeds `system: action_needed` with
`text-embedding-3-small` and compares it to every wall by cosine similarity.

| Closest wall | Result |
|---|---|
| Same `system`, at or above `WALLS_MATCH_THRESHOLD` (0.72) | Attaches as an occurrence. |
| Any `system`, at or above `WALLS_REVIEW_THRESHOLD` (0.65) | New wall, plus a pair in `walls_review`. |
| Below both | New wall. |

Only a wall on the same `system` attaches automatically. With real embeddings, walls on different
systems can score as high as true duplicates: "approve the pull request" on GitHub and "approve the
merge request" on GitLab scored 0.79, higher than many true paraphrases. These values come from 28
hand-written pairs, so check them against real walls with `walls_review` and adjust.

Other rules:

- `human_judgment` reports only match each other and stay out of the ranking, because a question
  for the human is not friction to remove.
- A repeat of the same wall in one MCP session within 6 hours is not counted again.
- A report of a resolved wall reopens it, which shows the fix did not hold.
- A merged wall still matches, and its reports go to the wall it was merged into.

## Weekly digest

The service sends one `DIGEST` to `WALLS_DIGEST_TO` from Monday 07:00 America/New_York. A script does
the ranking. A model writes a suggested fix only for a wall seen at least 3 times in 30 days, and
again only after the wall has doubled.

Preview it without sending: `walls digest-preview`. Mailbox's relay decides where mail to
`human:owner` shows up in Discord.

## Develop

Tests need a Postgres with pgvector and fail if there is none:

```sh
export WALLS_TEST_DATABASE_URL='postgresql://...'
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Deploy

CI publishes `ghcr.io/madelinecameron/walls:sha-<short>` from `main`.

1. Set the tag in `/opt/walls/.env` on the server: `APP_IMAGE=ghcr.io/madelinecameron/walls:sha-<short>`.
2. Run `./deploy.sh walls`.

The deploy config holds a copy of `deploy/docker-compose.yml`.
Change both together. Secrets are in Vault at `secret/walls`.

The service migrates its own schema on start. Provision the database once with
`deploy/postgres.sql`.

The server is reachable from the tailnet with no credential.
