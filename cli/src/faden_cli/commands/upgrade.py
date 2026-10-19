"""``faden upgrade`` — upgrade the running Fadenstack installation.

Performs a safe in-place upgrade:
  1. Pre-flight checks (Docker, disk)
  2. Auto-backup (unless --no-backup)
  3. Refresh the deployment files and .env (existing values kept), and
     switch on the modules on by default an administrator did not switch off
  4. Pull this release's images (an install made by ``faden deploy``
     from the published images), or rebuild them (a source checkout)
  5. Rolling restart: infra → migrators → app → modules
  6. Health gate: poll backend /api/health
  7. Print summary

Examples:
    faden upgrade                  # standard upgrade
    faden upgrade --no-backup      # skip pre-upgrade backup
    faden upgrade --no-build       # skip image rebuild
    faden upgrade --dry-run        # show what would happen
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from faden_cli import __version__
from faden_cli.core.backup import create_backup
from faden_cli.core.bundle import installed_release, unpack
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
from faden_cli.core.env_gen import default_env_vars, read_env_file, set_module_env, write_env_file
from faden_cli.core.registry import MODULES
from faden_cli.core.settings import load_config, save_config


@click.command("upgrade")
@click.option("--no-backup", is_flag=True, default=False, help="Skip pre-upgrade backup.")
@click.option(
    "--backup-dir",
    type=click.Path(file_okay=False),
    default="backups",
    show_default=True,
    help="Directory for the pre-upgrade backup.",
)
@click.option("--no-build", is_flag=True, default=False, help="Skip image rebuild.")
@click.option("--no-cache", is_flag=True, default=False, help="Build images without cache.")
@click.option("--skip-doctor", is_flag=True, default=False, help="Skip pre-flight checks.")
@click.option(
    "--runtime-images",
    "runtime_images",
    default=None,
    metavar="ARCHS",
    help=(
        "Runtime images to fetch for machines: all, none, or architectures such as "
        "x86_64,aarch64. Default: what deploy chose (all)."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would happen without executing.",
)
@click.option(
    "--chat-retention-days",
    type=click.IntRange(min=0),
    default=None,
    help="Days a chat someone deleted is kept before it is erased for good (default 30; asked when not given).",
)
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def upgrade_cmd(
    *,
    no_backup: bool,
    backup_dir: str,
    no_build: bool,
    no_cache: bool,
    skip_doctor: bool,
    runtime_images: str | None,
    dry_run: bool,
    yes: bool,
    chat_retention_days: int | None = None,
) -> None:
    """Upgrade the running Fadenstack installation in place.

    Creates a backup, refreshes configuration (preserving secrets),
    rebuilds container images, and performs a rolling restart with
    a health gate. Then fetches the runtime images this release pins, which
    machines download from this server.
    """
    console.print("\n[bold magenta]fadenstack — Upgrade[/bold magenta]\n")

    cfg = load_config()

    if not cfg.install_dir:
        error(
            "No install directory configured.\n"
            "  Run 'faden deploy' first, or set install_dir in faden.yaml."
        )
        sys.exit(1)

    shared_dir = cfg.install_path
    compose_file = shared_dir / cfg.compose_file
    if not compose_file.exists():
        error(f"Compose file not found: {compose_file}")
        sys.exit(1)

    env_path = shared_dir / ".env"

    # An install made from the published images moves to this CLI's release;
    # a source checkout rebuilds from its own code.
    release = installed_release(shared_dir)
    if release is not None and _older(__version__, release):
        error(
            f"This CLI ({__version__}) is older than the install ({release}).\n"
            "  Its database has already been migrated past this release; going back is a restore:\n"
            "    faden restore <install_dir>/backups/<time>\n"
            "  To upgrade, install the newer CLI first: pip install -U fadenstack"
        )
        sys.exit(1)
    # Some releases cannot be skipped (a data migration the next one builds on): the feed says which.
    if release is not None and _older(release, __version__):
        first = _required_first(release)
        if first is not None:
            from faden_cli.core.release_feed import cli_install_command  # noqa: PLC0415
            from faden_cli.core.update_check import installer  # noqa: PLC0415

            error(
                f"{release} cannot upgrade straight to {__version__}: {first} has to come first.\n"
                f"    {cli_install_command(installer(), first, latest=False)}\n"
                "    faden upgrade\n"
                f"  Then install {__version__} again and run faden upgrade once more."
            )
            sys.exit(1)

    # Build compose file list (include GPU overlay if present)
    from faden_cli.core.compose import has_nvidia_gpu  # noqa: PLC0415

    compose_files: list[Path] = [compose_file]
    gpu_overlay = shared_dir / "docker-compose.gpu.yaml"
    if has_nvidia_gpu() and gpu_overlay.exists():
        compose_files.append(gpu_overlay)

    # Modules on by default that this install does not run yet (provenance): the upgrade switches them on,
    # unless an administrator switched them off (faden module disable, recorded in faden.yaml).
    from faden_cli.commands.module import default_modules  # noqa: PLC0415

    switching_on = default_modules(cfg)
    profiles = sorted({*(cfg.profiles or []), *(MODULES[name].profile for name in switching_on)})

    ctx = ComposeContext(
        compose_files=compose_files,
        env_file=env_path if env_path.exists() else None,
        project_dir=shared_dir,
        profiles=profiles,
    )

    # ── Dry-run summary ───────────────────────────────────────
    if dry_run:
        console.print("[bold]Dry-run — no changes will be made.[/bold]\n")
        console.print(f"  Install directory: {shared_dir}")
        console.print(f"  Compose file:      {compose_file.name}")
        console.print(f"  Profiles:          {', '.join(profiles) or '(none)'}")
        if switching_on:
            console.print(f"  Switched on:       {', '.join(switching_on)} (on by default)")
        console.print(f"  Backup:            {'skip' if no_backup else backup_dir}")
        if release is not None:
            console.print(f"  Release:           {release} -> {__version__} (pull published images)")
            newer = _newer_than_cli()
            if newer:
                console.print(f"  [yellow]Newer release:     {newer}[/yellow]")
        else:
            console.print(f"  Build images:      {'skip' if no_build else 'yes'}")
        console.print(f"  Build cache:       {'no' if no_cache else 'yes'}")
        console.print(f"  Runtime images:    {runtime_images or cfg.runtime_images} (for machines)")
        return

    # ── Confirm ───────────────────────────────────────────────
    if not yes:
        click.confirm("Proceed with upgrade?", default=True, abort=True)

    step = 0

    # ── 1. Pre-flight checks ─────────────────────────────────
    if not skip_doctor:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Pre-flight checks…[/bold cyan]")

        docker = detect_docker()
        if not docker.ok:
            error("Docker is not available or the daemon is not running.")
            if docker.error:
                error(f"  {docker.error}")
            sys.exit(1)
        success(
            f"Docker {docker.version}, "
            f"Compose {docker.compose_version}, "
            f"daemon running"
        )
    else:
        console.print("\n[dim]Skipping pre-flight checks (--skip-doctor).[/dim]")

    # The compose file has no fallback for its passwords, keys and tokens any
    # more, and every compose command stops on one missing -- the checks and
    # the backup's too. So an older .env gets them first. A missing key that
    # seals stored data keeps the value this install ran on
    # (env_gen.fill_required_env).
    if env_path.exists():
        from faden_cli.core.env_gen import fill_required_env  # noqa: PLC0415

        for note in fill_required_env(env_path, profiles=list(cfg.profiles)):
            warning(note)

    # Before the backup and the builds, not after: compose cannot replace a
    # container it did not create, and the restart would stop on it.
    from faden_cli.core.compose import foreign_containers  # noqa: PLC0415

    foreign = foreign_containers(ctx)
    if foreign:
        error(
            "These containers have names this installation uses, but it did not create them,\n"
            "  so it cannot replace them:\n"
            + "".join(
                f"    {name}  (made by {f'compose project {owner}' if owner else 'docker run'})\n"
                for name, owner in foreign
            )
            + "  Remove them -- their data volumes stay -- and run the upgrade again:\n"
            f"    docker rm -f {' '.join(name for name, _ in foreign)}"
        )
        sys.exit(1)

    # ── 2. Pre-upgrade backup ─────────────────────────────────
    if not no_backup:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Pre-upgrade backup…[/bold cyan]")

        backup_path = Path(backup_dir)
        if not backup_path.is_absolute():
            backup_path = shared_dir / backup_path

        result = create_backup(ctx, output_dir=backup_path, kind="upgrade")
        if not result.ok:
            for msg in result.errors:
                error(msg)
            error("Backup failed — aborting upgrade. Fix the issue or use --no-backup.")
            sys.exit(1)
        success(f"Backup created: {result.backup_dir}")
    else:
        console.print("\n[dim]Skipping backup (--no-backup).[/dim]")

    # ── 3. Refresh .env (preserve secrets) ────────────────────
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Refreshing environment…[/bold cyan]")

    if release is not None:
        changed = unpack(shared_dir)
        success(f"Deployment files for release {__version__} ({len(changed)} changed).")

    # The modules on by default this install did not run (see switching_on above): their profile in
    # faden.yaml, their switches and secrets in .env.
    if switching_on:
        cfg.profiles = profiles
        save_config(cfg)
        if env_path.exists():
            set_module_env(env_path, enable=switching_on)
        for name in switching_on:
            info(f"Switched on {name}: on by default (faden module disable {name} switches it off).")

    if env_path.exists():
        # What the install has wins; a new release only adds what it
        # introduced. Rewritten from the defaults with just the secrets kept,
        # every upgrade dropped what deploy and the operator had set --
        # HF_CACHE_DIR (the host's model cache), the admin name synced to
        # Grafana, ports -- and the services came back without them.
        env_vars = default_env_vars(profiles=list(cfg.profiles))
        env_vars.update(read_env_file(env_path))
        write_env_file(env_path, env_vars)
        from faden_cli.commands.deploy import pin_release, record_installer  # noqa: PLC0415

        if release is not None:
            pin_release(env_path)
        else:
            record_installer(env_path, shared_dir)
        success(".env refreshed (existing settings kept).")
        # Deleted chats are erased after a retention from this release on: asked once. Chats deleted
        # before count from the upgrade (gateway migration d3l3t3d4t), so none is erased sooner.
        from faden_cli.core.chat_retention import ask_retention  # noqa: PLC0415

        retention_days = ask_retention(
            env_path, days=chat_retention_days, yes=yes, heading="Deleted chats…",
        )
        # Sized again, as deploy does: what ``faden tune`` last chose.
        from faden_cli.core.tune import tune_env  # noqa: PLC0415

        tune_env(env_path)
        # RabbitMQ makes its users from definitions.json on every start; the
        # services log in with the passwords in .env. Written only by deploy,
        # the file kept an older install's passwords, and after an upgrade
        # the gateway and backend were refused ("invalid credentials").
        from faden_cli.commands.deploy import _regenerate_rmq_definitions  # noqa: PLC0415

        _regenerate_rmq_definitions(shared_dir, read_env_file(env_path), cfg.profiles)
        success("RabbitMQ users written from .env.")
    else:
        warning("No .env file found — skipping env refresh.")

    # .env, the saved credentials and the install's backups, readable by this user only -- an install
    # made before kept them readable by every local user. (A --backup-dir of the operator's keeps its
    # mode; the backup written into it is private.)
    from faden_cli.core.private_files import secure_install  # noqa: PLC0415

    secure_install(shared_dir)

    # The authority machines keep for when HTTPS is switched on (faden tls):
    # made while they can still fetch it over plain HTTP.
    from faden_cli.core import tls as edge_tls  # noqa: PLC0415

    try:
        ca_public, ca_created = edge_tls.ensure_ca(shared_dir)
    except edge_tls.TlsError as exc:
        warning(str(exc))
    else:
        if ca_created:
            success(f"Certificate authority for the machines made: {ca_public}")
    # The provenance engine's keys: what an older .env lacks, and a signer issued again when it is due
    # (fewer than 30 days left, or issued by an authority the install no longer uses).
    from faden_cli.core.provenance_keys import ensure_step as provenance_keys_step  # noqa: PLC0415

    provenance_keys_step(shared_dir, env_path)
    from faden_cli.commands.tls import refresh_edge_info  # noqa: PLC0415

    refresh_edge_info(shared_dir)

    # ── 4. Images ─────────────────────────────────────────────
    if release is not None:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Pulling release {__version__}…[/bold cyan]")
        from faden_cli.core.compose import pull as compose_pull  # noqa: PLC0415

        rc = compose_pull(ctx)
        if rc != 0:
            error("Could not pull this release's images; nothing was restarted.")
            sys.exit(rc)
        success("Images pulled.")
    elif not no_build:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Building container images…[/bold cyan]")
        console.print("[dim]This may take several minutes.[/dim]")

        pull_base_images()

        info("Building platform base image…")
        rc = build_base_image(ctx, no_cache=no_cache)
        if rc != 0:
            warning("Base image build failed — service builds may fail.")
        else:
            success("Base image built.")

        rc = compose_build(ctx, no_cache=no_cache)
        installation.record_build(shared_dir, complete=rc == 0)
        if rc != 0:
            warning("Some images failed to build. Continuing with available images.")
        else:
            success("All images built successfully.")
    else:
        console.print("\n[dim]Skipping image build (--no-build).[/dim]")

    # ── 5. Rolling restart ────────────────────────────────────
    # Behind the maintenance page, from here until the backend is healthy:
    # users see "being updated" rather than a gateway error, and an open
    # console reloads into that page and comes back by itself afterwards.
    # Lifted however this ends -- a failed upgrade is better reached.
    from faden_cli.core import maintenance  # noqa: PLC0415

    with maintenance.during(shared_dir, reason="upgrade"):
        step = _restart_and_check(ctx, shared_dir, env_path, step)

    # ── 7. Runtime images ─────────────────────────────────────
    # After the restart, not before it: the service images are what the
    # upgrade waits on, and a runtime image is several gigabytes that only
    # matters once a machine starts a cluster.
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Runtime images for machines…[/bold cyan]")
    from faden_cli.commands.runtime_images import run_step  # noqa: PLC0415

    run_step(cfg, value=runtime_images)

    # ── Chats stored in plain text: sealed now that the gateway has its key (an install from before
    # 2 October 2026 stored them so). While the platform is in use; a warning, never an abort.
    from faden_cli.core.sealing import seal_and_report  # noqa: PLC0415

    seal_and_report()
    if env_path.exists():
        from faden_cli.core.chat_retention import apply_retention, ask_retention  # noqa: PLC0415

        apply_retention(ask_retention(env_path, days=chat_retention_days, yes=True, heading=""))
        # The backup key: asked once of someone at the terminal; -y leaves backups as they were.
        from faden_cli.core import backup_key  # noqa: PLC0415

        backup_key.key_step(env_path.parent, yes=yes, deploy=False)

    # ── Done ──────────────────────────────────────────────────
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event("system.upgrade", "install", __version__)
    console.print()
    success("Upgrade complete.")


def _restart_and_check(ctx: ComposeContext, shared_dir: Path, env_path: Path, step: int) -> int:
    """Steps 5 and 6: restart on the new images, then wait for the backend; returns the last step's number."""
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Rolling restart…[/bold cyan]")

    # Chat attachments an older release kept inside the gateway container, which the restart below replaces:
    # copied into the volume they now have and verified, while the old container is still there. First of
    # all, before anything is replaced (syncing the password starts Postgres, which compose may recreate): if
    # they cannot be kept, the command stops here and the old containers keep running. With FADEN_DATA_DIR set,
    # the files of the named volumes it covers are copied into its folders the same way.
    from faden_cli.core.backup import keep_company_files  # noqa: PLC0415

    keep_company_files(env_path, ctx)

    # Sync postgres password before restarting (same as deploy)
    from faden_cli.commands.deploy import _sync_postgres_password, retire_fixed_logins  # noqa: PLC0415

    _sync_postgres_password(ctx, env_path)
    # Earlier releases made two logins with fixed passwords on every install.
    retire_fixed_logins(env_path)

    # The front door first, on its own: recreated with this release's config
    # it serves the maintenance page while the rest restarts -- even on the
    # first upgrade to a release that has one. Then everything else, so it
    # is not recreated a second time (a few seconds of nothing answering).
    from faden_cli.core.compose import service_names  # noqa: PLC0415

    front = "nginx"
    names = service_names(ctx)
    if front in names:
        rc = compose_up(ctx, services=[front], no_deps=True, pull="missing", force_recreate=True)
        if rc != 0:
            warning("The front door did not restart first; it restarts with the rest.")
            names = []
    rest = [name for name in names if name != front] if front in names and names else None

    # Bring everything up (force-recreate to pick up new images)
    rc = compose_up(
        ctx, services=rest, detach=True, build=False, pull="missing",
        force_recreate=True, wait=True, timeout=180,
    )
    if rc != 0:
        error(f"docker compose up failed (exit code {rc}).")
        sys.exit(rc)
    success("All services restarted.")
    from faden_cli.core.compose import report_inactive_modules  # noqa: PLC0415

    report_inactive_modules(ctx)

    # ClickHouse's diagnostic logs are off from this release; what they hold
    # stays until dropped (41 GB of trace_log filled one install's disk).
    from faden_cli.core.clickhouse import drop_stale_logs  # noqa: PLC0415

    try:
        dropped = drop_stale_logs(ctx)
    except RuntimeError as exc:
        warning(f"Could not clear ClickHouse's old diagnostic logs: {exc}")
    else:
        if dropped:
            success(f"Dropped ClickHouse's old diagnostic logs: {', '.join(dropped)}.")

    # ── 6. Health gate ────────────────────────────────────────
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Health check…[/bold cyan]")

    from faden_cli.core.bootstrap import wait_for_backend  # noqa: PLC0415
    from faden_cli.core.edge import console_url  # noqa: PLC0415

    backend_url = console_url(shared_dir, read_env_file(env_path) if env_path.exists() else {})

    console.print("  [dim]Waiting for backend to become healthy…[/dim]")
    if wait_for_backend(backend_url, timeout=120):
        success("Backend is healthy.")
    else:
        warning(
            "Backend did not become healthy within 120 s.\n"
            "  Check logs: faden logs backend"
        )
    return step


