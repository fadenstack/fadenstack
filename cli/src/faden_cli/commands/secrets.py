"""``faden secrets`` -- the keys that seal an install's stored data.

Examples:
    faden secrets status                  # which keys are published defaults; is a rotation unfinished
    faden secrets rotate                  # new random keys, every stored value re-sealed
    faden secrets rotate --only settings  # just the settings master key
    faden secrets rotate --only provenance  # a new signing key and certificate, a new hash-key version
    faden secrets rotate --resume         # finish a rotation that stopped
"""

from __future__ import annotations

import sys

import click
from rich.table import Table

from faden_cli.core import secret_rotation as rotation
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.env_gen import ALLOW_PUBLISHED_KEYS, CREDENTIAL_PEPPER, read_env_file
from faden_cli.core.settings import load_config


def _config():  # noqa: ANN202
    cfg = load_config()
    if not cfg.install_dir:
        error(
            "No install directory configured.\n"
            "  Run 'faden deploy' first, or set install_dir in faden.yaml."
        )
        sys.exit(1)
    return cfg


def _hash_key_versions(env: dict[str, str]) -> str:
    """How many provenance hash-key and watermark-key versions .env holds: counts, never a key."""
    from faden_cli.core import provenance_keys as pk  # noqa: PLC0415

    shown: list[str] = []
    for name in pk.VERSIONED_KEYS:
        try:
            count = len(pk.parse_versions(env.get(name, ""), name))
        except pk.ProvenanceKeyError:
            shown.append(f"[red]{name} does not read[/red]")
            continue
        label = pk.LABELS[name]
        missing = f"[red]no {label}[/red] (faden up makes one)"
        shown.append(f"{count} {label} version(s)" if count else missing)
    return ", ".join(shown) + ", all kept"


def _shown(state: rotation.KeyState, env: dict[str, str], profiles: list[str]) -> str:
    """A key's state in the status table."""
    scope = state.scope
    if scope.profile and scope.profile not in profiles:
        shown = "[dim]module off[/dim]"
    elif not state.present:
        shown = {
            "chat": "[dim]not set (stored unencrypted)[/dim]",
            "provenance": "[red]not set[/red] (faden up issues it)",
        }.get(scope.name, "[red]not set[/red]")
    elif scope.name == "provenance":
        shown = f"[green]set[/green], {_hash_key_versions(env)}"
    elif state.published:
        shown = "[red]published default[/red] (anyone who reads the source code knows it)"
    else:
        shown = "[green]own random value[/green]"
    if state.unfinished:
        shown += " [yellow]- rotation unfinished[/yellow]"
    return shown


@click.group("secrets")
def secrets_group() -> None:
    """The keys that seal stored data: see which are defaults, and rotate them."""


@secrets_group.command("status")
def status_cmd() -> None:
    """Which keys are published defaults, and whether a rotation is unfinished. Never prints a key."""
    cfg = _config()
    env = read_env_file(cfg.env_path)
    states = rotation.key_states(env, cfg.profiles)

    table = Table(title="Keys that seal stored data", show_header=True, header_style="bold cyan")
    table.add_column("Key")
    table.add_column("Seals")
    table.add_column("State")
    for state in states:
        table.add_row(state.scope.key, state.scope.seals, _shown(state, env, cfg.profiles))
    console.print(table)

    other = [name for name, value in rotation.OTHER_PUBLISHED.items() if env.get(name) == value]
    if other:
        warning(
            f"{', '.join(other)} (Langfuse) are published defaults too. `faden secrets rotate` does not cover "
            "them: Langfuse seals its own data with them."
        )
    if env.get(CREDENTIAL_PEPPER):
        info(f"{CREDENTIAL_PEPPER} is set: rotating the keys leaves the machines connected.")
    else:
        warning(
            f"{CREDENTIAL_PEPPER} is not in .env. `faden upgrade` adds it; rotate only after that, or the "
            "machines would have to enroll again."
        )
    if env.get(ALLOW_PUBLISHED_KEYS) == "true":
        warning(f"{ALLOW_PUBLISHED_KEYS}=true: the services start with a published key and warn at every start.")

    unfinished = [s.scope.name for s in states if s.unfinished]
    if unfinished:
        warning(f"Unfinished rotation ({', '.join(unfinished)}). Finish it: faden secrets rotate --resume")
    elif any(s.published and s.applicable for s in states):
        info("Replace the published keys: faden secrets rotate")


