"""``faden backup`` — create a backup of all databases and config.

Examples:
    faden backup                          # backup to ./backups
    faden backup --output-dir /mnt/bak    # custom output directory
    faden backup --include-volumes        # also snapshot Docker volumes
    faden backup --db-only                # databases only, skip .env
    faden backup --retain 3               # keep only 3 most recent
    faden backup schedule                 # every night at 03:00, keep 7
    faden backup schedule --off           # stop the nightly backup
    faden backup key create               # encrypt backups; the recovery key is shown once
    faden backup key check                # does a kept copy of the recovery key still match?
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from faden_cli.core import backup_key
from faden_cli.core.backup import BACKUP_KINDS, DEFAULT_KIND, create_backup
from faden_cli.core.backup_crypto import KeyTextError
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
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event(
        "backup.create", "install", Path(str(result.backup_dir)).name,
        metadata={
            "kind": kind, "databases": len(result.databases), "volumes": list(result.volumes or []),
            "encrypted_to": result.encrypted_to or None,
        },
        severity="normal",
    )
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
    if result.encrypted_to:
        console.print(f"  Encrypted to the backup key {result.encrypted_to}: restoring it takes the recovery key.")
    if note := backup_key.reminder(cfg.install_path):
        warning(note)


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


# ── The backup key ────────────────────────────────────────────────


def _install() -> Path:
    cfg = load_config()
    if not cfg.install_dir:
        error("No install here: run `faden deploy` first.")
        sys.exit(1)
    return cfg.install_path


def key_text(content: str) -> str:
    """The key in a file's or a prompt's *content*: its last line that is not empty (a key file explains itself)."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return lines[-1] if lines else ""


@backup_cmd.group("key")
def key_group() -> None:
    """The backup key: every backup is encrypted to it, and only its recovery key restores one."""


@key_group.command("create")
@click.option(
    "--write-to",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the recovery key to this file (a USB stick, never inside the install folder) instead of showing it.",
)
@click.option(
    "--replace", is_flag=True, default=False,
    help="Make a new key although there is one. The backups made so far still need the old recovery key.",
)
@click.option(
    "-y", "--yes", is_flag=True, default=False,
    help="Unattended: leave the recovery key in the install folder until `faden backup key confirm`.",
)
def key_create_cmd(*, write_to: Path | None, replace: bool, yes: bool) -> None:
    """Make the backup key. The recovery key is shown once, and two groups of it are asked back."""
    install = _install()
    key = backup_key.create(install, unattended=yes, write_to=write_to, replace=replace)
    success(f"From now on every backup is encrypted to the backup key {key.id}.")
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event(
        "backup.key.create", "install", key.id,
        metadata={"replaced": replace, "left_on_server": yes and write_to is None},
    )
    if note := backup_key.reminder(install):
        warning(note)


@key_group.command("confirm")
def key_confirm_cmd() -> None:
    """After an unattended deploy: prove a copy of the recovery key is kept, then remove it from the server."""
    install = _install()
    if backup_key.confirm(install):
        from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

        current = backup_key.recipient(install)
        record_event("backup.key.confirm", "install", backup_key.backup_crypto.key_id(current) if current else "")


@key_group.command("check")
@click.option(
    "--key-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
    help="Read the recovery key from this file instead of asking for it.",
)
def key_check_cmd(*, key_file: Path | None) -> None:
    """Check that a kept copy of the recovery key is still this install's: nothing is stored.

    Do it once a year, and with every restore test.
    """
    install = _install()
    text = key_text(key_file.read_text(encoding="utf-8")) if key_file else click.prompt(
        "Recovery key", hide_input=True,
    )
    try:
        found = backup_key.check(install, text)
    except (KeyTextError, backup_key.KeyMissingError) as exc:
        error(f"{exc}")
        sys.exit(1)
    success(f"This is the install's backup key ({found}): it opens the backups made with it.")


@key_group.command("status")
def key_status_cmd() -> None:
    """Whether backups are encrypted, to which key, and what is still to do."""
    install = _install()
    current = backup_key.recipient(install)
    if current is not None:
        info(f"Backups are encrypted to the backup key {backup_key.backup_crypto.key_id(current)}.")
    if note := backup_key.reminder(install):
        warning(note)