def _older(a: str, b: str) -> bool:
    """Whether release *a* comes before release *b* (``0.3.0`` < ``0.10.0``, ``0.5.0rc1`` < ``0.5.0``)."""
    from faden_cli.core.release_feed import is_newer  # noqa: PLC0415

    return is_newer(b, a)


def _newer_than_cli() -> str | None:
    """A release newer than this CLI's, from the last update check: the upgrade would stop short of it."""
    from faden_cli.core import release_feed, update_check  # noqa: PLC0415

    cfg = load_config()
    feed = update_check.cached_feed() if update_check.enabled(cfg) else None
    if feed is None:
        return None
    found = release_feed.assess(feed, __version__, cfg.update_channel)
    commands = release_feed.upgrade_commands(update_check.installer(), found)
    if not found.update_available or found.latest is None or not commands:
        return None
    return (
        f"{found.latest.version} is out; this CLI installs {__version__}. "
        f"For the newer one first: {commands[0]}"
    )


def _required_first(installed: str) -> str | None:
    """The release *installed* has to pass through before this CLI's, when the release feed says one."""
    from faden_cli.core import release_feed, update_check  # noqa: PLC0415

    cfg = load_config()
    feed = None
    if update_check.enabled(cfg):
        try:
            feed = update_check.refresh(cfg)
        except Exception:  # offline: the last check, if any
            feed = update_check.cached_feed()
    else:
        feed = update_check.cached_feed()
    return release_feed.required_first(feed, installed, __version__) if feed is not None else None
