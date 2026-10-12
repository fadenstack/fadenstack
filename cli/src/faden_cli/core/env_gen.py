""".env file generation from Jinja2 templates.

Generates a production ``.env`` file with cryptographically random
secrets replacing every ``CHANGEME_*`` placeholder in the template.
The user can then override individual values via ``faden config set``.
"""

from __future__ import annotations

import secrets
import string
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from faden_cli.core.private_files import write_private
from faden_cli.core.registry import module_env_vars
from faden_cli.core.sysinfo import calculate_tune_profile

# Keys that contain cryptographic secrets. These must never be replaced
# when regenerating an .env file because changing them would make
# previously encrypted data (chat content, DB settings, etc.) unreadable.
SECRET_KEYS: frozenset[str] = frozenset({
    "POSTGRES_PASSWORD",
    "REDIS_AUTH",
    "MINIO_ROOT_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
    "RABBITMQ_ADMIN_PASS",
    "RABBITMQ_BACKEND_PASS",
    "RABBITMQ_API_PASS",
    "RABBITMQ_PII_PASS",
    "LANGFUSE_NEXTAUTH_SECRET",
    "LANGFUSE_SALT",
    "LANGFUSE_ENCRYPTION_KEY",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY",
    "FADEN_GATEWAY_ENCRYPTION_KEY",
    "FADEN_MCP_ENCRYPTION_KEY",
    "USERS_SECRET",
    # Inter-service auth tokens — each downstream service has one shared
    # value across (api → service), (backend → service), (service receiver).
    # All three keys per service MUST stay in lock-step, so they're listed
    # together here to be preserved as a group on .env regeneration.
    "FADEN_GATEWAY_MCP_SERVICE_TOKEN",
    "FADEN_BACKEND_MCP_SERVICE_TOKEN",
    "FADEN_MCP_SERVICE_TOKEN",
    "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN",
    "FADEN_BACKEND_SKILLS_SERVICE_TOKEN",
    "FADEN_SKILLS_SERVICE_TOKEN",
    "FADEN_BACKEND_RAG_SERVICE_TOKEN",
    "FADEN_RAG_SERVICE_TOKEN",
    # The PII service reports its scan events to the backend with this.
    "FADEN_BACKEND_INTERNAL_TOKEN",
    "FADEN_PII_BACKEND_TOKEN",
    "FADEN_BACKEND_BOOTSTRAP_TOKEN",
})

#: What the backend asks for before it creates the first administrator (``POST /api/bootstrap``), sent by
#: ``faden deploy``. Without it bootstrap is refused: the first admin went to whoever asked first.
#: Settings deploy writes with their defaults and upgrade adds to an older .env (the compose file has the same
#: fallbacks). Rotation of every container's Docker log: the largest file and how many are kept per container,
#: so 50m x 5 is at most 250 MB each. FADEN_DATA_DIR: empty keeps the company data (chat attachments) in
#: Docker named volumes; a directory puts it there instead (Help: Installing, Where the data is kept).
DEPLOY_SETTINGS: dict[str, str] = {
    "FADEN_LOG_MAX_SIZE": "50m",
    "FADEN_LOG_MAX_FILE": "5",
    "FADEN_DATA_DIR": "",
}

BOOTSTRAP_TOKEN_KEY = "FADEN_BACKEND_BOOTSTRAP_TOKEN"  # noqa: S105 - the variable's name

#: What the compose file has no fallback for (``${NAME:?...}``): compose stops on any of them missing, for
#: every command, whichever services it starts. It fell back to development values published with the
#: source code; ``faden deploy`` writes them all, ``faden upgrade`` adds what an older ``.env`` lacks
#: (:func:`fill_required_env`), and ``faden dev up`` gives a development ``.env`` the development ones.
REQUIRED_KEYS: tuple[str, ...] = (
    "POSTGRES_PASSWORD",
    "REDIS_AUTH",
    "MINIO_ROOT_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "LANGFUSE_NEXTAUTH_SECRET",
    "LANGFUSE_SALT",
    "LANGFUSE_ENCRYPTION_KEY",
    "RABBITMQ_ADMIN_PASS",
    "RABBITMQ_API_PASS",
    "RABBITMQ_BACKEND_PASS",
    "RABBITMQ_PII_PASS",
    "GRAFANA_ADMIN_PASSWORD",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY",
    "FADEN_MCP_ENCRYPTION_KEY",
    "FADEN_GATEWAY_MCP_SERVICE_TOKEN",
    "FADEN_BACKEND_MCP_SERVICE_TOKEN",
    "FADEN_MCP_SERVICE_TOKEN",
    "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN",
    "FADEN_BACKEND_SKILLS_SERVICE_TOKEN",
    "FADEN_SKILLS_SERVICE_TOKEN",
)

