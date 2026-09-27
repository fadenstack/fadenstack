import enum
import os
from pathlib import Path
from tempfile import gettempdir
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL

_CPU_COUNT = os.cpu_count() or 1

TEMP_DIR = Path(gettempdir())


class LogLevel(str, enum.Enum):
    """Possible log levels."""

    NOTSET = "NOTSET"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class Settings(BaseSettings):
    """Application settings for the Skills Registry service."""

    host: str = "127.0.0.1"
    port: int = 8008
    workers_count: int = min(_CPU_COUNT, 4)
    reload: bool = False

    environment: str = "production"
    log_level: LogLevel = LogLevel.INFO

    # ── Database (PostgreSQL) ──
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_user: str = "llm_user"
    db_pass: str = "llm_user"
    db_base: str = "llm_skills"
    db_echo: bool = False
    # Per process, not per CPU. Every uvicorn worker of every service holds
    # its own pool, and all of them share one Postgres (max_connections 100
    # by default). Sized from the CPU count, one gateway worker on a
    # 64-thread host could open 384 connections and locked every other
    # service out (2026-09-25). 5 kept + 10 on demand is SQLAlchemy's default.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ── Redis (optional — empty host disables) ──
    redis_host: str = ""
    redis_port: int = 6379
    redis_user: Optional[str] = None
    redis_pass: Optional[str] = None
    redis_base: Optional[int] = None

    # ── Internal service-to-service auth token ──
    service_token: str = ""

    # ── JWT (for admin API auth — validates backend-issued tokens) ──
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_audience: str = ""
    jwt_issuer: str = ""

    # ── Skills defaults ──
    # Published skills the whole tenant sees (visibility tenant or global).
    # People's private skills do not count against it.
    max_published_skills_per_tenant: int = 25
    # Skills one person may own. Versions are not limited: none is ever
    # deleted, since the audit log names the version a chat used.
    max_skills_per_user: int = 200
    max_assignments_per_tenant: int = 10
    default_max_resolve_results: int = 3
    # Skills asked for by name ($slug, the chat's picker) come on top of the
    # ones that match on their own, up to this many.
    max_explicit_skills: int = 5
    # Size limits. A skill's body goes into the prompt of every chat it
    # reaches; the recommendation is under 5000 tokens.
    max_body_chars: int = 100_000
    max_files_per_skill: int = 64
    max_file_bytes: int = 512 * 1024
    max_bundle_bytes: int = 4 * 1024 * 1024
    max_upload_bytes: int = 8 * 1024 * 1024

    # ── Observability ──
    # Named per service, like the backend's and gateway's. Each service clears
    # this directory on startup; with PII, MCP and skills sharing ``prom`` on
    # one host (``llmport dev up --modules``), the one starting last could not
    # clear files the others held open, and exited (FileExistsError).
    prometheus_dir: Path = TEMP_DIR / "prom-skills"
    sentry_dsn: Optional[str] = None
    sentry_sample_rate: float = 1.0
    opentelemetry_endpoint: Optional[str] = None

    @property
    def db_url(self) -> URL:
        """Assemble database URL from settings."""
        return URL.build(
            scheme="postgresql+asyncpg",
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_pass,
            path=f"/{self.db_base}",
        )

    @property
    def redis_enabled(self) -> bool:
        """Return True when a Redis host is configured."""
        return bool(self.redis_host)

    @property
    def redis_url(self) -> URL:
        """Assemble Redis URL from settings."""
        path = ""
        if self.redis_base is not None:
            path = f"/{self.redis_base}"
        return URL.build(
            scheme="redis",
            host=self.redis_host,
            port=self.redis_port,
            user=self.redis_user,
            password=self.redis_pass,
            path=path,
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LLM_PORT_SKILLS_",
        env_file_encoding="utf-8",
    )


settings = Settings()
