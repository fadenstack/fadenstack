"""``faden backup`` — create a backup of all databases and config.

Examples:
    faden backup                          # backup to ./backups
    faden backup --output-dir /mnt/bak    # custom output directory
    faden backup --include-volumes        # also snapshot Docker volumes
    faden backup --db-only                # databases only, skip .env
    faden backup --retain 3               # keep only 3 most recent
    faden backup schedule                 # every night at 03:00, keep 7
    faden backup schedule --off           # stop the nightly backup
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from faden_cli.core.backup import BACKUP_KINDS, DEFAULT_KIND, create_backup
from faden_cli.core.compose import build_context_from_config
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.private_files import private_dir
from faden_cli.core.settings import load_config


@click.group("backup", invoke_without_command=True)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False),
    default="backups",
    show_default=True,
    help="Directory to store backups in.",
)
@click.option(
    "--include-volumes",
    is_flag=True,
    default=False,
    help="Also snapshot the install's Docker volumes: knowledge-base uploads, MinIO, logs, traces, Grafana"
    " (not Postgres's data, whose dumps are the backup, nor the models).",
)
@click.option(
    "--retain",
    type=int,
    default=5,
    show_default=True,
    help="Keep only the N most recent backups of this kind (nightly, manual and upgrade backups count apart).",
)
@click.option(
    "--kind",
    type=click.Choice(BACKUP_KINDS),
    default=DEFAULT_KIND,
    hidden=True,
    help="What the backup is, recorded in it; --retain only counts backups of the same kind. The nightly timer sets it.",
)
@click.option(
    "--db-only",
    is_flag=True,
    default=False,
    help="Dump databases only (skip .env and volumes).",
)
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_context
def backup_cmd(
    click_ctx: click.Context,
    *,
    output_dir: str,
    include_volumes: bool,
    retain: int,
    kind: str,
    db_only: bool,
    yes: bool,
) -> None:
    """Create a backup of Fadenstack databases, config, and optionally volumes."""
    if click_ctx.invoked_subcommand is not None:
        return
    console.print("\n[bold magenta]fadenstack — Backup[/bold magenta]\n")

    cfg = load_config()
    ctx = build_context_from_config(cfg)

    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = cfg.install_path / output_path

    if not yes:
        click.confirm(
            f"Create backup in {output_path}?",
            default=True,
            abort=True,
        )

    result = create_backup(
        ctx,
        output_dir=output_path,
        include_volumes=include_volumes,
        db_only=db_only,
        retain=retain,
        kind=kind,
    )

    if not result.ok:
        for msg in result.errors:
            error(msg)
        sys.exit(1)

    console.print()
    success(f"Backup created: {result.backup_dir}")
    console.print(f"  Databases: {', '.join(result.databases)}")
    if result.env_snapshot:
        console.print(f"  .env snapshot: {result.env_snapshot}")
    if result.volumes:
        console.print(f"  Volumes: {', '.join(result.volumes)}")
    if result.tls_material:
        console.print(
            f"  Certificate material: {len(result.tls_material)} file(s), private keys among them;"
            " keep the backup as private as .env"
        )
    if result.manifest_path:
        console.print(f"  Manifest: {result.manifest_path.name}")


@backup_cmd.command("schedule")
@click.option("--at", "at", default="03:00", show_default=True, help="Time of day, HH:MM (server time).")
@click.option("--retain", type=int, default=7, show_default=True, help="Keep the N most recent nightly backups.")
@click.option("--off", is_flag=True, default=False, help="Stop the nightly backup.")
def schedule_cmd(*, at: str, retain: int, off: bool) -> None:
    """Back up every night, keeping the most recent few.

    The backups land in <install_dir>/backups/, on the same disk as the
    data: copy them elsewhere too (or pass --output-dir to a mounted disk
    with `faden backup` from your own scheduler).
    """
    from faden_cli.core import schedule  # noqa: PLC0415

    if not schedule.available():
        error("No systemd user manager or cron here: schedule `faden backup -y` with your own scheduler.")
        sys.exit(1)
    if off:
        if schedule.remove():
            success("Nightly backup stopped.")
        else:
            info("No nightly backup was scheduled.")
        return
    try:
        hour, minute = (int(part) for part in at.split(":", 1))
        if not (0 <= hour < 24 and 0 <= minute < 60):  # noqa: PLR2004
            raise ValueError
    except ValueError:
        error(f"--at wants HH:MM, got {at!r}.")
        sys.exit(2)
    if retain < 1:
        error("--retain must keep at least one backup.")
        sys.exit(2)
    cfg = load_config()
    if not cfg.install_dir:
        error("No install to back up: run `faden deploy` first.")
        sys.exit(1)
    backups = private_dir(cfg.install_path / "backups")
    try:
        how, warnings = schedule.install(hour=hour, minute=minute, retain=retain, log=backups / "backup.log")
    except (RuntimeError, OSError) as exc:
        error(f"Could not schedule the backup: {exc}")
        sys.exit(1)
    success(f"Backing up every night at {hour:02d}:{minute:02d}, keeping {retain}: {backups} ({how})")
    for message in warnings:
        warning(message)
    warning("The backups are on the same disk as the data; copy them elsewhere too.")