#: Keys that seal stored data, with the value the compose file used when ``.env`` had none. They are
#: published with the source code, and an install that ran on one reads its data only with it: an upgrade
#: that finds one missing writes this value, never a random one.
PUBLISHED_DATA_KEYS: dict[str, str] = {
    "FADEN_BACKEND_SETTINGS_MASTER_KEY": "dev-settings-master-key-change-me",
    "FADEN_MCP_ENCRYPTION_KEY": "dev-mcp-encryption-key-change-me-32b",
    "LANGFUSE_SALT": "mysalt",
    "LANGFUSE_ENCRYPTION_KEY": "0" * 64,
}

#: Lets the backend, the gateway and MCP start with a published key from :data:`PUBLISHED_DATA_KEYS`
#: (which they refuse otherwise), for an install that stored its data under it.
ALLOW_PUBLISHED_KEYS = "FADEN_ALLOW_PUBLISHED_KEYS"

#: Of the published keys, the ones the services refuse without :data:`ALLOW_PUBLISHED_KEYS`.
_REFUSED_DATA_KEYS = ("FADEN_BACKEND_SETTINGS_MASTER_KEY", "FADEN_MCP_ENCRYPTION_KEY")

#: Keys that must hold one value: what the gateway and the backend present, and what the module expects.
_TOKEN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("FADEN_GATEWAY_MCP_SERVICE_TOKEN", "FADEN_BACKEND_MCP_SERVICE_TOKEN", "FADEN_MCP_SERVICE_TOKEN"),
    ("FADEN_GATEWAY_SKILLS_SERVICE_TOKEN", "FADEN_BACKEND_SKILLS_SERVICE_TOKEN", "FADEN_SKILLS_SERVICE_TOKEN"),
)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a dict, ignoring comments and blanks."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key:
            result[key.strip()] = value.strip()
    return result


