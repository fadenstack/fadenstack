"""Central registry — single source of truth for Fadenstack metadata.

Every component (repos, ports, modules, services, databases, dev env vars)
is defined exactly **once** here.  Consumer modules import from this file
instead of hard-coding values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# ── GitHub organisation ───────────────────────────────────────────

GITHUB_ORG = "fadenstack"
CORE_REPO = "fadenstack"

# ── Container registry ───────────────────────────────────────────

DEFAULT_REGISTRY = "ghcr.io/fadenstack"


# ── Repository definitions ───────────────────────────────────────


@dataclass(frozen=True)
class RepoInfo:
    """Metadata for a single Fadenstack repository."""

    github_name: str  # e.g. "faden-backend"
    local_dir: str  # e.g. "backend"
    description: str = ""


REPOS: list[RepoInfo] = [
    # ── Core platform ─────────────────────────────────────────
    RepoInfo("faden-backend", "backend", "Backend API server"),
    RepoInfo("faden-frontend", "frontend", "React frontend"),
    RepoInfo("faden-gateway", "gateway", "LLM Gateway API"),
    RepoInfo("faden-pii", "pii", "PII redaction service"),
    RepoInfo("faden-mcp", "mcp", "MCP Tool Registry"),
    RepoInfo("faden-skills", "skills", "Skills Registry"),
    RepoInfo("faden-shared", "shared", "Shared infra (compose)"),
    RepoInfo("faden-cli", "cli", "CLI"),
]

REPO_DIR_MAP: dict[str, str] = {r.github_name: r.local_dir for r in REPOS}
"""Map GitHub repo name → local directory name."""

REPO_NAMES: list[str] = [r.github_name for r in REPOS]
"""All core repository names."""


# ── Module definitions ────────────────────────────────────────────


@dataclass(frozen=True)
class ModuleInfo:
    """An optional module that can be toggled via compose profiles."""

    name: str
    profile: str
    description: str
    container: str = ""
    port: int = 0
    service_url: str = ""
    env_vars: tuple[tuple[str, str], ...] = ()
    # ── How ``faden dev up`` runs it: on the host, like the backend ──
    #: The service directory it runs from; empty for a module dev mode
    #: does not run.
    dev_dir: str = ""
    #: Its own database, migrated with Alembic from ``dev_dir``.
    database: str = ""
    #: The Redis database number it uses, when it uses Redis.
    redis_base: int | None = None
    #: The token the gateway and backend present to it, in dev. The shared
    #: ``.env`` overrides it (``FADEN_<NAME>_SERVICE_TOKEN``).
    dev_service_token: str = ""
    #: What the backend's URL for it adds to the service root.
    backend_url_suffix: str = ""
    #: Settings of its own in dev, beyond host, port and credentials.
    dev_settings: tuple[tuple[str, str], ...] = ()
    #: Its settings that hold another module's URL: (setting, module name).
    dev_links: tuple[tuple[str, str], ...] = ()
    #: What it imports that its lock file cannot carry: (import name, wheel).
    extra_wheels: tuple[tuple[str, str], ...] = ()
    #: The secrets it needs in an install's ``.env``. Each group gets one
    #: random value, shared by every key in it: a token the gateway, the
    #: backend and the module itself all hold.
    secret_groups: tuple[tuple[str, ...], ...] = ()


MODULES: dict[str, ModuleInfo] = {
    "pii": ModuleInfo(
        name="pii",
        profile="pii",
        description="PII detection and redaction (Presidio + spaCy)",
        container="faden-pii",
        port=8003,
        service_url="http://faden-pii:8000",
        env_vars=(
            ("FADEN_GATEWAY_PII_ENABLED", "true"),
            ("FADEN_GATEWAY_PII_SERVICE_URL", "http://faden-pii:8000"),
            # The compose file defaults it to false, which the console's
            # module switch then cannot see past.
            ("FADEN_BACKEND_PII_ENABLED", "true"),
        ),
        dev_dir="pii",
        backend_url_suffix="/api",
        # The spaCy model Presidio loads. Not a PyPI package, so the lock
        # file cannot carry it; the image installs the same wheel.
        extra_wheels=((
            "en_core_web_lg",
            "https://github.com/explosion/spacy-models/releases/download/"
            "en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl",
        ),),
    ),
    "mcp": ModuleInfo(
        name="mcp",
        profile="mcp",
        description="MCP Tool Registry (governed MCP server broker)",
        container="faden-mcp",
        port=8007,
        service_url="http://faden-mcp:8000",
        env_vars=(
            ("FADEN_GATEWAY_MCP_ENABLED", "true"),
            ("FADEN_GATEWAY_MCP_SERVICE_URL", "http://faden-mcp:8000"),
            ("FADEN_BACKEND_MCP_ENABLED", "true"),
            ("FADEN_BACKEND_MCP_SERVICE_URL", "http://faden-mcp:8000"),
        ),
        dev_dir="mcp",
        database="faden_mcp",
        redis_base=3,
        dev_service_token="dev-mcp-service-token",
        # Fixed, like every other dev secret: it encrypts the MCP servers'
        # credentials in faden_mcp, which outlive a regenerated .env.
        dev_settings=(("ENCRYPTION_KEY", "dev-mcp-encryption-key-change-me-32b"),),
        dev_links=(("PII_SERVICE_URL", "pii"),),
        # Its own key in an install: the compose file falls back to the dev
        # key above, the same for every install that has none.
        secret_groups=(
            ("FADEN_MCP_ENCRYPTION_KEY",),
            (
                "FADEN_GATEWAY_MCP_SERVICE_TOKEN",
                "FADEN_BACKEND_MCP_SERVICE_TOKEN",
                "FADEN_MCP_SERVICE_TOKEN",
            ),
        ),
    ),
    "skills": ModuleInfo(
        name="skills",
        profile="skills",
        description="Skills Registry (centralized LLM instruction sets)",
        container="faden-skills",
        port=8008,
        service_url="http://faden-skills:8000",
        env_vars=(
            ("FADEN_GATEWAY_SKILLS_ENABLED", "true"),
            ("FADEN_GATEWAY_SKILLS_SERVICE_URL", "http://faden-skills:8000"),
            ("FADEN_BACKEND_SKILLS_ENABLED", "true"),
            ("FADEN_BACKEND_SKILLS_SERVICE_URL", "http://faden-skills:8000"),
        ),
        dev_dir="skills",
        database="faden_skills",
        redis_base=4,
        dev_service_token="dev-skills-service-token",
        secret_groups=(
            (
                "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN",
                "FADEN_BACKEND_SKILLS_SERVICE_TOKEN",
                "FADEN_SKILLS_SERVICE_TOKEN",
            ),
        ),
    ),
}

# Legacy dict-of-dicts shape consumed by commands/module.py
MODULES_COMPAT: dict[str, dict[str, str]] = {
    name: {"profile": m.profile, "description": m.description}
    for name, m in MODULES.items()
}


# ── Port registry ────────────────────────────────────────────────

KNOWN_PORTS: list[tuple[int, str]] = [
    # Core infrastructure
    (5432, "PostgreSQL"),
    (5672, "RabbitMQ AMQP"),
    (15672, "RabbitMQ Management"),
    (6379, "Redis"),
    (9000, "MinIO API"),
    (9001, "MinIO Console"),
    # Observability
    (3001, "Grafana"),
    (3100, "Loki"),
    (12345, "Alloy (OpenTelemetry)"),
    # Application services
    (8000, "Backend"),
    (5173, "Frontend (dev)"),
    (8001, "Gateway"),
    (8003, "PII Service"),
    (8007, "MCP Tool Registry"),
    (8008, "Skills Registry"),
    # Tools
    (5050, "pgAdmin"),
    (3000, "Langfuse"),
    (8123, "ClickHouse HTTP"),
    (9181, "ClickHouse Native"),
]


# ── Database registry ────────────────────────────────────────────

DATABASES: list[str] = [
    "faden_backend",
    "faden_gateway",
    "pii",
    "faden_mcp",
    "faden_skills",
    "langfuse",
]

POSTGRES_CONTAINER = "faden-postgres"

# Services that use pre-built public images and should be started in dev mode.
# Application services (backend, frontend, api) are run natively on the host.
INFRA_SERVICES: list[str] = [
    "postgres",
    "redis",
    "minio",
    "clickhouse",
    "langfuse-worker",
    "langfuse-web",
    "faden-rmq",
    "loki",
    "grafana",
    "alloy",
]


# ── Dev backend env vars ─────────────────────────────────────────

BACKEND_DEV_ENV: dict[str, str] = {
    # Bind the dev backend to all interfaces so it is reachable from
    # other machines on the LAN (remote dev server, node agents,
    # external API clients). Override with FADEN_BACKEND_HOST=
    # 127.0.0.1 in the local .env to keep it local-only.
    "FADEN_BACKEND_HOST": "0.0.0.0",
    "FADEN_BACKEND_PORT": "8000",
    # Session cookies must not carry the Secure flag in dev: browsers
    # drop Secure cookies over plain HTTP, which would silently break
    # login on a remote LAN access (HTTP, no TLS). Prod keeps the
    # default (True) behind TLS/nginx.
    "FADEN_BACKEND_COOKIE_SECURE": "false",
    "FADEN_BACKEND_ENVIRONMENT": "dev",
    # The dev API gateway runs on the host's port 8001.
    "FADEN_BACKEND_GATEWAY_URL": "http://127.0.0.1:8001",
    "FADEN_BACKEND_RELOAD": "true",
    # 127.0.0.1, never "localhost": see INFRA_LOOPBACK.
    "FADEN_BACKEND_DB_HOST": "127.0.0.1",
    "FADEN_BACKEND_DB_PORT": "5432",
    "FADEN_BACKEND_DB_USER": "faden_backend",
    "FADEN_BACKEND_DB_PASS": "faden_backend",
    "FADEN_BACKEND_DB_BASE": "faden_backend",
    "FADEN_BACKEND_RABBIT_HOST": "127.0.0.1",
    "FADEN_BACKEND_RABBIT_PORT": "5672",
    # Matches the per-service AMQP user created in
    # shared/rabbitmq/definitions.json by `faden dev init`
    # (see backend_dev_env_for for the password).
    "FADEN_BACKEND_RABBIT_USER": "faden-backend",
    "FADEN_BACKEND_RABBIT_PASS": "devpassword",
    "FADEN_BACKEND_RABBIT_VHOST": "/",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY": "dev-settings-master-key-change-me",
}


#: The address the host-run dev services reach the shared infra on.
#:
#: Postgres, Redis and RabbitMQ are published on 127.0.0.1 only
#: (``shared/docker-compose.yaml``). "localhost" resolves to ::1
#: first on Windows, where nothing listens -- and with WSL mirrored
#: networking the SYN to ::1 is not refused but dropped, so every new
#: connection waited out Windows' 21 s connect retry before falling back to
#: IPv4. The gateway opens connections per worker as bursts need them, so
#: chat requests stalled 21 s at random: measured 22,065 ms for a 4-token
#: reply that the engine produced in 90 ms.
INFRA_LOOPBACK = "127.0.0.1"

#: The keys that carry that address, in the backend and gateway env files.
INFRA_HOST_KEYS: tuple[str, ...] = (
    "FADEN_BACKEND_DB_HOST",
    "FADEN_BACKEND_RABBIT_HOST",
    "FADEN_GATEWAY_DB_HOST",
    "FADEN_GATEWAY_REDIS_HOST",
    "FADEN_GATEWAY_RABBIT_HOST",
)


#: Default env for the API gateway dev process (``faden dev up``).
#:
#: The gateway is the OpenAI-compatible ``/v1`` edge. In dev it runs as a
#: host process on port 8001 (bound to all interfaces, mirroring the
#: backend) and points at the shared 127.0.0.1-published infra (postgres
#: / redis / rabbit). Chat-file uploads go to a host-local directory
#: instead of the containerized ``/data/faden`` volume.
GATEWAY_DEV_ENV: dict[str, str] = {
    "FADEN_GATEWAY_HOST": "0.0.0.0",
    "FADEN_GATEWAY_PORT": "8001",
    # 127.0.0.1, never "localhost": see INFRA_LOOPBACK.
    "FADEN_GATEWAY_DB_HOST": "127.0.0.1",
    "FADEN_GATEWAY_DB_PORT": "5432",
    "FADEN_GATEWAY_REDIS_HOST": "127.0.0.1",
    "FADEN_GATEWAY_REDIS_PORT": "6379",
    "FADEN_GATEWAY_RABBIT_HOST": "127.0.0.1",
    "FADEN_GATEWAY_RABBIT_PORT": "5672",
    "FADEN_GATEWAY_RAG_LITE_BACKEND_URL": "http://127.0.0.1:8000",
    "FADEN_GATEWAY_CHAT_FILE_STORE_ROOT": "~/.faden-dev/chat-files",
}

# ── Dev process patterns (for status checks) ─────────────────────


@dataclass(frozen=True)
class DevProcess:
    """A dev-mode process pattern for ``faden dev status``."""

    name: str
    pattern: str
    url: str = "—"


DEV_PROCESSES: list[DevProcess] = [
    DevProcess("Backend", "faden_backend", "http://localhost:8000"),
    DevProcess("Worker", "taskiq worker", "—"),
    DevProcess("Frontend", "npm run dev", "http://localhost:5173"),
    DevProcess("API gateway", "faden_gateway", "http://localhost:8001"),
    # Optional modules (``faden dev up --modules``).
    DevProcess("PII", "faden_pii", "http://127.0.0.1:8003"),
    DevProcess("MCP", "faden_mcp", "http://127.0.0.1:8007"),
    DevProcess("Skills", "faden_skills", "http://127.0.0.1:8008"),
]


# ── Dev endpoint summary (for ``faden dev up``) ────────────────

DEV_ENDPOINTS: list[tuple[str, str]] = [
    ("Backend", "http://localhost:8000"),
    ("API Docs", "http://localhost:8000/api/docs"),
    ("Worker", "Taskiq (RabbitMQ)"),
    ("Frontend", "http://localhost:5173"),
    ("Grafana", "http://localhost:3001"),
    ("RabbitMQ", "http://localhost:15672"),
    ("LLM API", "http://localhost:8001"),
    ("PII", "http://127.0.0.1:8003"),
    ("MCP Registry", "http://127.0.0.1:8007"),
    ("Skills Registry", "http://127.0.0.1:8008"),
]

#: The endpoints above that exist only while their module runs.
DEV_MODULE_ENDPOINTS: dict[str, str] = {
    "PII": "pii",
    "MCP Registry": "mcp",
    "Skills Registry": "skills",
}


# ── Convenience helpers ──────────────────────────────────────────


def repo_clone_url(repo: str, *, method: str = "https", token: str = "") -> str:
    """Return the git clone URL for a repo in the Fadenstack org.

    When *token* is provided and *method* is ``"https"``, the token is
    embedded in the URL so that ``git clone`` can access private repos.
    """
    if method == "ssh":
        return f"git@github.com:{GITHUB_ORG}/{repo}.git"
    if token:
        return f"https://x-access-token:{token}@github.com/{GITHUB_ORG}/{repo}.git"
    return f"https://github.com/{GITHUB_ORG}/{repo}"


def core_repo_clone_url(*, method: str = "https", token: str = "") -> str:
    """Return the git clone URL for the core monorepo."""
    return repo_clone_url(CORE_REPO, method=method, token=token)


def module_env_vars(profile: str) -> dict[str, str]:
    """Return the env vars needed to enable a module by profile name.

    Used by ``env_gen.dev_env_vars()`` and ``env_gen.default_env_vars()``
    to inject module-specific settings into generated ``.env`` files.

    Each ``ModuleInfo`` carries its own ``env_vars`` tuple so that
    enterprise modules registered via ``extend_modules()`` are handled
    automatically — no hard-coded profile branches needed.
    """
    mod = MODULES.get(profile)
    if not mod:
        return {}
    return dict(mod.env_vars)


# ── Extension API (used by faden-ee to layer on EE data) ────────


def _rebuild_derived() -> None:
    """Rebuild derived dicts/lists after extending REPOS."""
    REPO_DIR_MAP.clear()
    REPO_DIR_MAP.update({r.github_name: r.local_dir for r in REPOS})
    REPO_NAMES.clear()
    REPO_NAMES.extend(r.github_name for r in REPOS)
    MODULES_COMPAT.clear()
    MODULES_COMPAT.update(
        {name: {"profile": m.profile, "description": m.description} for name, m in MODULES.items()}
    )


def extend_repos(extra: list[RepoInfo]) -> None:
    """Append repositories to the registry (idempotent)."""
    existing = {r.github_name for r in REPOS}
    for repo in extra:
        if repo.github_name not in existing:
            REPOS.append(repo)
            existing.add(repo.github_name)
    _rebuild_derived()


def extend_modules(extra: dict[str, ModuleInfo]) -> None:
    """Add modules to the registry (idempotent)."""
    for name, mod in extra.items():
        if name not in MODULES:
            MODULES[name] = mod
    _rebuild_derived()


def extend_ports(extra: list[tuple[int, str]]) -> None:
    """Append ports to the known-ports list (idempotent)."""
    existing = {p for p, _ in KNOWN_PORTS}
    for port, label in extra:
        if port not in existing:
            KNOWN_PORTS.append((port, label))
            existing.add(port)


def extend_databases(extra: list[str]) -> None:
    """Append database names to the registry (idempotent)."""
    existing = set(DATABASES)
    for db in extra:
        if db not in existing:
            DATABASES.append(db)
            existing.add(db)


def extend_dev_endpoints(extra: list[tuple[str, str]]) -> None:
    """Append dev endpoint entries (idempotent by name)."""
    existing = {name for name, _ in DEV_ENDPOINTS}
    for name, url in extra:
        if name not in existing:
            DEV_ENDPOINTS.append((name, url))
            existing.add(name)


def extend_dev_processes(extra: list[DevProcess]) -> None:
    """Append dev process patterns (idempotent by name)."""
    existing = {p.name for p in DEV_PROCESSES}
    for proc in extra:
        if proc.name not in existing:
            DEV_PROCESSES.append(proc)
            existing.add(proc.name)


def _read_env_values(path: Path) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` env file into a dict."""
    path = Path(path)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def backend_dev_env_for(shared_env_path: Path) -> dict[str, str]:
    """Build the backend dev ``.env`` with correct RabbitMQ credentials.

    The broker users are loaded from
    ``shared/rabbitmq/definitions.json``, which dev init regenerates
    from the shared ``.env`` passwords. The backend must therefore log in as
    ``faden-backend`` with ``RABBITMQ_BACKEND_PASS`` — ``BACKEND_DEV_ENV``'s
    historical ``guest/guest`` default does not exist once definitions are
    loaded (RabbitMQ reports ``ACCESS_REFUSED``).
    """
    env = dict(BACKEND_DEV_ENV)
    shared = _read_env_values(shared_env_path)
    backend_pass = shared.get("RABBITMQ_BACKEND_PASS")
    if backend_pass:
        env["FADEN_BACKEND_RABBIT_USER"] = "faden-backend"
        env["FADEN_BACKEND_RABBIT_PASS"] = backend_pass
        return env
    # Shared env predates per-service users — the only account guaranteed to
    # exist on the broker is the one the shared env configures for it.
    if shared.get("RABBITMQ_ADMIN_USER") and shared.get("RABBITMQ_ADMIN_PASS"):
        env["FADEN_BACKEND_RABBIT_USER"] = shared["RABBITMQ_ADMIN_USER"]
        env["FADEN_BACKEND_RABBIT_PASS"] = shared["RABBITMQ_ADMIN_PASS"]
    return env


