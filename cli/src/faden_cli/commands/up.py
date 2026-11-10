"""``faden up`` — start Fadenstack services."""

from __future__ import annotations

import sys

import click

from faden_cli.core.compose import (
    ComposeContext,
    build_context_from_config,
    ps as compose_ps,
    report_inactive_modules,
    up as compose_up,
)
from faden_cli.core.console import console, success, error
from faden_cli.core.env_gen import write_env_file, default_env_vars
from faden_cli.core.settings import load_config

#: How long `faden up` waits for the services to be healthy: as `faden upgrade` waits after starting them.
WAIT_SECONDS = 180


def _not_healthy(ctx: ComposeContext) -> list[str]:
    """The services not running and healthy (one-off migrators that ended with 0 aside), as `faden restore` reads them."""
    found: list[str] = []
    for row in compose_ps(ctx):
        state, health = (row.state or row.status or "").lower(), (row.health or "").lower()
        if state == "exited" and row.exit_code == 0:
            continue
        if state != "running" or health in ("unhealthy", "starting"):
            found.append(f"{row.service or row.name} ({health or state})")
    return found


@click.command("up")
@click.option("-d", "--detach/--no-detach", default=True, help="Run in detached mode (default).")
@click.option("--build", "do_build", is_flag=True, help="Build images before starting.")
@click.option("--pull/--no-pull", default=False, help="Pull images before starting.")
@click.option("--env-gen/--no-env-gen", "gen_env", default=False, help="Regenerate .env file before starting.")
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Return once the services are running and healthy (default), as `faden upgrade` and `faden restore` do.",
)
@click.option(
    "--wait-timeout",
    type=click.IntRange(min=10),
    default=WAIT_SECONDS,
    show_default=True,
    help="Seconds to wait for the services to be healthy.",
)
@click.argument("services", nargs=-1)
def up_cmd(
    *,
    detach: bool,
    do_build: bool,
    pull: bool,
    gen_env: bool,
    wait: bool,
    wait_timeout: int,
    services: tuple[str, ...],
) -> None:
    """Start Fadenstack infrastructure and services, and wait until they are healthy.

    Optionally pass SERVICE names to start only specific containers.

    It waits because a service that has just started may not answer yet: a module just switched on
    (`faden module enable pii`) loads its model first, and a chat sent in that time was refused.
    """
    cfg = load_config()

    # Generate .env if requested or if it doesn't exist
    if gen_env:
        from pathlib import Path

        env_path = Path(cfg.install_dir) / ".env"
        console.print("[cyan]Generating .env file…[/cyan]")
        env_vars = default_env_vars(profiles=cfg.profiles)
        write_env_file(env_path, env_vars, preserve_secrets=True)
        success(f"Environment file written to {env_path}")
        from faden_cli.core.tune import tune_env  # noqa: PLC0415

        tune_env(env_path)

    ctx = build_context_from_config(cfg)

    # The provenance engine's keys: made when .env lacks them, its signer issued again when it is due.
    if ctx.env_file is not None and ctx.project_dir is not None:
        from faden_cli.core.provenance_keys import ensure_step as provenance_keys_step  # noqa: PLC0415

        provenance_keys_step(ctx.project_dir, ctx.env_file)

    console.print("[bold cyan]Starting Fadenstack services…[/bold cyan]")

    # With FADEN_DATA_DIR set in .env, the files of the named volumes it covers go into its folders first,
    # verified: the containers about to be created mount the (possibly empty) folders (see core.backup).
    from faden_cli.core.backup import keep_company_files  # noqa: PLC0415

    keep_company_files(ctx.env_file, ctx)

    waiting = wait and detach  # compose waits for detached services only
    returncode = compose_up(
        ctx,
        detach=detach,
        build=do_build,
        pull="always" if pull else "",
        services=list(services) if services else None,
        wait=waiting,
        timeout=wait_timeout if waiting else 0,
    )

    if returncode == 0:
        success("Services started and healthy." if waiting else "Services started successfully.")
        # Only when everything was started: naming services is a partial start.
        if not services:
            report_inactive_modules(ctx)
    else:
        if waiting and (sick := _not_healthy(ctx)):
            error(
                f"Not running and healthy within {wait_timeout} s: {', '.join(sick)}. `faden status` shows the "
                "services, `faden logs <service>` why."
            )
        error(f"docker compose up exited with code {returncode}.")
        sys.exit(returncode)