def _random_password(length: int = 32) -> str:
    """Generate a URL-safe random password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _random_hex(length: int = 64) -> str:
    """Generate a hex string of the given length."""
    return secrets.token_hex(length // 2)


def _random_secret(length: int = 48) -> str:
    """Generate a URL-safe base64 secret."""
    return secrets.token_urlsafe(length)


# ── Dev-mode defaults (matching existing shared .env) ─────────────


def dev_env_vars(
    *,
    profiles: list[str] | None = None,
) -> dict[str, str]:
    """Build env vars with standard dev credentials.

    Uses ``devpassword`` everywhere — matches the existing
    ``shared/.env`` used during development.
    """
    pw = "devpassword"

    env = {
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": pw,
        "POSTGRES_DB": "postgres",
        "GRAFANA_ADMIN_USER": "admin",
        "GRAFANA_ADMIN_PASSWORD": pw,
        "LANGFUSE_NEXTAUTH_SECRET": "dev-langfuse-nextauth-secret",
        "LANGFUSE_SALT": "dev-langfuse-salt",
        "LANGFUSE_ENCRYPTION_KEY": "0" * 64,
        "LANGFUSE_DATABASE_URL": f"postgresql://postgres:{pw}@postgres:5432/langfuse",
        "CLICKHOUSE_USER": "clickhouse",
        "CLICKHOUSE_PASSWORD": pw,
        "CLICKHOUSE_MIGRATION_URL": "clickhouse://clickhouse:9000",
        "CLICKHOUSE_URL": "http://clickhouse:8123",
        "REDIS_AUTH": pw,
        "MINIO_ROOT_USER": "minio",
        "MINIO_ROOT_PASSWORD": pw,
        "LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT": "http://minio:9000",
        "LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT": "http://minio:9000",
        # RabbitMQ — admin for management console, per-service for AMQP
        "RABBITMQ_ADMIN_USER": "admin",
        "RABBITMQ_ADMIN_PASS": pw,
        "RABBITMQ_BACKEND_PASS": pw,
        "RABBITMQ_API_PASS": pw,
        "RABBITMQ_PII_PASS": pw,
        # Settings master key — used to encrypt/decrypt secrets stored in the DB.
        # Must match FADEN_BACKEND_SETTINGS_MASTER_KEY in the backend .env.
        # JWT secrets are seeded automatically in the DB on first startup.
        "FADEN_BACKEND_SETTINGS_MASTER_KEY": "dev-settings-master-key-change-me",
        "FADEN_GATEWAY_ENCRYPTION_KEY": "dev-encryption-key-change-me",
        # The modules' development tokens and key, the ones `faden dev up` runs them with
        # (registry.MODULES); the compose file requires them although dev mode runs the
        # modules on the host.
        **_dev_module_secrets(),
        "FADEN_GATEWAY_LANGFUSE_ENABLED": "false",
        "FADEN_BACKEND_GATEWAY_URL": "http://faden-gateway:8000",
        # Dev stacks opt IN to dev mode (seeded admin, /auth/dev-login); the
        # services and compose default to production.
        "FADEN_BACKEND_ENVIRONMENT": "dev",
        "FADEN_GATEWAY_ENVIRONMENT": "dev",
        "FADEN_PII_ENVIRONMENT": "dev",
        "FADEN_MCP_ENVIRONMENT": "dev",
        "FADEN_SKILLS_ENVIRONMENT": "dev",
        # Langfuse initial admin seeding
        "LANGFUSE_INIT_USER_EMAIL": "",
        "LANGFUSE_INIT_USER_PASSWORD": "",
        "LANGFUSE_INIT_USER_NAME": "Admin",
        "LANGFUSE_INIT_ORG_ID": "faden-org",
        "LANGFUSE_INIT_ORG_NAME": "fadenstack",
        "LANGFUSE_INIT_PROJECT_ID": "faden-project",
        "LANGFUSE_INIT_PROJECT_NAME": "fadenstack",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "",
    }

    # Scalability tuning (conservative dev defaults)
    env.update(calculate_tune_profile("dev").to_env_dict())

    profs = profiles or []
    for prof in profs:
        env.update(module_env_vars(prof))

    return env


def _dev_module_secrets() -> dict[str, str]:
    """The MCP and skills tokens and the MCP key a development stack uses."""
    from faden_cli.core.registry import MODULES  # noqa: PLC0415

    mcp, skills = MODULES["mcp"], MODULES["skills"]
    return {
        "FADEN_GATEWAY_MCP_SERVICE_TOKEN": mcp.dev_service_token,
        "FADEN_BACKEND_MCP_SERVICE_TOKEN": mcp.dev_service_token,
        "FADEN_MCP_SERVICE_TOKEN": mcp.dev_service_token,
        "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN": skills.dev_service_token,
        "FADEN_BACKEND_SKILLS_SERVICE_TOKEN": skills.dev_service_token,
        "FADEN_SKILLS_SERVICE_TOKEN": skills.dev_service_token,
        "FADEN_MCP_ENCRYPTION_KEY": dict(mcp.dev_settings)["ENCRYPTION_KEY"],
    }


# ── Production defaults (random secrets) ──────────────────────────


def default_env_vars(
    *,
    profiles: list[str] | None = None,
) -> dict[str, str]:
    """Build a dict of env vars with generated secrets.

    These correspond to the placeholders in the ``.env.j2`` template.
    """
    postgres_password = _random_password(24)
    redis_password = _random_password(24)
    minio_password = _random_password(24)

    # Inter-service auth tokens. One random value per downstream service,
    # written to all three env vars (api → service, backend → service,
    # service receiver) so they stay in lock-step. The receiver-side
    # ``verify_service_token`` fails open when its env var is empty, so it
    # is critical that these are generated and propagated for production
    # deployments.
    mcp_service_token = _random_secret()
    skills_service_token = _random_secret()
    rag_service_token = _random_secret()
    internal_token = _random_secret()

    env = {
        # Postgres
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_DB": "postgres",
        # Grafana
        "GRAFANA_ADMIN_USER": "admin",
        "GRAFANA_ADMIN_PASSWORD": _random_password(16),
        # Langfuse
        "LANGFUSE_NEXTAUTH_SECRET": _random_secret(),
        "LANGFUSE_SALT": _random_secret(32),
        "LANGFUSE_ENCRYPTION_KEY": _random_hex(64),
        "LANGFUSE_DATABASE_URL": f"postgresql://postgres:{postgres_password}@postgres:5432/langfuse",
        # ClickHouse
        "CLICKHOUSE_USER": "clickhouse",
        "CLICKHOUSE_PASSWORD": _random_password(24),
        # Redis
        "REDIS_AUTH": redis_password,
        # MinIO
        "MINIO_ROOT_USER": "minio",
        "MINIO_ROOT_PASSWORD": minio_password,
        # RabbitMQ — admin for management console, per-service for AMQP
        "RABBITMQ_ADMIN_USER": "admin",
        "RABBITMQ_ADMIN_PASS": _random_password(24),
        "RABBITMQ_BACKEND_PASS": _random_password(24),
        "RABBITMQ_API_PASS": _random_password(24),
        "RABBITMQ_PII_PASS": _random_password(24),
        # Settings master key — used to encrypt/decrypt secrets stored in the DB.
        # JWT secrets are seeded automatically in the DB on first startup.
        "FADEN_BACKEND_SETTINGS_MASTER_KEY": _random_secret(),
        "FADEN_GATEWAY_ENCRYPTION_KEY": _random_secret(),
        "FADEN_GATEWAY_LANGFUSE_ENABLED": "false",
        "FADEN_BACKEND_GATEWAY_URL": "http://faden-gateway:8000",
        "FADEN_BACKEND_ENVIRONMENT": "production",
        "FADEN_GATEWAY_ENVIRONMENT": "production",
        "FADEN_PII_ENVIRONMENT": "production",
        "FADEN_MCP_ENVIRONMENT": "production",
        "FADEN_SKILLS_ENVIRONMENT": "production",
        "USERS_SECRET": _random_secret(),
        **DEPLOY_SETTINGS,
        # Inter-service auth tokens (see comment above). Each pair of
        # caller env vars must equal the receiver's env var.
        "FADEN_GATEWAY_MCP_SERVICE_TOKEN": mcp_service_token,
        "FADEN_BACKEND_MCP_SERVICE_TOKEN": mcp_service_token,
        "FADEN_MCP_SERVICE_TOKEN": mcp_service_token,
        "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN": skills_service_token,
        "FADEN_BACKEND_SKILLS_SERVICE_TOKEN": skills_service_token,
        "FADEN_SKILLS_SERVICE_TOKEN": skills_service_token,
        "FADEN_BACKEND_RAG_SERVICE_TOKEN": rag_service_token,
        "FADEN_RAG_SERVICE_TOKEN": rag_service_token,
        "FADEN_BACKEND_INTERNAL_TOKEN": internal_token,
        "FADEN_PII_BACKEND_TOKEN": internal_token,
        # Encrypts the credentials MCP stores. Without it the compose file
        # falls back to a fixed dev key, the same for every install.
        "FADEN_MCP_ENCRYPTION_KEY": _random_secret(),
        # Asked for by the backend before it creates the first administrator;
        # deploy sends it. Without it bootstrap is refused.
        BOOTSTRAP_TOKEN_KEY: _random_secret(),
        # Langfuse initial admin seeding (populated during bootstrap)
        "LANGFUSE_INIT_USER_EMAIL": "",
        "LANGFUSE_INIT_USER_PASSWORD": "",
        "LANGFUSE_INIT_USER_NAME": "Admin",
        "LANGFUSE_INIT_ORG_ID": "faden-org",
        "LANGFUSE_INIT_ORG_NAME": "fadenstack",
        "LANGFUSE_INIT_PROJECT_ID": "faden-project",
        "LANGFUSE_INIT_PROJECT_NAME": "fadenstack",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "",
    }

    # Worker counts and pools are not set here: deploy, upgrade and
    # ``up --gen-env`` size them with ``faden tune`` (core/tune.py).

    # Module activation
    profs = profiles or []
    for prof in profs:
        env.update(module_env_vars(prof))

    return env


# ── Template rendering ────────────────────────────────────────────


def render_env_file(
    template_dir: Path,
    *,
    template_name: str = ".env.j2",
    variables: dict[str, str] | None = None,
) -> str:
    """Render the ``.env.j2`` template with the given variables."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(),
        keep_trailing_newline=True,
    )
    tmpl = env.get_template(template_name)
    return tmpl.render(**(variables or {}))


