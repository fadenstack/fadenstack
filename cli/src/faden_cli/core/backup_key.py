"""The backup key: made once, its recovery key shown once, kept off the server, and proven kept.

Large systems handle a key that must not live on the machine it protects the same way (BitLocker's recovery
key, Apple's recovery key, a wallet's seed words): shown once, nothing goes on until the person has kept it,
and a check that they did. Here the check asks for two groups of the key at random positions; random, not the
last ones, because the end of a key is what is still on the screen when someone copied half of it.

The install keeps the public key (``backup-key.pub``): every backup is encrypted to it. An unattended deploy
cannot show the key to anyone; it writes it to ``backup-recovery-key.txt`` (owner-only) in the install folder,
and ``faden backup key confirm`` removes that file once the person has a copy and proved it.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import click
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

from faden_cli.core import backup_crypto
from faden_cli.core.backup_crypto import RecoveryKey
from faden_cli.core.console import console, info, success, warning
from faden_cli.core.private_files import write_private

PUBLIC_FILE = "backup-key.pub"
PENDING_FILE = "backup-recovery-key.txt"
#: Groups asked for, and wrong answers before the key is shown again from the start.
ASKED_GROUPS = 2
ATTEMPTS = 3


class KeyMissingError(RuntimeError):
    """This install has no backup key yet."""


def recipient(install_dir: Path) -> X25519PublicKey | None:
    """The public key backups are encrypted to; None when this install has none yet."""
    path = install_dir / PUBLIC_FILE
    if not path.is_file():
        return None
    return backup_crypto.public_key(path.read_text(encoding="utf-8").strip())


def pending(install_dir: Path) -> Path | None:
    """The recovery key an unattended deploy left on the server, until ``faden backup key confirm``."""
    path = install_dir / PENDING_FILE
    return path if path.is_file() else None


def reminder(install_dir: Path) -> str | None:
    """What still has to be done about the backup key, if anything."""
    if (path := pending(install_dir)) is not None:
        return (
            f"The backup recovery key is still on this server ({path}). Copy it somewhere safe off this server, "
            "then run `faden backup key confirm`."
        )
    if recipient(install_dir) is None:
        return "Backups are not encrypted: run `faden backup key create` to make the backup key."
    return None


def _show(key: RecoveryKey) -> None:
    prefix, parts = backup_crypto.groups(key.secret)
    console.print("\n[bold]Backup recovery key[/bold] (shown once; it opens every backup from now on)\n")
    console.print(f"    [bold]{prefix}[/bold]")
    for row in range(0, len(parts), 4):
        row_parts = range(row, min(row + 4, len(parts)))
        cells = "   ".join(f"[dim]{i + 1:>2}[/dim] [bold]{parts[i]}[/bold]" for i in row_parts)
        console.print(f"    {cells}")
    console.print(
        "\n  Write it down or store it in your password manager, off this server. Without it no backup can be"
        "\n  restored, by anyone. Fadenstack keeps no copy.\n"
    )


def _proven(key: RecoveryKey) -> bool:
    """Ask for groups at random positions; True when they match, False after too many wrong answers."""
    _, parts = backup_crypto.groups(key.secret)
    asked = sorted(secrets.SystemRandom().sample(range(len(parts)), ASKED_GROUPS))
    wrong = 0
    for index in asked:
        while True:
            answer = click.prompt(f"  Group {index + 1} of your copy", default="", show_default=False)
            if "".join(answer.split()).lower() == parts[index].lower():
                break
            wrong += 1
            if wrong >= ATTEMPTS:
                return False
            warning(f"That is not group {index + 1}. Look at your copy again.")
    return True


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def ceremony(key: RecoveryKey, install_dir: Path, *, write_to: Path | None = None) -> None:
    """Show the recovery key, have the person keep it, and prove they did; raises click.Abort if they stop."""
    if write_to is not None:
        if _inside(write_to, install_dir):
            raise click.UsageError(f"{write_to} is inside the install folder: the key has to be kept off it.")
        write_private(write_to, (key.secret + "\n").encode("utf-8"))
        if RecoveryKey.from_text(write_to.read_text(encoding="utf-8")).secret != key.secret:
            raise click.ClickException(f"The key read back from {write_to} is not the key written.")
        success(f"Recovery key written to {write_to}. Keep that copy off this server.")
        return
    while True:
        _show(key)
        click.confirm("  I have kept a copy of this key off this server", default=False, abort=True)
        click.clear()
        console.print("\n[bold]Check of your copy[/bold]: type two groups of the key from what you kept.\n")
        if _proven(key):
            success("Your copy of the recovery key matches.")
            return
        warning("Those groups do not match your key. It is shown again; check your copy against it.")


def create(
    install_dir: Path, *, unattended: bool, write_to: Path | None = None, replace: bool = False,
) -> RecoveryKey:
    """Make the backup key; the recovery key is kept by the person (or left in the install folder, unattended)."""
    if recipient(install_dir) is not None and not replace:
        raise click.ClickException(
            "This install has a backup key already. A new one would leave the backups made so far needing the "
            "old one: pass --replace to make a new key anyway."
        )
    key = RecoveryKey.generate()
    if unattended and write_to is None:
        write_private(
            install_dir / PENDING_FILE,
            (
                "Fadenstack backup recovery key. It opens every backup of this install.\n"
                "Copy it somewhere safe, OFF this server, then run `faden backup key confirm`,\n"
                "which removes this file.\n\n" + key.secret + "\n"
            ).encode("utf-8"),
        )
    else:
        ceremony(key, install_dir, write_to=write_to)
    (install_dir / PUBLIC_FILE).write_text(key.public + "\n", encoding="utf-8")
    return key


def key_step(install_dir: Path, *, yes: bool, deploy: bool) -> None:
    """The backup key in ``faden deploy`` and ``faden upgrade``, when the install has none yet.

    Someone at the terminal makes it now (or later, if they say so). Unattended, a deploy leaves the
    recovery key in the install folder until ``faden backup key confirm``; an upgrade leaves backups as they
    were and says what to do.
    """
    if recipient(install_dir) is not None or pending(install_dir) is not None:
        if note := reminder(install_dir):
            warning(note)
        return
    if yes:
        if deploy:
            key = create(install_dir, unattended=True)
            info(f"Backups are encrypted to the backup key {key.id}.")
            warning(reminder(install_dir) or "")
        else:
            warning(reminder(install_dir) or "")
        return
    console.print("\n[bold cyan]Backup encryption[/bold cyan]")
    console.print(
        "  Backups hold every database, chat and key of this install. Encrypted, they open only with a\n"
        "  recovery key, shown once now, which you keep off this server (a password manager, a sheet in a safe)."
    )
    if not click.confirm("  Make the backup key now?", default=True):
        warning(reminder(install_dir) or "")
        return
    try:
        key = create(install_dir, unattended=False)
    except click.Abort:
        warning("No backup key was made. " + (reminder(install_dir) or ""))
        return
    success(f"From now on every backup is encrypted to the backup key {key.id}.")
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event("backup.key.create", "install", key.id, metadata={"replaced": False, "left_on_server": False})


def confirm(install_dir: Path) -> bool:
    """Prove the copy of a key an unattended deploy left behind, then remove it from the server."""
    path = pending(install_dir)
    if path is None:
        info("No recovery key is waiting on this server.")
        return False
    text = path.read_text(encoding="utf-8").strip().splitlines()[-1]
    key = RecoveryKey.from_text(text)
    console.print(
        f"\nThe recovery key is in {path}.\n"
        "Once you have a copy off this server, type two groups from that copy.\n"
    )
    if not _proven(key):
        warning("Those groups do not match the key: the file stays. Check your copy and run this again.")
        return False
    path.unlink()
    success("Your copy matches. The recovery key is no longer on this server.")
    return True


def check(install_dir: Path, text: str) -> str:
    """Whether the recovery key *text* is this install's backup key; returns its id, raises when it is not."""
    key = RecoveryKey.from_text(text)
    current = recipient(install_dir)
    if current is None:
        raise KeyMissingError("This install has no backup key.")
    if backup_crypto.key_id(current) != key.id:
        raise click.ClickException(
            f"This is not the backup key of this install: it is {key.id}, the install's is "
            f"{backup_crypto.key_id(current)}. Backups made before a key was replaced need the old key."
        )
    return key.id