def gateway_dev_env_for(shared_env_path: Path) -> dict[str, str]:
    """Build the gateway dev ``.env`` with credentials from the shared env.

    Mirrors :func:`backend_dev_env_for`: DB credentials come from
    ``POSTGRES_USER`` / ``POSTGRES_PASSWORD`` in
    ``shared/.env``, the RabbitMQ broker login from
    ``RABBITMQ_API_PASS`` as user ``faden-gateway`` (the per-service user
    that ``definitions.json`` creates; ``RABBITMQ_API_USER`` overrides
    the name, and the admin pair is used only for pre-per-service-user
    envs that have no ``RABBITMQ_API_PASS``), and the Redis password
    from ``REDIS_AUTH`` (the key ``dev init`` writes; ``REDIS_PASSWORD``
    is the prod-template alias and is kept as a fallback). Returns
    ``GATEWAY_DEV_ENV`` with the defaults when the shared env does not
    exist.
    """
    env = dict(GATEWAY_DEV_ENV)
    shared = _read_env_values(shared_env_path)
    if not shared:
        return env
    if shared.get("POSTGRES_USER"):
        env["FADEN_GATEWAY_DB_USER"] = shared["POSTGRES_USER"]
    if shared.get("POSTGRES_PASSWORD"):
        env["FADEN_GATEWAY_DB_PASS"] = shared["POSTGRES_PASSWORD"]
    api_pass = shared.get("RABBITMQ_API_PASS")
    if api_pass:
        # Env generation never writes RABBITMQ_API_USER — the per-service
        # user name is fixed by the definitions.json generator, so key off
        # the password and default the name (as backend_dev_env_for does).
        env["FADEN_GATEWAY_RABBIT_USER"] = shared.get("RABBITMQ_API_USER", "faden-gateway")
        env["FADEN_GATEWAY_RABBIT_PASS"] = api_pass
    elif shared.get("RABBITMQ_ADMIN_USER") and shared.get("RABBITMQ_ADMIN_PASS"):
        env["FADEN_GATEWAY_RABBIT_USER"] = shared["RABBITMQ_ADMIN_USER"]
        env["FADEN_GATEWAY_RABBIT_PASS"] = shared["RABBITMQ_ADMIN_PASS"]
    redis_pass = shared.get("REDIS_AUTH") or shared.get("REDIS_PASSWORD")
    if redis_pass:
        env["FADEN_GATEWAY_REDIS_PASS"] = redis_pass
    return env


def extend_backend_dev_env(extra: dict[str, str]) -> None:
    """Merge extra env vars into ``BACKEND_DEV_ENV`` (idempotent).

    Existing keys are **not** overwritten.  This allows the EE CLI
    overlay to inject ``FADEN_EE_*`` variables that will be written
    to the backend ``.env`` file by ``dev init`` and ``dev up``.
    """
    for key, value in extra.items():
        BACKEND_DEV_ENV.setdefault(key, value)