def write_env_file(
    output_path: Path,
    variables: dict[str, str],
    *,
    preserve_secrets: bool = False,
) -> Path:
    """Write a ``.env`` file from a variable dict (no template needed).

    This is the simpler path: just write ``KEY=VALUE`` lines directly.
    Suitable when we don't need Jinja2 logic in the env file.

    If *preserve_secrets* is True and *output_path* already exists,
    any keys listed in :data:`SECRET_KEYS` that are present in the
    existing file will be kept instead of being replaced.
    """
    if preserve_secrets:
        existing = read_env_file(output_path)
        for key in SECRET_KEYS:
            if key in existing and key in variables:
                variables[key] = existing[key]
        # Also fix derived values that embed a secret
        if "POSTGRES_PASSWORD" in existing and "LANGFUSE_DATABASE_URL" in variables:
            pw = existing["POSTGRES_PASSWORD"]
            user = variables.get("POSTGRES_USER", "postgres")
            db = variables.get("POSTGRES_DB", "langfuse")
            variables["LANGFUSE_DATABASE_URL"] = (
                f"postgresql://{user}:{pw}@postgres:5432/{db}"
            )
    lines: list[str] = []
    lines.append("# Generated by faden init — do not edit manually unless you know what you're doing.")
    lines.append(f"# Generated at: {_timestamp()}")
    lines.append("")

    # Group by prefix for readability
    groups: dict[str, list[tuple[str, str]]] = {"core": []}
    for key, value in variables.items():
        if key.startswith("FADEN_"):
            group = "faden"
        elif key.startswith("LANGFUSE_"):
            group = "langfuse"
        elif key.startswith("CLICKHOUSE_"):
            group = "clickhouse"
        else:
            group = "core"
        groups.setdefault(group, []).append((key, value))

    group_labels = {
        "core": "Core Infrastructure",
        "langfuse": "Langfuse",
        "clickhouse": "ClickHouse",
        "faden": "Fadenstack Services",
    }

    for group_key in ["core", "langfuse", "clickhouse", "faden"]:
        items = groups.get(group_key, [])
        if not items:
            continue
        lines.append(f"# ── {group_labels.get(group_key, group_key)} ──")
        for k, v in items:
            lines.append(f"{k}={v}")
        lines.append("")

    # Always write LF line endings — .env is consumed by Linux containers.
    # Readable by its owner only: it holds every password and key of the install.
    write_private(output_path, "\n".join(lines).encode("utf-8"))
    return output_path


