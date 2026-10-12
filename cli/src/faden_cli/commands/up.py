"""``faden up`` — start Fadenstack services."""

from __future__ import annotations

import sys

import click

from faden_cli.core.compose import build_context_from_config, up as compose_up
from faden_cli.core.console import console, success, error
from faden_cli.core.env_gen import write_env_file, default_env_vars
from faden_cli.core.settings import load_config


@click.command("up")
@click.option("-d", "--detach/--no-detach", default=True, help="Run in detached mode (default).")
@click.option("--build", "do_build", is_flag=True, help="Build images before starting.")
@click.option("--pull/--no-pull", default=False, help="Pull images before starting.")
@click.option("--env-gen/--no-env-gen", "gen_env", default=False, help="Regenerate .env file before starting.")
@click.argument("services", nargs=-1)
def up_cmd(
    *,
    detach: bool,
    do_build: bool,
    pull: bool,
    gen_env: bool,
    services: tuple[str, ...],
) -> None:
    """Start Fadenstack infrastructure and services.

    Optionally pass SERVICE names to start only specific containers.
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

    console.print("[bold cyan]Starting Fadenstack services…[/bold cyan]")

    # With FADEN_DATA_DIR set in .env, the files of the named volumes it covers go into its folders first,
    # verified: the containers about to be created mount the (possibly empty) folders (see core.backup).
    from faden_cli.core.backup import keep_company_files  # noqa: PLC0415

    keep_company_files(ctx.env_file, ctx)

    returncode = compose_up(
        ctx,
        detach=detach,
        build=do_build,
        pull=pull,
        services=list(services) if services else None,
    )

    if returncode == 0:
        success("Services started successfully.")
    else:
        error(f"docker compose up exited with code {returncode}.")
        sys.exit(returncode)
