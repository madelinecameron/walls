import pytest

from walls.scrub import MASK, scrub


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwx",
        "sk-ant-api03-abcdefghijklmnopqrstuvwx",
        "ghp_abcdefghijklmnopqrstuvwxyz012345",
        "github_pat_abcdefghijklmnopqrstuvwxyz",
        "glpat-abcdefghijklmnopqrst",
        "xoxb-1234567890-abcdefghij",
        "AKIAIOSFODNN7EXAMPLE",
        "mbx_abcdefghijklmnop",
        "hvs.abcdefghijklmnopqrstuvwx",
        "tskey-auth-abcdefghijklmnop",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop",
        "0123456789abcdef0123456789abcdef0123456789abcdef",
    ],
)
def test_known_token_shapes_are_removed(secret):
    cleaned = scrub(f"Replace {secret} on the server")
    assert secret not in cleaned and MASK in cleaned


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("postgres://walls:hunter2hunter@db.example.com/walls", "postgres://walls:"),
        ("Authorization: Bearer abcdefghij123456", "Bearer"),
        ("WALLS_LLM_KEY=abcd1234 is wrong", None),
        ("api_key: 'abcd1234'", "api_key"),
        ("password = correcthorse", "password"),
    ],
)
def test_secret_values_go_and_their_labels_stay(text, kept):
    cleaned = scrub(text)
    for secret in ("hunter2hunter", "abcdefghij123456", "correcthorse", "abcd1234"):
        assert secret not in cleaned
    if kept:
        assert kept in cleaned


def test_private_key_block_is_removed_whole():
    key = "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----"
    assert scrub(f"before {key} after") == f"before {MASK} after"


@pytest.mark.parametrize(
    "text",
    [
        "Rotate the Vault token",
        "Approve the secret-scan workflow in GitHub",
        "Grant the agent read access to secret/walls",
        "https://walls.example.com/mcp is unreachable",
    ],
)
def test_ordinary_sentences_about_secrets_are_left_alone(text):
    assert scrub(text) == text
