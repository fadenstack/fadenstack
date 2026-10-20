import enum
import os
from pathlib import Path
from tempfile import gettempdir
from typing import Optional, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator, field_validator, Field, AliasChoices
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
    """
    Application settings.

    These parameters can be configured
    with environment variables.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    # quantity of workers for uvicorn
    workers_count: int = 1
    # Enable uvicorn reloading
    reload: bool = False

    # Current environment
    environment: str = "production"

    log_level: LogLevel = LogLevel.INFO
    # Variables for the database
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_user: str = "faden_pii"
    db_pass: str = "faden_pii"
    db_base: str = "admin"
    db_echo: bool = False
    # Per process, not per CPU. Every uvicorn worker of every service holds
    # its own pool, and all of them share one Postgres (max_connections 100
    # by default). Sized from the CPU count, one gateway worker on a
    # 64-thread host could open 384 connections and locked every other
    # service out (2026-09-25). 5 kept + 10 on demand is SQLAlchemy's default.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # Variables for Redis (optional — empty host disables Redis)
    redis_host: str = ""
    redis_port: int = 6379
    redis_user: Optional[str] = None
    redis_pass: Optional[str] = None
    redis_base: Optional[int] = None

    # This variable is used to define
    # multiproc_dir. It's required for [uvi|guni]corn projects.
    # Named per service, like the backend's and gateway's. Each service clears
    # this directory on startup; with PII, MCP and skills sharing ``prom`` on
    # one host (``faden dev up --modules``), the one starting last could not
    # clear files the others held open, and exited (FileExistsError).
    prometheus_dir: Path = TEMP_DIR / "prom-pii"

    # Sentry's configuration.
    sentry_dsn: Optional[str] = None
    sentry_sample_rate: float = 1.0

    # Grpc endpoint for opentelemetry.
    # E.G. http://localhost:4317
    opentelemetry_endpoint: Optional[str] = None

    # PII detection settings
    pii_default_language: str = Field(
        default="en",
        validation_alias=AliasChoices(
            "FADEN_PII_DEFAULT_LANGUAGE", "FADEN_PII_PII_DEFAULT_LANGUAGE"
        ),
    )
    pii_score_threshold: float = Field(
        default=0.35,
        validation_alias=AliasChoices(
            "FADEN_PII_SCORE_THRESHOLD", "FADEN_PII_PII_SCORE_THRESHOLD"
        ),
    )
    pii_backend: Literal["presidio", "gliner2", "gliner-onnx"] = Field(
        default="presidio",
        validation_alias=AliasChoices("FADEN_PII_BACKEND", "FADEN_PII_PII_BACKEND"),
    )
    pii_execution_profile: Literal["cpu", "gpu-dedicated", "gpu-shared"] = Field(
        default="cpu",
        validation_alias=AliasChoices(
            "FADEN_PII_EXECUTION_PROFILE", "FADEN_PII_PII_EXECUTION_PROFILE"
        ),
    )
    pii_device: str = Field(
        default="cuda:0",
        validation_alias=AliasChoices("FADEN_PII_DEVICE", "FADEN_PII_PII_DEVICE"),
    )
    pii_precision: Literal["float32", "float16"] = Field(
        default="float32",
        validation_alias=AliasChoices("FADEN_PII_PRECISION", "FADEN_PII_PII_PRECISION"),
    )
    pii_model_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FADEN_PII_MODEL_PATH", "FADEN_PII_PII_MODEL_PATH"
        ),
    )
    pii_manifest_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FADEN_PII_MANIFEST_PATH", "FADEN_PII_PII_MANIFEST_PATH"
        ),
    )
    pii_calibration_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FADEN_PII_CALIBRATION_PATH", "FADEN_PII_PII_CALIBRATION_PATH"
        ),
    )
    pii_lock_dir: Path = Field(
        default=TEMP_DIR / "faden-pii-locks",
        validation_alias=AliasChoices("FADEN_PII_LOCK_DIR", "FADEN_PII_PII_LOCK_DIR"),
    )
    pii_nlp_models: dict[str, str] = Field(
        default={
            "en": "en_core_web_sm",
            "de": "de_core_news_sm",
            "fr": "fr_core_news_sm",
            "es": "es_core_news_sm",
        },
        validation_alias=AliasChoices(
            "FADEN_PII_NLP_MODELS", "FADEN_PII_PII_NLP_MODELS"
        ),
    )
    pii_cpu_threads: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "FADEN_PII_CPU_THREADS", "FADEN_PII_PII_CPU_THREADS"
        ),
    )
    pii_gpu_memory_mib: int = Field(
        default=4096,
        validation_alias=AliasChoices(
            "FADEN_PII_GPU_MEMORY_MIB", "FADEN_PII_PII_GPU_MEMORY_MIB"
        ),
    )
    pii_body_bytes: int = Field(
        default=1_048_576,
        validation_alias=AliasChoices(
            "FADEN_PII_BODY_BYTES", "FADEN_PII_PII_BODY_BYTES"
        ),
    )
    pii_max_units: int = Field(
        default=256,
        validation_alias=AliasChoices("FADEN_PII_MAX_UNITS", "FADEN_PII_PII_MAX_UNITS"),
    )
    pii_max_unit_bytes: int = Field(
        default=131_072,
        validation_alias=AliasChoices(
            "FADEN_PII_MAX_UNIT_BYTES", "FADEN_PII_PII_MAX_UNIT_BYTES"
        ),
    )
    pii_extracted_bytes: int = Field(
        default=524_288,
        validation_alias=AliasChoices(
            "FADEN_PII_EXTRACTED_BYTES", "FADEN_PII_PII_EXTRACTED_BYTES"
        ),
    )
    pii_mapping_entries: int = Field(
        default=2048,
        validation_alias=AliasChoices(
            "FADEN_PII_MAPPING_ENTRIES", "FADEN_PII_PII_MAPPING_ENTRIES"
        ),
    )
    pii_mapping_bytes: int = Field(
        default=262_144,
        validation_alias=AliasChoices(
            "FADEN_PII_MAPPING_BYTES", "FADEN_PII_PII_MAPPING_BYTES"
        ),
    )
    pii_max_request_windows: int = Field(
        default=64,
        validation_alias=AliasChoices(
            "FADEN_PII_MAX_REQUEST_WINDOWS", "FADEN_PII_PII_MAX_REQUEST_WINDOWS"
        ),
    )
    pii_window_tokens: int = Field(
        default=256,
        validation_alias=AliasChoices(
            "FADEN_PII_WINDOW_TOKENS", "FADEN_PII_PII_WINDOW_TOKENS"
        ),
    )
    pii_window_overlap: int = Field(
        default=24,
        validation_alias=AliasChoices(
            "FADEN_PII_WINDOW_OVERLAP", "FADEN_PII_PII_WINDOW_OVERLAP"
        ),
    )
    pii_pending_requests: int = Field(
        default=64,
        validation_alias=AliasChoices(
            "FADEN_PII_PENDING_REQUESTS", "FADEN_PII_PII_PENDING_REQUESTS"
        ),
    )
    pii_pending_windows: int = Field(
        default=512,
        validation_alias=AliasChoices(
            "FADEN_PII_PENDING_WINDOWS", "FADEN_PII_PII_PENDING_WINDOWS"
        ),
    )
    pii_pending_tokens: int = Field(
        default=131_072,
        validation_alias=AliasChoices(
            "FADEN_PII_PENDING_TOKENS", "FADEN_PII_PII_PENDING_TOKENS"
        ),
    )
    pii_pending_bytes: int = Field(
        default=33_554_432,
        validation_alias=AliasChoices(
            "FADEN_PII_PENDING_BYTES", "FADEN_PII_PII_PENDING_BYTES"
        ),
    )
    pii_max_subscribers: int = Field(
        default=64,
        validation_alias=AliasChoices(
            "FADEN_PII_MAX_SUBSCRIBERS", "FADEN_PII_PII_MAX_SUBSCRIBERS"
        ),
    )
    pii_batch_wait_ms: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "FADEN_PII_BATCH_WAIT_MS", "FADEN_PII_PII_BATCH_WAIT_MS"
        ),
    )
    pii_batch_short: int = Field(
        default=32,
        validation_alias=AliasChoices(
            "FADEN_PII_BATCH_SHORT", "FADEN_PII_PII_BATCH_SHORT"
        ),
    )
    pii_batch_long: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "FADEN_PII_BATCH_LONG", "FADEN_PII_PII_BATCH_LONG"
        ),
    )
    pii_batch_short_tokens: int = Field(
        default=4096,
        validation_alias=AliasChoices(
            "FADEN_PII_BATCH_SHORT_TOKENS", "FADEN_PII_PII_BATCH_SHORT_TOKENS"
        ),
    )
    pii_batch_long_tokens: int = Field(
        default=2048,
        validation_alias=AliasChoices(
            "FADEN_PII_BATCH_LONG_TOKENS", "FADEN_PII_PII_BATCH_LONG_TOKENS"
        ),
    )
    pii_cache_entries: int = Field(
        default=20_000,
        validation_alias=AliasChoices(
            "FADEN_PII_CACHE_ENTRIES", "FADEN_PII_PII_CACHE_ENTRIES"
        ),
    )
    pii_cache_bytes: int = Field(
        default=134_217_728,
        validation_alias=AliasChoices(
            "FADEN_PII_CACHE_BYTES", "FADEN_PII_PII_CACHE_BYTES"
        ),
    )
    pii_work_timeout_seconds: float = Field(
        default=8.0,
        validation_alias=AliasChoices(
            "FADEN_PII_WORK_TIMEOUT_SECONDS", "FADEN_PII_PII_WORK_TIMEOUT_SECONDS"
        ),
    )
    pii_event_entries: int = Field(
        default=1024,
        validation_alias=AliasChoices(
            "FADEN_PII_EVENT_ENTRIES", "FADEN_PII_PII_EVENT_ENTRIES"
        ),
    )
    pii_event_bytes: int = Field(
        default=1_048_576,
        validation_alias=AliasChoices(
            "FADEN_PII_EVENT_BYTES", "FADEN_PII_PII_EVENT_BYTES"
        ),
    )

    pii_max_recoveries: int = Field(
        default=1,
        ge=0,
        le=1,
        validation_alias=AliasChoices(
            "FADEN_PII_MAX_RECOVERIES", "FADEN_PII_PII_MAX_RECOVERIES"
        ),
    )
    pii_recovery_delay_seconds: float = Field(
        default=1,
        ge=0,
        le=5,
        validation_alias=AliasChoices(
            "FADEN_PII_RECOVERY_DELAY_SECONDS", "FADEN_PII_PII_RECOVERY_DELAY_SECONDS"
        ),
    )

    @field_validator("pii_calibration_path", mode="before")
    @classmethod
    def optional_calibration_path(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_pii_runtime(self):
        from faden_pii.cpu import available_threads

        available = available_threads()
        if "pii_cpu_threads" not in self.model_fields_set:
            self.pii_cpu_threads = min(
                2 if self.pii_execution_profile != "cpu" else 8,
                max(1, available // 4),
            )
        else:
            self.pii_cpu_threads = min(self.pii_cpu_threads, available)
        if (
            self.pii_backend == "gliner-onnx"
            and "pii_precision" not in self.model_fields_set
        ):
            self.pii_precision = "float16"
        if self.pii_backend != "presidio" and self.pii_execution_profile == "cpu":
            if "pii_batch_short" not in self.model_fields_set:
                self.pii_batch_short = 8
            if "pii_batch_long" not in self.model_fields_set:
                self.pii_batch_long = 2
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name in ("pii_cache_entries", "pii_cache_bytes", "pii_max_recoveries"):
                if value < 0:
                    raise ValueError("PII cache limits must be nonnegative")
                continue
            if (
                name.startswith("pii_")
                and type(value) is int
                and name not in ("pii_batch_wait_ms", "pii_window_overlap")
                and value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        if (
            not 0 <= self.pii_score_threshold <= 1
            or not 0 < self.pii_work_timeout_seconds <= 8
        ):
            raise ValueError("Invalid PII confidence/deadline")
        if (
            not 64 <= self.pii_window_tokens <= 256
            or not 0 <= self.pii_window_overlap <= 24
        ):
            raise ValueError("PII window exceeds validated envelope")
        if (
            self.pii_batch_short > 32
            or self.pii_batch_long > 8
            or self.pii_batch_short_tokens > 4096
            or self.pii_batch_long_tokens > 2048
            or not 0 <= self.pii_batch_wait_ms <= 5
        ):
            raise ValueError("PII batch exceeds validated envelope")
        if self.pii_backend != "presidio" and self.workers_count != 1:
            raise ValueError("Neural PII requires one HTTP worker")
        return self

    # Backend service URL for event-log forwarding
    backend_url: str = "http://127.0.0.1:8000"
    # Presented to the backend with scan events (Bearer): the backend's
    # FADEN_BACKEND_INTERNAL_TOKEN. Empty: nothing is sent (dev environment excepted).
    backend_token: str = ""

    # What the gateway, the backend and MCP must present on every route but
    # health (Bearer). Empty: every call is refused, not accepted.
    service_token: str = ""

    # RabbitMQ settings (used by taskiq worker)
    rabbit_host: str = "127.0.0.1"
    rabbit_port: int = 5672
    rabbit_user: str = "guest"
    rabbit_pass: str = "guest"  # noqa: S105
    rabbit_vhost: str = "/"

    @property
    def rabbit_url(self) -> URL:
        """Assemble RabbitMQ URL from settings."""
        return URL.build(
            scheme="amqp",
            host=self.rabbit_host,
            port=self.rabbit_port,
            user=self.rabbit_user,
            password=self.rabbit_pass,
            # The vhost is the URL's path: "/" stays "/", a name gets its slash.
            # A bare name ("faden") used to crash start-up: yarl refuses a
            # path without one next to a host.
            path="/" + self.rabbit_vhost.lstrip("/"),
        )

    @property
    def db_url(self) -> URL:
        """
        Assemble database URL from settings.

        :return: database URL.
        """
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
        """
        Assemble REDIS URL from settings.

        :return: redis URL.
        """
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
        env_prefix="FADEN_PII_",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        env_ignore_empty=True,
    )


settings = Settings()
