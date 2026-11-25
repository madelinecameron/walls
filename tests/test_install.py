import json
import tomllib
from dataclasses import replace

import pytest

from walls import install
from walls.install import BEGIN, END, PROMPT, Target

URL = "https://walls.example.com/mcp"


@pytest.fixture
def target(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    (tmp_path / "home").mkdir()
    (tmp_path / "repo").mkdir()
    return Target(URL, "t0ken", "user", tmp_path / "home", tmp_path / "repo")


@pytest.fixture
def commands(monkeypatch):
    ran = []
    monkeypatch.setattr(install, "_run", lambda command, check=True: ran.append(command))
    return ran


def test_block_is_appended_once_and_replaced_after(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("# Mine\n\nKeep this.")
    assert install.upsert_block(path, "first") == "added"
    assert install.upsert_block(path, "second") == "updated"
    text = path.read_text()
    assert text.startswith("# Mine\n\nKeep this.\n\n")
    assert text.count(BEGIN) == 1 and "second" in text and "first" not in text


def test_text_after_the_block_survives(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(f"before\n{BEGIN}\nold\n{END}\nafter\n")
    install.upsert_block(path, "new")
    assert path.read_text() == f"before\n{BEGIN}\nnew\n{END}\nafter\n"


def test_claude_user_scope(target, commands):
    install.install_claude(target)
    assert commands[-1] == [
        "claude", "mcp", "add", "--transport", "http", "--scope", "user", "walls", URL,
        "--header", "Authorization: Bearer t0ken",
    ]  # fmt: skip
    assert PROMPT.strip() in (target.home / ".claude" / "CLAUDE.md").read_text()


def test_claude_project_scope_keeps_the_token_out_of_the_repo(target, commands):
    lines = install.install_claude(replace(target, scope="project"))
    assert commands[-1][-1] == "Authorization: Bearer ${WALLS_TOKEN}"
    assert (target.cwd / "CLAUDE.md").exists()
    assert any("WALLS_TOKEN" in line for line in lines)


def test_codex_adds_the_server_and_keeps_other_settings(target):
    config = target.home / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'model = "o3"\n\n[mcp_servers.walls]\nurl = "http://old"\n\n'
        '[mcp_servers.walls.tools]\nx = 1\n\n[mcp_servers.other]\ncommand = "x"\n'
    )
    install.install_codex(target)
    data = tomllib.loads(config.read_text())
    assert data["model"] == "o3"
    assert data["mcp_servers"]["other"] == {"command": "x"}
    assert data["mcp_servers"]["walls"] == {
        "url": URL,
        "http_headers": {"Authorization": "Bearer t0ken"},
    }
    assert PROMPT.strip() in (target.home / ".codex" / "AGENTS.md").read_text()


def test_codex_honours_codex_home(target, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))
    install.install_codex(target)
    assert (tmp_path / "elsewhere" / "config.toml").exists()


def test_print_without_a_token(target):
    text = install.describe(replace(target, token=""))
    config = json.loads(text[text.index("{") : text.rindex("}") + 1])
    assert config == {"mcpServers": {"walls": {"type": "http", "url": URL}}}
    assert "Header: none" in text and BEGIN in text
