import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONFIG_ENV = "WALLS_CONFIG"
DEFAULT_CONFIG = Path("walls.toml")
MAILBOX_SETTINGS = ("mailbox_url", "mailbox_key", "digest_to")

# Where each setting sits in the config file. Its environment variable is WALLS_<NAME>, and an
# environment variable wins over the file.
FILE_KEYS: dict[str, tuple[str, str]] = {
    "database_url": ("", "database_url"),
    "host": ("server", "host"),
    "port": ("server", "port"),
    "public_url": ("server", "public_url"),
    "allowed_hosts": ("server", "allowed_hosts"),
    "auth_token": ("server", "auth_token"),
    "llm_url": ("llm", "url"),
    "llm_key": ("llm", "key"),
    "embedding_model": ("llm", "embedding_model"),
    "remedy_model": ("llm", "remedy_model"),
    "match_threshold": ("matching", "match_threshold"),
    "review_threshold": ("matching", "review_threshold"),
    "mailbox_url": ("digest", "mailbox_url"),
    "mailbox_key": ("digest", "mailbox_key"),
    "digest_to": ("digest", "to"),
    "timezone": ("digest", "timezone"),
    "digest_top": ("digest", "top"),
    "window_days": ("digest", "window_days"),
    "remedy_min_occurrences": ("digest", "remedy_min_occurrences"),
    "remedy_limit": ("digest", "remedy_limit"),
}


class ConfigError(SystemExit):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    host: str = "127.0.0.1"
    port: int = 8797
    public_url: str = ""
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1:*", "localhost:*"])
    auth_token: str = ""
    llm_url: str = ""
    llm_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    remedy_model: str = "gpt-4.1-mini"
    match_threshold: float = 0.72
    review_threshold: float = 0.65
    mailbox_url: str = ""
    mailbox_key: str = ""
    digest_to: str = ""
    timezone: str = "UTC"
    digest_top: int = 5
    window_days: int = 30
    remedy_min_occurrences: int = 3
    remedy_limit: int = 10

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ConfigError(f"unknown timezone: {self.timezone}") from err

    @classmethod
    def load(
        cls, path: str | Path | None = None, env: Mapping[str, str] = os.environ
    ) -> "Settings":
        """Settings from the config file, then WALLS_* environment variables on top. The file is
        `path`, else $WALLS_CONFIG, else ./walls.toml if it exists, else none."""
        path = path or env.get(CONFIG_ENV) or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
        values = _read_file(Path(path)) if path else {}
        for name in FILE_KEYS:
            raw = env.get(f"WALLS_{name.upper()}")
            if raw:
                values[name] = raw
        types = {f.name: f.type for f in fields(cls)}
        return cls(**{name: _coerce(name, value, types[name]) for name, value in values.items()})

    def require(self, *names: str) -> None:
        missing = [name for name in names if not getattr(self, name)]
        if missing:
            raise ConfigError(
                "missing settings: "
                + ", ".join(f"{_file_name(n)} (WALLS_{n.upper()})" for n in missing)
            )

    def check_digest(self) -> None:
        """Mailbox is optional, but half a Mailbox setup is a mistake worth stopping for."""
        if any(getattr(self, name) for name in MAILBOX_SETTINGS):
            self.require(*MAILBOX_SETTINGS)

    @property
    def digest_enabled(self) -> bool:
        return all(getattr(self, name) for name in MAILBOX_SETTINGS)

    @property
    def mcp_url(self) -> str:
        if self.public_url:
            return self.public_url
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}/mcp"

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.timezone))


def _file_name(name: str) -> str:
    section, key = FILE_KEYS[name]
    return f"[{section}] {key}" if section else key


def _read_file(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text())
    except OSError as err:
        raise ConfigError(f"cannot read config {path}: {err.strerror}") from err
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path}: {err}") from err
    by_place = {place: name for name, place in FILE_KEYS.items()}
    values = {}
    for key, value in data.items():
        entries = value.items() if isinstance(value, dict) else [(key, value)]
        section = key if isinstance(value, dict) else ""
        for sub, item in entries:
            name = by_place.get((section, sub))
            if name is None:
                where = f"[{section}] {sub}" if section else sub
                raise ConfigError(f"{path}: unknown setting {where}")
            values[name] = item
    return values


def _coerce(name: str, value: Any, kind: Any) -> Any:
    try:
        if kind == list[str]:
            items = value.split(",") if isinstance(value, str) else value
            return [str(item).strip() for item in items if str(item).strip()]
        if kind is int:
            return int(value)
        if kind is float:
            return float(value)
        if not isinstance(value, str):
            raise TypeError("expected a string")
        return value
    except (TypeError, ValueError) as err:
        raise ConfigError(f"bad value for {_file_name(name)}: {value!r} ({err})") from err
