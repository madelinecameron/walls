# walls

An MCP server that logs every time a coding agent has to ask a human to do something by hand:
approve a permission, paste a credential, click through a web UI, press a button. It groups
similar requests into one "wall", counts how often each wall comes back, and suggests the tool
or permission that would remove it.

Agents call `wall_report`, then ask the human in chat as usual. Logging never blocks the agent.

## Tools

| Tool | Purpose |
|---|---|
| `wall_report` | Log a wall. Never blocks: if embeddings are down, it queues the report. |
| `walls_top` | Walls ranked by occurrences in a window, with suggested fixes. |
| `wall_resolve` | Mark a wall fixed. A later report of it reopens it. |
| `walls_review` | Pairs of walls that may be duplicates. |
| `wall_merge` | Merge a duplicate wall into another. |
| `wall_keep_separate` | Settle a review pair as two walls. |

## Run the server

You need Postgres with [pgvector](https://github.com/pgvector/pgvector) and an
OpenAI-compatible API for embeddings (OpenAI, LiteLLM, Ollama, vLLM). The embedding model must
return 1536-dimensional vectors, like `text-embedding-3-small`.

### With Docker Compose

```sh
cp deploy/example.env deploy/.env      # set WALLS_AUTH_TOKEN and WALLS_LLM_KEY
docker compose -f deploy/docker-compose.yml up -d
```

This starts Postgres and walls on `127.0.0.1:8797`. The server applies its own migrations on start.

### From source

```sh
uv sync
uv run walls init          # writes walls.toml with a new auth token
$EDITOR walls.toml         # set database_url and [llm]
uv run walls serve
```

To use an existing Postgres cluster, create the database and role once with
`deploy/postgres.sql`. Its header explains how to pass the password.

## Connect an agent

`walls install` registers the server with a harness and adds the instructions to the file the
harness reads in every session. Run it on the machine where the agent runs:

```sh
uvx --from git+https://github.com/madelinecameron/walls walls install claude \
  --url https://walls.example.com/mcp --token <auth_token>
```

| Harness | MCP server goes to | Instructions go to |
|---|---|---|
| `claude` | `claude mcp add --scope user` | `~/.claude/CLAUDE.md` |
| `claude --scope project` | `.mcp.json`, reading the token from `$WALLS_TOKEN` | `./CLAUDE.md` |
| `codex` | `~/.codex/config.toml` (or `$CODEX_HOME`) | `~/.codex/AGENTS.md` |
| `codex --scope project` | `~/.codex/config.toml` | `./AGENTS.md` |

For any other harness, `walls install print` prints the URL, the header, an `mcpServers` JSON
block and the instructions to paste.

Run the command again to update. The instructions sit between `<!-- walls:begin -->` and
`<!-- walls:end -->` markers and are replaced in place. To uninstall, remove that block and the
MCP server entry.

On the server, `walls install print` without `--url` or `--token` uses `public_url` and
`auth_token` from its config.

## Configuration

Settings come from a TOML file and from the environment. The file is `--config`, else
`$WALLS_CONFIG`, else `./walls.toml`. Every setting also has an environment variable, which wins
over the file. `walls init` writes a commented starter file.

| File | Environment | Default |
|---|---|---|
| `database_url` | `WALLS_DATABASE_URL` | required |
| `[server] host` | `WALLS_HOST` | `127.0.0.1` |
| `[server] port` | `WALLS_PORT` | `8797` |
| `[server] public_url` | `WALLS_PUBLIC_URL` | `http://<host>:<port>/mcp` |
| `[server] allowed_hosts` | `WALLS_ALLOWED_HOSTS` (comma-separated) | `127.0.0.1:*`, `localhost:*` |
| `[server] auth_token` | `WALLS_AUTH_TOKEN` | none: no authentication |
| `[llm] url` | `WALLS_LLM_URL` | required |
| `[llm] key` | `WALLS_LLM_KEY` | none |
| `[llm] embedding_model` | `WALLS_EMBEDDING_MODEL` | `text-embedding-3-small` |
| `[llm] remedy_model` | `WALLS_REMEDY_MODEL` | `gpt-4.1-mini` |
| `[matching] match_threshold` | `WALLS_MATCH_THRESHOLD` | `0.72` |
| `[matching] review_threshold` | `WALLS_REVIEW_THRESHOLD` | `0.65` |
| `[digest] webhook_url` | `WALLS_DIGEST_WEBHOOK_URL` | none: no digest |
| `[digest] webhook_token` | `WALLS_DIGEST_WEBHOOK_TOKEN` | none |
| `[digest] to` | `WALLS_DIGEST_TO` | none |
| `[digest] timezone` | `WALLS_TIMEZONE` | `UTC` |
| `[digest] top` | `WALLS_DIGEST_TOP` | `5` |
| `[digest] window_days` | `WALLS_WINDOW_DAYS` | `30` |
| `[digest] remedy_min_occurrences` | `WALLS_REMEDY_MIN_OCCURRENCES` | `3` |
| `[digest] remedy_limit` | `WALLS_REMEDY_LIMIT` | `10` |

### Security

- With `auth_token` set, every request except `GET /healthz` needs
  `Authorization: Bearer <auth_token>`. Without it, anyone who can reach the port can read and
  change the log. Leave it unset only on a private network.
- `allowed_hosts` guards against DNS rebinding. Add the host name agents use, such as
  `walls.example.com`. `"*"` turns the check off.
- Before anything is stored or sent for embedding, the server removes text that looks like a
  credential: API keys, tokens, passwords in URLs, private keys.

## How matching works

The server embeds `system: action_needed` and compares it to every wall by cosine similarity.

| Closest wall | Result |
|---|---|
| Same `system`, at or above `match_threshold` | Attaches as an occurrence. |
| Any `system`, at or above `review_threshold` | New wall, plus a pair in `walls_review`. |
| Below both | New wall. |

Only a wall on the same `system` attaches automatically. Walls on different systems can score as
high as true duplicates: "approve the pull request" on GitHub and "approve the merge request" on
GitLab scored 0.79 with `text-embedding-3-small`, higher than many true paraphrases. The default
thresholds come from 28 hand-written pairs. Check them against your own walls with
`walls_review` and adjust.

Other rules:

- `human_judgment` reports only match each other and stay out of the ranking, because a question
  for the human is not friction to remove.
- A repeat of the same wall in one MCP session within 6 hours is not counted again.
- A report of a resolved wall reopens it, which shows the fix did not hold.
- A merged wall still matches, and its reports go to the wall it was merged into.

Matching compares against every wall under one lock. That is fine for thousands of walls, not
millions.

## Weekly digest

The digest is optional and off by default. To turn it on, set `webhook_url`. Once a week, from
Monday 07:00 in `timezone`, the server sends `POST <webhook_url>` with a JSON body:

```json
{
  "type": "DIGEST",
  "to": ["<to>"],
  "body": {
    "subject": "Walls: 4 open, 1 new, 9 reports this week",
    "period": "since 2026-11-16",
    "needs_you": ["#3 5x [credential] vault: Rotate the Vault token -> permission, small: ..."],
    "handled": ["1 walls resolved this week."],
    "heads_up": ["2 possible duplicates to review: ask for walls_review."]
  }
}
```

`to` is there only when set, and `handled` and `heads_up` only when they have lines. With
`webhook_token` set, the request carries `Authorization: Bearer <webhook_token>`. Any 2xx response
counts as sent; a `message_id` in a JSON response is recorded. A failed send is tried again on the
next tick, so each week's digest goes out once.

A script does the ranking. A model writes a suggested fix only for a wall seen at least
`remedy_min_occurrences` times in `window_days`, and again only after the wall has doubled.

`walls digest-preview` prints this week's digest without sending it.

## Develop

The tests need a Postgres with pgvector and fail if there is none:

```sh
docker run -d --name walls-test -e POSTGRES_PASSWORD=test -p 5432:5432 pgvector/pgvector:0.8.1-pg17
export WALLS_TEST_DATABASE_URL='postgresql://postgres:test@localhost:5432/postgres'
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests drop and recreate the `public` schema, so point them at a throwaway database.

## License

MIT
