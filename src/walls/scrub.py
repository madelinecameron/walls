"""Removes anything that looks like a credential before a report is embedded or stored.

Walls about credentials are the likeliest place for an agent to paste a token, and the text
goes to the embeddings provider, so this runs before anything else touches a report."""

import re

MASK = "[REDACTED]"

_PATTERNS = [
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL
    ),
    re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bmbx_[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bhv[sbr]\.[A-Za-z0-9_-]{16,}"),
    re.compile(r"\btskey-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
]
_BEARER = re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]*:)[^\s@/]+(@)", re.IGNORECASE)
_ASSIGNED = re.compile(
    r"\b([A-Za-z0-9_-]*(?:password|passwd|secret|token|[_-]key|apikey|credential)[A-Za-z0-9_-]*)"
    r"(\s*[=:]\s*)(['\"]?)[^\s'\"]{4,}\3",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
    text = _URL_PASSWORD.sub(lambda m: f"{m.group(1)}{MASK}{m.group(2)}", text)
    return _ASSIGNED.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
