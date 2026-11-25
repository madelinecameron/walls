import pytest

from walls.config import ConfigError, Settings

FILE = """
database_url = "postgresql://localhost/walls"

[server]
port = 9000
allowed_hosts = ["walls.example.com", "127.0.0.1:*"]

[llm]
url = "http://localhost:11434/v1"

[matching]
match_threshold = 0.8

[digest]
timezone = "Europe/Berlin"
"""


def write(tmp_path, text=FILE):
    path = tmp_path / "walls.toml"
    path.write_text(text)
    return path


def test_file_sections_map_to_settings(tmp_path):
    settings = Settings.load(write(tmp_path), env={})
    assert settings.database_url == "postgresql://localhost/walls"
    assert settings.port == 9000
    assert settings.allowed_hosts == ["walls.example.com", "127.0.0.1:*"]
    assert settings.llm_url == "http://localhost:11434/v1"
    assert settings.match_threshold == 0.8
    assert settings.timezone == "Europe/Berlin"
    assert settings.review_threshold == 0.65


def test_environment_wins_over_the_file(tmp_path):
    env = {"WALLS_PORT": "9100", "WALLS_ALLOWED_HOSTS": "a.example, b.example"}
    settings = Settings.load(write(tmp_path), env=env)
    assert settings.port == 9100
    assert settings.allowed_hosts == ["a.example", "b.example"]


def test_config_path_from_the_environment(tmp_path):
    path = write(tmp_path)
    assert Settings.load(env={"WALLS_CONFIG": str(path)}).port == 9000


def test_no_file_means_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Settings.load(env={}) == Settings()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[server]\nprot = 1\n", "unknown setting [server] prot"),
        ("[server]\nport = 'high'\n", "bad value for [server] port"),
        ("[digest]\ntimezone = 'Mars/Olympus'\n", "unknown timezone"),
        ("[server\n", "walls.toml"),
    ],
)
def test_mistakes_stop_with_a_clear_message(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[")):
        Settings.load(write(tmp_path, text), env={})


def test_digest_needs_only_a_webhook_url():
    Settings().check_digest()
    assert not Settings().digest_enabled
    with pytest.raises(ConfigError, match="webhook_url"):
        Settings(digest_to="you@example.com").check_digest()
    with pytest.raises(ConfigError, match="WALLS_DIGEST_WEBHOOK_URL"):
        Settings(digest_webhook_token="k").check_digest()
    only_url = Settings(digest_webhook_url="http://hooks.example/walls")
    only_url.check_digest()
    assert only_url.digest_enabled


def test_mcp_url():
    assert Settings().mcp_url == "http://127.0.0.1:8797/mcp"
    assert Settings(host="0.0.0.0", port=80).mcp_url == "http://127.0.0.1:80/mcp"
    assert Settings(public_url="https://walls.example.com/mcp").mcp_url.startswith("https://")


def test_auth_flag_reads_true_and_false(tmp_path):
    assert Settings.load(env={}).auth is True
    assert Settings.load(env={"WALLS_AUTH": "false"}).auth is False
    off = write(tmp_path, "[server]\nauth = false\n")
    assert Settings.load(off, env={}).auth is False
    with pytest.raises(ConfigError, match="auth"):
        Settings.load(env={"WALLS_AUTH": "maybe"})
