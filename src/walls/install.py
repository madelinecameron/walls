"""Connects an agent harness to a walls server: registers the MCP server with the harness, and
puts the instructions in the file the harness reads on every session."""

import json
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError

NAME = "walls"
BEGIN = "<!-- walls:begin -->"
END = "<!-- walls:end -->"
TOKEN_ENV = "WALLS_TOKEN"

PROMPT = """\
## Walls

When you must ask the human to do something by hand, call `wall_report` on the `walls` MCP
server first, then ask them in chat as usual. The log never replaces asking them. Never wait on
it and never retry it; if it fails, carry on.

Report actions only the human could take: approving a permission, supplying a credential,
clicking through a web UI, touching hardware. Use blocker_type `human_judgment` when you are
asking for a decision rather than an action. Do not report ordinary clarifying questions, or
actions you could have done yourself. Write `action_needed` as the general action ("Rotate the
Vault token"), and never include secret values.
"""


@dataclass(frozen=True)
class Target:
    url: str
    token: str
    scope: str
    home: Path
    cwd: Path


def upsert_block(path: Path, body: str) -> str:
    """Write `body` between the walls markers in `path`, replacing an earlier copy. Returns
    "added" or "updated"."""
    block = f"{BEGIN}\n{body.rstrip()}\n{END}\n"
    text = path.read_text() if path.exists() else ""
    if BEGIN in text and END in text:
        start = text.index(BEGIN)
        end = text.index(END, start) + len(END)
        if text[end : end + 1] == "\n":
            end += 1
        path.write_text(text[:start] + block + text[end:])
        return "updated"
    path.parent.mkdir(parents=True, exist_ok=True)
    separator = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + separator + block)
    return "added"


def _run(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    if shutil.which(command[0]) is None:
        raise ConfigError(f"{command[0]} is not on PATH; install it, or use `walls install print`")
    return subprocess.run(command, check=check, capture_output=True, text=True)


def install_claude(target: Target) -> list[str]:
    """User scope writes the token into Claude Code's user config. Project scope writes
    .mcp.json, which is usually committed, so it refers to $WALLS_TOKEN instead."""
    scope = target.scope
    command = ["claude", "mcp", "add", "--transport", "http", "--scope", scope, NAME, target.url]
    if target.token:
        value = "${" + TOKEN_ENV + "}" if scope == "project" else target.token
        command += ["--header", f"Authorization: Bearer {value}"]
    _run(["claude", "mcp", "remove", "--scope", scope, NAME], check=False)
    try:
        _run(command)
    except subprocess.CalledProcessError as err:
        raise ConfigError(f"claude mcp add failed: {err.stderr.strip()}") from err
    done = [f"Registered the walls MCP server with Claude Code ({scope} scope)."]
    if target.token and scope == "project":
        done.append(f".mcp.json reads the token from ${TOKEN_ENV}; export it in your shell.")
    prompt = target.home / ".claude" / "CLAUDE.md" if scope == "user" else target.cwd / "CLAUDE.md"
    done.append(f"{upsert_block(prompt, PROMPT).capitalize()} the instructions in {prompt}.")
    return done


def _drop_table(text: str, table: str) -> str:
    """Remove `[table]` and its subtables from TOML text, leaving every other line alone."""
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            header = stripped.split("]")[0].strip("[ ")
            skipping = header == table or header.startswith(table + ".")
        if not skipping:
            kept.append(line)
    return "".join(kept)


def _servers(config: Path, text: str) -> dict:
    try:
        return tomllib.loads(text).get("mcp_servers", {})
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{config}: {err}") from err


def install_codex(target: Target) -> list[str]:
    """Codex reads MCP servers from the user's config.toml only, so the server always goes
    there; project scope only changes where the instructions go."""
    codex_home = Path(os.environ.get("CODEX_HOME") or target.home / ".codex")
    config = codex_home / "config.toml"
    text = config.read_text() if config.exists() else ""
    if NAME in _servers(config, text):
        text = _drop_table(text, f"mcp_servers.{NAME}")
        if NAME in _servers(config, text):
            raise ConfigError(f"remove the existing {NAME} server from {config} and run again")
    lines = [f"[mcp_servers.{NAME}]", f"url = {json.dumps(target.url)}"]
    if target.token:
        header = json.dumps(f"Bearer {target.token}")
        lines.append(f"http_headers = {{ Authorization = {header} }}")
    text = text.rstrip("\n")
    text = (text + "\n\n" if text else "") + "\n".join(lines) + "\n"
    _servers(config, text)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(text)
    prompt = codex_home / "AGENTS.md" if target.scope == "user" else target.cwd / "AGENTS.md"
    return [
        f"Registered the walls MCP server in {config}.",
        f"{upsert_block(prompt, PROMPT).capitalize()} the instructions in {prompt}.",
    ]


def describe(target: Target) -> str:
    """Everything needed to connect a harness this command does not know."""
    server: dict = {"type": "http", "url": target.url}
    if target.token:
        server["headers"] = {"Authorization": f"Bearer {target.token}"}
    lines = [
        f"URL:    {target.url}",
        f"Header: Authorization: Bearer {target.token}" if target.token else "Header: none",
        "",
        "MCP config (the mcpServers shape most harnesses read):",
        json.dumps({"mcpServers": {NAME: server}}, indent=2),
        "",
        "Instructions for the harness's memory file (CLAUDE.md, AGENTS.md or similar):",
        "",
        f"{BEGIN}\n{PROMPT.rstrip()}\n{END}",
    ]
    return "\n".join(lines)


HARNESSES = {"claude": install_claude, "codex": install_codex}
