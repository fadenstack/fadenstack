"""``faden restore`` — restore from a backup directory.

Examples:
    faden restore backups/20250115T120000Z
    faden restore backups/20250115T120000Z --verify-only
    faden restore backups/20250115T120000Z --db-only
    faden restore backups/20250115T120000Z --skip-env
    faden restore backups/20250115T120000Z --key-file /media/usb/fadenstack-recovery-key.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from faden_cli.core import backup_crypto, backup_key, maintenance
from faden_cli.core.backup import (
    RestoreResult,
    read_manifest,
    restore_backup,
    verify_backup,
)
from faden_cli.core.compose import (
    ComposeContext,
    build_context_from_config,
    restart as compose_restart,
    service_names,
    stop as compose_stop,
    up as compose_up,
)
from faden_cli.core.console import console, error, info, success, warning
from faden_cli.core.settings import FadenConfig, load_config
from faden_cli.commands.deploy import _sync_postgres_password


@click.command("restore")
@click.argument("backup_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--db-only", is_flag=True, default=False, help="Restore databases only.")
@click.option("--skip-env", is_flag=True, default=False, help="Don't overwrite the current .env.")
@click.option(
    "--verify-only",
    is_flag=True,
    default=False,
    help="Validate checksums without restoring.",
)
@click.option(
    "--key-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="The recovery key, for an encrypted backup; otherwise it is asked for.",
)
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def restore_cmd(
    backup_dir: str,
    *,
    db_only: bool,
    skip_env: bool,
    verify_only: bool,
    key_file: Path | None,
    yes: bool,
) -> None:
    """Restore Fadenstack from a backup directory.

    BACKUP_DIR is the path to a timestamped backup folder containing
    a manifest.json file created by ``faden backup``. An encrypted backup
    is opened with the recovery key first.
    """
    console.print("\n[bold magenta]fadenstack — Restore[/bold magenta]\n")

    backup_path = Path(backup_dir).resolve()
    if not backup_crypto.is_encrypted(backup_path):
        _restore_cmd(backup_path, backup_path, db_only=db_only, skip_env=skip_env, verify_only=verify_only, yes=yes)
        return
    opened = _open(backup_path, key_file)
    try:
        _restore_cmd(backup_path, opened, db_only=db_only, skip_env=skip_env, verify_only=verify_only, yes=yes)
    finally:
        backup_crypto.close_backup(backup_path)


#: Recovery keys typed wrong before the restore stops.
_KEY_ATTEMPTS = 3


def _open(backup_path: Path, key_file: Path | None) -> Path:
    """Decrypt the encrypted backup with the recovery key: from --key-file, still on the server, or asked."""
    from faden_cli.commands.backup import key_text  # noqa: PLC0415

    index = backup_crypto.read_index(backup_path) or {}
    info(f"This backup is encrypted to the backup key {index.get('key_id') or 'unknown'}.")
    cfg = load_config()
    left = backup_key.pending(cfg.install_path) if cfg.install_dir else None
    given = key_file or left
    if left is not None and key_file is None:
        info(f"Using the recovery key still on this server ({left}).")
    for attempt in range(1, _KEY_ATTEMPTS + 1):
        text = key_text(given.read_text(encoding="utf-8")) if given else click.prompt("Recovery key", hide_input=True)
        try:
            key = backup_crypto.RecoveryKey.from_text(text)
            info("Opening the backup…")
            return backup_crypto.open_backup(backup_path, key)
        except (backup_crypto.KeyTextError, backup_crypto.OpenError) as exc:
            error(f"The backup does not open with that key: {exc}")
            if given or attempt == _KEY_ATTEMPTS:
                sys.exit(1)
    sys.exit(1)


def _restore_cmd(
    backup_path: Path, source: Path, *, db_only: bool, skip_env: bool, verify_only: bool, yes: bool,
) -> None:
    """Check the backup in *source* (the backup itself, or its decrypted copy) and restore it."""
    manifest = read_manifest(source)
    if manifest is None:
        error(f"No manifest.json found in {source}")
        sys.exit(1)

    # ── Show backup info ──────────────────────────────────────
    console.print(f"  Backup date:    {manifest.get('created_at', 'unknown')}")
    console.print(f"  faden version: {manifest.get('faden_version', 'unknown')}")
    db_names = list(manifest.get("databases", {}).keys())
    console.print(f"  Databases:      {', '.join(db_names)}")
    if manifest.get("env_snapshot"):
        console.print(f"  .env snapshot:  {manifest['env_snapshot']}")
    if manifest.get("volumes"):
        console.print(f"  Volumes:        {', '.join(manifest['volumes'])}")
    console.print()

    # ── Verify checksums ──────────────────────────────────────
    info("Verifying backup checksums…")
    check_errors = verify_backup(source, manifest)
    if check_errors:
        for msg in check_errors:
            error(f"  {msg}")
        sys.exit(1)
    success("Checksums OK.")

    if verify_only:
        return

    # ── Confirm ───────────────────────────────────────────────
    if not yes:
        console.print()
        warning("This will overwrite current databases with the backup data.")
        click.confirm("Proceed with restore?", default=False, abort=True)

    cfg = load_config()
    ctx = build_context_from_config(cfg)

    # Behind the maintenance page for the whole restore, as the upgrade is: the front door stays up and
    # tells users the platform is being worked on, instead of refusing connections until the end. Lifted
    # however this ends. nginx holds none of the volumes a restore writes (it mounts the install's nginx/
    # and tls/ directories, read-only), so it can run through it.
    with maintenance.during(Path(str(ctx.project_dir)), reason="restore"):
        result = _restore(ctx, cfg, source, db_only=db_only, skip_env=skip_env)

    console.print()
    success("Restore complete.")
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event(
        "backup.restore", "install", Path(str(backup_path)).name,
        metadata={
            "databases": list(result.databases_restored or []), "db_only": db_only,
            "encrypted": source != backup_path,
        },
    )
    if result.databases_restored:
        console.print(f"  Databases: {', '.join(result.databases_restored)}")
    if result.env_restored:
        console.print("  .env: restored")
    if result.volumes_restored:
        console.print(f"  Volumes: {', '.join(result.volumes_restored)}")
    if result.tls_restored:
        console.print(f"  Certificate material: {len(result.tls_restored)} file(s); the machines still trust it")


#: The front door, which keeps running (and serving the maintenance page) while everything else is restored.
_FRONT = "nginx"


def _restore(
    ctx: ComposeContext, cfg: FadenConfig, backup_path: Path, *, db_only: bool, skip_env: bool
) -> RestoreResult:
    """Stop everything but nginx, restore, and start everything again; exits when the restore fails."""
    console.print("\n[bold cyan]Stopping application services…[/bold cyan]")
    names = service_names(ctx)
    # Stopped, not taken down: nginx is left alone, and the containers keep their volumes and networks.
    # postgres is stopped with the rest and started again below, so the restore starts from a clean server.
    compose_stop(ctx, services=[name for name in names if name != _FRONT] or None)
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    result = restore_backup(
        ctx,
        backup_dir=backup_path,
        db_only=db_only,
        skip_env=skip_env,
    )

    if not result.ok:
        for msg in result.errors:
            error(msg)
        sys.exit(1)

    # ── Sync Postgres password with restored .env ─────────────
    if result.env_restored:
        env_path = ctx.env_file or Path(ctx.project_dir) / ".env"
        # A .env backed up before a release required it may lack a key the compose file now requires.
        from faden_cli.core.env_gen import fill_required_env  # noqa: PLC0415

        for note in fill_required_env(env_path, profiles=list(cfg.profiles)):
            warning(note)
        info("Syncing Postgres password with restored .env…")
        _sync_postgres_password(ctx, env_path)
    # The provenance signer of a .env from before the module, or issued by an authority the restore replaced.
    if (result.env_restored or result.tls_restored) and ctx.env_file and ctx.project_dir:
        from faden_cli.core.provenance_keys import ensure_step as provenance_keys_step  # noqa: PLC0415

        provenance_keys_step(Path(ctx.project_dir), Path(str(ctx.env_file)))

    # ── Restart all services ──────────────────────────────────
    console.print("\n[bold cyan]Restarting all services…[/bold cyan]")
    compose_up(ctx, detach=True, wait=True, timeout=120)
    # nginx read the certificate and picked plain HTTP or HTTPS when it started; a restored one is
    # only served after it starts again.
    if result.tls_restored and _FRONT in names:
        compose_restart(ctx, services=[_FRONT])
    return result