@secrets_group.command("rotate")
@click.option(
    "--only",
    "only",
    type=click.Choice(rotation.SCOPE_NAMES),
    multiple=True,
    help="Rotate just this key (repeatable). Default: every key the install uses.",
)
@click.option("--resume", is_flag=True, help="Finish a rotation that stopped (running the command again does the same).")
@click.option(
    "--skip-unreadable",
    is_flag=True,
    help="Leave stored values that neither the new nor the old key opens as they are, instead of stopping.",
)
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation.")
def rotate_cmd(*, only: tuple[str, ...], resume: bool, skip_unreadable: bool, yes: bool) -> None:
    """Replace the keys that seal stored data with new random ones.

    Takes a backup, writes the new keys to .env next to the old ones, restarts the services that use them
    behind the maintenance page, re-seals every stored value in one transaction per database, checks it,
    and only then drops the old keys. The machines stay connected. If it stops, run it again.

    The provenance engine gets a new signing key and certificate, and a new hash-key version; every earlier
    version stays, since what was hashed with it is checked with it.
    """
    cfg = _config()
    env = read_env_file(cfg.env_path)
    try:
        plan = rotation.make_plan(env, cfg.profiles, list(only) or None, resume=resume)
    except rotation.RotationError as exc:
        error(str(exc))
        sys.exit(1)

    console.print("\n[bold magenta]fadenstack - Rotating keys[/bold magenta]\n")
    if plan.mode == "resume":
        info("A rotation is unfinished; this run finishes it before anything else.")
    for scope in plan.scopes:
        console.print(f"  [bold]{scope.key}[/bold] {'seals' if scope.renew is None else 'is for'} {scope.seals}")
    if plan.deferred:
        info("Then run it again for: " + ", ".join(s.name for s in plan.deferred))
    console.print(
        "\nThe platform shows the maintenance page while the services restart and the values are re-sealed."
    )
    if not yes:
        click.confirm("Proceed?", default=True, abort=True)

    ops = rotation.ComposeOperations(cfg)
    try:
        results = rotation.rotate(
            ops, plan, skip_unreadable=skip_unreadable, say=lambda line: console.print(f"[dim]{line}[/dim]"),
        )
    except rotation.RotationError as exc:
        error(str(exc))
        sys.exit(1)

    console.print()
    for line in rotation.summary_lines(results):
        console.print(line)
    success("Keys rotated. Machines stay connected; the old keys are gone from .env.")
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event("secrets.rotate", "install", "keys", metadata={"scopes": [s.name for s in plan.scopes]})
    if any(scope.name == "chat" for scope in plan.scopes):
        # A rotation re-seals what was sealed; what was stored in plain text is sealed with the new key now.
        from faden_cli.core.sealing import seal_and_report  # noqa: PLC0415

        seal_and_report()
    if plan.deferred:
        info("Not yet rotated: " + ", ".join(s.name for s in plan.deferred) + " (run faden secrets rotate again).")


@secrets_group.command("seal")
def seal_cmd() -> None:
    """Seal the chats stored in plain text: from before the install had a key, or before a column was sealed.

    Runs while the platform is in use, in batches; run it again to finish a run that stopped. Values that look
    sealed but open with no key are counted and left as they are.
    """
    from faden_cli.core.sealing import seal_and_report  # noqa: PLC0415

    if not seal_and_report():
        sys.exit(1)
