"""``faden rebuild`` — rebuild and redeploy individual services.

Rebuild one or more services without touching the rest of the stack.
Useful for developers who changed frontend code, backend code, etc.
and want a quick turnaround without a full ``faden deploy``.

Examples:
    faden rebuild frontend          # rebuild + restart frontend only
    faden rebuild backend api       # rebuild backend and api
    faden rebuild --all             # rebuild all app services
"""

from __future__ import annotations

import sys

import click

from faden_cli.core.compose import (
    ComposeContext,
    build as compose_build,
    build_context_from_config,
    up as compose_up,
)
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.settings import load_config


# Map friendly names → compose service names.
# Keeps the CLI developer-friendly (``faden rebuild frontend``)
# while resolving to the actual compose service name.
SERVICE_ALIASES: dict[str, list[str]] = {
    "frontend": ["faden-frontend"],
    "backend": ["faden-backend-migrator", "faden-backend", "faden-backend-worker"],
    "gateway": ["faden-gateway-migrator", "faden-gateway"],
    "mcp": ["faden-mcp"],
    "skills": ["faden-skills"],
    "pii": ["faden-pii", "faden-pii-worker"],
    "rag": ["faden-rag"],
    "auth": ["faden-auth"],
    "mailer": ["faden-mailer"],
    "docling": ["faden-docling"],
    "nginx": ["faden-nginx"],
}

# Services that don't have a build context (they reuse another service's image).
# compose_build() skips these; they just need their container recreated.
_NO_BUILD_SERVICES: frozenset[str] = frozenset({
    "faden-backend-migrator",
    "faden-gateway-migrator",
})


def _resolve_services(names: tuple[str, ...]) -> list[str]:
    """Resolve friendly names to compose service names."""
    resolved: list[str] = []
    for name in names:
        name = name.lower().strip()
        if name in SERVICE_ALIASES:
            resolved.extend(SERVICE_ALIASES[name])
        elif name.startswith("faden-"):
            resolved.append(name)
        else:
            # Try with prefix
            prefixed = f"faden-{name}"
            resolved.append(prefixed)
    return list(dict.fromkeys(resolved))  # dedupe, preserve order


@click.command("rebuild")
@click.argument("services", nargs=-1)
@click.option("--all", "rebuild_all", is_flag=True, help="Rebuild all application services.")
@click.option("--no-cache", is_flag=True, help="Build without Docker cache.")
@click.option("--no-deps", is_flag=True, default=True, help="Don't restart dependent services (default: true).")
@click.option("--with-deps", is_flag=True, help="Also restart dependent services.")
def rebuild_cmd(
    services: tuple[str, ...],
    *,
    rebuild_all: bool,
    no_cache: bool,
    no_deps: bool,
    with_deps: bool,
) -> None:
    """Rebuild and redeploy specific services.

    Pass one or more service names (e.g. frontend, backend, api, mcp).
    The service image is rebuilt from source and the container is
    recreated with the latest .env values — no full redeploy needed.

    \b
    Examples:
        faden rebuild frontend           # just the React UI
        faden rebuild backend            # backend + worker
        faden rebuild backend api        # multiple services
        faden rebuild --all              # all app images
        faden rebuild frontend --no-cache  # clean build
    """
    if not services and not rebuild_all:
        # Show available aliases
        console.print("[bold cyan]Available services:[/bold cyan]")
        for alias, targets in sorted(SERVICE_ALIASES.items()):
            console.print(f"  [bold]{alias:12s}[/bold] → {', '.join(targets)}")
        console.print("\n[dim]Usage: faden rebuild <service> [service...][/dim]")
        console.print("[dim]       faden rebuild --all[/dim]")
        return

    cfg = load_config()
    ctx = build_context_from_config(cfg)

    if rebuild_all:
        # Build all services that have a build context
        target_services = None  # compose build with no args = build all
        info("Rebuilding all application images…")
    else:
        target_services = _resolve_services(services)
        info(f"Rebuilding: {', '.join(target_services)}")

    # Step 1: Build (skip migrators — they share the parent service image)
    build_services = (
        [s for s in target_services if s not in _NO_BUILD_SERVICES]
        if target_services
        else None
    )
    console.print("\n[bold cyan]Building images…[/bold cyan]")
    rc = compose_build(ctx, services=build_services or None, no_cache=no_cache)
    if rc != 0:
        error(f"Build failed (exit code {rc}).")
        sys.exit(rc)
    success("Images built.")

    # Step 1b: Sync Postgres password if .env credentials differ from
    # what the running Postgres volume was initialised with (e.g. after
    # deploy-remote.sh updated the .env or a manual edit).
    env_path = ctx.env_file
    if env_path and env_path.exists():
        from faden_cli.commands.deploy import _sync_postgres_password  # noqa: PLC0415

        _sync_postgres_password(ctx, env_path)

    # The gateway's chat files: from an old container into the volume it now has (see core.backup).
    if not target_services or any("gateway" in name for name in target_services):
        from faden_cli.core.backup import keep_chat_files  # noqa: PLC0415

        keep_chat_files(env_path)

    # Step 2: Recreate containers (picks up new image + new .env)
    console.print("\n[bold cyan]Recreating containers…[/bold cyan]")

    cmd = ctx.base_cmd() + ["up", "-d", "--force-recreate"]
    if no_deps and not with_deps:
        cmd.append("--no-deps")
    if target_services:
        cmd.extend(target_services)

    import subprocess
    result = subprocess.run(cmd, check=False)  # noqa: S603
    if result.returncode != 0:
        error(f"Container recreation failed (exit code {result.returncode}).")
        sys.exit(result.returncode)

    success("Services rebuilt and redeployed.")
    if target_services:
        console.print(f"  [dim]Rebuilt: {', '.join(target_services)}[/dim]")
    console.print("  [dim]Tip: use 'faden logs <service>' to follow logs.[/dim]")
