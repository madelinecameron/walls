import os
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

DEFAULT_ALLOWED_HOSTS = "walls.example.ts.net,127.0.0.1:*,localhost:*"


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    litellm_url: str = ""
    litellm_key: str = ""
    embedding_model: str = "openai/text-embedding-3-small"
    remedy_model: str = "balanced"
    mailbox_url: str = ""
    mailbox_key: str = ""
    digest_to: str = "human:owner"
    match_threshold: float = 0.72
    review_threshold: float = 0.55
    remedy_min_occurrences: int = 3
    remedy_limit: int = 10
    digest_top: int = 5
    window_days: int = 30
    host: str = "127.0.0.1"
    port: int = 8797
    allowed_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS.split(",")))
    tz: tzinfo = field(default_factory=lambda: ZoneInfo("America/New_York"))

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            database_url=env("WALLS_DATABASE_URL", ""),
            litellm_url=env("WALLS_LITELLM_URL", ""),
            litellm_key=env("WALLS_LITELLM_KEY", ""),
            embedding_model=env("WALLS_EMBEDDING_MODEL", cls.embedding_model),
            remedy_model=env("WALLS_REMEDY_MODEL", cls.remedy_model),
            mailbox_url=env("WALLS_MAILBOX_URL", ""),
            mailbox_key=env("WALLS_MAILBOX_KEY", ""),
            digest_to=env("WALLS_DIGEST_TO", cls.digest_to),
            match_threshold=float(env("WALLS_MATCH_THRESHOLD", cls.match_threshold)),
            review_threshold=float(env("WALLS_REVIEW_THRESHOLD", cls.review_threshold)),
            host=env("WALLS_HOST", cls.host),
            port=int(env("WALLS_PORT", cls.port)),
            allowed_hosts=env("WALLS_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(","),
            tz=ZoneInfo(env("WALLS_TZ", "America/New_York")),
        )

    def require(self, *names: str) -> None:
        missing = [name for name in names if not getattr(self, name)]
        if missing:
            raise SystemExit(
                "missing settings: " + ", ".join(f"WALLS_{n.upper()}" for n in missing)
            )

    def now(self) -> datetime:
        return datetime.now(self.tz)
