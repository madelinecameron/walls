# walls

<p align="center"><img src="docs/demo.gif" width="940" alt="Claude Code logs a wall with wall_report, then lists the top walls and their suggested fixes"></p>

walls is an MCP server that records each time a coding agent has to ask you to do something it
can't do itself, like approving a permission or pasting a token. It groups repeats of the same
request into one *wall*, counts how often each wall comes back, and suggests a change that would
remove it. You end up with a ranked list of the manual steps that cause you to need to give
guidance or do a task, reducing the autonomy of your workflow.

## What you get

Ask a connected agent "what are my top walls?" and it calls `walls_top`. After a month the answer
looks something like:

| # | Seen (30 days) | Type | System | What the agent needed | Suggested fix |
|---|---|---|---|---|---|
| 1 | 14 | permission | github | Approve the workflow run on a fork pull request | **config, small:** let workflows on pull requests from collaborators run without approval |
| 2 | 9 | credential | npm | Log in to the npm registry | **tool, small:** store a granular automation token and read it from `NPM_TOKEN` |
| 3 | 6 | physical_or_ui | aws | Enable a model in the Bedrock console | **workflow, medium:** request Bedrock model access with the AWS CLI in the setup script |
| 4 | 4 | permission | claude | Allow docker compose in this repo | **permission, small:** add `Bash(docker compose:*)` to the project's allowed commands |

Each suggested fix also states its risk. Once you've made a fix, tell the agent, for example
"wall 3 is fixed: I allowed Actions for collaborators", and it calls `wall_resolve`.

The optional weekly digest sends the same list to a webhook every Monday morning.

## Quick start

