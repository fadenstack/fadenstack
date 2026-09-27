"""``faden ports`` — the host ports the console, the API and /v1 are served on."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from faden_cli.core.console import console, error, success, warning
from faden_cli.core.edge import HTTP_PORT_KEY, HTTPS_PORT_KEY, check_ports, ports
from faden_cli.core.env_gen import read_env_file, write_env_file
from faden_cli.core.settings import load_config


def port_conflicts(http: int, https: int, *, current: tuple[int, int]) -> list[str]:
    """Which of the wanted ports something other than this install holds."""
    from faden_cli.core.detect import _ports_held_by_faden, check_port  # noqa: PLC0415

    ours = _ports_held_by_faden()
    problems = []
    for label, port in (("HTTP", http), ("HTTPS", https)):
        if port in current or port in ours:
            continue
        if check_port(port).in_use:
            problems.append(f"{label} port {port} is in use by something else.")
    return problems


def set_ports(env_path: Path, *, http: int | None, https: int | None) -> tuple[int, int] | None:
    """Write the wanted ports into *env_path*; the new pair, or ``None`` when nothing changed."""
    env = read_env_file(env_path)
    current = ports(env)
    wanted = (http if http is not None else current[0], https if https is not None else current[1])
    if wanted == current:
        return None
    problem = check_ports(*wanted)
    if problem:
        raise click.UsageError(problem)
    env[HTTP_PORT_KEY] = str(wanted[0])
    env[HTTPS_PORT_KEY] = str(wanted[1])
    write_env_file(env_path, env)
    return wanted


@click.command("ports")
@click.option("--http", "http", type=int, default=None, help="Host port for plain HTTP (default 80).")
@click.option("--https", "https", type=int, default=None, help="Host port for HTTPS (default 443).")
@click.option(
    "--no-restart", is_flag=True, help="Write .env only; the proxy moves when it is next recreated."
)
def ports_cmd(*, http: int | None, https: int | None, no_restart: bool) -> None:
    """Show or change the ports the console, the API and /v1 are served on.

    \b
    faden ports                        show them
    faden ports --http 8080 --https 8443
    """
    from faden_cli.commands.tls import addresses, apply_to_proxy  # noqa: PLC0415

    cfg = load_config()
    if not cfg.install_dir:
        error("No install found. Run faden deploy first.")
        sys.exit(1)
    install_dir = Path(cfg.install_dir)
    env_path = install_dir / ".env"
    current = ports(read_env_file(env_path))

    if http is None and https is None:
        console.print(f"HTTP on port [bold]{current[0]}[/bold], HTTPS on port [bold]{current[1]}[/bold].")
        console.print("  " + "  ".join(addresses(install_dir)))
        return

    wanted = (http if http is not None else current[0], https if https is not None else current[1])
    problem = check_ports(*wanted)
    if problem:
        error(problem)
        sys.exit(2)
    for conflict in port_conflicts(*wanted, current=current):
        warning(conflict + " The proxy cannot start on it until it is free.")

    if set_ports(env_path, http=http, https=https) is None:
        console.print("Nothing to change.")
        return
    success(f"HTTP on port {wanted[0]}, HTTPS on port {wanted[1]} (written to {env_path}).")
    if not no_restart and not apply_to_proxy(install_dir, recreate=True):
        sys.exit(1)
    from faden_cli.commands.tls import refresh_edge_info  # noqa: PLC0415

    refresh_edge_info(install_dir)
    console.print("  " + "  ".join(addresses(install_dir)))
