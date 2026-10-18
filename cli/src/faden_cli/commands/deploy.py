"""``faden deploy`` — full production deployment in one command.

Runs the complete production flow:
  1. Pre-flight checks (Docker, Compose, disk, RAM)
  2. Locate or set up the install directory
  3. Generate ``.env`` with random secrets, and size the services for
     this host (what ``faden tune`` does)
  4. Enable requested modules (compose profiles)
     and choose the ports and whether to serve HTTPS
  5. Pull pre-built container images from the registry
  6. Start all services (infra + app + modules)
  7. Print endpoint summary
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

import click

from faden_cli import __version__
from faden_cli.core.bundle import DEFAULT_INSTALL_DIR, bundled_files, installed_release, unpack
from faden_cli.core.compose import (
    ComposeContext,
    build as compose_build,
    build_base_image,
    build_context_from_config,
    pull_base_images,
    up as compose_up,
)
from faden_cli.core import installation
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.detect import detect_docker
from faden_cli.core.env_gen import _random_password, default_env_vars, ensure_bootstrap_token, read_env_file, set_env_values, write_env_file
from faden_cli.core.private_files import private_dir, secure_install
from faden_cli.core.registry import MODULES_COMPAT as KNOWN_MODULES
from faden_cli.core.rmq import write_definitions as write_rmq_definitions
from faden_cli.core.settings import FadenConfig, load_config, save_config
from faden_cli.core.tune import tune_env


def set_deploy_options(
    env_path: Path, *, data_dir: str | None, log_max_size: str | None, log_max_file: int | None
) -> None:
    """Write the data location and the log rotation the operator chose into ``.env``; what is not given stays."""
    values: dict[str, str] = {}
    if data_dir is not None:
        # The path goes into a compose volume line, where ':' separates fields: POSIX style (/srv/x, /c/Users/x).
        if data_dir and (not data_dir.startswith("/") or ":" in data_dir):
            raise click.BadParameter(
                "give an absolute path in POSIX form, e.g. /srv/fadenstack (on Windows /c/data)", param_hint="--data-dir"
            )
        values["FADEN_DATA_DIR"] = data_dir
    if log_max_size is not None:
        if not re.fullmatch(r"[1-9][0-9]*[kmg]", log_max_size.lower()):
            raise click.BadParameter("a size such as 50m, 500k or 1g", param_hint="--log-max-size")
        values["FADEN_LOG_MAX_SIZE"] = log_max_size.lower()
    if log_max_file is not None:
        values["FADEN_LOG_MAX_FILE"] = str(log_max_file)
    if values:
        set_env_values(env_path, values)
        info("Set in .env: " + ", ".join(values))


def langfuse_login_env(env_vars: dict[str, str], admin_email: str) -> dict[str, str]:
    """The .env values that give Langfuse its first user: the administrator's e-mail and a password of its own.

    Grafana, Langfuse and RabbitMQ's management used to be set to the administrator's console password. A
    password in four places is four places to lose it from, and ``faden admin reset-password`` could not keep
    them in step. Each service has a random password in ``.env`` instead, which is what the service runs on:
    Langfuse reads ``LANGFUSE_INIT_USER_PASSWORD`` only when it creates the user, so one already set is kept.
    """
    values = {
        "LANGFUSE_INIT_USER_EMAIL": admin_email,
        "LANGFUSE_INIT_USER_NAME": "Admin",
        "LANGFUSE_INIT_PROJECT_NAME": "fadenstack",
    }
    if not env_vars.get("LANGFUSE_INIT_USER_PASSWORD"):
        values["LANGFUSE_INIT_USER_PASSWORD"] = _random_password(24)
    # Langfuse's first project gets a secret of its own. It was the setup token's first 40
    # characters -- the JWT header, the same on every install.
    if not env_vars.get("LANGFUSE_INIT_PROJECT_SECRET_KEY"):
        values["LANGFUSE_INIT_PROJECT_SECRET_KEY"] = f"sk-lf-{secrets.token_hex(20)}"
        values["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"] = "pk-lf-faden"
    return values


def _regenerate_rmq_definitions(
    shared_dir: Path,
    env_vars: dict[str, str],
    profiles: set[str] | list[str],
) -> None:
    """Write ``rabbitmq/definitions.json`` from current env vars."""
    write_rmq_definitions(
        shared_dir,
        admin_user=env_vars.get("RABBITMQ_ADMIN_USER", "admin"),
        admin_pass=env_vars.get("RABBITMQ_ADMIN_PASS", "guest"),
        backend_pass=env_vars.get("RABBITMQ_BACKEND_PASS", "guest"),
        api_pass=env_vars.get("RABBITMQ_API_PASS", "guest"),
        pii_pass=env_vars.get("RABBITMQ_PII_PASS") if "pii" in set(profiles) else None,
    )


def summary_base(shared_dir: Path, backend_url: str) -> str:
    """The address the closing summary names: one other machines reach it by.

    The backend's own URL is ``https://localhost`` on the server, which only a
    browser on the server itself can open; the table said so above the real
    addresses (the end-to-end test of 0.4.2).
    """
    from faden_cli.commands.tls import addresses, reachable_example  # noqa: PLC0415

    reach = addresses(shared_dir)
    return reachable_example(reach) if reach else backend_url


def pin_release(env_path: Path) -> None:
    """Run the published images of this CLI's version (an install made from the bundle).

    Unpinned, the compose file asks for ``latest``: an install would pull
    whatever was published last, and two machines deployed a day apart
    would run different releases under the same CLI.
    """
    record_installer(env_path, None)


def record_installer(env_path: Path, source: Path | None) -> None:
    """Pin a release install to this CLI's version, and say how the install is upgraded.

    The console's update notice shows the commands for it (FADEN_CLI_INSTALLER): the
    way this CLI was installed, or ``source`` for an install built from the checkout
    *source*, which a newer release reaches by ``git pull``, not by a newer CLI.
    """
    from faden_cli.core.update_check import installer  # noqa: PLC0415

    env_vars = read_env_file(env_path)
    if source:
        pins = {"FADEN_CLI_INSTALLER": "source"}
    else:
        pins = {"VERSION": __version__, "FADEN_CLI_INSTALLER": installer()}
    if any(env_vars.get(k) != v for k, v in pins.items()):
        env_vars.update(pins)
        write_env_file(env_path, env_vars)


def update_check_step(env_path: Path, cfg: FadenConfig, *, choice: bool | None, yes: bool) -> None:
    """Ask once whether this install hears about new releases; the CLI and the backend both follow it.

    The answer goes into faden.yaml (the CLI) and .env (the backend's default; an
    administrator can change it later under Settings > Updates).
    """
    from faden_cli.core.release_feed import FEED_URL  # noqa: PLC0415

    env_vars = read_env_file(env_path)
    if choice is None:
        if "FADEN_UPDATE_CHECK" in env_vars:
            choice = env_vars["FADEN_UPDATE_CHECK"].strip().lower() == "true"
        elif yes:
            choice = True
        else:
            console.print("\n[bold cyan]Step 3e: New releases…[/bold cyan]")
            console.print(
                f"  Once a day the server and this CLI read {FEED_URL},\n"
                "  the public list of releases. They send nothing about this server: the list is\n"
                "  compared here, and administrators see in the console when an update is out."
            )
            choice = click.confirm("  Check for new releases?", default=True)
    if env_vars.get("FADEN_UPDATE_CHECK") != ("true" if choice else "false"):
        env_vars["FADEN_UPDATE_CHECK"] = "true" if choice else "false"
        write_env_file(env_path, env_vars)
    cfg.update_check = choice
    if not choice:
        info("Update check off (update_check in faden.yaml; Settings > Updates in the console).")


def _sync_postgres_password(ctx: ComposeContext, env_path: Path) -> None:
    """Ensure the Postgres superuser password matches ``.env``.

    The official ``postgres`` image only reads ``POSTGRES_PASSWORD``
    during the **first** initialisation of the data volume.  If the
    volume already exists and ``.env`` contains a different password,
    every service that connects with the new password will fail with
    ``InvalidPasswordError``.

    This function boots only the ``postgres`` service, waits for it to
    become healthy, then runs ``ALTER USER`` via local socket auth
    (which never requires a password) to bring the DB in sync.
    """
    import shutil
    import subprocess

    from faden_cli.core.env_gen import read_env_file

    docker = shutil.which("docker")
    if not docker:
        return

    env_vars = read_env_file(env_path)
    pg_user = env_vars.get("POSTGRES_USER", "postgres")
    pg_pass = env_vars.get("POSTGRES_PASSWORD", "")
    if not pg_pass:
        return

    info("Syncing Postgres password with .env…")

    # Start only postgres so we can ALTER before migrators run.
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    # ALTER via local socket auth (no password needed inside container).
    # Use stdin pipe to avoid shell-quoting issues with special chars.
    # Escape single quotes in password for SQL literal safety.
    safe_pass = pg_pass.replace("'", "''")
    alter_sql = f"ALTER USER {pg_user} PASSWORD '{safe_pass}';\n"
    result = subprocess.run(  # noqa: S603
        [docker, "exec", "-i", "faden-postgres", "psql", "-U", pg_user],
        input=alter_sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        success("Postgres password synchronised.")
    else:
        warning(f"Could not sync Postgres password: {result.stderr.strip()}")


#: Logins the initdb script of earlier releases made on every install, with
#: the same fixed passwords everywhere (llm_user could read every backend
#: table). No service of an install uses them; a development stack makes the
#: one it needs itself (faden dev up).
FIXED_LOGINS = ("llm_user", "faden_backend")


def retire_fixed_logins(env_path: Path) -> list[str]:
    """Switch off the fixed-password logins an install made before still has; returns which.

    ``ALTER ROLE ... NOLOGIN``: the roles and their grants stay, nobody can sign
    in with them. Postgres must be running (after :func:`_sync_postgres_password`).
    Only an install whose ``.env`` says FADEN_BACKEND_ENVIRONMENT=production (deploy
    writes it) is touched: a development stack's host-run backend signs in as
    ``faden_backend``, and an ``.env`` that says nothing may belong to one.
    """
    import shutil
    import subprocess

    env_vars = read_env_file(env_path)
    if env_vars.get("FADEN_BACKEND_ENVIRONMENT") != "production":
        return []
    docker = shutil.which("docker")
    if not docker:
        return []
    pg_user = env_vars.get("POSTGRES_USER", "postgres")
    roles = [role for role in FIXED_LOGINS if role != pg_user]
    if not roles:
        return []
    names = ", ".join(f"'{role}'" for role in roles)
    sql = (
        f"SELECT coalesce(string_agg(rolname, ','), '') FROM pg_roles "
        f"WHERE rolname IN ({names}) AND rolcanlogin;\n"
        "DO $$ DECLARE r text; BEGIN "
        f"FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ({names}) AND rolcanlogin LOOP "
        "EXECUTE format('ALTER ROLE %I NOLOGIN', r); END LOOP; END $$;\n"
    )
    result = subprocess.run(  # noqa: S603
        [docker, "exec", "-i", "faden-postgres", "psql", "-U", pg_user, "-v", "ON_ERROR_STOP=1", "-At"],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        warning(f"Could not switch off the old fixed Postgres logins: {result.stderr.strip()}")
        return []
    retired = [name for name in (result.stdout.splitlines() or [""])[0].split(",") if name]
    if retired:
        success(f"Old Postgres logins with fixed passwords switched off: {', '.join(retired)}.")
    return retired


def _resolve_compose_file(install_dir: Path) -> Path | None:
    """Find the shared docker-compose file."""
    for candidate in (
        install_dir / "docker-compose.yaml",
        install_dir / "docker-compose.yml",
    ):
        if candidate.exists():
            return candidate
    return None


def _find_shared_dir(workspace: Path) -> Path | None:
    """Locate the shared directory relative to workspace.

    Searches in order:
      1. workspace/shared        (monorepo layout)
      2. workspace/fadenstack/shared  (monorepo cloned)
      3. workspace/../shared      (legacy polyrepo sibling)
    """
    for candidate in (
        workspace / "shared",
        workspace / "fadenstack" / "shared",
        workspace.parent / "shared",
    ):
        if candidate.is_dir():
            return candidate
    return None


def _login_for_api_token(backend_url: str, email: str) -> str | None:
    """Prompt for password, login via the auth endpoint, and return an access token."""
    import click  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from faden_cli.core.console import error, success  # noqa: PLC0415
    from faden_cli.core.edge import verify_for  # noqa: PLC0415

    password = click.prompt(f"  Password for {email}", hide_input=True, show_default=False)
    try:
        resp = httpx.post(
            f"{backend_url.rstrip('/')}/api/auth/jwt/login",
            data={"username": email, "password": password},
            timeout=15,
            verify=verify_for(backend_url),
        )
        if resp.status_code == 200:  # noqa: PLR2004
            token = resp.json().get("access_token", "")
            if token:
                success("Logged in — creating enrollment token…")
                return token
        error(f"Login failed (HTTP {resp.status_code})")
        return None
    except httpx.HTTPError as exc:
        error(f"Could not reach backend for login: {exc}")
        return None


@click.command("deploy")
@click.argument(
    "install_dir",
    required=False,
    type=click.Path(file_okay=False, resolve_path=True),
)
@click.option(
    "--modules", "-m",
    default="",
    help="Comma-separated modules to enable (EE only, e.g. pii,auth).",
)
@click.option("--build", is_flag=True, help="Build images from source instead of pulling pre-built images.")
@click.option("--no-cache", is_flag=True, help="Build images without Docker cache (implies --build).")
@click.option("--gpu/--no-gpu", default=None, help="Include GPU (NVIDIA) compose overlay. Default: auto-detect.")
@click.option("--force-env", is_flag=True, help="Regenerate .env even if it exists.")
@click.option(
    "--runtime-images",
    "runtime_images",
    default=None,
    metavar="ARCHS",
    help=(
        "Runtime images to fetch for the machines that will run models: all (default), none, "
        "or architectures such as x86_64,aarch64. Remembered for upgrades."
    ),
)
@click.option(
    "--http-port", type=click.IntRange(1, 65535), default=None, help="Host port for plain HTTP (default 80)."
)
@click.option(
    "--https-port", type=click.IntRange(1, 65535), default=None, help="Host port for HTTPS (default 443)."
)
@click.option(
    "--data-dir",
    default=None,
    help="Keep the company data (chat attachments) in directories under this absolute path on the host, "
    "e.g. /srv/fadenstack, instead of Docker named volumes (FADEN_DATA_DIR in .env).",
)
@click.option(
    "--log-max-size",
    default=None,
    help="Largest Docker log file per container before it rotates, e.g. 50m (default 50m; FADEN_LOG_MAX_SIZE).",
)
@click.option(
    "--log-max-file",
    type=click.IntRange(1, 1000),
    default=None,
    help="Rotated log files kept per container (default 5; FADEN_LOG_MAX_FILE).",
)
@click.option(
    "--tls",
    "tls_mode",
    type=click.Choice(["off", "generate", "own"]),
    default=None,
    help="HTTPS: generate (a certificate made here for this machine; the default), own (--tls-cert/--tls-key), "
    "or off. Default: ask; with --yes, generate unless HTTPS was switched off on purpose.",
)
@click.option(
    "--tls-cert", type=click.Path(dir_okay=False, path_type=Path), default=None,
    help="Your certificate (PEM).",
)
@click.option(
    "--tls-key", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Its private key (PEM)."
)
@click.option(
    "--tls-chain", type=click.Path(dir_okay=False, path_type=Path), default=None,
    help="Intermediate certificates (PEM), unless --tls-cert already carries them.",
)
@click.option(
    "--tls-ca", type=click.Path(dir_okay=False, path_type=Path), default=None,
    help="With --tls own: a private issuer's root (PEM), given to the machines so they trust the certificate.",
)
@click.option(
    "--tls-name", "tls_names", multiple=True, metavar="NAME",
    help="With --tls generate: another DNS name or address the console is reached by (repeatable).",
)
@click.option(
    "--update-check/--no-update-check",
    "update_check",
    default=None,
    help="Read the public list of releases once a day and say when one is out (sends nothing about "
    "this server). Default: ask; with --yes, on.",
)
@click.option(
    "--skip-doctor", is_flag=True,
    help="Skip pre-flight system checks.",
)
@click.option(
    "--yes", "-y", is_flag=True,
    help="Auto-confirm all prompts.",
)
@click.option(
    "--local-node",
    is_flag=True,
    help="Provision faden_agent locally or over SSH during deployment.",
)
@click.option(
    "--local-node-host",
    default="",
    help="SSH host for node-agent provisioning (example: ubuntu@10.0.0.12).",
)
@click.option(
    "--local-node-workdir",
    default="",
    help="Install directory for the core checkout on the target host (development installs).",
)
@click.option(
    "--local-node-branch",
    default="",
    help="Core git branch checked out on a remote host (default: config dev.branch).",
)
@click.option(
    "--local-node-backend-url",
    default="",
    help="Backend URL written to node-agent environment. Default: same as deploy target.",
)
@click.option(
    "--local-node-advertise-host",
    default="",
    help="Host/IP that node agent advertises for runtime endpoints.",
)
@click.option(
    "--local-node-enrollment-token",
    default="",
    help="Optional one-time enrollment token for initial node onboarding.",
)
@click.option(
    "--local-node-sudo/--local-node-no-sudo",
    default=True,
    show_default=True,
    help="Use sudo for systemd installation in node-agent provisioning.",
)
@click.option(
    "--chat-retention-days",
    type=click.IntRange(min=0),
    default=None,
    help="Days a chat someone deleted is kept before it is erased for good (default 30; asked when not given).",
)
def deploy_cmd(
    install_dir: str | None,
    *,
    runtime_images: str | None,
    modules: str,
    build: bool,
    no_cache: bool,
    gpu: bool | None,
    force_env: bool,
    update_check: bool | None,
    skip_doctor: bool,
    yes: bool,
    http_port: int | None,
    https_port: int | None,
    data_dir: str | None,
    log_max_size: str | None,
    log_max_file: int | None,
    tls_mode: str | None,
    tls_cert: Path | None,
    tls_key: Path | None,
    tls_chain: Path | None,
    tls_names: tuple[str, ...],
    tls_ca: Path | None,
    local_node: bool,
    local_node_host: str,
    local_node_workdir: str,
    local_node_branch: str,
    local_node_backend_url: str,
    local_node_advertise_host: str,
    local_node_enrollment_token: str,
    local_node_sudo: bool,
    chat_retention_days: int | None = None,
) -> None:
    """Deploy Fadenstack to a local production environment.

    Performs a complete deployment: pre-flight checks, environment
    generation, image builds, database migrations, and service startup.

    INSTALL_DIR defaults to the current directory or the configured
    install path.

    \b
    Examples:
        faden deploy                         # deploy the published images (into ~/fadenstack)
        faden deploy /opt/faden           # deploy to specific directory
        faden deploy --build                 # from a source checkout: build the images
        faden deploy --force-env             # regenerate secrets
        faden deploy --tls generate          # HTTPS with a certificate made here
        faden deploy --tls own --tls-cert fullchain.pem --tls-key key.pem
        faden deploy --http-port 8080 --https-port 8443
    """
    console.print("\n[bold magenta]fadenstack — Production Deployment[/bold magenta]\n")

    # ── 0. Resolve install directory ──────────────────────────────
    #
    # Priority order:
    #   1. Explicit CLI argument  (faden deploy /path)
    #   2. Current working directory — if it contains shared
    #   3. Saved config install_dir  (from a previous dev init / deploy)
    #   4. ~/fadenstack, unpacked from the files this CLI carries
    #   5. CWD as final fallback
    #
    # This ensures that running ``faden deploy`` from a development
    # workspace always builds from the local source, even when the
    # config still points at a different (possibly stale) clone.
    cfg = load_config()

    if install_dir:
        workspace = Path(install_dir)
    else:
        cwd = Path.cwd()
        cwd_shared = _find_shared_dir(cwd)
        if cwd_shared:
            workspace = cwd
        elif cfg.install_dir:
            workspace = Path(cfg.install_dir)
        elif bundled_files() is not None:
            workspace = DEFAULT_INSTALL_DIR
        else:
            workspace = cwd

    shared_dir = _find_shared_dir(workspace)
    if not shared_dir:
        # If install_dir itself IS the shared dir
        if (workspace / "docker-compose.yaml").exists() or (workspace / "docker-compose.yml").exists():
            shared_dir = workspace
        elif bundled_files() is not None:
            # No checkout: the deployment files this CLI carries.
            unpack(workspace)
            shared_dir = workspace
            success(f"Deployment files for release {__version__} written to {workspace}")
        else:
            error(
                f"Cannot find shared in {workspace}.\n"
                "  Run from the workspace root, or pass the install directory as an argument.\n"
                "  If you haven't cloned the repos yet, run: faden dev init <dir>"
            )
            sys.exit(1)

    # An install made from the bundle runs one release: the CLI's.
    release = installed_release(shared_dir)
    if release is not None:
        if release != __version__:
            error(
                f"{shared_dir} runs release {release}; this CLI is {__version__}.\n"
                "  To move it to this release, run: faden upgrade"
            )
            sys.exit(1)
        if build or no_cache:
            error(
                "--build needs a source checkout; this install runs the published images.\n"
                "  Deploy without --build, or deploy from a checkout of fadenstack."
            )
            sys.exit(1)
        unpack(shared_dir)  # puts back anything deleted since

    compose_file = _resolve_compose_file(shared_dir)
    if not compose_file:
        error(f"No docker-compose.yaml found in {shared_dir}.")
        sys.exit(1)

    info(f"Install directory: {shared_dir}")

    # ── 1. Pre-flight checks ─────────────────────────────────────
    if not skip_doctor:
        console.print("\n[bold cyan]Step 1: Pre-flight checks…[/bold cyan]")

        docker = detect_docker()
        preflight_errors: list[str] = []

        if not docker.installed:
            msg = docker.error or "Docker Engine 24+ is required."
            preflight_errors.append(
                f"Docker is not available. {msg}\n"
                f"  Install: {docker.install_hint}"
            )
        if docker.installed and not docker.compose_installed:
            preflight_errors.append(
                "Docker Compose V2 is not installed.\n"
                f"  Install: {docker.install_hint}"
            )
        if docker.installed and not docker.daemon_running:
            hint = docker.error or docker.daemon_hint
            preflight_errors.append(f"Docker daemon is not running. {hint}")

        if preflight_errors:
            for msg in preflight_errors:
                error(msg)
            sys.exit(1)

        success(
            f"Docker {docker.version}, "
            f"Compose {docker.compose_version}, "
            f"daemon running"
        )
    else:
        console.print("\n[dim]Skipping pre-flight checks (--skip-doctor).[/dim]")

    # ── 2. Parse requested modules ────────────────────────────────
    console.print("\n[bold cyan]Step 2: Module configuration…[/bold cyan]")

    known_profiles = {m["profile"] for m in KNOWN_MODULES.values()}
    profiles: set[str] = {p for p in (cfg.profiles or []) if p in known_profiles}

    if modules:
        for mod in modules.split(","):
            mod = mod.strip().lower()
            if not mod:
                continue
            if mod not in KNOWN_MODULES:
                warning(f"Unknown module: {mod}. Available: {', '.join(sorted(KNOWN_MODULES))}")
                continue
            profiles.add(KNOWN_MODULES[mod]["profile"])
            info(f"Enabled module: {mod}")

    if profiles:
        console.print(f"  Active profiles: [bold]{', '.join(sorted(profiles))}[/bold]")
    else:
        console.print("  No optional modules enabled.")

    # ── 3. Generate .env file ─────────────────────────────────────
    console.print("\n[bold cyan]Step 3: Environment configuration…[/bold cyan]")

    env_path = shared_dir / ".env"
    if env_path.exists() and not force_env:
        info(f".env already exists at {env_path} (use --force-env to regenerate)")
        # What the compose file requires and an older .env lacks; stored data keeps its key.
        from faden_cli.core.env_gen import fill_required_env  # noqa: PLC0415

        for note in fill_required_env(env_path, profiles=sorted(profiles)):
            warning(note)
    else:
        env_vars = default_env_vars(profiles=sorted(profiles))
        write_env_file(env_path, env_vars, preserve_secrets=not force_env)
        success(f"Environment file written to {env_path}")
    set_deploy_options(env_path, data_dir=data_dir, log_max_size=log_max_size, log_max_file=log_max_file)
    # The backend creates the first administrator only for whoever sends this; an .env from before it
    # existed gets one, so a deploy that stopped before the administrator was made can finish.
    if ensure_bootstrap_token(env_path):
        info("Bootstrap token added to .env.")

    # Ensure host HuggingFace cache directory exists and is configured
    existing = read_env_file(env_path)
    if "HF_CACHE_DIR" not in existing:
        hf_default = Path.home() / ".cache" / "huggingface" / "hub"
        hf_default.mkdir(parents=True, exist_ok=True)
        # Docker Desktop on Windows needs POSIX-style paths
        # (e.g. /c/Users/…) because ':' is used as the volume
        # mount delimiter in compose files.
        hf_str = str(hf_default)
        if sys.platform == "win32":
            hf_str = hf_default.as_posix()
            # C:/Users/… → /c/Users/…
            if len(hf_str) >= 2 and hf_str[1] == ":":
                hf_str = "/" + hf_str[0].lower() + hf_str[2:]
        with env_path.open("a", encoding="utf-8") as f:
            f.write("\n# HuggingFace cache — mount into backend\n")
            f.write(f"HF_CACHE_DIR={hf_str}\n")
        info(f"HF cache directory: {hf_default}")
    # Ensure empty fallback directory exists for compose
    fallback_dir = shared_dir / ".empty-hf-cache"
    fallback_dir.mkdir(exist_ok=True)

    if release is not None:
        pin_release(env_path)
        info(f"Images: release {__version__}")
    else:
        record_installer(env_path, shared_dir)

    # ── 3a. Size the services for this host ───────────────────────
    # On every deploy, not only with a new .env: an install deployed before
    # a fix to the sizing gets it too. Profile and CPU share are what
    # ``faden tune`` last chose.
    console.print("\n[bold cyan]Step 3a: Sizing the services for this host…[/bold cyan]")
    tune_env(env_path)

    # ── 3b. Migrate old single-credential RabbitMQ env vars ───────
    existing = read_env_file(env_path)
    if "RABBITMQ_BACKEND_PASS" not in existing:
        from faden_cli.core.env_gen import _random_password  # noqa: PLC0415

        info("Migrating RabbitMQ credentials to per-service format…")
        existing["RABBITMQ_ADMIN_USER"] = existing.pop("RABBITMQ_USER", "admin")
        existing["RABBITMQ_ADMIN_PASS"] = existing.pop("RABBITMQ_PASS", _random_password(24))
        existing["RABBITMQ_BACKEND_PASS"] = _random_password(24)
        existing["RABBITMQ_API_PASS"] = _random_password(24)
        existing["RABBITMQ_PII_PASS"] = _random_password(24)
        write_env_file(env_path, existing)
        success("RabbitMQ credentials migrated to per-service format.")

    # ── 3c. Generate RabbitMQ definitions.json ─────────────────────
    existing = read_env_file(env_path)
    _regenerate_rmq_definitions(shared_dir, existing, profiles)
    success("RabbitMQ definitions.json generated.")

    # .env, the saved credentials and the backups, readable by this user only -- an install made
    # before kept them readable by every local user.
    secure_install(shared_dir)

    # ── 3d. Ports and HTTPS ───────────────────────────────────────
    # Before anything starts: nginx picks plain HTTP or HTTPS when it starts.
    console.print("\n[bold cyan]Step 3d: Ports and HTTPS…[/bold cyan]")
    from faden_cli.commands.tls import deploy_step as edge_step  # noqa: PLC0415

    ca_to_trust = edge_step(
        shared_dir,
        env_path,
        yes=yes,
        http_port=http_port,
        https_port=https_port,
        mode=tls_mode,
        cert=tls_cert,
        key=tls_key,
        chain=tls_chain,
        names=tls_names,
        ca=tls_ca,
    )

    # ── 3e. New releases ──────────────────────────────────────────
    update_check_step(env_path, cfg, choice=update_check, yes=yes)

    # ── 3f. Deleted chats ─────────────────────────────────────────
    from faden_cli.core.chat_retention import apply_retention, ask_retention  # noqa: PLC0415

    retention_days = ask_retention(
        env_path, days=chat_retention_days, yes=yes, heading="Step 3f: Deleted chats…",
    )

    # ── 4. Save config ────────────────────────────────────────────
    cfg.install_dir = str(shared_dir)
    cfg.compose_file = compose_file.name
    cfg.profiles = sorted(profiles)
    save_config(cfg)

    # ── 5. Build images ───────────────────────────────────────────
    from faden_cli.core.compose import has_nvidia_gpu  # noqa: PLC0415

    use_gpu = has_nvidia_gpu() if gpu is None else gpu
    compose_files = [compose_file]
    gpu_overlay = shared_dir / "docker-compose.gpu.yaml"
    if use_gpu and gpu_overlay.exists():
        compose_files.append(gpu_overlay)
        info("NVIDIA GPU detected — including GPU overlay.")
    elif gpu_overlay.exists():
        info("GPU overlay skipped (no NVIDIA runtime detected or --no-gpu).")

    ctx = ComposeContext(
        compose_files=compose_files,
        env_file=env_path if env_path.exists() else None,
        project_dir=shared_dir,
        profiles=sorted(profiles),
    )

    if no_cache:
        build = True

    if build:
        console.print("\n[bold cyan]Step 4: Building container images…[/bold cyan]")
        console.print("[dim]This may take several minutes on first run.[/dim]")

        pull_base_images()

        # Build the shared platform base image first (same BuildKit context)
        info("Building platform base image…")
        rc = build_base_image(ctx, no_cache=no_cache)
        if rc != 0:
            warning("Base image build failed — service builds may fail.")
        else:
            success("Base image built.")

        rc = compose_build(ctx, no_cache=no_cache)
        installation.record_build(shared_dir, complete=rc == 0)
        if rc != 0:
            warning(
                "Some images failed to build. Services with successful"
                " builds will still start."
            )
        else:
            success("All images built successfully.")
    else:
        console.print("\n[bold cyan]Step 4: Pulling pre-built images…[/bold cyan]")
        from faden_cli.core.compose import pull as compose_pull  # noqa: PLC0415

        rc = compose_pull(ctx)
        if rc != 0:
            warning("Some images failed to pull. Will try to start with available images.")
        else:
            success("All images pulled successfully.")

    # ── 6. Start services ─────────────────────────────────────────
    console.print("\n[bold cyan]Step 5: Starting services…[/bold cyan]")

    # Start Postgres first and sync its password.
    # The POSTGRES_PASSWORD env var only takes effect on first volume
    # initialisation.  If the data volume already exists with a
    # different password, migrators will fail with InvalidPasswordError.
    # We boot Postgres alone, ALTER USER to match .env, then start
    # everything else.
    # An install from before the chat files had a volume: they go from the old gateway container into it,
    # verified, before anything is started or replaced (syncing the password starts Postgres). Re-running
    # deploy on an install that has just set --data-dir copies the files of its named volumes into the folders.
    from faden_cli.core.backup import keep_company_files  # noqa: PLC0415

    keep_company_files(env_path, ctx)
    _sync_postgres_password(ctx, env_path)
    retire_fixed_logins(env_path)

    # Start infrastructure services and wait for them to become healthy
    # BEFORE starting application containers.  This avoids a race
    # condition where workers start before Docker's embedded DNS has
    # registered the RMQ container, causing permanent AMQP connection
    # failures.
    infra_services = ["redis", "faden-rmq", "minio"]
    info("Starting infrastructure services (Redis, RabbitMQ, MinIO)…")
    rc = compose_up(ctx, services=infra_services, detach=True, wait=True, timeout=60)
    if rc != 0:
        error(f"Infrastructure services failed to start (exit code {rc}).")
        sys.exit(rc)
    success("Infrastructure services healthy.")

    rc = compose_up(ctx, detach=True, build=False, pull="missing", wait=True, timeout=180)
    if rc != 0:
        error(f"docker compose up failed (exit code {rc}).")
        sys.exit(rc)
    success("All services started.")
    from faden_cli.core.compose import report_inactive_modules  # noqa: PLC0415

    report_inactive_modules(ctx)

    # ── 6. Initial admin setup ────────────────────────────────────
    console.print("\n[bold cyan]Step 6: Initial admin setup…[/bold cyan]")

    from faden_cli.core.bootstrap import bootstrap_interactive, wait_for_backend  # noqa: PLC0415
    from faden_cli.core.edge import console_url  # noqa: PLC0415

    # HTTPS as soon as the install holds a certificate: plain HTTP only redirects then.
    backend_url = console_url(shared_dir, read_env_file(env_path) if env_path.exists() else {})
    console.print("  [dim]Waiting for backend to become healthy…[/dim]")
    creds = None
    if wait_for_backend(backend_url):
        creds = bootstrap_interactive(
            backend_url,
            shared_dir,
            auto_confirm=yes,
        )
        if creds:
            cfg.admin_email = creds["email"]
            save_config(cfg)

            # ── Langfuse's first user ──
            from faden_cli.core.compose import up as compose_up_svc  # noqa: PLC0415

            env_vars = read_env_file(env_path)
            env_vars.update(langfuse_login_env(env_vars, creds["email"]))
            write_env_file(env_path, env_vars)
            info("Langfuse's first user is the administrator's e-mail, with a password of its own in .env.")

            # Force-recreate so Langfuse picks up the user (it creates it when it starts). Grafana and
            # RabbitMQ keep the random passwords the .env was generated with: they are not the console's.
            compose_up_svc(
                ctx,
                services=["langfuse-web"],
                detach=True,
                force_recreate=True,
                wait=True,
                timeout=60,
            )
            success("Langfuse recreated with its first user.")
    else:
        warning("Backend did not become healthy in time — skipping admin setup.")
        console.print(
            "  [dim]Check 'faden status' and 'faden logs faden-backend', fix what they show, then run"
            " 'faden deploy' again: it keeps the .env and creates the administrator.[/dim]"
        )

    # ── 7. Runtime images ─────────────────────────────────────────
    # Machines download the container they run models in from this server,
    # so it has to be here before the first machine starts a cluster.
    console.print("\n[bold cyan]Step 7: Runtime images for machines…[/bold cyan]")
    from faden_cli.commands.runtime_images import run_step  # noqa: PLC0415

    run_step(cfg, value=runtime_images)

    # ── 8. Optional local-node provisioning ───────────────────────
    if local_node:
        console.print("\n[bold cyan]Step 8: Local node-agent provisioning…[/bold cyan]")
        from faden_cli.core.local_node import (  # noqa: PLC0415
            create_enrollment_token,
            provision_local_node_agent,
        )

        # Auto-create enrollment token if none was provided and
        # bootstrap gave us an API token.
        enrollment_token = local_node_enrollment_token
        if not enrollment_token.strip() and creds and creds.get("api_token"):
            info("No enrollment token provided — creating one automatically…")
            enrollment_token = create_enrollment_token(backend_url, creds["api_token"]) or ""
        if not enrollment_token.strip():
            # Bootstrap already happened — try logging in to get a token.
            info("No API token from bootstrap — attempting login…")
            api_token = _login_for_api_token(backend_url, cfg.admin_email)
            if api_token:
                enrollment_token = create_enrollment_token(backend_url, api_token) or ""
        if not enrollment_token.strip():
            warning(
                "No enrollment token available. Provide one with"
                " --local-node-enrollment-token or re-run after bootstrap."
            )

        branch = local_node_branch.strip() or (cfg.dev.branch if cfg.dev and cfg.dev.branch else "main")
        method = cfg.dev.clone_method if cfg.dev and cfg.dev.clone_method else "https"
        github_token = cfg.dev.github_token if cfg.dev else ""
        remote_host = local_node_host.strip() or None
        workspace_for_node = shared_dir.parent

        # Default to the resolved backend URL (via nginx) if not explicitly set.
        node_backend = local_node_backend_url.strip() or backend_url
        # On HTTPS the machine is handed what the install publishes for machines to trust.
        from faden_cli.core.tls import CA_PUBLIC_FILE, TLS_DIR  # noqa: PLC0415

        published_ca = shared_dir / TLS_DIR / CA_PUBLIC_FILE
        node_ca = published_ca if node_backend.startswith("https://") and published_ca.is_file() else None

        ok = provision_local_node_agent(
            workspace=workspace_for_node,
            branch=branch,
            backend_url=node_backend,
            advertise_host=local_node_advertise_host,
            enrollment_token=enrollment_token,
            remote_host=remote_host,
            use_sudo=local_node_sudo,
            method=method,
            github_token=github_token,
            workdir_override=local_node_workdir.strip() or None,
            ca_file=node_ca,
        )
        if not ok:
            error("Local-node provisioning failed.")
            sys.exit(1)

    # ── 7b. Nightly backups ───────────────────────────────────────
    from faden_cli.core import schedule  # noqa: PLC0415

    if schedule.available() and schedule.current() is None:
        console.print()
        if yes or click.confirm("  Back up every night at 03:00, keeping the last 7?", default=True):
            backups = private_dir(shared_dir / "backups")
            try:
                how, notes = schedule.install(hour=3, minute=0, retain=7, log=backups / "backup.log")
            except (RuntimeError, OSError) as exc:
                warning(f"Could not schedule nightly backups: {exc}")
            else:
                success(
                    f"Nightly backups into {backups} ({how}; change: faden backup schedule --at HH:MM,"
                    " stop: --off)."
                )
                for note in notes:
                    warning(note)

    # The console's setting, now that the backend runs (an administrator changes it there later).
    apply_retention(retention_days)

    # The backup key: backups encrypted from the first one (features/admin-guardrails.md, 6.4).
    from faden_cli.core import backup_key  # noqa: PLC0415

    backup_key.key_step(env_path.parent, yes=yes, deploy=True)

    # ── 8. Endpoint summary ───────────────────────────────────────
    console.print("\n[bold green]✨ Deployment complete![/bold green]\n")

    from rich.table import Table

    table = Table(title="Service Endpoints", show_header=True, header_style="bold cyan")
    table.add_column("Service", style="bold")
    table.add_column("URL")

    base = summary_base(shared_dir, backend_url)
    endpoints = [
        ("Application", base),
        ("API Docs", f"{base}/api/docs"),
        ("OpenAI-compat Gateway", f"{base}/v1/"),
        ("Grafana", "http://localhost:3001 (local only)"),
        ("Langfuse", "http://localhost:3002 (local only)"),
        ("RabbitMQ Management", "http://localhost:15672 (local only)"),
    ]

    for name, url in endpoints:
        table.add_row(name, url)

    console.print(table)

    from faden_cli.commands.tls import print_addresses, print_trust_hints  # noqa: PLC0415

    print_addresses(shared_dir)
    if ca_to_trust is not None:
        print_trust_hints(shared_dir, ca_to_trust)

    console.print(
        "\n[dim]Useful commands:\n"
        "  faden status        — container status\n"
        "  faden logs -f       — follow logs\n"
        "  faden module list   — show enabled modules\n"
        "  faden down          — stop all services\n"
        "  faden tls status    — HTTPS and its certificate\n"
        "  faden ports         — the HTTP and HTTPS ports\n"
        "  faden deploy --help — deployment options[/dim]"
    )