def ensure_bootstrap_token(env_path: Path) -> bool:
    """Give an install's ``.env`` the bootstrap token when it has none; whether it was added.

    An ``.env`` written before the token existed keeps its other values. Deploy
    never rewrites an existing ``.env``, and the backend refuses bootstrap without
    the token, so an install whose first deploy stopped before the administrator
    was made would otherwise never get one.
    """
    env = read_env_file(env_path)
    if env.get(BOOTSTRAP_TOKEN_KEY):
        return False
    env[BOOTSTRAP_TOKEN_KEY] = _random_secret()
    write_env_file(env_path, env)
    return True


def fill_required_env(env_path: Path, *, profiles: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Give an install's ``.env`` every key the compose file requires that it lacks; notes for the operator.

    Everything else in the file stays as it is. A missing token gets the value the rest of its group holds,
    or a new random one; a missing password a random one (deploy and upgrade then set Postgres to it, see
    ``_sync_postgres_password``). A missing key that seals stored data means the install ran on the
    compose file's published fallback, and its data is readable only with that: the published value is
    written, :data:`ALLOW_PUBLISHED_KEYS` lets the services start with it, and a note says to change it.
    Not for MCP's key when MCP is not switched on here: it stored nothing.
    """
    current = read_env_file(env_path)
    defaults = default_env_vars(profiles=list(profiles))
    added: dict[str, str] = {}
    for group in _TOKEN_GROUPS:
        value = next((current[key] for key in group if current.get(key)), None) or defaults[group[0]]
        added.update({key: value for key in group if not current.get(key)})
    kept: list[str] = []
    for key in REQUIRED_KEYS:
        if current.get(key) or key in added:
            continue
        if key in PUBLISHED_DATA_KEYS and (key != "FADEN_MCP_ENCRYPTION_KEY" or "mcp" in profiles):
            added[key] = PUBLISHED_DATA_KEYS[key]
            kept.append(key)
        else:
            added[key] = defaults[key]
    # Not secrets: the settings a release introduced, written with their defaults. A key the operator
    # set, even to nothing, stays as it is.
    added.update({key: value for key, value in DEPLOY_SETTINGS.items() if key not in current})
    if "POSTGRES_PASSWORD" in added and current.get("LANGFUSE_DATABASE_URL"):
        user = current.get("POSTGRES_USER") or "postgres"
        added["LANGFUSE_DATABASE_URL"] = f"postgresql://{user}:{added['POSTGRES_PASSWORD']}@postgres:5432/langfuse"
    if any(key in kept for key in _REFUSED_DATA_KEYS) and not current.get(ALLOW_PUBLISHED_KEYS):
        added[ALLOW_PUBLISHED_KEYS] = "true"
    if added:
        _put_env(env_path, added, header="Required by the compose file (faden upgrade)")
    return [
        f"{key} was not in .env: this install ran on the published development value, which is now written "
        "there so the data stored with it stays readable. Change it as Help describes (Installing, Secrets in .env)."
        for key in kept
    ]


def set_env_values(env_path: Path, values: dict[str, str]) -> None:
    """Set *values* in the install's ``.env``, the rest of the file as it is."""
    _put_env(env_path, values, header="Set by faden deploy")


def fill_dev_env(env_path: Path) -> list[str]:
    """Give a development ``.env`` the keys the compose file requires that it lacks, with development values.

    A development stack's own ``.env`` predates them: the compose file supplied fixed values. Returns the
    keys it added, never their values.
    """
    current = read_env_file(env_path)
    dev = dev_env_vars()
    added = {key: dev[key] for key in REQUIRED_KEYS if not current.get(key)}
    if added:
        _put_env(env_path, added, header="Required by the compose file (faden dev up)")
    return list(added)


def _put_env(env_path: Path, values: dict[str, str], *, header: str) -> None:
    """Set *values* in the ``.env`` at *env_path*: a key it has is rewritten in place, the rest appended."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    where = _key_lines(lines)
    for key, value in values.items():
        if key in where:
            lines[where[key]] = f"{key}={value}"
    appended = [f"{key}={value}" for key, value in values.items() if key not in where]
    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"# ── {header} ──", *appended]
    # LF and owner-only, like write_env_file.
    write_private(env_path, ("\n".join(lines) + "\n").encode("utf-8"))


def set_module_env(
    env_path: Path,
    *,
    enable: list[str] | tuple[str, ...] = (),
    disable: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Switch modules on or off in an install's ``.env``, changing nothing else.

    Enabling writes the module's settings (its ``*_ENABLED`` switches and its
    URLs) and gives every secret it needs a random value when the file has
    none; a token group missing only some of its keys takes the value the
    others hold. Disabling sets its switches to false and keeps its secrets:
    data may already be encrypted with them. Returns the keys it set, never
    their values.

    ``faden module enable`` used to record the module in faden.yaml
    only. Its containers started, but the gateway and the backend kept the
    compose file's ``*_ENABLED=false`` and answered "MCP module is not
    enabled.", and MCP encrypted with the compose file's fixed dev key.
    """
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    where = _key_lines(lines)
    current = {key: lines[number].partition("=")[2].strip() for key, number in where.items()}
    changes = {**_enabling(current, enable), **_disabling(current, disable)}
    if not changes:
        return []
    appended = [f"{key}={value}" for key, value in changes.items() if key not in where]
    for key, value in changes.items():
        if key in where:
            lines[where[key]] = f"{key}={value}"
    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["# ── Modules (faden module enable) ──", *appended]
    # LF, like write_env_file: the file is read by Linux containers. Owner-only, like write_env_file.
    write_private(env_path, ("\n".join(lines) + "\n").encode("utf-8"))
    return list(changes)


def _key_lines(lines: list[str]) -> dict[str, int]:
    """Which line of an ``.env`` sets each key."""
    where: dict[str, int] = {}
    for number, line in enumerate(lines):
        text = line.strip()
        if text and not text.startswith("#") and "=" in text:
            where[text.partition("=")[0].strip()] = number
    return where


def _enabling(current: dict[str, str], names: list[str] | tuple[str, ...]) -> dict[str, str]:
    """The settings and missing secrets that switch *names* on."""
    from faden_cli.core.registry import MODULES  # noqa: PLC0415

    changes: dict[str, str] = {}
    for name in names:
        module = MODULES[name]
        changes.update({key: value for key, value in module.env_vars if current.get(key) != value})
        for group in module.secret_groups:
            value = next((current[key] for key in group if current.get(key)), None) or _random_secret()
            changes.update({key: value for key in group if not current.get(key)})
    return changes


def _disabling(current: dict[str, str], names: list[str] | tuple[str, ...]) -> dict[str, str]:
    """The switches that turn *names* off; their secrets stay."""
    from faden_cli.core.registry import MODULES  # noqa: PLC0415

    return {
        key: "false"
        for name in names
        for key, _value in MODULES[name].env_vars
        if key.endswith("_ENABLED") and current.get(key) != "false"
    }


def _timestamp() -> str:
    """Return an ISO timestamp string."""
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