You need Docker, [uv](https://docs.astral.sh/uv/), and an OpenAI API key. Any other
OpenAI-compatible API also works; see [Configuration](#configuration).

### 1. Start the server

This runs walls and its own Postgres on your machine.

```sh
git clone https://github.com/madelinecameron/walls && cd walls
cp deploy/example.env deploy/.env
```

Open `deploy/.env` and set `WALLS_LLM_KEY` to your OpenAI API key. Then start the server:

```sh
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs walls
```

On first start the server makes an auth token and prints it in the logs once, along with the
exact command for step 2. Save it: the server keeps only a hash and can't show it again. If you
lose it, `docker compose -f deploy/docker-compose.yml exec walls walls rotate-token` makes a new
one, and the old one stops working.

### 2. Connect your agent

Run this on the machine where your agent runs, with the token the server printed:

```sh
uvx --from git+https://github.com/madelinecameron/walls walls install claude \
  --url http://127.0.0.1:8797/mcp --token <your WALLS_AUTH_TOKEN>
```

This registers the server with Claude Code and adds instructions to `~/.claude/CLAUDE.md` that
tell the agent when to log a wall. For Codex, use `install codex`. Other agents are covered under
[Connect other agents](#connect-other-agents).

### 3. Use your agent as usual

Start a new session. The next time the agent needs you to do something, it logs a wall first.
After a few days, ask it which walls come up most.

Each report costs one embedding call. Suggested fixes use a small model and only run for walls
seen at least 3 times.

## How it works

1. When the agent needs something only you can do, it calls `wall_report` with the action, the
   system involved, and why it couldn't do it itself.
2. The server removes anything that looks like a credential before it stores the report or sends
   it for embedding.
3. The server embeds the report and compares it with existing walls. A close match on the same
   system counts as another occurrence of that wall. A weaker match creates a new wall and flags
   the pair as a possible duplicate. Anything else becomes a new wall.

The server exposes six tools:

| Tool | What it does |
|---|---|
| `wall_report` | Log a wall |
| `walls_top` | Walls ranked by occurrences, with suggested fixes |
| `wall_resolve` | Marks a wall fixed |
| `walls_review` | Review walls that may be duplicates |
| `wall_merge` | Merge a duplicate into another wall |
| `wall_keep_separate` | Settle a review pair as two different walls. |

When the agent asks you to decide something ("should I delete this branch?"), it logs the
question as `human_judgment`. Those stay out of the ranking, since decisions are yours to make,
not steps to automate.

## Connect other agents

`walls install` registers the MCP server with your agent and adds instructions to the file the
agent reads at the start of every session. The agent needs those instructions to know when to
call `wall_report`.

| Command | Server is added to | Instructions go in |
|---|---|---|
| `walls install claude` | Claude Code, for all your projects | `~/.claude/CLAUDE.md` |
| `walls install claude --scope project` | `.mcp.json` in this repo; the token is read from `$WALLS_TOKEN` so it is never committed | `./CLAUDE.md` |
| `walls install codex` | `~/.codex/config.toml` | `~/.codex/AGENTS.md` |
| `walls install codex --scope project` | `~/.codex/config.toml` | `./AGENTS.md` |
| `walls install print` | Nowhere: prints the URL, the auth header, an `mcpServers` JSON block and the instructions to paste | You paste them |

Use `print` for Cursor, Windsurf, Zed or any other MCP client.

To update, run the same command again. The instructions sit between `<!-- walls:begin -->` and
`<!-- walls:end -->` and are replaced in place.

To uninstall, delete that block and remove the server with `claude mcp remove walls`, or delete
the `[mcp_servers.walls]` table from Codex's config.

To use walls from several machines, run one server where they can all reach it and point every
agent at it. Read [Security](#security) before you expose it beyond localhost.

## Configuration

Most setups only need the database and the model API. If agents connect from other machines,
also set the host name they use.

Settings come from a TOML file and from environment variables; an environment variable wins. The
file is `--config`, else `$WALLS_CONFIG`, else `./walls.toml`. `walls init` writes a commented
starter file with a fresh auth token. With Docker Compose, put the variables in `deploy/.env`.

### Other model APIs

Any OpenAI-compatible API works, including LiteLLM, Ollama and vLLM. Set `WALLS_LLM_URL` to its
`/v1` base URL. The embedding model must return 1536-dimensional vectors, as
`text-embedding-3-small` does.

### Without Docker

```sh
uv sync
uv run walls init        # writes walls.toml
$EDITOR walls.toml       # set database_url and [llm]
uv run walls serve
```

You need Postgres with [pgvector](https://github.com/pgvector/pgvector). For an existing cluster,
`deploy/postgres.sql` creates the database and role.

### All settings

| File | Environment | Default |
|---|---|---|
| `database_url` | `WALLS_DATABASE_URL` | required |
| `[server] host` | `WALLS_HOST` | `127.0.0.1` |
| `[server] port` | `WALLS_PORT` | `8797` |
| `[server] public_url` | `WALLS_PUBLIC_URL` | `http://<host>:<port>/mcp` |
| `[server] allowed_hosts` | `WALLS_ALLOWED_HOSTS` (comma-separated) | `127.0.0.1:*`, `localhost:*` |
| `[server] auth` | `WALLS_AUTH` | `true`; `false` turns authentication off |
| `[server] auth_token` | `WALLS_AUTH_TOKEN` | made on first start; only its hash is kept |
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

## Security

- Every request except `GET /healthz` needs `Authorization: Bearer <token>`. The token is
  `auth_token` if you set one. Otherwise the server makes one on first start and prints it once.
  The database holds only its SHA-256 hash, so nothing on the server can show it again;
  `walls rotate-token` replaces it. Anyone who can run commands on the server can still rotate
  it, so don't give agents shell or Docker access to the walls host.
- With `auth = false`, anyone who can reach the port can read and change the log. Use that only
  on localhost or a private network.
- If agents connect by host name, such as `walls.example.com`, add it to `allowed_hosts`. The
  check guards against DNS rebinding. `"*"` turns it off.
- The server removes text that looks like a credential, such as API keys, tokens, passwords in
  URLs and private keys, before it stores a report or sends it for embedding.
- The scrubbed report text goes to your model API for embedding, and the top walls go to it for
  suggested fixes. Nothing else leaves the server.

## Matching rules

The server embeds `system: action_needed` and compares it to every wall by cosine similarity.

| Closest wall | Result |
|---|---|
| Same system, at or above `match_threshold` (0.72) | Counted as another occurrence of that wall |
| Any system, at or above `review_threshold` (0.65) | New wall, plus a pair in `walls_review` |
| Below both | New wall |

Only walls on the same system merge automatically, because walls on different systems can look
alike: "approve the pull request" on GitHub and "approve the merge request" on GitLab scored
0.79. The defaults come from 28 hand-written pairs; check them against your own walls with
`walls_review`.

- A repeat of the same wall in one MCP session within 6 hours is counted once.
- A merged wall still matches, and its reports go to the wall it was merged into.
- Matching compares against every wall under one lock: fine for thousands of walls, not millions.

## Weekly digest

The digest is off by default. With `webhook_url` set, the server sends this week's top walls as a
JSON POST every Monday from 07:00 in `timezone`:

```json
{
  "type": "DIGEST",
  "to": ["you@example.com"],
  "body": {
    "subject": "Walls: 4 open, 1 new, 9 reports this week",
    "period": "since 2026-11-16",
    "needs_you": ["#3 5x [credential] vault: Rotate the Vault token -> permission, small: ..."],
    "handled": ["1 walls resolved this week."],
    "heads_up": ["2 possible duplicates to review: ask for walls_review."]
  }
}
```

`to` appears only when set; `handled` and `heads_up` only when they have lines. With
`webhook_token` set, the request carries `Authorization: Bearer <webhook_token>`. Any 2xx
response counts as sent, and a failed send is retried, so each week goes out once.
`walls digest-preview` prints this week's digest without sending it.

## Develop

The tests need Postgres with pgvector:

```sh
docker run -d --name walls-test -e POSTGRES_PASSWORD=test -p 5432:5432 pgvector/pgvector:0.8.1-pg17
export WALLS_TEST_DATABASE_URL='postgresql://postgres:test@localhost:5432/postgres'
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests drop and recreate the `public` schema, so point them at a throwaway database.

## License

MIT
